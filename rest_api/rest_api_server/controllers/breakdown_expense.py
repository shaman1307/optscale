import logging
from collections import defaultdict
from datetime import timezone

from rest_api.rest_api_server.controllers.base_async import (
    BaseAsyncControllerWrapper
)
from rest_api.rest_api_server.controllers.expense import (
    CleanExpenseController, NOT_SET_NAME, clickhouse_time_filter)
from rest_api.rest_api_server.exceptions import Err
from rest_api.rest_api_server.models.models import Pool
from rest_api.rest_api_server.utils import (
    get_nil_uuid, is_virtual_tag_breakdown, virtual_tag_breakdown_key,
    load_virtual_tag_cost_shares, virtual_tag_filter_values_for_key,
    virtual_tags_for_quarter, current_quarter, invoice_month_to_quarter,
    quarters_for_invoice_months)

from tools.optscale_exceptions.common_exc import WrongArgumentsException

from tools.optscale_data.clickhouse import ExternalDataConverter
from tools.optscale_time import utcfromtimestamp


LOG = logging.getLogger(__name__)
DAY_IN_SECONDS = 86400
DAYS_IN_YEAR = 365
SUBPOOL_BREAKDOWN = 'subpool'


class BreakdownBaseController(CleanExpenseController):
    JOIN_TRAFFIC_EXPENSES = False

    def split_params(self, organization_id, params):
        query_filters, data_filters, extra_filters = self._split_params(
            organization_id, params)
        extra_filters['breakdown_by'] = (
            extra_filters.get('breakdown_by')
            or query_filters.pop('breakdown_by', None)
            or data_filters.pop('breakdown_by', None)
        )
        return query_filters, data_filters, extra_filters

    def get_resources_data(self, organization_id, query_filters, data_filters,
                           extra_params):
        extra_params = dict(extra_params)
        extra_params['breakdown_by'] = (
            extra_params.get('breakdown_by')
            or query_filters.pop('breakdown_by', None)
        )
        extra_params['virtual_tag'] = (
            extra_params.get('virtual_tag')
            or query_filters.get('virtual_tag')
        )
        extra_params['invoice_months'] = self.invoice_months
        query = self.generate_filters_pipeline(
            organization_id, self.start_date, self.end_date,
            query_filters, data_filters)
        return self._aggregate_resource_data(query, **extra_params)

    @staticmethod
    def mongo_breakdown_field(breakdown_by):
        # Resources store pool_id only; subpool collapses series by pool name.
        if breakdown_by == SUBPOOL_BREAKDOWN:
            return 'pool_id'
        if is_virtual_tag_breakdown(breakdown_by):
            return None
        return breakdown_by

    def get_db_entities_info(self, organization_id, organization_cloud_acc,
                             unique_values):
        entities = self._get_join_entities(
            organization_id, organization_cloud_acc)
        entities = self.update_unique_values(unique_values, entities)
        return entities

    def update_unique_values(self, unique_values, entities):
        result = defaultdict(dict)
        for entity_name, v in self.JOINED_ENTITY_MAP.items():
            res_key, entity_key, fields = v
            keys = unique_values.pop(res_key, {})
            for key in keys:
                if not key or key in result[entity_name]:
                    continue
                entity = entities.get(entity_key, {}).get(key)
                result[entity_name][key] = {
                    i: entity[i] for i in fields
                }
        result.update(unique_values)
        return result

    @staticmethod
    def _get_breakdown_dates(start_date, end_date):
        first_breakdown = int(utcfromtimestamp(start_date).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
        ).timestamp())
        last_breakdown = int(utcfromtimestamp(end_date).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
        ).timestamp())
        return [x for x in range(first_breakdown, last_breakdown + 1,
                                 DAY_IN_SECONDS)]

    def get_breakdown_entity_map(self, entities, breakdown_by):
        if breakdown_by == SUBPOOL_BREAKDOWN:
            return entities.get(SUBPOOL_BREAKDOWN, {})
        entity_key = None
        for k, v in self.JOINED_ENTITY_MAP.items():
            if v[0] == breakdown_by:
                entity_key = k
                break
        if not entity_key:
            return {}
        return entities.get(entity_key, {})

    def build_subpool_name_map(self, organization_id, pool_ids):
        """Map pool_id -> display name and name -> legend entity."""
        pool_entities = self._get_object_entities(organization_id, Pool)
        id_to_name = {}
        name_entities = {}
        for pool_id in pool_ids:
            if not pool_id:
                id_to_name[pool_id] = None
                continue
            pool = pool_entities.get(pool_id) or {}
            name = pool.get('name') or NOT_SET_NAME
            id_to_name[pool_id] = name
            if name not in name_entities:
                name_entities[name] = {
                    'id': name,
                    'name': name,
                    'purpose': pool.get('purpose'),
                    'parent_id': pool.get('parent_id'),
                }
        if None in pool_ids or None in id_to_name.values():
            name_entities[None] = {
                'id': None,
                'name': NOT_SET_NAME,
                'purpose': NOT_SET_NAME,
            }
        return id_to_name, name_entities

    def remap_breakdown_keys_to_subpool(
            self, organization_id, resource_breakdown_map):
        pool_ids = set(resource_breakdown_map.values())
        id_to_name, name_entities = self.build_subpool_name_map(
            organization_id, pool_ids)
        remapped = {
            res_id: id_to_name.get(pool_id)
            for res_id, pool_id in resource_breakdown_map.items()
        }
        return remapped, name_entities

    def get_value_resource_type(self, value, is_cluster=False, is_env=False):
        if is_cluster:
            identity = self.CLUSTER_IDENTITY
        elif is_env:
            identity = self.ENVIRONMENT_IDENTITY
        else:
            identity = None
        return self.IDENTITY_DELIMITER.join(
            [value, identity]) if identity else value


