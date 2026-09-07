import re
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from optscale_data.clickhouse import ExternalDataConverter
from tools.optscale_time import utcnow

# Fan-out count_documents beats a single $in+$group scan on large orgs.
_PARALLEL_COUNT_THRESHOLD = 16
_PARALLEL_COUNT_WORKERS = 16
_SEARCH_CLOUD_ACCOUNT_DATES_HINT = 'SearchCloudAccountDates2'
_CLOUD_ACCOUNT_ID_HINT = 'CloudAccountID'


def count_resources_by_cloud_account(
        resources_collection, cloud_acc_list, match_extra=None, hint=None):
    """Return {cloud_account_id: count} for non-deleted resources.

    For large cloud_acc_list, parallel per-account count_documents uses the
    cloud_account_id indexes instead of scanning the whole $in set once.
    """
    cloud_acc_list = list(cloud_acc_list)
    if not cloud_acc_list:
        return {}

    match_extra = match_extra or {}

    if len(cloud_acc_list) < _PARALLEL_COUNT_THRESHOLD:
        match = {
            'cloud_account_id': {'$in': cloud_acc_list},
            'deleted_at': 0,
            **match_extra,
        }
        pipeline = [
            {'$match': match},
            {'$group': {'_id': '$cloud_account_id', 'count': {'$sum': 1}}},
        ]
        return {
            row['_id']: int(row.get('count') or 0)
            for row in resources_collection.aggregate(pipeline)
        }

    def _count_one(cloud_account_id):
        query = {
            'cloud_account_id': cloud_account_id,
            'deleted_at': 0,
            **match_extra,
        }
        if hint:
            try:
                return cloud_account_id, resources_collection.count_documents(
                    query, hint=hint)
            except Exception:
                pass
        return cloud_account_id, resources_collection.count_documents(query)

    workers = min(_PARALLEL_COUNT_WORKERS, len(cloud_acc_list))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return {
            cloud_account_id: int(count)
            for cloud_account_id, count in pool.map(_count_one, cloud_acc_list)
            if count
        }


class ExpenseQuery:
    def __init__(self, execute_clickhouse, resources_collection):
        self._execute_ch = execute_clickhouse
        self._resources = resources_collection

    def _execute(self, query, **kwargs):
        return self._execute_ch(query=query, **kwargs)

    def get_cloud_expenses_with_resource_info(self, cloud_acc_list, start_date, end_date):
        start_day = start_date.replace(
            hour=0, minute=0, second=0, microsecond=0)
        resource_counts = count_resources_by_cloud_account(
            self._resources,
            cloud_acc_list,
            match_extra={
                '_first_seen_date': {'$lt': end_date},
                '_last_seen_date': {'$gte': start_day},
                'first_seen': {'$lt': int(end_date.timestamp())},
                'last_seen': {'$gte': int(start_date.timestamp())},
            },
            hint=_SEARCH_CLOUD_ACCOUNT_DATES_HINT,
        )
        # Keep the historical INNER JOIN semantics: only accounts that have
        # matching resources in the period are returned with costs.
        if not resource_counts:
            return []

        resource_count_rows = [
            {'_id': cloud_account_id, 'count': count}
            for cloud_account_id, count in resource_counts.items()
        ]
        query = """
            SELECT cloud_account_id, SUM(cost * sign), count
            FROM expenses
            JOIN cloud_accounts
                ON expenses.cloud_account_id = cloud_accounts._id
            WHERE cloud_account_id IN %(cloud_acc_list)s
                AND date >= %(start_date)s AND date < %(end_date)s
            GROUP BY cloud_account_id, count
        """
        return self._execute(
            query=query,
            parameters={
                'start_date': start_date,
                'end_date': end_date,
                'cloud_acc_list': list(resource_counts.keys())
            },
            external_data=ExternalDataConverter()([{
                'name': 'cloud_accounts',
                'structure': [
                    ('_id', 'String'),
                    ('count', 'Int32')
                ],
                'data': resource_count_rows
            }]),
        )

    @staticmethod
    def get_monthly_forecast(cost, month_cost, first_expense=None):
        today = datetime.today()
        month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month_start = (month_start - timedelta(days=1)).replace(day=1)
        start_date = max(last_month_start, first_expense) if (
            first_expense) else last_month_start
        worked_days = (today - month_start).days
        forecast_days = (today - start_date).days
        daily_forecast = cost / forecast_days if forecast_days > 0 else cost
        _, days_in_month = monthrange(today.year, today.month)
        forecast = month_cost + daily_forecast * (days_in_month - worked_days)
        return round(forecast, 2)

    def _get_first_cloud_account_expense(
            self, cloud_account_ids, date, field=None, values=None
    ):
        if (field and not values) or not cloud_account_ids:
            return []
        if field and re.search(r'[^_A-Za-z0-9]', field):
            raise ValueError('Suspected SQL injection ')
        query = f"""
            SELECT {field if field else 'cloud_account_id'}, min(date)
            FROM expenses
            WHERE cloud_account_id
                IN cloud_account_ids{' AND %s IN values' %
                                     field if field else ''}
                AND date >= %(date)s
            GROUP BY cloud_account_id{', %s' % field if field else ''}
        """
        external_tables = [
            {
                'name': 'cloud_account_ids',
                'structure': [('id', 'String')],
                'data': [{'id': r_id} for r_id in cloud_account_ids]
            }
        ]
        if values:
            external_tables.append({
                'name': 'values',
                'structure': [('id', 'String')],
                'data': [{'id': r_id} for r_id in values]
            })
        return self._execute(
            query=query,
            parameters={
                'date': date
            },
            external_data=ExternalDataConverter()(external_tables)
        )

    def get_first_expenses_for_forecast(self, field, values):
        prev_month_start = (utcnow().replace(day=1) - timedelta(
            days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if field in ['cloud_account_id']:
            result = self._get_first_cloud_account_expense(values, prev_month_start)
        else:
            # TODO: Not the optimal solution to get expenses dates.
            #  We can get all the necessary dates at the time of receiving the
            #  expenses for the previous month
            resources = list(self._resources.find(
                {field: {'$in': values}, 'cloud_account_id': {'$ne': None}},
                ['cloud_account_id', field]))
            r_ids = list(map(lambda x: x['_id'], resources))
            cloud_account_ids = set(
                map(lambda x: x.get('cloud_account_id'), resources))
            expenses = self._get_first_cloud_account_expense(
                list(cloud_account_ids), prev_month_start, 'resource_id',
                r_ids)
            expenses_map = {e[0]: e[1] for e in expenses}
            result = {}
            for resource in resources:
                value = resource.get(field)
                date = expenses_map.get(resource['_id'])
                if not date:
                    continue
                if value not in result or result[value] > date:
                    result[value] = date
            result = [(k, v) for k, v in result.items()]
        return {r[0]: r[1] for r in result}
