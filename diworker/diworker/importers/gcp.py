from collections import defaultdict
import logging
import re
import time
from datetime import datetime, timedelta

from pymongo import UpdateOne

from diworker.diworker.importers.base import (
    BaseReportImporter,
    IMPORT_PHASE_BQ_READ,
    IMPORT_PHASE_MONGO_WRITE,
    IMPORT_PHASE_PYTHON_DESERIALIZE,
)
from diworker.diworker.utils import retry_mongo_upsert, get_month_start
from tools.cloud_adapter.gcp_resource_collapse import (
    COLLAPSED_IDENTITY_ID_REGEX,
    collapsed_identity_duplicate_match,
    collapsed_keeper_set,
    collapsed_labeled_member_match,
    deleted_collapsed_member_match,
    deleted_miscollapsed_billing_sku_match,
    collapse_expense_chunk,
    composer_collapse_identity,
    dataproc_collapse_identity,
    ENCODED_AIRFLOW_DAG_ID_FIELD,
    ENCODED_COMPOSER_UUID_FIELD,
    ENCODED_DATAPROC_CLUSTER_UUID_FIELD,
    ENCODED_GKE_NAME_FIELD,
    GCP_BILLING_SKU_ID_REGEX,
    collapse_leftover_may_fold,
    cloudsql_instance_leftover_identity,
    gcp_collapse_identity,
    gcp_detailed_collapse_identity,
    gcp_row_collapse_identity,
    collapsed_identity_from_resource_id,
    gke_collapse_identity,
    is_foreign_collapsed_identity,
    is_gcp_billing_sku_id,
    is_labeled_collapse_leftover_id,
    is_unlabeled_gke_volume,
    labeled_collapse_raw_filter,
    labeled_collapse_raw_rewrite_updates,
    pick_collapsed_duplicate_keeper,
    PLAIN_AIRFLOW_DAG_ID_FIELD,
    PLAIN_COMPOSER_UUID_FIELD,
    PLAIN_DATAPROC_BATCH_ID_FIELD,
    PLAIN_DATAPROC_BATCH_UUID_FIELD,
    PLAIN_DATAPROC_CLUSTER_UUID_FIELD,
    PLAIN_GKE_NAME_FIELD,
    SERVERLESS_DATAPROC_DAG_RAW_FIELD,
    serverless_dataproc_raw_rewrite_filter,
    serverless_dataproc_raw_rewrite_updates,
    stale_serverless_dataproc_keeper_set,
    stale_serverless_dataproc_resource_filter,
    unlabeled_gke_pvc_resource_filter,
    unlabeled_gke_volume_collapse_identity,
)
import tools.optscale_time as opttime

LOG = logging.getLogger(__name__)
# Align Mongo insert batches with BQ page size so each fetched page can be
# flushed in fewer insert_many round-trips (pymongo splits oversized batches).
WRITE_CHUNK_SIZE = 20000
READ_CHUNK_SIZE = 200
# Fetch larger BQ pages so long first-loads spend less time on RPC round-trips.
BQ_PAGE_SIZE = 20000
# Progress % is BQ rows already consumed by merge / QueryJob.total_rows.
# Do not key off unique merged outputs: 264k BQ rows can collapse to 20k
# items and then PROGRESS_LOG_EVERY never fires (status stays MongoDB 0%).
PROGRESS_LOG_EVERY = 10000
PROGRESS_LOG_EVERY_SEC = 10
CLEAN_PROGRESS_LOG_EVERY_PCT = 10
# Allow small BQ↔Mongo billed-cost drift (float/merge); fail when the gap
# exceeds $1. ClickHouse vs Mongo is a cloud-account warning, not a fail.
GCP_RAW_COST_MISMATCH_TOLERANCE = 1.0
# How far back (by last expense date) to scan fresh billing partitions on
# incremental runs. Late GCP exports for older usage_start land in new
# partitions; those rows are merged into the Mongo day of usage_start
# without wiping that day and without $set-clobbering a previously merged
# unique-key total. generate_clean then rewrites ClickHouse for the
# billing identities that were merged, from that usage day forward —
# not by widening the BigQuery partition scan.
GCP_INCREMENTAL_LOOKBACK_DAYS = 3
_GCP_NUMERIC_RAW_FIELDS = (
    'cost', 'usage_amount', 'usage_amount_in_pricing_units')
GCP_MONTH_TYPE_ORDER = (
    'Instance', 'Volume', 'Snapshot', 'Bucket', 'IP Address', 'Image',
)
# Prefer collapse / SKU-helper types over leftover service names when one
# billing id has both (VM "Micro Instance" + "Network HTTP …" → Instance).
_RECONCILE_TYPE_RANK = {
    'GKE': 0, 'Composer': 0, 'Dataproc': 0,
    'Cloud Run': 0, 'Cloud Run Functions': 0,
    'Instance': 1, 'Volume': 1, 'Snapshot': 1, 'Bucket': 1,
    'IP Address': 1, 'Image': 1, 'Cloud SQL': 1,
}
GCP_RAW_CHECKPOINT_KIND = 'gcp_raw_first_load'
GCP_RAW_CHECKPOINT_COLLECTION = 'import_checkpoints'
CORES_SYSTEM_TAG = 'compute.googleapis.com/cores'
FLAVOR_SYSTEM_TAG = 'compute.googleapis.com/machine_spec'
OPTSCALE_RESOURCE_ID_TAG = 'optscale_tracking_id'
# Detailed export resource.global_name tails that match discovery
# cloud_resource_id (GCP numeric id).
_GCP_GLOBAL_NAME_ID_RE = re.compile(
    r'/(?:instances|disk|disks|addresses|globalAddresses|snapshots|images)/'
    r'([^/]+)$'
)
_GCP_BUCKET_NAME_RE = re.compile(
    r'//storage\.googleapis\.com/projects/[^/]+/buckets/([^/]+)$'
)


