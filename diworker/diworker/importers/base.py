import logging
import time
import os
import requests
import gzip
import shutil
import threading
import uuid
from contextlib import nullcontext
from functools import cached_property

from collections import defaultdict
from pymongo import UpdateOne
from datetime import date, datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta
import boto3
from boto3.session import Config as BotoConfig
from tools.cloud_adapter.cloud import Cloud as CloudAdapter
from diworker.diworker.utils import (
    retry_mongo_upsert,
    get_month_start,
)

from tools.optscale_data.clickhouse import ExternalDataConverter
import tools.optscale_time as opttime

LOG = logging.getLogger(__name__)
CHUNK_SIZE = 200

# Stable short labels for import-queue-status.sh (legend uses full names).
# Raw expenses go to MongoDB; MariaDB only holds report_import metadata.
# Display pipeline collapses cloud fetch + deserialize + Mongo write → MongoDB.
# GCP: BQ → Deser → MongoDB → Clean
IMPORT_PHASE_BQ_READ = 'BQ'
IMPORT_PHASE_PYTHON_DESERIALIZE = 'Deser'
# Snowflake: MongoDB (SF usage + write) → Clean  — SF is not a separate status step
IMPORT_PHASE_SF_USAGE = 'SF'
# AWS (CUR files): S3 → Parse → MongoDB → Clean
IMPORT_PHASE_S3_DOWNLOAD = 'S3'
IMPORT_PHASE_REPORT_PARSE = 'Parse'
IMPORT_PHASE_MONGO_WRITE = 'MongoDB'
IMPORT_PHASE_RESOURCES_CLEAN = 'Clean'
IMPORT_PHASE_CLICKHOUSE_INSERT = 'ClickHouse'

_THROTTLE_SEMAPHORES: dict[str, threading.Semaphore] = {}
_THROTTLE_LOCK = threading.Lock()

GZIP_ENDING = '.gz'
REPORTS_PATH_PREFIX = 'reports'


def _get_throttle_semaphore(parent_id: str,
                            max_concurrent: int) -> threading.Semaphore:
    with _THROTTLE_LOCK:
        if parent_id not in _THROTTLE_SEMAPHORES:
            _THROTTLE_SEMAPHORES[parent_id] = threading.Semaphore(max_concurrent)
        return _THROTTLE_SEMAPHORES[parent_id]


