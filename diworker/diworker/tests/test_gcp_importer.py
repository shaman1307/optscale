#!/usr/bin/env python
"""Unit tests for GCP report importer billing fetch.

Requires diworker runtime deps. Inside the diworker image / local venv:

  PYTHONPATH=. ./diworker/.venv/bin/python \\
    -m unittest diworker.diworker.tests.test_gcp_importer -v
"""
import unittest
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

try:
    from diworker.diworker.importers.base import BaseReportImporter
    from diworker.diworker.importers.gcp import GcpReportImporter
except ImportError as exc:  # pragma: no cover - host without deps
    BaseReportImporter = None
    GcpReportImporter = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class _FakeRow:
    def __init__(self, **kwargs):
        self._data = kwargs

    def items(self):
        return self._data.items()


def _naive(dt):
    if dt is None:
        return None
    if getattr(dt, 'tzinfo', None) is not None:
        return dt.replace(tzinfo=None)
    return dt


def _bq_month_rows_matching_store(store):
    """BQ month aggregate that mirrors in-memory Mongo docs (no raw dump)."""

    def _inner(month_start, month_end, partition_end=None):
        start_n = _naive(month_start)
        end_n = _naive(month_end)
        by_type = defaultdict(lambda: {'billed_sum': 0.0, 'rids': set()})
        for doc in store.docs:
            sd = _naive(doc.get('start_date'))
            if sd is None or start_n is None or end_n is None:
                continue
            if not (start_n <= sd < end_n):
                continue
            rtype, _ = GcpReportImporter._get_resource_type_and_name({
                'cost_type': doc.get('cost_type') or 'regular',
                'sku': doc.get('sku') or '',
                'service': doc.get('service'),
                'resource_id': doc.get('resource_id'),
                'resource_hash': doc.get('resource_hash'),
                'region': None,
            })
            by_type[rtype]['billed_sum'] += float(doc.get('cost') or 0)
            rid = doc.get('resource_id') or doc.get('resource_hash')
            if rid:
                by_type[rtype]['rids'].add(rid)
        return [
            {
                'resource_type': rtype,
                'billed_sum': vals['billed_sum'],
                'resource_count': len(vals['rids']),
            }
            for rtype, vals in by_type.items()
        ]

    return _inner


class _InMemoryRawExpenses:
    """Applies pymongo UpdateOne $set / $setOnInsert / $inc / $addToSet."""

    def __init__(self):
        self.docs = []

    @staticmethod
    def _lookup(doc, key):
        if '.' not in key:
            return doc.get(key, None), key in doc
        cur = doc
        for part in key.split('.'):
            if isinstance(cur, list):
                try:
                    cur = cur[int(part)]
                except (ValueError, IndexError, TypeError):
                    return None, False
            elif isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return None, False
        return cur, True

    def _matches(self, doc, filt):
        for key, expected in filt.items():
            if key == '$or':
                if not any(self._matches(doc, clause) for clause in expected):
                    return False
                continue
            if key == '$and':
                if not all(self._matches(doc, clause) for clause in expected):
                    return False
                continue
            if isinstance(expected, dict):
                if '$in' in expected:
                    value, _present = self._lookup(doc, key)
                    if value not in expected['$in']:
                        return False
                    continue
                if '$exists' in expected:
                    _, present = self._lookup(doc, key)
                    if expected['$exists'] and not present:
                        return False
                    if (not expected['$exists']) and present:
                        return False
                    continue
                if '$nin' in expected:
                    value, present = self._lookup(doc, key)
                    banned = expected['$nin']
                    if isinstance(value, list):
                        if any(item in banned for item in value):
                            return False
                    elif present and value in banned:
                        return False
                    continue
                value = _naive(doc.get(key))
                if '$gte' in expected and not (
                        value is not None and
                        value >= _naive(expected['$gte'])):
                    return False
                if '$lt' in expected and not (
                        value is not None and
                        value < _naive(expected['$lt'])):
                    return False
                if '$gt' in expected and not (
                        value is not None and
                        value > _naive(expected['$gt'])):
                    return False
                if '$lte' in expected and not (
                        value is not None and
                        value <= _naive(expected['$lte'])):
                    return False
            elif doc.get(key) != expected:
                return False
        return True

    @staticmethod
    def _group_id(spec_id, doc):
        if spec_id is None:
            return None
        if isinstance(spec_id, str) and spec_id.startswith('$'):
            return doc.get(spec_id[1:])
        if not isinstance(spec_id, dict):
            return spec_id
        if '$ifNull' in spec_id:
            for path in spec_id['$ifNull']:
                if isinstance(path, str) and path.startswith('$'):
                    val = doc.get(path[1:])
                    if val is not None:
                        return val
            return None
        key = {}
        for field, path in spec_id.items():
            attr = path[1:] if isinstance(path, str) and path.startswith('$') else path
            if not isinstance(attr, str):
                continue
            key[field] = doc.get(attr)
        return tuple(sorted(key.items()))

    def aggregate(self, pipeline, allowDiskUse=False):
        docs = list(self.docs)
        for stage in pipeline:
            if '$match' in stage:
                docs = [d for d in docs if self._matches(d, stage['$match'])]
                continue
            if '$group' not in stage:
                raise NotImplementedError(stage)
            spec = stage['$group']
            grouped = {}
            spec_id = spec.get('_id')
            for doc in docs:
                gid = self._group_id(spec_id, doc)
                bucket = grouped.setdefault(gid, {
                    '_id': gid if not isinstance(gid, tuple) else dict(gid),
                    'cost': 0.0,
                    'rids': set(),
                    'resource_hash': None,
                    'sku': None,
                    'service': None,
                    'cost_type': None,
                    'tags': None,
                    'resource_id': None,
                })
                bucket['cost'] += float(doc.get('cost') or 0)
                rid = doc.get('resource_id') or doc.get('resource_hash')
                if rid:
                    bucket['rids'].add(rid)
                for field in (
                        'resource_hash', 'sku', 'service', 'cost_type',
                        'tags', 'resource_id'):
                    if bucket[field] is None and doc.get(field) is not None:
                        bucket[field] = doc.get(field)
            docs = []
            for bucket in grouped.values():
                row = {
                    '_id': bucket['_id'],
                    'cost': bucket['cost'],
                    'rids': list(bucket['rids']),
                    'resource_hash': bucket['resource_hash'],
                    'sku': bucket['sku'],
                    'service': bucket['service'],
                    'cost_type': bucket['cost_type'],
                    'tags': bucket['tags'] or {},
                    'resource_id': bucket['resource_id'],
                }
                docs.append(row)
        return docs

    def bulk_write(self, ops, ordered=False):
        for op in ops:
            filt = op._filter
            update = op._doc
            upsert = bool(getattr(op, '_upsert', False))
            match_idx = None
            for i, doc in enumerate(self.docs):
                if self._matches(doc, filt):
                    match_idx = i
                    break
            set_fields = dict(update.get('$set') or {})
            set_on_insert = dict(update.get('$setOnInsert') or {})
            inc_fields = dict(update.get('$inc') or {})
            add_to_set = dict(update.get('$addToSet') or {})
            if match_idx is None:
                if not upsert:
                    continue
                new_doc = dict(set_on_insert)
                new_doc.update(set_fields)
                for key, value in inc_fields.items():
                    new_doc[key] = float(new_doc.get(key) or 0) + float(value)
                for key, value in add_to_set.items():
                    new_doc[key] = [value]
                self.docs.append(new_doc)
                continue
            doc = self.docs[match_idx]
            doc.update(set_fields)
            for key, value in inc_fields.items():
                doc[key] = float(doc.get(key) or 0) + float(value)
            for key, value in add_to_set.items():
                arr = list(doc.get(key) or [])
                if value not in arr:
                    arr.append(value)
                doc[key] = arr
        return MagicMock()

    def delete_many(self, filt):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not self._matches(d, filt)]
        return MagicMock(deleted_count=before - len(self.docs))

    def insert_many(self, docs, ordered=True):
        self.docs.extend(dict(d) for d in docs)
        return MagicMock()

    def find_cost(self, **filt):
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in filt.items()):
                return doc.get('cost')
        return None

    def count_matching(self, **filt):
        return sum(
            1 for doc in self.docs
            if all(doc.get(k) == v for k, v in filt.items()))


@unittest.skipIf(
    GcpReportImporter is None,
    'diworker runtime deps missing: %s' % _IMPORT_ERROR)