class BreakdownExpenseController(BreakdownBaseController):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._previous_period_start = None

    @staticmethod
    def update_params(**params):
        if params.get('invoice_months'):
            return params
        start_date = params.get('start_date')
        end_date = params.get('end_date')
        start_dt = utcfromtimestamp(start_date).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
        end_dt = utcfromtimestamp(end_date).replace(
            hour=23, minute=59, second=59, microsecond=0, tzinfo=timezone.utc)
        params.update({
            'start_date': int(start_dt.timestamp()),
            'end_date': int(end_dt.timestamp())
        })
        return params

    def check_filters(self, filters, organization_id):
        super().check_filters(filters, organization_id)
        if self.invoice_months:
            return
        start_dt = filters.get('start_date')
        end_dt = filters.get('end_date')
        if (end_dt - start_dt) / DAY_IN_SECONDS > DAYS_IN_YEAR:
            raise WrongArgumentsException(Err.OE0515, [])

    def get(self, organization_id, **params):
        params = self.update_params(**params)
        return super().get(organization_id, **params)

    def fill_empty_days(self, breakdown):
        if self.invoice_months or self.start_date is None:
            return
        empty_days = list(filter(
            lambda x: x not in breakdown,
            self._get_breakdown_dates(self.start_date, self.end_date)
        ))
        breakdown.update({dt: {} for dt in empty_days})

    @property
    def previous_period_start(self):
        if self.invoice_months:
            return 0
        if not self._previous_period_start:
            interval = self.end_date - self.start_date + 1
            prev_start = self.start_date - interval
            self._previous_period_start = prev_start if prev_start > 0 else 0
        return self._previous_period_start

    def generate_filters_pipeline(self, organization_id, start_date, end_date,
                                  params, data_filters):
        if self.invoice_months:
            return super().generate_filters_pipeline(
                organization_id, start_date, end_date, params,
                data_filters)
        return super().generate_filters_pipeline(
            organization_id, self.previous_period_start, end_date, params,
            data_filters)

    def process_data(self, resources_data, organization_id, filters, **kwargs):
        breakdown_by = kwargs.get('breakdown_by')
        if is_virtual_tag_breakdown(breakdown_by):
            return self._process_virtual_tag_breakdown(
                resources_data, organization_id, breakdown_by)
        extracted_values = self._extract_values_from_data(
            resources_data, filters, organization_id,
            breakdown_by=breakdown_by)
        resource_cluster_map, resource_breakdown_map = extracted_values
        ids_for_pop = set()
        for k, v in resource_cluster_map.items():
            value = resource_breakdown_map.get(v)
            resource_breakdown_map[k] = value
            ids_for_pop.add(v)
        for pop_id in ids_for_pop:
            resource_breakdown_map.pop(pop_id, None)
        _, organization_cloud_accs = self.get_organization_and_cloud_accs(
            organization_id)
        if breakdown_by == SUBPOOL_BREAKDOWN:
            resource_breakdown_map, breakdown_entities = (
                self.remap_breakdown_keys_to_subpool(
                    organization_id, resource_breakdown_map))
        else:
            unique_values = {
                breakdown_by: set(resource_breakdown_map.values())}
            entities = self.get_db_entities_info(
                organization_id, organization_cloud_accs, unique_values)
            breakdown_entities = self.get_breakdown_entity_map(
                entities, breakdown_by)
        cloud_account_ids = list(map(lambda x: x.id, organization_cloud_accs))
        share_by_id = load_virtual_tag_cost_shares(
            self.resources_collection,
            list(resource_breakdown_map.keys()),
            filters.get('virtual_tag'),
            invoice_months=self.invoice_months)
        breakdown_expenses = self.get_breakdown_expenses(
            cloud_account_ids, resource_breakdown_map, share_by_id)
        result = self._get_base_result(
            breakdown_by, breakdown_expenses, breakdown_entities)
        return result

    def _get_base_result(self, breakdown_by, breakdown_expenses, entities_map):
        totals = {
            'total': 0,
            'previous_total': 0,
            'previous_range_start': self.previous_period_start,
        }
        breakdown = defaultdict(dict)
        counts = {}
        previous_period_dt = utcfromtimestamp(
            self.previous_period_start)
        start_dt = utcfromtimestamp(
            0 if self.start_date is None else self.start_date)
        for breakdown_date, day_info in breakdown_expenses.items():
            for k, cost in day_info.items():
                if k not in counts:
                    counts[k] = {'total': 0, 'previous_total': 0,
                                 **entities_map.get(k, {})}
                if breakdown_date < previous_period_dt:
                    continue
                if breakdown_date < start_dt:
                    totals['previous_total'] += cost
                    counts[k]['previous_total'] += cost
                    continue
                totals['total'] += cost
                dt_timestamp = int(breakdown_date.replace(
                    hour=0, minute=0, second=0, microsecond=0,
                    tzinfo=timezone.utc).timestamp())
                breakdown[dt_timestamp][k] = {
                    'cost': cost, **entities_map.get(k, {})
                }
                counts[k]['total'] += cost
        self.fill_empty_days(breakdown)
        res = {
            'counts': counts,
            'breakdown': breakdown,
            'start_date': self.start_date,
            'end_date': self.end_date,
            **totals
        }
        if breakdown_by:
            res['breakdown_by'] = breakdown_by
        if self.invoice_months:
            res['invoice_months'] = self.invoice_months
        return res

    def _extract_values_from_data(self, resources_data, input_filters,
                                  organization_id, breakdown_by):
        clustered_resources_map = {}
        input_ca_ids = input_filters.get('cloud_account_id', [])
        real_ca_filter = any(
            c_id and c_id != get_nil_uuid() for c_id in input_ca_ids)
        cluster_ids = []
        breakdown_map = {}
        last_run = self.get_last_run_ts_by_org_id(organization_id)
        clusters_to_exclude = set()
        for data in resources_data:
            _id = data.pop('_id')
            ca_id = _id.get('cloud_account_id')
            cluster_id = _id.get('cluster_id')
            cluster_type_id = _id.get('cluster_type_id')
            r_ids = data.pop('resources', [])
            is_cluster = False
            is_env = _id.pop('is_environment', False)
            is_cluster_parent = bool(
                cluster_type_id or (ca_id is None and cluster_id is None))
            if is_cluster_parent and real_ca_filter:
                # Members are listed on this CA; parent has no own expenses.
                continue
            if is_cluster_parent:
                is_cluster = True
                cluster_ids.extend(r_ids)
            if ca_id:
                if cluster_id and not input_ca_ids:
                    clustered_resources_map.update(
                        {r: cluster_id for r in r_ids})
                # When a CA filter is set, cluster parents never match the
                # member path. Keep members in the breakdown so the CA total
                # stays complete.
            for r in r_ids:
                field = self.mongo_breakdown_field(breakdown_by)
                value = _id.get(field)
                if breakdown_by == 'resource_type' and (is_cluster or is_env):
                    value = self.get_value_resource_type(
                        value, is_cluster, is_env)
                breakdown_map[r] = value
        sub_resources = self.resources_collection.find(
            {'cluster_id': {'$in': cluster_ids + list(set(
                clustered_resources_map.values()))}})
        for s in sub_resources:
            cluster_id = s['cluster_id']
            if (cluster_id not in clusters_to_exclude and
                    self._is_cluster_excluded_by_sub_res(
                        s, input_filters, last_run)):
                clusters_to_exclude.add(cluster_id)
            clustered_resources_map[s['_id']] = cluster_id
        if clusters_to_exclude:
            for res_id, cluster_id in clustered_resources_map.copy().items():
                if cluster_id in clusters_to_exclude:
                    breakdown_map.pop(res_id, None)
                    breakdown_map.pop(cluster_id, None)
                    clustered_resources_map.pop(res_id, None)
        return clustered_resources_map, breakdown_map

    def _process_virtual_tag_breakdown(
            self, resources_data, organization_id, breakdown_by):
        rows = []
        counts = defaultdict(set)
        for data in resources_data:
            ident = data.pop('_id')
            value = ident.get('virtual_tag_value')
            share = ident.get('share') or 0
            for resource_id in data.pop('resources', []):
                rows.append({
                    'id': resource_id,
                    'group_field': value,
                    'share': float(share),
                    'invoice_month': ident.get('invoice_month'),
                })
                counts[value].add(resource_id)
        _, organization_cloud_accs = self.get_organization_and_cloud_accs(
            organization_id)
        cloud_account_ids = list(map(lambda x: x.id, organization_cloud_accs))
        breakdown_expenses = self.get_virtual_tag_breakdown_expenses(
            cloud_account_ids, rows)
        entities_map = {
            value: {
                'id': value,
                'name': value if value is not None else NOT_SET_NAME,
            }
            for value in counts
        }
        result = self._get_base_result(
            breakdown_by, breakdown_expenses, entities_map)
        for value, entity in entities_map.items():
            result['counts'].setdefault(
                value, {'total': 0, 'previous_total': 0, **entity})
        return result

    def get_virtual_tag_breakdown_expenses(self, cloud_account_ids, rows):
        if not self.invoice_months:
            resource_ids = {row['id']: row['id'] for row in rows}
            raw = self.get_breakdown_expenses(cloud_account_ids, resource_ids)
            result = defaultdict(dict)
            for date, costs in raw.items():
                for row in rows:
                    cost = costs.get(row['id']) or 0
                    value = row['group_field']
                    result[date][value] = (
                        result[date].get(value, 0)
                        + cost * float(row.get('share') or 0) / 100.0)
            return result
        time_sql, time_params = clickhouse_time_filter(
            invoice_months=self.invoice_months,
            start_date=None, end_date=None,
            date_column='expenses.date')
        expenses = self.execute_clickhouse(
            query=f"""
                SELECT
                    resources.group_field, date,
                    sum(cost*sign * resources.share / 100)
                FROM expenses
                JOIN resources ON expenses.resource_id = resources.id
                    AND expenses.invoice_month = resources.invoice_month
                WHERE {time_sql}
                    AND cloud_account_id in %(cloud_account_ids)s
                GROUP BY resources.group_field, date
            """,
            parameters={
                **time_params,
                'cloud_account_ids': list(cloud_account_ids),
            },
            external_data=ExternalDataConverter()([{
                'name': 'resources',
                'structure': [
                    ('id', 'String'),
                    ('invoice_month', 'String'),
                    ('group_field', 'Nullable(String)'),
                    ('share', 'Float64'),
                ],
                'data': [
                    {
                        'id': row['id'],
                        'invoice_month': row.get('invoice_month') or '',
                        'group_field': row['group_field'],
                        'share': float(row.get('share') or 0),
                    }
                    for row in rows
                ],
            }]),
        )
        result = defaultdict(dict)
        for value, date, cost in expenses:
            result[date][value] = result[date].get(value, 0) + cost
        return result

    def get_breakdown_expenses(self, cloud_account_ids, resources,
                               share_by_id=None):
        share_by_id = share_by_id or {}
        mixed = any(isinstance(value, dict) for value in share_by_id.values())
        if mixed:
            external_table = []
            for resource_id, group_field in resources.items():
                month_shares = share_by_id.get(resource_id) or {}
                if not isinstance(month_shares, dict):
                    month_shares = {
                        month: month_shares
                        for month in (self.invoice_months or [])
                    }
                for month, share in month_shares.items():
                    external_table.append({
                        'id': resource_id,
                        'invoice_month': month,
                        'group_field': group_field,
                        'share': float(share) * 100.0,
                    })
            join_sql = (
                'JOIN resources ON expenses.resource_id = resources.id '
                'AND expenses.invoice_month = resources.invoice_month')
            structure = [
                ('id', 'String'),
                ('invoice_month', 'String'),
                ('group_field', 'Nullable(String)'),
                ('share', 'Float64'),
            ]
        else:
            external_table = [
                {
                    'id': k,
                    'group_field': v,
                    'share': float(share_by_id.get(k, 1.0)) * 100.0,
                }
                for k, v in resources.items()
            ]
            join_sql = 'JOIN resources ON expenses.resource_id = resources.id'
            structure = [
                ('id', 'String'),
                ('group_field', 'Nullable(String)'),
                ('share', 'Float64'),
            ]
        start_dt = utcfromtimestamp(self.previous_period_start)
        end_dt = utcfromtimestamp(self.end_date) if self.end_date else None
        time_sql, time_params = clickhouse_time_filter(
            invoice_months=self.invoice_months,
            start_date=start_dt, end_date=end_dt,
            date_column='expenses.date')
        expenses = self.execute_clickhouse(
            query=f"""
                SELECT
                    resources.group_field, date,
                    sum(cost*sign * resources.share / 100)
                FROM expenses
                {join_sql}
                WHERE {time_sql}
                    AND cloud_account_id in %(cloud_account_ids)s
                GROUP BY resources.group_field, date
            """,
            parameters={
                **time_params,
                'cloud_account_ids': list(cloud_account_ids)
            },
            external_data=ExternalDataConverter()([{
                'name': 'resources',
                'structure': structure,
                'data': external_table
            }]),
        )
        result = defaultdict(dict)
        for value, date, cost in expenses:
            result[date][value] = result[date].get(value, 0) + cost
        return result

    def _aggregate_resource_data(self, match_query, **kwargs):
        breakdown_by = kwargs.get('breakdown_by')
        if is_virtual_tag_breakdown(breakdown_by):
            key = virtual_tag_breakdown_key(breakdown_by)
            allowed = virtual_tag_filter_values_for_key(
                kwargs.get('virtual_tag'), key)
            grouped = defaultdict(lambda: {'resources': set()})
            months = list(self.invoice_months or [])
            quarters = (
                quarters_for_invoice_months(months)
                or [current_quarter()])
            for resource in self.resources_collection.find(match_query):
                if months:
                    month_pairs = [
                        (month, invoice_month_to_quarter(month))
                        for month in months]
                else:
                    month_pairs = [(None, quarters[0])]
                for month, quarter in month_pairs:
                    for alloc in virtual_tags_for_quarter(resource, quarter):
                        if not isinstance(alloc, dict):
                            continue
                        if alloc.get('key') != key:
                            continue
                        if allowed is not None and alloc.get('value') not in allowed:
                            continue
                        ident = (
                            resource.get('cloud_account_id'),
                            resource.get('cluster_id'),
                            resource.get('is_environment'),
                            alloc.get('value'),
                            alloc.get('share') or 0,
                            month,
                        )
                        grouped[ident]['resources'].add(resource['_id'])
            return [
                {
                    '_id': {
                        'cloud_account_id': ident[0],
                        'cluster_id': ident[1],
                        'is_environment': ident[2],
                        'virtual_tag_value': ident[3],
                        'share': ident[4],
                        'invoice_month': ident[5],
                    },
                    'resources': list(info['resources']),
                }
                for ident, info in grouped.items()
            ]
        group_by = self.mongo_breakdown_field(breakdown_by)
        group_dict = {
            'cloud_account_id': '$cloud_account_id',
            'cluster_id': '$cluster_id',
            'cluster_type_id': {'$ifNull': ['$cluster_type_id', None]},
            'is_environment': '$is_environment',
            'day': {'$trunc': {
                '$divide': ['$first_seen', DAY_IN_SECONDS]}},
        }
        if group_by and group_by not in group_dict:
            group_dict[group_by] = '$%s' % group_by
        group_stage = {
            '_id': group_dict,
            'resources': {'$addToSet': '$_id'},
        }
        return self.resources_collection.aggregate([
            {'$match': match_query},
            {'$group': group_stage}
        ], allowDiskUse=True)


class BreakdownExpenseAsyncController(BaseAsyncControllerWrapper):

    def _get_controller_class(self):
        return BreakdownExpenseController
