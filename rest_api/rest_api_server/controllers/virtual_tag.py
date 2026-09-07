import logging
import tools.optscale_time as opttime

from tools.optscale_exceptions.common_exc import (
    WrongArgumentsException, ConflictException, NotFoundException)
from datetime import datetime, timezone
from calendar import monthrange
from collections import defaultdict

from rest_api.rest_api_server.controllers.base import (
    BaseController, OrganizationValidatorMixin, MongoMixin, ClickHouseMixin)
from rest_api.rest_api_server.controllers.base_async import (
    BaseAsyncControllerWrapper)
from rest_api.rest_api_server.controllers.employee import EmployeeController
from rest_api.rest_api_server.exceptions import Err
from rest_api.rest_api_server.models.enums import (
    ConditionTypes, VirtualTagModes)
from rest_api.rest_api_server.models.models import (
    VirtualTag, VirtualTagRule, VirtualTagRuleBranch,
    VirtualTagRuleCondition, VirtualTagRuleAllocation, VirtualTagValueLimit,
    CloudAccount)
from rest_api.rest_api_server.controllers.rule import RuleController
from rest_api.rest_api_server.utils import (
    check_string_attribute, check_bool_attribute, check_int_attribute,
    check_list_attribute, check_dict_attribute, raise_not_provided_exception,
    check_quarter, current_quarter, virtual_tags_for_quarter)
from sqlalchemy.orm import selectinload
from tools.optscale_data.clickhouse import ExternalDataConverter
from tools.optscale_data.expenses import ExpenseQuery

LOG = logging.getLogger(__name__)