class BaseReportImporter:
    def __init__(self, cloud_account_id, rest_cl, config_cl, mongo_raw,
                 mongo_resources, clickhouse_cl, import_file=None,
                 recalculate=False, detect_period_start=True,
                 max_tenant_concurrent=1, csv_rewrite_days=5):
        self.cloud_acc_id = cloud_account_id
        self.max_tenant_concurrent = max_tenant_concurrent
        self.csv_rewrite_days = csv_rewrite_days
        self.rest_cl = rest_cl
        self.config_cl = config_cl
        self.mongo_raw = mongo_raw
        self.mongo_resources = mongo_resources
        self.clickhouse_cl = clickhouse_cl
        self.import_file = import_file
        self._cloud_adapter = None
        self._cloud_acc = None
        self._mongo = None
        self._s3_client = None
        self.recalculate = recalculate
        self.period_start = None
        if detect_period_start:
            self.detect_period_start()
        self.imported_raw_dates_map = defaultdict(dict)
        self.report_identity = opttime.utcnow().timestamp()

    @property
    def cloud_acc(self):
        # Cache for the import run. Hitting REST on every expense row (e.g.
        # Snowflake tenant _is_tenant_import) fails the whole import when
        # restapi is briefly down (compose recreate / rolling restart).
        if self._cloud_acc is None:
            _, cloud = self.rest_cl.cloud_account_get(self.cloud_acc_id)
            self._cloud_acc = cloud
        return self._cloud_acc

    def invalidate_cloud_acc_cache(self):
        self._cloud_acc = None

    @property
    def cloud_adapter(self):
        if self._cloud_adapter is None:
            cloud_acc = self.cloud_acc.copy()
            cloud_acc.update(self.cloud_acc['config'])
            self._cloud_adapter = CloudAdapter.get_adapter(cloud_acc)
            _, organization = self.rest_cl.organization_get(
                cloud_acc['organization_id'])
            self._cloud_adapter.set_currency(
                organization.get('currency', 'USD'))
        return self._cloud_adapter

    @property
    def s3_client(self):
        if self._s3_client is None:
            s3_params = self.config_cl.read_branch('/minio')
            self._s3_client = boto3.client(
                's3',
                endpoint_url='http://{}:{}'.format(
                    s3_params['host'], s3_params['port']),
                aws_access_key_id=s3_params['access'],
                aws_secret_access_key=s3_params['secret'],
                config=BotoConfig(s3={'addressing_style': 'path'})
            )
        return self._s3_client

    def prepare(self):
        pass

    def load_raw_data(self):
        raise NotImplementedError

    def get_update_fields(self):
        raise NotImplementedError

    @property
    def need_extend_report_interval(self):
        # decided not to consider the beginning of the month because we always
        # take a period of at least three months in the case of the first report
        if self.cloud_acc['last_import_at'] != 0:
            return False
        return True

    def get_raw_upsert_filters(self, expense):
        return {f: expense.get(f, {'$exists': False})
                for f in self.get_unique_field_list()}

    def update_raw_records(self, chunk):
        update_fields = self.get_update_fields()
        upsert_bulk = []
        for e in chunk:
            self._update_imported_raw_interval(e)
            upsert_bulk.append(UpdateOne(
                filter=self.get_raw_upsert_filters(e),
                update={
                    '$set': {k: e[k] for k in update_fields if k in e},
                    '$setOnInsert': {k: v for k, v in e.items()
                                     if k not in update_fields},
                },
                upsert=True,
            ))
        r = retry_mongo_upsert(
            self.mongo_raw.bulk_write, upsert_bulk, ordered=False)
        LOG.debug('updated: %s', r.bulk_api_result)

    @staticmethod
    def _get_fake_cad_extras(expense):
        res = {}
        for k in ['image_id']:
            val = expense.get(k)
            if val:
                res[k] = val
        return res

    def _get_cloud_extras(self, info):
        return {}

    def get_resource_data(self, r_id, info,
                          unique_id_field='cloud_resource_id'):
        return {
            unique_id_field: r_id,
            'resource_type': info['type'],
            'name': info['name'],
            'tags': info['tags'],
            'region': info['region'],
            'service_name': info.get('service_name'),
            'first_seen': info['first_seen'],
            'last_seen': info['last_seen'],
            **self._get_fake_cad_extras(info),
            **self._get_cloud_extras(info)
        }

    def create_resources_if_not_exist(self, cloud_account_id,
                                      resources_info_map,
                                      unique_id_field='cloud_resource_id'):
        resources_data = []
        for r_id, info in resources_info_map.items():
            row = self.get_resource_data(
                r_id, info, unique_id_field=unique_id_field)
            months = info.get('invoice_months')
            if months:
                row['invoice_months'] = months
            resources_data.append(row)
        _, result = self.rest_cl.cloud_resource_create_bulk(
            cloud_account_id, {'resources': resources_data},
            behavior='skip_existing', return_resources=True,
            is_report_import=True)
        return result['resources']

    def get_resource_info_from_expenses(self, expenses):
        raise NotImplementedError

    @staticmethod
    def _invoice_month_value(expense):
        value = expense.get('invoice_month')
        if value is None:
            return ''
        return str(value)

    def clean_expenses_for_resource(self, resource_id, expenses):
        clean_expenses = {}
        for e in expenses:
            usage_date = e['start_date'].replace(
                hour=0, minute=0, second=0, microsecond=0)
            invoice_month = self._invoice_month_value(e)
            key = (usage_date, invoice_month)
            if key in clean_expenses:
                clean_expenses[key]['cost'] += e['cost']
            else:
                clean_expenses[key] = {
                    'date': usage_date,
                    'invoice_month': invoice_month,
                    'cost': e['cost'],
                    'resource_id': resource_id,
                    'cloud_account_id': e['cloud_account_id']
                }
        return clean_expenses

    @staticmethod
    def gen_clickhouse_expense(expense, new_cost=None):
        expense = [
            expense['cloud_account_id'],
            expense['resource_id'],
            expense['date'],
            expense['cost'],
            1,
            str(expense.get('invoice_month') or ''),
        ]
        if new_cost is not None:
            expense[3] = new_cost
            expense[4] = -1
        return expense

    @staticmethod
    def _clickhouse_lookup_date(value):
        """Naive midnight datetime so Date and DateTime rows share a lookup key."""
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                value = value.replace(tzinfo=None)
            return value.replace(hour=0, minute=0, second=0, microsecond=0)
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day)
        return value

    def get_clickhouse_expenses(self, from_dt, to_dt, resource_ids,
                                cloud_account_id):
        # Do not use FINAL. Duplicate +1 rows share ORDER BY
        # (cloud_account_id, date, resource_id, invoice_month); FINAL keeps
        # one and a single -1 cannot collapse the rest.
        # Stay inside [from_dt, to_dt]. Pulling invoice_month='' for all
        # history lets unbilled_clickhouse_negations zero April/May on an
        # August incremental (those days are not in this chunk's billed_keys).
        return self.clickhouse_cl.query("""
            SELECT resource_id, date, invoice_month, cost, sign
            FROM expenses
            WHERE cloud_account_id = %(cloud_account_id)s
                AND resource_id in %(resource_ids)s
                AND date >= %(from_dt)s
                AND date <= %(to_dt)s
        """, parameters={
            'cloud_account_id': cloud_account_id,
            'from_dt': from_dt,
            'to_dt': to_dt,
            'resource_ids': list(resource_ids)
        }).result_rows

    def get_resource_info_map(self, chunk):
        return {
            r_id: self.get_resource_info_from_expenses(expenses)
            for r_id, expenses in chunk.items()
        }

    @staticmethod
    def empty_invoice_month_negations(
            cloud_account_id, billed_resource_dates, existing_map):
        """Negate leftover invoice_month='' rows replaced by a billed month.

        After invoice_month was added to ClickHouse ORDER BY, regenerator
        writes YYYYMM keys and leaves the pre-migration '' copy in place.
        Calendar SUM(date) then double-counts. Collapse '' once a billed
        sibling exists for the same resource+date.
        """
        rows = []
        for resource_id, expense_date in billed_resource_dates:
            empty_rows = existing_map.get(resource_id, {}).get(
                (expense_date, ''))
            if not empty_rows:
                continue
            if not sum(sign for _, sign in empty_rows):
                continue
            empty_cost = sum(cost * sign for cost, sign in empty_rows)
            if empty_cost == 0:
                continue
            rows.append(BaseReportImporter.gen_clickhouse_expense({
                'cloud_account_id': cloud_account_id,
                'resource_id': resource_id,
                'date': expense_date,
                'cost': empty_cost,
                'invoice_month': '',
            }, new_cost=empty_cost))
        return rows

    @staticmethod
    def clickhouse_pairs_match_billed(pairs, billed_cost, eps=1e-6):
        """True when unmerged rows are already one +billed collapsing row.

        Duplicate +1 rows share ORDER BY (cost is not in the key). FINAL is
        then undefined even if sum(cost*sign) equals mongo, so save_clean
        must rewrite those days. A lone cost=0 +1 is not canonical.
        """
        billed = float(billed_cost or 0)
        if not pairs:
            return abs(billed) <= eps
        if len(pairs) != 1:
            return False
        cost, sign = pairs[0]
        return int(sign) == 1 and abs(float(cost) - billed) <= eps

    @staticmethod
    def invert_clickhouse_pairs(
            cloud_account_id, resource_id, expense_date, invoice_month, pairs):
        """Emit the opposite sign for every unmerged CollapsingMergeTree row.

        Cost is not in ORDER BY, so one -1 with the net cancels only one +1.
        """
        rows = []
        for cost, sign in pairs:
            expense = {
                'cloud_account_id': cloud_account_id,
                'resource_id': resource_id,
                'date': expense_date,
                'cost': cost,
                'invoice_month': invoice_month,
            }
            if sign == 1:
                rows.append(BaseReportImporter.gen_clickhouse_expense(
                    expense, new_cost=cost))
            elif sign == -1:
                rows.append(BaseReportImporter.gen_clickhouse_expense(expense))
        return rows

    @staticmethod
    def unbilled_clickhouse_negations(
            cloud_account_id, billed_keys, existing_map,
            from_dt=None, to_dt=None):
        """Negate ClickHouse nets that mongo did not bill in this chunk.

        save_clean only upserts (date, invoice_month) present in raw. Extra
        CH days inside the clean window (collapse leftovers, truncated
        reimport, duplicate +1) are never visited and stay forever.
        billed_keys is {(resource_id, date, invoice_month)}.

        Do not negate days outside [from_dt, to_dt]: an incremental chunk
        does not re-bill April/May, and those nets must stay.
        """
        start = BaseReportImporter._clickhouse_lookup_date(
            from_dt) if from_dt is not None else None
        end = BaseReportImporter._clickhouse_lookup_date(
            to_dt) if to_dt is not None else None
        rows = []
        for resource_id, ch_days in existing_map.items():
            for (expense_date, invoice_month), pairs in ch_days.items():
                day = BaseReportImporter._clickhouse_lookup_date(
                    expense_date)
                if start is not None and day < start:
                    continue
                if end is not None and day > end:
                    continue
                if (resource_id, expense_date, invoice_month) in billed_keys:
                    continue
                net = sum(cost * sign for cost, sign in pairs)
                if net == 0:
                    continue
                rows.extend(BaseReportImporter.invert_clickhouse_pairs(
                    cloud_account_id, resource_id, expense_date,
                    invoice_month, pairs))
        return rows

    def _clickhouse_clean_window(self, min_date, max_date):
        from_dt = self._clickhouse_lookup_date(min_date)
        to_dt = self._clickhouse_lookup_date(max_date)
        period_start = getattr(self, 'period_start', None)
        if period_start:
            period_start = self._clickhouse_lookup_date(period_start)
            if period_start < from_dt:
                from_dt = period_start
        now = self._clickhouse_lookup_date(opttime.utcnow())
        if now > to_dt:
            to_dt = now
        return from_dt, to_dt

    def save_clean_expenses(self, cloud_account_id, chunk,
                            unique_id_field='resource_id'):
        self.log_import_phase(IMPORT_PHASE_RESOURCES_CLEAN)
        info_map = self.get_resource_info_map(chunk)
        for r_id, expenses in chunk.items():
            if r_id not in info_map:
                continue
            months = sorted({
                self._invoice_month_value(expense)
                for expense in expenses
                if self._invoice_month_value(expense)
            })
            if months:
                info_map[r_id]['invoice_months'] = months
        cloud_unique_id_field = 'cloud_%s' % unique_id_field

        resources_map = {
            r[cloud_unique_id_field]: r
            for r in self.create_resources_if_not_exist(
                cloud_account_id, info_map,
                unique_id_field=cloud_unique_id_field)
        }

        clean_expenses = []
        last_expense_info = {}
        column_names = [
            "cloud_account_id", "resource_id", "date", "cost", "sign",
            "invoice_month"]
        max_date, min_date = None, None
        for r_id, expenses in chunk.items():
            resource_id = resources_map[r_id]['id']
            clean_expenses_map = self.clean_expenses_for_resource(
                resource_id, expenses)
            if not clean_expenses_map:
                continue
            dates = [e['date'] for e in clean_expenses_map.values()]
            min_resource_date = min(dates)
            max_resource_date = max(dates)
            last_exp = max(
                clean_expenses_map.values(), key=lambda e: e['date'])
            last_expense_info[resource_id] = (
                last_exp['date'], last_exp['cost'])
            if not min_date or min_resource_date < min_date:
                min_date = min_resource_date
            if not max_date or max_resource_date > max_date:
                max_date = max_resource_date
            clean_expenses.extend(clean_expenses_map.values())
        resource_ids = last_expense_info.keys()
        if resource_ids:
            from_dt, to_dt = self._clickhouse_clean_window(min_date, max_date)
            existing_expenses = self.get_clickhouse_expenses(
                from_dt, to_dt, resource_ids, cloud_account_id)
            resource_id_date_cost_map = defaultdict(dict)
            for (resource_id, ch_date, invoice_month, clickhouse_cost,
                 sign) in existing_expenses:
                date_n = self._clickhouse_lookup_date(ch_date)
                key = (date_n, str(invoice_month or ''))
                if not resource_id_date_cost_map[resource_id].get(key):
                    resource_id_date_cost_map[resource_id][key] = list()
                resource_id_date_cost_map[resource_id][key].append(
                    (clickhouse_cost, sign))
            clickhouse_expenses = []
            billed_keys = set()
            for expense in clean_expenses:
                expense_date = self._clickhouse_lookup_date(expense['date'])
                expense['date'] = expense_date
                invoice_month = str(expense.get('invoice_month') or '')
                lookup_key = (expense_date, invoice_month)
                billed_keys.add(
                    (expense['resource_id'], expense_date, invoice_month))
                clickhouse_expense = resource_id_date_cost_map[
                    expense['resource_id']].get(lookup_key)
                billed = expense['cost']
                if self.clickhouse_pairs_match_billed(
                        clickhouse_expense, billed):
                    continue
                if clickhouse_expense:
                    clickhouse_expenses.extend(
                        self.invert_clickhouse_pairs(
                            cloud_account_id, expense['resource_id'],
                            expense_date, invoice_month, clickhouse_expense))
                if abs(float(billed or 0)) > 0.000001:
                    clickhouse_expenses.append(
                        self.gen_clickhouse_expense(expense))
            clickhouse_expenses.extend(
                self.unbilled_clickhouse_negations(
                    cloud_account_id, billed_keys,
                    resource_id_date_cost_map,
                    from_dt=from_dt, to_dt=to_dt))
            if clickhouse_expenses:
                self.update_clickhouse_expenses(clickhouse_expenses,
                                                column_names)
                self.update_resource_expense_info(cloud_account_id,
                                                  last_expense_info)

    def update_resource_expense_info(self, cloud_account_id,
                                     last_expense_info):
        resource_info = self.get_common_resource_expense_info(
            cloud_account_id, last_expense_info.keys())
        bulk = []
        for r_id, info in resource_info.items():
            max_date, total_cost = info
            last_expense_date, last_expense_cost = last_expense_info[r_id]
            updates = {
                'total_cost': total_cost
            }
            if last_expense_date.replace(tzinfo=None) >= max_date:
                updates['last_expense'] = {
                    'date': int(last_expense_date.timestamp()),
                    'cost': last_expense_cost
                }
            bulk.append(
                UpdateOne(
                    filter={
                        'cloud_account_id': cloud_account_id,
                        '_id': r_id
                    },
                    update={'$set': updates}
                )
            )
        if bulk:
            r = retry_mongo_upsert(self.mongo_resources.bulk_write, bulk)
            LOG.debug(
                'Updated resources with expense info: %s' % r.bulk_api_result)

    def get_common_resource_expense_info(self, cloud_account_id, resource_ids):
        info_q = self.clickhouse_cl.query("""
            SELECT resource_id, max(date), sum(cost*sign)
            FROM expenses
            WHERE cloud_account_id = %(cloud_account_id)s
                AND resource_id IN resource_ids
            GROUP BY resource_id
        """, parameters={
            'cloud_account_id': cloud_account_id,
        }, external_data=ExternalDataConverter()([
            {
                'name': 'resource_ids',
                'structure': [('id', 'String')],
                'data': [{'id': r_id} for r_id in resource_ids]
            }
        ]))
        return {r[0]: (r[1], r[2]) for r in info_q.result_rows}

    def get_resource_ids(self, cloud_account_id, period_start):
        base_filters = {'cloud_account_id': cloud_account_id}
        if period_start:
            base_filters['start_date'] = {'$gte': period_start}
        resource_ids = self.mongo_raw.aggregate([
            {'$match': base_filters},
            {'$group': {'_id': '$resource_id'}}
        ], allowDiskUse=True)
        return [x['_id'] for x in resource_ids]

    @staticmethod
    def set_raw_chunk(expenses):
        chunk = defaultdict(list)
        for ex in expenses:
            resource_id = ex['_id']['resource_id']
            chunk[resource_id].extend(ex['expenses'])
        return chunk

    @staticmethod
    def _get_additional_expenses_groupings():
        return

    def get_raw_expenses_by_filters(self, filters):
        grp_stage = {
            'resource_id': '$resource_id',
            'dt': '$start_date',
        }
        additional = self._get_additional_expenses_groupings()
        if additional:
            grp_stage.update(additional)
        return self.mongo_raw.aggregate([
                {'$match': {
                    '$and': filters,
                }},
                {'$group': {
                    '_id': grp_stage,
                    'expenses': {'$push': '$$ROOT'}
                }},
            ], allowDiskUse=True)

    def _get_billing_period_filters(self, period_start):
        return {'start_date': {'$gte': period_start}}

    def _generate_clean_records(self, resource_ids, cloud_account_id,
                                period_start):
        resource_count = len(resource_ids)
        LOG.info(
            'Generating clean expenses for %s resources in account %s for %s',
            resource_count, cloud_account_id, period_start)
        progress = 0
        for i in range(0, resource_count, CHUNK_SIZE):
            new_progress = round(i / resource_count * 100)
            if new_progress != progress:
                progress = new_progress
                LOG.info('Progress: %s', progress)

            filters = [
                {'cloud_account_id': cloud_account_id},
                self._get_billing_period_filters(period_start),
                {'resource_id': {
                    '$in': resource_ids[i:i + CHUNK_SIZE]}}]
            expenses = self.get_raw_expenses_by_filters(filters)
            chunk = self.set_raw_chunk(expenses)
            self.save_clean_expenses(cloud_account_id, chunk)

        LOG.info('Finished generating clean expenses for %s resources',
                 resource_count)

    def generate_clean_records(self, regeneration=False):
        resource_ids = self.get_resource_ids(self.cloud_acc_id,
                                             self.period_start)
        self._generate_clean_records(resource_ids, self.cloud_acc_id,
                                     self.period_start)

    def cleanup(self):
        pass

    def get_unique_field_list(self):
        raise NotImplementedError

    def recalculate_raw_expenses(self):
        raise NotImplementedError

    def process_alerts(self):
        self.rest_cl.alert_process(self.cloud_acc['organization_id'])

    def data_import(self):
        if self.recalculate:
            LOG.info('Recalculating raw expenses')
            self.recalculate_raw_expenses()
            # need to re-create clean expenses based on updated raw data
            regeneration = True
        else:
            LOG.info('Importing raw data')
            self.load_raw_data()
            regeneration = False
        LOG.info('Generating clean records')
        self.log_import_phase(IMPORT_PHASE_RESOURCES_CLEAN)
        self.generate_clean_records(regeneration=regeneration)

    # Billing importers that do not share a cloud-provider API rate-limit
    # quota via parent_id. GCP children query BigQuery independently; the
    # per-tenant semaphore (MPT-21087) is for Azure-like APIs that 429 when
    # many sibling accounts import in parallel.
    _TENANT_THROTTLE_EXEMPT_TYPES = frozenset({'gcp_cnr', 'gcp_tenant'})

    def import_report(self):
        parent_id = self.cloud_acc.get('parent_id')
        cloud_type = (self.cloud_acc.get('type') or '').lower()
        use_throttle = (
            bool(parent_id)
            and cloud_type not in self._TENANT_THROTTLE_EXEMPT_TYPES
        )
        throttle = (
            _get_throttle_semaphore(parent_id, self.max_tenant_concurrent)
            if use_throttle else nullcontext()
        )
        with throttle:
            self._run_import()

    def _run_import(self):
        LOG.info('Started import for %s', self.cloud_acc_id)
        self.prepare()
        try:
            self.data_import()
        finally:
            LOG.info('Cleanup')
            self.cleanup()

        LOG.info('Updating import time')
        self.update_cloud_import_time(int(time.time()))
        self.update_cloud_account_config()
        LOG.info('Import completed')

        LOG.info('Processing alerts')
        try:
            self.process_alerts()
        except Exception as exc:
            # Alerts are post-processing: do not fail a successful import.
            LOG.exception(
                'process_alerts failed for %s (import data is complete): %s',
                self.cloud_acc_id, exc)

        LOG.info('Creating traffic processing tasks')
        self.create_traffic_processing_tasks()

        LOG.info('Creating risp processing tasks')
        self.create_risp_processing_tasks()

        LOG.info('Processing completed')

    def log_import_phase(self, phase):
        """Emit a parseable phase marker for import-queue-status.sh."""
        LOG.info('Import phase for %s: %s', self.cloud_acc_id, phase)

    def update_clickhouse_expenses(self, expenses, column_names):
        self.log_import_phase(IMPORT_PHASE_CLICKHOUSE_INSERT)
        self.clickhouse_cl.insert(
            'expenses', expenses, column_names=column_names)

    def update_cloud_import_time(self, ts):
        # Clear prior attempt error so UI "Billing import failed" does not
        # stick after a successful run (esp. when last_import_at is later
        # rewound for a full reimport).
        self.rest_cl.cloud_account_update(self.cloud_acc_id,
                                          {'last_import_at': ts,
                                           'last_import_attempt_at': ts,
                                           'last_import_attempt_error': None})

    def update_cloud_import_attempt(self, ts, error=None):
        self.rest_cl.cloud_account_update(
            self.cloud_acc_id,
            {'last_import_attempt_at': ts,
             'last_import_attempt_error': (
                 error[:255] if error else None)})

    def update_cloud_account_config(self):
        pass

    @staticmethod
    def extract_tags(raw_tags):
        tags = {}

        def extract_tag(tag_name, value):
            if isinstance(value, dict):
                for key, val in value.items():
                    extract_tag("%s.%s" % (tag_name, key), val)
            elif isinstance(value, list):
                for index, val in enumerate(value):
                    extract_tag("%s.%s" % (tag_name, index), val)
            else:
                tags[tag_name] = value

        for k, v in raw_tags.items():
            extract_tag(k, v)
        return tags

    def detect_period_start(self):
        ca_last_import_at = self.cloud_acc.get('last_import_at')
        if ca_last_import_at:
            ca_last_import_dt = opttime.utcfromtimestamp(ca_last_import_at)
            now = opttime.utcnow()
            same_calendar_month = (
                ca_last_import_dt.year == now.year and
                ca_last_import_dt.month == now.month)
        else:
            same_calendar_month = False
        if ca_last_import_at and same_calendar_month:
            last_import_at = self.get_last_import_date(self.cloud_acc_id)
            # someone cleared expenses collection
            if not last_import_at:
                last_import_at = opttime.utcfromtimestamp(
                    self.cloud_acc['last_import_at'])
            if last_import_at.day == 1:
                self.period_start = get_month_start(
                    last_import_at - timedelta(days=1))
            else:
                self.period_start = last_import_at
        elif ca_last_import_at:
            self.period_start = opttime.utcfromtimestamp(
                self.cloud_acc['last_import_at'])
            self.remove_raw_expenses_from_period_start(self.cloud_acc_id)

        if self.period_start is None:
            self.set_period_start()

    def set_period_start(self):
        if self.need_extend_report_interval:
            this_month_start = opttime.utcnow().replace(
                day=1, hour=0, minute=0, second=0, microsecond=0)
            self.period_start = this_month_start - relativedelta(months=+3)
        else:
            self.period_start = get_month_start(opttime.utcnow())

    def get_last_import_date(self, cloud_account_id, tzinfo=None):
        max_dt_q = self.clickhouse_cl.query(
            'SELECT max(date), count(date) from expenses '
            'WHERE cloud_account_id=%(ca_id)s',
            parameters={'ca_id': cloud_account_id})
        result, count = 0, 0
        for dt in max_dt_q.result_rows:
            m_dt, count = dt
            result = m_dt if count else 0
        if result and tzinfo:
            result = result.replace(tzinfo=tzinfo)
        return result

    def remove_raw_expenses_from_period_start(self, cloud_account_id):
        query = {
            'start_date': {'$gte': self.period_start},
            'cloud_account_id': cloud_account_id
        }
        r = self.mongo_raw.delete_many(query)
        LOG.info('Raw expenses for cloud account %s since %s were '
                 'deleted: %s' % (
                    cloud_account_id, self.period_start, r.raw_result))

    def _clear_clickhouse_expenses_from_period_start(
            self, cloud_account_id=None):
        """Hard-delete ClickHouse rows for a period reload. Increments merge."""
        ca_id = cloud_account_id or self.cloud_acc_id
        from_dt = self.period_start
        if from_dt is None:
            return
        if getattr(from_dt, 'tzinfo', None) is not None:
            from_dt = from_dt.replace(tzinfo=None)
        LOG.info(
            'Clearing ClickHouse expenses for cloud account %s since %s',
            ca_id, from_dt)
        self.clickhouse_cl.query(
            'ALTER TABLE expenses DELETE WHERE cloud_account_id = %(ca_id)s '
            'AND date >= %(from_dt)s',
            parameters={
                'ca_id': ca_id,
                'from_dt': from_dt,
            })
        self._wait_clickhouse_period_cleared(ca_id, from_dt)

    def _wait_clickhouse_period_cleared(self, ca_id, from_dt, timeout_sec=180):
        deadline = time.time() + timeout_sec
        while True:
            pending = self.clickhouse_cl.query(
                "SELECT count() FROM system.mutations "
                "WHERE table = 'expenses' AND is_done = 0"
            ).result_rows
            leftover = self.clickhouse_cl.query(
                'SELECT count() FROM expenses '
                'WHERE cloud_account_id = %(ca_id)s AND date >= %(from_dt)s',
                parameters={'ca_id': ca_id, 'from_dt': from_dt},
            ).result_rows
            pending_n = int(pending[0][0]) if pending else 0
            leftover_n = int(leftover[0][0]) if leftover else 0
            if pending_n == 0 and leftover_n == 0:
                return
            if time.time() >= deadline:
                raise RuntimeError(
                    'ClickHouse period clear did not finish for %s since %s '
                    '(pending_mutations=%s leftover_rows=%s)' % (
                        ca_id, from_dt, pending_n, leftover_n))
            time.sleep(1)

    def create_traffic_processing_tasks(self):
        return

    def _create_traffic_processing_tasks(self):
        for cloud_account_id, dates in self.imported_raw_dates_map.items():
            body = {
                'start_date': int(dates.get('start_date').timestamp()),
                'end_date': int(dates.get('end_date').timestamp())
            }
            try:
                self.rest_cl.traffic_processing_task_create(cloud_account_id,
                                                            body)
            except requests.exceptions.HTTPError as exc:
                if exc.response.status_code != 409:
                    raise
                LOG.info('Traffic processing task %s already exists '
                         'for cloud account %s' % (body, cloud_account_id))

    def create_risp_processing_tasks(self):
        return

    def _create_risp_processing_tasks(self):
        for cloud_account_id, dates in self.imported_raw_dates_map.items():
            body = {
                'start_date': int(dates.get('start_date').timestamp()),
                'end_date': int(dates.get('end_date').timestamp())
            }
            try:
                self.rest_cl.risp_processing_task_create(cloud_account_id,
                                                         body)
            except requests.exceptions.HTTPError as exc:
                if exc.response.status_code != 409:
                    raise
                LOG.info('Risp processing task %s already exists '
                         'for cloud account %s' % (body, cloud_account_id))

    def _update_imported_raw_interval(self, expense):
        cloud_account_id = expense['cloud_account_id']
        start_date = expense['start_date']
        cl_acc_dates = self.imported_raw_dates_map[cloud_account_id]
        raw_first_dt = cl_acc_dates.get('start_date')
        raw_last_date = cl_acc_dates.get('end_date')
        last_start_date = cl_acc_dates.get('last_start_date')
        if not raw_first_dt or raw_first_dt > start_date:
            cl_acc_dates['start_date'] = start_date
        if not raw_last_date or raw_last_date < start_date:
            cl_acc_dates['end_date'] = start_date
        if not last_start_date or last_start_date < start_date:
            cl_acc_dates['last_start_date'] = start_date

    def clear_rudiments(self):
        for cloud_account_id, dates in self.imported_raw_dates_map.items():
            result = self.mongo_raw.delete_many({
                'cloud_account_id': cloud_account_id,
                'start_date': {
                    '$gte': dates.get('start_date'),
                    '$lte': dates.get('last_start_date')
                },
                'report_identity': {'$ne': self.report_identity}
            })
            LOG.info('Cleared %s rudiments for cloud_account %s' %
                     (result.deleted_count, cloud_account_id))