class GcpReportImporter(BaseReportImporter):

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
            ca_last_import_dt = None
        if self.recalculate:
            # Mongo already holds billed raw. Honor the CA cursor as the
            # reload window; wipe ClickHouse only (increments still merge).
            if ca_last_import_at:
                self.period_start = ca_last_import_dt.replace(
                    hour=0, minute=0, second=0, microsecond=0)
            if not self.period_start:
                self.set_period_start()
            self._clear_clickhouse_expenses_from_period_start()
            return
        period_reload = False
        if ca_last_import_at and same_calendar_month:
            # Normal incremental: prefer last expense date (minus lookback)
            # because the latest GCP expenses are not available immediately.
            # Intentional reimport: if the CA cursor was rewound earlier than
            # that lookback window, honor last_import_at and wipe raw from
            # there (same as BaseReportImporter for prior-month rewinds).
            last_exp_date = self.get_last_import_date(self.cloud_acc_id)
            if last_exp_date:
                last_exp_day = last_exp_date.replace(
                    hour=0, minute=0, second=0, microsecond=0)
                incremental_start = last_exp_day - timedelta(
                    days=GCP_INCREMENTAL_LOOKBACK_DAYS)
                cursor_day = ca_last_import_dt.replace(
                    hour=0, minute=0, second=0, microsecond=0)
                if cursor_day < incremental_start:
                    self.period_start = cursor_day
                    self.remove_raw_expenses_from_period_start(
                        self.cloud_acc_id)
                    period_reload = True
                else:
                    self.period_start = incremental_start
        if not self.period_start:
            super().detect_period_start()
            if ca_last_import_at and not same_calendar_month:
                period_reload = True
        if period_reload:
            self._clear_clickhouse_expenses_from_period_start()

    def get_unique_field_list(self):
        # Same usage hour can carry two invoice.month values (CUD / late
        # export) on real projects and virtual CAs. Without invoice_month
        # in the upsert key those rows merge and billing-month totals drift.
        return [
            'start_date',
            'resource_id',
            'resource_hash',
            'cloud_account_id',
            'sku',
            'service',
            'invoice_month',
        ]

    def get_update_fields(self):
        return ['cost', 'usage_amount', 'usage_amount_in_pricing_units', 'credits']

    def _get_resource_region(self, region_data):
        """Gcp provides location info in format:
        {
          "location": "europe-west3",
          "country": "DE",
          "region": "europe-west3",
          "zone": None
        }
        Sometimes region and zone data is None, so use
        country or location as resource's region
        """
        if not region_data:
            return None
        elif region_data.get('region'):
            return region_data['region']
        elif region_data.get('zone'):
            return self.cloud_adapter.zone_region(
                region_data['zone'])
        # see https://datatrendstech.atlassian.net/browse/OS-5071 on why we
        # prefer location over country
        region = region_data['location'] or region_data['country']
        return region.lower() if region else None

    @staticmethod
    def _resource_id_from_detailed(resource_global_name, resource_name):
        """Map detailed-export identity to discovery cloud_resource_id.

        Only use detailed identity for resource kinds discovery also tracks
        (Compute instance/disk/IP/snapshot/image and GCS buckets). For other
        services (BigQuery datasets, Composer, Logging, …) return None so the
        caller falls back to sku.id — otherwise every global_name becomes a
        new Mongo resource and totals explode.
        """
        if resource_global_name:
            gname = str(resource_global_name)
            match = _GCP_GLOBAL_NAME_ID_RE.search(gname)
            if match:
                return match.group(1)
            match = _GCP_BUCKET_NAME_RE.search(gname)
            if match:
                return match.group(1)
            return None
        if resource_name:
            name = str(resource_name)
            # Accept compute-style paths only (not bare BQ dataset names).
            if any(p in name for p in (
                    '/instances/', '/disk/', '/disks/',
                    '/addresses/', '/snapshots/', '/images/')):
                return name.rstrip('/').rsplit('/', 1)[-1]
        return None

    def _get_discovery_resource_ids(self):
        """cloud_resource_id set for resources created by discovery (have active)."""
        cached = getattr(self, '_discovery_resource_ids', None)
        if cached is None:
            cached = set(self.mongo_resources.distinct(
                'cloud_resource_id',
                {
                    'cloud_account_id': self.cloud_acc_id,
                    'deleted_at': 0,
                    'active': {'$exists': True},
                }))
            self._discovery_resource_ids = cached
            LOG.info(
                'Loaded %s discovery resource ids for %s',
                len(cached), self.cloud_acc_id)
        return cached

    def _unique_gke_cluster(self, refresh=False):
        """Live Mongo gke/<name> when this account has exactly one.

        Raw goog-k8s-cluster-name is not used: Composer environments also
        carry GKE cluster labels, which made a single real GKE look like
        many clusters and skipped unlabeled PVC collapse.
        """
        if refresh:
            self._unique_gke_cluster_cache = Ellipsis
        cached = getattr(self, '_unique_gke_cluster_cache', Ellipsis)
        if cached is not Ellipsis:
            return cached
        names = set()
        resources = getattr(self, 'mongo_resources', None)
        resource_distinct = getattr(resources, 'distinct', None)
        if callable(resource_distinct):
            for rid in resource_distinct('cloud_resource_id', {
                'cloud_account_id': self.cloud_acc_id,
                'deleted_at': 0,
                'cloud_resource_id': {'$regex': r'^gke/'},
            }) or []:
                text = str(rid or '')
                if text.startswith('gke/') and len(text) > 4:
                    names.add(text[4:])
        names.discard('')
        self._unique_gke_cluster_cache = (
            next(iter(names)) if len(names) == 1 else None)
        return self._unique_gke_cluster_cache

    @staticmethod
    def _generate_resource_id(row_dict, discovery_ids=None):
        """Stable GCP billing resource id for clean expenses / discovery merge.

        Dataproc / Composer / GKE members share a cluster label: collapse
        onto that identity so billing and discovery upsert one Mongo doc.
        Serverless Dataproc batches collapse by Airflow DAG (same rule in
        every project); classic clusters keep cluster-uuid.
        Otherwise use detailed-export identity (numeric instance/disk id or
        GCS bucket name) whenever `_resource_id_from_detailed` matches —
        waiting for discovery created SKU-id twins next to numeric docs.
        Other services still fall back to sku.id.
        discovery_ids is kept for callers; matching kinds no longer require
        the id to already exist in discovery.
        """
        collapse = gcp_collapse_identity(row_dict.get('tags') or {})
        if collapse:
            return collapse['cloud_resource_id']
        detailed = gcp_detailed_collapse_identity(row_dict)
        if detailed:
            return detailed['cloud_resource_id']
        detailed_id = GcpReportImporter._resource_id_from_detailed(
            row_dict.get('resource_global_name'),
            row_dict.get('resource_name'),
        )
        if detailed_id:
            return detailed_id
        # Legacy / explicit aliases
        for key in ('bq_resource_id',):
            val = row_dict.get(key)
            if val:
                return str(val)
        sku_id = row_dict.get('sku_id')
        if sku_id:
            return str(sku_id)
        return str(row_dict.get('sku') or '')

    @staticmethod
    def _resource_short_name(row_dict):
        """Human-readable name from detailed export resource.name if present."""
        name = row_dict.get('resource_name')
        if not name:
            return None
        return str(name).rstrip('/').rsplit('/', 1)[-1] or None

    @staticmethod
    def _convert_tags_list_to_dict(tags):
        if isinstance(tags, list):
            result = {}
            for tag_dict in tags:
                result[tag_dict['key']] = tag_dict['value']
            return result
        else:
            return tags

    def _process_row_cost(self, row_dict: dict):
        # to get the final billed cost we need to add credits,
        # which are usually negative, to cost values.
        # original value of the cost field is stored for visibility.
        row_dict['original_cost'] = row_dict['cost']
        row_dict['cost'] += sum(credit['amount'] for credit in row_dict['credits'])

    def _row_to_dict(self, row):
        row_dict = dict(row.items())
        self._process_row_cost(row_dict)
        row_dict['region'] = self._get_resource_region(
            row_dict.pop('location', None))
        row_dict['cloud_account_id'] = self.cloud_acc_id
        row_dict['tags'] = self._convert_tags_list_to_dict(row_dict['tags'])
        row_dict['system_tags'] = self._convert_tags_list_to_dict(
            row_dict['system_tags'])
        # Keep BQ usage_start_time / usage_end_time as OptScale dates.
        # Do not shift virtual-CA rows into invoice.month: that invents
        # future calendar days (e.g. Jul 31 usage → Aug 31) and breaks
        # Data Sources total vs Resources for the real loaded window.
        resource_hash = row_dict['tags'].get(OPTSCALE_RESOURCE_ID_TAG)
        # Check that hash is sha1. This is only needed for our hystaxcom account
        # where we experimented with our resource tagging strategies
        # and some expenses have unexpected values for resource hash.
        if resource_hash and len(resource_hash) == 40:
            row_dict['resource_hash'] = resource_hash
        else:
            row_dict['resource_id'] = self._generate_resource_id(
                row_dict,
                discovery_ids=self._get_discovery_resource_ids())
            if is_unlabeled_gke_volume(row_dict):
                ident = unlabeled_gke_volume_collapse_identity(
                    row_dict, self._unique_gke_cluster())
                if ident:
                    row_dict['resource_id'] = ident['cloud_resource_id']
        export_time = row_dict.get('export_time')
        row_dict['export_times'] = (
            [export_time] if export_time is not None else [])
        return row_dict

    def _merge_same_billing_items(self, items):
        unique_fields = self.get_unique_field_list()
        update_fields = self.get_update_fields()

        b_item_map = defaultdict(list)
        for b_item in items:
            key = tuple(b_item.get(f) for f in unique_fields)
            b_item_map[key].append(b_item)

        for k, items_list in b_item_map.items():
            if len(items_list) <= 1:
                continue
            common_item = {k: v for k, v in items_list[0].items()}
            for item in items_list[1:]:
                for field in update_fields:
                    value = item.get(field)
                    if value:
                        common_item[field] += value
            b_item_map[k] = [common_item]
        updated_billing_items = [v[0] for v in b_item_map.values()]
        return updated_billing_items

    def _merge_billing_items(self, base_item, new_item):
        for field in self.get_update_fields():
            value = new_item.get(field)
            if value:
                base_item[field] += value
        base_times = list(base_item.get('export_times') or [])
        for export_time in (new_item.get('export_times') or []):
            if export_time not in base_times:
                base_times.append(export_time)
        base_item['export_times'] = base_times
        return base_item

    def _iter_merged_billing_items(self, rows):
        """Merge billing rows that share the raw unique key.

        Must NOT rely on adjacency: with detailed export, resource_id flips
        between discovery numeric ids and sku.id for neighboring BQ rows
        (ORDER BY is by location/zone, not by the Python merge key). Adjacent
        streaming merge then emits many items with the same unique key;
        unordered bulk upsert inserts a duplicate for each.

        BQ orders by start_date first, so a completed hour never reappears:
        flush the hashmap when start_date advances to keep memory bounded.
        """
        unique_fields = self.get_unique_field_list()
        by_key = {}
        order = []
        current_start = object()

        def _flush():
            nonlocal by_key, order
            for item_key in order:
                yield by_key[item_key]
            by_key = {}
            order = []

        for row in rows:
            item = self._row_to_dict(row)
            start = item.get('start_date')
            if start != current_start:
                if by_key:
                    yield from _flush()
                current_start = start
            item_key = tuple(item.get(field) for field in unique_fields)
            existing = by_key.get(item_key)
            if existing is not None:
                self._merge_billing_items(existing, item)
                continue
            by_key[item_key] = item
            order.append(item_key)
        if by_key:
            yield from _flush()

    def update_raw_records(self, chunk):
        for expense in chunk:
            self._update_imported_raw_interval(expense)
        if getattr(self, '_raw_insert_only', False):
            # First load / day rebuild after wipe: plain bulk insert.
            # Upserts are pointless on an empty window and are much slower;
            # unordered upsert also races when duplicate keys slip into a chunk.
            retry_mongo_upsert(
                self.mongo_raw.insert_many, chunk, ordered=False)
            return
        ops = []
        for expense in chunk:
            ops.extend(self._incremental_raw_update_ops(expense))
        if ops:
            retry_mongo_upsert(
                self.mongo_raw.bulk_write, ops, ordered=False)

    def _incremental_raw_update_ops(self, expense):
        """Merge one incremental BQ slice without clobbering a full-key total.

        Full-month load sums every partition line that shares the unique key.
        Incremental only sees the last N partitions. $set of that slice would
        overwrite the merged cost (month reconcile breaks). Re-reading the
        same partitions must not $inc either (would double). Apply incoming
        numeric fields only when this export_time is new. Legacy docs with no
        export_times keep cost and start tracking the seen export_time.

        Uses 3.6-safe update documents (no aggregation-pipeline updates).
        """
        filt = self.get_raw_upsert_filters(expense)
        export_time = expense.get('export_time')
        insert_doc = dict(expense)
        if export_time is not None:
            insert_doc['export_times'] = list(
                insert_doc.get('export_times') or [export_time])
        else:
            insert_doc.setdefault('export_times', [])
        ops = [UpdateOne(
            filter=filt,
            update={'$setOnInsert': insert_doc},
            upsert=True,
        )]
        if export_time is None:
            return ops
        inc_fields = {
            field: float(expense.get(field) or 0)
            for field in _GCP_NUMERIC_RAW_FIELDS
            if field in expense
        }
        ops.append(UpdateOne(
            filter={
                **filt,
                'export_times.0': {'$exists': True},
                'export_times': {'$nin': [export_time]},
            },
            update={
                '$inc': inc_fields,
                '$addToSet': {'export_times': export_time},
            },
            upsert=False,
        ))
        ops.append(UpdateOne(
            filter={
                **filt,
                '$or': [
                    {'export_times': {'$exists': False}},
                    {'export_times': None},
                    {'export_times': []},
                ],
            },
            update={'$set': {'export_times': [export_time]}},
            upsert=False,
        ))
        return ops

    def _clear_raw_for_window(self, start, end):
        """Delete raw expenses in [start, end) before an idempotent rebuild."""
        result = self.mongo_raw.delete_many({
            'cloud_account_id': self.cloud_acc_id,
            'start_date': {'$gte': start, '$lt': end},
        })
        LOG.info(
            'Cleared %s raw expense(s) for %s in %s..%s before rebuild',
            result.deleted_count, self.cloud_acc_id, start, end)
        return result.deleted_count

    def _clear_raw_for_invoice_months(self, months):
        """Delete virtual-CA raw rows for invoice months being rebuilt.

        Virtual CAs reconcile by invoice.month; usage_start can sit in the
        previous calendar month (CUD). A start_date window wipe leaves those
        rows, and the next load $inc's them again — billed vs Mongo diverges.
        """
        months = [m for m in (months or []) if m]
        if not months:
            return 0
        result = self.mongo_raw.delete_many({
            'cloud_account_id': self.cloud_acc_id,
            'invoice_month': {'$in': list(months)},
        })
        LOG.info(
            'Cleared %s raw expense(s) for %s invoice_month in %s',
            result.deleted_count, self.cloud_acc_id, months)
        return result.deleted_count

    @property
    def _mongo_checkpoints(self):
        return self.mongo_raw.database[GCP_RAW_CHECKPOINT_COLLECTION]

    def _raw_checkpoint_filter(self):
        return {
            'cloud_account_id': self.cloud_acc_id,
            'kind': GCP_RAW_CHECKPOINT_KIND,
        }

    def _get_raw_checkpoint(self):
        return self._mongo_checkpoints.find_one(self._raw_checkpoint_filter())

    def _save_raw_checkpoint(self, period_start, period_end, written,
                             bq_billed_sum=0.0, written_cost_sum=0.0):
        self._mongo_checkpoints.update_one(
            self._raw_checkpoint_filter(),
            {'$set': {
                'cloud_account_id': self.cloud_acc_id,
                'kind': GCP_RAW_CHECKPOINT_KIND,
                'period_start': period_start,
                'period_end': period_end,
                'written': written,
                'bq_billed_sum': bq_billed_sum,
                'written_cost_sum': written_cost_sum,
                'completed_at': opttime.utcnow(),
            }},
            upsert=True,
        )

    def _record_billed_check(self, start, end, bq_billed_sum, written_cost_sum,
                             reconciliation=None, target_cost_sum=None):
        self._last_load_start = start
        self._last_load_end = end
        self._last_bq_billed_sum = float(bq_billed_sum)
        self._last_written_cost_sum = float(written_cost_sum)
        if target_cost_sum is not None:
            self._last_target_cost_sum = float(target_cost_sum)
        self._last_reconciliation = list(reconciliation or [])
        self._publish_import_details()

    def _publish_import_details(self):
        """Persist Billing Reconciliation before any later fail-gate."""
        report_import_id = getattr(self, 'report_import_id', None)
        rest_cl = getattr(self, 'rest_cl', None)
        if not report_import_id or rest_cl is None:
            return
        details = self.get_import_details()
        if not details:
            return
        try:
            rest_cl.report_import_update(
                report_import_id, {'details': details})
        except Exception as exc:
            LOG.warning(
                'Failed to publish GCP import details: %s', exc)

    def get_import_details(self):
        """Current-month source (BQ) vs stage (Mongo) vs target (CH)."""
        rows = getattr(self, '_last_reconciliation', None)
        source = getattr(self, '_last_bq_billed_sum', None)
        if source is None and not rows:
            return None
        local = float(getattr(self, '_last_written_cost_sum', 0.0) or 0.0)
        source = 0.0 if source is None else float(source)
        start = getattr(self, '_last_load_start', None)
        end = getattr(self, '_last_load_end', None)
        payload = {
            'period_start': int(start.timestamp()) if start else None,
            'period_end': int(end.timestamp()) if end else None,
            'source_sum': source,
            'local_sum': local,
            'delta': abs(source - local),
            'reconciliation': rows or [],
        }
        if hasattr(self, '_last_target_cost_sum'):
            target = float(self._last_target_cost_sum or 0.0)
            payload['target_sum'] = target
            payload['delta'] = max(
                abs(source - local), abs(source - target), abs(local - target))
        return payload

    def _current_month_bounds(self):
        now = opttime.utcnow()
        month_start = get_month_start(now)
        month_end = (month_start.replace(day=28) + timedelta(days=8)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0)
        partition_end = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        return month_start, month_end, partition_end

    def _mongo_month_match(self, month_start, month_end):
        """Mongo filter aligned with BQ current-month reconcile.

        Real projects: usage_start in [month_start, month_end).
        Virtual (null-project) CAs: invoice.month from BigQuery — usage_start
        can fall in the previous calendar month for the same invoice.
        """
        start_n = self._naive_utc(month_start)
        end_n = self._naive_utc(month_end)
        match = {'cloud_account_id': self.cloud_acc_id}
        if self.cloud_adapter.is_virtual_billing_project:
            match['invoice_month'] = start_n.strftime('%Y%m')
        else:
            match['start_date'] = {'$gte': start_n, '$lt': end_n}
        return match

    @staticmethod
    def _canonical_reconcile_type(rtype):
        """Only aliases that split the same cost across two table names.

        Matching types (GKE, Volume, …) stay as-is. Cloud Storage is the
        SKU-helper leftover of Bucket. Cloud Composer is the billing service
        name for the same Composer environment identity.
        """
        if rtype == 'Cloud Storage':
            return 'Bucket'
        if rtype == 'Cloud Composer':
            return 'Composer'
        return rtype or 'Unknown'

    def _reconcile_resource_type(self, expense):
        """Same type cases as clean/CH: collapse first, then SKU helper.

        ClickHouse is keyed by the collapsed keeper. SKU-only grouping left
        Instance/Volume/K8s on Mongo/BQ while CH sat on GKE/Dataproc/Composer.
        """
        rid = str((expense or {}).get('resource_id') or '')
        from_id = collapsed_identity_from_resource_id(rid)
        if from_id:
            return from_id['resource_type']
        collapse = gcp_collapse_identity((expense or {}).get('tags') or {})
        if collapse:
            return collapse['resource_type']
        detailed = gcp_detailed_collapse_identity(expense or {})
        if detailed:
            return detailed['resource_type']
        if is_unlabeled_gke_volume(expense):
            ident = unlabeled_gke_volume_collapse_identity(
                expense, self._unique_gke_cluster())
            if ident:
                return ident['resource_type']
        r_type, _ = self._get_resource_type_and_name({
            'cost_type': (expense or {}).get('cost_type') or 'regular',
            'sku': (expense or {}).get('sku') or '',
            'service': (expense or {}).get('service'),
            'resource_id': (expense or {}).get('resource_id'),
            'resource_hash': (expense or {}).get('resource_hash'),
            'region': (expense or {}).get('region'),
        })
        return self._canonical_reconcile_type(r_type)

    def _clickhouse_resource_type(self, doc):
        """Classify a CH resource with the same billing type as Mongo.

        Discovery resource_type can say Cloud SQL / Instance while this
        month's raw grouped as Instance / Compute Engine. Use the billing
        map when present; otherwise collapse identity, then the doc type.
        Do not merge matching types (GKE, Composer, Cloud SQL that already
        agrees with billing).
        """
        type_by_rid = getattr(self, '_reconcile_type_by_billing_id', None) or {}
        for key in (
                doc.get('cloud_resource_id'),
                doc.get('cloud_resource_hash'),
                doc.get('_id')):
            if key and key in type_by_rid:
                return type_by_rid[key]
        rid = str(doc.get('cloud_resource_id') or '')
        from_id = collapsed_identity_from_resource_id(rid)
        if from_id:
            return from_id['resource_type']
        collapse = gcp_collapse_identity(doc.get('tags') or {})
        if collapse:
            return collapse['resource_type']
        return self._canonical_reconcile_type(doc.get('resource_type'))

    @staticmethod
    def _mongo_month_group_expense(row):
        """Normalize a month $group row to an expense dict plus resource ids."""
        key = row.get('_id')
        if isinstance(key, dict) and (
                'sku' in key or 'service' in key or 'cost_type' in key
                or 'rid' in key):
            rids = [rid for rid in (row.get('rids') or []) if rid]
            rid = key.get('rid') or row.get('resource_id')
            if rid and rid not in rids:
                rids.append(rid)
            return {
                'cost_type': key.get('cost_type') or row.get('cost_type')
                or 'regular',
                'sku': key.get('sku') or row.get('sku') or '',
                'service': key.get('service') or row.get('service'),
                'resource_hash': row.get('resource_hash'),
                'resource_id': rids[0] if rids else rid,
                'tags': row.get('tags') or {},
            }, rids
        rid = key or row.get('resource_id') or row.get('resource_hash')
        rids = [rid] if rid else []
        return {
            'cost_type': row.get('cost_type') or 'regular',
            'sku': row.get('sku') or '',
            'service': row.get('service'),
            'resource_hash': row.get('resource_hash'),
            'resource_id': rid,
            'tags': row.get('tags') or {},
        }, rids

    @staticmethod
    def _bind_billing_type(type_by_rid, rid, rtype):
        """Keep collapse/Instance over leftover service names on the same id."""
        if not rid:
            return
        prev = type_by_rid.get(rid)
        if prev is None:
            type_by_rid[rid] = rtype
            return
        new_rank = _RECONCILE_TYPE_RANK.get(rtype, 50)
        prev_rank = _RECONCILE_TYPE_RANK.get(prev, 50)
        if new_rank < prev_rank:
            type_by_rid[rid] = rtype

    def _mongo_month_by_resource_type(self, month_start, month_end):
        """Same type rules as BQ stream: sku groups, collapse via tags/id.

        Do not $first sku per resource_id: a VM's Network SKU would relabel
        Micro Instance cost as Compute Engine and split the reconcile card.
        """
        pipeline = [
            {'$match': self._mongo_month_match(month_start, month_end)},
            {'$group': {
                '_id': {
                    'rid': {'$ifNull': ['$resource_id', '$resource_hash']},
                    'sku': '$sku',
                    'service': '$service',
                    'cost_type': '$cost_type',
                },
                'cost': {'$sum': '$cost'},
                'tags': {'$first': '$tags'},
                'resource_hash': {'$first': '$resource_hash'},
                'resource_id': {'$first': '$resource_id'},
            }},
        ]
        by_type = defaultdict(lambda: {'cost': 0.0, 'rids': set()})
        type_by_rid = {}
        for row in self.mongo_raw.aggregate(pipeline, allowDiskUse=True):
            expense, rids = self._mongo_month_group_expense(row)
            rtype = self._reconcile_resource_type(expense)
            by_type[rtype]['cost'] += float(row.get('cost') or 0)
            for rid in rids:
                by_type[rtype]['rids'].add(rid)
                self._bind_billing_type(type_by_rid, rid, rtype)
        self._reconcile_type_by_billing_id = type_by_rid
        return {
            rtype: {
                'cost': vals['cost'],
                'resource_count': len(vals['rids']),
            }
            for rtype, vals in by_type.items()
        }

    @staticmethod
    def _sort_resource_types(types):
        rank = {name: i for i, name in enumerate(GCP_MONTH_TYPE_ORDER)}
        return sorted(types, key=lambda t: (rank.get(t, 100), t or ''))

    def _reset_stream_month_totals(self, window_start=None):
        """Cache current-month bounds once; do not call utcnow per row."""
        month_start, month_end, _ = self._current_month_bounds()
        self._stream_month_start = self._naive_utc(month_start)
        self._stream_month_end = self._naive_utc(month_end)
        self._stream_window_start = self._naive_utc(window_start)
        # sku/service/cost_type → same grain as Mongo $group, current month only.
        self._stream_month_groups = defaultdict(
            lambda: {'cost': 0.0, 'rids': set(), 'resource_hash': None})

    def _virtual_invoice_month_reconcile(self):
        """Virtual CAs (Compute Engine, Support) reconcile by invoice.month.

        Usage_start can sit in the previous (or next) calendar month for the
        same invoice. Real projects keep the usage_start window.
        """
        adapter = getattr(self, 'cloud_adapter', None)
        return bool(
            adapter is not None
            and getattr(adapter, 'is_virtual_billing_project', False))

    def _stream_row_in_current_month(self, row_dict):
        """Current-month grain for stream source.

        Virtual CAs: invoice.month. Real projects: usage_start.
        """
        start = getattr(self, '_stream_month_start', None)
        end = getattr(self, '_stream_month_end', None)
        if start is None or end is None:
            return False
        if self._virtual_invoice_month_reconcile():
            return (
                str(row_dict.get('invoice_month') or '')
                == start.strftime('%Y%m'))
        sd = self._naive_utc(row_dict.get('start_date'))
        return sd is not None and start <= sd < end

    def _accumulate_stream_month_row(self, row_dict):
        """Fold one merged get_usage row into current-month source totals.

        Uses the same cost (credits already applied) and sku grain as the
        Mongo write. Do not query BigQuery again.
        """
        groups = getattr(self, '_stream_month_groups', None)
        start = getattr(self, '_stream_month_start', None)
        end = getattr(self, '_stream_month_end', None)
        if groups is None or start is None or end is None:
            return
        if not self._stream_row_in_current_month(row_dict):
            return
        rtype = self._reconcile_resource_type(row_dict)
        key = (
            row_dict.get('sku') or '',
            row_dict.get('service'),
            row_dict.get('cost_type') or 'regular',
            rtype,
        )
        bucket = groups[key]
        bucket['cost'] += float(row_dict.get('cost') or 0)
        rid = row_dict.get('resource_id') or row_dict.get('resource_hash')
        if rid:
            bucket['rids'].add(rid)
        if bucket['resource_hash'] is None:
            bucket['resource_hash'] = row_dict.get('resource_hash')

    def _stream_covers_current_month(self):
        window_start = getattr(self, '_stream_window_start', None)
        month_start = getattr(self, '_stream_month_start', None)
        if window_start is None or month_start is None:
            return False
        return window_start <= month_start

    def _stream_month_source_by_type(self):
        """Map sku groups with the same helper as _mongo_month_by_resource_type."""
        groups = getattr(self, '_stream_month_groups', None) or {}
        by_type = defaultdict(lambda: {'billed_sum': 0.0, 'rids': set()})
        for key, vals in groups.items():
            if len(key) == 4:
                rtype = key[3]
            else:
                sku, service, cost_type = key[0], key[1], key[2]
                rtype = self._reconcile_resource_type({
                    'cost_type': cost_type or 'regular',
                    'sku': sku or '',
                    'service': service,
                    'resource_hash': vals.get('resource_hash'),
                })
            by_type[rtype]['billed_sum'] += float(vals.get('cost') or 0)
            by_type[rtype]['rids'].update(vals.get('rids') or ())
        return {
            rtype: {
                'billed_sum': vals['billed_sum'],
                'resource_count': len(vals['rids']),
            }
            for rtype, vals in by_type.items()
        }

    def _reconcile_current_month(self):
        """After each load, reconcile the current calendar month only.

        When this job's get_usage window covers the month start, source
        totals come from the same merged rows written to Mongo — never a
        second billing-export query. An incremental slice is not a
        full-month BQ snapshot; source then follows Mongo so ClickHouse
        compare stays local vs target.
        """
        month_start, month_end, _partition_end = self._current_month_bounds()
        mongo_by_type = self._mongo_month_by_resource_type(
            month_start, month_end)
        if self._stream_covers_current_month():
            bq_by_type = self._stream_month_source_by_type()
        else:
            bq_by_type = {
                rtype: {
                    'billed_sum': vals['cost'],
                    'resource_count': vals['resource_count'],
                }
                for rtype, vals in mongo_by_type.items()
            }
        types = self._sort_resource_types(
            set(bq_by_type) | set(mongo_by_type))
        reconciliation = []
        bq_total = 0.0
        mongo_total = 0.0
        for rtype in types:
            bq = bq_by_type.get(rtype) or {}
            mongo = mongo_by_type.get(rtype) or {}
            bq_cost = float(bq.get('billed_sum') or 0)
            mongo_cost = float(mongo.get('cost') or 0)
            delta = abs(bq_cost - mongo_cost)
            bq_total += bq_cost
            mongo_total += mongo_cost
            reconciliation.append({
                'resource_type': rtype,
                'source_sum': bq_cost,
                'local_sum': mongo_cost,
                'source_count': int(bq.get('resource_count') or 0),
                'local_count': int(mongo.get('resource_count') or 0),
                'delta': delta,
                'status': (
                    'ok' if delta <= GCP_RAW_COST_MISMATCH_TOLERANCE
                    else 'mismatch'),
            })
        LOG.info(
            'GCP month reconcile for %s %s..%s: bq_billed=%s mongo_cost=%s '
            'types=%s',
            self.cloud_acc_id, month_start, month_end, bq_total, mongo_total,
            len(reconciliation))
        self._record_billed_check(
            month_start, month_end, bq_total, mongo_total,
            reconciliation=reconciliation)
        self._verify_raw_load_billed_cost(bq_total, mongo_total)
        return bq_total, mongo_total

    @staticmethod
    def _naive_utc(dt):
        if dt is None:
            return None
        if getattr(dt, 'tzinfo', None) is not None:
            return dt.replace(tzinfo=None)
        return dt

    def _row_in_window(self, row, start, end):
        sd = self._naive_utc(row.get('start_date'))
        start_n = self._naive_utc(start)
        end_n = self._naive_utc(end)
        if sd is None or start_n is None or end_n is None:
            return True
        return start_n <= sd < end_n

    def _reset_backdated_clean_state(self):
        self._backdated_usage_days = set()
        self._backdated_resource_ids = set()
        self._backdated_resource_hashes = set()
        self._backdated_hour_keys = []

    def _usage_day(self, start_date):
        dt = self._naive_utc(start_date)
        if dt is None:
            return None
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    def _note_backdated_row(self, row):
        """Remember a late-export row so clean can rewrite that usage day."""
        day = self._usage_day(row.get('start_date'))
        if day is not None:
            self._backdated_usage_days.add(day)
        rid = row.get('resource_id')
        if rid:
            self._backdated_resource_ids.add(rid)
        rhash = row.get('resource_hash')
        if rhash:
            self._backdated_resource_hashes.add(rhash)
        key = {
            'start_date': row.get('start_date'),
            'sku': row.get('sku'),
            'service': row.get('service'),
        }
        invoice = row.get('invoice_month')
        if invoice:
            key['invoice_month'] = invoice
        if key.get('start_date') is not None:
            self._backdated_hour_keys.append(key)

    def _backdated_clean_from(self):
        """Oldest backdated usage day still before period_start, or None."""
        days = getattr(self, '_backdated_usage_days', None) or set()
        if not days:
            return None
        start = self._naive_utc(getattr(self, 'period_start', None))
        if start is None:
            return None
        older = [day for day in days if day < start]
        if not older:
            return None
        return min(older)

    def _backdated_clean_identities(self):
        """Post-rekey resource_id / resource_hash for late-merged raw rows."""
        ids = {rid for rid in (
            getattr(self, '_backdated_resource_ids', None) or ()) if rid}
        hashes = {rhash for rhash in (
            getattr(self, '_backdated_resource_hashes', None) or ()) if rhash}
        hour_keys = list(getattr(self, '_backdated_hour_keys', None) or ())
        mongo_raw = getattr(self, 'mongo_raw', None)
        if mongo_raw is not None and hour_keys:
            match = {'cloud_account_id': self.cloud_acc_id}
            if len(hour_keys) == 1:
                match.update(hour_keys[0])
            else:
                match['$or'] = hour_keys
            for row in mongo_raw.aggregate([
                    {'$match': match},
                    {'$group': {
                        '_id': {
                            'resource_id': '$resource_id',
                            'resource_hash': '$resource_hash',
                        },
                    }},
            ], allowDiskUse=True):
                gid = row.get('_id') if isinstance(row, dict) else None
                if isinstance(gid, dict):
                    if gid.get('resource_id'):
                        ids.add(gid['resource_id'])
                    if gid.get('resource_hash'):
                        hashes.add(gid['resource_hash'])
        return list(ids), list(hashes)

    def _verify_raw_load_billed_cost(self, source_sum, local_sum):
        """Fail when current-month local storage diverges from billing source.

        Totals are summed in-process from the grouped month query, not from a
        second ungrouped SUM or the incremental stream window.
        """
        delta = abs(float(source_sum) - float(local_sum))
        tolerance = GCP_RAW_COST_MISMATCH_TOLERANCE
        if delta > tolerance:
            raise RuntimeError(
                'GCP current-month reconcile for {ca}: billed cost mismatch — '
                'BQ billed={bq:.6f} but Mongo written cost={mongo:.6f} '
                '(delta={delta:.6f}, tolerance={tol:.6f})'.format(
                    ca=self.cloud_acc_id,
                    bq=float(source_sum),
                    mongo=float(local_sum),
                    delta=delta,
                    tol=tolerance,
                ))

    def _clickhouse_month_filter(self, month_start, month_end):
        """Align ClickHouse with Mongo/BQ current-month bounds."""
        start_n = self._naive_utc(month_start)
        end_n = self._naive_utc(month_end)
        if self.cloud_adapter.is_virtual_billing_project:
            return (
                "invoice_month = %(invoice_month)s",
                {'invoice_month': start_n.strftime('%Y%m')},
            )
        return (
            "date >= %(start)s AND date < %(end)s",
            {'start': start_n, 'end': end_n},
        )

    def _clickhouse_month_by_resource_type(self, month_start, month_end):
        """CH cost by Mongo resource_type (clean target, after collapse).

        CollapsingMergeTree needs FINAL: reassign writes +keeper/-extra pairs
        that otherwise inflate HAVING sum(sign) > 0 without collapsing.
        """
        clickhouse_cl = getattr(self, 'clickhouse_cl', None)
        mongo_resources = getattr(self, 'mongo_resources', None)
        if clickhouse_cl is None or mongo_resources is None:
            return {}
        time_sql, time_params = self._clickhouse_month_filter(
            month_start, month_end)
        rows = clickhouse_cl.query(
            """
            SELECT resource_id, sum(cost * sign) AS cost
            FROM expenses FINAL
            WHERE cloud_account_id = %(cloud_account_id)s
              AND {time_sql}
            GROUP BY resource_id
            HAVING abs(cost) > 0.000001
            """.format(time_sql=time_sql),
            parameters={
                'cloud_account_id': self.cloud_acc_id,
                **time_params,
            },
        ).result_rows
        cost_by_id = {}
        for resource_id, cost in rows or []:
            if not resource_id:
                continue
            cost_by_id[resource_id] = float(cost or 0)
        if not cost_by_id:
            return {}
        if (getattr(self, '_reconcile_type_by_billing_id', None) is None
                and getattr(self, 'mongo_raw', None) is not None):
            self._mongo_month_by_resource_type(month_start, month_end)
        type_by_rid = getattr(self, '_reconcile_type_by_billing_id', None) or {}
        type_by_id = {}
        billed_docs = {}
        for doc in mongo_resources.find(
                {'_id': {'$in': list(cost_by_id)}},
                {'resource_type': 1, 'cloud_resource_id': 1,
                 'cloud_resource_hash': 1, 'tags': 1}):
            billed_docs[doc['_id']] = doc
            type_by_id[doc['_id']] = self._clickhouse_resource_type(doc)
        by_type = defaultdict(lambda: {'cost': 0.0, 'rids': set()})
        for resource_id, cost in cost_by_id.items():
            doc = billed_docs.get(resource_id) or {}
            if type_by_rid and not any(
                    key and key in type_by_rid for key in (
                        doc.get('cloud_resource_id'),
                        doc.get('cloud_resource_hash'),
                        doc.get('_id'),
                        resource_id)):
                # SKU twin / leftover CH not billed this month.
                continue
            rtype = type_by_id.get(resource_id) or 'Unknown'
            by_type[rtype]['cost'] += cost
            by_type[rtype]['rids'].add(resource_id)
        return {
            rtype: {
                'cost': vals['cost'],
                'resource_count': len(vals['rids']),
            }
            for rtype, vals in by_type.items()
        }

    def _merge_target_into_reconciliation(self, ch_by_type):
        rows = list(getattr(self, '_last_reconciliation', None) or [])
        by_type = {}
        for row in rows:
            canon = self._canonical_reconcile_type(row.get('resource_type'))
            existing = by_type.get(canon)
            if existing is None:
                folded = dict(row)
                folded['resource_type'] = canon
                by_type[canon] = folded
                continue
            existing['source_sum'] = (
                float(existing.get('source_sum') or 0)
                + float(row.get('source_sum') or 0))
            existing['local_sum'] = (
                float(existing.get('local_sum') or 0)
                + float(row.get('local_sum') or 0))
            existing['source_count'] = (
                int(existing.get('source_count') or 0)
                + int(row.get('source_count') or 0))
            existing['local_count'] = (
                int(existing.get('local_count') or 0)
                + int(row.get('local_count') or 0))
        folded_ch = {}
        for rtype, ch in (ch_by_type or {}).items():
            canon = self._canonical_reconcile_type(rtype)
            existing = folded_ch.get(canon)
            if existing is None:
                folded_ch[canon] = {
                    'cost': float(ch.get('cost') or 0),
                    'resource_count': int(ch.get('resource_count') or 0),
                }
                continue
            existing['cost'] += float(ch.get('cost') or 0)
            existing['resource_count'] += int(ch.get('resource_count') or 0)
        types = self._sort_resource_types(set(by_type) | set(folded_ch))
        merged = []
        for rtype in types:
            row = by_type.get(rtype) or {
                'resource_type': rtype,
                'source_sum': 0.0,
                'local_sum': 0.0,
                'source_count': 0,
                'local_count': 0,
            }
            ch = folded_ch.get(rtype) or {}
            target_sum = float(ch.get('cost') or 0)
            source_sum = float(row.get('source_sum') or 0)
            local_sum = float(row.get('local_sum') or 0)
            delta = max(
                abs(source_sum - local_sum),
                abs(source_sum - target_sum),
                abs(local_sum - target_sum),
            )
            row['resource_type'] = rtype
            row['target_sum'] = target_sum
            row['target_count'] = int(ch.get('resource_count') or 0)
            row['delta'] = delta
            row['status'] = (
                'ok' if delta <= GCP_RAW_COST_MISMATCH_TOLERANCE
                else 'mismatch')
            merged.append(row)
        return merged

    def _verify_clickhouse_target_cost(self, source_sum, local_sum, target_sum):
        """Warn on the cloud account when ClickHouse diverges from Mongo.

        BQ vs Mongo is the fail-gate. A ClickHouse-only gap must not abort:
        raw and clean already landed, and aborting skips last_import_at.
        """
        tolerance = GCP_RAW_COST_MISMATCH_TOLERANCE
        source = float(source_sum or 0)
        local = float(local_sum or 0)
        target = float(target_sum or 0)
        vs_source = abs(target - source)
        vs_local = abs(target - local)
        if vs_local <= tolerance:
            self._clickhouse_reconcile_warning = None
            return
        warning = (
            'GCP current-month reconcile for {ca}: ClickHouse target cost '
            'mismatch — BQ billed={bq:.6f} Mongo={mongo:.6f} '
            'ClickHouse={ch:.6f} (delta_bq={dbq:.6f}, delta_mongo={dm:.6f}, '
            'tolerance={tol:.6f})'.format(
                ca=self.cloud_acc_id,
                bq=source,
                mongo=local,
                ch=target,
                dbq=vs_source,
                dm=vs_local,
                tol=tolerance,
            ))
        LOG.warning(warning)
        self._clickhouse_reconcile_warning = warning

    def _reconciliation_rows_from_mongo(self, month_start, month_end):
        """Source=local snapshot from Mongo when BQ was not loaded."""
        mongo_by_type = self._mongo_month_by_resource_type(
            month_start, month_end)
        rows = []
        total = 0.0
        for rtype in self._sort_resource_types(mongo_by_type):
            mongo_row = mongo_by_type[rtype]
            local_sum = float(mongo_row.get('cost') or 0)
            local_count = int(mongo_row.get('resource_count') or 0)
            total += local_sum
            rows.append({
                'resource_type': rtype,
                'source_sum': local_sum,
                'local_sum': local_sum,
                'source_count': local_count,
                'local_count': local_count,
                'delta': 0.0,
                'status': 'ok',
            })
        return rows, total

    def _reconcile_clickhouse_target(self):
        """After clean expenses, attach CH totals; warn on CH vs Mongo drift."""
        clickhouse_cl = getattr(self, 'clickhouse_cl', None)
        if clickhouse_cl is None:
            return
        month_start, month_end, _partition_end = self._current_month_bounds()
        if getattr(self, 'recalculate', False):
            rows, mongo_total = self._reconciliation_rows_from_mongo(
                month_start, month_end)
            self._last_reconciliation = rows
            source_total = mongo_total
            local_total = mongo_total
            start = month_start
            end = month_end
        else:
            source_total = float(
                getattr(self, '_last_bq_billed_sum', 0.0) or 0.0)
            local_total = float(
                getattr(self, '_last_written_cost_sum', 0.0) or 0.0)
            start = getattr(self, '_last_load_start', None) or month_start
            end = getattr(self, '_last_load_end', None) or month_end
        try:
            ch_by_type = self._clickhouse_month_by_resource_type(
                month_start, month_end)
        except Exception as exc:
            raise RuntimeError(
                'GCP month reconcile for {ca}: ClickHouse aggregate failed: '
                '{err}'.format(ca=self.cloud_acc_id, err=exc)) from exc
        merged = self._merge_target_into_reconciliation(ch_by_type)
        target_total = sum(float(row.get('target_sum') or 0) for row in merged)
        self._record_billed_check(
            start, end, source_total, local_total,
            reconciliation=merged, target_cost_sum=target_total)
        LOG.info(
            'GCP ClickHouse target reconcile for %s %s..%s: '
            'bq_billed=%s mongo_cost=%s clickhouse_cost=%s types=%s',
            self.cloud_acc_id, start, end, source_total, local_total,
            target_total, len(merged))
        if getattr(self, 'recalculate', False) or not hasattr(
                self, '_last_bq_billed_sum'):
            return
        self._verify_clickhouse_target_cost(
            source_total, local_total, target_total)

    def recalculate_raw_expenses(self):
        """Mongo already has billed raw; recalculation only rebuilds clean/CH."""
        return

    def _clear_raw_checkpoint(self):
        self._mongo_checkpoints.delete_many(self._raw_checkpoint_filter())

    @staticmethod
    def _coerce_row_count(value):
        if value is None or isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            return max(0, int(value))
        if isinstance(value, str):
            try:
                return max(0, int(value))
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _coerce_cost_sum(value):
        if value is None or isinstance(value, bool):
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return 0.0
        return 0.0

    def _can_skip_first_raw_load(self, period_start):
        checkpoint = self._get_raw_checkpoint()
        if not checkpoint:
            return None
        checkpoint_start = checkpoint.get('period_start')
        if checkpoint_start != period_start:
            return None
        if self.mongo_raw.count_documents(
                {'cloud_account_id': self.cloud_acc_id}, limit=1) == 0:
            return None
        return checkpoint

    def _flush_raw_chunk(self, chunk, insert_only):
        if not chunk:
            return 0
        self._raw_insert_only = insert_only
        self.update_raw_records(chunk)
        return len(chunk)

    def _stream_usage_window(self, start, end, split_backdated=False):
        """Stream get_usage(start, end) into Mongo; return write stats.

        Progress % is BQ rows already consumed by merge / QueryJob.total_rows
        (no COUNT(*)). Unique output count can be much smaller than bq_total.

        split_backdated: wipe+insert rows whose start_date is in [start, end);
        merge rows with an older start_date (late export in a fresh
        partition) without wiping that historical day and without $set of a
        partition slice over a previously merged unique-key total.
        generate_clean rewrites ClickHouse for those backdated identities.
        """
        self._reset_backdated_clean_state()
        self._reset_stream_month_totals(start)
        self.log_import_phase(IMPORT_PHASE_BQ_READ)
        in_chunk = []
        back_chunk = []
        merged = 0
        written = 0
        written_cost_sum = 0.0
        bq_seen = 0
        wiped_window = False
        started = time.time()
        last_log_bq = 0
        last_log_time = started
        logged_python_phase = False
        logged_mongo_phase = False
        usage_job = self.cloud_adapter.get_usage(start, end)
        usage_rows = usage_job.result(page_size=BQ_PAGE_SIZE)
        bq_total = self._coerce_row_count(
            getattr(usage_rows, 'total_rows', None))
        if not bq_total:
            bq_total = self._coerce_row_count(
                getattr(usage_job, 'total_rows', None))
        if bq_total:
            LOG.info(
                'GCP raw load BQ total for %s: %s rows (from job)',
                self.cloud_acc_id, bq_total)
            self.log_import_phase(IMPORT_PHASE_MONGO_WRITE)
            logged_mongo_phase = True
            LOG.info(
                'GCP raw load progress for %s: merged=0 written=0 '
                'bq_seen=0 bq_total=%s pct=0 elapsed=0s',
                self.cloud_acc_id, bq_total)

        def _log_progress():
            pct = (
                int(100 * bq_seen / bq_total)
                if bq_total else None)
            if pct is not None:
                LOG.info(
                    'GCP raw load progress for %s: merged=%s written=%s '
                    'bq_seen=%s bq_total=%s pct=%s elapsed=%.0fs',
                    self.cloud_acc_id, merged, written, bq_seen, bq_total,
                    min(100, pct), time.time() - started)
            else:
                LOG.info(
                    'GCP raw load progress for %s: merged=%s written=%s '
                    'bq_seen=%s elapsed=%.0fs',
                    self.cloud_acc_id, merged, written, bq_seen,
                    time.time() - started)

        def _maybe_log_progress(force=False):
            nonlocal last_log_bq, last_log_time
            now = time.time()
            due_rows = bq_seen - last_log_bq >= PROGRESS_LOG_EVERY
            due_time = (
                now - last_log_time >= PROGRESS_LOG_EVERY_SEC
                and bq_seen > last_log_bq)
            if not force and not due_rows and not due_time:
                return
            _log_progress()
            last_log_bq = bq_seen
            last_log_time = now

        def _counting_rows(rows):
            nonlocal bq_seen
            for row in rows:
                bq_seen += 1
                _maybe_log_progress()
                yield row

        def _ensure_mongo_phase():
            nonlocal logged_mongo_phase
            if not logged_mongo_phase:
                self.log_import_phase(IMPORT_PHASE_MONGO_WRITE)
                logged_mongo_phase = True

        for r in self._iter_merged_billing_items(_counting_rows(usage_rows)):
            if not logged_python_phase:
                self.log_import_phase(IMPORT_PHASE_PYTHON_DESERIALIZE)
                logged_python_phase = True
            merged += 1
            written_cost_sum += float(r.get('cost') or 0)
            self._accumulate_stream_month_row(r)
            in_date_window = self._row_in_window(r, start, end)
            # Virtual rebuild (split_backdated=False) wipe+inserts the whole
            # invoice month, including usage_start before period_start.
            # Still write those on the insert path, but mark current-invoice
            # backdated days so generate_clean rewrites ClickHouse too —
            # otherwise Stage (Mongo) drifts from Target (CH) like Support.
            if not split_backdated or in_date_window:
                if split_backdated and not wiped_window:
                    self._clear_raw_for_window(start, end)
                    wiped_window = True
                if (not split_backdated and not in_date_window
                        and self._stream_row_in_current_month(r)):
                    self._note_backdated_row(r)
                in_chunk.append(r)
                if len(in_chunk) == WRITE_CHUNK_SIZE:
                    _ensure_mongo_phase()
                    written += self._flush_raw_chunk(in_chunk, insert_only=True)
                    in_chunk = []
                    _maybe_log_progress(force=True)
            else:
                self._note_backdated_row(r)
                back_chunk.append(r)
                if len(back_chunk) == WRITE_CHUNK_SIZE:
                    _ensure_mongo_phase()
                    written += self._flush_raw_chunk(
                        back_chunk, insert_only=False)
                    back_chunk = []
                    _maybe_log_progress(force=True)
        if in_chunk:
            _ensure_mongo_phase()
            written += self._flush_raw_chunk(in_chunk, insert_only=True)
        if back_chunk:
            _ensure_mongo_phase()
            written += self._flush_raw_chunk(back_chunk, insert_only=False)
        if bq_total:
            LOG.info(
                'GCP raw load finished for %s: merged=%s written=%s '
                'bq_seen=%s bq_total=%s pct=100 elapsed=%.0fs',
                self.cloud_acc_id, merged, written, bq_seen, bq_total,
                time.time() - started)
        else:
            LOG.info(
                'GCP raw load finished for %s: merged=%s written=%s '
                'bq_seen=%s elapsed=%.0fs',
                self.cloud_acc_id, merged, written, bq_seen,
                time.time() - started)
        return written, written_cost_sum

    def load_raw_data(self):
        start = self.period_start.replace(
            hour=0, minute=0, second=0, microsecond=0)
        # Exclusive end on the next UTC midnight so today's partition is
        # included in a single range query.
        end = (opttime.utcnow() + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        first_load = not self.cloud_acc.get('last_import_at')
        self._raw_insert_only = first_load
        if first_load:
            checkpoint = self._can_skip_first_raw_load(start)
            if checkpoint is not None:
                LOG.info(
                    'Skipping GCP raw load for %s: checkpoint written=%s '
                    'period=%s..%s (raw already complete, continue clean)',
                    self.cloud_acc_id, checkpoint.get('written'),
                    checkpoint.get('period_start'),
                    checkpoint.get('period_end'))
                self._reconcile_current_month()
                return
            LOG.info(
                'Loading GCP billing for %s from %s to %s '
                '(single BigQuery job for the whole period, write=insert_many)',
                self.cloud_acc_id, start, end)
            # Only wipe billing raw leftovers from a previous failed first
            # attempt. Do NOT delete resources: discovery may already have
            # created them, and generate_clean_records merges via
            # skip_existing / resource_hash (optscale_tracking_id).
            LOG.info(
                'First GCP load for %s: clearing leftover raw expenses '
                'before bulk-insert', self.cloud_acc_id)
            deleted_raw = self.mongo_raw.delete_many(
                {'cloud_account_id': self.cloud_acc_id})
            LOG.info(
                'First GCP load for %s: cleared %s leftover raw expense(s); '
                'will bulk-insert (resources left intact for discovery merge)',
                self.cloud_acc_id, deleted_raw.deleted_count)
            written, written_cost_sum = self._stream_usage_window(start, end)
            LOG.info(
                'GCP raw load streamed for %s: written_cost=%s; '
                'reconcile current month next',
                self.cloud_acc_id, written_cost_sum)
            self._reconcile_current_month()
            self._save_raw_checkpoint(
                start, end, written,
                bq_billed_sum=getattr(self, '_last_bq_billed_sum', 0.0),
                written_cost_sum=getattr(
                    self, '_last_written_cost_sum', written_cost_sum))
            return

        if self.cloud_adapter.is_virtual_billing_project:
            # Invoice-month CAs: usage_start can be in the previous month.
            # Wipe those invoice months and insert (do not $inc backdated
            # CUD rows — that is what broke Compute Engine reconcile).
            months = self.cloud_adapter.invoice_months_for_window(start, end)
            LOG.info(
                'GCP virtual invoice rebuild for %s: invoice_month=%s '
                'partitions %s..%s',
                self.cloud_acc_id, months, start, end)
            self._clear_raw_for_invoice_months(months)
            written, written_cost_sum = self._stream_usage_window(
                start, end, split_backdated=False)
            LOG.info(
                'GCP raw load streamed for %s: written_cost=%s; '
                'reconcile current month next',
                self.cloud_acc_id, written_cost_sum)
            self._reconcile_current_month()
            return

        LOG.info(
            'GCP incremental raw load for %s: partitions %s..%s → '
            'wipe in-window + merge backdated by export_time',
            self.cloud_acc_id, start, end)
        written, written_cost_sum = self._stream_usage_window(
            start, end, split_backdated=True)
        LOG.info(
            'GCP incremental streamed for %s: written_cost=%s; '
            'reconcile current month next',
            self.cloud_acc_id, written_cost_sum)
        self._reconcile_current_month()

    def update_cloud_import_time(self, ts):
        super().update_cloud_import_time(ts)
        self._clear_raw_checkpoint()
        warning = getattr(self, '_clickhouse_reconcile_warning', None)
        if warning:
            # Super clears last_import_attempt_error. Re-attach the CH
            # warning on the same timestamps so status stays completed.
            self.update_cloud_import_attempt(ts, warning)

    @staticmethod
    def _get_resource_type_and_name(expense):
        cost_type = expense.get('cost_type')
        sku = expense.get('sku', '')
        sku_lower = sku.lower()
        region = expense.get('region')
        service = expense.get('service')
        resource_hash = expense.get('resource_hash')
        r_name = None
        if cost_type == 'regular':
            if 'snapshot' in sku_lower:
                r_type = 'Snapshot'
            elif 'image' in sku_lower:
                r_type = 'Image'
            elif 'pd capacity' in sku_lower:
                r_type = 'Volume'
            elif service == 'Cloud Storage' and (
                    'storage' in sku_lower or resource_hash):
                r_type = 'Bucket'
            elif (
                    service in (None, '', 'Compute Engine')
                    and 'discount' not in sku_lower
                    and is_labeled_collapse_leftover_id(
                        expense.get('resource_id'))):
                # GCP numeric compute id, not SKU text: core, GPU, network
                # and IP on that VM are Instance. Billing sku.id and Cloud
                # SQL stay leftover / service.
                r_type = 'Instance'
            elif 'ip charge' in sku_lower:
                r_type = 'IP Address'
            else:
                r_type = f'{service}'
            r_name = f'{sku} {region}'
        elif cost_type == 'tax':
            r_type = 'Tax'
        elif cost_type == 'rounding_error':
            r_type = 'Rounding Error'
            r_name = f'{service} {r_type}'
        elif cost_type == 'adjustment':
            r_type = 'Adjustment'
        else:
            raise Exception(f'Unknown cost_type: {cost_type}')
        if not r_name:
            r_name = f'{sku} {r_type}'
        return r_type, r_name

    def _get_cloud_extras(self, info):
        res = defaultdict(dict)
        for k in ['cpu_count', 'flavor']:
            val = info.get(k)
            if val:
                res['meta'][k] = val
        return res

    def get_resource_info_from_expenses(self, expenses):
        expense = expenses[-1]
        if expense.get('service') == 'Compute Engine':
            expense = next(
                (row for row in expenses
                 if is_labeled_collapse_leftover_id(row.get('resource_id'))),
                expense)
        r_type, r_name = self._get_resource_type_and_name(
            expense)
        service = expense.get('service')
        region = self.cloud_adapter.fix_region(expense.get('region'))
        first_seen = opttime.utcnow()
        last_seen = opttime.utcfromtimestamp(0).replace()
        tags = {}
        system_tags = {}
        for e in expenses:
            start_date = e['start_date']
            if start_date and start_date < first_seen:
                first_seen = start_date
            end_date = e['end_date']
            if end_date and end_date > last_seen:
                last_seen = end_date
            tags.update(e.get('tags', {}))
            system_tags.update(e.get('system_tags', {}))
        if last_seen < first_seen:
            last_seen = first_seen
        # Prefer Terraform/user label "name", then detailed-export resource
        # short name, over SKU+region for display. Identity unchanged.
        collapse = gcp_collapse_identity(tags)
        if not collapse:
            for e in expenses:
                collapse = gcp_detailed_collapse_identity(e)
                if collapse:
                    break
        if not collapse:
            rid = str(expense.get('resource_id') or '')
            collapse = collapsed_identity_from_resource_id(rid)
        if collapse:
            r_type = collapse['resource_type']
            r_name = collapse['name']
            tags.update(collapse.get('tag_overrides') or {})
        else:
            tag_name = (tags.get('name') or '').strip()
            if tag_name:
                r_name = tag_name
            else:
                for e in expenses:
                    short = self._resource_short_name(e)
                    if short:
                        r_name = short
                        break
        info = {
            'name': r_name,
            'type': r_type,
            'region': region,
            'service_name': service,
            'tags': tags,
            'first_seen': int(first_seen.timestamp()),
            'last_seen': int(last_seen.timestamp())
        }
        if FLAVOR_SYSTEM_TAG in system_tags:
            info['flavor'] = system_tags[FLAVOR_SYSTEM_TAG]
        if CORES_SYSTEM_TAG in system_tags:
            info['cpu_count'] = system_tags[CORES_SYSTEM_TAG]
        LOG.debug('Detected resource info: %s', info)
        return info

    def save_clean_expenses(self, cloud_account_id, chunk,
                            unique_id_field='resource_id'):
        collapsed = collapse_expense_chunk(chunk)
        if unique_id_field == 'resource_id':
            super().save_clean_expenses(
                cloud_account_id, collapsed, unique_id_field=unique_id_field)
            self._clear_stale_clickhouse_for_rebilled_names(collapsed)
            return
        remapped = {}
        leftover = {}
        original_ids = set(chunk)
        for r_id, expenses in collapsed.items():
            if r_id in original_ids:
                leftover[r_id] = expenses
            else:
                remapped[r_id] = expenses
        if remapped:
            super().save_clean_expenses(
                cloud_account_id, remapped, unique_id_field='resource_id')
            self._clear_stale_clickhouse_for_rebilled_names(remapped)
        if leftover:
            super().save_clean_expenses(
                cloud_account_id, leftover, unique_id_field=unique_id_field)
            self._clear_stale_clickhouse_for_rebilled_names(leftover)

    def _clear_stale_clickhouse_for_rebilled_names(self, chunk):
        """Negate predecessor CH days when this chunk re-bills the same name.

        Mongo is unchanged. Only calendar days present in the chunk are
        considered, so history outside the increment stays (87e93e7).
        Another cloud_resource_id that still has Mongo raw on the same
        name+day is left alone (concurrent billing).
        """
        billed = defaultdict(set)
        for crid, expenses in (chunk or {}).items():
            crid_s = str(crid)
            for expense in expenses or []:
                name = expense.get('resource_name')
                if not name:
                    continue
                day = self._usage_day(expense.get('start_date'))
                if day is None:
                    continue
                billed[(str(name), day)].add(crid_s)
        if not billed:
            return
        pairs = self._billing_name_predecessor_day_pairs(billed)
        n = self._negate_clickhouse_resource_days(pairs)
        if n:
            LOG.info(
                'Negated %s stale ClickHouse day row(s) after billing name '
                'moved to a new resource_id for %s',
                n, self.cloud_acc_id)

    def _billing_name_predecessor_day_pairs(self, billed):
        """Build (mongo_id, day) pairs for names re-billed in this chunk.

        Batched Mongo lookups: one pass for candidate crids, one for days
        still billed on those crids, one for mongo resource _ids.
        """
        mongo_raw = getattr(self, 'mongo_raw', None)
        mongo_resources = getattr(self, 'mongo_resources', None)
        # PyMongo Collection rejects bool(); compare to None explicitly.
        if not billed or mongo_raw is None or mongo_resources is None:
            return []
        names = sorted({name for name, _ in billed})
        days = sorted({day for _, day in billed})
        if not names or not days:
            return []
        day_min = self._clickhouse_lookup_date(days[0])
        day_max = self._clickhouse_lookup_date(days[-1]) + timedelta(days=1)

        name_to_crids = defaultdict(set)
        for doc in mongo_raw.aggregate([
            {'$match': {
                'cloud_account_id': self.cloud_acc_id,
                'resource_name': {'$in': names},
            }},
            {'$group': {
                '_id': '$resource_name',
                'crids': {'$addToSet': '$resource_id'},
            }},
        ], allowDiskUse=True):
            name = str((doc or {}).get('_id') or '')
            for crid in (doc or {}).get('crids') or []:
                if crid:
                    name_to_crids[name].add(str(crid))
        for doc in mongo_resources.find({
            'cloud_account_id': self.cloud_acc_id,
            'name': {'$in': names},
        }, {'name': 1, 'cloud_resource_id': 1}):
            name = str((doc or {}).get('name') or '')
            crid = (doc or {}).get('cloud_resource_id')
            if name and crid:
                name_to_crids[name].add(str(crid))

        candidate_crids = set()
        for (name, day), keepers in billed.items():
            for crid in name_to_crids.get(name, ()):
                if crid not in keepers:
                    candidate_crids.add(crid)
        if not candidate_crids:
            return []

        billed_on_day = set()
        for doc in mongo_raw.aggregate([
            {'$match': {
                'cloud_account_id': self.cloud_acc_id,
                'resource_id': {'$in': list(candidate_crids)},
                'start_date': {'$gte': day_min, '$lt': day_max},
            }},
            {'$project': {
                'resource_id': 1,
                'day': {
                    '$dateToString': {
                        'format': '%Y-%m-%d',
                        'date': '$start_date',
                    }
                },
            }},
            {'$group': {
                '_id': {'rid': '$resource_id', 'day': '$day'},
            }},
        ], allowDiskUse=True):
            key = (doc or {}).get('_id') or {}
            rid = key.get('rid')
            day_s = key.get('day')
            if not rid or not day_s:
                continue
            try:
                y, m, d = (int(x) for x in str(day_s).split('-', 2))
            except ValueError:
                continue
            billed_on_day.add((str(rid), datetime(y, m, d)))

        crid_to_mongo_ids = defaultdict(list)
        for doc in mongo_resources.find({
            'cloud_account_id': self.cloud_acc_id,
            'cloud_resource_id': {'$in': list(candidate_crids)},
        }, {'_id': 1, 'cloud_resource_id': 1}):
            crid = (doc or {}).get('cloud_resource_id')
            mongo_id = (doc or {}).get('_id')
            if crid and mongo_id:
                crid_to_mongo_ids[str(crid)].append(mongo_id)

        pairs = []
        seen = set()
        for (name, day), keepers in billed.items():
            day_n = self._clickhouse_lookup_date(day)
            for crid in name_to_crids.get(name, ()):
                if crid in keepers:
                    continue
                if (crid, day_n) in billed_on_day:
                    continue
                for mongo_id in crid_to_mongo_ids.get(crid, ()):
                    key = (mongo_id, day_n)
                    if key in seen:
                        continue
                    seen.add(key)
                    pairs.append(key)
        return pairs

    def _billing_name_predecessor_mongo_ids(
            self, resource_name, keeper_crids, day):
        """Mongo _ids that used to bill resource_name but not on this day."""
        billed = {(str(resource_name), day): set(keeper_crids or [])}
        return [mongo_id for mongo_id, _ in
                self._billing_name_predecessor_day_pairs(billed)]

    def _negate_clickhouse_resource_days(self, resource_days):
        """Zero ClickHouse nets for specific (mongo_resource_id, day) only.

        Unlike _negate_clickhouse_resource_ids / _reassign_clickhouse_expenses,
        residual days on the same id (still billed in Mongo) are not touched.
        """
        if not resource_days or getattr(self, 'clickhouse_cl', None) is None:
            return 0
        by_id = defaultdict(set)
        for resource_id, day in resource_days:
            if not resource_id or day is None:
                continue
            by_id[resource_id].add(self._clickhouse_lookup_date(day))
        if not by_id:
            return 0
        payload = []
        # One CH query for all touched resource ids in this chunk.
        all_ids = list(by_id)
        all_days = sorted({d for days in by_id.values() for d in days})
        rows = self.clickhouse_cl.query("""
            SELECT resource_id, date, invoice_month, sum(cost * sign)
            FROM expenses FINAL
            WHERE cloud_account_id = %(cloud_account_id)s
              AND resource_id IN %(resource_ids)s
              AND date IN %(dates)s
            GROUP BY resource_id, date, invoice_month
            HAVING abs(sum(cost * sign)) > 0.000001
        """, parameters={
            'cloud_account_id': self.cloud_acc_id,
            'resource_ids': all_ids,
            'dates': all_days,
        }).result_rows
        for rid, date, invoice_month, net in rows or []:
            day_n = self._clickhouse_lookup_date(date)
            if day_n not in by_id.get(rid, ()):
                continue
            net = float(net or 0)
            if abs(net) <= 0.000001:
                continue
            payload.append([
                self.cloud_acc_id,
                rid,
                date,
                abs(net),
                -1 if net > 0 else 1,
                str(invoice_month or ''),
            ])
        if payload:
            self.update_clickhouse_expenses(payload, [
                'cloud_account_id', 'resource_id', 'date', 'cost', 'sign',
                'invoice_month'])
        return len(payload)

    def _rewrite_serverless_dataproc_raw_ids(self):
        """Point leftover dataproc/<uuid> raw rows at the stable DAG id.

        Mongo 3.6 rejects aggregation-pipeline updates (u must be an object).
        """
        filt = serverless_dataproc_raw_rewrite_filter(self.cloud_acc_id)
        dags = self.mongo_raw.distinct(
            SERVERLESS_DATAPROC_DAG_RAW_FIELD, filt) or []
        modified = 0
        for query, update in serverless_dataproc_raw_rewrite_updates(
                self.cloud_acc_id, dags):
            result = self.mongo_raw.update_many(query, update)
            modified += int(getattr(result, 'modified_count', 0) or 0)
        if modified:
            LOG.info(
                'Rewrote %s serverless Dataproc raw resource_id(s) for %s',
                modified, self.cloud_acc_id)

    def _rewrite_labeled_collapse_raw_ids(self):
        """Point leftover Composer/GKE raw rows at the stable cluster id."""
        rewrites = (
            (
                PLAIN_DATAPROC_CLUSTER_UUID_FIELD,
                dataproc_collapse_identity,
                (PLAIN_DATAPROC_BATCH_UUID_FIELD, PLAIN_DATAPROC_BATCH_ID_FIELD),
                'dataproc',
            ),
            (
                PLAIN_COMPOSER_UUID_FIELD,
                composer_collapse_identity,
                (PLAIN_DATAPROC_CLUSTER_UUID_FIELD,),
                'composer',
            ),
            (
                PLAIN_GKE_NAME_FIELD,
                gke_collapse_identity,
                (PLAIN_DATAPROC_CLUSTER_UUID_FIELD, PLAIN_COMPOSER_UUID_FIELD),
                'gke',
            ),
        )
        for tag_field, ident_fn, exclude_fields, skip_prefix in rewrites:
            filt = labeled_collapse_raw_filter(
                self.cloud_acc_id, tag_field, exclude_fields, skip_prefix)
            values = self.mongo_raw.distinct(tag_field, filt) or []
            modified = 0
            for query, update in labeled_collapse_raw_rewrite_updates(
                    self.cloud_acc_id, tag_field, values, ident_fn,
                    exclude_fields, skip_prefix):
                result = self.mongo_raw.update_many(query, update)
                modified += int(getattr(result, 'modified_count', 0) or 0)
            if modified:
                LOG.info(
                    'Rewrote %s %s raw resource_id(s) for %s',
                    modified, skip_prefix, self.cloud_acc_id)

    def _note_detailed_collapse_mapping(self, old_id, keeper):
        mapping = getattr(self, '_detailed_collapse_old_to_keepers', None)
        if mapping is None:
            mapping = {}
            self._detailed_collapse_old_to_keepers = mapping
        old_id = str(old_id or '')
        keeper = str(keeper or '')
        if not old_id or not keeper or old_id == keeper:
            return
        mapping.setdefault(old_id, set()).add(keeper)

    def _rewrite_detailed_collapse_raw_ids(self):
        """Point SQL/Run/Functions/backup raw rows at the billing keeper.

        One Cloud Run / Functions SKU often bills many services. Rewrite
        per identity (resource_name / global_name), and record old_id →
        keepers so Mongo rekey can fold only unique leftovers.
        """
        self._detailed_collapse_old_to_keepers = {}
        passes = (
            (r'//(?:sqladmin|cloudsql)\.googleapis\.com/', 'cloudsql'),
            (r'//run\.googleapis\.com/', 'cloudrun'),
            (r'//cloudfunctions\.googleapis\.com/', 'function'),
        )
        for gname_re, skip_prefix in passes:
            filt = {
                'cloud_account_id': self.cloud_acc_id,
                'resource_global_name': {'$regex': gname_re},
                'resource_id': {
                    '$regex': '^(?!%s/)' % skip_prefix,
                },
            }
            names = self.mongo_raw.distinct('resource_name', filt) or []
            modified = 0
            for name in names:
                sample = self.mongo_raw.find_one(
                    dict(filt, resource_name=name),
                    {'resource_global_name': 1, 'resource_name': 1,
                     'tags': 1, 'sku': 1})
                ident = gcp_detailed_collapse_identity(sample or {})
                if not ident:
                    continue
                keeper = ident['cloud_resource_id']
                name_filt = dict(filt, resource_name=name)
                for old_id in self.mongo_raw.distinct(
                        'resource_id', name_filt) or []:
                    self._note_detailed_collapse_mapping(old_id, keeper)
                result = self.mongo_raw.update_many(
                    name_filt, {'$set': {'resource_id': keeper}})
                modified += int(getattr(result, 'modified_count', 0) or 0)
            if modified:
                LOG.info(
                    'Rewrote %s %s raw resource_id(s) for %s',
                    modified, skip_prefix, self.cloud_acc_id)
        backup_filt = {
            'cloud_account_id': self.cloud_acc_id,
            'resource_id': {'$regex': r'^(?!cloudsql/)'},
            'resource_name': {'$regex': r'-backup-\d+$'},
            '$or': [
                {'sku': {'$regex': 'snapshot', '$options': 'i'}},
                {'resource_global_name': {'$regex': '/snapshots/'}},
            ],
        }
        names = self.mongo_raw.distinct('resource_name', backup_filt) or []
        by_keeper = {}
        for name in names:
            ident = gcp_detailed_collapse_identity({
                'resource_name': name,
                'sku': 'Storage PD Snapshot',
                'resource_global_name': (
                    '//compute.googleapis.com/x/snapshots/1'),
            })
            if ident:
                by_keeper.setdefault(
                    ident['cloud_resource_id'], []).append(name)
        modified = 0
        for keeper, keeper_names in by_keeper.items():
            name_filt = dict(backup_filt)
            name_filt['resource_name'] = {'$in': keeper_names}
            for old_id in self.mongo_raw.distinct(
                    'resource_id', name_filt) or []:
                self._note_detailed_collapse_mapping(old_id, keeper)
            result = self.mongo_raw.update_many(
                name_filt, {'$set': {'resource_id': keeper}})
            modified += int(getattr(result, 'modified_count', 0) or 0)
        if modified:
            LOG.info(
                'Rewrote %s Cloud SQL backup raw resource_id(s) for %s',
                modified, self.cloud_acc_id)
        ip_filt = {
            'cloud_account_id': self.cloud_acc_id,
            'resource_name': {'$regex': 'serverless-ipv4'},
            'resource_id': {
                '$regex': r'^(?!(cloudrun|function)/)',
            },
        }
        names = self.mongo_raw.distinct('resource_name', ip_filt) or []
        modified = 0
        for name in names:
            sample = self.mongo_raw.find_one(
                dict(ip_filt, resource_name=name),
                {'resource_global_name': 1, 'resource_name': 1,
                 'tags': 1, 'sku': 1})
            ident = gcp_detailed_collapse_identity(sample or {})
            if not ident:
                continue
            keeper = ident['cloud_resource_id']
            name_filt = dict(ip_filt, resource_name=name)
            for old_id in self.mongo_raw.distinct(
                    'resource_id', name_filt) or []:
                self._note_detailed_collapse_mapping(old_id, keeper)
            result = self.mongo_raw.update_many(
                name_filt, {'$set': {'resource_id': keeper}})
            modified += int(getattr(result, 'modified_count', 0) or 0)
        if modified:
            LOG.info(
                'Rewrote %s labeled serverless-ipv4 raw resource_id(s) for %s',
                modified, self.cloud_acc_id)

    def _rekey_detailed_collapse_leftovers(self):
        """Fold unique SQL/Run leftovers; retire mixed SKU leftovers.

        Live data: one Cloud Run SKU bills many services. Folding that
        leftover onto one keeper would steal cost. Unique mappings (SQL
        instance name, one-service SKU, backup snapshot id) merge. Mixed
        SKU leftovers are soft-deleted and their ClickHouse is negated so
        save_clean can rebuild keepers from rewritten raw.
        """
        mapping = getattr(self, '_detailed_collapse_old_to_keepers', None) or {}
        projection = {
            '_id': 1, 'cloud_resource_id': 1, 'cloud_resource_hash': 1,
            'first_seen': 1, 'last_seen': 1, 'cluster_id': 1, 'tags': 1,
            'resource_type': 1, 'name': 1, 'deleted_at': 1,
        }
        leftover_ids = list(mapping.keys())
        leftovers = []
        if leftover_ids:
            leftovers = list(self.mongo_resources.find({
                'cloud_account_id': self.cloud_acc_id,
                'deleted_at': 0,
                'cloud_resource_id': {'$in': leftover_ids},
            }, projection))
        seen = {doc['_id'] for doc in leftovers if doc.get('_id')}
        extra = list(self.mongo_resources.find({
            'cloud_account_id': self.cloud_acc_id,
            'deleted_at': 0,
            'resource_type': {'$in': ['Snapshot', 'Cloud SQL']},
            'cloud_resource_id': {'$regex': r'^(?!cloudsql/)'},
        }, projection))
        groups = {}
        mixed = []
        for doc in leftovers:
            crid = str(doc.get('cloud_resource_id') or '')
            keepers = mapping.get(crid) or set()
            if len(keepers) != 1:
                if len(keepers) > 1:
                    mixed.append(doc)
                continue
            keeper = next(iter(keepers))
            ident = collapsed_identity_from_resource_id(keeper)
            if not ident or crid == keeper:
                continue
            if not collapse_leftover_may_fold(
                    crid, ident, None,
                    mongo_resource_type=doc.get('resource_type')):
                continue
            groups.setdefault(keeper, {
                'ident': ident, 'members': []})['members'].append(doc)
        for doc in extra:
            if doc.get('_id') in seen:
                continue
            crid = str(doc.get('cloud_resource_id') or '')
            ident = cloudsql_instance_leftover_identity(
                crid, doc.get('resource_type'))
            if not ident:
                ident = gcp_row_collapse_identity({
                    'name': doc.get('name'),
                    'tags': doc.get('tags') or {},
                    'sku': 'Storage PD Snapshot',
                    'resource_global_name': (
                        '//compute.googleapis.com/x/snapshots/1'),
                })
            if not ident:
                continue
            keeper = ident['cloud_resource_id']
            if crid == keeper:
                continue
            groups.setdefault(keeper, {
                'ident': ident, 'members': []})['members'].append(doc)
        merged = 0
        for keeper_id, group in groups.items():
            ident = group['ident']
            members = group['members']
            existing = list(self.mongo_resources.find({
                'cloud_account_id': self.cloud_acc_id,
                'deleted_at': 0,
                'cloud_resource_id': keeper_id,
            }, projection))
            merged += self._merge_collapsed_identity_group(
                existing + members, ident)
        retired = 0
        mixed_ids = [doc['_id'] for doc in mixed if doc.get('_id')]
        if mixed_ids:
            now = opttime.utcnow_timestamp()
            result = self.mongo_resources.update_many(
                {'_id': {'$in': mixed_ids}, 'deleted_at': 0},
                {'$set': {'deleted_at': now}})
            retired = int(getattr(result, 'modified_count', 0) or 0)
            self._negate_clickhouse_resource_ids(mixed_ids)
        if merged:
            LOG.info(
                'Collapsed %s detailed SQL/Run leftover resource(s) for %s',
                merged, self.cloud_acc_id)
        if retired:
            LOG.info(
                'Retired %s mixed Cloud Run/Functions SKU leftover(s) for %s',
                retired, self.cloud_acc_id)

    def _remember_collapsed_member_cluster_ids(self, docs):
        cluster_ids = getattr(self, '_collapsed_member_cluster_ids', None)
        if cluster_ids is None:
            cluster_ids = []
            self._collapsed_member_cluster_ids = cluster_ids
        for doc in docs or []:
            cid = doc.get('cluster_id')
            if cid and cid not in cluster_ids:
                cluster_ids.append(cid)

    def _merge_collapsed_identity_group(self, docs, ident):
        """Keep one live doc per collapsed identity; move ClickHouse cost.

        OptResourceUnique blocks many live rows with the same cloud_resource_id,
        so extras are soft-deleted instead of bulk-rekeyed.
        """
        ident = collapsed_keeper_set(ident)
        if not ident:
            return 0
        by_id = {doc['_id']: doc for doc in docs if doc.get('_id')}
        docs = [
            doc for doc in by_id.values()
            if not is_foreign_collapsed_identity(
                doc.get('cloud_resource_id'), ident)
        ]
        if not docs:
            return 0
        already = [
            doc for doc in docs
            if doc.get('cloud_resource_id') == ident['cloud_resource_id']]
        keeper = pick_collapsed_duplicate_keeper(already or docs)
        extra_ids = [
            doc['_id'] for doc in docs if doc['_id'] != keeper['_id']]
        extras = [doc for doc in docs if doc['_id'] != keeper['_id']]
        if not extra_ids:
            return 0
        self._remember_collapsed_member_cluster_ids(extras)
        first_seen = keeper.get('first_seen')
        last_seen = keeper.get('last_seen')
        for doc in docs:
            fs, ls = doc.get('first_seen'), doc.get('last_seen')
            if fs is not None and (first_seen is None or fs < first_seen):
                first_seen = fs
            if ls is not None and (last_seen is None or ls > last_seen):
                last_seen = ls
        keeper_set = dict(ident)
        if first_seen is not None:
            keeper_set['first_seen'] = first_seen
        if last_seen is not None:
            keeper_set['last_seen'] = last_seen
        self.mongo_resources.update_one(
            {'_id': keeper['_id']},
            {'$set': keeper_set, '$unset': {'cloud_resource_hash': ''}})
        now = opttime.utcnow_timestamp()
        chunk = 2000
        for i in range(0, len(extra_ids), chunk):
            part = extra_ids[i:i + chunk]
            self.mongo_resources.update_many(
                {'_id': {'$in': part}, 'deleted_at': 0},
                {'$set': {'deleted_at': now}})
            self._reassign_clickhouse_expenses(part, keeper['_id'])
        return 1 + len(extra_ids)

    def _raw_row_for_collapse_leftover(self, cloud_resource_id):
        find_one = getattr(self.mongo_raw, 'find_one', None)
        if not callable(find_one):
            return None
        return find_one({
            'cloud_account_id': self.cloud_acc_id,
            'resource_id': cloud_resource_id,
        }, {'tags': 1})

    def _merge_stale_serverless_dataproc_group(self, docs, dag):
        return self._merge_collapsed_identity_group(
            docs, stale_serverless_dataproc_keeper_set(dag))

    def _rekey_stale_serverless_dataproc_resources(self):
        """Collapse leftover srvls-batch dataproc/<uuid> Mongo docs per DAG.

        Raw rewrite only changes expenses. Data Sources counts Mongo docs, so
        each DAG keeps one resource and extras are retired with their cost.
        """
        filt = stale_serverless_dataproc_resource_filter(self.cloud_acc_id)
        self._stale_serverless_cluster_ids = [
            cid for cid in (
                self.mongo_resources.distinct('cluster_id', filt) or [])
            if cid
        ]
        projection = {
            '_id': 1, 'cloud_resource_id': 1, 'cloud_resource_hash': 1,
            'first_seen': 1, 'last_seen': 1, 'cluster_id': 1,
        }
        dags = []
        for field in (ENCODED_AIRFLOW_DAG_ID_FIELD, PLAIN_AIRFLOW_DAG_ID_FIELD):
            dags.extend(self.mongo_resources.distinct(field, filt) or [])
        seen = set()
        merged = 0
        for raw in dags:
            if raw in (None, ''):
                continue
            dag = str(raw).strip()
            if not dag or dag in seen:
                continue
            seen.add(dag)
            members = list(self.mongo_resources.find({
                **filt,
                '$or': [
                    {ENCODED_AIRFLOW_DAG_ID_FIELD: raw},
                    {PLAIN_AIRFLOW_DAG_ID_FIELD: raw},
                    {ENCODED_AIRFLOW_DAG_ID_FIELD: dag},
                    {PLAIN_AIRFLOW_DAG_ID_FIELD: dag},
                ],
            }, projection))
            ident = stale_serverless_dataproc_keeper_set(dag)
            existing = list(self.mongo_resources.find({
                'cloud_account_id': self.cloud_acc_id,
                'deleted_at': 0,
                'cloud_resource_id': ident['cloud_resource_id'],
            }, projection))
            merged += self._merge_stale_serverless_dataproc_group(
                existing + members, dag)
        leftovers = list(self.mongo_resources.find(filt, projection))
        if leftovers:
            ident = stale_serverless_dataproc_keeper_set('')
            existing = list(self.mongo_resources.find({
                'cloud_account_id': self.cloud_acc_id,
                'deleted_at': 0,
                'cloud_resource_id': ident['cloud_resource_id'],
            }, projection))
            merged += self._merge_stale_serverless_dataproc_group(
                existing + leftovers, '')
        if merged:
            LOG.info(
                'Collapsed %s stale serverless Dataproc resource(s) for %s',
                merged, self.cloud_acc_id)

    def _rekey_collapsed_labeled_resources(self):
        """Fold Dataproc/Composer/GKE labeled Mongo members onto one keeper.

        Discovery Instances/Volumes keep numeric ids; skip_existing does not
        rewrite them. New billing already keys dataproc/<uuid>, so leftover
        ClickHouse on those members double-counts until they are merged.
        Priority matches billing: Dataproc, then Composer, then GKE.
        """
        projection = {
            '_id': 1, 'cloud_resource_id': 1, 'cloud_resource_hash': 1,
            'first_seen': 1, 'last_seen': 1, 'cluster_id': 1, 'tags': 1,
            'resource_type': 1,
        }
        passes = (
            (
                'Dataproc',
                ENCODED_DATAPROC_CLUSTER_UUID_FIELD,
                PLAIN_DATAPROC_CLUSTER_UUID_FIELD,
                dataproc_collapse_identity,
            ),
            (
                'Composer',
                ENCODED_COMPOSER_UUID_FIELD,
                PLAIN_COMPOSER_UUID_FIELD,
                composer_collapse_identity,
            ),
            (
                'GKE',
                ENCODED_GKE_NAME_FIELD,
                PLAIN_GKE_NAME_FIELD,
                gke_collapse_identity,
            ),
        )
        for kind, encoded_field, plain_field, ident_fn in passes:
            filt = collapsed_labeled_member_match(
                self.cloud_acc_id, encoded_field, plain_field)
            values = []
            for field in (encoded_field, plain_field):
                values.extend(
                    self.mongo_resources.distinct(field, filt) or [])
            seen = set()
            merged = 0
            for raw in values:
                if raw in (None, ''):
                    continue
                value_key = str(raw).strip()
                if not value_key or value_key in seen:
                    continue
                seen.add(value_key)
                ident = ident_fn(raw)
                if not ident or ident.get('resource_type') != kind:
                    continue
                members = list(self.mongo_resources.find({
                    **filt,
                    '$or': [
                        {encoded_field: raw},
                        {plain_field: raw},
                        {encoded_field: value_key},
                        {plain_field: value_key},
                    ],
                }, projection))
                matched = []
                for doc in members:
                    actual = gcp_collapse_identity(doc.get('tags') or {})
                    if (not actual or actual.get('cloud_resource_id')
                            != ident['cloud_resource_id']):
                        continue
                    crid = doc.get('cloud_resource_id')
                    # Billing sku.id leftovers belong to
                    # _rekey_collapsed_sku_leftovers: that pass rewrites every
                    # raw row on the SKU (including unlabeled) before folding.
                    # Folding them here leaves unlabeled raw on the SKU, and
                    # _restore_miscollapsed_billing_sku_resources undeletes.
                    if is_gcp_billing_sku_id(crid):
                        continue
                    if not collapse_leftover_may_fold(
                            crid, ident,
                            None if crid == ident['cloud_resource_id']
                            or is_labeled_collapse_leftover_id(crid)
                            else self._raw_row_for_collapse_leftover(crid),
                            mongo_resource_type=doc.get('resource_type')):
                        continue
                    matched.append(doc)
                uncollapsed = [
                    doc for doc in matched
                    if doc.get('cloud_resource_id') != ident[
                        'cloud_resource_id']
                ]
                if not uncollapsed:
                    continue
                existing = list(self.mongo_resources.find({
                    'cloud_account_id': self.cloud_acc_id,
                    'deleted_at': 0,
                    'cloud_resource_id': ident['cloud_resource_id'],
                }, projection))
                merged += self._merge_collapsed_identity_group(
                    existing + matched, ident)
            if merged:
                LOG.info(
                    'Collapsed %s labeled %s resource(s) for %s',
                    merged, kind, self.cloud_acc_id)

    def _rekey_collapsed_sku_leftovers(self):
        """Fold live billing sku.id docs whose tags already name a keeper.

        Composer network SKUs often keep unlabeled leftover raw rows after
        tagged rows were rewritten, so labeled rekey would skip them. Family
        types (Composer / Dataproc / GKE) still fold; Instance SKUs need
        tagged raw or no remaining SKU-keyed raw (cf57077).
        """
        projection = {
            '_id': 1, 'cloud_resource_id': 1, 'cloud_resource_hash': 1,
            'first_seen': 1, 'last_seen': 1, 'cluster_id': 1, 'tags': 1,
            'resource_type': 1, 'deleted_at': 1,
        }
        leftovers = list(self.mongo_resources.find({
            'cloud_account_id': self.cloud_acc_id,
            'cloud_resource_id': {'$regex': GCP_BILLING_SKU_ID_REGEX},
        }, projection))
        groups = {}
        for doc in leftovers:
            ident = gcp_collapse_identity(doc.get('tags') or {})
            if not ident:
                continue
            crid = doc.get('cloud_resource_id')
            if not collapse_leftover_may_fold(
                    crid, ident,
                    self._raw_row_for_collapse_leftover(crid),
                    mongo_resource_type=doc.get('resource_type')):
                continue
            bucket = groups.setdefault(ident['cloud_resource_id'], {
                'ident': ident, 'members': [], 'deleted': []})
            if doc.get('deleted_at'):
                bucket['deleted'].append(doc)
            else:
                bucket['members'].append(doc)
        merged = 0
        for keeper_id, group in groups.items():
            ident = group['ident']
            members = group['members']
            # Rewrite unlabeled SKU raw even when leftover Mongo is already
            # deleted. Otherwise the next incremental insert (sku.id) would
            # recreate the leftover in save_expenses.
            for doc in members + group['deleted']:
                self.mongo_raw.update_many(
                    {
                        'cloud_account_id': self.cloud_acc_id,
                        'resource_id': doc.get('cloud_resource_id'),
                    },
                    {'$set': {'resource_id': keeper_id}},
                )
            if not members:
                continue
            existing = list(self.mongo_resources.find({
                'cloud_account_id': self.cloud_acc_id,
                'deleted_at': 0,
                'cloud_resource_id': keeper_id,
            }, projection))
            merged += self._merge_collapsed_identity_group(
                existing + members, ident)
        if merged:
            LOG.info(
                'Collapsed %s billing SKU leftover resource(s) for %s',
                merged, self.cloud_acc_id)

    def _rekey_unlabeled_gke_pvc_resources(self):
        """Fold leftover unlabeled GKE PVC Volume docs onto the GKE keeper."""
        cluster = self._unique_gke_cluster(refresh=True)
        ident = gke_collapse_identity(cluster)
        if not ident:
            return
        projection = {
            '_id': 1, 'cloud_resource_id': 1, 'cloud_resource_hash': 1,
            'first_seen': 1, 'last_seen': 1, 'cluster_id': 1,
        }
        members = list(self.mongo_resources.find(
            unlabeled_gke_pvc_resource_filter(self.cloud_acc_id), projection))
        if not members:
            return
        existing = list(self.mongo_resources.find({
            'cloud_account_id': self.cloud_acc_id,
            'deleted_at': 0,
            'cloud_resource_id': ident['cloud_resource_id'],
        }, projection))
        merged = self._merge_collapsed_identity_group(
            existing + members, ident)
        if merged:
            LOG.info(
                'Collapsed %s unlabeled GKE PVC resource(s) for %s',
                merged, self.cloud_acc_id)

    def _retire_stale_serverless_dataproc_resources(self):
        """Soft-delete leftover srvls-batch dataproc/<uuid> Mongo docs.

        Classic dataproc/<uuid> clusters are not named srvls-batch-* and stay.
        Cluster parents with no remaining members are retired with them.
        """
        now = opttime.utcnow_timestamp()
        filt = stale_serverless_dataproc_resource_filter(self.cloud_acc_id)
        cluster_ids = [
            cid for cid in (self.mongo_resources.distinct('cluster_id', filt) or [])
            if cid
        ]
        for cid in getattr(self, '_stale_serverless_cluster_ids', []) or []:
            if cid and cid not in cluster_ids:
                cluster_ids.append(cid)
        for cid in getattr(self, '_collapsed_member_cluster_ids', []) or []:
            if cid and cid not in cluster_ids:
                cluster_ids.append(cid)
        result = self.mongo_resources.update_many(
            filt, {'$set': {'deleted_at': now}})
        retired = int(getattr(result, 'modified_count', 0) or 0)
        if retired:
            LOG.info(
                'Retired %s ephemeral Dataproc serverless resource(s) for %s',
                retired, self.cloud_acc_id)
        if not cluster_ids:
            return
        remaining = set()
        chunk = 5000
        for i in range(0, len(cluster_ids), chunk):
            remaining.update(self.mongo_resources.distinct('cluster_id', {
                'cluster_id': {'$in': cluster_ids[i:i + chunk]},
                'deleted_at': 0,
            }) or [])
        orphans = [cid for cid in cluster_ids if cid not in remaining]
        if not orphans:
            return
        parents = 0
        for i in range(0, len(orphans), chunk):
            parent_result = self.mongo_resources.update_many(
                {
                    '_id': {'$in': orphans[i:i + chunk]},
                    'cluster_type_id': {'$exists': True, '$nin': [None, '']},
                    'deleted_at': 0,
                },
                {'$set': {'deleted_at': now}},
            )
            parents += int(getattr(parent_result, 'modified_count', 0) or 0)
        if parents:
            LOG.info(
                'Retired %s orphan collapsed GCP cluster parent(s) for %s',
                parents, self.cloud_acc_id)

    def _reassign_clickhouse_expenses(self, extra_ids, keeper_id):
        """Move extra Mongo _id expenses onto the kept collapsed resource.

        Cost is not in CollapsingMergeTree ORDER BY. Copying every unmerged
        +1 (including cost=0 placeholders) onto the keeper makes FINAL
        undefined. Use FINAL nets: copy a day only when the keeper has none;
        always zero the extra. Same-id twins that already have the day on
        the live keeper are not doubled (8366112).
        """
        if not extra_ids or not keeper_id or not getattr(
                self, 'clickhouse_cl', None):
            return
        ids = list(extra_ids) + [keeper_id]
        rows = self.clickhouse_cl.query("""
            SELECT resource_id, date, invoice_month, sum(cost * sign)
            FROM expenses FINAL
            WHERE cloud_account_id = %(cloud_account_id)s
              AND resource_id IN %(resource_ids)s
            GROUP BY resource_id, date, invoice_month
            HAVING abs(sum(cost * sign)) > 0.000001
        """, parameters={
            'cloud_account_id': self.cloud_acc_id,
            'resource_ids': ids,
        }).result_rows
        if not rows:
            return
        live_net = {}
        extras = []
        for resource_id, date, invoice_month, net in rows:
            net = float(net or 0)
            key = (self._clickhouse_lookup_date(date),
                   str(invoice_month or ''))
            if resource_id == keeper_id:
                live_net[key] = live_net.get(key, 0.0) + net
            else:
                extras.append((resource_id, date, invoice_month, net, key))
        payload = []
        for resource_id, date, invoice_month, net, key in extras:
            invoice = str(invoice_month or '')
            if abs(live_net.get(key, 0.0)) <= 0.000001:
                payload.append([
                    self.cloud_acc_id, keeper_id, date, abs(net),
                    1 if net > 0 else -1, invoice])
                live_net[key] = live_net.get(key, 0.0) + net
            payload.append([
                self.cloud_acc_id, resource_id, date, abs(net),
                -1 if net > 0 else 1, invoice])
        if payload:
            self.update_clickhouse_expenses(payload, [
                'cloud_account_id', 'resource_id', 'date', 'cost', 'sign',
                'invoice_month'])

    def _negate_clickhouse_resource_ids(self, resource_ids):
        """Zero leftover net cost on these ClickHouse resource_ids."""
        if not resource_ids or not getattr(self, 'clickhouse_cl', None):
            return 0
        payload = []
        chunk = 500
        ids = list(resource_ids)
        for i in range(0, len(ids), chunk):
            part = ids[i:i + chunk]
            rows = self.clickhouse_cl.query("""
                SELECT resource_id, date, invoice_month, sum(cost * sign)
                FROM expenses FINAL
                WHERE cloud_account_id = %(cloud_account_id)s
                  AND resource_id IN %(resource_ids)s
                GROUP BY resource_id, date, invoice_month
                HAVING abs(sum(cost * sign)) > 0.000001
            """, parameters={
                'cloud_account_id': self.cloud_acc_id,
                'resource_ids': part,
            }).result_rows
            for resource_id, date, invoice_month, net in rows or []:
                net = float(net or 0)
                if abs(net) <= 0.000001:
                    continue
                payload.append([
                    self.cloud_acc_id,
                    resource_id,
                    date,
                    abs(net),
                    -1 if net > 0 else 1,
                    str(invoice_month or ''),
                ])
        if payload:
            self.update_clickhouse_expenses(payload, [
                'cloud_account_id', 'resource_id', 'date', 'cost', 'sign',
                'invoice_month'])
        return len(payload)

    def _restore_miscollapsed_billing_sku_resources(self):
        """Undelete sku.id docs rekey folded as Composer/GKE members.

        Clean writes the deleted Mongo _id (include_deleted), then negate
        zeros that ClickHouse copy. While deleted_at>0 the next import
        cannot stick. Restore when raw still bills that sku and no live twin.

        Do not restore Composer / Dataproc / GKE family leftovers: those
        were folded on purpose. A later incremental would otherwise
        resurrect Duplicate groups from unlabeled sku.id raw (cf57077
        still restores inherited-tag Instance SKUs).
        """
        docs = list(self.mongo_resources.find(
            deleted_miscollapsed_billing_sku_match(self.cloud_acc_id),
            {'_id': 1, 'cloud_resource_id': 1, 'tags': 1,
             'resource_type': 1}))
        restored = 0
        for doc in docs:
            crid = doc.get('cloud_resource_id')
            if not is_gcp_billing_sku_id(crid):
                continue
            ident = gcp_collapse_identity(doc.get('tags') or {})
            if ident and collapse_leftover_may_fold(
                    crid, ident, {'tags': {}},
                    mongo_resource_type=doc.get('resource_type')):
                continue
            live = self.mongo_resources.find_one({
                'cloud_account_id': self.cloud_acc_id,
                'deleted_at': 0,
                'cloud_resource_id': crid,
            }, {'_id': 1})
            if live:
                continue
            billed_filt = {
                'cloud_account_id': self.cloud_acc_id,
                'resource_id': crid,
            }
            if getattr(self, 'period_start', None):
                billed_filt['start_date'] = {'$gte': self.period_start}
            billed = self.mongo_raw.find_one(billed_filt, {'_id': 1})
            if not billed:
                continue
            self.mongo_resources.update_one(
                {'_id': doc['_id']}, {'$set': {'deleted_at': 0}})
            restored += 1
        if restored:
            LOG.info(
                'Restored %s billing SKU resource(s) wrongly collapsed as '
                'Dataproc/Composer/GKE members for %s',
                restored, self.cloud_acc_id)

    def _raw_resource_ids_with_expenses(self, crids):
        """resource_id values that still have at least one mongo_raw row.

        After Phase 2/3 rewrite the keeper owns the raw; leftover sku.id
        docs stay deleted. cf57077 skipped every billing SKU in negate so
        restore could undelete Instance SKUs that still billed. That also
        left ghost ClickHouse on SKUs whose raw had already moved.
        """
        billed = set()
        ids = list({c for c in crids if c})
        if not ids:
            return billed
        for i in range(0, len(ids), 200):
            chunk = ids[i:i + 200]
            found = self.mongo_raw.distinct('resource_id', {
                'cloud_account_id': self.cloud_acc_id,
                'resource_id': {'$in': chunk},
            })
            if not isinstance(found, (list, tuple, set)):
                return set(ids)
            billed.update(x for x in found if x)
        return billed

    def _negate_deleted_collapsed_clickhouse_expenses(self):
        """Drop leftover CH on soft-deleted collapse members and empty SKUs.

        Same-id deleted twins of a live keeper are merged onto the keeper
        per day (copy when live has no net; negate when the day is already
        there). Members that kept the original cloud_resource_id (node, pvc,
        uuid) are negated: the keeper already has the Mongo raw cost, and
        moving them would double.

        Billing sku.id leftovers are negated only when mongo_raw no longer
        bills that id. SKUs that still have raw stay (restore path, cf57077).
        SQL/Run mixed SKUs often lack Composer/GKE tags, so they miss
        deleted_collapsed_member_match; those are picked up as deleted
        docs with no remaining raw.
        """
        self._reassign_deleted_collapsed_identity_expenses()
        live_crids = {
            doc.get('cloud_resource_id')
            for doc in self.mongo_resources.find(
                collapsed_identity_duplicate_match(self.cloud_acc_id),
                {'cloud_resource_id': 1})
            if doc.get('cloud_resource_id')
        }
        extra_ids = []
        sku_docs = []
        for doc in self.mongo_resources.find(
                deleted_collapsed_member_match(self.cloud_acc_id),
                {'_id': 1, 'cloud_resource_id': 1}):
            crid = doc.get('cloud_resource_id')
            if crid in live_crids:
                continue
            if is_gcp_billing_sku_id(crid):
                sku_docs.append(doc)
                continue
            if collapsed_identity_from_resource_id(crid):
                continue
            extra_ids.append(doc['_id'])
        billed_skus = self._raw_resource_ids_with_expenses(
            [doc.get('cloud_resource_id') for doc in sku_docs])
        extra_ids.extend(
            doc['_id'] for doc in sku_docs
            if doc.get('cloud_resource_id') not in billed_skus)

        deleted = list(self.mongo_resources.find(
            {'cloud_account_id': self.cloud_acc_id,
             'deleted_at': {'$gt': 0}},
            {'_id': 1, 'cloud_resource_id': 1}))
        billed_deleted = self._raw_resource_ids_with_expenses(
            [doc.get('cloud_resource_id') for doc in deleted])
        seen = set(extra_ids)
        for doc in deleted:
            if doc['_id'] in seen:
                continue
            crid = doc.get('cloud_resource_id')
            if (not crid or crid in live_crids
                    or crid in billed_deleted):
                continue
            extra_ids.append(doc['_id'])
            seen.add(doc['_id'])

        if not extra_ids:
            return
        n = self._negate_clickhouse_resource_ids(extra_ids)
        if n:
            LOG.info(
                'Negated %s leftover ClickHouse row(s) on deleted '
                'Dataproc/Composer/GKE/PVC/SKU member(s) for %s',
                n, self.cloud_acc_id)

    def _dedupe_collapsed_gcp_identity_resources(self):
        """Keep one live Mongo doc per dataproc/composer/gke identity."""
        pipeline = [
            {'$match': collapsed_identity_duplicate_match(self.cloud_acc_id)},
            {'$group': {
                '_id': '$cloud_resource_id',
                'count': {'$sum': 1},
                'docs': {'$push': {
                    '_id': '$_id',
                    'cloud_resource_hash': '$cloud_resource_hash',
                    'first_seen': '$first_seen',
                    'last_seen': '$last_seen',
                }},
            }},
            {'$match': {'count': {'$gt': 1}}},
        ]
        now = opttime.utcnow_timestamp()
        groups = 0
        extras_n = 0
        for row in self.mongo_resources.aggregate(pipeline, allowDiskUse=True):
            docs = row.get('docs') or []
            keeper = pick_collapsed_duplicate_keeper(docs)
            if not keeper:
                continue
            extra_ids = [
                doc['_id'] for doc in docs if doc['_id'] != keeper['_id']]
            if not extra_ids:
                continue
            first_seen = keeper.get('first_seen')
            last_seen = keeper.get('last_seen')
            for doc in docs:
                fs, ls = doc.get('first_seen'), doc.get('last_seen')
                if fs is not None and (first_seen is None or fs < first_seen):
                    first_seen = fs
                if ls is not None and (last_seen is None or ls > last_seen):
                    last_seen = ls
            keeper_set = {}
            if first_seen is not None:
                keeper_set['first_seen'] = first_seen
            if last_seen is not None:
                keeper_set['last_seen'] = last_seen
            keeper_update = {'$unset': {'cloud_resource_hash': ''}}
            if keeper_set:
                keeper_update['$set'] = keeper_set
            self.mongo_resources.update_one(
                {'_id': keeper['_id']}, keeper_update)
            self.mongo_resources.update_many(
                {'_id': {'$in': extra_ids}, 'deleted_at': 0},
                {'$set': {'deleted_at': now}})
            self._reassign_clickhouse_expenses(extra_ids, keeper['_id'])
            groups += 1
            extras_n += len(extra_ids)
        if groups:
            LOG.info(
                'Deduped %s collapsed GCP identity group(s) '
                '(%s extra docs) for %s',
                groups, extras_n, self.cloud_acc_id)
        self._reassign_deleted_collapsed_identity_expenses()
        self._purge_deleted_collapsed_identity_twins()

    def _reassign_deleted_collapsed_identity_expenses(self):
        """Merge ClickHouse from soft-deleted twins onto the live same-id keeper.

        skip_existing now returns the live twin (8366112). Unique days still
        sitting on the dead id must move; days already on live are only
        negated so billed cost does not double.
        """
        live = list(self.mongo_resources.find({
            'cloud_account_id': self.cloud_acc_id,
            'deleted_at': 0,
            'cloud_resource_id': {'$exists': True, '$nin': [None, '']},
        }, {'_id': 1, 'cloud_resource_id': 1,
            'cloud_resource_hash': 1, 'first_seen': 1, 'last_seen': 1}))
        live_by_crid = defaultdict(list)
        for doc in live:
            live_by_crid[doc['cloud_resource_id']].append(doc)
        if not live_by_crid:
            return
        extras = list(self.mongo_resources.find({
            'cloud_account_id': self.cloud_acc_id,
            'deleted_at': {'$gt': 0},
            'cloud_resource_id': {'$in': list(live_by_crid)},
        }, {'_id': 1, 'cloud_resource_id': 1}))
        extra_ids_by_crid = defaultdict(list)
        for extra in extras:
            crid = extra.get('cloud_resource_id')
            if not crid:
                continue
            extra_ids_by_crid[crid].append(extra['_id'])
        moved = 0
        for crid, extra_ids in extra_ids_by_crid.items():
            keeper = pick_collapsed_duplicate_keeper(live_by_crid[crid])
            if not keeper:
                continue
            extra_ids = [
                extra_id for extra_id in extra_ids
                if extra_id != keeper['_id']]
            if not extra_ids:
                continue
            self._reassign_clickhouse_expenses(extra_ids, keeper['_id'])
            moved += len(extra_ids)
        if moved:
            LOG.info(
                'Merged ClickHouse from %s deleted same-id twin(s) for %s',
                moved, self.cloud_acc_id)

    def _purge_deleted_collapsed_identity_twins(self):
        """Hard-delete a soft-deleted keeper when a live same-id twin exists.

        Do not undelete the old _id. The live doc already owns billing; the
        dead twin is leftover from a bad fold and only clutters Mongo.
        """
        live = list(self.mongo_resources.find({
            'cloud_account_id': self.cloud_acc_id,
            'deleted_at': 0,
            'cloud_resource_id': {'$regex': COLLAPSED_IDENTITY_ID_REGEX},
        }, {'_id': 1, 'cloud_resource_id': 1}))
        live_crids = list({
            doc['cloud_resource_id'] for doc in live
            if doc.get('cloud_resource_id')})
        if not live_crids:
            return 0
        dead = list(self.mongo_resources.find({
            'cloud_account_id': self.cloud_acc_id,
            'deleted_at': {'$gt': 0},
            'cloud_resource_id': {'$in': live_crids},
        }, {'_id': 1}))
        dead_ids = [doc['_id'] for doc in dead]
        if not dead_ids:
            return 0
        self.mongo_resources.delete_many({'_id': {'$in': dead_ids}})
        LOG.info(
            'Permanently deleted %s obsolete collapsed keeper twin(s) for %s',
            len(dead_ids), self.cloud_acc_id)
        return len(dead_ids)

    def generate_clean_records(self, regeneration=False):
        def save_expenses(resources_unique_ids, unique_id_field, date_from=None):
            resource_count = len(resources_unique_ids)
            if resource_count == 0:
                return
            progress = -CLEAN_PROGRESS_LOG_EVERY_PCT
            date_filter = date_from
            if date_filter is None:
                date_filter = self.period_start
            for i in range(0, resource_count, READ_CHUNK_SIZE):
                pct = int(i / resource_count * 100)
                bucket = pct - (pct % CLEAN_PROGRESS_LOG_EVERY_PCT)
                if bucket > progress:
                    progress = bucket
                    LOG.info(
                        'Clean progress for %s (%s): %s%%',
                        self.cloud_acc_id, unique_id_field, progress)

                filters = [{'cloud_account_id': self.cloud_acc_id}]
                if date_filter:
                    filters.append({'start_date': {'$gte': date_filter}})
                filters.append({unique_id_field: {
                    '$in': resources_unique_ids[i:i + READ_CHUNK_SIZE]
                }})
                expenses = self.mongo_raw.aggregate([
                    {'$match': {
                        '$and': filters,
                    }},
                ], allowDiskUse=True)
                chunk = defaultdict(list)
                for e in expenses:
                    chunk[e[unique_id_field]].append(e)
                self.save_clean_expenses(self.cloud_acc_id, chunk,
                                         unique_id_field=unique_id_field)

        base_filters = [{'cloud_account_id': self.cloud_acc_id}]
        if self.period_start:
            base_filters.append({'start_date': {'$gte': self.period_start}})

        distinct_filters = {}
        for f in base_filters:
            distinct_filters.update(f)

        self._rewrite_serverless_dataproc_raw_ids()
        self._rewrite_labeled_collapse_raw_ids()
        self._rewrite_detailed_collapse_raw_ids()
        self._rekey_stale_serverless_dataproc_resources()
        # Numeric/discovery leftovers first. Billing SKU leftovers stay live
        # until _rekey_collapsed_sku_leftovers rewrites unlabeled raw.
        self._rekey_collapsed_labeled_resources()
        self._rekey_collapsed_sku_leftovers()
        self._rekey_detailed_collapse_leftovers()
        self._rekey_unlabeled_gke_pvc_resources()
        self._dedupe_collapsed_gcp_identity_resources()
        self._restore_miscollapsed_billing_sku_resources()
        r_id_filters = distinct_filters.copy()
        r_id_filters['resource_id'] = {'$exists': True, '$ne': None}
        resource_ids = list(x['_id'] for x in self.mongo_raw.aggregate([
            {'$match': r_id_filters},
            {'$group': {'_id': '$resource_id'}}
        ], allowDiskUse=True))

        r_hash_filters = distinct_filters.copy()
        r_hash_filters['$or'] = [{'resource_id': {'$exists': False}},
                                 {'resource_id': {'$eq': None}}]
        r_hash_filters['resource_hash'] = {'$exists': True}
        resource_hashes = list(x['_id'] for x in self.mongo_raw.aggregate([
            {'$match': r_hash_filters},
            {'$group': {'_id': '$resource_hash'}}
        ], allowDiskUse=True))

        LOG.info(
            'Resources with ids count for %s: %s',
            self.cloud_acc_id, len(resource_ids))
        save_expenses(resource_ids, 'resource_id')
        if resource_hashes:
            LOG.info(
                'Resources without ids count for %s: %s',
                self.cloud_acc_id, len(resource_hashes))
        save_expenses(resource_hashes, 'resource_hash')
        extra_from = self._backdated_clean_from()
        if extra_from:
            extra_ids, extra_hashes = self._backdated_clean_identities()
            LOG.info(
                'Recleaning ClickHouse from %s for %s: %s resource_id(s), '
                '%s resource_hash(es) merged outside the incremental window',
                extra_from, self.cloud_acc_id,
                len(extra_ids), len(extra_hashes))
            save_expenses(extra_ids, 'resource_id', date_from=extra_from)
            save_expenses(extra_hashes, 'resource_hash', date_from=extra_from)
        self._retire_stale_serverless_dataproc_resources()
        self._negate_deleted_collapsed_clickhouse_expenses()
        self._reconcile_clickhouse_target()
        self._refresh_resource_duplicates()

    def _refresh_resource_duplicates(self):
        """Snapshot Duplicate groups after clean so billing leftovers show up."""
        rest = getattr(self, 'rest_cl', None)
        if rest is None or not hasattr(rest, 'resource_duplicates_refresh'):
            return
        try:
            rest.resource_duplicates_refresh(self.cloud_acc_id)
        except Exception:
            LOG.warning(
                'Failed to refresh resource duplicates for %s',
                self.cloud_acc_id, exc_info=True)

    def create_traffic_processing_tasks(self):
        self._create_traffic_processing_tasks()
