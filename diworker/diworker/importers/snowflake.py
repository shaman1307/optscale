#!/usr/bin/env python
import logging
from collections import defaultdict
from datetime import timedelta, timezone

from diworker.diworker.importers.base import BaseReportImporter
from tools.cloud_adapter.clouds.snowflake import calculate_cost
import tools.optscale_time as opttime

LOG = logging.getLogger(__name__)
CHUNK_SIZE = 200
META_FIELDS = [
    'service_category', 'service_type', 'account_name', 'account_locator',
    'region', 'model_name', 'function_name', 'query_id', 'user_id',
]


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
            'service_category', 'account_name', 'account_locator',
            'region', 'resource_name', 'model_name', 'function_name',
            'query_id', 'user_id', 'tokens_input', 'tokens_output',
            'tokens_total',
        ]

    def detect_period_start(self):
        super().detect_period_start()
        # Rewind 2 days for late-arriving ACCOUNT_USAGE rows.
        self.period_start = self.period_start - timedelta(days=2)

    def load_raw_data(self):
        start = self.period_start
        end = opttime.utcnow()
        cost_model = self.cloud_acc['config'].get('cost_model', {})
        chunk = []
        for record in self.cloud_adapter.download_usage(start, end):
            self._enrich_record(record, cost_model)
            if record['start_date'] >= self.period_start.replace(
                    tzinfo=timezone.utc):
                chunk.append(record)
            if len(chunk) >= CHUNK_SIZE:
                self.update_raw_records(chunk)
                chunk = []
        if chunk:
            self.update_raw_records(chunk)
        for warning in self.cloud_adapter.get_import_warnings():
            LOG.warning('Snowflake import warning: %s', warning)

    def get_import_details(self):
        return self.cloud_adapter.get_import_details()

    def _enrich_record(self, record, cost_model):
        record['cloud_account_id'] = self.cloud_acc_id
        record['cost'] = calculate_cost(record, cost_model)
        if not record.get('resource_id'):
            record['resource_id'] = (
                f"{record.get('account_locator')}/"
                f"{record.get('service_type')}/"
                f"{record.get('start_date')}")

    def recalculate_raw_expenses(self):
        cost_model = self.cloud_acc['config'].get('cost_model', {})
        chunk = []
        for expense in self.mongo_raw.find(
                {'cloud_account_id': self.cloud_acc_id}):
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
        resource_type = None
        for e in expenses:
            start_date = e.get('start_date')
            end_date = e.get('end_date') or start_date
            if start_date and start_date < first_seen:
                first_seen = start_date
            if end_date and end_date > last_seen:
                last_seen = end_date
            if not name:
                name = e.get('resource_name') or e.get('resource_id')
            if not resource_type:
                resource_type = e.get('service_type') or 'Snowflake'
            for k in META_FIELDS:
                v = e.get(k)
                if v is not None and k not in meta_dict:
                    meta_dict[k] = v
        if last_seen < first_seen:
            last_seen = first_seen
        info = {
            'name': name,
            'type': resource_type,
            'tags': {},
            'first_seen': int(first_seen.timestamp()),
            'last_seen': int(last_seen.timestamp()),
            **meta_dict,
        }
        LOG.debug('Detected Snowflake resource info: %s', info)
        return info

    def get_resource_data(self, r_id, info,
                          unique_id_field='cloud_resource_id'):
        return {
            unique_id_field: r_id,
            'resource_type': info['type'],
            'name': info['name'],
            'tags': info.get('tags', {}),
            'first_seen': info['first_seen'],
            'last_seen': info['last_seen'],
            **self._get_cloud_extras(info),
        }