class TestGcpImporterLoadRaw(unittest.TestCase):
    def _importer(self, last_import_at=0):
        from unittest.mock import PropertyMock

        imp = GcpReportImporter.__new__(GcpReportImporter)
        object.__setattr__(imp, 'cloud_acc_id', 'ca-gcp-1')
        object.__setattr__(
            imp,
            'period_start',
            datetime(2026, 4, 1, 12, 30, tzinfo=timezone.utc))
        object.__setattr__(imp, '_raw_insert_only', False)
        adapter = MagicMock()
        adapter.is_virtual_billing_project = False
        adapter.get_usage_month_by_resource_type.return_value = []
        object.__setattr__(imp, '_cloud_adapter', adapter)
        mongo_raw = MagicMock()
        mongo_raw.delete_many.return_value = MagicMock(deleted_count=0)
        mongo_raw.count_documents.return_value = 0
        mongo_raw.aggregate.return_value = []
        checkpoints = MagicMock()
        checkpoints.find_one.return_value = None
        mongo_raw.database = {'import_checkpoints': checkpoints}
        object.__setattr__(imp, 'mongo_raw', mongo_raw)
        object.__setattr__(imp, '_checkpoints', checkpoints)
        mongo_resources = MagicMock()
        mongo_resources.distinct.return_value = []
        object.__setattr__(imp, 'mongo_resources', mongo_resources)
        object.__setattr__(imp, '_discovery_resource_ids', None)
        object.__setattr__(imp, 'report_import_id', 'ri-test-1')
        object.__setattr__(imp, 'rest_cl', MagicMock())
        ca_patch = patch.object(
            GcpReportImporter,
            'cloud_acc',
            new_callable=PropertyMock,
            return_value={'last_import_at': last_import_at})
        ca_patch.start()
        self.addCleanup(ca_patch.stop)
        imp.update_raw_records = MagicMock()
        return imp

    def test_load_raw_data_uses_single_range_get_usage(self):
        """Whole import window must be one BigQuery job, not a day loop."""
        imp = self._importer()
        job = MagicMock()
        job.result.return_value = iter([])
        imp.cloud_adapter.get_usage.return_value = job
        now = datetime(2026, 7, 29, 15, 45, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp.load_raw_data()

        imp.cloud_adapter.get_usage.assert_called_once_with(
            datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 30, 0, 0, tzinfo=timezone.utc),
        )
        job.result.assert_called_once()
        imp.mongo_raw.delete_many.assert_called_once_with(
            {'cloud_account_id': 'ca-gcp-1'})
        self.assertTrue(imp._raw_insert_only)
        imp.update_raw_records.assert_not_called()
        imp._checkpoints.update_one.assert_called_once()

    def test_load_raw_data_skips_when_checkpoint_and_raw_exist(self):
        imp = self._importer()
        period_start = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
        imp._checkpoints.find_one.return_value = {
            'cloud_account_id': 'ca-gcp-1',
            'kind': 'gcp_raw_first_load',
            'period_start': period_start,
            'period_end': datetime(2026, 7, 30, 0, 0, tzinfo=timezone.utc),
            'written': 1000,
        }
        imp.mongo_raw.count_documents.return_value = 1
        now = datetime(2026, 7, 29, 15, 45, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp.load_raw_data()

        imp.cloud_adapter.get_usage.assert_not_called()
        imp.mongo_raw.delete_many.assert_not_called()
        imp.update_raw_records.assert_not_called()
        imp._checkpoints.update_one.assert_not_called()

    def test_load_raw_data_does_not_skip_without_raw_docs(self):
        imp = self._importer()
        period_start = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
        imp._checkpoints.find_one.return_value = {
            'period_start': period_start,
            'written': 1000,
        }
        imp.mongo_raw.count_documents.return_value = 0
        job = MagicMock()
        job.result.return_value = iter([])
        imp.cloud_adapter.get_usage.return_value = job
        now = datetime(2026, 7, 29, 15, 45, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp.load_raw_data()

        imp.mongo_raw.delete_many.assert_called_once()
        imp.cloud_adapter.get_usage.assert_called_once()

    def test_update_cloud_import_time_clears_checkpoint(self):
        imp = self._importer()
        with patch(
                'diworker.diworker.importers.base.BaseReportImporter'
                '.update_cloud_import_time') as base_update:
            imp.update_cloud_import_time(123)
        base_update.assert_called_once_with(123)
        imp._checkpoints.delete_many.assert_called_once()
        imp.rest_cl.cloud_account_update.assert_not_called()

    def test_read_chunk_size_is_200(self):
        from diworker.diworker.importers import gcp as gcp_mod
        self.assertEqual(gcp_mod.READ_CHUNK_SIZE, 200)

    def test_load_raw_data_logs_real_bq_progress_pct(self):
        """Progress % comes from get_usage job.total_rows, not COUNT(*)."""
        from diworker.diworker.importers import gcp as gcp_mod

        imp = self._importer()
        row = _FakeRow(
            service='Compute Engine',
            start_date=datetime(2026, 7, 1),
            end_date=datetime(2026, 7, 2),
            cost=1.0,
            cost_type='regular',
            location={'region': 'europe-west3'},
            currency='USD',
            currency_conversion_rate=1.0,
            sku='Core',
            sku_id='sku-1',
            tags=[],
            usage_amount=1.0,
            usage_unit='seconds',
            usage_amount_in_pricing_units=1.0,
            usage_pricing_unit='hour',
            system_tags=[],
            credits=[],
            adjustment_info=None,
            export_time=None,
        )
        row_iter = MagicMock()
        row_iter.total_rows = None
        row_iter.__iter__ = lambda self: iter([row, row])
        job = MagicMock()
        job.result.return_value = row_iter
        job.total_rows = 2
        imp.cloud_adapter.get_usage.return_value = job
        imp.mongo_raw.aggregate.return_value = [{
            '_id': {
                'sku': 'Core',
                'service': 'Compute Engine',
                'cost_type': 'regular',
            },
            'cost': 2.0,
            'rids': ['sku-1'],
            'resource_hash': None,
        }]
        now = datetime(2026, 7, 29, 15, 45, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now), \
                patch.object(gcp_mod, 'PROGRESS_LOG_EVERY', 1), \
                patch.object(gcp_mod, 'WRITE_CHUNK_SIZE', 1), \
                patch('diworker.diworker.importers.gcp.LOG') as log:
            imp.load_raw_data()

        messages = ' '.join(
            str(c.args[0]) % c.args[1:] if c.args else ''
            for c in log.info.call_args_list)
        self.assertIn('from job', messages)
        self.assertIn('pct=0', messages)
        self.assertIn('pct=', messages)
        self.assertIn('bq_total=2', messages)
        imp.cloud_adapter.get_usage_count.assert_not_called()

    def test_load_raw_data_progress_pct_follows_bq_rows_through_merge(self):
        """pct = BQ rows consumed by merge / total, even when keys collapse."""
        from diworker.diworker.importers import gcp as gcp_mod

        imp = self._importer()
        rows = []
        for _ in range(10):
            rows.append(_FakeRow(
                service='Compute Engine',
                start_date=datetime(2026, 7, 1),
                end_date=datetime(2026, 7, 2),
                cost=1.0,
                cost_type='regular',
                location={'region': 'europe-west3'},
                currency='USD',
                currency_conversion_rate=1.0,
                sku='Core',
                sku_id='sku-1',
                tags=[],
                usage_amount=1.0,
                usage_unit='seconds',
                usage_amount_in_pricing_units=1.0,
                usage_pricing_unit='hour',
                system_tags=[],
                credits=[],
                adjustment_info=None,
                export_time=None,
            ))
        row_iter = MagicMock()
        row_iter.total_rows = None
        row_iter.__iter__ = lambda self: iter(rows)
        job = MagicMock()
        job.result.return_value = row_iter
        job.total_rows = 10
        imp.cloud_adapter.get_usage.return_value = job
        imp.mongo_raw.aggregate.return_value = [{
            '_id': {
                'sku': 'Core',
                'service': 'Compute Engine',
                'cost_type': 'regular',
            },
            'cost': 10.0,
            'rids': ['sku-1'],
            'resource_hash': None,
        }]
        now = datetime(2026, 7, 29, 15, 45, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now), \
                patch.object(gcp_mod, 'PROGRESS_LOG_EVERY', 4), \
                patch.object(gcp_mod, 'PROGRESS_LOG_EVERY_SEC', 10 ** 9), \
                patch.object(gcp_mod, 'WRITE_CHUNK_SIZE', 1000), \
                patch('diworker.diworker.importers.gcp.LOG') as log:
            imp.load_raw_data()

        messages = [
            str(c.args[0]) % c.args[1:] if c.args else ''
            for c in log.info.call_args_list]
        progress = [m for m in messages if 'GCP raw load progress' in m]
        self.assertTrue(any('pct=40' in m and 'bq_seen=4' in m for m in progress))
        self.assertTrue(any('pct=80' in m and 'bq_seen=8' in m for m in progress))
        finished = [m for m in messages if 'GCP raw load finished' in m]
        self.assertTrue(any('pct=100' in m and 'bq_seen=10' in m for m in finished))
        self.assertTrue(any('bq_total=10' in m for m in progress))

    def test_load_raw_data_incremental_wipes_and_inserts(self):
        imp = self._importer(last_import_at=1719792000)
        row = _FakeRow(
            service='Compute Engine',
            start_date=datetime(2026, 4, 1, tzinfo=timezone.utc),
            end_date=datetime(2026, 4, 1, 1, tzinfo=timezone.utc),
            cost=1.0,
            cost_type='regular',
            location={'region': 'europe-west3'},
            currency='USD',
            currency_conversion_rate=1.0,
            sku='Core',
            sku_id='sku-1',
            tags=[],
            usage_amount=1.0,
            usage_unit='hour',
            usage_amount_in_pricing_units=1.0,
            usage_pricing_unit='hour',
            system_tags=[],
            credits=[],
            adjustment_info=None,
            export_time=None,
        )
        job = MagicMock()
        job.result.return_value = iter([row])
        imp.cloud_adapter.get_usage.return_value = job
        now = datetime(2026, 7, 29, 15, 45, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp.load_raw_data()

        part_start = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
        part_end = datetime(2026, 7, 30, 0, 0, tzinfo=timezone.utc)
        imp.mongo_raw.delete_many.assert_called_once_with({
            'cloud_account_id': 'ca-gcp-1',
            'start_date': {
                '$gte': part_start,
                '$lt': part_end,
            },
        })
        self.assertTrue(imp._raw_insert_only)
        imp.cloud_adapter.get_usage.assert_called_once_with(
            part_start, part_end)

    def test_load_raw_data_incremental_empty_stream_does_not_wipe(self):
        imp = self._importer(last_import_at=1719792000)
        job = MagicMock()
        job.result.return_value = iter([])
        imp.cloud_adapter.get_usage.return_value = job
        now = datetime(2026, 7, 29, 15, 45, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp.load_raw_data()

        imp.cloud_adapter.get_usage.assert_called_once()
        imp.mongo_raw.delete_many.assert_not_called()
        imp.update_raw_records.assert_not_called()

    def test_load_raw_data_incremental_single_get_usage(self):
        imp = self._importer(last_import_at=1719792000)
        job = MagicMock()
        job.result.return_value = iter([])
        imp.cloud_adapter.get_usage.return_value = job
        now = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp.load_raw_data()

        self.assertEqual(imp.cloud_adapter.get_usage.call_count, 1)
        imp.cloud_adapter.get_usage.assert_called_once_with(
            datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc),
        )

    def test_load_raw_data_incremental_virtual_keeps_single_window(self):
        from tools.cloud_adapter.clouds.gcp import Gcp

        imp = self._importer(last_import_at=1719792000)
        imp.cloud_adapter.is_virtual_billing_project = True
        imp.cloud_adapter.invoice_months_for_window = (
            Gcp.invoice_months_for_window)
        job = MagicMock()
        job.result.return_value = iter([])
        imp.cloud_adapter.get_usage.return_value = job
        now = datetime(2026, 7, 29, 15, 45, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp.load_raw_data()

        imp.cloud_adapter.get_usage.assert_called_once_with(
            datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 30, 0, 0, tzinfo=timezone.utc),
        )
        imp.mongo_raw.delete_many.assert_called_once_with({
            'cloud_account_id': 'ca-gcp-1',
            'invoice_month': {
                '$in': ['202604', '202605', '202606', '202607'],
            },
        })

    def test_load_raw_data_compute_engine_virtual_reconciles_invoice_month(self):
        """CE virtual CUD with prior usage_start must not fail current-month gate."""
        from tools.cloud_adapter.clouds.gcp import Gcp

        imp = self._importer(last_import_at=1719792000)
        imp.cloud_adapter.is_virtual_billing_project = True
        imp.cloud_adapter.project_id = Gcp.virtual_service_project_id(
            'Compute Engine')
        imp.cloud_adapter.parse_virtual_service = Gcp.parse_virtual_service
        imp.cloud_adapter.invoice_months_for_window = (
            Gcp.invoice_months_for_window)
        rows = [
            _FakeRow(
                service='Compute Engine',
                start_date=datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
                end_date=datetime(2026, 7, 31, 13, 0, tzinfo=timezone.utc),
                invoice_month='202608',
                cost=-10.0,
                cost_type='regular',
                location={'region': 'us-central1'},
                currency='USD',
                currency_conversion_rate=1.0,
                sku='Compute Flexible Committed Use Discounts - 3 Year',
                sku_id='B22F-51BE-D599',
                tags=[],
                usage_amount=1.0,
                usage_unit='seconds',
                usage_amount_in_pricing_units=1.0,
                usage_pricing_unit='hour',
                system_tags=[],
                credits=[],
                adjustment_info=None,
                export_time=None,
            ),
            _FakeRow(
                service='Compute Engine',
                start_date=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
                end_date=datetime(2026, 8, 2, 13, 0, tzinfo=timezone.utc),
                invoice_month='202608',
                cost=4.0,
                cost_type='regular',
                location={'region': 'us-central1'},
                currency='USD',
                currency_conversion_rate=1.0,
                sku='Compute Flexible Committed Use Discounts - 3 Year',
                sku_id='B22F-51BE-D599',
                tags=[],
                usage_amount=1.0,
                usage_unit='seconds',
                usage_amount_in_pricing_units=1.0,
                usage_pricing_unit='hour',
                system_tags=[],
                credits=[],
                adjustment_info=None,
                export_time=None,
            ),
        ]
        job = MagicMock()
        job.result.return_value = iter(rows)
        imp.cloud_adapter.get_usage.return_value = job
        imp.mongo_raw.aggregate.return_value = [{
            '_id': {
                'sku': 'Compute Flexible Committed Use Discounts - 3 Year',
                'service': 'Compute Engine',
                'cost_type': 'regular',
            },
            'cost': -6.0,
            'rids': ['B22F-51BE-D599'],
            'resource_hash': None,
        }]
        now = datetime(2026, 8, 15, 16, 0, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp.load_raw_data()

        details = imp.get_import_details()
        self.assertAlmostEqual(details['source_sum'], -6.0)
        self.assertAlmostEqual(details['local_sum'], -6.0)
        self.assertEqual(details['reconciliation'][0]['status'], 'ok')

    def test_virtual_rebuild_marks_current_invoice_days_before_period_start(self):
        """Support-style gap: Sep invoice usage on Aug 24 must reclean CH.

        Virtual wipe+insert rewrites Mongo for the whole invoice month, but
        clean only covers period_start unless backdated days are noted.
        Prior-invoice days before the window stay unmarked.
        """
        from tools.cloud_adapter.clouds.gcp import Gcp

        imp = self._importer(last_import_at=1719792000)
        object.__setattr__(
            imp,
            'period_start',
            datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc))
        imp.cloud_adapter.is_virtual_billing_project = True
        imp.cloud_adapter.invoice_months_for_window = (
            Gcp.invoice_months_for_window)
        backdated = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        in_window = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        prior_invoice = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
        rows = [
            _FakeRow(
                service='Support',
                start_date=backdated,
                end_date=backdated + timedelta(hours=1),
                invoice_month='202609',
                cost=0.94,
                cost_type='regular',
                location={'region': 'global', 'location': 'global',
                          'country': None, 'zone': None},
                currency='USD',
                currency_conversion_rate=1.0,
                sku='GCP Support Variable fee',
                sku_id='5467-9D2D-5B98',
                tags=[],
                usage_amount=1.0,
                usage_unit='month',
                usage_amount_in_pricing_units=1.0,
                usage_pricing_unit='month',
                system_tags=[],
                credits=[],
                adjustment_info=None,
                export_time=None,
            ),
            _FakeRow(
                service='Support',
                start_date=in_window,
                end_date=in_window + timedelta(hours=1),
                invoice_month='202609',
                cost=1.0,
                cost_type='regular',
                location={'region': 'global', 'location': 'global',
                          'country': None, 'zone': None},
                currency='USD',
                currency_conversion_rate=1.0,
                sku='GCP Support Variable fee',
                sku_id='5467-9D2D-5B98',
                tags=[],
                usage_amount=1.0,
                usage_unit='month',
                usage_amount_in_pricing_units=1.0,
                usage_pricing_unit='month',
                system_tags=[],
                credits=[],
                adjustment_info=None,
                export_time=None,
            ),
            _FakeRow(
                service='Support',
                start_date=prior_invoice,
                end_date=prior_invoice + timedelta(hours=1),
                invoice_month='202608',
                cost=10.0,
                cost_type='regular',
                location={'region': 'global', 'location': 'global',
                          'country': None, 'zone': None},
                currency='USD',
                currency_conversion_rate=1.0,
                sku='GCP Support Variable fee',
                sku_id='5467-9D2D-5B98',
                tags=[],
                usage_amount=1.0,
                usage_unit='month',
                usage_amount_in_pricing_units=1.0,
                usage_pricing_unit='month',
                system_tags=[],
                credits=[],
                adjustment_info=None,
                export_time=None,
            ),
        ]
        job = MagicMock()
        job.result.return_value = iter(rows)
        imp.cloud_adapter.get_usage.return_value = job
        imp.mongo_raw.aggregate.return_value = [{
            '_id': {
                'sku': 'GCP Support Variable fee',
                'service': 'Support',
                'cost_type': 'regular',
            },
            'cost': 1.94,
            'rids': ['5467-9D2D-5B98'],
            'resource_hash': None,
        }]
        now = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp.load_raw_data()

        self.assertEqual(
            imp._backdated_clean_from(),
            datetime(2026, 8, 24))
        self.assertIn('5467-9D2D-5B98', imp._backdated_resource_ids)

    def test_update_raw_records_first_load_insert_many(self):
        imp = self._importer(last_import_at=0)
        # Use the real method, not the load_raw_data mock.
        del imp.update_raw_records
        imp._raw_insert_only = True
        imp._update_imported_raw_interval = MagicMock()
        chunk = [
            {'cloud_account_id': 'ca-gcp-1', 'cost': 1.0, 'start_date': 1},
            {'cloud_account_id': 'ca-gcp-1', 'cost': 2.0, 'start_date': 2},
        ]

        with patch(
                'diworker.diworker.importers.gcp.retry_mongo_upsert') as retry:
            imp.update_raw_records(chunk)

        retry.assert_called_once()
        self.assertIs(retry.call_args[0][0], imp.mongo_raw.insert_many)
        self.assertEqual(retry.call_args[0][1], chunk)
        self.assertEqual(retry.call_args[1].get('ordered'), False)

    def test_load_raw_data_streams_merged_rows_to_mongo(self):
        imp = self._importer()
        # Two rows that merge into one, then a distinct third row.
        rows = [
            _FakeRow(
                service='Compute Engine',
                start_date=datetime(2026, 7, 1, tzinfo=timezone.utc),
                end_date=datetime(2026, 7, 2, tzinfo=timezone.utc),
                cost=1.0,
                cost_type='regular',
                location={'region': 'europe-west3'},
                currency='USD',
                currency_conversion_rate=1.0,
                sku='N1 Instance Core',
                sku_id='sku-1',
                tags=[],
                usage_amount=1.0,
                usage_unit='hour',
                usage_amount_in_pricing_units=1.0,
                usage_pricing_unit='hour',
                system_tags=[],
                credits=[],
                adjustment_info=None,
                export_time=None,
            ),
            _FakeRow(
                service='Compute Engine',
                start_date=datetime(2026, 7, 1, tzinfo=timezone.utc),
                end_date=datetime(2026, 7, 2, tzinfo=timezone.utc),
                cost=2.0,
                cost_type='regular',
                location={'region': 'europe-west3'},
                currency='USD',
                currency_conversion_rate=1.0,
                sku='N1 Instance Core',
                sku_id='sku-1',
                tags=[],
                usage_amount=2.0,
                usage_unit='hour',
                usage_amount_in_pricing_units=2.0,
                usage_pricing_unit='hour',
                system_tags=[],
                credits=[],
                adjustment_info=None,
                export_time=None,
            ),
            _FakeRow(
                service='Cloud Storage',
                start_date=datetime(2026, 7, 2, tzinfo=timezone.utc),
                end_date=datetime(2026, 7, 3, tzinfo=timezone.utc),
                cost=0.5,
                cost_type='regular',
                location={'region': 'europe-west3'},
                currency='USD',
                currency_conversion_rate=1.0,
                sku='Standard Storage',
                sku_id='sku-2',
                tags=[],
                usage_amount=10.0,
                usage_unit='byte-seconds',
                usage_amount_in_pricing_units=10.0,
                usage_pricing_unit='gibibyte month',
                system_tags=[],
                credits=[],
                adjustment_info=None,
                export_time=None,
            ),
        ]
        job = MagicMock()
        job.result.return_value = iter(rows)
        imp.cloud_adapter.get_usage.return_value = job
        imp.mongo_raw.aggregate.return_value = [
            {
                '_id': {
                    'sku': 'N1 Instance Core',
                    'service': 'Compute Engine',
                    'cost_type': 'regular',
                },
                'cost': 3.0,
                'rids': ['sku-1'],
                'resource_hash': None,
            },
            {
                '_id': {
                    'sku': 'Standard Storage',
                    'service': 'Cloud Storage',
                    'cost_type': 'regular',
                },
                'cost': 0.5,
                'rids': ['sku-2'],
                'resource_hash': None,
            },
        ]
        now = datetime(2026, 7, 29, 15, 45, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp.load_raw_data()

        imp.cloud_adapter.get_usage.assert_called_once()
        job.result.assert_called_once()
        imp.update_raw_records.assert_called_once()
        written = imp.update_raw_records.call_args[0][0]
        self.assertEqual(len(written), 2)
        self.assertEqual(written[0]['cost'], 3.0)
        self.assertEqual(written[0]['usage_amount'], 3.0)
        self.assertEqual(written[1]['cost'], 0.5)
        self.assertEqual(written[1]['service'], 'Cloud Storage')

    def test_load_raw_data_records_billed_mismatch_and_fails(self):
        """BQ vs Mongo billed mismatch aborts after publishing reconciliation."""
        imp = self._importer()
        row = _FakeRow(
            service='Compute Engine',
            start_date=datetime(2026, 7, 1),
            end_date=datetime(2026, 7, 2),
            cost=5.0,
            cost_type='regular',
            location={'region': 'europe-west3'},
            currency='USD',
            currency_conversion_rate=1.0,
            sku='N1 Instance Core',
            sku_id='sku-1',
            tags=[],
            usage_amount=1.0,
            usage_unit='seconds',
            usage_amount_in_pricing_units=1.0,
            usage_pricing_unit='hour',
            system_tags=[],
            credits=[],
            adjustment_info=None,
            export_time=None,
        )
        job = MagicMock()
        job.result.return_value = iter([row])
        imp.cloud_adapter.get_usage.return_value = job
        imp.mongo_raw.aggregate.return_value = [{
            '_id': {
                'sku': 'N1 Instance Core',
                'service': 'Compute Engine',
                'cost_type': 'regular',
            },
            'cost': 1.0,
            'rids': ['sku-1'],
            'resource_hash': None,
        }]
        now = datetime(2026, 7, 29, 15, 45, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            with self.assertRaises(RuntimeError) as ctx:
                imp.load_raw_data()

        self.assertIn('billed cost mismatch', str(ctx.exception))
        self.assertIn('BQ billed=5.000000', str(ctx.exception))
        self.assertIn('Mongo written cost=1.000000', str(ctx.exception))
        imp.update_raw_records.assert_called_once()
        imp.cloud_adapter.get_usage_month_by_resource_type.assert_not_called()
        details = imp.get_import_details()
        self.assertIsNotNone(details)
        self.assertEqual(details['source_sum'], 5.0)
        self.assertEqual(details['local_sum'], 1.0)
        self.assertEqual(details['delta'], 4.0)
        self.assertEqual(details['reconciliation'][0]['status'], 'mismatch')
        imp.rest_cl.report_import_update.assert_called()
        published = imp.rest_cl.report_import_update.call_args[0][1]
        self.assertEqual(published['details']['source_sum'], 5.0)
        self.assertEqual(
            published['details']['reconciliation'][0]['status'], 'mismatch')

    def test_load_raw_data_allows_billed_delta_up_to_one_dollar(self):
        imp = self._importer()
        row = _FakeRow(
            service='Compute Engine',
            start_date=datetime(2026, 7, 1),
            end_date=datetime(2026, 7, 2),
            cost=1.0,
            cost_type='regular',
            location={'region': 'europe-west3'},
            currency='USD',
            currency_conversion_rate=1.0,
            sku='Core',
            sku_id='sku-1',
            tags=[],
            usage_amount=1.0,
            usage_unit='seconds',
            usage_amount_in_pricing_units=1.0,
            usage_pricing_unit='hour',
            system_tags=[],
            credits=[],
            adjustment_info=None,
            export_time=None,
        )
        job = MagicMock()
        job.result.return_value = iter([row])
        imp.cloud_adapter.get_usage.return_value = job
        # Stream and Mongo both 1.0 — within tolerance, must not fail.
        imp.mongo_raw.aggregate.return_value = [{
            '_id': {
                'sku': 'Core',
                'service': 'Compute Engine',
                'cost_type': 'regular',
            },
            'cost': 1.0,
            'rids': ['sku-1'],
            'resource_hash': None,
        }]
        now = datetime(2026, 7, 29, 15, 45, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp.load_raw_data()

        imp.update_raw_records.assert_called_once()
        imp.cloud_adapter.get_usage_month_by_resource_type.assert_not_called()

    def test_stream_month_cache_sums_merged_cost_not_raw_rows(self):
        """Cache must match Mongo write: credits + merge, not raw BQ lines."""
        imp = self._importer()
        now = datetime(2026, 8, 15, 1, 0, tzinfo=timezone.utc)
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp._reset_stream_month_totals(datetime(2026, 7, 1))
            for cost, credits in ((2.0, []), (3.0, [{'amount': -0.5}])):
                merged = {
                    'start_date': datetime(2026, 8, 2),
                    'sku': 'N1 Instance Core',
                    'service': 'Compute Engine',
                    'cost_type': 'regular',
                    'cost': cost + sum(c['amount'] for c in credits),
                    'resource_id': '962315076699295255',
                    'resource_hash': None,
                }
                imp._accumulate_stream_month_row(merged)
            source = imp._stream_month_source_by_type()
        self.assertAlmostEqual(source['Instance']['billed_sum'], 4.5)
        self.assertEqual(source['Instance']['resource_count'], 1)

    def test_four_month_stream_cache_keeps_only_current_month(self):
        """A 4-month reload must not keep April–July ids in the month cache."""
        imp = self._importer()
        now = datetime(2026, 8, 15, 1, 0, tzinfo=timezone.utc)
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp._reset_stream_month_totals(datetime(2026, 5, 1))
            for month, rid, cost in (
                    (5, 'may-1', 10.0),
                    (6, 'jun-1', 20.0),
                    (7, 'jul-1', 30.0),
                    (8, '962315076699295255', 4.0),
                    (8, '3688322087841111737', 1.0)):
                imp._accumulate_stream_month_row({
                    'start_date': datetime(2026, month, 2),
                    'sku': 'N1 Instance Core',
                    'service': 'Compute Engine',
                    'cost_type': 'regular',
                    'cost': cost,
                    'resource_id': rid,
                    'resource_hash': None,
                })
            groups = imp._stream_month_groups
            source = imp._stream_month_source_by_type()
        self.assertEqual(len(groups), 1)
        only = next(iter(groups.values()))
        self.assertEqual(only['rids'], {
            '962315076699295255', '3688322087841111737'})
        self.assertAlmostEqual(only['cost'], 5.0)
        self.assertAlmostEqual(source['Instance']['billed_sum'], 5.0)
        self.assertEqual(source['Instance']['resource_count'], 2)

    def test_iter_merged_billing_items_merges_non_adjacent_duplicates(self):
        """Same unique key must merge even when interrupted by another key.

        Detailed export interleaves discovery ids and sku.id fallbacks so
        identical Mongo unique keys are often non-adjacent in BQ ORDER BY.
        """
        imp = self._importer()
        base = {
            'start_date': datetime(2026, 7, 1),
            'resource_id': 'r1',
            'resource_hash': None,
            'cloud_account_id': 'ca-gcp-1',
            'sku': 'sku',
            'service': 'Compute Engine',
            'cost': 1.0,
            'usage_amount': 1.0,
            'usage_amount_in_pricing_units': 1.0,
            'credits': 0,
        }
        other = dict(base, resource_id='r2', cost=5.0, usage_amount=5.0,
                     usage_amount_in_pricing_units=5.0)

        def _identity(_self, row):
            return row

        with patch.object(
                GcpReportImporter, '_row_to_dict', new=_identity):
            merged = list(imp._iter_merged_billing_items([
                dict(base),
                other,
                dict(base, cost=2.0, usage_amount=2.0,
                     usage_amount_in_pricing_units=2.0),
            ]))
        self.assertEqual(len(merged), 2)
        costs_by_rid = {m['resource_id']: m['cost'] for m in merged}
        self.assertEqual(costs_by_rid['r1'], 3.0)
        self.assertEqual(costs_by_rid['r2'], 5.0)

    def test_iter_merged_billing_items_keeps_distinct_invoice_months(self):
        """Same usage hour under two invoice.month values must not merge."""
        imp = self._importer()
        july = {
            'start_date': datetime(2026, 7, 15, 6, 0),
            'resource_id': 'r1',
            'resource_hash': None,
            'cloud_account_id': 'ca-gcp-1',
            'sku': 'sku',
            'service': 'Compute Engine',
            'invoice_month': '202607',
            'cost': 5.0,
            'usage_amount': 1.0,
            'usage_amount_in_pricing_units': 1.0,
            'credits': 0,
        }
        august = dict(july, invoice_month='202608', cost=1.0)

        def _identity(_self, row):
            return row

        with patch.object(
                GcpReportImporter, '_row_to_dict', new=_identity):
            merged = list(imp._iter_merged_billing_items([july, august]))
        self.assertEqual(len(merged), 2)
        by_inv = {m['invoice_month']: m['cost'] for m in merged}
        self.assertEqual(by_inv['202607'], 5.0)
        self.assertEqual(by_inv['202608'], 1.0)

    def test_iter_merged_billing_items_flushes_on_start_date_change(self):
        """Different start_dates must not stay buffered forever."""
        imp = self._importer()
        hour1 = datetime(2026, 7, 1, 1, 0)
        hour2 = datetime(2026, 7, 1, 2, 0)
        base = {
            'resource_id': 'r1',
            'resource_hash': None,
            'cloud_account_id': 'ca-gcp-1',
            'sku': 'sku',
            'service': 'Compute Engine',
            'cost': 1.0,
            'usage_amount': 1.0,
            'usage_amount_in_pricing_units': 1.0,
            'credits': 0,
        }

        def _identity(_self, row):
            return row

        with patch.object(
                GcpReportImporter, '_row_to_dict', new=_identity):
            merged = list(imp._iter_merged_billing_items([
                dict(base, start_date=hour1, cost=1.0),
                dict(base, start_date=hour1, cost=2.0),
                dict(base, start_date=hour2, cost=4.0),
                dict(base, start_date=hour2, cost=1.0),
            ]))
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]['start_date'], hour1)
        self.assertEqual(merged[0]['cost'], 3.0)
        self.assertEqual(merged[1]['start_date'], hour2)
        self.assertEqual(merged[1]['cost'], 5.0)


@unittest.skipIf(
    GcpReportImporter is None,
    'diworker runtime deps missing: %s' % _IMPORT_ERROR)
class TestGcpDetectPeriodStart(unittest.TestCase):
    def _importer(self, last_import_at, last_exp_date):
        from unittest.mock import PropertyMock

        imp = GcpReportImporter.__new__(GcpReportImporter)
        object.__setattr__(imp, 'cloud_acc_id', 'ca-gcp-1')
        object.__setattr__(imp, 'period_start', None)
        ca_patch = patch.object(
            GcpReportImporter,
            'cloud_acc',
            new_callable=PropertyMock,
            return_value={'last_import_at': last_import_at})
        ca_patch.start()
        self.addCleanup(ca_patch.stop)
        imp.get_last_import_date = MagicMock(return_value=last_exp_date)
        imp.remove_raw_expenses_from_period_start = MagicMock()
        imp._clear_clickhouse_expenses_from_period_start = MagicMock()
        object.__setattr__(imp, 'recalculate', False)
        return imp

    def test_same_month_incremental_uses_expense_lookback(self):
        last_import_at = int(datetime(
            2026, 8, 12, 6, 0, tzinfo=timezone.utc).timestamp())
        last_exp = datetime(2026, 8, 12, 15, 0)
        imp = self._importer(last_import_at, last_exp)
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=datetime(2026, 8, 12, 16, 0)):
            imp.detect_period_start()
        self.assertEqual(
            imp.period_start,
            datetime(2026, 8, 9, 0, 0))
        imp.remove_raw_expenses_from_period_start.assert_not_called()
        imp._clear_clickhouse_expenses_from_period_start.assert_not_called()

    def test_same_month_reimport_honors_rewound_cursor(self):
        """Rewind to Aug 1 must reload from Aug 1, not last_exp-3."""
        last_import_at = int(datetime(
            2026, 8, 1, tzinfo=timezone.utc).timestamp())
        last_exp = datetime(2026, 8, 12, 15, 0)
        imp = self._importer(last_import_at, last_exp)
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=datetime(2026, 8, 12, 16, 0)):
            imp.detect_period_start()
        self.assertEqual(
            imp.period_start,
            datetime(2026, 8, 1, 0, 0))
        imp.remove_raw_expenses_from_period_start.assert_called_once_with(
            'ca-gcp-1')
        imp._clear_clickhouse_expenses_from_period_start.assert_called_once()

    def test_prior_month_rewind_honors_cursor(self):
        """Reload from June 1 must start at June 1 even if August raw exists."""
        last_import_at = int(datetime(
            2026, 6, 1, tzinfo=timezone.utc).timestamp())
        last_exp = datetime(2026, 8, 14, 14, 0)
        imp = self._importer(last_import_at, last_exp)
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=datetime(2026, 8, 15, 1, 0)):
            imp.detect_period_start()
        self.assertEqual(
            imp.period_start,
            datetime(2026, 6, 1, 0, 0))
        imp.remove_raw_expenses_from_period_start.assert_called_once_with(
            'ca-gcp-1')
        imp._clear_clickhouse_expenses_from_period_start.assert_called_once()

    def test_recalculate_rewound_cursor_clears_ch_not_mongo(self):
        last_import_at = int(datetime(
            2026, 8, 1, tzinfo=timezone.utc).timestamp())
        last_exp = datetime(2026, 8, 12, 15, 0)
        imp = self._importer(last_import_at, last_exp)
        object.__setattr__(imp, 'recalculate', True)
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=datetime(2026, 8, 12, 16, 0)):
            imp.detect_period_start()
        self.assertEqual(
            imp.period_start,
            datetime(2026, 8, 1, 0, 0))
        imp.remove_raw_expenses_from_period_start.assert_not_called()
        imp._clear_clickhouse_expenses_from_period_start.assert_called_once()


@unittest.skipIf(
    GcpReportImporter is None,
    'diworker runtime deps missing: %s' % _IMPORT_ERROR)
