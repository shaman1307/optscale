import logging
from collections import defaultdict

from tools.optscale_exceptions.common_exc import (
    FailedDependency, WrongArgumentsException
)

from rest_api.rest_api_server.controllers.base_async import (
    BaseAsyncControllerWrapper
)
from rest_api.rest_api_server.controllers.expense import CleanExpenseController
from rest_api.rest_api_server.exceptions import Err
from rest_api.rest_api_server.utils import (
    encode_string, get_nil_uuid, virtual_tags_for_quarter,
    quarters_for_invoice_months, current_quarter)

LOG = logging.getLogger(__name__)
DAY_IN_SECONDS = 86400

FACET_CORE = 'core'
FACET_TAG = 'tag'
FACET_META = 'meta'
FACET_VIRTUAL_TAG = 'virtual_tag'
VALID_FACETS = {FACET_CORE, FACET_TAG, FACET_META, FACET_VIRTUAL_TAG}
DEFAULT_FACETS = frozenset({FACET_CORE})

CORE_PROJECT_FIELDS = [
    'cloud_account_id', 'cluster_type_id', 'is_environment', 'first_seen',
    'service_name', 'pool_id', 'employee_id', 'k8s_node',
    'region', 'resource_type', 'k8s_namespace', 'k8s_service',
    'account_locator', 'active', 'constraint_violated', 'recommendations',
]