class VirtualTagController(BaseController, OrganizationValidatorMixin,
                           MongoMixin, ClickHouseMixin):
    def _get_model_type(self):
        return VirtualTag

    def _validate(self, item, is_new=True, **kwargs):
        org_id = kwargs.get('organization_id') or item.organization_id
        self.check_organization(org_id)
        mode = item.mode
        if hasattr(mode, 'value'):
            mode = VirtualTagModes(mode.value)
        elif isinstance(mode, str):
            if mode not in VirtualTagModes.values():
                raise WrongArgumentsException(Err.OE0579, [mode])
            mode = VirtualTagModes(mode)
            item.mode = mode
        if mode == VirtualTagModes.EXTRACT and not item.source_tag_key:
            raise WrongArgumentsException(Err.OE0578, [])
        quarter = kwargs.get('quarter') or item.quarter
        if not quarter:
            quarter = current_quarter()
            item.quarter = quarter
        else:
            item.quarter = check_quarter(quarter)
        existing = self.session.query(VirtualTag).filter(
            VirtualTag.organization_id == org_id,
            VirtualTag.deleted.is_(False),
            VirtualTag.key == item.key,
            VirtualTag.quarter == item.quarter,
        )
        if not is_new:
            existing = existing.filter(VirtualTag.id != item.id)
        if existing.one_or_none():
            raise ConflictException(Err.OE0149, [VirtualTag.__name__, item.key])

    def list(self, organization_id, **kwargs):
        self.check_organization(organization_id)
        quarter = kwargs.get('quarter')
        if quarter:
            quarter = check_quarter(quarter)
        else:
            quarter = current_quarter()
        query = self.session.query(VirtualTag).filter(
            VirtualTag.organization_id == organization_id,
            VirtualTag.deleted.is_(False),
            VirtualTag.quarter == quarter,
        )
        name = kwargs.get('name')
        if name:
            query = query.filter(VirtualTag.name.ilike('%%%s%%' % name))
        value = kwargs.get('value')
        cloud_account_id = kwargs.get('cloud_account_id')
        if isinstance(cloud_account_id, str):
            cloud_account_id = [cloud_account_id]
        items = query.order_by(VirtualTag.created_at).all()
        if value or cloud_account_id:
            items = [
                item for item in items
                if self._matches_list_filters(item, value, cloud_account_id)
            ]
        return {'virtual_tags': [
            self._format(item, with_allocation_values=False) for item in items
        ], 'quarter': quarter, 'quarters': self._org_quarters(organization_id)}

    def _org_quarters(self, organization_id):
        rows = self.session.query(VirtualTag.quarter).filter(
            VirtualTag.organization_id == organization_id,
            VirtualTag.deleted.is_(False),
        ).distinct().all()
        return sorted({row[0] for row in rows if row[0]})

    def _matches_list_filters(self, item, value, cloud_account_ids):
        if value:
            has_value = self.session.query(VirtualTagRuleAllocation.id).join(
                VirtualTagRuleBranch,
                VirtualTagRuleAllocation.branch_id == VirtualTagRuleBranch.id,
            ).join(
                VirtualTagRule,
                VirtualTagRuleBranch.rule_id == VirtualTagRule.id,
            ).filter(
                VirtualTagRule.virtual_tag_id == item.id,
                VirtualTagRule.deleted.is_(False),
                VirtualTagRuleBranch.deleted.is_(False),
                VirtualTagRuleAllocation.deleted.is_(False),
                VirtualTagRuleAllocation.value == value,
            ).first()
            if not has_value:
                return False
        if cloud_account_ids:
            has_account = self.session.query(VirtualTagRuleCondition.id).join(
                VirtualTagRuleBranch,
                VirtualTagRuleCondition.branch_id == VirtualTagRuleBranch.id,
            ).join(
                VirtualTagRule,
                VirtualTagRuleBranch.rule_id == VirtualTagRule.id,
            ).filter(
                VirtualTagRule.virtual_tag_id == item.id,
                VirtualTagRule.deleted.is_(False),
                VirtualTagRuleBranch.deleted.is_(False),
                VirtualTagRuleCondition.deleted.is_(False),
                VirtualTagRuleCondition.type == ConditionTypes.CLOUD_IS,
                VirtualTagRuleCondition.meta_info.in_(list(cloud_account_ids)),
            ).first()
            if not has_account:
                return False
        return True

    def _format(self, item, with_stats=False, with_allocation_values=True):
        result = item.to_dict()
        result['value_limits'] = [
            limit.to_dict() for limit in item.value_limits
            if not limit.deleted]
        if with_allocation_values:
            result['allocation_values'] = self._allocation_values(item)
        if with_stats:
            result['stats'] = self._value_stats(item)
        return result

    def _allocation_values(self, item):
        values = {
            row[0] for row in self.session.query(
                VirtualTagRuleAllocation.value
            ).join(
                VirtualTagRuleBranch,
                VirtualTagRuleAllocation.branch_id == VirtualTagRuleBranch.id,
            ).join(
                VirtualTagRule,
                VirtualTagRuleBranch.rule_id == VirtualTagRule.id,
            ).join(
                VirtualTag,
                VirtualTagRule.virtual_tag_id == VirtualTag.id,
            ).filter(
                VirtualTag.organization_id == item.organization_id,
                VirtualTag.deleted.is_(False),
                VirtualTag.key == item.key,
                VirtualTagRule.deleted.is_(False),
                VirtualTagRuleBranch.deleted.is_(False),
                VirtualTagRuleAllocation.deleted.is_(False),
            ).distinct()
            if row[0]
        }
        values.update(
            row[0] for row in self.session.query(
                VirtualTagValueLimit.value
            ).join(
                VirtualTag,
                VirtualTagValueLimit.virtual_tag_id == VirtualTag.id,
            ).filter(
                VirtualTag.organization_id == item.organization_id,
                VirtualTag.deleted.is_(False),
                VirtualTag.key == item.key,
                VirtualTagValueLimit.deleted.is_(False),
            ).distinct()
            if row[0]
        )
        return sorted(values)

    def get_info(self, item_id):
        item = self.get(item_id)
        if not item:
            raise NotFoundException(
                Err.OE0002, [VirtualTag.__name__, item_id])
        return self._format(item, with_stats=True)

    def _current_month_range(self):
        now = datetime.now(timezone.utc)
        start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        last_day = monthrange(now.year, now.month)[1]
        end = datetime(
            now.year, now.month, last_day, 23, 59, 59, tzinfo=timezone.utc)
        return start, end

    def _value_stats(self, item):
        stats = defaultdict(
            lambda: {'resource_count': 0, 'cost': 0.0, 'forecast': 0.0})
        quarter_key = 'virtual_tags_by_quarter.%s.key' % item.quarter
        resources = list(self.resources_collection.find(
            {'deleted_at': 0, '$or': [
                {'virtual_tags.key': item.key},
                {quarter_key: item.key},
            ]},
            ['virtual_tags', 'virtual_tags_by_quarter']))
        rows = []
        for resource in resources:
            for alloc in virtual_tags_for_quarter(resource, item.quarter):
                if alloc.get('key') != item.key:
                    continue
                value = alloc.get('value')
                stats[value]['resource_count'] += 1
                rows.append({
                    'id': resource['_id'],
                    'group_field': value,
                    'share': float(alloc.get('share') or 0),
                })
        cloud_accounts = self.session.query(CloudAccount).filter(
            CloudAccount.organization_id == item.organization_id,
            CloudAccount.deleted.is_(False),
        ).all()
        cloud_account_ids = [account.id for account in cloud_accounts]
        if rows and cloud_account_ids:
            try:
                start, end = self._current_month_range()
                expenses = self.execute_clickhouse(
                    query="""
                        SELECT resources.group_field,
                            sum(cost * sign * resources.share / 100)
                        FROM expenses
                        JOIN resources ON expenses.resource_id = resources.id
                        WHERE expenses.date >= %(start_date)s
                            AND expenses.date <= %(end_date)s
                            AND cloud_account_id in %(cloud_account_ids)s
                        GROUP BY resources.group_field
                    """,
                    parameters={
                        'start_date': start,
                        'end_date': end,
                        'cloud_account_ids': cloud_account_ids,
                    },
                    external_data=ExternalDataConverter()([{
                        'name': 'resources',
                        'structure': [
                            ('id', 'String'),
                            ('group_field', 'Nullable(String)'),
                            ('share', 'Float64'),
                        ],
                        'data': rows,
                    }]),
                )
                for value, cost in expenses:
                    stats[value]['cost'] = cost
                    stats[value]['forecast'] = ExpenseQuery.get_monthly_forecast(
                        cost, cost)
            except Exception:
                LOG.exception('Failed to load virtual tag costs')
        limits = {
            limit.value: limit.limit
            for limit in item.value_limits if not limit.deleted
        }
        return [
            {
                'value': value,
                'resource_count': data['resource_count'],
                'cost': round(data['cost']),
                'forecast': round(data['forecast']),
                'limit': limits.get(value),
                'over_limit': (
                    limits.get(value) is not None
                    and data['cost'] > limits[value]),
            }
            for value, data in sorted(stats.items(), key=lambda item: item[0] or '')
        ]

    def create(self, **kwargs):
        mode = kwargs.get('mode', VirtualTagModes.ASSIGNMENT.value)
        if mode not in VirtualTagModes.values():
            raise WrongArgumentsException(Err.OE0579, [mode])
        kwargs['mode'] = VirtualTagModes(mode)
        if not kwargs.get('quarter'):
            kwargs['quarter'] = current_quarter()
        else:
            kwargs['quarter'] = check_quarter(kwargs['quarter'])
        item = super().create(**kwargs)
        return self._format(item)

    def edit(self, item_id, **kwargs):
        if 'mode' in kwargs:
            mode = kwargs['mode']
            if mode not in VirtualTagModes.values():
                raise WrongArgumentsException(Err.OE0579, [mode])
            kwargs['mode'] = VirtualTagModes(mode)
        item = super().edit(item_id, **kwargs)
        if not item:
            raise NotFoundException(
                Err.OE0002, [VirtualTag.__name__, item_id])
        if any(key in kwargs for key in ('key', 'mode', 'source_tag_key')):
            from rest_api.rest_api_server.controllers.virtual_tag_apply import (
                VirtualTagApplyController)
            VirtualTagApplyController(
                self.session, self._config, self.token
            ).reapply_organization(
                item.organization_id, quarter=item.quarter)
        return self._format(item)

    def delete(self, item_id):
        item = self.get(item_id)
        if not item:
            raise NotFoundException(
                Err.OE0002, [VirtualTag.__name__, item_id])
        now = opttime.utcnow_timestamp()
        for rule in item.rules:
            if not rule.deleted:
                rule.deleted_at = now
                for branch in rule.branches:
                    if not branch.deleted:
                        branch.deleted_at = now
        for limit in item.value_limits:
            if not limit.deleted:
                limit.deleted_at = now
        super().delete(item_id)

    def replace_value_limits(self, item_id, limits):
        item = self.get(item_id)
        if not item:
            raise NotFoundException(
                Err.OE0002, [VirtualTag.__name__, item_id])
        check_list_attribute('value_limits', limits)
        now = opttime.utcnow_timestamp()
        for existing in item.value_limits:
            if not existing.deleted:
                existing.deleted_at = now
        for row in limits:
            check_dict_attribute('value_limit', row)
            check_string_attribute('value', row.get('value'))
            check_int_attribute('limit', row.get('limit'), min_length=0)
            self.session.add(VirtualTagValueLimit(
                virtual_tag_id=item.id,
                value=row['value'],
                limit=row['limit'],
            ))
        self.session.commit()
        return self._format(self.get(item_id))

    def copy_quarter(self, organization_id, source_quarter, target_quarter):
        self.check_organization(organization_id)
        source_quarter = check_quarter(source_quarter, 'source_quarter')
        target_quarter = check_quarter(target_quarter, 'target_quarter')
        if source_quarter == target_quarter:
            raise WrongArgumentsException(
                Err.OE0218, ['target_quarter', target_quarter])
        targets = self.session.query(VirtualTag).filter(
            VirtualTag.organization_id == organization_id,
            VirtualTag.deleted.is_(False),
            VirtualTag.quarter == target_quarter,
        ).all()
        for item in targets:
            self.delete(item.id)
        sources = self.session.query(VirtualTag).filter(
            VirtualTag.organization_id == organization_id,
            VirtualTag.deleted.is_(False),
            VirtualTag.quarter == source_quarter,
        ).order_by(VirtualTag.created_at).all()
        copied = []
        for vt in sources:
            copied.append(self._clone_virtual_tag(vt, target_quarter))
        self.session.commit()
        from rest_api.rest_api_server.controllers.virtual_tag_apply import (
            VirtualTagApplyController)
        apply_result = VirtualTagApplyController(
            self.session, self._config, self.token
        ).reapply_organization(organization_id, quarter=target_quarter)
        return {
            'source_quarter': source_quarter,
            'target_quarter': target_quarter,
            'copied': len(copied),
            'virtual_tags': [self._format(item) for item in copied],
            **apply_result,
        }

    def _clone_virtual_tag(self, source, target_quarter):
        clone = VirtualTag(
            organization_id=source.organization_id,
            key=source.key,
            name=source.name,
            mode=source.mode,
            source_tag_key=source.source_tag_key,
            quarter=target_quarter,
        )
        self.session.add(clone)
        self.session.flush()
        for limit in source.value_limits:
            if limit.deleted:
                continue
            self.session.add(VirtualTagValueLimit(
                virtual_tag_id=clone.id,
                value=limit.value,
                limit=limit.limit,
            ))
        for rule in source.rules:
            if rule.deleted:
                continue
            new_rule = VirtualTagRule(
                organization_id=rule.organization_id,
                virtual_tag_id=clone.id,
                name=rule.name,
                priority=rule.priority,
                active=rule.active,
                creator_id=rule.creator_id,
            )
            for branch in rule.branches:
                if branch.deleted:
                    continue
                new_branch = VirtualTagRuleBranch(priority=branch.priority)
                for cond in branch.conditions:
                    if cond.deleted:
                        continue
                    new_branch.conditions.append(VirtualTagRuleCondition(
                        type=cond.type,
                        meta_info=cond.meta_info,
                    ))
                for alloc in branch.allocations:
                    if alloc.deleted:
                        continue
                    new_branch.allocations.append(VirtualTagRuleAllocation(
                        value=alloc.value,
                        share=alloc.share,
                    ))
                new_rule.branches.append(new_branch)
            self.session.add(new_rule)
            clone.rules.append(new_rule)
        return clone

    def search_resources(self, organization_id, search=None,
                         cloud_account_id=None, limit=50):
        self.check_organization(organization_id)
        cloud_ids = [
            row[0] for row in self.session.query(CloudAccount.id).filter(
                CloudAccount.organization_id == organization_id,
                CloudAccount.deleted.is_(False),
            ).all()]
        if cloud_account_id:
            if isinstance(cloud_account_id, str):
                cloud_account_id = [cloud_account_id]
            cloud_ids = [cid for cid in cloud_ids if cid in set(cloud_account_id)]
        match = {
            'deleted_at': 0,
            '$or': [
                {'cloud_account_id': {'$in': cloud_ids}},
                {'organization_id': organization_id},
            ],
        }
        search = (search or '').strip()
        if search:
            match['$and'] = [{'$or': [
                {'name': {'$regex': search, '$options': 'i'}},
                {'cloud_resource_id': {'$regex': search, '$options': 'i'}},
            ]}]
        cursor = self.resources_collection.find(
            match,
            ['name', 'cloud_resource_id', 'cloud_account_id'],
        ).limit(min(int(limit or 50), 100))
        return {
            'resources': [
                {
                    'id': doc['_id'],
                    'name': doc.get('name'),
                    'cloud_resource_id': doc.get('cloud_resource_id'),
                    'cloud_account_id': doc.get('cloud_account_id'),
                }
                for doc in cursor
            ]
        }