class TestGcpClickhouseTargetReconcile(unittest.TestCase):
    def _importer(self):
        from unittest.mock import PropertyMock

        imp = GcpReportImporter.__new__(GcpReportImporter)
        object.__setattr__(imp, 'cloud_acc_id', 'ca-gcp-1')
        adapter = MagicMock()
        adapter.is_virtual_billing_project = False
        object.__setattr__(imp, '_cloud_adapter', adapter)
        object.__setattr__(imp, 'clickhouse_cl', MagicMock())
        object.__setattr__(imp, 'mongo_resources', MagicMock())
        object.__setattr__(imp, 'report_import_id', 'ri-ch-1')
        object.__setattr__(imp, 'rest_cl', MagicMock())
        ca_patch = patch.object(
            GcpReportImporter,
            'cloud_acc',
            new_callable=PropertyMock,
            return_value={'last_import_at': 1})
        ca_patch.start()
        self.addCleanup(ca_patch.stop)
        return imp

    def test_merge_adds_target_columns(self):
        imp = self._importer()
        imp._last_reconciliation = [{
            'resource_type': 'Instance',
            'source_sum': 10.0,
            'local_sum': 10.0,
            'source_count': 2,
            'local_count': 2,
            'delta': 0.0,
            'status': 'ok',
        }]
        merged = imp._merge_target_into_reconciliation({
            'Instance': {'cost': 10.0, 'resource_count': 1},
        })
        self.assertEqual(merged[0]['target_sum'], 10.0)
        self.assertEqual(merged[0]['target_count'], 1)
        self.assertEqual(merged[0]['status'], 'ok')

    def test_merge_folds_cloud_composer_alias_into_composer(self):
        imp = self._importer()
        imp._last_reconciliation = [
            {
                'resource_type': 'Cloud Composer',
                'source_sum': 254.54,
                'local_sum': 0.0,
                'source_count': 4,
                'local_count': 0,
            },
            {
                'resource_type': 'Composer',
                'source_sum': 262.22,
                'local_sum': 262.22,
                'source_count': 6,
                'local_count': 6,
            },
        ]
        merged = imp._merge_target_into_reconciliation({
            'Composer': {'cost': 262.22, 'resource_count': 6},
            'Cloud Composer': {'cost': 0.0, 'resource_count': 0},
        })
        types = [row['resource_type'] for row in merged]
        self.assertEqual(types.count('Composer'), 1)
        self.assertNotIn('Cloud Composer', types)
        composer = merged[0]
        self.assertEqual(composer['resource_type'], 'Composer')
        self.assertAlmostEqual(composer['source_sum'], 516.76)
        self.assertAlmostEqual(composer['local_sum'], 262.22)
        self.assertAlmostEqual(composer['target_sum'], 262.22)

    def test_mongo_numeric_vm_folds_gpu_and_network_into_instance(self):
        """Compute Engine leftover SKUs on a GCP numeric VM id are Instance."""
        imp = self._importer()
        imp.mongo_raw = MagicMock()
        captured = {}

        def aggregate(pipeline, allowDiskUse=True):
            captured['pipeline'] = pipeline
            return [
                {
                    '_id': {
                        'rid': '3688322087841111737',
                        'sku': 'N2 Instance Core running in Americas',
                        'service': 'Compute Engine',
                        'cost_type': 'regular',
                    },
                    'cost': 111.53,
                    'tags': {},
                    'resource_hash': None,
                    'resource_id': '3688322087841111737',
                },
                {
                    '_id': {
                        'rid': '3688322087841111737',
                        'sku': 'Nvidia Tesla T4 GPU running in Americas',
                        'service': 'Compute Engine',
                        'cost_type': 'regular',
                    },
                    'cost': 107.90,
                    'tags': {},
                    'resource_hash': None,
                    'resource_id': '3688322087841111737',
                },
                {
                    '_id': {
                        'rid': '3688322087841111737',
                        'sku': 'Network Inter Region Data Transfer Out from Americas to Netherlands',
                        'service': 'Compute Engine',
                        'cost_type': 'regular',
                    },
                    'cost': 0.82,
                    'tags': {},
                    'resource_hash': None,
                    'resource_id': '3688322087841111737',
                },
            ]

        imp.mongo_raw.aggregate.side_effect = aggregate
        by_type = imp._mongo_month_by_resource_type(
            datetime(2026, 8, 1), datetime(2026, 9, 1))
        self.assertAlmostEqual(by_type['Instance']['cost'], 220.25)
        self.assertNotIn('Compute Engine', by_type)
        self.assertEqual(
            imp._reconcile_type_by_billing_id['3688322087841111737'],
            'Instance')

    def test_clickhouse_skips_sku_twin_not_in_month_billing_map(self):
        imp = self._importer()
        imp._reconcile_type_by_billing_id = {
            '962315076699295255': 'Instance',
        }
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
            ('id-numeric', 1.9211),
            ('id-sku-twin', 3.8422),
        ])
        imp.mongo_resources.find.return_value = [
            {'_id': 'id-numeric', 'resource_type': 'Instance',
             'cloud_resource_id': '962315076699295255'},
            {'_id': 'id-sku-twin', 'resource_type': 'Instance',
             'cloud_resource_id': '5CE5-087B-6C3C'},
        ]
        by_type = imp._clickhouse_month_by_resource_type(
            datetime(2026, 8, 1), datetime(2026, 9, 1))
        self.assertAlmostEqual(by_type['Instance']['cost'], 1.9211)
        self.assertEqual(by_type['Instance']['resource_count'], 1)

    def test_reconcile_type_uses_collapse_cases_not_sku(self):
        imp = self._importer()
        object.__setattr__(imp, '_unique_gke_cluster', lambda refresh=False: (
            'pf-da-shared-nonprod-gke'))
        self.assertEqual(
            imp._reconcile_resource_type({
                'sku': 'N1 Instance Core',
                'service': 'Compute Engine',
                'cost_type': 'regular',
                'tags': {'goog-k8s-cluster-name': 'pf-da-shared-nonprod-gke'},
            }),
            'GKE')
        self.assertEqual(
            imp._reconcile_resource_type({
                'sku': 'N1 Instance Core',
                'service': 'Compute Engine',
                'cost_type': 'regular',
                'tags': {'goog-dataproc-cluster-uuid': 'c2fb4fff-3fe3-46da-a8f5-77db54ee2f29'},
            }),
            'Dataproc')
        self.assertEqual(
            imp._reconcile_resource_type({
                'sku': 'N1 Instance Core',
                'service': 'Compute Engine',
                'cost_type': 'regular',
                'resource_id': 'composer/6530bd7f-6ca8-4a5d-b528-54436c176f9a',
            }),
            'Composer')
        self.assertEqual(
            imp._reconcile_resource_type({
                'sku': 'N1 Instance Core',
                'service': 'Compute Engine',
                'cost_type': 'regular',
                'resource_id': '962315076699295255',
            }),
            'Instance')
        self.assertEqual(
            imp._reconcile_resource_type({
                'sku': 'Nvidia Tesla T4 GPU running in Americas',
                'service': 'Compute Engine',
                'cost_type': 'regular',
                'resource_id': '3688322087841111737',
            }),
            'Instance')
        self.assertEqual(
            imp._reconcile_resource_type({
                'sku': 'Nvidia Tesla T4 GPU running in Americas',
                'service': 'Compute Engine',
                'cost_type': 'regular',
                'resource_id': 'C2F8-6C2C-1847',
            }),
            'Compute Engine')
        self.assertEqual(
            imp._reconcile_resource_type({
                'sku': 'Static Ip Charge',
                'service': 'Compute Engine',
                'cost_type': 'regular',
                'resource_id': '962315076699295255',
            }),
            'Instance')
        self.assertEqual(
            imp._reconcile_resource_type({
                'sku': 'Static Ip Charge',
                'service': 'Compute Engine',
                'cost_type': 'regular',
                'resource_id': '9E4E-F9A7-5EAE',
            }),
            'IP Address')
        self.assertEqual(
            imp._reconcile_resource_type({
                'sku': 'Small Cloud Composer Environment Fee (us-central1).',
                'service': 'Cloud Composer',
                'cost_type': 'regular',
            }),
            'Composer')
        self.assertEqual(
            imp._reconcile_resource_type({
                'sku': 'Network Egress',
                'service': 'Cloud Storage',
                'cost_type': 'regular',
            }),
            'Bucket')
        self.assertEqual(
            imp._reconcile_resource_type({
                'sku': 'Cloud SQL for MySQL: Regional - Micro instance in Americas',
                'service': 'Cloud SQL',
                'cost_type': 'regular',
                'resource_id': 'dq-monitor-db',
            }),
            'Cloud SQL')

    def test_clickhouse_follows_billing_type_not_discovery_label(self):
        """Mismatched discovery labels follow Mongo billing; matching stay."""
        imp = self._importer()
        imp._reconcile_type_by_billing_id = {
            'dq-monitor-db': 'Instance',
            'cfg-db': 'Cloud SQL',
            'gke/cluster': 'GKE',
            'sku-ce': 'Compute Engine',
        }
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
            ('id-sql-extra', 8.13),
            ('id-sql', 41.0),
            ('id-gke', 100.0),
            ('id-ce', 5.17),
        ])
        imp.mongo_resources.find.return_value = [
            {'_id': 'id-sql-extra', 'resource_type': 'Cloud SQL',
             'cloud_resource_id': 'dq-monitor-db'},
            {'_id': 'id-sql', 'resource_type': 'Cloud SQL',
             'cloud_resource_id': 'cfg-db'},
            {'_id': 'id-gke', 'resource_type': 'GKE',
             'cloud_resource_id': 'gke/cluster'},
            {'_id': 'id-ce', 'resource_type': 'Instance',
             'cloud_resource_hash': 'sku-ce'},
        ]
        by_type = imp._clickhouse_month_by_resource_type(
            datetime(2026, 8, 1), datetime(2026, 9, 1))
        sql = imp.clickhouse_cl.query.call_args[0][0]
        self.assertIn('FROM expenses FINAL', sql)
        self.assertIn('HAVING abs(cost)', sql)
        self.assertNotIn('HAVING abs(sum(', sql)
        self.assertAlmostEqual(by_type['Instance']['cost'], 8.13)
        self.assertAlmostEqual(by_type['Cloud SQL']['cost'], 41.0)
        self.assertAlmostEqual(by_type['GKE']['cost'], 100.0)
        self.assertAlmostEqual(by_type['Compute Engine']['cost'], 5.17)
        self.assertNotIn('Cloud Storage', by_type)

    def test_get_clickhouse_expenses_reads_unmerged_rows(self):
        imp = self._importer()
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[])
        imp.get_clickhouse_expenses(
            datetime(2026, 7, 1), datetime(2026, 7, 31),
            ['res-1'], 'ca-gcp-1')
        sql = imp.clickhouse_cl.query.call_args[0][0]
        self.assertIn('FROM expenses', sql)
        self.assertNotIn('FROM expenses FINAL', sql)
        self.assertNotIn("invoice_month = ''", sql)

    def test_clickhouse_cloud_storage_doc_aliases_to_bucket(self):
        imp = self._importer()
        imp._reconcile_type_by_billing_id = {}
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
            ('id-b', 0.15),
        ])
        imp.mongo_resources.find.return_value = [
            {'_id': 'id-b', 'resource_type': 'Cloud Storage',
             'cloud_resource_id': 'bucket-1'},
        ]
        by_type = imp._clickhouse_month_by_resource_type(
            datetime(2026, 8, 1), datetime(2026, 9, 1))
        self.assertAlmostEqual(by_type['Bucket']['cost'], 0.15)
        self.assertNotIn('Cloud Storage', by_type)

    def test_clickhouse_cloud_composer_doc_aliases_to_composer(self):
        imp = self._importer()
        imp._reconcile_type_by_billing_id = {}
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
            ('id-cc', 94.97),
        ])
        imp.mongo_resources.find.return_value = [
            {'_id': 'id-cc', 'resource_type': 'Cloud Composer',
             'cloud_resource_id': '6EA4-3652-173E'},
        ]
        by_type = imp._clickhouse_month_by_resource_type(
            datetime(2026, 8, 1), datetime(2026, 9, 1))
        self.assertAlmostEqual(by_type['Composer']['cost'], 94.97)
        self.assertNotIn('Cloud Composer', by_type)

    def test_mongo_month_collapsed_matches_clickhouse_gke(self):
        imp = self._importer()
        imp.mongo_raw = MagicMock()
        imp.mongo_raw.aggregate.return_value = [{
            '_id': 'gke/pf-da-shared-nonprod-gke',
            'cost': 160.01,
            'sku': 'N1 Instance Core',
            'service': 'Compute Engine',
            'cost_type': 'regular',
            'tags': {'goog-k8s-cluster-name': 'pf-da-shared-nonprod-gke'},
            'resource_id': 'gke/pf-da-shared-nonprod-gke',
            'resource_hash': None,
        }]
        mongo_by_type = imp._mongo_month_by_resource_type(
            datetime(2026, 8, 1), datetime(2026, 9, 1))
        self.assertIn('GKE', mongo_by_type)
        self.assertNotIn('Instance', mongo_by_type)
        self.assertAlmostEqual(mongo_by_type['GKE']['cost'], 160.01)
        imp._last_reconciliation = [{
            'resource_type': 'GKE',
            'source_sum': 160.01,
            'local_sum': mongo_by_type['GKE']['cost'],
            'source_count': 1,
            'local_count': mongo_by_type['GKE']['resource_count'],
            'delta': 0.0,
            'status': 'ok',
        }]
        merged = imp._merge_target_into_reconciliation({
            'GKE': {'cost': 160.01, 'resource_count': 1},
        })
        self.assertEqual(merged[0]['resource_type'], 'GKE')
        self.assertEqual(merged[0]['status'], 'ok')
        self.assertAlmostEqual(merged[0]['target_sum'], 160.01)

    def test_stream_gke_tagged_core_is_gke_not_instance(self):
        imp = self._importer()
        now = datetime(2026, 8, 15, 1, 0, tzinfo=timezone.utc)
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp._reset_stream_month_totals(datetime(2026, 8, 1))
            imp._accumulate_stream_month_row({
                'start_date': datetime(2026, 8, 2),
                'sku': 'N1 Instance Core',
                'service': 'Compute Engine',
                'cost_type': 'regular',
                'cost': 12.0,
                'resource_id': 'gke/pf-da-shared-nonprod-gke',
                'tags': {'goog-k8s-cluster-name': 'pf-da-shared-nonprod-gke'},
                'resource_hash': None,
            })
            source = imp._stream_month_source_by_type()
        self.assertIn('GKE', source)
        self.assertNotIn('Instance', source)
        self.assertAlmostEqual(source['GKE']['billed_sum'], 12.0)

    def test_clickhouse_vs_mongo_mismatch_warns_does_not_fail(self):
        """CH vs Mongo is a CA warning; Billing Reconciliation still publishes."""
        imp = self._importer()
        imp._last_bq_billed_sum = 10.0
        imp._last_written_cost_sum = 10.0
        imp._last_reconciliation = [{
            'resource_type': 'Instance',
            'source_sum': 10.0,
            'local_sum': 10.0,
            'source_count': 1,
            'local_count': 1,
            'status': 'ok',
        }]
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
            ('res-1', 5.0),
        ])
        imp.mongo_resources.find.return_value = [
            {'_id': 'res-1', 'resource_type': 'Instance'},
        ]
        now = datetime(2026, 8, 15, 1, 0)
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp._reconcile_clickhouse_target()
        warning = imp._clickhouse_reconcile_warning
        self.assertIn('ClickHouse target cost mismatch', warning)
        self.assertIn('ClickHouse=5', warning)
        self.assertIn('Mongo=10', warning)
        details = imp.get_import_details()
        self.assertEqual(details['target_sum'], 5.0)
        self.assertEqual(details['reconciliation'][0]['status'], 'mismatch')
        imp.rest_cl.report_import_update.assert_called()
        published = imp.rest_cl.report_import_update.call_args[0][1]
        self.assertEqual(published['details']['target_sum'], 5.0)
        self.assertEqual(
            published['details']['reconciliation'][0]['status'], 'mismatch')

    def test_update_cloud_import_time_writes_clickhouse_warning(self):
        imp = self._importer()
        imp._clickhouse_reconcile_warning = (
            'GCP current-month reconcile for ca-gcp-1: ClickHouse target '
            'cost mismatch — BQ billed=10.000000 Mongo=10.000000 '
            'ClickHouse=5.000000')
        with patch(
                'diworker.diworker.importers.base.BaseReportImporter'
                '.update_cloud_import_time') as base_update, patch.object(
                    imp, '_clear_raw_checkpoint'):
            imp.update_cloud_import_time(123)
        base_update.assert_called_once_with(123)
        imp.rest_cl.cloud_account_update.assert_called_once_with(
            'ca-gcp-1',
            {
                'last_import_attempt_at': 123,
                'last_import_attempt_error': (
                    imp._clickhouse_reconcile_warning[:255]),
            })

    def test_clickhouse_match_does_not_fail(self):
        imp = self._importer()
        imp._last_bq_billed_sum = 10.0
        imp._last_written_cost_sum = 10.0
        imp._last_reconciliation = [{
            'resource_type': 'Instance',
            'source_sum': 10.0,
            'local_sum': 10.0,
            'source_count': 1,
            'local_count': 1,
            'status': 'ok',
        }]
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
            ('res-1', 10.0),
        ])
        imp.mongo_resources.find.return_value = [
            {'_id': 'res-1', 'resource_type': 'Instance'},
        ]
        now = datetime(2026, 8, 15, 1, 0)
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp._reconcile_clickhouse_target()
        details = imp.get_import_details()
        self.assertEqual(details['target_sum'], 10.0)
        self.assertEqual(details['reconciliation'][0]['status'], 'ok')
        self.assertFalse(getattr(imp, '_clickhouse_reconcile_warning', None))

    def test_skips_without_clickhouse_client(self):
        imp = self._importer()
        object.__setattr__(imp, 'clickhouse_cl', None)
        imp._reconcile_clickhouse_target()

    def test_virtual_ca_filters_invoice_month(self):
        imp = self._importer()
        imp.cloud_adapter.is_virtual_billing_project = True
        sql, params = imp._clickhouse_month_filter(
            datetime(2026, 8, 1), datetime(2026, 9, 1))
        self.assertEqual(sql, 'invoice_month = %(invoice_month)s')
        self.assertEqual(params['invoice_month'], '202608')


@unittest.skipIf(
    GcpReportImporter is None,
    'diworker runtime deps missing: %s' % _IMPORT_ERROR)