class AvailableFiltersController(CleanExpenseController):
    JOIN_TRAFFIC_EXPENSES = False

    def split_params(self, organization_id, params):
        query_filters, data_filters, extra_filters = self._split_params(
            organization_id, params)
        extra_filters['last_recommend_run'] = self.get_last_run_ts_by_org_id(
            organization_id)
        return query_filters, data_filters, extra_filters

    def get_extended_input_filters(self, filters):
        input_filters = filters.copy()
        pool_ids = input_filters.pop('pool_id', [])
        if pool_ids:
            input_filters['pool_id'] = [
                x.removesuffix(self.WITH_SUBPOOLS_SIGN) for x in pool_ids]
        return input_filters

    def identify_resource(self, e):
        if e.get('cluster_type_id'):
            return self.CLUSTER_IDENTITY
        elif e.get('is_environment'):
            return self.ENVIRONMENT_IDENTITY
        else:
            return self.REGULAR_IDENTITY

    @staticmethod
    def _get_base_result(filter_values):
        return {
            'filter_values': filter_values
        }

    @staticmethod
    def _parse_facets(raw):
        if raw is None:
            return set(DEFAULT_FACETS)
        if isinstance(raw, (list, tuple)):
            parts = []
            for item in raw:
                parts.extend(str(item).split(','))
        else:
            parts = str(raw).split(',')
        facets = {part.strip() for part in parts if part and part.strip()}
        if not facets:
            return set(DEFAULT_FACETS)
        invalid = facets - VALID_FACETS
        if invalid:
            raise WrongArgumentsException(
                Err.OE0212, [', '.join(sorted(invalid))])
        return facets

    def process_data(self, resources_data, organization_id, filters, **kwargs):
        input_filters = self.get_extended_input_filters(filters)
        _, organization_cloud_accs = self.get_organization_and_cloud_accs(
            organization_id)
        entities = self._get_join_entities(
            organization_id, organization_cloud_accs)
        unique_values = self.collect_unique_values(resources_data, entities)
        filter_values = self.get_filter_values(unique_values, input_filters)
        return self._get_base_result(filter_values)

    def collect_unique_values(self, resource_data, entities):
        result = defaultdict(dict)
        r_sets = defaultdict(set)
        for r in resource_data:
            _id = r.pop('_id')
            cloud_account_id = _id.get('cloud_account_id')
            cloud_account = entities.get(
                'cloud_account_id', {}).get(cloud_account_id, {})
            # Legacy aggregations may still emit cloud_resource_ids; ignore.
            r.pop('cloud_resource_ids', None)
            for entity_name, v in self.JOINED_ENTITY_MAP.items():
                res_key, entity_key, fields = v
                keys = r.pop(res_key, {})
                for key in keys:
                    if key in result[entity_name]:
                        continue
                    entity = entities.get(entity_key, {}).get(key)
                    result[entity_name][key] = {
                        i: entity[i] for i in fields
                    } if entity else key
            for field in ['service_name', 'region', 'k8s_node',
                          'k8s_service', 'k8s_namespace', 'account_locator']:
                r_keys = r.pop(field, {})
                for r_key in r_keys:
                    if r_key not in result[field]:
                        result[field][r_key] = {
                            'name': r_key,
                            'cloud_type': cloud_account.get('type')
                        } if r_key else r_key
            resource_types = r.pop('resource_type', {})
            rt_identifier = self.identify_resource(_id)
            for resource_type in resource_types:
                rt_key = resource_type, rt_identifier
                if rt_key not in result['resource_type']:
                    result['resource_type'][rt_key] = {
                        'name': rt_key[0], 'type': rt_key[1]
                    }
            tags = r.pop('tags', {})
            decoded_tags = []
            for tag in tags:
                tag = encode_string(tag, decode=True) if tag else tag
                decoded_tags.append(tag)
            if decoded_tags:
                for t in ['tag', 'without_tag']:
                    r_sets[t].update(decoded_tags)
            for k, v in r.items():
                r_sets[k].update(v)
        result.update(r_sets)
        # add all available optscale entities
        for entity_name, v in self.JOINED_ENTITY_MAP.items():
            _, entity_key, fields = v
            entities_dict = entities.get(entity_key, {})
            for entity_id, entity in entities_dict.items():
                result[entity_name].update({
                    entity_id: {f: entity[f] for f in fields}})
        # Resolve cloud_type from cloud accounts — do not upload millions of
        # cloud_resource_ids to ClickHouse as external JOIN data (send timeout).
        ca_type_map = {
            ca_id: ca.get('type')
            for ca_id, ca in entities.get('cloud_account_id', {}).items()
            if ca_id and ca.get('type')
        }
        if ca_type_map:
            result.update(self.get_traffic_filters(
                list(ca_type_map.keys()), ca_type_map))
        return result

    def get_traffic_filters(self, cloud_account_ids, cloud_account_type_map):
        if self.invoice_months:
            return {}
        cloud_account_ids = [c_id for c_id in cloud_account_ids if c_id]
        if not cloud_account_ids:
            return {}
        try:
            res_filters = self.execute_clickhouse(
                query="""
                    SELECT DISTINCT cloud_account_id, from, to
                    FROM traffic_expenses
                    WHERE cloud_account_id in %(cloud_account_ids)s
                        AND date >= %(start_date)s
                        AND date <= %(end_date)s
                """,
                parameters={
                    'start_date': self.start_date,
                    'end_date': self.end_date,
                    'cloud_account_ids': cloud_account_ids,
                },
            )
        except Exception:
            # Traffic facets are optional; pool/cloud_account must still load.
            LOG.exception('Failed to load traffic filters from ClickHouse')
            return {}
        result_set = defaultdict(set)
        for cloud_account_id, _from, _to in res_filters:
            cloud_type = cloud_account_type_map.get(cloud_account_id)
            if not cloud_type:
                continue
            result_set['traffic_from'].add((cloud_type, _from))
            result_set['traffic_to'].add((cloud_type, _to))
        result = {}
        for k, values in result_set.items():
            regions = [{'name': v[1], 'cloud_type': v[0]} for v in values]
            if regions:
                regions.append('ANY')
            result[k] = regions
        return result

    def generate_filters_pipeline(self, organization_id, start_date, end_date,
                                  params, data_filters):
        query = super().generate_filters_pipeline(
            organization_id, start_date, end_date, params,
            data_filters)
        for part in query['$or']:
            part['$and'].append({'cluster_id': None})
        return query

    def get_filter_values(self, uniq_values_map, filters):
        filters['pool'] = filters.pop('pool_id', [])
        filters['cloud_account'] = filters.pop('cloud_account_id', [])
        filters['owner'] = filters.pop('owner_id', [])
        for field in ['active', 'recommendations', 'constraint_violated']:
            value = filters.pop(field, None)
            filters[field] = [] if value is None else value
        for k in filters:
            if isinstance(filters[k], list):
                filters[k] = [x if x != get_nil_uuid() else None
                              for x in filters[k]]
        return self._get_filter_values(uniq_values_map, filters)

    def _get_filter_values(self, uniq_values_map, filters):
        filter_values = {}
        for field, values in uniq_values_map.items():
            if field in self.JOINED_ENTITY_MAP and filters.get(field, []):
                values = [
                    v for k, v in values.items() if k in filters.get(field, [])
                ]
            if isinstance(values, dict):
                values = values.values()
            filter_values[field] = list(values)
        for src_k, dst_k in [('tag', 'without_tag'), ('without_tag', 'tag')]:
            if filters.get(src_k):
                filter_values[dst_k] = list(set(
                    filter_values.pop(dst_k, [])) - set(filters[src_k]))
        return filter_values

    def _aggregate_core_resource_data(self, match_query, **kwargs):
        last_recommend_run = kwargs['last_recommend_run']
        collected_filters = [
            'service_name', 'pool_id', 'employee_id', 'k8s_node', 'region',
            'resource_type', 'k8s_namespace', 'k8s_service', 'account_locator',
            'cloud_account_id'
        ]
        group_stage = {
            f: {'$addToSet': {'$ifNull': ['$%s' % f, None]}}
            for f in collected_filters
        }
        for bool_field in ['active', 'constraint_violated']:
            group_stage.update({
                bool_field: {'$addToSet': {'$cond': {
                    'if': {'$eq': ['$%s' % bool_field, True]},
                    'then': True,
                    'else': False
                }}},
            })
        group_stage.update({
            'recommendations': {'$addToSet': {'$cond': {
                'if': {'$and': [
                    {'$ne': ['$recommendations', None]},
                    {'$gte': [
                        '$recommendations.run_timestamp', last_recommend_run
                    ]}
                ]},
                'then': True,
                'else': False
            }}}
        })
        group_stage.update({
            '_id': {
                'cloud_account_id': '$cloud_account_id',
                'cluster_type_id': '$cluster_type_id',
                'is_environment': '$is_environment',
                'day': {'$trunc': {
                    '$divide': ['$first_seen', DAY_IN_SECONDS]}},
            },
        })
        project_stage = {field: 1 for field in CORE_PROJECT_FIELDS}
        return self.resources_collection.aggregate([
            {'$match': match_query},
            {'$project': project_stage},
            {'$group': group_stage},
        ], allowDiskUse=True)

    def _collect_object_keys(self, match_query, object_field, decode_keys=False):
        pipeline = [
            {'$match': match_query},
            {'$project': {object_field: 1}},
            {
                '$project': {
                    'keys': {
                        '$map': {
                            'input': {
                                '$objectToArray': {
                                    '$ifNull': ['$%s' % object_field, {}]
                                }
                            },
                            'as': 'entry',
                            'in': '$$entry.k'
                        }
                    }
                }
            },
            {'$unwind': '$keys'},
            {'$group': {'_id': None, 'keys': {'$addToSet': '$keys'}}},
        ]
        rows = list(self.resources_collection.aggregate(
            pipeline, allowDiskUse=True))
        if not rows:
            return []
        keys = []
        for key in rows[0].get('keys') or []:
            if key and decode_keys:
                keys.append(encode_string(key, decode=True))
            else:
                keys.append(key)
        return keys

    def _collect_virtual_tags(self, match_query):
        quarters = (
            quarters_for_invoice_months(self.invoice_months)
            or [current_quarter()])
        pairs = {}
        for resource in self.resources_collection.find(
                match_query, ['virtual_tags', 'virtual_tags_by_quarter']):
            for quarter in quarters:
                for alloc in virtual_tags_for_quarter(resource, quarter):
                    if not isinstance(alloc, dict) or not alloc.get('key'):
                        continue
                    pairs[(alloc.get('key'), alloc.get('value'))] = True
        result = [
            {'key': key, 'value': value}
            for key, value in sorted(pairs, key=lambda item: (
                item[0] or '', item[1] or ''))
        ]
        result.insert(0, {'key': None, 'value': None})
        return result

    def _apply_tag_filter_exclusions(self, filter_values, input_filters):
        for src_k, dst_k in [('tag', 'without_tag'), ('without_tag', 'tag')]:
            selected = list(input_filters.get(src_k) or [])
            if not selected:
                continue
            selected = [
                None if value == get_nil_uuid() else value
                for value in selected
            ]
            filter_values[dst_k] = list(
                set(filter_values.get(dst_k, [])) - set(selected))
        return filter_values

    def get(self, organization_id, **params):
        try:
            self.get_organization_and_cloud_accs(organization_id)
        except FailedDependency:
            return self._get_base_result({})

        facets = self._parse_facets(params.pop('facets', None))

        filters = params.copy()
        self.handle_filters(params, filters, organization_id)
        query_filters, data_filters, extra_params = self.split_params(
            organization_id, params.copy())
        match_query = self.generate_filters_pipeline(
            organization_id, self.start_date, self.end_date,
            query_filters.copy(), data_filters.copy())

        filter_values = {}
        if FACET_CORE in facets:
            resources_data = self._aggregate_core_resource_data(
                match_query, **extra_params)
            result = self.process_data(
                resources_data, organization_id, filters,
                **query_filters, **extra_params)
            # FilterDetailsController returns filter values unwrapped;
            # AvailableFiltersController wraps them as {'filter_values': ...}.
            # Use membership check (not __getitem__) so defaultdict(list) does
            # not auto-create a list for a missing 'filter_values' key.
            if isinstance(result, dict) and 'filter_values' in result:
                filter_values = result['filter_values']
            else:
                filter_values = result
            if FACET_TAG not in facets:
                filter_values['tag'] = []
                filter_values['without_tag'] = []
            if FACET_META not in facets:
                filter_values['meta'] = []

        input_filters = self.get_extended_input_filters(filters)
        if FACET_TAG in facets:
            tag_keys = self._collect_object_keys(
                match_query, 'tags', decode_keys=True)
            filter_values['tag'] = list(tag_keys)
            filter_values['without_tag'] = list(tag_keys)
            filter_values = self._apply_tag_filter_exclusions(
                filter_values, input_filters)
        if FACET_META in facets:
            filter_values['meta'] = self._collect_object_keys(
                match_query, 'meta', decode_keys=False)
        if FACET_VIRTUAL_TAG in facets:
            filter_values['virtual_tag'] = self._collect_virtual_tags(
                match_query)

        return self._get_base_result(filter_values)


class AvailableFiltersAsyncController(BaseAsyncControllerWrapper):

    def _get_controller_class(self):
        return AvailableFiltersController
