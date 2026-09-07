#!/usr/bin/env python
import base64
import logging
import math
import re
from collections import defaultdict
from datetime import timedelta, timezone

from pymongo import UpdateOne

from diworker.diworker.importers.base import (
    BaseReportImporter,
    IMPORT_PHASE_MONGO_WRITE,
)
from tools.cloud_adapter.clouds.snowflake import calculate_cost
import tools.optscale_time as opttime

LOG = logging.getLogger(__name__)
# Larger chunks cut Mongo round-trips on high-volume collectors
# (e.g. AUTOMATIC_CLUSTERING after daily aggregation).
CHUNK_SIZE = 2000
# Publish report_import.details (incl. per-account collectors) while the first
# SF collector still streams — otherwise child Advanced stays empty until the
# adapter finishes a whole service_type (can take hours on org COMPUTE).
DETAILS_PUBLISH_INTERVAL_SEC = 15
# How far back (by last expense date) to re-fetch ACCOUNT_USAGE on incremental
# runs. Snowflake views lag; late rows for older usage days must be merged
# into Mongo and ClickHouse without wiping those days. generate_clean then
# rewrites ClickHouse for the lookback window (and everything to the right,
# so a weekend with increments off is filled on the next run).
SF_INCREMENTAL_LOOKBACK_DAYS = 3
META_FIELDS = [
    'service_category', 'service_type', 'account_name',
    'region', 'model_name', 'function_name', 'query_id', 'user_id',
    'user_name', 'request_id', 'agent_id', 'agent_name', 'transfer_type',
    'tag', 'listing_global_name', 'consumer_account_locator',
    'jobs', 'unique_users_1d',
]
# Legacy OptScale resource_type → current service_type labels.
RESOURCE_TYPE_RENAMES = {
    'WAREHOUSE_METERING': 'COMPUTE',
    'DATABASE_STORAGE': 'STORAGE',
    'STAGE_STORAGE': 'STAGE',
    'PIPE': 'SNOWPIPE',
    'AI_FUNCTIONS': 'AI_SERVICES',
    'CORTEX_AGENTS': 'AI_SERVICES',
    'CORTEX_CODE_CLI': 'AI_SERVICES',
    'CORTEX_CODE_SNOWSIGHT': 'AI_SERVICES',
    'CORTEX_CODE_DESKTOP': 'AI_SERVICES',
    'SNOWFLAKE_INTELLIGENCE': 'AI_SERVICES',
    'SNOWFLAKE_COCO_SNOWSIGHT': 'AI_SERVICES',
    'SNOWFLAKE_COCO_CLI': 'AI_SERVICES',
    'SNOWFLAKE_COCO_DESKTOP': 'AI_SERVICES',
}
# Legacy cloud_resource_id patterns replaced by STAGE / per-user Cortex ids /
# listing auto-fulfillment (bill of record for REPLICATION transfers).
_LEGACY_RESOURCE_ID_RE = re.compile('|'.join((
    r'.*/stages$',
    r'.*/cortex_code_(?:cli|snowsight|desktop)/[0-9a-fA-F-]{8,}$',
    r'.*/cortex_agents/[^/]+/[0-9a-fA-F-]{8,}$',
    r'.*/snowflake_intelligence/[^/]+/[0-9a-fA-F-]{8,}$',
    r'.*/ai_functions/[^/]+/[^/]+/[^/]+$',
    # DATA_TRANSFER_HISTORY transfer_type=REPLICATION duplicates
    # LISTING_AUTO_FULFILLMENT_USAGE_HISTORY (service_type DATA TRANSFER).
    r'.*/data_transfer/[^/]+/[^/]+/REPLICATION(?:/|$)',
)))
# Org-usage types that must live on child CAs after tenant→child routing.
# Leftovers on the tenant CA keep old composite names in the UI.
_TENANT_MIRROR_RESOURCE_TYPES = (
    'DATA_TRANSFER',
    'LISTING_AUTO_FULFILLMENT',
)
# Pre-rename DATA_TRANSFER display: "COPY:us-east-1->us-west-2".
_COMPOSITE_TRANSFER_NAME_RE = re.compile(
    r'^(?P<transfer_type>[^:]+):[^:]+\->.+$')