class TestGcpIncrementalLateRowsVsMongo(unittest.TestCase):
    """Late BQ exports must not wipe historical days already in Mongo.

    Concrete failure (ci-retail-media-prod / EC7A, 2026-08-01 06:00):
    early partition wrote ~$25.93; a later partition added ~$1.33 late rows.
    Full-day rebuild with ±60 partitions is too expensive. Incremental reads
    only fresh partitions: in-window days are wipe+insert; backdated rows
    merge by export_time into their usage_start day without wiping that day
    and without $set of a partition slice over the merged unique-key total.
    """

    SKU = 'EC7A-EF05-537E'
    HOUR = datetime(2026, 8, 1, 6, 0, tzinfo=timezone.utc)
    EARLY_COST = 25.93
    LATE_COST = 1.33
    FULL_COST = 27.26

    def _billing_row(self, cost, start_date=None, export_time=None):
        start = start_date or self.HOUR
        return _FakeRow(
            service='Compute Engine',
            start_date=start,
            end_date=start + timedelta(hours=1),
            cost=cost,
            cost_type='regular',
            location={'region': 'us-central1'},
            currency='USD',
            currency_conversion_rate=1.0,
            sku='N1 Instance Core',
            sku_id=self.SKU,
            tags=[],
            usage_amount=1.0,
            usage_unit='hour',
            usage_amount_in_pricing_units=1.0,
            usage_pricing_unit='hour',
            system_tags=[],
            credits=[],
            adjustment_info=None,
            export_time=export_time,
        )

    def _importer(self, store):
        from unittest.mock import PropertyMock

        imp = GcpReportImporter.__new__(GcpReportImporter)
        object.__setattr__(imp, 'cloud_acc_id', 'ca-retail-prod')
        # Incremental partition lookback window (fresh partitions only).
        object.__setattr__(
            imp,
            'period_start',
            datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc))
        object.__setattr__(imp, '_raw_insert_only', False)
        object.__setattr__(imp, 'imported_raw_dates_map', defaultdict(dict))
        object.__setattr__(imp, 'report_identity', 1)
        adapter = MagicMock()
        adapter.is_virtual_billing_project = False
        adapter.get_usage_month_by_resource_type.side_effect = (
            _bq_month_rows_matching_store(store))
        object.__setattr__(imp, '_cloud_adapter', adapter)
        object.__setattr__(imp, 'mongo_raw', store)
        mongo_resources = MagicMock()
        mongo_resources.distinct.return_value = []
        object.__setattr__(imp, 'mongo_resources', mongo_resources)
        object.__setattr__(imp, '_discovery_resource_ids', None)
        object.__setattr__(imp, 'report_import_id', 'ri-inc-1')
        object.__setattr__(imp, 'rest_cl', MagicMock())
        checkpoints = MagicMock()
        checkpoints.find_one.return_value = None
        object.__setattr__(imp, '_checkpoints', checkpoints)
        ca_patch = patch.object(
            GcpReportImporter,
            'cloud_acc',
            new_callable=PropertyMock,
            return_value={'last_import_at': 1786517000})
        ca_patch.start()
        self.addCleanup(ca_patch.stop)
        return imp

    def _seed_existing_hour(self, store, cost, export_times=None):
        store.docs.append({
            'start_date': self.HOUR,
            'resource_id': self.SKU,
            'cloud_account_id': 'ca-retail-prod',
            'sku': 'N1 Instance Core',
            'service': 'Compute Engine',
            'cost': cost,
            'usage_amount': 1.0,
            'usage_amount_in_pricing_units': 1.0,
            'credits': 0,
            'export_times': list(export_times or []),
        })

    def test_late_slice_does_not_clobber_existing_mongo_cost(self):
        """Partition-slice upsert must not $set over a previously merged total."""
        store = _InMemoryRawExpenses()
        self._seed_existing_hour(store, self.EARLY_COST)
        imp = self._importer(store)
        imp._raw_insert_only = False

        late_only = imp._row_to_dict(self._billing_row(self.LATE_COST))
        GcpReportImporter.update_raw_records(imp, [late_only])

        self.assertEqual(
            store.find_cost(
                resource_id=self.SKU, start_date=self.HOUR),
            self.EARLY_COST)

    def test_new_export_time_adds_to_existing_mongo_cost(self):
        store = _InMemoryRawExpenses()
        self._seed_existing_hour(
            store, self.EARLY_COST, export_times=['t-early'])
        imp = self._importer(store)
        imp._raw_insert_only = False

        late_only = imp._row_to_dict(
            self._billing_row(self.LATE_COST, export_time='t-late'))
        GcpReportImporter.update_raw_records(imp, [late_only])

        self.assertAlmostEqual(
            store.find_cost(
                resource_id=self.SKU, start_date=self.HOUR),
            self.FULL_COST)

    def test_same_export_time_does_not_double_existing_mongo_cost(self):
        store = _InMemoryRawExpenses()
        self._seed_existing_hour(
            store, self.FULL_COST, export_times=['t-early', 't-late'])
        imp = self._importer(store)
        imp._raw_insert_only = False

        late_only = imp._row_to_dict(
            self._billing_row(self.LATE_COST, export_time='t-late'))
        GcpReportImporter.update_raw_records(imp, [late_only])

        self.assertAlmostEqual(
            store.find_cost(
                resource_id=self.SKU, start_date=self.HOUR),
            self.FULL_COST)

    def test_incremental_backdated_upserts_without_wiping_that_day(self):
        """Late row for Aug 1 in fresh partitions: upsert, leave other Aug 1 rows."""
        store = _InMemoryRawExpenses()
        self._seed_existing_hour(store, self.EARLY_COST)
        other_hour = datetime(2026, 8, 1, 7, 0, tzinfo=timezone.utc)
        store.docs.append({
            'start_date': other_hour,
            'resource_id': 'OTHER-SKU',
            'cloud_account_id': 'ca-retail-prod',
            'sku': 'Other',
            'service': 'Compute Engine',
            'cost': 9.99,
            'usage_amount': 1.0,
            'usage_amount_in_pricing_units': 1.0,
            'credits': 0,
        })
        imp = self._importer(store)
        in_window_hour = datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)
        part_start = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)
        part_end = datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc)
        job = MagicMock()
        job.result.return_value = iter([
            self._billing_row(self.LATE_COST),
            self._billing_row(1.0, start_date=in_window_hour),
        ])
        imp.cloud_adapter.get_usage.return_value = job
        now = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now), \
                patch(
                    'diworker.diworker.importers.base.retry_mongo_upsert',
                    side_effect=lambda fn, *a, **k: fn(*a, **k)), \
                patch(
                    'diworker.diworker.importers.gcp.retry_mongo_upsert',
                    side_effect=lambda fn, *a, **k: fn(*a, **k)):
            imp.load_raw_data()

        imp.cloud_adapter.get_usage.assert_called_once_with(
            part_start, part_end)
        self.assertEqual(
            store.find_cost(resource_id='OTHER-SKU', start_date=other_hour),
            9.99)
        self.assertEqual(
            store.find_cost(resource_id=self.SKU, start_date=self.HOUR),
            self.EARLY_COST)
        self.assertEqual(
            store.find_cost(resource_id=self.SKU, start_date=in_window_hour),
            1.0)
        details = imp.get_import_details()
        self.assertAlmostEqual(
            details['source_sum'], self.EARLY_COST + 9.99 + 1.0)
        self.assertAlmostEqual(details['local_sum'], details['source_sum'])
        self.assertTrue(details['reconciliation'])
        self.assertTrue(
            all(row['status'] == 'ok' for row in details['reconciliation']))

    def test_incremental_inserts_brand_new_usage_day(self):
        """New costs for today in fresh partitions → insert, no prior doc."""
        store = _InMemoryRawExpenses()
        imp = self._importer(store)
        hour = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
        job = MagicMock()
        job.result.return_value = iter([self._billing_row(4.5, start_date=hour)])
        imp.cloud_adapter.get_usage.return_value = job
        now = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now), \
                patch(
                    'diworker.diworker.importers.base.retry_mongo_upsert',
                    side_effect=lambda fn, *a, **k: fn(*a, **k)), \
                patch(
                    'diworker.diworker.importers.gcp.retry_mongo_upsert',
                    side_effect=lambda fn, *a, **k: fn(*a, **k)):
            imp.load_raw_data()

        self.assertEqual(len(store.docs), 1)
        self.assertEqual(
            store.find_cost(resource_id=self.SKU, start_date=hour),
            4.5)

    def test_incremental_untouched_day_left_alone_when_not_in_partitions(self):
        """July hour stays put when the import window does not cover July."""
        store = _InMemoryRawExpenses()
        july_hour = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
        store.docs.append({
            'start_date': july_hour,
            'resource_id': self.SKU,
            'cloud_account_id': 'ca-retail-prod',
            'sku': 'N1 Instance Core',
            'service': 'Compute Engine',
            'cost': 9.99,
            'usage_amount': 1.0,
            'usage_amount_in_pricing_units': 1.0,
            'credits': 0,
        })
        imp = self._importer(store)
        hour = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
        job = MagicMock()
        job.result.return_value = iter([self._billing_row(1.0, start_date=hour)])
        imp.cloud_adapter.get_usage.return_value = job
        now = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now), \
                patch(
                    'diworker.diworker.importers.base.retry_mongo_upsert',
                    side_effect=lambda fn, *a, **k: fn(*a, **k)), \
                patch(
                    'diworker.diworker.importers.gcp.retry_mongo_upsert',
                    side_effect=lambda fn, *a, **k: fn(*a, **k)):
            imp.load_raw_data()

        self.assertEqual(
            store.find_cost(resource_id=self.SKU, start_date=july_hour),
            9.99)
        self.assertEqual(
            store.find_cost(resource_id=self.SKU, start_date=hour),
            1.0)
        self.assertIsNone(imp._backdated_clean_from())

    def test_incremental_backdated_marks_usage_day_for_clean(self):
        store = _InMemoryRawExpenses()
        self._seed_existing_hour(
            store, self.EARLY_COST, export_times=['t-early'])
        imp = self._importer(store)
        in_window_hour = datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)
        job = MagicMock()
        job.result.return_value = iter([
            self._billing_row(self.LATE_COST, export_time='t-late'),
            self._billing_row(1.0, start_date=in_window_hour),
        ])
        imp.cloud_adapter.get_usage.return_value = job
        now = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)

        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now), \
                patch(
                    'diworker.diworker.importers.base.retry_mongo_upsert',
                    side_effect=lambda fn, *a, **k: fn(*a, **k)), \
                patch(
                    'diworker.diworker.importers.gcp.retry_mongo_upsert',
                    side_effect=lambda fn, *a, **k: fn(*a, **k)):
            imp.load_raw_data()

        self.assertEqual(
            imp._backdated_clean_from(),
            datetime(2026, 8, 1))
        self.assertIn(self.SKU, imp._backdated_resource_ids)

    def test_generate_clean_rewrites_clickhouse_for_backdated_day(self):
        """Late $inc outside period_start must still invert the stale CH day."""
        store = _InMemoryRawExpenses()
        self._seed_existing_hour(
            store, self.FULL_COST, export_times=['t-early', 't-late'])
        in_window = datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)
        store.docs.append({
            'start_date': in_window,
            'resource_id': self.SKU,
            'cloud_account_id': 'ca-retail-prod',
            'sku': 'N1 Instance Core',
            'service': 'Compute Engine',
            'cost': 1.0,
            'usage_amount': 1.0,
            'usage_amount_in_pricing_units': 1.0,
            'credits': 0,
        })
        store.docs.append({
            'start_date': datetime(2026, 8, 1, 7, 0, tzinfo=timezone.utc),
            'resource_id': 'OTHER-SKU',
            'cloud_account_id': 'ca-retail-prod',
            'sku': 'Other',
            'service': 'Compute Engine',
            'cost': 9.99,
            'usage_amount': 1.0,
            'usage_amount_in_pricing_units': 1.0,
            'credits': 0,
        })
        imp = self._importer(store)
        object.__setattr__(imp, '_backdated_usage_days', {
            datetime(2026, 8, 1)})
        object.__setattr__(imp, '_backdated_resource_ids', {self.SKU})
        object.__setattr__(imp, '_backdated_resource_hashes', set())
        object.__setattr__(imp, '_backdated_hour_keys', [{
            'start_date': self.HOUR,
            'sku': 'N1 Instance Core',
            'service': 'Compute Engine',
        }])
        for name in (
                '_rewrite_serverless_dataproc_raw_ids',
                '_rewrite_labeled_collapse_raw_ids',
                '_rewrite_detailed_collapse_raw_ids',
                '_rekey_stale_serverless_dataproc_resources',
                '_rekey_collapsed_labeled_resources',
                '_rekey_collapsed_sku_leftovers',
                '_rekey_detailed_collapse_leftovers',
                '_rekey_unlabeled_gke_pvc_resources',
                '_dedupe_collapsed_gcp_identity_resources',
                '_restore_miscollapsed_billing_sku_resources',
                '_retire_stale_serverless_dataproc_resources',
                '_negate_deleted_collapsed_clickhouse_expenses',
                '_refresh_resource_duplicates',
                '_reconcile_clickhouse_target'):
            setattr(imp, name, MagicMock())
        rid = 'mongo-sku-1'
        ch_day = datetime(2026, 8, 1)
        later_day = datetime(2026, 8, 10)
        imp.get_resource_info_map = MagicMock(return_value={self.SKU: {}})
        imp.create_resources_if_not_exist = MagicMock(return_value=[{
            'id': rid,
            'cloud_resource_id': self.SKU,
        }])
        def _ch_expenses(from_dt, to_dt, resource_ids, cloud_account_id):
            rows = [
                (rid, ch_day, '', self.EARLY_COST, 1),
                (rid, later_day, '', 1.0, 1),
            ]
            start = GcpReportImporter._clickhouse_lookup_date(from_dt)
            end = GcpReportImporter._clickhouse_lookup_date(to_dt)
            return [
                row for row in rows
                if start <= row[1] <= end
            ]

        imp.get_clickhouse_expenses = _ch_expenses
        imp.update_clickhouse_expenses = MagicMock()
        imp.update_resource_expense_info = MagicMock()
        imp.log_import_phase = MagicMock()

        imp.generate_clean_records()

        payload = []
        for call in imp.update_clickhouse_expenses.call_args_list:
            payload.extend(call[0][0])
        aug1 = [row for row in payload if row[2] == ch_day]
        later = [row for row in payload if row[2] == later_day]
        self.assertEqual(
            sorted((row[3], row[4]) for row in aug1),
            [(self.EARLY_COST, -1), (self.FULL_COST, 1)])
        self.assertEqual(later, [])

    def test_generate_clean_does_not_reclean_untouched_resource(self):
        store = _InMemoryRawExpenses()
        self._seed_existing_hour(
            store, self.FULL_COST, export_times=['t-early', 't-late'])
        store.docs.append({
            'start_date': datetime(2026, 8, 1, 7, 0, tzinfo=timezone.utc),
            'resource_id': 'OTHER-SKU',
            'cloud_account_id': 'ca-retail-prod',
            'sku': 'Other',
            'service': 'Compute Engine',
            'cost': 9.99,
            'usage_amount': 1.0,
            'usage_amount_in_pricing_units': 1.0,
            'credits': 0,
        })
        imp = self._importer(store)
        object.__setattr__(imp, '_backdated_usage_days', {
            datetime(2026, 8, 1)})
        object.__setattr__(imp, '_backdated_resource_ids', {self.SKU})
        object.__setattr__(imp, '_backdated_resource_hashes', set())
        object.__setattr__(imp, '_backdated_hour_keys', [{
            'start_date': self.HOUR,
            'sku': 'N1 Instance Core',
            'service': 'Compute Engine',
        }])
        for name in (
                '_rewrite_serverless_dataproc_raw_ids',
                '_rewrite_labeled_collapse_raw_ids',
                '_rewrite_detailed_collapse_raw_ids',
                '_rekey_stale_serverless_dataproc_resources',
                '_rekey_collapsed_labeled_resources',
                '_rekey_collapsed_sku_leftovers',
                '_rekey_detailed_collapse_leftovers',
                '_rekey_unlabeled_gke_pvc_resources',
                '_dedupe_collapsed_gcp_identity_resources',
                '_restore_miscollapsed_billing_sku_resources',
                '_retire_stale_serverless_dataproc_resources',
                '_negate_deleted_collapsed_clickhouse_expenses',
                '_refresh_resource_duplicates',
                '_reconcile_clickhouse_target'):
            setattr(imp, name, MagicMock())
        cleaned_ids = []

        def _capture(_ca, chunk, unique_id_field='resource_id'):
            cleaned_ids.extend(chunk.keys())

        imp.save_clean_expenses = _capture
        imp.generate_clean_records()
        self.assertIn(self.SKU, cleaned_ids)
        self.assertNotIn('OTHER-SKU', cleaned_ids)


@unittest.skipIf(
    GcpReportImporter is None,
    'diworker runtime deps missing: %s' % _IMPORT_ERROR)
class TestGcpVirtualInvoiceMonth(unittest.TestCase):
    def _importer(self, virtual=True):
        imp = GcpReportImporter.__new__(GcpReportImporter)
        object.__setattr__(imp, 'cloud_acc_id', 'ca-gcp-virtual')
        adapter = MagicMock()
        adapter.is_virtual_billing_project = virtual
        adapter.zone_region.side_effect = lambda z: z
        object.__setattr__(imp, '_cloud_adapter', adapter)
        mongo_resources = MagicMock()
        mongo_resources.distinct.return_value = []
        object.__setattr__(imp, 'mongo_resources', mongo_resources)
        object.__setattr__(imp, '_discovery_resource_ids', None)
        return imp

    def test_unique_fields_include_invoice_month_for_virtual(self):
        imp = self._importer(virtual=True)
        self.assertIn('invoice_month', imp.get_unique_field_list())

    def test_unique_fields_include_invoice_month_for_real_project(self):
        imp = self._importer(virtual=False)
        self.assertIn('invoice_month', imp.get_unique_field_list())

    def test_row_to_dict_keeps_usage_dates_for_virtual(self):
        imp = self._importer(virtual=True)
        start = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
        end = datetime(2026, 7, 31, 13, 0, tzinfo=timezone.utc)
        row = _FakeRow(
            service='Compute Engine',
            start_date=start,
            end_date=end,
            invoice_month='202608',
            cost=10.0,
            location={'region': 'us-central1'},
            sku='Flexible CUD',
            sku_id='B22F-51BE-D599',
            tags=[],
            system_tags=[],
            credits=[],
        )
        item = imp._row_to_dict(row)
        self.assertEqual(item['start_date'], start)
        self.assertEqual(item['end_date'], end)
        self.assertEqual(item['invoice_month'], '202608')
        self.assertEqual(item['resource_id'], 'B22F-51BE-D599')

    def test_row_to_dict_keeps_usage_dates_for_real_project(self):
        imp = self._importer(virtual=False)
        start = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        row = _FakeRow(
            service='Compute Engine',
            start_date=start,
            end_date=datetime(2026, 8, 1, 13, 0, tzinfo=timezone.utc),
            invoice_month='202607',
            cost=1.0,
            location={'region': 'us-central1'},
            sku='Core',
            sku_id='sku-1',
            tags=[],
            system_tags=[],
            credits=[],
        )
        item = imp._row_to_dict(row)
        self.assertEqual(item['start_date'], start)
        self.assertEqual(item['invoice_month'], '202607')

    def test_mongo_month_match_uses_invoice_month_for_virtual(self):
        imp = self._importer(virtual=True)
        match = imp._mongo_month_match(
            datetime(2026, 8, 1), datetime(2026, 9, 1))
        self.assertEqual(match['invoice_month'], '202608')
        self.assertNotIn('start_date', match)

    def test_mongo_month_match_uses_usage_start_for_real_project(self):
        imp = self._importer(virtual=False)
        match = imp._mongo_month_match(
            datetime(2026, 8, 1), datetime(2026, 9, 1))
        self.assertEqual(
            match['start_date'],
            {'$gte': datetime(2026, 8, 1), '$lt': datetime(2026, 9, 1)})
        self.assertNotIn('invoice_month', match)

    def _virtual_service_importer(self, service):
        from tools.cloud_adapter.clouds.gcp import Gcp

        imp = self._importer(virtual=True)
        adapter = imp.cloud_adapter
        adapter.project_id = Gcp.virtual_service_project_id(service)
        adapter.parse_virtual_service = Gcp.parse_virtual_service
        return imp

    def test_stream_cache_uses_invoice_month_for_compute_engine_virtual(self):
        """July CUD billed on August invoice must count in CE virtual source."""
        imp = self._virtual_service_importer('Compute Engine')
        now = datetime(2026, 8, 15, 16, 0, tzinfo=timezone.utc)
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp._reset_stream_month_totals(datetime(2026, 6, 1))
            imp._accumulate_stream_month_row({
                'start_date': datetime(2026, 7, 31, 12, 0),
                'invoice_month': '202608',
                'sku': 'Compute Flexible Committed Use Discounts - 3 Year',
                'service': 'Compute Engine',
                'cost_type': 'regular',
                'cost': -1424.365744,
                'resource_id': 'B22F-51BE-D599',
            })
            imp._accumulate_stream_month_row({
                'start_date': datetime(2026, 8, 2, 12, 0),
                'invoice_month': '202608',
                'sku': 'Compute Flexible Committed Use Discounts - 3 Year',
                'service': 'Compute Engine',
                'cost_type': 'regular',
                'cost': -202.724459,
                'resource_id': 'B22F-51BE-D599',
            })
            imp._accumulate_stream_month_row({
                'start_date': datetime(2026, 8, 2, 12, 0),
                'invoice_month': '202607',
                'sku': 'N1 Instance Core',
                'service': 'Compute Engine',
                'cost_type': 'regular',
                'cost': 2002.21,
                'resource_id': 'sku-core',
            })
            source = imp._stream_month_source_by_type()
        total = sum(v['billed_sum'] for v in source.values())
        self.assertAlmostEqual(total, -1627.090203)

    def test_stream_cache_uses_invoice_month_for_support_virtual(self):
        """July Support fee billed on August invoice must count in source."""
        imp = self._virtual_service_importer('Support')
        now = datetime(2026, 8, 15, 16, 0, tzinfo=timezone.utc)
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=now):
            imp._reset_stream_month_totals(datetime(2026, 6, 1))
            imp._accumulate_stream_month_row({
                'start_date': datetime(2026, 7, 31, 12, 0),
                'invoice_month': '202608',
                'sku': 'GCP Support Variable fee',
                'service': 'Support',
                'cost_type': 'regular',
                'cost': 174.03483,
                'resource_id': 'support-sku',
            })
            imp._accumulate_stream_month_row({
                'start_date': datetime(2026, 8, 2, 12, 0),
                'invoice_month': '202608',
                'sku': 'GCP Support Variable fee',
                'service': 'Support',
                'cost_type': 'regular',
                'cost': 4145.087136,
                'resource_id': 'support-sku',
            })
            imp._accumulate_stream_month_row({
                'start_date': datetime(2026, 8, 2, 12, 0),
                'invoice_month': '202607',
                'sku': 'GCP Support Variable fee',
                'service': 'Support',
                'cost_type': 'regular',
                'cost': 10.79618,
                'resource_id': 'support-sku',
            })
            source = imp._stream_month_source_by_type()
        total = sum(v['billed_sum'] for v in source.values())
        self.assertAlmostEqual(total, 4319.121966)

    def test_clear_raw_for_invoice_months(self):
        imp = self._importer(virtual=True)
        mongo_raw = MagicMock()
        mongo_raw.delete_many.return_value = MagicMock(deleted_count=51)
        object.__setattr__(imp, 'mongo_raw', mongo_raw)
        object.__setattr__(imp, 'cloud_acc_id', 'ca-gcp-virtual')
        deleted = imp._clear_raw_for_invoice_months(['202608'])
        self.assertEqual(deleted, 51)
        mongo_raw.delete_many.assert_called_once_with({
            'cloud_account_id': 'ca-gcp-virtual',
            'invoice_month': {'$in': ['202608']},
        })

    def test_empty_invoice_month_negations_collapses_stale_copy(self):
        day = datetime(2026, 7, 1)
        rows = GcpReportImporter.empty_invoice_month_negations(
            'ca-gcp-1',
            {('res-1', day)},
            {'res-1': {(day, ''): [(10.0, 1)]}},
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], 'res-1')
        self.assertEqual(rows[0][3], 10.0)
        self.assertEqual(rows[0][4], -1)
        self.assertEqual(rows[0][5], '')

    def test_empty_invoice_month_negations_skips_already_collapsed(self):
        day = datetime(2026, 7, 1)
        rows = GcpReportImporter.empty_invoice_month_negations(
            'ca-gcp-1',
            {('res-1', day)},
            {'res-1': {(day, ''): [(10.0, 1), (10.0, -1)]}},
        )
        self.assertEqual(rows, [])

    def test_unbilled_clickhouse_negations_drops_extra_day(self):
        billed = datetime(2026, 7, 1)
        extra = datetime(2026, 7, 19)
        rows = GcpReportImporter.unbilled_clickhouse_negations(
            'ca-gcp-1',
            {('res-1', billed, '202607')},
            {'res-1': {
                (billed, '202607'): [(10.0, 1)],
                (extra, '202607'): [(3.05, 1)],
            }},
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], 'res-1')
        self.assertEqual(rows[0][2], extra)
        self.assertEqual(rows[0][3], 3.05)
        self.assertEqual(rows[0][4], -1)
        self.assertEqual(rows[0][5], '202607')

    def test_unbilled_clickhouse_negations_inverts_each_duplicate_plus(self):
        extra = datetime(2026, 8, 21)
        rows = GcpReportImporter.unbilled_clickhouse_negations(
            'ca-gcp-1',
            set(),
            {'res-1': {
                (extra, '202608'): [(233.75, 1), (233.75, 1)],
            }},
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual([row[4] for row in rows], [-1, -1])
        self.assertEqual([row[3] for row in rows], [233.75, 233.75])

    def test_unbilled_clickhouse_negations_skips_days_outside_window(self):
        billed = datetime(2026, 8, 21)
        extra = datetime(2026, 8, 22)
        may = datetime(2026, 5, 10)
        rows = GcpReportImporter.unbilled_clickhouse_negations(
            'ca-gcp-1',
            {('res-1', billed, '202608')},
            {'res-1': {
                (billed, '202608'): [(10.0, 1)],
                (extra, '202608'): [(3.05, 1)],
                (may, ''): [(4353.76, 1)],
                (may, '202605'): [(25.32, 1)],
            }},
            from_dt=datetime(2026, 8, 21),
            to_dt=datetime(2026, 8, 25),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], extra)
        self.assertEqual(rows[0][5], '202608')

    def test_save_clean_collapses_empty_invoice_month_when_billed_exists(self):
        imp = self._importer(virtual=False)
        day = datetime(2026, 7, 1)
        rid = 'mongo-res-1'
        imp.get_resource_info_map = MagicMock(return_value={'gce-1': {}})
        imp.create_resources_if_not_exist = MagicMock(return_value=[{
            'id': rid,
            'cloud_resource_id': 'gce-1',
        }])
        imp.get_clickhouse_expenses = MagicMock(return_value=[
            (rid, day, '', 10.0, 1),
        ])
        imp.update_clickhouse_expenses = MagicMock()
        imp.update_resource_expense_info = MagicMock()
        imp.log_import_phase = MagicMock()
        imp.save_clean_expenses('ca-gcp-1', {
            'gce-1': [{
                'start_date': day,
                'end_date': day,
                'cost': 10.0,
                'cloud_account_id': 'ca-gcp-1',
                'invoice_month': '202607',
            }]
        })
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        billed = [row for row in payload if row[5] == '202607']
        empty = [row for row in payload if row[5] == '']
        self.assertEqual(len(billed), 1)
        self.assertEqual(billed[0][4], 1)
        self.assertEqual(len(empty), 1)
        self.assertEqual(empty[0][4], -1)
        self.assertEqual(empty[0][3], 10.0)

    def test_save_clean_negates_orphan_clickhouse_day(self):
        imp = self._importer(virtual=False)
        billed_day = datetime(2026, 7, 1)
        extra_day = datetime(2026, 7, 19)
        rid = 'mongo-res-1'
        imp.get_resource_info_map = MagicMock(return_value={'gce-1': {}})
        imp.create_resources_if_not_exist = MagicMock(return_value=[{
            'id': rid,
            'cloud_resource_id': 'gce-1',
        }])
        imp.get_clickhouse_expenses = MagicMock(return_value=[
            (rid, billed_day, '202607', 10.0, 1),
            (rid, extra_day, '202607', 3.05, 1),
        ])
        imp.update_clickhouse_expenses = MagicMock()
        imp.update_resource_expense_info = MagicMock()
        imp.log_import_phase = MagicMock()
        imp.save_clean_expenses('ca-gcp-1', {
            'gce-1': [{
                'start_date': billed_day,
                'end_date': billed_day,
                'cost': 10.0,
                'cloud_account_id': 'ca-gcp-1',
                'invoice_month': '202607',
            }]
        })
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        extras = [
            row for row in payload
            if row[2] == extra_day and row[5] == '202607'
        ]
        self.assertEqual(len(extras), 1)
        self.assertEqual(extras[0][4], -1)
        self.assertEqual(extras[0][3], 3.05)

    def test_save_clean_inverts_duplicate_plus_orphan_days(self):
        imp = self._importer(virtual=False)
        billed_day = datetime(2026, 8, 20)
        extra_day = datetime(2026, 8, 21)
        rid = 'mongo-res-1'
        imp.get_resource_info_map = MagicMock(return_value={'gce-1': {}})
        imp.create_resources_if_not_exist = MagicMock(return_value=[{
            'id': rid,
            'cloud_resource_id': 'gce-1',
        }])
        imp.get_clickhouse_expenses = MagicMock(return_value=[
            (rid, billed_day, '202608', 10.0, 1),
            (rid, extra_day, '202608', 233.75, 1),
            (rid, extra_day, '202608', 233.75, 1),
        ])
        imp.update_clickhouse_expenses = MagicMock()
        imp.update_resource_expense_info = MagicMock()
        imp.log_import_phase = MagicMock()
        imp.save_clean_expenses('ca-gcp-1', {
            'gce-1': [{
                'start_date': billed_day,
                'end_date': billed_day,
                'cost': 10.0,
                'cloud_account_id': 'ca-gcp-1',
                'invoice_month': '202608',
            }]
        })
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        extras = [
            row for row in payload
            if row[2] == extra_day and row[5] == '202608'
        ]
        self.assertEqual(len(extras), 2)
        self.assertEqual([row[4] for row in extras], [-1, -1])

    def test_save_clean_rewrites_duplicate_plus_when_net_matches(self):
        """Two +1 on the same ORDER BY key: FINAL is undefined, rewrite."""
        imp = self._importer(virtual=False)
        billed_day = datetime(2026, 8, 1)
        rid = 'mongo-res-1'
        imp.get_resource_info_map = MagicMock(return_value={'gce-1': {}})
        imp.create_resources_if_not_exist = MagicMock(return_value=[{
            'id': rid,
            'cloud_resource_id': 'gce-1',
        }])
        imp.get_clickhouse_expenses = MagicMock(return_value=[
            (rid, billed_day, '202608', 0.0, 1),
            (rid, billed_day, '202608', 64.39, 1),
        ])
        imp.update_clickhouse_expenses = MagicMock()
        imp.update_resource_expense_info = MagicMock()
        imp.log_import_phase = MagicMock()
        imp.save_clean_expenses('ca-gcp-1', {
            'gce-1': [{
                'start_date': billed_day,
                'end_date': billed_day,
                'cost': 64.39,
                'cloud_account_id': 'ca-gcp-1',
                'invoice_month': '202608',
            }]
        })
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        billed = [
            row for row in payload
            if row[2] == billed_day and row[5] == '202608'
        ]
        self.assertEqual([row[4] for row in billed], [-1, -1, 1])
        self.assertEqual([row[3] for row in billed], [0.0, 64.39, 64.39])

    def test_save_clean_queries_from_period_start_through_now(self):
        imp = self._importer(virtual=False)
        object.__setattr__(imp, 'period_start', datetime(2026, 7, 1))
        billed_day = datetime(2026, 8, 20)
        rid = 'mongo-res-1'
        imp.get_resource_info_map = MagicMock(return_value={'gce-1': {}})
        imp.create_resources_if_not_exist = MagicMock(return_value=[{
            'id': rid,
            'cloud_resource_id': 'gce-1',
        }])
        imp.get_clickhouse_expenses = MagicMock(return_value=[])
        imp.update_clickhouse_expenses = MagicMock()
        imp.update_resource_expense_info = MagicMock()
        imp.log_import_phase = MagicMock()
        now = datetime(2026, 8, 24, 15, 0)
        with patch(
                'diworker.diworker.importers.base.opttime.utcnow',
                return_value=now):
            imp.save_clean_expenses('ca-gcp-1', {
                'gce-1': [{
                    'start_date': billed_day,
                    'end_date': billed_day,
                    'cost': 10.0,
                    'cloud_account_id': 'ca-gcp-1',
                    'invoice_month': '202608',
                }]
            })
        from_dt, to_dt = imp.get_clickhouse_expenses.call_args[0][:2]
        self.assertEqual(from_dt, datetime(2026, 7, 1))
        self.assertEqual(to_dt, datetime(2026, 8, 24))

    def test_save_clean_incremental_does_not_negate_may_history(self):
        """Regression for 87e93e7: August chunk must not wipe May CH."""
        imp = self._importer(virtual=False)
        object.__setattr__(imp, 'period_start', datetime(2026, 8, 21))
        billed_day = datetime(2026, 8, 21)
        extra_day = datetime(2026, 8, 22)
        may = datetime(2026, 5, 10)
        rid = 'mongo-res-1'
        old_rid = 'mongo-old-disk'
        imp.get_resource_info_map = MagicMock(return_value={'gce-1': {}})
        imp.create_resources_if_not_exist = MagicMock(return_value=[{
            'id': rid,
            'cloud_resource_id': 'gce-1',
        }])
        imp.get_clickhouse_expenses = MagicMock(return_value=[
            (rid, billed_day, '202608', 10.0, 1),
            (rid, extra_day, '202608', 3.05, 1),
            (rid, may, '', 4353.76, 1),
            (rid, may, '202605', 25.32, 1),
        ])
        # Predecessor still has May (+ residual Aug) in CH; August rebill on
        # the new id must not clear May on the old id (87e93e7).
        mongo_raw = MagicMock()

        def _raw_aggregate(pipeline, **kwargs):
            match = pipeline[0].get('$match') or {}
            if 'resource_name' in match:
                return [{'_id': 'disk-a', 'crids': ['gce-1', 'old-disk']}]
            return []

        mongo_raw.aggregate.side_effect = _raw_aggregate
        mongo_resources = MagicMock()

        def _resources_find(filt, *args, **kwargs):
            if 'name' in filt:
                return [{'name': 'disk-a', 'cloud_resource_id': 'old-disk'}]
            if 'cloud_resource_id' in filt:
                crids = filt['cloud_resource_id'].get('$in') or [
                    filt.get('cloud_resource_id')]
                rows = []
                if 'old-disk' in crids:
                    rows.append({
                        '_id': old_rid,
                        'cloud_resource_id': 'old-disk',
                    })
                return rows
            return []

        mongo_resources.find.side_effect = _resources_find
        object.__setattr__(imp, 'mongo_raw', mongo_raw)
        object.__setattr__(imp, 'mongo_resources', mongo_resources)
        object.__setattr__(imp, 'cloud_acc_id', 'ca-gcp-1')
        ch = MagicMock()

        def _ch_query(sql, parameters=None):
            dates = {
                BaseReportImporter._clickhouse_lookup_date(d)
                for d in (parameters or {}).get('dates') or []
            }
            rids = set((parameters or {}).get('resource_ids') or [])
            rows = []
            for row in (
                    (old_rid, may, '202605', 25.32),
                    (old_rid, billed_day, '202608', 10.0),
            ):
                if dates and BaseReportImporter._clickhouse_lookup_date(
                        row[1]) not in dates:
                    continue
                if rids and row[0] not in rids:
                    continue
                rows.append(row)
            return MagicMock(result_rows=rows)

        ch.query.side_effect = _ch_query
        object.__setattr__(imp, 'clickhouse_cl', ch)
        imp.update_clickhouse_expenses = MagicMock()
        imp.update_resource_expense_info = MagicMock()
        imp.log_import_phase = MagicMock()
        now = datetime(2026, 8, 25, 3, 0)
        with patch(
                'diworker.diworker.importers.base.opttime.utcnow',
                return_value=now):
            imp.save_clean_expenses('ca-gcp-1', {
                'gce-1': [{
                    'start_date': billed_day,
                    'end_date': billed_day,
                    'cost': 10.0,
                    'cloud_account_id': 'ca-gcp-1',
                    'invoice_month': '202608',
                    'resource_name': 'disk-a',
                }]
            })
        payload = []
        for call in imp.update_clickhouse_expenses.call_args_list:
            payload.extend(call[0][0])
        may_rows = [row for row in payload if row[2] == may]
        extras = [
            row for row in payload
            if row[2] == extra_day and row[5] == '202608'
        ]
        old_aug = [
            row for row in payload
            if row[1] == old_rid and row[2] == billed_day
        ]
        self.assertEqual(may_rows, [])
        self.assertEqual(len(extras), 1)
        self.assertEqual(extras[0][4], -1)
        # Rebilled August day on the new id may clear the same day on the
        # predecessor, but never May.
        self.assertEqual(len(old_aug), 1)
        self.assertEqual(old_aug[0][4], -1)

    def test_save_clean_matches_clickhouse_date_to_datetime(self):
        from datetime import date as date_cls

        imp = self._importer(virtual=False)
        billed_day = datetime(2026, 7, 1, 0, 0)
        rid = 'mongo-res-1'
        imp.get_resource_info_map = MagicMock(return_value={'gce-1': {}})
        imp.create_resources_if_not_exist = MagicMock(return_value=[{
            'id': rid,
            'cloud_resource_id': 'gce-1',
        }])
        imp.get_clickhouse_expenses = MagicMock(return_value=[
            (rid, date_cls(2026, 7, 1), '202607', 10.0, 1),
        ])
        imp.update_clickhouse_expenses = MagicMock()
        imp.update_resource_expense_info = MagicMock()
        imp.log_import_phase = MagicMock()
        imp.save_clean_expenses('ca-gcp-1', {
            'gce-1': [{
                'start_date': billed_day,
                'end_date': billed_day,
                'cost': 10.0,
                'cloud_account_id': 'ca-gcp-1',
                'invoice_month': '202607',
            }]
        })
        imp.update_clickhouse_expenses.assert_not_called()


