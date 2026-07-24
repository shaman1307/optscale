#!/usr/bin/env python
import base64
import logging
import re
from collections import defaultdict
from datetime import timedelta, timezone

from pymongo import UpdateOne

from diworker.diworker.importers.base import BaseReportImporter
from tools.cloud_adapter.clouds.snowflake import calculate_cost
import tools.optscale_time as opttime

LOG = logging.getLogger(__name__)
# Larger chunks cut Mongo round-trips on high-volume collectors
# (e.g. AUTOMATIC_CLUSTERING after daily aggregation).
CHUNK_SIZE = 2000
# Billing reimport windows longer than this soft-delete all resources so they
# are recreated with current ids/tag keys (base64).
FULL_REIMPORT_DAYS = 5
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
# Legacy cloud_resource_id patterns replaced by STAGE / per-user Cortex ids.
_LEGACY_RESOURCE_ID_RE = re.compile('|'.join((
    r'.*/stages$',
    r'.*/cortex_code_(?:cli|snowsight|desktop)/[0-9a-fA-F-]{8,}$',
    r'.*/cortex_agents/[^/]+/[0-9a-fA-F-]{8,}$',
    r'.*/snowflake_intelligence/[^/]+/[0-9a-fA-F-]{8,}$',
    r'.*/ai_functions/[^/]+/[^/]+/[^/]+$',
)))


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
            'service_category', 'account_name', 'account_locator',
            'region', 'resource_name', 'model_name', 'function_name',
            'query_id', 'user_id', 'user_name', 'request_id', 'agent_id',
            'agent_name', 'tokens_input', 'tokens_output', 'tokens_total',
            'transfer_type', 'source_region', 'target_region', 'tag',
        ]

    def detect_period_start(self):
        # Honor cloud-account last_import_at (Billing Reimport sets this).
        # Base importer skips raw wipe within the current calendar month and
        # may ignore the reimport cursor — Snowflake always wipes from the
        # chosen cursor so reloads are clean.
        ca_last_import_at = self.cloud_acc.get('last_import_at')
        if ca_last_import_at:
            self.period_start = opttime.utcfromtimestamp(ca_last_import_at)
        else:
            self.set_period_start()
        # ACCOUNT_USAGE lag rewind — expand wipe + reload window.
        self.period_start = self.period_start - timedelta(days=2)
        self.remove_raw_expenses_from_period_start(self.cloud_acc_id)
        self._clear_clickhouse_expenses_from_period_start()
        # Full reimport: remember to drop resources that do not come back.
        self._full_reimport = (
            (opttime.utcnow() - self.period_start).days >= FULL_REIMPORT_DAYS)

    def _clear_clickhouse_expenses_from_period_start(self):
        from_dt = self.period_start
        if getattr(from_dt, 'tzinfo', None) is not None:
            from_dt = from_dt.replace(tzinfo=None)
        LOG.info(
            'Clearing ClickHouse expenses for cloud account %s since %s',
            self.cloud_acc_id, from_dt)
        self.clickhouse_cl.query(
            'ALTER TABLE expenses DELETE WHERE cloud_account_id = %(ca_id)s '
            'AND date >= %(from_dt)s',
            parameters={
                'ca_id': self.cloud_acc_id,
                'from_dt': from_dt,
            })

    def load_raw_data(self):
        start = self.period_start
        end = opttime.utcnow()
        cost_model = self.cloud_acc['config'].get('cost_model', {})
        chunk = []
        for record in self.cloud_adapter.download_usage(
                start, end,
                progress_callback=self._publish_import_progress):
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

    def _publish_import_progress(self):
        report_import_id = getattr(self, 'report_import_id', None)
        if not report_import_id:
            return
        details = self.get_import_details()
        if not details:
            return
        try:
            self.rest_cl.report_import_update(
                report_import_id, {'details': details})
        except Exception as exc:
            LOG.warning(
                'Failed to publish Snowflake import details: %s', exc)

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
            if not name:
                name = e.get('resource_name') or e.get('resource_id')
            # Prefer the newest expense's service_type so renames apply when
            # legacy and current raw rows coexist for the same resource_id.
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
                # Reimport soft-deletes all resources first; resurrect those
                # that appear again so skip_existing + include_deleted cannot
                # leave the account empty.
                'deleted_at': 0,
            }
            if info.get('name'):
                update['name'] = info['name']
            if info.get('account_locator'):
                update['account_locator'] = info['account_locator']
            if info.get('account_name'):
                update['account_name'] = info['account_name']
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
        self._soft_delete_legacy_resources(cloud_account_id, unique_id_field)
        # Accumulate ids across clean-expense chunks; orphans dropped in cleanup.
        seen = getattr(self, '_seen_resource_ids', None)
        if seen is None:
            self._seen_resource_ids = set()
            seen = self._seen_resource_ids
        seen.update(resources_info_map.keys())
        return resources

    def cleanup(self):
        super().cleanup()
        if (getattr(self, '_full_reimport', False)
                and getattr(self, '_seen_resource_ids', None)):
            self._soft_delete_orphan_resources(
                self.cloud_acc_id, self._seen_resource_ids)
            self._seen_resource_ids = set()

    def _soft_delete_orphan_resources(
            self, cloud_account_id, keep_ids, unique_id_field='cloud_resource_id'):
        """After full reimport, drop active resources not seen in this load."""
        if not keep_ids:
            return
        now = int(opttime.utcnow().timestamp())
        result = self.mongo_resources.update_many(
            {
                'cloud_account_id': cloud_account_id,
                'deleted_at': 0,
                unique_id_field: {'$nin': list(keep_ids)},
            },
            {'$set': {'deleted_at': now}},
        )
        if result.modified_count:
            LOG.info(
                'Soft-deleted %s orphan Snowflake resources for %s',
                result.modified_count, cloud_account_id)

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
