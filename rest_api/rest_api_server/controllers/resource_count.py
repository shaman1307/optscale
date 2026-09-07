import logging
from collections import defaultdict
from datetime import datetime

from rest_api.rest_api_server.controllers.base_async import BaseAsyncControllerWrapper
from rest_api.rest_api_server.controllers.breakdown_expense import (
    BreakdownBaseController, SUBPOOL_BREAKDOWN)
from rest_api.rest_api_server.exceptions import Err
from rest_api.rest_api_server.utils import (
    is_virtual_tag_breakdown, virtual_tag_breakdown_key,
    virtual_tags_for_quarter, current_quarter, quarters_for_invoice_months)

from tools.optscale_exceptions.common_exc import WrongArgumentsException

LOG = logging.getLogger(__name__)
SECONDS_IN_DAY = 86400


class ResourceCountController(BreakdownBaseController):
    collected_filters = ['cloud_account_id', 'employee_id', 'pool_id']

    @staticmethod
    def get_base_breakdown(start_date, end_date):
        breakdown = {}
        current_breakdown = int(datetime.fromtimestamp(start_date).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp())
        first_breakdown = current_breakdown
        while current_breakdown < end_date:
            breakdown[current_breakdown] = {}
            current_breakdown += SECONDS_IN_DAY
        last_breakdown = current_breakdown - SECONDS_IN_DAY
        return {'breakdown': breakdown, 'first_breakdown': first_breakdown,
                'last_breakdown': last_breakdown}

    def get_value_resource_type(self, value, **kwargs):
        return super().get_value_resource_type(
            value.get('resource_type'), value.get('is_cluster'),
            value.get('is_environment'))

    def _get_resources_breakdowns(
            self, match_query, breakdown_by, start_date, end_date,
            collected_filters):
        if is_virtual_tag_breakdown(breakdown_by):
            return self._get_virtual_tag_count_breakdowns(
                match_query, breakdown_by, start_date, end_date,
                collected_filters)
        breakdowns = self._get_breakdown_dates(start_date, end_date)
        if breakdown_by == 'resource_type':
            group_value = {
                'resource_type': '$resource_type',
                'is_cluster': '$cluster_type_id',
                'is_environment': '$is_environment'
            }
        else:
            group_field = self.mongo_breakdown_field(breakdown_by)
            group_value = '$%s' % group_field

        match_stage = {
            '$match': match_query
        }

        add_stage = {
            '$addFields': {
                'first_breakdown': {
                    '$subtract': [
                        '$first_seen', {
                            '$mod': ['$first_seen', SECONDS_IN_DAY]}]
                },
                'last_breakdown':
                    {
                        '$subtract': [
                            '$last_seen', {
                                '$mod': ['$last_seen', SECONDS_IN_DAY]}
                        ]},
                'cloud_account_id': {
                    '$ifNull': ['$cloud_account_id', None]},
            },
        }

        facet_stage = {'$facet': {}}
        # collect filters values
        facet_stage['$facet'].update({
            f: [{'$project': {'_id': 1, f: '$%s' % f}},
                {'$group': {'_id': None, f: {'$addToSet': '$%s' % f}}}]
            for f in collected_filters})

        # collect totals
        facet_stage['$facet'].update({'total': [{'$count': 'count'}]})
        facet_stage['$facet'].update({'totals': [{
            '$group': {'_id': group_value, 'count': {'$sum': 1}}}, {
            '$project': {'_id': '$_id', 'count': '$count'}
        }]})

        # collect breakdown counts
        brkdwns = {'%s' % b: {'$sum': {'$cond': [{'$and': [
            {'$lte': ['$first_breakdown', b]},
            {'$gte': ['$last_breakdown', b]}]}, 1, 0]}} for b in breakdowns}
        brkdwns_created = {'%s_crt' % b: {'$sum': {'$cond': [{'$and': [
            {'$eq': ['$first_breakdown', b]},
            {'$ne': [b, breakdowns[0]]}]}, 1, 0]}} for b in breakdowns}
        brkdwns_removed = {'%s_rmv' % b: {'$sum': {'$cond': [{'$and': [
            {'$eq': ['$last_breakdown', b - SECONDS_IN_DAY]},
            {'$ne': [b, breakdowns[0]]}]}, 1, 0]}} for b in breakdowns}

        facet_stage['$facet'].update({'breakdowns': [
            {'$group': {'_id': group_value, **brkdwns, **brkdwns_created,
                        **brkdwns_removed}},
            {'$project': {
                '_id': '$_id',
                'breakdowns': {
                    'count': {'%s' % b: '$%s' % b for b in breakdowns},
                    'created': {'%s' % b: '$%s_crt' % b
                                for b in breakdowns},
                    'deleted_day_before': {'%s' % b: '$%s_rmv' % b
                                           for b in breakdowns},
                    'average': {'$divide': [{'$sum': ['$%s' % b
                                                      for b in breakdowns]},
                                            len(breakdowns)]}
                }}}]})

        pipeline = [
            match_stage,
            add_stage,
            facet_stage
        ]
        return self.resources_collection.aggregate(pipeline, allowDiskUse=True)

    def _get_virtual_tag_count_breakdowns(
            self, match_query, breakdown_by, start_date, end_date,
            collected_filters):
        """Count +1 per matching VT value (contains). Share is not applied."""
        key = virtual_tag_breakdown_key(breakdown_by)
        breakdowns = self._get_breakdown_dates(start_date, end_date)
        first_day = breakdowns[0] if breakdowns else 0
        totals = defaultdict(int)
        counts = defaultdict(lambda: defaultdict(int))
        created = defaultdict(lambda: defaultdict(int))
        deleted = defaultdict(lambda: defaultdict(int))
        filter_values = {field: set() for field in collected_filters}
        unique_ids = set()
        for resource in self.resources_collection.find(match_query):
            unique_ids.add(resource['_id'])
            for field in collected_filters:
                filter_values[field].add(resource.get(field))
            first_seen = int(resource.get('first_seen') or 0)
            last_seen = int(resource.get('last_seen') or 0)
            first_bd = first_seen - (first_seen % SECONDS_IN_DAY)
            last_bd = last_seen - (last_seen % SECONDS_IN_DAY)
            values = [
                alloc.get('value')
                for quarter in (
                    quarters_for_invoice_months(
                        getattr(self, 'invoice_months', None))
                    or [current_quarter()])
                for alloc in virtual_tags_for_quarter(resource, quarter)
                if isinstance(alloc, dict) and alloc.get('key') == key
            ]
            for value in values:
                totals[value] += 1
                for day in breakdowns:
                    if first_bd <= day <= last_bd:
                        counts[value][day] += 1
                    if first_bd == day and day != first_day:
                        created[value][day] += 1
                    if last_bd == day - SECONDS_IN_DAY and day != first_day:
                        deleted[value][day] += 1
        day_count = len(breakdowns) or 1
        breakdown_rows = []
        totals_rows = []
        for value, total in totals.items():
            day_counts = {
                str(day): counts[value].get(day, 0) for day in breakdowns}
            breakdown_rows.append({
                '_id': value,
                'breakdowns': {
                    'count': day_counts,
                    'created': {
                        str(day): created[value].get(day, 0)
                        for day in breakdowns},
                    'deleted_day_before': {
                        str(day): deleted[value].get(day, 0)
                        for day in breakdowns},
                    'average': sum(day_counts.values()) / day_count,
                },
            })
            totals_rows.append({'_id': value, 'count': total})
        facet = {
            'total': [{'count': len(unique_ids)}] if unique_ids else [],
            'totals': totals_rows,
            'breakdowns': breakdown_rows,
        }
        for field in collected_filters:
            values = list(filter_values[field])
            facet[field] = [{field: values}] if values else []
        return [facet]

    def get_resource_type_condition(self, resource_types):
        if not resource_types:
            return [{'cluster_id': {'$exists': False}}]

        identity_resource_types_map = defaultdict(list)
        for resource_type in resource_types:
            try:
                type_, identity = self._parse_filter_with_type(resource_type)
            except ValueError:
                raise WrongArgumentsException(
                    Err.OE0218, ['resource_type', resource_type])
            if identity not in [self.REGULAR_IDENTITY, self.CLUSTER_IDENTITY,
                                self.ENVIRONMENT_IDENTITY]:
                raise WrongArgumentsException(Err.OE0499, [])
            identity_resource_types_map[identity].append(type_)

        type_filters = []
        for identity, resource_types in identity_resource_types_map.items():
            resource_type_cond = {
                'cluster_type_id': {'$exists': False},
                'cluster_id': {'$exists': False},
                'is_environment': {'$ne': True},
                'resource_type': {'$in': resource_types}
            }
            if identity == self.CLUSTER_IDENTITY:
                resource_type_cond['cluster_type_id'] = {'$exists': True}
            elif identity == self.ENVIRONMENT_IDENTITY:
                resource_type_cond['is_environment'] = True
            type_filters.append({'$and': [resource_type_cond]})

        return type_filters

    def get_resources_data(self, organization_id, query_filters, data_filters,
                           extra_params):
        extra_params = dict(extra_params)
        breakdown_by = extra_params.get('breakdown_by') or query_filters.pop(
            'breakdown_by', None)
        if isinstance(breakdown_by, list):
            breakdown_by = breakdown_by[0] if breakdown_by else None
        extra_params['breakdown_by'] = breakdown_by
        query = self.generate_filters_pipeline(
            organization_id, self.start_date, self.end_date, query_filters,
            data_filters)
        raw_result = self._get_resources_breakdowns(
            query, extra_params['breakdown_by'],
            self.start_date, self.end_date, self.collected_filters)
        return raw_result

    def _collapse_resource_count_by_subpool(
            self, organization_id, row, result):
        pool_ids = {x['_id'] for x in row.get('totals', [])}
        for breakdown_by_type in row.get('breakdowns', []):
            pool_ids.add(breakdown_by_type['_id'])
        id_to_name, name_entities = self.build_subpool_name_map(
            organization_id, pool_ids)

        counts = defaultdict(lambda: {'total': 0, 'average': 0})
        for x in row.get('totals', []):
            key = id_to_name.get(x['_id'])
            counts[key]['total'] += x['count']
            if key in name_entities:
                counts[key].update(name_entities[key])

        for breakdown_by_type in row.get('breakdowns', []):
            key = id_to_name.get(breakdown_by_type['_id'])
            br = breakdown_by_type['breakdowns']
            counts[key]['average'] = (
                counts[key].get('average', 0) + br.get('average', 0))
            if key in name_entities:
                counts[key].update(name_entities[key])
            for timestamp, r_count in br['count'].items():
                if timestamp not in result['breakdown']:
                    result['breakdown'][timestamp] = {}
                cell = result['breakdown'][timestamp].setdefault(key, {
                    'count': 0,
                    'created': 0,
                    'deleted_day_before': 0,
                    **name_entities.get(key, {}),
                })
                cell['count'] += r_count
                cell['created'] += br['created'][timestamp]
                cell['deleted_day_before'] += br['deleted_day_before'][
                    timestamp]

        result['counts'] = dict(counts)
        return result

    def process_data(self, breakdown_info, organization_id, filters, **kwargs):
        breakdown_by = kwargs['breakdown_by']
        result = self.get_base_result(
            self.start_date, self.end_date, breakdown_by)
        unique_values = {f: set() for f in self.collected_filters}
        result['breakdown'] = defaultdict(dict)
        row = list(breakdown_info)[0]
        for f in unique_values.keys():
            if row[f]:
                unique_values[f].update(row[f][0][f])
        _, organization_cloud_accs = self.get_organization_and_cloud_accs(
            organization_id)
        if row['total']:
            result['count'] = row['total'][0]['count']
        else:
            result['count'] = 0

        if breakdown_by == SUBPOOL_BREAKDOWN:
            return self._collapse_resource_count_by_subpool(
                organization_id, row, result)

        entities = self.get_db_entities_info(
            organization_id, organization_cloud_accs, unique_values)
        breakdown_entities = self.get_breakdown_entity_map(
            entities, breakdown_by)
        if breakdown_by == 'resource_type':
            result['counts'] = {
                self.get_value_resource_type(x['_id']): {
                    'total': x['count']} for x in row['totals']}
        else:
            result['counts'] = {x['_id']: {
                'total': x['count'],
                **breakdown_entities.get(x['_id'], {})
            } for x in row['totals']}

        breakdowns_all = row['breakdowns']
        for breakdown_by_type in breakdowns_all:
            breakdown_counters = breakdown_by_type['breakdowns']
            if breakdown_by == 'resource_type':
                value = self.get_value_resource_type(breakdown_by_type['_id'])
            else:
                value = breakdown_by_type['_id']
            counts = breakdown_counters['count']
            created = breakdown_counters['created']
            deleted_day_before = breakdown_counters['deleted_day_before']
            result['counts'][value]['average'] = breakdown_counters['average']
            for timestamp, r_count in counts.items():
                r_created = created[timestamp]
                r_deleted = deleted_day_before[timestamp]
                if not result['breakdown'].get(timestamp):
                    result['breakdown'][timestamp] = {}
                result['breakdown'][timestamp].update({value: {
                    'count': r_count,
                    'created': r_created,
                    'deleted_day_before': r_deleted,
                    **breakdown_entities.get(value, {})
                }})
        return result

    def get_base_result(self, start_date, end_date, breakdown_by):
        res = {
            'start_date': start_date,
            'end_date': end_date,
            'count': 0,
            **self.get_base_breakdown(start_date, end_date),
        }
        if breakdown_by:
            res['breakdown_by'] = breakdown_by
        return res


class ResourceCountAsyncController(BaseAsyncControllerWrapper):

    def _get_controller_class(self):
        return ResourceCountController