class VirtualTagRuleController(BaseController, OrganizationValidatorMixin):
    def _get_model_type(self):
        return VirtualTagRule

    def _get_virtual_tag(self, virtual_tag_id, organization_id=None):
        query = self.session.query(VirtualTag).filter(
            VirtualTag.id == virtual_tag_id,
            VirtualTag.deleted.is_(False),
        )
        if organization_id:
            query = query.filter(VirtualTag.organization_id == organization_id)
        item = query.one_or_none()
        if not item:
            raise NotFoundException(
                Err.OE0002, [VirtualTag.__name__, virtual_tag_id])
        return item

    def list(self, organization_id, virtual_tag_id=None, **kwargs):
        self.check_organization(organization_id)
        query = self.session.query(VirtualTagRule).filter(
            VirtualTagRule.organization_id == organization_id,
            VirtualTagRule.deleted.is_(False),
        )
        if virtual_tag_id:
            query = query.filter(
                VirtualTagRule.virtual_tag_id == virtual_tag_id)
        rules = query.options(
            selectinload(VirtualTagRule.branches).selectinload(
                VirtualTagRuleBranch.conditions),
            selectinload(VirtualTagRule.branches).selectinload(
                VirtualTagRuleBranch.allocations),
        ).order_by(
            VirtualTagRule.virtual_tag_id, VirtualTagRule.priority).all()
        return {'virtual_tag_rules': [rule.to_dict() for rule in rules]}

    def get_info(self, item_id):
        item = self.get(item_id)
        if not item:
            raise NotFoundException(
                Err.OE0002, [VirtualTagRule.__name__, item_id])
        return item.to_dict()

    def _next_priority(self, virtual_tag_id):
        last = self.session.query(VirtualTagRule).filter(
            VirtualTagRule.virtual_tag_id == virtual_tag_id,
            VirtualTagRule.deleted.is_(False),
        ).order_by(VirtualTagRule.priority.desc()).first()
        return (last.priority + 1) if last else 1

    def _mark_rule_dirty(self, rule):
        now = opttime.utcnow_timestamp()
        rule.updated_at = max(now, (rule.last_applied_at or 0) + 1)

    def delete(self, item_id):
        rule = self.get(item_id)
        vt_id = rule.virtual_tag_id if rule else None
        item = super().delete(item_id)
        if vt_id:
            siblings = self.session.query(VirtualTagRule).filter(
                VirtualTagRule.virtual_tag_id == vt_id,
                VirtualTagRule.deleted.is_(False),
            ).all()
            for sibling in siblings:
                self._mark_rule_dirty(sibling)
            self.session.commit()
        return item

    def create_rule(self, user_id, organization_id, **kwargs):
        self.check_organization(organization_id)
        virtual_tag_id = kwargs.get('virtual_tag_id')
        if not virtual_tag_id:
            raise_not_provided_exception('virtual_tag_id')
        vt = self._get_virtual_tag(virtual_tag_id, organization_id)
        check_string_attribute('name', kwargs.get('name'))
        branches = kwargs.get('branches')
        check_list_attribute('branches', branches)
        if not branches:
            raise_not_provided_exception('branches')
        self._validate_branches(vt, branches)
        self._validate_cloud_is_unique(
            vt.id, branches, exclude_rule_id=None)
        employee = EmployeeController(
            self.session, self._config, self.token
        ).get_employee_by_user_and_organization(
            user_id, organization_id=organization_id)
        active = kwargs.get('active', True)
        if active is not None:
            check_bool_attribute('active', active)
        priority = kwargs.get('priority') or self._next_priority(vt.id)
        check_int_attribute('priority', priority, min_length=1)
        rule = VirtualTagRule(
            organization_id=organization_id,
            virtual_tag_id=vt.id,
            name=kwargs['name'],
            priority=priority,
            active=True if active is None else active,
            creator_id=employee.id,
        )
        self._mark_rule_dirty(rule)
        self._attach_branches(rule, branches)
        self.session.add(rule)
        self.session.commit()
        return rule.to_dict()

    def edit_rule(self, item_id, **kwargs):
        rule = self.get(item_id)
        if not rule:
            raise NotFoundException(
                Err.OE0002, [VirtualTagRule.__name__, item_id])
        vt = self._get_virtual_tag(rule.virtual_tag_id)
        if 'name' in kwargs:
            check_string_attribute('name', kwargs['name'])
            rule.name = kwargs['name']
        if 'active' in kwargs:
            check_bool_attribute('active', kwargs['active'])
            rule.active = kwargs['active']
        if 'priority' in kwargs:
            check_int_attribute('priority', kwargs['priority'], min_length=1)
            rule.priority = kwargs['priority']
        if 'branches' in kwargs:
            branches = kwargs['branches']
            check_list_attribute('branches', branches)
            if not branches:
                raise_not_provided_exception('branches')
            self._validate_branches(vt, branches)
            self._validate_cloud_is_unique(
                vt.id, branches, exclude_rule_id=rule.id)
            now = opttime.utcnow_timestamp()
            for branch in rule.branches:
                if not branch.deleted:
                    branch.deleted_at = now
            self._attach_branches(rule, branches)
        self._mark_rule_dirty(rule)
        self.session.add(rule)
        self.session.commit()
        return rule.to_dict()

    def _attach_branches(self, rule, branches):
        for index, payload in enumerate(branches):
            branch = VirtualTagRuleBranch(
                priority=payload.get('priority') or (index + 1))
            for cond in payload.get('conditions') or []:
                branch.conditions.append(VirtualTagRuleCondition(
                    type=ConditionTypes(cond['type']),
                    meta_info=cond.get('meta_info'),
                ))
            for alloc in payload.get('allocations') or []:
                branch.allocations.append(VirtualTagRuleAllocation(
                    value=alloc['value'],
                    share=alloc['share'],
                ))
            rule.branches.append(branch)

    def _validate_branches(self, virtual_tag, branches):
        is_extract = virtual_tag.mode == VirtualTagModes.EXTRACT
        split_branch = False
        for payload in branches:
            check_dict_attribute('branch', payload)
            conditions = payload.get('conditions')
            check_list_attribute('conditions', conditions)
            RuleController._validate_conditions_data(conditions)
            allocations = payload.get('allocations') or []
            if is_extract:
                if allocations:
                    raise WrongArgumentsException(Err.OE0212, ['allocations'])
                continue
            check_list_attribute('allocations', allocations)
            if not allocations:
                raise_not_provided_exception('allocations')
            share_sum = 0
            for alloc in allocations:
                check_dict_attribute('allocation', alloc)
                check_string_attribute('value', alloc.get('value'))
                check_int_attribute(
                    'share', alloc.get('share'), min_length=1, max_length=100)
                share_sum += alloc['share']
            if share_sum != 100:
                raise WrongArgumentsException(Err.OE0575, [share_sum])
            if len(allocations) > 1 or any(
                    alloc['share'] != 100 for alloc in allocations):
                split_branch = True
        if split_branch and len(branches) > 1:
            raise WrongArgumentsException(Err.OE0576, [])

    def _collect_cloud_is(self, branches):
        ids = []
        for payload in branches:
            for cond in payload.get('conditions') or []:
                if cond.get('type') == ConditionTypes.CLOUD_IS.value:
                    ids.append(cond.get('meta_info'))
        return [cid for cid in ids if cid]

    def _validate_cloud_is_unique(self, virtual_tag_id, branches,
                                  exclude_rule_id=None):
        new_ids = set(self._collect_cloud_is(branches))
        if not new_ids:
            return
        query = self.session.query(VirtualTagRule).filter(
            VirtualTagRule.virtual_tag_id == virtual_tag_id,
            VirtualTagRule.deleted.is_(False),
        )
        if exclude_rule_id:
            query = query.filter(VirtualTagRule.id != exclude_rule_id)
        for rule in query.all():
            for branch in rule.branches:
                if branch.deleted:
                    continue
                for cond in branch.conditions:
                    if cond.deleted:
                        continue
                    if cond.type == ConditionTypes.CLOUD_IS and (
                            cond.meta_info in new_ids):
                        raise ConflictException(
                            Err.OE0577, [cond.meta_info, rule.name])
        accounts = self.session.query(CloudAccount.id).filter(
            CloudAccount.id.in_(new_ids),
            CloudAccount.deleted.is_(False),
        ).all()
        existing = {row[0] for row in accounts}
        missing = new_ids - existing
        if missing:
            raise WrongArgumentsException(
                Err.OE0005, ['cloud_is', next(iter(missing))])


class VirtualTagAsyncController(BaseAsyncControllerWrapper):
    def _get_controller_class(self):
        return VirtualTagController


class VirtualTagRuleAsyncController(BaseAsyncControllerWrapper):
    def _get_controller_class(self):
        return VirtualTagRuleController