# Pre-rename listing display: "Listing auto-fulfillment · DATA TRANSFER".
_COMPOSITE_LISTING_NAME_RE = re.compile(
    r'^Listing auto-fulfillment · (?P<sf_type>.+)$')


def _encode_tag_keys(tags: dict) -> dict:
    """OptScale stores resource tag keys as base64 (see encoded_tags)."""
    if not tags:
        return {}
    return {
        base64.b64encode(str(k).encode('utf-8')).decode('utf-8'): v
        for k, v in tags.items()
    }


class SnowflakeReportImporter(BaseReportImporter):
    def get_unique_field_list(self):
        return [
            'cloud_account_id',
            'service_type',
            'resource_id',
            'start_date',
        ]

    def get_update_fields(self):
        return [
            'end_date', 'cost', 'credits_used', 'average_bytes',
            'bytes_transferred', 'billable_amount',
            'effective_rate', 'cloud_services_effective_rate',
            'service_category', 'account_name', 'account_locator',
            'region', 'resource_name', 'model_name', 'function_name',
            'query_id', 'user_id', 'user_name', 'request_id', 'agent_id',
            'agent_name', 'tokens_input', 'tokens_output', 'tokens_total',
            'transfer_type', 'source_region', 'target_region', 'tag',
        ]

    @property
    def _is_tenant_import(self):
        # Resolved once — cloud_acc is cached, but avoid even that lookup on
        # every ORGANIZATION_USAGE row during raw load.
        cached = getattr(self, '_is_tenant_import_cached', None)
        if cached is None:
            self._is_tenant_import_cached = (
                self.cloud_acc.get('type') == 'snowflake_tenant')
            cached = self._is_tenant_import_cached
        return cached

    def _load_locator_to_child_id(self):
        """Map ORGANIZATION_USAGE account_locator → child cloud_account_id."""
        if getattr(self, '_locator_child_map', None) is not None:
            return self._locator_child_map
        org_id = self.cloud_acc.get('organization_id')
        _, resp = self.rest_cl.cloud_account_list(org_id)
        locator_map = {}
        for acc in resp.get('cloud_accounts') or []:
            if acc.get('parent_id') != self.cloud_acc_id:
                continue
            if acc.get('type') != 'snowflake':
                continue
            locator = (acc.get('config') or {}).get('account_locator')
            if not locator:
                # account_id is set from account_locator at create time.
                locator = acc.get('account_id')
            if not locator:
                continue
            locator_map[str(locator).upper()] = acc['id']
        self._locator_child_map = locator_map
        if not locator_map:
            raise RuntimeError(
                'Snowflake tenant %s has no children; run observer / '
                'create_children_accounts before import' % self.cloud_acc_id)
        LOG.info(
            'Snowflake tenant %s locator map: %s children',
            self.cloud_acc_id, len(locator_map))
        return locator_map

    def _child_cloud_account_ids(self):
        return list(self._load_locator_to_child_id().values())

    def _expense_cloud_account_ids(self):
        """CAs that hold raw/CH expenses for this import."""
        if self._is_tenant_import:
            return self._child_cloud_account_ids()
        return [self.cloud_acc_id]

    def _resolve_expense_cloud_account_id(self, record):
        if not self._is_tenant_import:
            return self.cloud_acc_id
        locator = record.get('account_locator')
        if not locator:
            raise RuntimeError(
                'Snowflake org usage row missing account_locator '
                '(service_type=%s resource_id=%s)' % (
                    record.get('service_type'), record.get('resource_id')))
        child_id = self._load_locator_to_child_id().get(str(locator).upper())
        if not child_id:
            raise RuntimeError(
                'No child cloud account for Snowflake account_locator=%s '
                'on tenant %s' % (locator, self.cloud_acc_id))
        return child_id

    def detect_period_start(self):
        """Choose import window for Snowflake.

        - First import: load from the default/extended start, wipe that window.
        - Billing reimport: CA last_import_at moved earlier than the
          incremental lookback window → wipe+reload from that cursor.
        - Scheduled/continuous import: last expense day minus
          SF_INCREMENTAL_LOOKBACK_DAYS, upsert into Mongo and merge
          ClickHouse (no wipe). Usage to the right of last_expense is
          always included, so a weekend with increments off is filled.
        """
        ca_last_import_at = self.cloud_acc.get('last_import_at')
        if not ca_last_import_at:
            self.set_period_start()
            self._wipe_expenses_from_period_start()
            return

        ca_cursor = self._naive_startday(
            opttime.utcfromtimestamp(ca_last_import_at))
        last_expense = self._last_expense_across_targets()

        if last_expense:
            last_exp_day = self._naive_startday(last_expense)
            incremental_start = last_exp_day - timedelta(
                days=SF_INCREMENTAL_LOOKBACK_DAYS)
            # Intentional reimport: cursor earlier than the overlap window.
            if ca_cursor < incremental_start:
                self.period_start = ca_cursor
                self._wipe_expenses_from_period_start()
                return
            self.period_start = incremental_start
            LOG.info(
                'Snowflake incremental window for %s: lookback %s days '
                'from last expense %s → period_start %s (merge, no wipe)',
                self.cloud_acc_id, SF_INCREMENTAL_LOOKBACK_DAYS,
                last_exp_day, self.period_start)
            return

        # ClickHouse empty: continue from the CA cursor, no wipe.
        self.period_start = ca_cursor

    @staticmethod
    def _naive_startday(dt):
        if dt is None:
            return None
        if getattr(dt, 'tzinfo', None) is not None:
            dt = dt.replace(tzinfo=None)
        return opttime.startday(dt)

    def _last_expense_across_targets(self):
        latest = None
        for ca_id in self._expense_cloud_account_ids():
            last = self.get_last_import_date(ca_id, tzinfo=timezone.utc)
            if last and (latest is None or last > latest):
                latest = last
        return latest

    def _wipe_expenses_from_period_start(self):
        for ca_id in self._expense_cloud_account_ids():
            self.remove_raw_expenses_from_period_start(ca_id)
            self._clear_clickhouse_expenses_from_period_start(ca_id)

    def load_raw_data(self):
        # Same status model as GCP: cloud fetch + Mongo write = one MongoDB step.
        self.log_import_phase(IMPORT_PHASE_MONGO_WRITE)
        if self._is_tenant_import:
            # Fail early if children are missing before long SF queries.
            self._load_locator_to_child_id()
        start = self.period_start
        end = opttime.utcnow()
        cost_model = self.cloud_acc['config'].get('cost_model', {})
        self._raw_written = 0
        self._account_collectors = {}
        chunk = []
        for record in self.cloud_adapter.download_usage(
                start, end,
                progress_callback=self._publish_import_progress):
            self._enrich_record(record, cost_model)
            if record['start_date'] >= self.period_start.replace(
                    tzinfo=timezone.utc):
                chunk.append(record)
            if len(chunk) >= CHUNK_SIZE:
                self._flush_raw_chunk(chunk)
                chunk = []
        if chunk:
            self._flush_raw_chunk(chunk)
        self._log_raw_load_progress()
        self._publish_import_details(force=True)
        LOG.info(
            'Snowflake raw load finished for %s: written=%s',
            self.cloud_acc_id, self._raw_written)
        for warning in self.cloud_adapter.get_import_warnings():
            LOG.warning('Snowflake import warning: %s', warning)

    def _flush_raw_chunk(self, chunk):
        self.log_import_phase(IMPORT_PHASE_MONGO_WRITE)
        self._accumulate_account_collectors(chunk)
        self.update_raw_records(chunk)
        self._raw_written = getattr(self, '_raw_written', 0) + len(chunk)
        self._log_raw_load_progress()
        # Mid-collector: push details.accounts so child Advanced is not empty
        # until the whole service_type finishes.
        self._publish_import_details()

    def _accumulate_account_collectors(self, chunk):
        """Build per-account_locator service stats for child Advanced tables."""
        stats = getattr(self, '_account_collectors', None)
        if stats is None:
            self._account_collectors = {}
            stats = self._account_collectors
        for record in chunk:
            loc = str(record.get('account_locator') or '').upper()
            if not loc:
                continue
            service_type = record.get('service_type') or 'UNKNOWN'
            by_type = stats.setdefault(loc, {})
            row = by_type.get(service_type)
            if row is None:
                row = {
                    'service_type': service_type,
                    'records': 0,
                    'credits': 0.0,
                    'average_bytes': 0,
                    'tb': 0.0,
                    'status': 'in_progress',
                    'message': None,
                    'finished_at': None,
                    '_storage_bytes_by_day': {},
                }
                by_type[service_type] = row
            row['records'] += 1
            row['credits'] += float(record.get('credits_used') or 0)
            bytes_used = int(record.get('average_bytes') or 0)
            if not bytes_used:
                continue
            if service_type in ('STORAGE', 'STAGE'):
                day = record.get('start_date')
                if hasattr(day, 'date'):
                    day = day.date()
                if day is not None:
                    day_map = row['_storage_bytes_by_day']
                    day_map[day] = int(day_map.get(day) or 0) + bytes_used
            else:
                row['average_bytes'] += bytes_used

    def get_import_details(self):
        details = self.cloud_adapter.get_import_details() or {}
        org_collectors = {
            row.get('service_type'): row
            for row in (details.get('collectors') or [])
        }
        accounts = {}
        for loc, by_type in (getattr(self, '_account_collectors', {}) or {}).items():
            rows = []
            for service_type, row in by_type.items():
                out = {
                    'service_type': service_type,
                    'records': int(row.get('records') or 0),
                    'credits': round(float(row.get('credits') or 0), 4),
                    'average_bytes': int(row.get('average_bytes') or 0),
                    'tb': 0.0,
                    'status': 'in_progress',
                    'message': None,
                    'finished_at': None,
                }
                day_map = row.get('_storage_bytes_by_day') or {}
                if day_map:
                    out['average_bytes'] = int(day_map[max(day_map)])
                if out['average_bytes']:
                    out['tb'] = round(out['average_bytes'] / (1024 ** 4), 4)
                org = org_collectors.get(service_type) or {}
                if org:
                    out['status'] = org.get('status') or out['status']
                    out['message'] = org.get('message')
                    out['finished_at'] = org.get('finished_at')
                rows.append(out)
            rows.sort(key=lambda item: item['service_type'])
            accounts[loc] = {'collectors': rows}
        if accounts:
            details['accounts'] = accounts
        if not details.get('collectors') and not details.get('accounts') and (
                not details.get('reconciliation') and not details.get('warnings')):
            return None
        return details

    def _collector_progress(self):
        """Return (done, total, pct) from seeded Snowflake collectors.

        Finished collectors drive the base %. While collectors still stream
        (org COMPUTE can write 100k+ rows with done=0), soft-fill into the
        remaining MongoDB room from ``_raw_written`` so status is not pinned
        at ~9% for the whole first service_type.
        """
        collectors = getattr(
            self.cloud_adapter, '_import_collectors', None) or []
        total = len(collectors)
        if not total:
            return 0, 0, None
        done = sum(
            1 for row in collectors
            if row.get('status') != 'in_progress')
        written = getattr(self, '_raw_written', 0)
        base = 100.0 * done / total
        if done < total and written > 0:
            soft_cap = 95.0
            room = max(0.0, soft_cap - base)
            # ~100k rows → ~63% of remaining room; full org load is larger.
            fill = room * (1.0 - math.exp(-written / 100000.0))
            pct = min(99, int(base + fill))
        else:
            pct = min(100, int(base))
        return done, total, pct

    def _log_raw_load_progress(self, force=False):
        """Emit parseable MongoDB-stage % for import-queue-status.sh."""
        del force  # callers pass force for clarity; always log when invoked
        done, total, pct = self._collector_progress()
        written = getattr(self, '_raw_written', 0)
        if pct is not None:
            LOG.info(
                'Snowflake raw load progress for %s: written=%s '
                'collectors_done=%s collectors_total=%s pct=%s',
                self.cloud_acc_id, written, done, total, pct)
        else:
            LOG.info(
                'Snowflake raw load progress for %s: written=%s',
                self.cloud_acc_id, written)

    def _publish_import_progress(self):
        # Keep MongoDB phase visible while collectors run (long org-usage queries).
        self.log_import_phase(IMPORT_PHASE_MONGO_WRITE)
        self._log_raw_load_progress()
        self._publish_import_details(force=True)

    def _publish_import_details(self, force=False):
        """Persist collectors + per-account stats on the active report_import."""
        report_import_id = getattr(self, 'report_import_id', None)
        if not report_import_id:
            return
        now = opttime.utcnow().timestamp()
        last = getattr(self, '_last_details_publish_at', 0.0)
        if (not force and last
                and now - last < DETAILS_PUBLISH_INTERVAL_SEC):
            return
        details = self.get_import_details()
        if not details:
            return
        try:
            self.rest_cl.report_import_update(
                report_import_id, {'details': details})
            self._last_details_publish_at = now
        except Exception as exc:
            LOG.warning(
                'Failed to publish Snowflake import details: %s', exc)

    def _enrich_record(self, record, cost_model):
        record['cloud_account_id'] = self._resolve_expense_cloud_account_id(
            record)
        record['cost'] = calculate_cost(record, cost_model)
        if not record.get('resource_id'):
            record['resource_id'] = (
                f"{record.get('account_locator')}/"
                f"{record.get('service_type')}/"
                f"{record.get('start_date')}")

    def update_cloud_import_time(self, ts):
        super().update_cloud_import_time(ts)
        if not self._is_tenant_import:
            return
        # Mirror successful import onto children so UI billing is not Never.
        for child_id in self._child_cloud_account_ids():
            try:
                self.rest_cl.cloud_account_update(
                    child_id,
                    {'last_import_at': ts,
                     'last_import_attempt_at': ts,
                     'last_import_attempt_error': None})
            except Exception as exc:
                LOG.warning(
                    'Failed to mirror last_import_at onto child %s: %s',
                    child_id, exc)

    def generate_clean_records(self, regeneration=False):
        if not self._is_tenant_import:
            return super().generate_clean_records(regeneration=regeneration)
        children = self._child_cloud_account_ids()
        total = len(children) or 1
        for i, ca_id in enumerate(children):
            pct = int(100 * i / total)
            LOG.info(
                'Clean progress for %s (children): %s%%',
                self.cloud_acc_id, pct)
            resource_ids = self.get_resource_ids(ca_id, self.period_start)
            self._generate_clean_records(
                resource_ids, ca_id, self.period_start)
        LOG.info(
            'Clean progress for %s (children): 100%%', self.cloud_acc_id)

    def recalculate_raw_expenses(self):
        cost_model = self.cloud_acc['config'].get('cost_model', {})
        chunk = []
        for ca_id in self._expense_cloud_account_ids():
            for expense in self.mongo_raw.find({'cloud_account_id': ca_id}):
                new_cost = calculate_cost(expense, cost_model)
                if new_cost != expense.get('cost'):
                    expense['cost'] = new_cost
                    chunk.append(expense)
                if len(chunk) >= CHUNK_SIZE:
                    self.update_raw_records(chunk)
                    chunk = []
        if chunk:
            self.update_raw_records(chunk)

    def _get_cloud_extras(self, info):
        res = defaultdict(dict)
        for k in META_FIELDS:
            val = info.get(k)
            if val is not None and val != '':
                res['meta'][k] = val
        return res

    def get_resource_info_from_expenses(self, expenses):
        first_seen = opttime.utcnow()
        last_seen = opttime.utcfromtimestamp(0)
        meta_dict = {}
        name = None
        name_ts = None
        resource_type = None
        resource_type_ts = None
        tag = None
        account_locator = None
        account_name = None
        for e in expenses:
            start_date = e.get('start_date')
            end_date = e.get('end_date') or start_date
            if start_date and start_date < first_seen:
                first_seen = start_date
            if end_date and end_date > last_seen:
                last_seen = end_date
            # Prefer the newest expense's name/type so renames apply when
            # legacy and current raw rows coexist for the same resource_id.
            candidate_name = e.get('resource_name') or e.get('resource_id')
            if candidate_name and (
                    name_ts is None
                    or (start_date and start_date >= name_ts)):
                name = candidate_name
                name_ts = start_date
            service_type = e.get('service_type')
            if service_type and (
                    resource_type_ts is None
                    or (start_date and start_date >= resource_type_ts)):
                resource_type = service_type
                resource_type_ts = start_date
            if not tag and e.get('tag'):
                tag = e.get('tag')
            if not account_locator and e.get('account_locator'):
                account_locator = e.get('account_locator')
            if not account_name and e.get('account_name'):
                account_name = e.get('account_name')
            for k in META_FIELDS:
                v = e.get(k)
                if v is not None and k not in meta_dict:
                    meta_dict[k] = v
        if last_seen < first_seen:
            last_seen = first_seen
        resource_type = (
            RESOURCE_TYPE_RENAMES.get(resource_type, resource_type)
            or 'Snowflake')
        if resource_type:
            meta_dict['service_type'] = resource_type
        tags = {'product': tag} if tag else {}
        info = {
            'name': name,
            'type': resource_type,
            'tags': tags,
            'first_seen': int(first_seen.timestamp()),
            'last_seen': int(last_seen.timestamp()),
            'account_locator': account_locator,
            'account_name': account_name,
            **meta_dict,
        }
        LOG.debug('Detected Snowflake resource info: %s', info)
        return info

    def get_resource_data(self, r_id, info,
                          unique_id_field='cloud_resource_id'):
        data = {
            unique_id_field: r_id,
            'resource_type': info['type'],
            'name': info['name'],
            'tags': info.get('tags', {}),
            'first_seen': info['first_seen'],
            'last_seen': info['last_seen'],
            **self._get_cloud_extras(info),
        }
        # First-class fields for Resources filters / categorize-by / table.
        if info.get('account_locator'):
            data['account_locator'] = info['account_locator']
        if info.get('account_name'):
            data['account_name'] = info['account_name']
        if info.get('region'):
            data['region'] = info['region']
        return data

    def create_resources_if_not_exist(self, cloud_account_id,
                                      resources_info_map,
                                      unique_id_field='cloud_resource_id'):
        # Report import uses skip_existing, which leaves resource_type on
        # $setOnInsert only. Force-refresh type/name/tags for Snowflake renames.
        resources = super().create_resources_if_not_exist(
            cloud_account_id, resources_info_map, unique_id_field)
        bulk = []
        for r_id, info in resources_info_map.items():
            resource_type = info.get('type')
            if not resource_type:
                continue
            update = {
                'resource_type': resource_type,
                # Resurrect if a prior bad orphan-cleanup soft-deleted the row.
                'deleted_at': 0,
            }
            if info.get('name'):
                update['name'] = info['name']
            if info.get('account_locator'):
                update['account_locator'] = info['account_locator']
            if info.get('account_name'):
                update['account_name'] = info['account_name']
            if info.get('region'):
                update['region'] = info['region']
            if info.get('service_type'):
                update['meta.service_type'] = info['service_type']
            if info.get('user_name'):
                update['meta.user_name'] = info['user_name']
            for meta_key in (
                    'listing_global_name', 'consumer_account_locator',
                    'jobs', 'unique_users_1d', 'model_name', 'function_name',
                    'query_id', 'region', 'account_name'):
                if info.get(meta_key) is not None and info.get(meta_key) != '':
                    update['meta.%s' % meta_key] = info[meta_key]
            # info['tags'] uses plain keys for REST create_bulk (which encodes).
            # Direct Mongo $set must use base64 keys or clean_expenses 500s.
            plain_tags = info.get('tags') or {}
            # Always rewrite tags (including empty) so stale plain keys are gone.
            update['tags'] = _encode_tag_keys(plain_tags)
            bulk.append(UpdateOne(
                {
                    'cloud_account_id': cloud_account_id,
                    unique_id_field: r_id,
                },
                {'$set': update},
            ))
        if bulk:
            self.mongo_resources.bulk_write(bulk, ordered=False)
        # Only drop known legacy cloud_resource_id shapes superseded by
        # current collectors — never wipe resources missing from this window.
        self._soft_delete_legacy_resources(cloud_account_id, unique_id_field)
        self._rename_composite_transfer_names(cloud_account_id)
        self._rename_composite_listing_names(cloud_account_id)
        return resources

    def cleanup(self):
        # create_resources only touches CAs that had expenses in this window.
        # Orphan REPLICATION transfers / tenant mirrors need a full pass.
        unique_id_field = 'cloud_resource_id'
        for ca_id in self._expense_cloud_account_ids():
            self._soft_delete_legacy_resources(ca_id, unique_id_field)
            self._rename_composite_transfer_names(ca_id)
            self._rename_composite_listing_names(ca_id)
        if self._is_tenant_import:
            self._soft_delete_tenant_org_usage_mirrors()

    def _soft_delete_legacy_resources(
            self, cloud_account_id, unique_id_field='cloud_resource_id'):
        now = int(opttime.utcnow().timestamp())
        result = self.mongo_resources.update_many(
            {
                'cloud_account_id': cloud_account_id,
                'deleted_at': 0,
                unique_id_field: {'$regex': _LEGACY_RESOURCE_ID_RE.pattern},
            },
            {'$set': {'deleted_at': now}},
        )
        if result.modified_count:
            LOG.info(
                'Soft-deleted %s legacy Snowflake resources for %s',
                result.modified_count, cloud_account_id)
        # Name-based catch for pre-id-scheme REPLICATION leftovers.
        name_result = self.mongo_resources.update_many(
            {
                'cloud_account_id': cloud_account_id,
                'deleted_at': 0,
                'resource_type': 'DATA_TRANSFER',
                'name': {'$regex': r'^REPLICATION:'},
            },
            {'$set': {'deleted_at': now}},
        )
        if name_result.modified_count:
            LOG.info(
                'Soft-deleted %s REPLICATION DATA_TRANSFER leftovers for %s',
                name_result.modified_count, cloud_account_id)

    def _soft_delete_tenant_org_usage_mirrors(self):
        """Drop tenant-CA copies after org usage routed to children."""
        now = int(opttime.utcnow().timestamp())
        result = self.mongo_resources.update_many(
            {
                'cloud_account_id': self.cloud_acc_id,
                'deleted_at': 0,
                'resource_type': {'$in': list(_TENANT_MIRROR_RESOURCE_TYPES)},
            },
            {'$set': {'deleted_at': now}},
        )
        if result.modified_count:
            LOG.info(
                'Soft-deleted %s tenant-mirrored Snowflake resources for %s',
                result.modified_count, self.cloud_acc_id)

    def _rename_composite_transfer_names(self, cloud_account_id):
        """Fix DATA_TRANSFER names still using TYPE:src->dst."""
        bulk = []
        for doc in self.mongo_resources.find(
                {
                    'cloud_account_id': cloud_account_id,
                    'deleted_at': 0,
                    'resource_type': 'DATA_TRANSFER',
                    'name': {'$regex': _COMPOSITE_TRANSFER_NAME_RE.pattern},
                },
                {'_id': 1, 'name': 1}):
            match = _COMPOSITE_TRANSFER_NAME_RE.match(doc.get('name') or '')
            if not match:
                continue
            transfer_type = match.group('transfer_type')
            if transfer_type == 'REPLICATION':
                # Soft-deleted by _soft_delete_legacy_resources.
                continue
            bulk.append(UpdateOne(
                {'_id': doc['_id']},
                {'$set': {'name': transfer_type}},
            ))
        if bulk:
            result = self.mongo_resources.bulk_write(bulk, ordered=False)
            LOG.info(
                'Renamed %s composite DATA_TRANSFER names for %s',
                result.modified_count, cloud_account_id)

    def _rename_composite_listing_names(self, cloud_account_id):
        """Fix LISTING_AUTO_FULFILLMENT names still using the long label."""
        bulk = []
        for doc in self.mongo_resources.find(
                {
                    'cloud_account_id': cloud_account_id,
                    'deleted_at': 0,
                    'resource_type': 'LISTING_AUTO_FULFILLMENT',
                    'name': {'$regex': _COMPOSITE_LISTING_NAME_RE.pattern},
                },
                {'_id': 1, 'name': 1}):
            match = _COMPOSITE_LISTING_NAME_RE.match(doc.get('name') or '')
            if not match:
                continue
            bulk.append(UpdateOne(
                {'_id': doc['_id']},
                {'$set': {'name': match.group('sf_type')}},
            ))
        if bulk:
            result = self.mongo_resources.bulk_write(bulk, ordered=False)
            LOG.info(
                'Renamed %s composite LISTING_AUTO_FULFILLMENT names for %s',
                result.modified_count, cloud_account_id)