@unittest.skipIf(
    GcpReportImporter is None,
    'diworker runtime deps missing: %s' % _IMPORT_ERROR)
class TestGcpStaleBillingNameClickhouse(unittest.TestCase):
    """Day-scoped CH cleanup when billing name moves to a new resource_id."""

    def _importer(self):
        imp = GcpReportImporter.__new__(GcpReportImporter)
        object.__setattr__(imp, 'cloud_acc_id', 'ca-gcp-1')
        object.__setattr__(imp, 'mongo_raw', MagicMock())
        object.__setattr__(imp, 'mongo_resources', MagicMock())
        object.__setattr__(imp, 'clickhouse_cl', MagicMock())
        imp.update_clickhouse_expenses = MagicMock()
        return imp

    def _mock_name_crids(self, imp, name_to_crids, billed_days=None,
                         crid_to_mongo=None):
        """Wire batched aggregate/find used by predecessor lookup."""
        billed_days = billed_days or set()
        crid_to_mongo = crid_to_mongo or {}

        def _raw_aggregate(pipeline, **kwargs):
            match = pipeline[0].get('$match') or {}
            if 'resource_name' in match:
                names = match['resource_name'].get('$in') or []
                return [
                    {'_id': name, 'crids': list(name_to_crids.get(name, ()))}
                    for name in names
                ]
            # day presence aggregate
            out = []
            for crid, day in billed_days:
                if crid not in (match.get('resource_id') or {}).get('$in', [crid]):
                    # still include when $in lists candidates
                    pass
                out.append({
                    '_id': {
                        'rid': crid,
                        'day': day.strftime('%Y-%m-%d'),
                    }
                })
            wanted = set((match.get('resource_id') or {}).get('$in') or [])
            if wanted:
                out = [row for row in out if row['_id']['rid'] in wanted]
            return out

        imp.mongo_raw.aggregate.side_effect = _raw_aggregate

        def _resources_find(filt, *args, **kwargs):
            if 'name' in filt:
                names = filt['name'].get('$in') or [filt.get('name')]
                rows = []
                for name in names:
                    for crid in name_to_crids.get(name, ()):
                        rows.append({'name': name, 'cloud_resource_id': crid})
                return rows
            if 'cloud_resource_id' in filt:
                crids = filt['cloud_resource_id'].get('$in') or [
                    filt.get('cloud_resource_id')]
                rows = []
                for crid in crids:
                    for mongo_id in crid_to_mongo.get(crid, ()):
                        rows.append({
                            '_id': mongo_id,
                            'cloud_resource_id': crid,
                        })
                return rows
            return []

        imp.mongo_resources.find.side_effect = _resources_find

    def test_negate_clickhouse_resource_days_only_requested_days(self):
        imp = self._importer()
        old_rid = 'mongo-old'
        day = datetime(2026, 9, 1)
        residual = datetime(2026, 9, 6)

        def _ch_query(sql, parameters=None):
            dates = set((parameters or {}).get('dates') or [])
            rids = set((parameters or {}).get('resource_ids') or [])
            if (parameters or {}).get('resource_id'):
                rids.add(parameters['resource_id'])
            rows = []
            for row in (
                    (old_rid, day, '202609', 2.21),
                    (old_rid, residual, '202609', 0.53),
            ):
                if row[1] in dates and (not rids or row[0] in rids):
                    rows.append(row)
            return MagicMock(result_rows=rows)

        imp.clickhouse_cl.query.side_effect = _ch_query
        n = imp._negate_clickhouse_resource_days([(old_rid, day)])
        self.assertEqual(n, 1)
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0][1], old_rid)
        self.assertEqual(payload[0][2], day)
        self.assertEqual(payload[0][3], 2.21)
        self.assertEqual(payload[0][4], -1)

    def test_clear_stale_negates_predecessor_rebilled_day(self):
        imp = self._importer()
        day = datetime(2026, 9, 1)
        residual = datetime(2026, 9, 6)
        old_rid = 'mongo-old-vol'
        new_crid = '6030439029285641096'
        old_crid = '2383083339015793200'
        name = 'fastdbdev1-data-disk'
        self._mock_name_crids(
            imp,
            {name: {new_crid, old_crid}},
            billed_days=set(),
            crid_to_mongo={old_crid: [old_rid]},
        )

        def _ch_query(sql, parameters=None):
            dates = set((parameters or {}).get('dates') or [])
            rids = set((parameters or {}).get('resource_ids') or [])
            rows = []
            for row in (
                    (old_rid, day, '202609', 2.211739),
                    (old_rid, residual, '202609', 0.533954),
            ):
                if row[1] in dates and (not rids or row[0] in rids):
                    rows.append(row)
            return MagicMock(result_rows=rows)

        imp.clickhouse_cl.query.side_effect = _ch_query
        imp._clear_stale_clickhouse_for_rebilled_names({
            new_crid: [{
                'start_date': day,
                'cost': 2.211739,
                'resource_name': name,
                'invoice_month': '202609',
            }],
        })
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0][1], old_rid)
        self.assertEqual(payload[0][2], day)
        self.assertEqual(payload[0][4], -1)

    def test_clear_stale_skips_concurrent_same_name_billing(self):
        imp = self._importer()
        day = datetime(2026, 9, 2)
        keeper = '8934680598987540291'
        concurrent = '3437376382869099150'
        name = 'mysql-backup-extractor-disk-3'
        self._mock_name_crids(
            imp,
            {name: {keeper, concurrent}},
            billed_days={(concurrent, day)},
            crid_to_mongo={concurrent: ['mongo-concurrent']},
        )
        imp._clear_stale_clickhouse_for_rebilled_names({
            keeper: [{
                'start_date': day,
                'cost': 1.386401,
                'resource_name': name,
                'invoice_month': '202609',
            }],
        })
        imp.update_clickhouse_expenses.assert_not_called()
        imp.clickhouse_cl.query.assert_not_called()

    def test_clear_stale_skips_days_not_in_chunk(self):
        """Regression: chunk without a day must not clear that day on old id."""
        imp = self._importer()
        day = datetime(2026, 9, 1)
        may = datetime(2026, 5, 10)
        old_rid = 'mongo-old'
        self._mock_name_crids(
            imp,
            {'vol-a': {'new-disk', 'old-disk'}},
            billed_days=set(),
            crid_to_mongo={'old-disk': [old_rid]},
        )

        def _ch_query(sql, parameters=None):
            dates = set((parameters or {}).get('dates') or [])
            self.assertNotIn(may, dates)
            rows = []
            if day in dates:
                rows.append((old_rid, day, '202609', 1.0))
            return MagicMock(result_rows=rows)

        imp.clickhouse_cl.query.side_effect = _ch_query
        imp._clear_stale_clickhouse_for_rebilled_names({
            'new-disk': [{
                'start_date': day,
                'cost': 1.0,
                'resource_name': 'vol-a',
                'invoice_month': '202609',
            }],
        })
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        self.assertTrue(all(row[2] != may for row in payload))

    def test_clear_stale_noop_without_resource_name(self):
        imp = self._importer()
        imp._clear_stale_clickhouse_for_rebilled_names({
            'gce-1': [{'start_date': datetime(2026, 9, 1), 'cost': 1.0}],
        })
        imp.mongo_raw.aggregate.assert_not_called()
        imp.update_clickhouse_expenses.assert_not_called()

    def test_clear_stale_works_for_instance_and_cloudrun_names(self):
        imp = self._importer()
        day = datetime(2026, 9, 1)
        cases = (
            ('inst-1', 'projects/1/instances/vm-a', 'mongo-old-inst'),
            ('run-1', 'my-cloud-run-service', 'mongo-old-run'),
        )
        for new_crid, name, old_rid in cases:
            imp.update_clickhouse_expenses.reset_mock()
            old_crid = 'old-' + new_crid
            self._mock_name_crids(
                imp,
                {name: {new_crid, old_crid}},
                billed_days=set(),
                crid_to_mongo={old_crid: [old_rid]},
            )
            imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
                (old_rid, day, '202609', 4.5),
            ])
            imp._clear_stale_clickhouse_for_rebilled_names({
                new_crid: [{
                    'start_date': day,
                    'cost': 4.5,
                    'resource_name': name,
                    'invoice_month': '202609',
                }],
            })
            payload = imp.update_clickhouse_expenses.call_args[0][0]
            self.assertEqual(payload[0][1], old_rid)
            self.assertEqual(payload[0][4], -1)


@unittest.skipIf(
    GcpReportImporter is None,
    'diworker runtime deps missing: %s' % _IMPORT_ERROR)
class TestGcpResourceNameFromTags(unittest.TestCase):
    def _importer(self):
        from unittest.mock import PropertyMock

        imp = GcpReportImporter.__new__(GcpReportImporter)
        adapter = MagicMock()
        adapter.is_virtual_billing_project = False
        adapter.fix_region.side_effect = lambda r: r
        object.__setattr__(imp, '_cloud_adapter', adapter)
        return imp

    def _expense(self, **overrides):
        base = {
            'cost_type': 'regular',
            'sku': 'N2 Instance Ram running in Americas',
            'region': 'us-central1',
            'service': 'Compute Engine',
            'resource_id': '746230590525317299',
            # Naive datetimes: get_resource_info_from_expenses compares
            # against opttime.utcnow() which is timezone-naive.
            'start_date': datetime(2026, 8, 1),
            'end_date': datetime(2026, 8, 2),
            'tags': {},
            'system_tags': {},
        }
        base.update(overrides)
        return base

    def test_prefers_tag_name_over_sku_region(self):
        imp = self._importer()
        info = imp.get_resource_info_from_expenses([
            self._expense(tags={'name': 'vmoptscale', 'env': 'mgmt'}),
        ])
        self.assertEqual(info['name'], 'vmoptscale')
        self.assertEqual(info['type'], 'Instance')
        self.assertEqual(info['tags']['name'], 'vmoptscale')

    def test_dataproc_labels_set_type_and_name(self):
        imp = self._importer()
        info = imp.get_resource_info_from_expenses([
            self._expense(tags={
                'goog-dataproc-cluster-uuid': '5fcf5527-4bb6-4991-82f4-55b339a0a2af',
                'goog-dataproc-cluster-name': 'dataproc',
                'name': 'gke-spot-pool',
            }),
            self._expense(
                sku='Storage PD Capacity',
                tags={
                    'goog-dataproc-cluster-uuid': '5fcf5527-4bb6-4991-82f4-55b339a0a2af',
                    'goog-dataproc-cluster-name': 'dataproc',
                }),
        ])
        self.assertEqual(info['type'], 'Dataproc')
        self.assertEqual(info['name'], 'dataproc')

    def test_serverless_dataproc_labels_set_dag_name(self):
        imp = self._importer()
        info = imp.get_resource_info_from_expenses([
            self._expense(tags={
                'goog-dataproc-cluster-uuid': '0014ed00-da95-4d7a-a75c-22459e7337ea',
                'goog-dataproc-cluster-name': 'srvls-batch-aaa',
                'goog-dataproc-batch-uuid': 'aaa',
                'airflow-dag-id': 'sim_dataset_processing_prod',
            }),
        ])
        self.assertEqual(info['type'], 'Dataproc')
        self.assertEqual(info['name'], 'sim_dataset_processing_prod')
        self.assertEqual(
            info['tags']['goog-dataproc-cluster-uuid'],
            'sim_dataset_processing_prod')

    def test_falls_back_to_sku_region_without_tag_name(self):
        imp = self._importer()
        info = imp.get_resource_info_from_expenses([self._expense()])
        self.assertEqual(
            info['name'],
            'N2 Instance Ram running in Americas us-central1')

    def test_ignores_blank_tag_name(self):
        imp = self._importer()
        info = imp.get_resource_info_from_expenses([
            self._expense(tags={'name': '  '}),
        ])
        self.assertEqual(
            info['name'],
            'N2 Instance Ram running in Americas us-central1')

    def test_network_sku_uses_tag_name(self):
        imp = self._importer()
        info = imp.get_resource_info_from_expenses([
            self._expense(
                sku='Network Intra Zone Data Transfer Out',
                tags={'name': 'vmoptscale'}),
        ])
        self.assertEqual(info['name'], 'vmoptscale')
        self.assertEqual(info['type'], 'Instance')

    def test_gke_resource_id_sets_type_and_name(self):
        imp = self._importer()
        info = imp.get_resource_info_from_expenses([
            self._expense(
                sku='Storage PD Capacity',
                resource_id='gke/pf-da-shared-nonprod-gke',
                resource_name='pvc-537c475a-c155-45f4-b89c-dfec36a5d939',
            ),
        ])
        self.assertEqual(info['type'], 'GKE')
        self.assertEqual(info['name'], 'pf-da-shared-nonprod-gke')

    def test_standalone_disk_expenses_stay_volume(self):
        imp = self._importer()
        info = imp.get_resource_info_from_expenses([
            self._expense(
                sku='Storage PD Capacity',
                resource_id='2534772272498265879',
                resource_name='calcdsnl1-data',
                resource_global_name=(
                    '//compute.googleapis.com/projects/1/zones/z/disk/1'),
            ),
        ])
        self.assertEqual(info['type'], 'Volume')
        self.assertEqual(info['name'], 'calcdsnl1-data')

    def test_instance_expenses_stay_instance(self):
        imp = self._importer()
        info = imp.get_resource_info_from_expenses([
            self._expense(
                resource_id='746230590525317299',
                resource_name='proxygwd-nonprod-01',
                resource_global_name=(
                    '//compute.googleapis.com/projects/1/zones/z/instances/1'),
            ),
        ])
        self.assertEqual(info['type'], 'Instance')
        self.assertEqual(info['name'], 'proxygwd-nonprod-01')

    def test_cloud_sql_global_name_sets_type_and_name(self):
        imp = self._importer()
        info = imp.get_resource_info_from_expenses([
            self._expense(
                service='Cloud SQL',
                sku='Cloud SQL for MySQL: Zonal - RAM in Americas',
                resource_id='cloudsql/cfg-db',
                resource_name='cfg-db',
                resource_global_name=(
                    '//sqladmin.googleapis.com/projects/p/instances/cfg-db'),
            ),
        ])
        self.assertEqual(info['type'], 'Cloud SQL')
        self.assertEqual(info['name'], 'cfg-db')

    def test_cloud_run_global_name_sets_type_and_name(self):
        imp = self._importer()
        info = imp.get_resource_info_from_expenses([
            self._expense(
                service='Cloud Run',
                sku='Requests',
                resource_id='cloudrun/sim-config-nonprod',
                resource_name='sim-config-nonprod',
                resource_global_name=(
                    '//run.googleapis.com/projects/p/locations/us-central1/'
                    'services/sim-config-nonprod'),
            ),
        ])
        self.assertEqual(info['type'], 'Cloud Run')
        self.assertEqual(info['name'], 'sim-config-nonprod')


@unittest.skipIf(
    GcpReportImporter is None,
    'diworker runtime deps missing: %s' % _IMPORT_ERROR)
