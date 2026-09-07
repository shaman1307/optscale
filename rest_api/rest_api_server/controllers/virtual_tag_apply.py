import json
import logging
import threading
import time
from pymongo import UpdateOne
from pymongo.errors import CursorNotFound
from sqlalchemy.orm import subqueryload

from tools.optscale_exceptions.common_exc import NotFoundException

from rest_api.rest_api_server.controllers.base import BaseController, MongoMixin
from rest_api.rest_api_server.controllers.base_async import (
    BaseAsyncControllerWrapper)
from rest_api.rest_api_server.controllers.rule_apply import match_conditions
from rest_api.rest_api_server.exceptions import Err
from rest_api.rest_api_server.models.enums import (
    ConditionTypes, RuleOperators, VirtualTagModes)
from rest_api.rest_api_server.models.models import (
    VirtualTag, VirtualTagRule, VirtualTagRuleBranch, CloudAccount)
from rest_api.rest_api_server.utils import (
    encoded_tags, current_quarter, check_quarter)
import tools.optscale_time as opttime

LOG = logging.getLogger(__name__)
# One find()+bulk_write per page so the Mongo cursor cannot idle-timeout.
CHUNK_SIZE = 500
# Status grep in import-queue-status.sh. Log per bulk chunk, not per resource.
PROGRESS_LOG_EVERY = 10000
PROGRESS_LOG_EVERY_SEC = 10
LEGACY_BACKFILL_QUARTERS = ('2026Q3', '2026Q2')
_APPLY_PROGRESS = {}
_APPLY_PROGRESS_LOCK = threading.Lock()


def _progress_pct(processed, total):
    if total == 0:
        return 100
    return min(100, int(100 * processed / total))


def publish_apply_progress(organization_id, quarter, processed, total, state):
    snap = {
        'quarter': quarter,
        'state': state,
        'processed': processed,
        'total': total,
        'pct': _progress_pct(processed, total),
    }
    with _APPLY_PROGRESS_LOCK:
        _APPLY_PROGRESS[(organization_id, quarter)] = snap
    return snap


def backfill_legacy_virtual_tags_by_quarter(collection, quarters=None):
    """Copy legacy virtual_tags into missing by_quarter keys (Q3 and Q2)."""
    quarters = list(quarters or LEGACY_BACKFILL_QUARTERS)
    updated = 0
    ops = []
    cursor = collection.find(
        {
            'deleted_at': 0,
            'virtual_tags': {'$exists': True, '$nin': [None, []]},
        },
        ['virtual_tags', 'virtual_tags_by_quarter'],
    )
    for doc in cursor:
        tags = doc.get('virtual_tags') or []
        if not tags:
            continue
        by_quarter = dict(doc.get('virtual_tags_by_quarter') or {})
        changed = False
        for quarter in quarters:
            if quarter not in by_quarter:
                by_quarter[quarter] = tags
                changed = True
        if not changed:
            continue
        ops.append(UpdateOne(
            {'_id': doc['_id']},
            {'$set': {'virtual_tags_by_quarter': by_quarter}},
        ))
        if len(ops) >= CHUNK_SIZE:
            collection.bulk_write(ops, ordered=False)
            updated += len(ops)
            ops = []
    if ops:
        collection.bulk_write(ops, ordered=False)
        updated += len(ops)
    return updated