class CSVBaseReportImporter(BaseReportImporter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.billing_periods = set()
        self.detected_cloud_accounts = set()
        self.detected_cloud_accounts.add(self.cloud_acc_id)
        self.reports_dir = f'{REPORTS_PATH_PREFIX}/{uuid.uuid4()}'
        os.makedirs(self.reports_dir)
        self.report_files = defaultdict(list)
        self.last_import_modified_at = self.cloud_acc.get(
            'last_import_modified_at', 0)

    @cached_property
    def min_date_import_threshold(self) -> datetime:
        last_import_dt = datetime.fromtimestamp(
            self.cloud_acc.get('last_import_modified_at', 0), tz=timezone.utc)
        return last_import_dt.replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=self.csv_rewrite_days)

    def detect_period_start(self):
        pass

    def get_new_report_path(self, date=''):
        return os.path.join(self.reports_dir, date, str(uuid.uuid4()))

    def download_from_object_store(self):
        bucket, filename = self.import_file.split('/')
        self.report_files['reports'] = [self.get_new_report_path()]
        with open(self.report_files['reports'][0], 'wb') as f_report:
            self.s3_client.download_fileobj(bucket, filename, f_report)

    def get_current_reports(self, reports_groups, last_import_modified_at):
        raise NotImplementedError

    def _get_legacy_key(self, old_key):
        return

    def _download_report_files(self, current_reports, last_import_modified_at):
        for date, reports in current_reports.items():
            for report in reports:
                if last_import_modified_at < report['LastModified']:
                    last_import_modified_at = report['LastModified']
                target_path = self.get_new_report_path(date)
                os.makedirs(os.path.join(self.reports_dir, date),
                            exist_ok=True)
                try:
                    # python2 way
                    with open(target_path, 'wb') as f_report:
                        self.cloud_adapter.download_report_file(report['Key'],
                                                                f_report)
                except TypeError:
                    # python3 way
                    with open(target_path, 'w') as f_report:
                        self.cloud_adapter.download_report_file(report['Key'],
                                                                f_report)
                self.report_files[date].append(target_path)
        return last_import_modified_at

    @staticmethod
    def gunzip_report(report_path, dest_dir):
        LOG.info('Extracting %s as gzip archive to %s',
                 report_path, dest_dir)
        new_report_path = os.path.basename(report_path)
        if new_report_path.endswith(GZIP_ENDING):
            new_report_path = new_report_path[
                              :len(new_report_path) - len(GZIP_ENDING)]
        else:
            new_report_path = str(uuid.uuid4())
        new_report_path = os.path.join(dest_dir, new_report_path)
        try:
            with gzip.open(report_path, 'rb') as f_gzip:
                with open(new_report_path, 'wb') as f_out:
                    shutil.copyfileobj(f_gzip, f_out)
        except Exception:
            if os.path.exists(new_report_path):
                os.remove(new_report_path)
            return

        return new_report_path

    def download_from_cloud(self):
        reports_groups = self.cloud_adapter.get_report_files()
        if self.last_import_modified_at <= 0:
            last_import_modified_at = datetime.min.replace(
                tzinfo=timezone.utc)
            LOG.info('Decided to download latest reports set')
            current_reports = defaultdict(list)
            report_groups_keys = list(reports_groups.keys())
            report_groups_keys.sort()
            # to get reports for the current and three previous months
            num_last_reports = 4 if self.need_extend_report_interval else 1
            report_groups_keys = report_groups_keys[-num_last_reports:]
            for key in report_groups_keys:
                current_reports[key].extend(reports_groups[key])
        else:
            last_import_modified_at = datetime.fromtimestamp(
                self.last_import_modified_at, tz=timezone.utc)
            current_reports = self.get_current_reports(
                reports_groups, last_import_modified_at)

        last_import_modified_at = self._download_report_files(
            current_reports, last_import_modified_at)
        self.last_import_modified_at = int(last_import_modified_at.timestamp())

    def unpack_report_files(self):
        pass

    def load_report(self, report_path, account_id_ca_id_ma):
        raise NotImplementedError

    def prepare(self):
        if self.import_file is not None:
            self.download_from_object_store()
        else:
            self.download_from_cloud()
        self.unpack_report_files()

    def get_linked_account_map(self):
        return {self.cloud_acc['account_id']: self.cloud_acc_id}

    def _import_reports_ordered_by_date(self, account_id_ca_id_map):
        dates = [x for x in self.report_files]
        dates.sort(reverse=True)
        for date in dates:
            reports = self.report_files[date]
            for report in reports:
                self.load_report(report, account_id_ca_id_map)
            LOG.info('Generating clean records')
            self.generate_clean_records()
            self.billing_periods = set()

    def data_import(self):
        if self.cloud_acc['last_import_at'] == 0 and self.import_file is None:
            # on first auto report import we will load raw data from reports and
            # generate expenses month by month from newest to oldest
            account_id_ca_id_map = self.get_linked_account_map()
            self._import_reports_ordered_by_date(account_id_ca_id_map)
        else:
            super().data_import()

    def load_raw_data(self):
        account_id_ca_id_map = {self.cloud_acc['account_id']: self.cloud_acc_id}
        report_files = []
        for r in self.report_files.values():
            report_files.extend(r)
        for report_path in report_files:
            self.load_report(report_path, account_id_ca_id_map)
        self.clear_rudiments()

    def get_resource_ids(self, cloud_account_id, billing_period):
        raise NotImplementedError

    def generate_clean_records(self, regeneration=False):
        # useless if there is nothing to import
        if not self.report_files and not regeneration:
            return
        billing_periods = {
            None} if not self.billing_periods else self.billing_periods
        for cc_id in self.detected_cloud_accounts:
            for billing_period in sorted(billing_periods, reverse=True):
                resource_ids = self.get_resource_ids(cc_id, billing_period)
                self._generate_clean_records(resource_ids, cc_id, billing_period)

    def cleanup(self):
        shutil.rmtree(self.reports_dir, ignore_errors=True)
        if self.import_file:
            bucket, filename = self.import_file.split('/')
            self.s3_client.delete_object(Bucket=bucket, Key=filename)

    def update_cloud_import_attempt(self, ts, error=None):
        for cloud_acc_id in self.detected_cloud_accounts:
            self.rest_cl.cloud_account_update(
                cloud_acc_id,
                {'last_import_attempt_at': ts,
                 'last_import_attempt_error': (
                     error[:255] if error else None)})

    def update_cloud_import_time(self, ts):
        for cloud_acc_id in self.detected_cloud_accounts:
            self.rest_cl.cloud_account_update(
                cloud_acc_id,
                {'last_import_at': ts,
                 'last_import_modified_at': self.last_import_modified_at,
                 'last_import_attempt_at': ts,
                 'last_import_attempt_error': None})