class TestGcpGenerateResourceId(unittest.TestCase):
    def test_composer_environment_uuid_collapses_sku_rows(self):
        rid = GcpReportImporter._generate_resource_id({
            'sku_id': 'D7FD-FC38-57D9',
            'sku': 'Cloud Composer Compute Memory (us-central1)',
            'region': 'us-central1',
            'tags': {
                'goog-composer-environment-uuid': 'e8711ea5-ec4c-4ece-bad1-e54b3b6a923c',
                'k8s-workload-name': 'airflow-worker-4fkzb',
            },
        })
        self.assertEqual(
            rid, 'composer/e8711ea5-ec4c-4ece-bad1-e54b3b6a923c')

    def test_dataproc_uuid_beats_discovery_numeric_id(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/'
                'zones/us-central1-f/instances/7804579355146023165'),
            'sku_id': '5535-6D2D-4B50',
            'tags': {
                'goog-dataproc-cluster-uuid': '5fcf5527-4bb6-4991-82f4-55b339a0a2af',
                'goog-dataproc-cluster-name': 'dataproc',
            },
        }, discovery_ids={'7804579355146023165'})
        self.assertEqual(
            rid, 'dataproc/5fcf5527-4bb6-4991-82f4-55b339a0a2af')

    def test_serverless_dataproc_collapses_by_dag_not_uuid(self):
        rid = GcpReportImporter._generate_resource_id({
            'sku_id': 'EC7A-EF05-537E',
            'tags': {
                'goog-dataproc-cluster-uuid': '0014ed00-da95-4d7a-a75c-22459e7337ea',
                'goog-dataproc-cluster-name': (
                    'srvls-batch-98fa3997-4d55-478b-a235-2a841bda97b0'),
                'goog-dataproc-batch-uuid': '98fa3997-4d55-478b-a235-2a841bda97b0',
                'airflow-dag-id': 'sim_dataset_processing_prod',
            },
        })
        self.assertEqual(rid, 'dataproc/dag/sim_dataset_processing_prod')

    def test_serverless_sku_without_cluster_uuid_collapses_by_dag(self):
        rid = GcpReportImporter._generate_resource_id({
            'sku_id': 'EC7A-EF05-537E',
            'tags': {
                'airflow-dag-id': 'sim_products_onboarding_ace_prod',
                'goog-dataproc-batch-uuid': '10c1bc79-26ca-4c50-886c-1066b3160872',
            },
        })
        self.assertEqual(rid, 'dataproc/dag/sim_products_onboarding_ace_prod')

    def test_same_sku_id_collapses_different_label_sets(self):
        a = GcpReportImporter._generate_resource_id({
            'sku_id': 'B701-3D6E-BFF1',
            'sku': 'Cloud Composer Compute mCPUs (us-central1)',
            'region': 'us-central1',
            'tags': {'k8s-workload-name': 'worker-aaa'},
        })
        b = GcpReportImporter._generate_resource_id({
            'sku_id': 'B701-3D6E-BFF1',
            'sku': 'Cloud Composer Compute mCPUs (us-central1)',
            'region': 'europe-west4',
            'tags': {'k8s-workload-name': 'worker-bbb', 'dag_id': 'x'},
        })
        self.assertEqual(a, b)
        self.assertEqual(a, 'B701-3D6E-BFF1')

    def test_prefers_bq_resource_global_name(self):
        # Non-discoverable global_name (Composer) must NOT become resource_id —
        # fall back to sku.id to avoid one Mongo doc per environment path.
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': '//composer.googleapis.com/projects/p/envs/e',
            'sku_id': 'D7FD-FC38-57D9',
            'sku': 'Cloud Composer Compute Memory (us-central1)',
            'region': 'us-central1',
            'tags': {},
        })
        self.assertEqual(rid, 'D7FD-FC38-57D9')

    def test_bigquery_dataset_global_name_falls_back_to_sku_id(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//bigquery.googleapis.com/projects/bigdata-269309/datasets/1113'),
            'resource_name': '1113',
            'sku_id': 'AAAA-BBBB-CCCC',
            'sku': 'Active Storage',
            'tags': {},
        })
        self.assertEqual(rid, 'AAAA-BBBB-CCCC')

    def test_parses_instance_numeric_id_from_global_name(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//compute.googleapis.com/projects/540187916048/'
                'zones/us-central1-c/instances/746230590525317299'),
            'resource_name': (
                'projects/540187916048/instances/'
                'gke-pf-sns-prod-gke-primary-pool-f677f69f-6kq4'),
            'sku_id': '5535-6D2D-4B50',
            'sku': 'N2D AMD Instance Ram running in Americas',
            'tags': {},
        }, discovery_ids={'746230590525317299'})
        self.assertEqual(rid, '746230590525317299')

    def test_instance_not_in_discovery_uses_numeric_id(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//compute.googleapis.com/projects/540187916048/'
                'zones/us-central1-c/instances/746230590525317299'),
            'sku_id': '5535-6D2D-4B50',
            'sku': 'N2D AMD Instance Ram running in Americas',
            'tags': {},
        }, discovery_ids=set())
        self.assertEqual(rid, '746230590525317299')

    def test_bucket_not_in_discovery_uses_bucket_name(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//storage.googleapis.com/projects/_/buckets/'
                'pf-prod-sns-dataproc-us-restatement'),
            'sku_id': 'AAAA-BBBB-CCCC',
            'sku': 'Standard Storage US Multi-region',
            'tags': {},
        }, discovery_ids=set())
        self.assertEqual(rid, 'pf-prod-sns-dataproc-us-restatement')

    def test_parses_disk_numeric_id_from_global_name(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//compute.googleapis.com/projects/540187916048/'
                'zones/us-central1-a/disk/2534772272498265879'),
            'resource_name': 'calcdsnl1-data',
            'sku_id': '6F81-5844-456A',
            'sku': 'Storage PD Capacity',
            'tags': {},
        }, discovery_ids={'2534772272498265879'})
        self.assertEqual(rid, '2534772272498265879')

    def test_parses_snapshot_numeric_id_from_global_name(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/global/snapshots/'
                '4758898924634663632'),
            'resource_name': 'cfg-db',
            'sku_id': 'AAAA-BBBB-CCCC',
            'sku': 'Storage PD Snapshot',
            'tags': {},
        })
        self.assertEqual(rid, '4758898924634663632')

    def test_parses_ip_numeric_id_from_global_name(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/regions/us-central1/'
                'addresses/952981511495052626'),
            'resource_name': 'serverless-ipv4',
            'sku_id': 'AAAA-BBBB-CCCC',
            'sku': 'Static Ip Charge',
            'tags': {},
        })
        self.assertEqual(rid, '952981511495052626')

    def test_parses_image_numeric_id_from_global_name(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/global/images/'
                '123456789012345678'),
            'resource_name': 'custom-image',
            'sku_id': 'AAAA-BBBB-CCCC',
            'sku': 'Storage Image',
            'tags': {},
        })
        self.assertEqual(rid, '123456789012345678')

    def test_sqladmin_instance_collapses_to_cloudsql(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//sqladmin.googleapis.com/projects/pf-da-shared-prod/'
                'instances/cfg-db'),
            'resource_name': 'cfg-db',
            'sku_id': '93DA-3F55-CB04',
            'sku': 'Cloud SQL for MySQL: Zonal - RAM in Americas',
            'tags': {},
        })
        self.assertEqual(rid, 'cloudsql/cfg-db')

    def test_cloud_sql_sku_without_global_name_stays_sku_id(self):
        rid = GcpReportImporter._generate_resource_id({
            'sku_id': '93DA-3F55-CB04',
            'sku': 'Cloud SQL for PostgreSQL: Zonal - RAM in Americas',
            'tags': {},
        })
        self.assertEqual(rid, '93DA-3F55-CB04')

    def test_sql_backup_snapshot_collapses_to_cloudsql(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/global/snapshots/'
                '5295581089999983207'),
            'resource_name': (
                'a-244966926347-s-6bf71ce3a8428ad3-backup-1779707006387'),
            'sku_id': 'AAAA-BBBB-CCCC',
            'sku': 'Storage PD Snapshot',
            'tags': {'cloud_sql_backup_id': '612a141a-125e-4d12-a3ab-288f1dd95cb9'},
        })
        self.assertEqual(
            rid, 'cloudsql/a-244966926347-s-6bf71ce3a8428ad3')

    def test_cloud_run_service_collapses_from_global_name(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//run.googleapis.com/projects/p/locations/us-central1/'
                'services/sim-config-nonprod'),
            'resource_name': 'sim-config-nonprod',
            'sku_id': '011E-3072-CBDB',
            'tags': {},
        })
        self.assertEqual(rid, 'cloudrun/sim-config-nonprod')

    def test_cloud_function_collapses_from_global_name(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//cloudfunctions.googleapis.com/projects/p/locations/'
                'us-central1/functions/fun_001_hello_world'),
            'resource_name': 'fun_001_hello_world',
            'sku_id': '8E10-82EB-6917',
            'tags': {},
        })
        self.assertEqual(rid, 'function/fun_001_hello_world')

    def test_labeled_serverless_ip_collapses_to_cloud_run(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/regions/us-central1/'
                'addresses/952981511495052626'),
            'resource_name': 'serverless-ipv4-1753273277343418364',
            'sku_id': 'AAAA-BBBB-CCCC',
            'sku': 'Static Ip Charge',
            'tags': {'goog-cloud-run-service': 'sim-config-nonprod'},
        })
        self.assertEqual(rid, 'cloudrun/sim-config-nonprod')

    def test_dataflow_job_tag_does_not_collapse_resource_id(self):
        rid = GcpReportImporter._generate_resource_id({
            'sku_id': '9E4E-F9A7-5EAE',
            'sku': 'Dataflow vCPU Time',
            'resource_global_name': (
                '//dataflow.googleapis.com/projects/p/locations/'
                'us-central1/jobs/my-streaming-job'),
            'tags': {'goog-dataflow-job-id': '2026-08-24_12_00_00-job'},
        })
        self.assertEqual(rid, '9E4E-F9A7-5EAE')

    def test_unlabeled_serverless_ip_keeps_numeric_id(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/regions/us-central1/'
                'addresses/952981511495052626'),
            'resource_name': 'serverless-ipv4-1753273277343418364',
            'sku_id': 'AAAA-BBBB-CCCC',
            'sku': 'Static Ip Charge',
            'tags': {},
        })
        self.assertEqual(rid, '952981511495052626')

    def test_unlabeled_pvc_without_cluster_label_keeps_numeric_id(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//compute.googleapis.com/projects/54380973644/'
                'zones/us-central1-a/disk/7175957273002431882'),
            'resource_name': 'pvc-537c475a-c155-45f4-b89c-dfec36a5d939',
            'sku_id': '6F81-5844-456A',
            'sku': 'Storage PD Capacity',
            'tags': {},
        })
        self.assertEqual(rid, '7175957273002431882')

    def test_labeled_gke_volume_uses_cluster_name(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/zones/z/disks/99'),
            'resource_name': 'pvc-537c475a-c155-45f4-b89c-dfec36a5d939',
            'sku_id': '6F81-5844-456A',
            'sku': 'Storage PD Capacity',
            'tags': {'goog-k8s-cluster-name': 'real-cluster'},
        })
        self.assertEqual(rid, 'gke/real-cluster')

    def test_dataproc_volume_beats_numeric_disk_id(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/zones/z/disk/1'),
            'resource_name': 'pvc-537c475a-c155-45f4-b89c-dfec36a5d939',
            'sku_id': '6F81-5844-456A',
            'sku': 'Storage PD Capacity',
            'tags': {
                'goog-dataproc-cluster-uuid': '5fcf5527-4bb6-4991-82f4-55b339a0a2af',
                'goog-dataproc-cluster-name': 'dataproc',
            },
        })
        self.assertEqual(
            rid, 'dataproc/5fcf5527-4bb6-4991-82f4-55b339a0a2af')

    def test_composer_beats_numeric_disk_id(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/zones/z/disk/1'),
            'resource_name': 'pvc-537c475a-c155-45f4-b89c-dfec36a5d939',
            'sku_id': '6F81-5844-456A',
            'sku': 'Storage PD Capacity',
            'tags': {
                'goog-composer-environment-uuid':
                    'e8711ea5-ec4c-4ece-bad1-e54b3b6a923c',
            },
        })
        self.assertEqual(
            rid, 'composer/e8711ea5-ec4c-4ece-bad1-e54b3b6a923c')

    def test_parses_bucket_name_from_global_name(self):
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//storage.googleapis.com/projects/_/buckets/'
                'pf-prod-sns-dataproc-us-restatement'),
            'sku_id': 'AAAA-BBBB-CCCC',
            'sku': 'Standard Storage US Multi-region',
            'tags': {},
        }, discovery_ids={'pf-prod-sns-dataproc-us-restatement'})
        self.assertEqual(rid, 'pf-prod-sns-dataproc-us-restatement')

    def test_logging_bucket_global_name_falls_back_to_sku_id(self):
        # logging.googleapis.com/.../buckets/_Default must NOT be treated as GCS
        rid = GcpReportImporter._generate_resource_id({
            'resource_global_name': (
                '//logging.googleapis.com/projects/447759250112/'
                'locations/global/buckets/_Default'),
            'sku_id': '143F-A1B0-E0BE',
            'sku': 'Log Storage cost',
            'tags': {},
        }, discovery_ids={'_Default'})
        self.assertEqual(rid, '143F-A1B0-E0BE')

    def test_falls_back_to_sku_description(self):
        rid = GcpReportImporter._generate_resource_id({
            'sku': 'Cloud Composer Compute Memory (us-central1)',
            'region': 'us-central1',
            'tags': {},
        })
        self.assertEqual(rid, 'Cloud Composer Compute Memory (us-central1)')

    def test_resource_short_name_from_path(self):
        short = GcpReportImporter._resource_short_name({
            'resource_name': (
                'projects/540187916048/instances/'
                'gke-pf-sns-prod-gke-primary-pool-f677f69f-6kq4'),
        })
        self.assertEqual(
            short, 'gke-pf-sns-prod-gke-primary-pool-f677f69f-6kq4')


@unittest.skipIf(
    GcpReportImporter is None,
    'diworker runtime deps missing: %s' % _IMPORT_ERROR)