class VirtualTagApplyController(BaseController, MongoMixin):
    """Assign resource VT maps per quarter. Never writes pool_id / employee_id."""

    def load_org_virtual_tags(self, organization_id, quarter=None):
        query = self.session.query(VirtualTag).options(
            subqueryload(VirtualTag.rules)
            .subqueryload(VirtualTagRule.branches)
            .subqueryload(VirtualTagRuleBranch.conditions),
            subqueryload(VirtualTag.rules)
            .subqueryload(VirtualTagRule.branches)
            .subqueryload(VirtualTagRuleBranch.allocations),
        ).filter(
            VirtualTag.organization_id == organization_id,
            VirtualTag.deleted.is_(False),
        )
        if quarter:
            query = query.filter(VirtualTag.quarter == quarter)
        return query.all()

    def apply_to_resource(self, resource, virtual_tags=None,
                          organization_id=None, quarter=None,
                          replace_keys=None):
        org_id = organization_id or resource.get('organization_id')
        quarter = quarter or current_quarter()
        if virtual_tags is None:
            virtual_tags = (
                self.load_org_virtual_tags(org_id, quarter=quarter)
                if org_id else [])
        allocations = []
        overlap_pairs = []
        for vt in virtual_tags:
            applied, pairs = self._apply_one_vt(resource, vt)
            allocations.extend(applied)
            overlap_pairs.extend(pairs)
        by_quarter = dict(resource.get('virtual_tags_by_quarter') or {})
        existing = list(by_quarter.get(quarter) or [])
        if replace_keys:
            kept = [
                alloc for alloc in existing
                if isinstance(alloc, dict) and alloc.get('key') not in replace_keys]
            allocations = kept + allocations
        by_quarter[quarter] = allocations
        resource['virtual_tags_by_quarter'] = by_quarter
        if quarter == current_quarter():
            resource['virtual_tags'] = allocations
        return resource, overlap_pairs

    def _active_rules(self, vt):
        rules = [rule for rule in vt.rules
                 if not rule.deleted and rule.active]
        return sorted(rules, key=lambda rule: rule.priority)

    @staticmethod
    def _branch_signature(branch):
        allocs = [
            (alloc.value, int(alloc.share))
            for alloc in branch.allocations if not alloc.deleted]
        return tuple(sorted(allocs))

    @staticmethod
    def _branch_allocations(vt_key, branch, rule_id):
        return [
            {
                'key': vt_key,
                'value': alloc.value,
                'share': int(alloc.share),
                'rule_id': str(rule_id),
            }
            for alloc in branch.allocations
            if not alloc.deleted
        ]

    def _apply_one_vt(self, resource, vt):
        rules = self._active_rules(vt)
        if vt.mode == VirtualTagModes.EXTRACT:
            if rules and not any(
                    self._rule_matches(resource, rule) for rule in rules):
                return [], []
            tags = resource.get('tags') or {}
            if isinstance(tags, dict):
                value = tags.get(vt.source_tag_key)
            else:
                value = None
            if not value:
                return [], []
            return [{'key': vt.key, 'value': value, 'share': 100}], []
        matches = []
        for rule in rules:
            matched_branch = self._first_matching_branch(resource, rule)
            if matched_branch is None:
                continue
            matches.append((rule, matched_branch))
        if not matches:
            return [], []
        winner_rule, winner_branch = matches[0]
        allocations = self._branch_allocations(
            vt.key, winner_branch, winner_rule.id)
        overlap_pairs = []
        winner_sig = self._branch_signature(winner_branch)
        for other_rule, other_branch in matches[1:]:
            if self._branch_signature(other_branch) != winner_sig:
                overlap_pairs.append(
                    (str(winner_rule.id), str(other_rule.id)))
        return allocations, overlap_pairs

    def _rule_matches(self, resource, rule):
        return self._first_matching_branch(resource, rule) is not None

    def _first_matching_branch(self, resource, rule):
        branches = sorted(
            [branch for branch in rule.branches if not branch.deleted],
            key=lambda branch: branch.priority)
        for branch in branches:
            conditions = [
                cond for cond in branch.conditions if not cond.deleted]
            if match_conditions(conditions, resource, RuleOperators.AND):
                return branch
        return None

    def _page_resource_filter(self, resource_filter, last_id):
        if last_id is None:
            return resource_filter
        return {'$and': [resource_filter, {'_id': {'$gt': last_id}}]}

    def _fetch_resource_page(self, resource_filter, last_id):
        query = self._page_resource_filter(resource_filter, last_id)
        return list(
            self.resources_collection.find(query).sort('_id', 1).limit(
                CHUNK_SIZE))

    def reapply_organization(self, organization_id, quarter=None,
                             changed_only=False, virtual_tag_id=None):
        quarter = quarter or current_quarter()
        virtual_tags = self.load_org_virtual_tags(
            organization_id, quarter=quarter)
        if virtual_tag_id:
            virtual_tags = [
                vt for vt in virtual_tags if vt.id == virtual_tag_id]
            if not virtual_tags:
                raise NotFoundException(
                    Err.OE0002, [VirtualTag.__name__, virtual_tag_id])
        target_tags, replace_keys, scoped_cloud_ids, changed_rule_ids = (
            self._changed_apply_scope(
                organization_id, quarter, virtual_tags, changed_only,
                virtual_tag_id=virtual_tag_id))
        if not changed_only and virtual_tag_id:
            # Full reapply of one VT must keep sibling keys on resources.
            replace_keys = {vt.key for vt in virtual_tags}
            scoped_cloud_ids, changed_rule_ids = (
                self._resource_scope_for_tags(virtual_tags))
        if changed_only and not replace_keys:
            publish_apply_progress(
                organization_id, quarter, 0, 0, 'finished')
            return {
                'processed_resources': 0,
                'quarter': quarter,
                'changed_only': True,
                'overlap_rule_ids': {},
            }
        cloud_ids = [
            row[0] for row in self.session.query(CloudAccount.id).filter(
                CloudAccount.organization_id == organization_id,
                CloudAccount.deleted.is_(False),
            ).all()]
        resource_filter = {
            '$or': [
                {'cloud_account_id': {'$in': cloud_ids}},
                {'organization_id': organization_id},
            ],
            'deleted_at': 0,
        }
        if scoped_cloud_ids is not None:
            scope_or = [
                {'cloud_account_id': {'$in': list(scoped_cloud_ids)}},
                {
                    'virtual_tags_by_quarter.%s.rule_id' % quarter: {
                        '$in': list(changed_rule_ids) or ['']},
                },
                {
                    'virtual_tags.rule_id': {
                        '$in': list(changed_rule_ids) or ['']},
                },
            ]
            if replace_keys:
                scope_or.extend([
                    {
                        'virtual_tags_by_quarter.%s.key' % quarter: {
                            '$in': list(replace_keys)},
                    },
                    {'virtual_tags.key': {'$in': list(replace_keys)}},
                ])
            resource_filter = {
                '$and': [
                    resource_filter,
                    {'$or': scope_or},
                ]
            }
        apply_tags = target_tags if changed_only else virtual_tags
        total = self.resources_collection.count_documents(resource_filter)
        processed = 0
        overlap_pairs = set()
        publish_apply_progress(
            organization_id, quarter, processed, total, 'running')
        LOG.info(
            'Virtual tag apply started for org %s quarter %s: total=%s '
            'changed_only=%s virtual_tag_id=%s',
            organization_id, quarter, total, bool(changed_only),
            virtual_tag_id)
        last_log_processed = 0
        last_log_at = time.time()

        def maybe_log_progress(force=False):
            nonlocal last_log_processed, last_log_at
            now = time.time()
            due_rows = processed - last_log_processed >= PROGRESS_LOG_EVERY
            due_time = (
                now - last_log_at >= PROGRESS_LOG_EVERY_SEC
                and processed > last_log_processed)
            if not force and not due_rows and not due_time:
                return
            pct = _progress_pct(processed, total)
            LOG.info(
                'Virtual tag apply progress for org %s quarter %s: '
                'processed=%s total=%s pct=%s',
                organization_id, quarter, processed, total, pct)
            last_log_processed = processed
            last_log_at = now

        last_id = None
        cursor_retries = 0
        while True:
            try:
                page = self._fetch_resource_page(resource_filter, last_id)
            except CursorNotFound:
                cursor_retries += 1
                if cursor_retries > 5:
                    raise
                LOG.warning(
                    'Virtual tag apply cursor lost for org %s quarter %s '
                    'after _id=%s; retry %s',
                    organization_id, quarter, last_id, cursor_retries)
                continue
            cursor_retries = 0
            if not page:
                break
            ops = []
            for resource in page:
                tags = encoded_tags(resource.get('tags') or {}, decode=True)
                match_info = dict(resource)
                match_info['tags'] = tags
                match_info['virtual_tags_by_quarter'] = dict(
                    resource.get('virtual_tags_by_quarter') or {})
                _, pairs = self.apply_to_resource(
                    match_info, virtual_tags=apply_tags,
                    organization_id=organization_id, quarter=quarter,
                    replace_keys=replace_keys)
                for left, right in pairs:
                    left_id, right_id = str(left), str(right)
                    if left_id == right_id:
                        continue
                    overlap_pairs.add(frozenset((left_id, right_id)))
                fields = {
                    'virtual_tags_by_quarter.%s' % quarter: match_info[
                        'virtual_tags_by_quarter'].get(quarter) or [],
                }
                if quarter == current_quarter():
                    fields['virtual_tags'] = match_info.get(
                        'virtual_tags') or []
                ops.append(UpdateOne(
                    {'_id': resource['_id']},
                    {'$set': fields},
                ))
            if ops:
                self.resources_collection.bulk_write(ops, ordered=False)
                processed += len(ops)
                publish_apply_progress(
                    organization_id, quarter, processed, total, 'running')
                maybe_log_progress()
            last_id = page[-1]['_id']
        maybe_log_progress(force=True)
        overlap_map = self._persist_apply_metadata(
            organization_id, quarter, apply_tags, overlap_pairs,
            replace_keys=replace_keys)
        publish_apply_progress(
            organization_id, quarter, processed, total, 'finished')
        LOG.info(
            'Virtual tag apply finished for org %s quarter %s: processed=%s '
            'overlaps=%s',
            organization_id, quarter, processed, len(overlap_pairs))
        result = {
            'processed_resources': processed,
            'quarter': quarter,
            'changed_only': bool(changed_only),
            'overlap_rule_ids': overlap_map,
        }
        if virtual_tag_id:
            result['virtual_tag_id'] = virtual_tag_id
        return result

    def _condition_type(self, cond):
        ctype = cond.type
        return ctype.value if hasattr(ctype, 'value') else ctype

    def _rule_cloud_ids(self, rule):
        ids = []
        for branch in rule.branches or []:
            if branch.deleted:
                continue
            for cond in branch.conditions or []:
                if cond.deleted:
                    continue
                if self._condition_type(cond) == ConditionTypes.CLOUD_IS.value:
                    if cond.meta_info:
                        ids.append(cond.meta_info)
        return ids

    def _resource_scope_for_tags(self, virtual_tags):
        scoped_cloud_ids = set()
        rule_ids = set()
        unscoped = False
        for vt in virtual_tags:
            for rule in vt.rules:
                if rule.deleted or not rule.active:
                    continue
                rule_ids.add(str(rule.id))
                cloud_ids = self._rule_cloud_ids(rule)
                if cloud_ids:
                    scoped_cloud_ids.update(cloud_ids)
                else:
                    unscoped = True
        if unscoped or not scoped_cloud_ids:
            return None, rule_ids
        return scoped_cloud_ids, rule_ids

    def _changed_apply_scope(self, organization_id, quarter, virtual_tags,
                             changed_only, virtual_tag_id=None):
        if not changed_only:
            return virtual_tags, None, None, set()
        live_by_id = {
            rule.id: rule
            for vt in virtual_tags
            for rule in vt.rules
            if not rule.deleted
        }
        changed_live = [
            rule for rule in live_by_id.values()
            if (rule.updated_at or 0) > (rule.last_applied_at or 0)
        ]
        deleted_changed = self.session.query(VirtualTagRule).filter(
            VirtualTagRule.organization_id == organization_id,
            VirtualTagRule.deleted_at != 0,
            VirtualTagRule.deleted_at > VirtualTagRule.last_applied_at,
        ).all()
        deleted_changed = [
            rule for rule in deleted_changed
            if rule.virtual_tag and rule.virtual_tag.quarter == quarter
        ]
        if virtual_tag_id:
            deleted_changed = [
                rule for rule in deleted_changed
                if rule.virtual_tag_id == virtual_tag_id]
        if not changed_live and not deleted_changed:
            return [], None, None, set()
        affected_vt_ids = {
            rule.virtual_tag_id for rule in changed_live}
        affected_vt_ids.update(
            rule.virtual_tag_id for rule in deleted_changed)
        target_tags = [
            vt for vt in virtual_tags if vt.id in affected_vt_ids]
        replace_keys = {vt.key for vt in target_tags}
        replace_keys.update(
            rule.virtual_tag.key for rule in deleted_changed
            if rule.virtual_tag and rule.virtual_tag.key)
        changed_rule_ids = {str(rule.id) for rule in changed_live}
        changed_rule_ids.update(str(rule.id) for rule in deleted_changed)
        scoped_cloud_ids = set()
        unscoped = False
        for rule in list(changed_live) + list(deleted_changed):
            cloud_ids = self._rule_cloud_ids(rule)
            if cloud_ids:
                scoped_cloud_ids.update(cloud_ids)
            else:
                unscoped = True
        if unscoped:
            return target_tags, replace_keys, None, changed_rule_ids
        return target_tags, replace_keys, scoped_cloud_ids, changed_rule_ids

    def _persist_apply_metadata(self, organization_id, quarter, applied_tags,
                                overlap_pairs, replace_keys=None):
        now = opttime.utcnow_timestamp()
        overlap_map = {}
        for pair in overlap_pairs:
            items = [str(item) for item in pair]
            if len(items) != 2:
                continue
            left, right = items
            overlap_map.setdefault(left, set()).add(right)
            overlap_map.setdefault(right, set()).add(left)
        affected_keys = set(replace_keys or [])
        affected_keys.update(vt.key for vt in applied_tags)
        rules = self.session.query(VirtualTagRule).filter(
            VirtualTagRule.organization_id == organization_id,
        ).all()
        for rule in rules:
            vt = rule.virtual_tag
            if not vt or vt.quarter != quarter:
                continue
            if affected_keys and vt.key not in affected_keys:
                continue
            rule.last_applied_at = now
            if not rule.deleted:
                ids = sorted(overlap_map.get(str(rule.id), []))
                rule.overlap_rule_ids = json.dumps(ids)
        self.session.commit()
        return {
            rule_id: sorted(others)
            for rule_id, others in overlap_map.items()
        }

    def get_apply_progress(self, organization_id, quarter=None):
        quarter = check_quarter(quarter) if quarter else current_quarter()
        with _APPLY_PROGRESS_LOCK:
            snap = _APPLY_PROGRESS.get((organization_id, quarter))
            if snap:
                return dict(snap)
        return {
            'quarter': quarter,
            'state': 'idle',
            'processed': 0,
            'total': 0,
            'pct': 0,
        }


class VirtualTagApplyAsyncController(BaseAsyncControllerWrapper):
    def _get_controller_class(self):
        return VirtualTagApplyController