class TestGcpServerlessDataprocCleanup(unittest.TestCase):
    def _importer(self):
        imp = GcpReportImporter.__new__(GcpReportImporter)
        object.__setattr__(imp, 'cloud_acc_id', 'ca-gcp-1')
        mongo_raw = MagicMock()
        mongo_resources = MagicMock()
        object.__setattr__(imp, 'mongo_raw', mongo_raw)
        object.__setattr__(imp, 'mongo_resources', mongo_resources)
        return imp

    def test_rewrite_serverless_raw_ids_uses_36_safe_set(self):
        from tools.cloud_adapter.gcp_resource_collapse import (
            SERVERLESS_DATAPROC_DAG_RAW_FIELD,
            serverless_dataproc_raw_rewrite_filter,
        )
        imp = self._importer()
        imp.mongo_raw.distinct.return_value = ['sim_dag', '', None]
        imp.mongo_raw.update_many.return_value = MagicMock(modified_count=3)
        imp._rewrite_serverless_dataproc_raw_ids()
        imp.mongo_raw.distinct.assert_called_once_with(
            SERVERLESS_DATAPROC_DAG_RAW_FIELD,
            serverless_dataproc_raw_rewrite_filter('ca-gcp-1'))
        calls = imp.mongo_raw.update_many.call_args_list
        self.assertEqual(len(calls), 2)
        dag_filt, dag_update = calls[0][0]
        leftover_filt, leftover_update = calls[1][0]
        self.assertEqual(
            dag_update, {'$set': {'resource_id': 'dataproc/dag/sim_dag'}})
        self.assertEqual(
            leftover_update, {'$set': {'resource_id': 'dataproc/serverless'}})
        self.assertEqual(dag_filt[SERVERLESS_DATAPROC_DAG_RAW_FIELD], 'sim_dag')
        self.assertEqual(
            leftover_filt, serverless_dataproc_raw_rewrite_filter('ca-gcp-1'))
        for _filt, update in (calls[0][0], calls[1][0]):
            self.assertIsInstance(update, dict)
            self.assertNotIsInstance(update, list)

    def test_rekey_keeps_one_doc_per_dag_and_moves_clickhouse(self):
        from tools.cloud_adapter.gcp_resource_collapse import (
            ENCODED_AIRFLOW_DAG_ID_FIELD,
            stale_serverless_dataproc_resource_filter,
        )
        imp = self._importer()
        imp.clickhouse_cl = MagicMock()
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
            ('extra-id', '2026-08-01', '', 1.5),
        ])
        imp.update_clickhouse_expenses = MagicMock()
        filt = stale_serverless_dataproc_resource_filter('ca-gcp-1')
        imp.mongo_resources.distinct.side_effect = [
            [],
            ['sim_dag'], [],
        ]
        extra = {
            '_id': 'extra-id', 'cloud_resource_id': 'dataproc/bbb',
            'first_seen': 20, 'last_seen': 50,
        }
        existing = {
            '_id': 'keep-id',
            'cloud_resource_id': 'dataproc/dag/sim_dag',
            'first_seen': 10, 'last_seen': 40,
        }

        def _find(query, projection=None):
            if query.get('cloud_resource_id') == 'dataproc/dag/sim_dag':
                return [existing]
            if query.get('$or'):
                return [extra]
            return []

        imp.mongo_resources.find.side_effect = _find
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow_timestamp',
                return_value=1786700000):
            imp._rekey_stale_serverless_dataproc_resources()
        self.assertEqual(
            imp.mongo_resources.distinct.call_args_list[1][0],
            (ENCODED_AIRFLOW_DAG_ID_FIELD, filt))
        set_call = imp.mongo_resources.update_one.call_args[0]
        self.assertEqual(set_call[0], {'_id': 'keep-id'})
        self.assertEqual(
            set_call[1]['$set']['cloud_resource_id'],
            'dataproc/dag/sim_dag')
        extras = imp.mongo_resources.update_many.call_args[0]
        self.assertEqual(extras[0]['_id']['$in'], ['extra-id'])
        self.assertEqual(extras[1], {'$set': {'deleted_at': 1786700000}})
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        self.assertEqual(payload[0][1], 'keep-id')
        self.assertEqual(payload[1][1], 'extra-id')
        self.assertEqual(payload[1][4], -1)

    def test_retire_skips_when_no_stale_docs(self):
        imp = self._importer()
        imp.mongo_resources.distinct.return_value = []
        imp.mongo_resources.update_many.return_value = MagicMock(
            modified_count=0)
        imp._retire_stale_serverless_dataproc_resources()
        self.assertEqual(imp.mongo_resources.update_many.call_count, 1)

    def test_retire_soft_deletes_orphan_cluster_parents(self):
        imp = self._importer()
        imp.mongo_resources.distinct.side_effect = [
            ['cluster-1', 'cluster-2'],
            ['cluster-2'],
        ]
        member_result = MagicMock(modified_count=10)
        parent_result = MagicMock(modified_count=1)
        imp.mongo_resources.update_many.side_effect = [
            member_result, parent_result]
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow_timestamp',
                return_value=1786700000):
            imp._retire_stale_serverless_dataproc_resources()
        parent_call = imp.mongo_resources.update_many.call_args_list[1]
        self.assertEqual(parent_call[0][0]['_id']['$in'], ['cluster-1'])
        self.assertEqual(
            parent_call[0][1], {'$set': {'deleted_at': 1786700000}})

    def test_dedupe_keeps_canonical_and_moves_clickhouse(self):
        imp = self._importer()
        imp.clickhouse_cl = MagicMock()
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
            ('extra-id', '2026-08-01', '', 1.5),
        ])
        imp.update_clickhouse_expenses = MagicMock()
        imp.mongo_resources.aggregate.return_value = [{
            '_id': 'dataproc/5fcf5527-4bb6-4991-82f4-55b339a0a2af',
            'count': 2,
            'docs': [
                {'_id': 'extra-id', 'cloud_resource_hash': 'abc',
                 'first_seen': 20, 'last_seen': 50},
                {'_id': 'keep-id', 'first_seen': 10, 'last_seen': 40},
            ],
        }]
        imp.mongo_resources.find.return_value = []
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow_timestamp',
                return_value=1786700000):
            imp._dedupe_collapsed_gcp_identity_resources()
        unset = imp.mongo_resources.update_one.call_args[0]
        self.assertEqual(unset[0], {'_id': 'keep-id'})
        self.assertEqual(unset[1]['$unset'], {'cloud_resource_hash': ''})
        self.assertEqual(unset[1]['$set']['first_seen'], 10)
        self.assertEqual(unset[1]['$set']['last_seen'], 50)
        extras = imp.mongo_resources.update_many.call_args[0]
        self.assertEqual(extras[0]['_id']['$in'], ['extra-id'])
        self.assertEqual(extras[1], {'$set': {'deleted_at': 1786700000}})
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        self.assertEqual(payload[0][1], 'keep-id')
        self.assertEqual(payload[1][1], 'extra-id')
        self.assertEqual(payload[1][4], -1)

    def test_purge_hard_deletes_dead_keeper_when_live_twin_exists(self):
        imp = self._importer()
        live = [{'_id': 'live-id',
                 'cloud_resource_id': 'cloudrun/pf-dedup-api-service'}]
        dead = [{'_id': 'dead-id'}]
        imp.mongo_resources.find.side_effect = [live, dead]
        n = imp._purge_deleted_collapsed_identity_twins()
        self.assertEqual(n, 1)
        imp.mongo_resources.delete_many.assert_called_once_with(
            {'_id': {'$in': ['dead-id']}})

    def test_purge_skips_when_no_live_keeper(self):
        imp = self._importer()
        imp.mongo_resources.find.return_value = []
        self.assertEqual(imp._purge_deleted_collapsed_identity_twins(), 0)
        imp.mongo_resources.delete_many.assert_not_called()

    def test_rewrite_labeled_raw_ids_uses_36_safe_set(self):
        from tools.cloud_adapter.gcp_resource_collapse import (
            PLAIN_COMPOSER_UUID_FIELD,
            PLAIN_GKE_NAME_FIELD,
        )
        uuid = '05ca77cc-4f2d-4497-9465-f0d341b9a441'
        imp = self._importer()
        imp.mongo_raw.distinct.side_effect = [[], [uuid], ['pf-sns-prod-gke']]
        imp.mongo_raw.update_many.return_value = MagicMock(modified_count=4)
        imp._rewrite_labeled_collapse_raw_ids()
        self.assertEqual(imp.mongo_raw.update_many.call_count, 2)
        composer_filt, composer_update = (
            imp.mongo_raw.update_many.call_args_list[0][0])
        gke_filt, gke_update = imp.mongo_raw.update_many.call_args_list[1][0]
        self.assertEqual(
            composer_update,
            {'$set': {'resource_id': 'composer/%s' % uuid}})
        self.assertEqual(composer_filt[PLAIN_COMPOSER_UUID_FIELD], uuid)
        self.assertEqual(
            gke_update, {'$set': {'resource_id': 'gke/pf-sns-prod-gke'}})
        self.assertEqual(gke_filt[PLAIN_GKE_NAME_FIELD], 'pf-sns-prod-gke')
        for update in (composer_update, gke_update):
            self.assertIsInstance(update, dict)
            self.assertNotIsInstance(update, list)

    def test_rekey_labeled_keeps_one_composer_and_moves_clickhouse(self):
        from tools.cloud_adapter.gcp_resource_collapse import (
            ENCODED_COMPOSER_UUID_FIELD,
            encode_tag_key,
        )
        uuid = '05ca77cc-4f2d-4497-9465-f0d341b9a441'
        imp = self._importer()
        imp.clickhouse_cl = MagicMock()
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
            ('node-id', '2026-08-01', '', 2.5),
        ])
        imp.update_clickhouse_expenses = MagicMock()
        keeper = {
            '_id': 'keep-id',
            'cloud_resource_id': 'composer/%s' % uuid,
            'first_seen': 10, 'last_seen': 40,
            'tags': {encode_tag_key('goog-composer-environment-uuid'): uuid},
        }
        extra = {
            '_id': 'node-id',
            'cloud_resource_id': '7804579355146023165',
            'first_seen': 20, 'last_seen': 50,
            'cluster_id': 'cluster-1',
            'tags': {encode_tag_key('goog-composer-environment-uuid'): uuid},
        }

        def _distinct(field, query=None):
            if field == ENCODED_COMPOSER_UUID_FIELD:
                return [uuid]
            return []

        def _find(query, projection=None):
            if query.get('cloud_resource_id') == 'composer/%s' % uuid:
                return [keeper]
            if query.get('$or'):
                return [keeper, extra]
            return []

        imp.mongo_resources.distinct.side_effect = _distinct
        imp.mongo_resources.find.side_effect = _find
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow_timestamp',
                return_value=1786700000):
            imp._rekey_collapsed_labeled_resources()
        set_call = imp.mongo_resources.update_one.call_args[0]
        self.assertEqual(set_call[0], {'_id': 'keep-id'})
        self.assertEqual(
            set_call[1]['$set']['cloud_resource_id'],
            'composer/%s' % uuid)
        extras = imp.mongo_resources.update_many.call_args[0]
        self.assertEqual(extras[0]['_id']['$in'], ['node-id'])
        self.assertEqual(extras[1], {'$set': {'deleted_at': 1786700000}})
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        self.assertEqual(payload[0][1], 'keep-id')
        self.assertEqual(payload[1][1], 'node-id')
        self.assertEqual(payload[1][4], -1)
        self.assertEqual(imp._collapsed_member_cluster_ids, ['cluster-1'])

    def test_rekey_labeled_skips_billing_sku_with_cluster_tags(self):
        from tools.cloud_adapter.gcp_resource_collapse import (
            ENCODED_COMPOSER_UUID_FIELD,
            encode_tag_key,
        )
        uuid = '6530bd7f-4f2d-4497-9465-f0d341b9a441'
        sku = '9E4E-F9A7-5EAE'
        imp = self._importer()
        imp.clickhouse_cl = MagicMock()
        imp.update_clickhouse_expenses = MagicMock()
        keeper = {
            '_id': 'keep-id',
            'cloud_resource_id': 'composer/%s' % uuid,
            'first_seen': 10, 'last_seen': 40,
            'tags': {encode_tag_key('goog-composer-environment-uuid'): uuid},
        }
        extra = {
            '_id': 'sku-mongo',
            'cloud_resource_id': sku,
            'first_seen': 20, 'last_seen': 50,
            'cluster_id': 'cluster-1',
            'tags': {encode_tag_key('goog-composer-environment-uuid'): uuid},
        }

        def _distinct(field, query=None):
            if field == ENCODED_COMPOSER_UUID_FIELD:
                return [uuid]
            return []

        def _find(query, projection=None):
            if query.get('cloud_resource_id') == 'composer/%s' % uuid:
                return [keeper]
            if query.get('$or'):
                return [keeper, extra]
            return []

        imp.mongo_resources.distinct.side_effect = _distinct
        imp.mongo_resources.find.side_effect = _find
        imp.mongo_raw.find_one.return_value = {'tags': {}}
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow_timestamp',
                return_value=1786700000):
            imp._rekey_collapsed_labeled_resources()
        imp.mongo_resources.update_many.assert_not_called()
        imp.update_clickhouse_expenses.assert_not_called()

    def test_rekey_labeled_does_not_fold_cloudrun_onto_gke(self):
        from tools.cloud_adapter.gcp_resource_collapse import (
            ENCODED_GKE_NAME_FIELD,
            encode_tag_key,
        )
        cluster = 'pf-data-enrichment-nonprod-gke'
        crid = 'cloudrun/pf-dedup-api-service'
        imp = self._importer()
        imp.clickhouse_cl = MagicMock()
        imp.update_clickhouse_expenses = MagicMock()
        keeper = {
            '_id': 'gke-id',
            'cloud_resource_id': 'gke/%s' % cluster,
            'first_seen': 10, 'last_seen': 40,
            'tags': {encode_tag_key('goog-k8s-cluster-name'): cluster},
        }
        extra = {
            '_id': 'run-id',
            'cloud_resource_id': crid,
            'resource_type': 'Cloud Run',
            'first_seen': 20, 'last_seen': 50,
            'tags': {encode_tag_key('goog-k8s-cluster-name'): cluster},
        }

        def _distinct(field, query=None):
            if field == ENCODED_GKE_NAME_FIELD:
                return [cluster]
            return []

        def _find(query, projection=None):
            if query.get('cloud_resource_id') == 'gke/%s' % cluster:
                return [keeper]
            if query.get('$or'):
                return [keeper, extra]
            return []

        imp.mongo_resources.distinct.side_effect = _distinct
        imp.mongo_resources.find.side_effect = _find
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow_timestamp',
                return_value=1787611589):
            imp._rekey_collapsed_labeled_resources()
        imp.mongo_resources.update_many.assert_not_called()
        imp.update_clickhouse_expenses.assert_not_called()

    def test_rekey_labeled_skips_billing_sku_even_when_raw_rewritten(self):
        from tools.cloud_adapter.gcp_resource_collapse import (
            ENCODED_COMPOSER_UUID_FIELD,
            encode_tag_key,
        )
        uuid = '6530bd7f-4f2d-4497-9465-f0d341b9a441'
        sku = '9E4E-F9A7-5EAE'
        imp = self._importer()
        imp.clickhouse_cl = MagicMock()
        imp.update_clickhouse_expenses = MagicMock()
        keeper = {
            '_id': 'keep-id',
            'cloud_resource_id': 'composer/%s' % uuid,
            'first_seen': 10, 'last_seen': 40,
            'tags': {encode_tag_key('goog-composer-environment-uuid'): uuid},
        }
        extra = {
            '_id': 'sku-mongo',
            'cloud_resource_id': sku,
            'resource_type': 'Cloud Composer',
            'first_seen': 20, 'last_seen': 50,
            'tags': {encode_tag_key('goog-composer-environment-uuid'): uuid},
        }

        def _distinct(field, query=None):
            if field == ENCODED_COMPOSER_UUID_FIELD:
                return [uuid]
            return []

        def _find(query, projection=None):
            if query.get('cloud_resource_id') == 'composer/%s' % uuid:
                return [keeper]
            if query.get('$or'):
                return [keeper, extra]
            return []

        imp.mongo_resources.distinct.side_effect = _distinct
        imp.mongo_resources.find.side_effect = _find
        imp.mongo_raw.find_one.return_value = None
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow_timestamp',
                return_value=1786700000):
            imp._rekey_collapsed_labeled_resources()
        imp.mongo_resources.update_many.assert_not_called()
        imp.update_clickhouse_expenses.assert_not_called()

    def test_rekey_sku_leftovers_rewrites_unlabeled_raw_then_folds(self):
        from tools.cloud_adapter.gcp_resource_collapse import (
            encode_tag_key,
        )
        uuid = 'c1123396-1a25-46a6-b0f1-ec4d792b0f84'
        sku = '25C6-4E91-086B'
        keeper_id = 'composer/%s' % uuid
        imp = self._importer()
        imp.clickhouse_cl = MagicMock()
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
            ('sku-mongo', '2026-07-01', '202607', 1.25),
        ])
        imp.update_clickhouse_expenses = MagicMock()
        keeper = {
            '_id': 'keep-id',
            'cloud_resource_id': keeper_id,
            'first_seen': 10, 'last_seen': 40,
            'tags': {encode_tag_key('goog-composer-environment-uuid'): uuid},
        }
        extra = {
            '_id': 'sku-mongo',
            'cloud_resource_id': sku,
            'resource_type': 'Cloud Composer',
            'first_seen': 20, 'last_seen': 50,
            'tags': {encode_tag_key('goog-composer-environment-uuid'): uuid},
        }

        def _find(query, projection=None):
            crid = query.get('cloud_resource_id')
            if crid == keeper_id:
                return [keeper]
            if isinstance(crid, dict) and crid.get('$regex'):
                return [extra]
            return []

        imp.mongo_resources.find.side_effect = _find
        imp.mongo_raw.find_one.return_value = {'tags': {}}
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow_timestamp',
                return_value=1786700000):
            imp._rekey_collapsed_sku_leftovers()
        raw_set = imp.mongo_raw.update_many.call_args[0]
        self.assertEqual(raw_set[0]['resource_id'], sku)
        self.assertEqual(raw_set[1]['$set']['resource_id'], keeper_id)
        extras = imp.mongo_resources.update_many.call_args[0]
        self.assertEqual(extras[0]['_id']['$in'], ['sku-mongo'])
        self.assertEqual(extras[1], {'$set': {'deleted_at': 1786700000}})
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        self.assertEqual(payload[0][1], 'keep-id')
        self.assertEqual(payload[1][1], 'sku-mongo')
        self.assertEqual(payload[1][4], -1)

    def test_rekey_sku_rewrites_raw_when_leftover_already_deleted(self):
        from tools.cloud_adapter.gcp_resource_collapse import encode_tag_key
        uuid = 'c1123396-1a25-46a6-b0f1-ec4d792b0f84'
        sku = '25C6-4E91-086B'
        keeper_id = 'composer/%s' % uuid
        imp = self._importer()
        imp.update_clickhouse_expenses = MagicMock()
        extra = {
            '_id': 'sku-mongo',
            'cloud_resource_id': sku,
            'resource_type': 'Cloud Composer',
            'deleted_at': 1786700000,
            'tags': {encode_tag_key('goog-composer-environment-uuid'): uuid},
        }

        def _find(query, projection=None):
            crid = query.get('cloud_resource_id')
            if isinstance(crid, dict) and crid.get('$regex'):
                return [extra]
            return []

        imp.mongo_resources.find.side_effect = _find
        imp.mongo_raw.find_one.return_value = {'tags': {}}
        imp._rekey_collapsed_sku_leftovers()
        raw_set = imp.mongo_raw.update_many.call_args[0]
        self.assertEqual(raw_set[0]['resource_id'], sku)
        self.assertEqual(raw_set[1]['$set']['resource_id'], keeper_id)
        imp.mongo_resources.update_many.assert_not_called()
        imp.update_clickhouse_expenses.assert_not_called()

    def test_rekey_labeled_keeps_one_dataproc_and_moves_clickhouse(self):
        from tools.cloud_adapter.gcp_resource_collapse import (
            ENCODED_DATAPROC_CLUSTER_UUID_FIELD,
            encode_tag_key,
        )
        uuid = '5fcf5527-4bb6-4991-82f4-55b339a0a2af'
        imp = self._importer()
        imp.clickhouse_cl = MagicMock()
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
            ('node-id', '2026-07-01', '202607', 224.8),
        ])
        imp.update_clickhouse_expenses = MagicMock()
        keeper = {
            '_id': 'keep-id',
            'cloud_resource_id': 'dataproc/%s' % uuid,
            'first_seen': 10, 'last_seen': 40,
            'tags': {encode_tag_key('goog-dataproc-cluster-uuid'): uuid},
        }
        extra = {
            '_id': 'node-id',
            'cloud_resource_id': '1908938302707523326',
            'first_seen': 20, 'last_seen': 50,
            'cluster_id': 'cluster-dp',
            'tags': {
                encode_tag_key('goog-dataproc-cluster-uuid'): uuid,
                encode_tag_key('goog-dataproc-cluster-name'): 'dataproc',
            },
        }

        def _distinct(field, query=None):
            if field == ENCODED_DATAPROC_CLUSTER_UUID_FIELD:
                return [uuid]
            return []

        def _find(query, projection=None):
            if query.get('cloud_resource_id') == 'dataproc/%s' % uuid:
                return [keeper]
            if query.get('$or'):
                return [keeper, extra]
            return []

        imp.mongo_resources.distinct.side_effect = _distinct
        imp.mongo_resources.find.side_effect = _find
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow_timestamp',
                return_value=1786700000):
            imp._rekey_collapsed_labeled_resources()
        set_call = imp.mongo_resources.update_one.call_args[0]
        self.assertEqual(set_call[0], {'_id': 'keep-id'})
        self.assertEqual(
            set_call[1]['$set']['cloud_resource_id'],
            'dataproc/%s' % uuid)
        extras = imp.mongo_resources.update_many.call_args[0]
        self.assertEqual(extras[0]['_id']['$in'], ['node-id'])
        self.assertEqual(extras[1], {'$set': {'deleted_at': 1786700000}})
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        self.assertEqual(payload[0][1], 'keep-id')
        self.assertEqual(payload[1][1], 'node-id')
        self.assertEqual(payload[1][4], -1)
        self.assertEqual(imp._collapsed_member_cluster_ids, ['cluster-dp'])

    def test_reassign_deleted_collapsed_twin_moves_clickhouse(self):
        imp = self._importer()
        imp.clickhouse_cl = MagicMock()
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
            ('dead-id', '2026-07-01', '202607', 345.8),
        ])
        imp.update_clickhouse_expenses = MagicMock()
        live = {
            '_id': 'live-id',
            'cloud_resource_id': 'gke/pf-data-processing-prod-gke',
        }
        dead = {
            '_id': 'dead-id',
            'cloud_resource_id': 'gke/pf-data-processing-prod-gke',
        }

        def _find(query, projection=None):
            if query.get('deleted_at') == 0:
                return [live]
            if isinstance(query.get('deleted_at'), dict):
                return [dead]
            return []

        imp.mongo_resources.find.side_effect = _find
        imp._reassign_deleted_collapsed_identity_expenses()
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0][1], 'live-id')
        self.assertEqual(payload[0][4], 1)
        self.assertEqual(payload[1][1], 'dead-id')
        self.assertEqual(payload[1][4], -1)

    def test_reassign_deleted_collapsed_twin_skips_days_already_on_live(self):
        imp = self._importer()
        imp.clickhouse_cl = MagicMock()
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
            ('live-id', '2026-07-01', '202607', 345.8),
            ('dead-id', '2026-07-01', '202607', 345.8),
        ])
        imp.update_clickhouse_expenses = MagicMock()
        live = {
            '_id': 'live-id',
            'cloud_resource_id': 'gke/pf-data-processing-prod-gke',
        }
        dead = {
            '_id': 'dead-id',
            'cloud_resource_id': 'gke/pf-data-processing-prod-gke',
        }

        def _find(query, projection=None):
            if query.get('deleted_at') == 0:
                return [live]
            if isinstance(query.get('deleted_at'), dict):
                return [dead]
            return []

        imp.mongo_resources.find.side_effect = _find
        imp._reassign_deleted_collapsed_identity_expenses()
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0][1], 'dead-id')
        self.assertEqual(payload[0][4], -1)

    def test_reassign_deleted_sku_twin_moves_clickhouse(self):
        """Ordinary SKU/SQL twins, not only gke/composer keepers."""
        imp = self._importer()
        imp.clickhouse_cl = MagicMock()
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
            ('dead-sql', '2026-08-01', '202608', 41.03),
        ])
        imp.update_clickhouse_expenses = MagicMock()
        live = {'_id': 'live-sql', 'cloud_resource_id': 'cfg-db'}
        dead = {'_id': 'dead-sql', 'cloud_resource_id': 'cfg-db'}

        def _find(query, projection=None):
            if query.get('deleted_at') == 0:
                return [live]
            if isinstance(query.get('deleted_at'), dict):
                return [dead]
            return []

        imp.mongo_resources.find.side_effect = _find
        imp._reassign_deleted_collapsed_identity_expenses()
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0][1], 'live-sql')
        self.assertEqual(payload[0][4], 1)
        self.assertEqual(payload[1][1], 'dead-sql')
        self.assertEqual(payload[1][4], -1)

    def test_negate_deleted_collapsed_does_not_copy_onto_keeper(self):
        imp = self._importer()
        imp.clickhouse_cl = MagicMock()
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
            ('pvc-id', '2026-07-01', '202607', 509.44),
        ])
        imp.update_clickhouse_expenses = MagicMock()
        imp._reassign_deleted_collapsed_identity_expenses = MagicMock()

        def _find(query, projection=None):
            if query.get('deleted_at') == 0:
                return [{'_id': 'live-id',
                         'cloud_resource_id': 'gke/pf-da-shared-nonprod-gke'}]
            return [{'_id': 'pvc-id', 'cloud_resource_id': '7175957273002431882'}]

        imp.mongo_resources.find.side_effect = _find
        imp._negate_deleted_collapsed_clickhouse_expenses()
        imp._reassign_deleted_collapsed_identity_expenses.assert_called_once()
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0][1], 'pvc-id')
        self.assertEqual(payload[0][3], 509.44)
        self.assertEqual(payload[0][4], -1)

    def test_negate_skips_same_id_deleted_twin(self):
        imp = self._importer()
        imp.clickhouse_cl = MagicMock()
        imp.update_clickhouse_expenses = MagicMock()
        imp._reassign_deleted_collapsed_identity_expenses = MagicMock()
        live_crid = 'gke/pf-da-shared-nonprod-gke'

        def _find(query, projection=None):
            if query.get('deleted_at') == 0:
                return [{'_id': 'live-id', 'cloud_resource_id': live_crid}]
            return [{'_id': 'dead-id', 'cloud_resource_id': live_crid}]

        imp.mongo_resources.find.side_effect = _find
        imp._negate_deleted_collapsed_clickhouse_expenses()
        imp._reassign_deleted_collapsed_identity_expenses.assert_called_once()
        imp.update_clickhouse_expenses.assert_not_called()

    def test_negate_skips_deleted_billing_sku(self):
        imp = self._importer()
        imp.clickhouse_cl = MagicMock()
        imp.update_clickhouse_expenses = MagicMock()
        imp._reassign_deleted_collapsed_identity_expenses = MagicMock()
        sku = '9E4E-F9A7-5EAE'
        imp.mongo_raw.distinct.return_value = [sku]

        def _find(query, projection=None):
            if query.get('deleted_at') == 0:
                return [{'_id': 'live-id',
                         'cloud_resource_id': 'gke/pf-da-shared-nonprod-gke'}]
            return [{'_id': 'sku-id', 'cloud_resource_id': sku}]

        imp.mongo_resources.find.side_effect = _find
        imp._negate_deleted_collapsed_clickhouse_expenses()
        imp._reassign_deleted_collapsed_identity_expenses.assert_called_once()
        imp.update_clickhouse_expenses.assert_not_called()

    def test_negate_deleted_billing_sku_without_raw(self):
        """After detailed rewrite leftover SKU CH is ghost (no mongo_raw)."""
        imp = self._importer()
        imp.clickhouse_cl = MagicMock()
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
            ('sku-id', '2026-07-01', '202607', 12.5),
        ])
        imp.update_clickhouse_expenses = MagicMock()
        imp._reassign_deleted_collapsed_identity_expenses = MagicMock()
        sku = '8111-FEE9-BFEE'
        imp.mongo_raw.distinct.return_value = []

        def _find(query, projection=None):
            if query.get('deleted_at') == 0:
                return [{'_id': 'live-id',
                         'cloud_resource_id': 'cloudsql/cfg-db'}]
            return [{'_id': 'sku-id', 'cloud_resource_id': sku}]

        imp.mongo_resources.find.side_effect = _find
        imp._negate_deleted_collapsed_clickhouse_expenses()
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0][1], 'sku-id')
        self.assertEqual(payload[0][3], 12.5)
        self.assertEqual(payload[0][4], -1)

    def test_restore_miscollapsed_billing_sku_undeletes_when_raw_exists(self):
        sku = '9E4E-F9A7-5EAE'
        imp = self._importer()
        imp.mongo_resources.find.return_value = [
            {'_id': 'sku-mongo', 'cloud_resource_id': sku,
             'resource_type': 'Instance', 'tags': {}}]
        imp.mongo_resources.find_one.return_value = None
        imp.mongo_raw.find_one.return_value = {'_id': 'raw-1'}
        imp._restore_miscollapsed_billing_sku_resources()
        imp.mongo_resources.update_one.assert_called_once_with(
            {'_id': 'sku-mongo'}, {'$set': {'deleted_at': 0}})

    def test_negate_skips_deleted_billed_collapsed_keeper(self):
        crid = 'cloudrun/pf-dedup-api-service'
        imp = self._importer()
        imp.clickhouse_cl = MagicMock()
        imp.update_clickhouse_expenses = MagicMock()
        imp._reassign_deleted_collapsed_identity_expenses = MagicMock()
        imp.mongo_raw.distinct.return_value = [crid]

        def _find(query, projection=None):
            if query.get('deleted_at') == 0:
                return [{'_id': 'gke-id',
                         'cloud_resource_id': 'gke/pf-data-enrichment-nonprod-gke'}]
            return [{'_id': 'run-id', 'cloud_resource_id': crid}]

        imp.mongo_resources.find.side_effect = _find
        imp._negate_deleted_collapsed_clickhouse_expenses()
        imp.update_clickhouse_expenses.assert_not_called()

    def test_restore_skips_composer_family_even_when_raw_exists(self):
        from tools.cloud_adapter.gcp_resource_collapse import encode_tag_key
        uuid = 'c1123396-1a25-46a6-b0f1-ec4d792b0f84'
        sku = '25C6-4E91-086B'
        imp = self._importer()
        imp.mongo_resources.find.return_value = [{
            '_id': 'sku-mongo',
            'cloud_resource_id': sku,
            'resource_type': 'Cloud Composer',
            'tags': {encode_tag_key('goog-composer-environment-uuid'): uuid},
        }]
        imp.mongo_raw.find_one.return_value = {'_id': 'raw-1'}
        imp._restore_miscollapsed_billing_sku_resources()
        imp.mongo_resources.update_one.assert_not_called()
        imp.mongo_resources.find_one.assert_not_called()

    def test_restore_undeletes_instance_sku_with_inherited_tags(self):
        from tools.cloud_adapter.gcp_resource_collapse import encode_tag_key
        sku = 'A03E-E620-7389'
        imp = self._importer()
        imp.mongo_resources.find.return_value = [{
            '_id': 'sku-mongo',
            'cloud_resource_id': sku,
            'resource_type': 'Instance',
            'tags': {
                encode_tag_key('goog-k8s-cluster-name'): 'pf-sns-prod-gke',
            },
        }]
        imp.mongo_resources.find_one.return_value = None
        imp.mongo_raw.find_one.return_value = {'_id': 'raw-1'}
        imp._restore_miscollapsed_billing_sku_resources()
        imp.mongo_resources.update_one.assert_called_once_with(
            {'_id': 'sku-mongo'}, {'$set': {'deleted_at': 0}})

    def test_restore_miscollapsed_billing_sku_skips_live_twin(self):
        imp = self._importer()
        imp.mongo_resources.find.return_value = [
            {'_id': 'dead-sku', 'cloud_resource_id': '9E4E-F9A7-5EAE'}]
        imp.mongo_resources.find_one.return_value = {'_id': 'live-sku'}
        imp._restore_miscollapsed_billing_sku_resources()
        imp.mongo_resources.update_one.assert_not_called()
        imp.mongo_raw.find_one.assert_not_called()

    def test_restore_miscollapsed_billing_sku_skips_without_raw(self):
        imp = self._importer()
        imp.mongo_resources.find.return_value = [
            {'_id': 'dead-sku', 'cloud_resource_id': '9E4E-F9A7-5EAE'}]
        imp.mongo_resources.find_one.return_value = None
        imp.mongo_raw.find_one.return_value = None
        imp._restore_miscollapsed_billing_sku_resources()
        imp.mongo_resources.update_one.assert_not_called()

    def test_recalculate_skips_clickhouse_fail_gate(self):
        imp = self._importer()
        object.__setattr__(imp, 'recalculate', True)
        object.__setattr__(imp, '_cloud_adapter', MagicMock(
            is_virtual_billing_project=False))
        imp.clickhouse_cl = MagicMock()
        imp.mongo_resources = MagicMock()
        imp.mongo_resources.find.return_value = []
        imp._clickhouse_month_by_resource_type = MagicMock(
            return_value={'GKE': {'cost': 100.0, 'resource_count': 1}})
        imp._reconciliation_rows_from_mongo = MagicMock(
            return_value=([], 0.0))
        imp._merge_target_into_reconciliation = MagicMock(return_value=[])
        imp._record_billed_check = MagicMock()
        imp._verify_clickhouse_target_cost = MagicMock(
            side_effect=AssertionError('fail-gate must not run'))
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow',
                return_value=datetime(2026, 8, 15, tzinfo=timezone.utc)):
            imp._reconcile_clickhouse_target()
        imp._verify_clickhouse_target_cost.assert_not_called()


@unittest.skipIf(
    GcpReportImporter is None,
    'diworker runtime deps missing: %s' % _IMPORT_ERROR)
class TestGcpUnlabeledGkePvcCleanup(unittest.TestCase):
    def _importer(self):
        imp = GcpReportImporter.__new__(GcpReportImporter)
        object.__setattr__(imp, 'cloud_acc_id', 'ca-gcp-1')
        object.__setattr__(imp, 'mongo_raw', MagicMock())
        object.__setattr__(imp, 'mongo_resources', MagicMock())
        object.__setattr__(imp, '_discovery_resource_ids', set())
        adapter = MagicMock()
        adapter.is_virtual_billing_project = False
        adapter.zone_region.side_effect = lambda z: z
        object.__setattr__(imp, '_cloud_adapter', adapter)
        return imp

    def _row(self, **overrides):
        base = dict(
            service='Compute Engine',
            start_date=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
            end_date=datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc),
            invoice_month='202606',
            cost=1.0,
            location={'region': 'us-central1'},
            sku='Storage PD Capacity',
            sku_id='6F81-5844-456A',
            tags=[],
            system_tags=[],
            credits=[],
            resource_name='pvc-537c475a-c155-45f4-b89c-dfec36a5d939',
            resource_global_name=(
                '//compute.googleapis.com/projects/1/zones/z/disk/'
                '7175957273002431882'),
        )
        base.update(overrides)
        return _FakeRow(**base)

    def test_unique_mongo_gke_name(self):
        imp = self._importer()
        imp.mongo_resources.distinct.return_value = [
            'gke/pf-da-shared-nonprod-gke']
        imp.mongo_raw.distinct.return_value = []
        self.assertEqual(
            imp._unique_gke_cluster(), 'pf-da-shared-nonprod-gke')

    def test_two_gke_clusters_are_ambiguous(self):
        imp = self._importer()
        imp.mongo_resources.distinct.return_value = ['gke/a', 'gke/b']
        imp.mongo_raw.distinct.return_value = []
        self.assertIsNone(imp._unique_gke_cluster())

    def test_composer_raw_k8s_names_do_not_hide_unique_mongo_gke(self):
        """Measured on pf-da-shared-nonprod: one live gke/ plus five
        Composer GKE names in raw tags.goog-k8s-cluster-name."""
        imp = self._importer()
        imp.mongo_resources.distinct.return_value = [
            'gke/pf-da-shared-nonprod-gke']
        imp.mongo_raw.distinct.return_value = [
            'us-central1-da-shared-airfl-755e2519-gke',
            'pf-da-shared-nonprod-gke',
            'us-central1-da-shared-airfl-f9dfbfbe-gke',
            'us-central1-da-shared-airfl-3ab7adae-gke',
            'us-central1-da-shared-airfl-c1d5f922-gke',
            'us-central1-da-shared-airfl-22ed3fdf-gke',
        ]
        self.assertEqual(
            imp._unique_gke_cluster(), 'pf-da-shared-nonprod-gke')
        imp.mongo_raw.distinct.assert_not_called()

    def test_unlabeled_pvc_collapses_when_raw_has_composer_gke_names(self):
        imp = self._importer()
        imp.mongo_resources.distinct.return_value = [
            'gke/pf-da-shared-nonprod-gke']
        imp.mongo_raw.distinct.return_value = [
            'us-central1-da-shared-airfl-755e2519-gke',
            'pf-da-shared-nonprod-gke',
        ]
        item = imp._row_to_dict(self._row())
        self.assertEqual(
            item['resource_id'], 'gke/pf-da-shared-nonprod-gke')

    def test_none_when_empty(self):
        imp = self._importer()
        imp.mongo_resources.distinct.return_value = []
        imp.mongo_raw.distinct.return_value = []
        self.assertIsNone(imp._unique_gke_cluster())

    def test_none_when_no_live_gke_even_if_raw_has_names(self):
        imp = self._importer()
        imp.mongo_resources.distinct.return_value = []
        imp.mongo_raw.distinct.return_value = [
            'us-central1-da-shared-airfl-755e2519-gke']
        self.assertIsNone(imp._unique_gke_cluster())
        imp.mongo_raw.distinct.assert_not_called()

    def test_other_collapse_still_wins_with_unique_mongo_gke(self):
        imp = self._importer()
        imp.mongo_resources.distinct.return_value = [
            'gke/pf-da-shared-nonprod-gke']
        composer = imp._row_to_dict(self._row(tags=[
            {'key': 'goog-composer-environment-uuid',
             'value': 'e8711ea5-ec4c-4ece-bad1-e54b3b6a923c'},
        ]))
        self.assertEqual(
            composer['resource_id'],
            'composer/e8711ea5-ec4c-4ece-bad1-e54b3b6a923c')
        dataproc = imp._row_to_dict(self._row(tags=[
            {'key': 'goog-dataproc-cluster-uuid',
             'value': '5fcf5527-4bb6-4991-82f4-55b339a0a2af'},
            {'key': 'goog-dataproc-cluster-name', 'value': 'dataproc'},
        ]))
        self.assertEqual(
            dataproc['resource_id'],
            'dataproc/5fcf5527-4bb6-4991-82f4-55b339a0a2af')
        labeled = imp._row_to_dict(self._row(tags=[
            {'key': 'goog-k8s-cluster-name', 'value': 'real-cluster'},
        ]))
        self.assertEqual(labeled['resource_id'], 'gke/real-cluster')
        standalone = imp._row_to_dict(self._row(
            resource_name='calcdsnl1-data',
            resource_global_name=(
                '//compute.googleapis.com/projects/1/zones/z/disk/'
                '2534772272498265879'),
        ))
        self.assertEqual(standalone['resource_id'], '2534772272498265879')

    def test_row_to_dict_pvc_uses_unique_gke_cluster(self):
        imp = self._importer()
        imp._unique_gke_cluster = MagicMock(
            return_value='pf-da-shared-nonprod-gke')
        item = imp._row_to_dict(self._row())
        self.assertEqual(item['resource_id'], 'gke/pf-da-shared-nonprod-gke')

    def test_row_to_dict_pvc_keeps_numeric_without_unique_cluster(self):
        imp = self._importer()
        imp._unique_gke_cluster = MagicMock(return_value=None)
        item = imp._row_to_dict(self._row())
        self.assertEqual(item['resource_id'], '7175957273002431882')

    def test_row_to_dict_standalone_disk_keeps_numeric_id(self):
        imp = self._importer()
        imp._unique_gke_cluster = MagicMock(
            return_value='pf-da-shared-nonprod-gke')
        item = imp._row_to_dict(self._row(
            resource_name='calcdsnl1-data',
            resource_global_name=(
                '//compute.googleapis.com/projects/1/zones/z/disk/'
                '2534772272498265879'),
        ))
        self.assertEqual(item['resource_id'], '2534772272498265879')
        imp._unique_gke_cluster.assert_not_called()

    def test_row_to_dict_instance_keeps_numeric_id(self):
        imp = self._importer()
        imp._unique_gke_cluster = MagicMock(
            return_value='pf-da-shared-nonprod-gke')
        item = imp._row_to_dict(self._row(
            sku='N2D AMD Instance Core running in Americas',
            sku_id='5535-6D2D-4B50',
            resource_name='proxygwd-nonprod-01',
            resource_global_name=(
                '//compute.googleapis.com/projects/1/zones/z/instances/'
                '746230590525317299'),
        ))
        self.assertEqual(item['resource_id'], '746230590525317299')
        imp._unique_gke_cluster.assert_not_called()

    def test_row_to_dict_snapshot_keeps_numeric_id(self):
        imp = self._importer()
        imp._unique_gke_cluster = MagicMock(
            return_value='pf-da-shared-nonprod-gke')
        item = imp._row_to_dict(self._row(
            sku='Storage PD Snapshot',
            resource_name='cfg-db',
            resource_global_name=(
                '//compute.googleapis.com/projects/1/global/snapshots/'
                '4758898924634663632'),
        ))
        self.assertEqual(item['resource_id'], '4758898924634663632')
        imp._unique_gke_cluster.assert_not_called()

    def test_row_to_dict_ip_keeps_numeric_id(self):
        imp = self._importer()
        imp._unique_gke_cluster = MagicMock(
            return_value='pf-da-shared-nonprod-gke')
        item = imp._row_to_dict(self._row(
            sku='Static Ip Charge',
            resource_name='serverless-ipv4',
            resource_global_name=(
                '//compute.googleapis.com/projects/1/regions/r/addresses/'
                '952981511495052626'),
        ))
        self.assertEqual(item['resource_id'], '952981511495052626')
        imp._unique_gke_cluster.assert_not_called()

    def test_row_to_dict_image_keeps_numeric_id(self):
        imp = self._importer()
        imp._unique_gke_cluster = MagicMock(
            return_value='pf-da-shared-nonprod-gke')
        item = imp._row_to_dict(self._row(
            sku='Storage Image',
            resource_name='custom-image',
            resource_global_name=(
                '//compute.googleapis.com/projects/1/global/images/'
                '123456789012345678'),
        ))
        self.assertEqual(item['resource_id'], '123456789012345678')
        imp._unique_gke_cluster.assert_not_called()

    def test_row_to_dict_bucket_keeps_bucket_name(self):
        imp = self._importer()
        imp._unique_gke_cluster = MagicMock(
            return_value='pf-da-shared-nonprod-gke')
        item = imp._row_to_dict(self._row(
            service='Cloud Storage',
            sku='Standard Storage',
            resource_name='pvc-bucket',
            resource_global_name=(
                '//storage.googleapis.com/projects/_/buckets/pvc-bucket'),
        ))
        self.assertEqual(item['resource_id'], 'pvc-bucket')
        imp._unique_gke_cluster.assert_not_called()

    def test_row_to_dict_dataproc_volume_keeps_dataproc_id(self):
        imp = self._importer()
        imp._unique_gke_cluster = MagicMock(
            return_value='pf-da-shared-nonprod-gke')
        item = imp._row_to_dict(self._row(tags=[
            {'key': 'goog-dataproc-cluster-uuid',
             'value': '5fcf5527-4bb6-4991-82f4-55b339a0a2af'},
            {'key': 'goog-dataproc-cluster-name', 'value': 'dataproc'},
        ]))
        self.assertEqual(
            item['resource_id'],
            'dataproc/5fcf5527-4bb6-4991-82f4-55b339a0a2af')
        imp._unique_gke_cluster.assert_not_called()

    def test_row_to_dict_composer_keeps_composer_id(self):
        imp = self._importer()
        imp._unique_gke_cluster = MagicMock(
            return_value='pf-da-shared-nonprod-gke')
        item = imp._row_to_dict(self._row(tags=[
            {'key': 'goog-composer-environment-uuid',
             'value': 'e8711ea5-ec4c-4ece-bad1-e54b3b6a923c'},
        ]))
        self.assertEqual(
            item['resource_id'],
            'composer/e8711ea5-ec4c-4ece-bad1-e54b3b6a923c')
        imp._unique_gke_cluster.assert_not_called()

    def test_row_to_dict_labeled_gke_ignores_other_cluster(self):
        imp = self._importer()
        imp._unique_gke_cluster = MagicMock(return_value='wrong-cluster')
        item = imp._row_to_dict(self._row(tags=[
            {'key': 'goog-k8s-cluster-name', 'value': 'real-cluster'},
        ]))
        self.assertEqual(item['resource_id'], 'gke/real-cluster')
        imp._unique_gke_cluster.assert_not_called()

    def test_rekey_skips_when_no_unique_cluster(self):
        imp = self._importer()
        imp._unique_gke_cluster = MagicMock(return_value=None)
        imp._rekey_unlabeled_gke_pvc_resources()
        imp.mongo_resources.find.assert_not_called()

    def test_rekey_skips_when_no_pvc_members(self):
        imp = self._importer()
        imp._unique_gke_cluster = MagicMock(
            return_value='pf-da-shared-nonprod-gke')
        imp.mongo_resources.find.return_value = []
        imp._rekey_unlabeled_gke_pvc_resources()
        imp.mongo_resources.update_one.assert_not_called()
        imp.mongo_resources.update_many.assert_not_called()

    def test_rekey_keeps_gke_and_moves_clickhouse(self):
        from tools.cloud_adapter.gcp_resource_collapse import (
            unlabeled_gke_pvc_resource_filter,
        )
        imp = self._importer()
        imp._unique_gke_cluster = MagicMock(
            return_value='pf-da-shared-nonprod-gke')
        imp.clickhouse_cl = MagicMock()
        imp.clickhouse_cl.query.return_value = MagicMock(result_rows=[
            ('pvc-id', '2026-06-01', '', 3.5),
        ])
        imp.update_clickhouse_expenses = MagicMock()
        keeper = {
            '_id': 'keep-id',
            'cloud_resource_id': 'gke/pf-da-shared-nonprod-gke',
            'first_seen': 10, 'last_seen': 40,
        }
        extra = {
            '_id': 'pvc-id',
            'cloud_resource_id': '7175957273002431882',
            'first_seen': 20, 'last_seen': 50,
            'cluster_id': 'cluster-gke',
        }

        def _find(query, projection=None):
            if query.get('cloud_resource_id') == (
                    'gke/pf-da-shared-nonprod-gke'):
                return [keeper]
            if query.get('resource_type') == 'Volume':
                self.assertEqual(
                    query, unlabeled_gke_pvc_resource_filter('ca-gcp-1'))
                return [extra]
            return []

        imp.mongo_resources.find.side_effect = _find
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow_timestamp',
                return_value=1786700000):
            imp._rekey_unlabeled_gke_pvc_resources()
        set_call = imp.mongo_resources.update_one.call_args[0]
        self.assertEqual(set_call[0], {'_id': 'keep-id'})
        self.assertEqual(
            set_call[1]['$set']['cloud_resource_id'],
            'gke/pf-da-shared-nonprod-gke')
        self.assertEqual(set_call[1]['$set']['resource_type'], 'GKE')
        extras = imp.mongo_resources.update_many.call_args[0]
        self.assertEqual(extras[0]['_id']['$in'], ['pvc-id'])
        self.assertEqual(extras[1], {'$set': {'deleted_at': 1786700000}})
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        self.assertEqual(payload[0][1], 'keep-id')
        self.assertEqual(payload[1][1], 'pvc-id')
        self.assertEqual(payload[1][4], -1)
        self.assertEqual(imp._collapsed_member_cluster_ids, ['cluster-gke'])

    def test_generate_clean_records_rekeys_unlabeled_pvc_after_labeled(self):
        imp = self._importer()
        order = []

        def _track(name):
            def _inner(*args, **kwargs):
                order.append(name)
            return _inner

        for name in (
                '_rewrite_serverless_dataproc_raw_ids',
                '_rewrite_labeled_collapse_raw_ids',
                '_rewrite_detailed_collapse_raw_ids',
                '_rekey_stale_serverless_dataproc_resources',
                '_rekey_collapsed_labeled_resources',
                '_rekey_collapsed_sku_leftovers',
                '_rekey_detailed_collapse_leftovers',
                '_rekey_unlabeled_gke_pvc_resources',
                '_dedupe_collapsed_gcp_identity_resources',
                '_restore_miscollapsed_billing_sku_resources',
                '_retire_stale_serverless_dataproc_resources',
                '_negate_deleted_collapsed_clickhouse_expenses',
                '_refresh_resource_duplicates'):
            setattr(imp, name, _track(name))
        object.__setattr__(imp, 'period_start', None)
        imp.mongo_raw.aggregate.return_value = []
        imp.generate_clean_records()
        self.assertEqual(order, [
            '_rewrite_serverless_dataproc_raw_ids',
            '_rewrite_labeled_collapse_raw_ids',
            '_rewrite_detailed_collapse_raw_ids',
            '_rekey_stale_serverless_dataproc_resources',
            '_rekey_collapsed_labeled_resources',
            '_rekey_collapsed_sku_leftovers',
            '_rekey_detailed_collapse_leftovers',
            '_rekey_unlabeled_gke_pvc_resources',
            '_dedupe_collapsed_gcp_identity_resources',
            '_restore_miscollapsed_billing_sku_resources',
            '_retire_stale_serverless_dataproc_resources',
            '_negate_deleted_collapsed_clickhouse_expenses',
            '_refresh_resource_duplicates',
        ])


@unittest.skipIf(
    GcpReportImporter is None,
    'diworker runtime deps missing: %s' % _IMPORT_ERROR)
class TestGcpDetailedCollapseCleanup(unittest.TestCase):
    def _importer(self):
        imp = GcpReportImporter.__new__(GcpReportImporter)
        object.__setattr__(imp, 'cloud_acc_id', 'ca-gcp-1')
        object.__setattr__(imp, 'mongo_raw', MagicMock())
        object.__setattr__(imp, 'mongo_resources', MagicMock())
        return imp

    def test_rewrite_sqladmin_records_old_id_mapping(self):
        imp = self._importer()
        imp.mongo_raw.update_many.return_value = MagicMock(modified_count=1)

        def _distinct(field, filt=None):
            gname = (filt or {}).get('resource_global_name', {})
            regex = gname.get('$regex', '') if isinstance(gname, dict) else ''
            if field == 'resource_name' and 'sqladmin' in regex:
                return ['cfg-db']
            if field == 'resource_id' and (
                    filt or {}).get('resource_name') == 'cfg-db':
                return ['cfg-db']
            return []

        imp.mongo_raw.distinct.side_effect = _distinct
        imp.mongo_raw.find_one.return_value = {
            'resource_global_name': (
                '//sqladmin.googleapis.com/projects/p/instances/cfg-db'),
            'resource_name': 'cfg-db',
        }
        imp._rewrite_detailed_collapse_raw_ids()
        self.assertEqual(
            imp._detailed_collapse_old_to_keepers,
            {'cfg-db': {'cloudsql/cfg-db'}})
        set_call = imp.mongo_raw.update_many.call_args_list[0][0]
        self.assertEqual(set_call[1], {'$set': {'resource_id': 'cloudsql/cfg-db'}})

    def test_rekey_unique_sql_instance_folds_onto_keeper(self):
        imp = self._importer()
        leftover = {
            '_id': 'sql-old', 'cloud_resource_id': 'cfg-db',
            'resource_type': 'Cloud SQL', 'first_seen': 10, 'last_seen': 20,
        }
        keeper = {
            '_id': 'sql-keep', 'cloud_resource_id': 'cloudsql/cfg-db',
            'first_seen': 5, 'last_seen': 15,
        }
        imp._detailed_collapse_old_to_keepers = {
            'cfg-db': {'cloudsql/cfg-db'},
        }

        def _find(query, projection=None):
            crid = query.get('cloud_resource_id')
            if isinstance(crid, dict) and '$in' in crid:
                return [leftover]
            if query.get('resource_type', {}).get('$in'):
                return []
            if crid == 'cloudsql/cfg-db':
                return [keeper]
            return []

        imp.mongo_resources.find.side_effect = _find
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow_timestamp',
                return_value=1786700000):
            imp._rekey_detailed_collapse_leftovers()
        set_call = imp.mongo_resources.update_one.call_args[0]
        self.assertEqual(set_call[0], {'_id': 'sql-keep'})
        self.assertEqual(
            set_call[1]['$set']['cloud_resource_id'], 'cloudsql/cfg-db')
        extras = imp.mongo_resources.update_many.call_args[0]
        self.assertEqual(extras[0]['_id']['$in'], ['sql-old'])
        self.assertEqual(extras[1], {'$set': {'deleted_at': 1786700000}})

    def test_rekey_mixed_run_sku_is_retired_not_merged(self):
        imp = self._importer()
        leftover = {
            '_id': 'run-sku', 'cloud_resource_id': '02A2-9231-36A6',
            'resource_type': 'Cloud Run', 'first_seen': 10, 'last_seen': 20,
        }
        imp._detailed_collapse_old_to_keepers = {
            '02A2-9231-36A6': {'cloudrun/a', 'cloudrun/b'},
        }
        imp._negate_clickhouse_resource_ids = MagicMock()

        def _find(query, projection=None):
            crid = query.get('cloud_resource_id')
            if isinstance(crid, dict) and '$in' in crid:
                return [leftover]
            return []

        imp.mongo_resources.find.side_effect = _find
        imp.mongo_resources.update_many.return_value = MagicMock(
            modified_count=1)
        with patch(
                'diworker.diworker.importers.gcp.opttime.utcnow_timestamp',
                return_value=1786700000):
            imp._rekey_detailed_collapse_leftovers()
        imp.mongo_resources.update_one.assert_not_called()
        extras = imp.mongo_resources.update_many.call_args[0]
        self.assertEqual(extras[0]['_id']['$in'], ['run-sku'])
        self.assertEqual(extras[1], {'$set': {'deleted_at': 1786700000}})
        imp._negate_clickhouse_resource_ids.assert_called_once_with(
            ['run-sku'])

    def test_rekey_skips_sql_sku_without_identity(self):
        imp = self._importer()
        sku = {
            '_id': 'sql-sku', 'cloud_resource_id': '93DA-3F55-CB04',
            'resource_type': 'Cloud SQL',
            'name': 'Cloud SQL for PostgreSQL: Zonal - RAM in Americas',
        }
        imp._detailed_collapse_old_to_keepers = {}

        def _find(query, projection=None):
            if query.get('resource_type', {}).get('$in'):
                return [sku]
            return []

        imp.mongo_resources.find.side_effect = _find
        imp._rekey_detailed_collapse_leftovers()
        imp.mongo_resources.update_one.assert_not_called()
        imp.mongo_resources.update_many.assert_not_called()


if __name__ == '__main__':
    unittest.main()
