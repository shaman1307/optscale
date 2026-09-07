#!/usr/bin/env python
"""Unit tests for Snowflake report importer helpers.

Requires diworker runtime deps. On a bare host without those packages the
suite is skipped. Inside the diworker image:

  cd /src
  PYTHONPATH=/src:/src/tools /src/diworker/.venv/bin/python \\
    -m unittest diworker.diworker.tests.test_snowflake_importer -v
"""
import unittest
from collections import defaultdict
from datetime import datetime, timedelta, timezone as tz
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

try:
    from diworker.diworker.importers.snowflake import (
        META_FIELDS,
        RESOURCE_TYPE_RENAMES,
        SF_INCREMENTAL_LOOKBACK_DAYS,
        SnowflakeReportImporter,
    )
except ImportError as exc:  # pragma: no cover - host without deps
    SnowflakeReportImporter = None
    SF_INCREMENTAL_LOOKBACK_DAYS = 3
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


@unittest.skipIf(
    SnowflakeReportImporter is None,
    'diworker runtime deps missing: %s' % _IMPORT_ERROR)
class TestSnowflakeImporterHelpers(unittest.TestCase):
    def _importer(self, cloud_acc=None, cloud_adapter=None):
        # Avoid BaseReportImporter.__init__ (needs full DI wiring).
        imp = SnowflakeReportImporter.__new__(SnowflakeReportImporter)
        object.__setattr__(imp, 'cloud_acc_id', 'ca-1')
        # Match BaseReportImporter cache fields used by cloud_acc / cloud_adapter.
        object.__setattr__(imp, '_cloud_acc', cloud_acc or {
            'id': 'ca-1', 'type': 'snowflake'})
        object.__setattr__(imp, '_cloud_adapter', cloud_adapter)
        object.__setattr__(imp, '_is_tenant_import_cached', None)
        return imp

    def test_resource_info_maps_listing_meta(self):
        imp = self._importer()
        start = datetime(2026, 7, 1)
        expenses = [{
            'start_date': start,
            'end_date': start,
            'resource_id': 'HW/listing_consumption/L/S/C',
            'resource_name': 'My Listing',
            'service_type': 'LISTING_CONSUMPTION',
            'service_category': 'data_sharing',
            'account_locator': 'HW44440',
            'account_name': 'PUBLICIS_PROD',
            'listing_global_name': 'GZ.LIST',
            'consumer_account_locator': 'CONS01',
            'jobs': 12,
            'unique_users_1d': 2,
        }]
        info = imp.get_resource_info_from_expenses(expenses)
        self.assertEqual(info['type'], 'LISTING_CONSUMPTION')
        self.assertEqual(info['name'], 'My Listing')
        self.assertEqual(info['account_locator'], 'HW44440')
        self.assertEqual(info['account_name'], 'PUBLICIS_PROD')
        self.assertEqual(info['jobs'], 12)
        self.assertEqual(info['listing_global_name'], 'GZ.LIST')
        data = imp.get_resource_data('HW/listing_consumption/L/S/C', info)
        self.assertEqual(data['resource_type'], 'LISTING_CONSUMPTION')
        self.assertEqual(data['meta']['jobs'], 12)
        self.assertEqual(data['account_locator'], 'HW44440')
        self.assertEqual(data['account_name'], 'PUBLICIS_PROD')
        self.assertEqual(data['meta']['account_name'], 'PUBLICIS_PROD')

    def test_resource_data_elevates_region(self):
        imp = self._importer()
        start = datetime(2026, 7, 1)
        expenses = [{
            'start_date': start,
            'end_date': start,
            'resource_id': 'HW/warehouse/11',
            'resource_name': 'WH',
            'service_type': 'COMPUTE',
            'account_locator': 'HW44440',
            'region': 'AWS_US_EAST_1',
        }]
        info = imp.get_resource_info_from_expenses(expenses)
        self.assertEqual(info['region'], 'AWS_US_EAST_1')
        data = imp.get_resource_data('HW/warehouse/11', info)
        self.assertEqual(data['region'], 'AWS_US_EAST_1')
        self.assertEqual(data['meta']['region'], 'AWS_US_EAST_1')

    def test_resource_type_rename_legacy_ai(self):
        imp = self._importer()
        start = datetime(2026, 7, 1)
        expenses = [{
            'start_date': start,
            'end_date': start,
            'resource_id': 'HW/ai',
            'resource_name': 'fn',
            'service_type': 'AI_FUNCTIONS',
        }]
        info = imp.get_resource_info_from_expenses(expenses)
        self.assertEqual(info['type'], 'AI_SERVICES')
        self.assertEqual(
            RESOURCE_TYPE_RENAMES['AI_FUNCTIONS'], 'AI_SERVICES')

    def test_resource_info_prefers_newest_name(self):
        from diworker.diworker.importers.snowflake import (
            _COMPOSITE_LISTING_NAME_RE,
            _COMPOSITE_TRANSFER_NAME_RE,
            _LEGACY_RESOURCE_ID_RE,
        )

        imp = self._importer()
        old = datetime(2026, 6, 1)
        new = datetime(2026, 7, 1)
        info = imp.get_resource_info_from_expenses([
            {
                'start_date': old,
                'end_date': old,
                'resource_id': 'DA/listing_auto_fulfillment/DATA TRANSFER',
                'resource_name': 'Listing auto-fulfillment · DATA TRANSFER',
                'service_type': 'LISTING_AUTO_FULFILLMENT',
            },
            {
                'start_date': new,
                'end_date': new,
                'resource_id': 'DA/listing_auto_fulfillment/DATA TRANSFER',
                'resource_name': 'DATA TRANSFER',
                'service_type': 'LISTING_AUTO_FULFILLMENT',
            },
        ])
        self.assertEqual(info['name'], 'DATA TRANSFER')

        self.assertRegex(
            'PMB/data_transfer/us-central1/us-west-2/REPLICATION/1781481600',
            _LEGACY_RESOURCE_ID_RE)
        self.assertEqual(
            _COMPOSITE_TRANSFER_NAME_RE.match(
                'REPLICATION:us-central1->us-west-2').group('transfer_type'),
            'REPLICATION')
        self.assertEqual(
            _COMPOSITE_LISTING_NAME_RE.match(
                'Listing auto-fulfillment · DATA TRANSFER').group('sf_type'),
            'DATA TRANSFER')

    def test_cloud_acc_and_tenant_flag_are_cached(self):
        from unittest.mock import MagicMock

        from diworker.diworker.importers.base import BaseReportImporter

        imp = BaseReportImporter.__new__(BaseReportImporter)
        object.__setattr__(imp, 'cloud_acc_id', 'tenant-1')
        object.__setattr__(imp, '_cloud_acc', None)
        rest = MagicMock()
        rest.cloud_account_get.return_value = (
            200, {'id': 'tenant-1', 'type': 'snowflake_tenant'})
        object.__setattr__(imp, 'rest_cl', rest)

        self.assertEqual(imp.cloud_acc['type'], 'snowflake_tenant')
        self.assertEqual(imp.cloud_acc['type'], 'snowflake_tenant')
        self.assertEqual(rest.cloud_account_get.call_count, 1)

        sf = SnowflakeReportImporter.__new__(SnowflakeReportImporter)
        object.__setattr__(sf, 'cloud_acc_id', 'tenant-1')
        object.__setattr__(sf, '_cloud_acc', {
            'id': 'tenant-1', 'type': 'snowflake_tenant'})
        self.assertTrue(sf._is_tenant_import)
        object.__setattr__(sf, '_cloud_acc', {
            'id': 'tenant-1', 'type': 'snowflake'})
        # Cached tenant flag must not flip mid-import.
        self.assertTrue(sf._is_tenant_import)

    def test_meta_fields_include_listing_and_cortex(self):
        for key in (
                'model_name', 'function_name', 'query_id',
                'listing_global_name', 'jobs'):
            self.assertIn(key, META_FIELDS)

    def test_collector_progress_pct(self):
        from types import SimpleNamespace

        imp = self._importer(cloud_adapter=SimpleNamespace(_import_collectors=[
            {'service_type': 'COMPUTE', 'status': 'ok'},
            {'service_type': 'STORAGE', 'status': 'in_progress'},
            {'service_type': 'STAGE', 'status': 'in_progress'},
            {'service_type': 'SNOWPIPE', 'status': 'failed'},
        ]))
        done, total, pct = imp._collector_progress()
        self.assertEqual((done, total, pct), (2, 4, 50))

        # First collector still running: soft-fill so status is not stuck at 0.
        imp._raw_written = 8000
        object.__setattr__(imp, '_cloud_adapter', SimpleNamespace(
            _import_collectors=[
                {'service_type': 'COMPUTE', 'status': 'in_progress'},
                {'service_type': 'STORAGE', 'status': 'in_progress'},
            ]))
        done, total, pct = imp._collector_progress()
        self.assertEqual((done, total), (0, 2))
        self.assertGreater(pct, 0)
        self.assertLess(pct, 50)

        # Long streaming collector must keep climbing past a single 10% slot.
        imp._raw_written = 8000
        pct_lo = imp._collector_progress()[2]
        imp._raw_written = 200000
        pct_hi = imp._collector_progress()[2]
        self.assertGreater(pct_hi, pct_lo)
        self.assertGreaterEqual(pct_hi, 50)
        self.assertLess(pct_hi, 95)

        object.__setattr__(
            imp, '_cloud_adapter', SimpleNamespace(_import_collectors=[]))
        self.assertEqual(imp._collector_progress(), (0, 0, None))

    def test_account_collectors_in_import_details(self):
        from types import SimpleNamespace

        imp = self._importer(cloud_adapter=SimpleNamespace(
            get_import_details=lambda: {
                'collectors': [{
                    'service_type': 'COMPUTE',
                    'records': 10,
                    'credits': 5.0,
                    'status': 'ok',
                    'finished_at': 100,
                    'message': None,
                }],
            }))
        imp._account_collectors = {
            'YT11624': {
                'COMPUTE': {
                    'service_type': 'COMPUTE',
                    'records': 3,
                    'credits': 1.5,
                    'average_bytes': 0,
                    'tb': 0.0,
                    'status': 'in_progress',
                    'message': None,
                    'finished_at': None,
                    '_storage_bytes_by_day': {},
                },
            },
        }
        details = imp.get_import_details()
        self.assertIn('accounts', details)
        child_rows = details['accounts']['YT11624']['collectors']
        self.assertEqual(len(child_rows), 1)
        self.assertEqual(child_rows[0]['records'], 3)
        self.assertEqual(child_rows[0]['credits'], 1.5)
        self.assertEqual(child_rows[0]['status'], 'ok')
        self.assertEqual(child_rows[0]['finished_at'], 100)

    def test_publish_import_details_throttled(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        imp = self._importer(cloud_adapter=SimpleNamespace(
            get_import_details=lambda: {
                'collectors': [
                    {'service_type': 'COMPUTE', 'status': 'in_progress'}],
            }))
        imp.report_import_id = 'ri-1'
        imp.rest_cl = SimpleNamespace(report_import_update=MagicMock())
        imp._account_collectors = {
            'AU89958': {
                'COMPUTE': {
                    'service_type': 'COMPUTE',
                    'records': 1,
                    'credits': 0.1,
                    'average_bytes': 0,
                    '_storage_bytes_by_day': {},
                },
            },
        }
        imp._publish_import_details()
        self.assertEqual(imp.rest_cl.report_import_update.call_count, 1)
        # Within throttle window — skipped.
        imp._publish_import_details()
        self.assertEqual(imp.rest_cl.report_import_update.call_count, 1)
        imp._publish_import_details(force=True)
        self.assertEqual(imp.rest_cl.report_import_update.call_count, 2)
        payload = imp.rest_cl.report_import_update.call_args[0][1]
        self.assertIn('AU89958', payload['details']['accounts'])

    def test_enrich_sets_cost_from_credits(self):
        from tools.cloud_adapter.clouds.snowflake import calculate_cost
        imp = self._importer()
        record = {
            'credits_used': 4.0,
            'effective_rate': 2.5,
            'service_type': 'COMPUTE',
            'account_locator': 'HW',
            'start_date': datetime(2026, 7, 1),
        }
        imp._enrich_record(record, {})
        self.assertEqual(record['cloud_account_id'], 'ca-1')
        self.assertEqual(record['cost'], 10.0)
        self.assertEqual(calculate_cost(record), 10.0)

    def _detect_period_start_importer(self, last_expense, last_import_at):
        imp = self._importer()
        imp.get_last_import_date = MagicMock(return_value=last_expense)
        imp.remove_raw_expenses_from_period_start = MagicMock()
        imp._clear_clickhouse_expenses_from_period_start = MagicMock()
        ca_patch = patch.object(
            SnowflakeReportImporter, 'cloud_acc',
            new_callable=PropertyMock,
            return_value={'last_import_at': last_import_at})
        ca_patch.start()
        self.addCleanup(ca_patch.stop)
        return imp

    def test_detect_period_start_incremental_lookback_no_wipe(self):
        import tools.optscale_time as opttime

        last_expense = opttime.utcfromtimestamp(
            int(datetime(2026, 8, 21, tzinfo=tz.utc).timestamp()))
        ca_now = datetime(2026, 8, 21, 15, 0, tzinfo=tz.utc)
        imp = self._detect_period_start_importer(
            last_expense, int(ca_now.timestamp()))
        imp.detect_period_start()

        self.assertEqual(
            imp.period_start,
            datetime(2026, 8, 21) - timedelta(
                days=SF_INCREMENTAL_LOOKBACK_DAYS))
        self.assertEqual(imp.period_start, datetime(2026, 8, 18))
        imp.remove_raw_expenses_from_period_start.assert_not_called()
        imp._clear_clickhouse_expenses_from_period_start.assert_not_called()

    def test_detect_period_start_weekend_gap_still_merges(self):
        """Increments off Fri–Sun: next run looks back 3 days and does not wipe.

        download_usage still goes through utcnow, so Sat/Sun are fetched to
        the right of last_expense and merged into Mongo/ClickHouse.
        """
        import tools.optscale_time as opttime

        last_expense = opttime.utcfromtimestamp(
            int(datetime(2026, 8, 21, tzinfo=tz.utc).timestamp()))
        # last_import_at stays Friday while the next run is Monday.
        ca_friday = datetime(2026, 8, 21, 15, 0, tzinfo=tz.utc)
        imp = self._detect_period_start_importer(
            last_expense, int(ca_friday.timestamp()))
        imp.detect_period_start()

        self.assertEqual(imp.period_start, datetime(2026, 8, 18))
        imp.remove_raw_expenses_from_period_start.assert_not_called()
        imp._clear_clickhouse_expenses_from_period_start.assert_not_called()

    def test_detect_period_start_cursor_inside_lookback_still_merges(self):
        """A 1-day cursor rewind is overlap merge, not a wipe reimport."""
        import tools.optscale_time as opttime

        last_expense = opttime.utcfromtimestamp(
            int(datetime(2026, 8, 21, tzinfo=tz.utc).timestamp()))
        ca_cursor = datetime(2026, 8, 20, tzinfo=tz.utc)
        imp = self._detect_period_start_importer(
            last_expense, int(ca_cursor.timestamp()))
        imp.detect_period_start()

        self.assertEqual(imp.period_start, datetime(2026, 8, 18))
        imp.remove_raw_expenses_from_period_start.assert_not_called()
        imp._clear_clickhouse_expenses_from_period_start.assert_not_called()

    def test_detect_period_start_reimport_exact_cursor_no_minus_two(self):
        import tools.optscale_time as opttime

        # Reimport cursor Jan 1 while expenses already exist through Jul.
        ca_cursor = datetime(2026, 1, 1, tzinfo=tz.utc)
        last_expense = opttime.utcfromtimestamp(
            int(datetime(2026, 7, 29, tzinfo=tz.utc).timestamp()))
        imp = self._detect_period_start_importer(
            last_expense, int(ca_cursor.timestamp()))
        imp.detect_period_start()

        self.assertEqual(imp.period_start.date(), ca_cursor.date())
        # Must not rewind left of the chosen reimport date.
        self.assertEqual(imp.period_start.day, 1)
        self.assertEqual(imp.period_start.month, 1)
        imp.remove_raw_expenses_from_period_start.assert_called_once()
        imp._clear_clickhouse_expenses_from_period_start.assert_called_once()


def _naive(dt):
    if dt is None:
        return None
    if getattr(dt, 'tzinfo', None) is not None:
        return dt.replace(tzinfo=None)
    return dt


class _RawStore:
    """Applies pymongo UpdateOne $set / $setOnInsert without wiping extras."""

    def __init__(self):
        self.docs = []
        self.delete_many_calls = []

    def _matches(self, doc, filt):
        if not filt:
            return True
        if '$and' in filt:
            return all(self._matches(doc, clause) for clause in filt['$and'])
        for key, expected in filt.items():
            if isinstance(expected, dict):
                value = doc.get(key)
                if '$gte' in expected:
                    if value is None or _naive(value) < _naive(expected['$gte']):
                        return False
                    continue
                if '$in' in expected:
                    if value not in expected['$in']:
                        return False
                    continue
                if '$exists' in expected:
                    present = key in doc
                    if expected['$exists'] and not present:
                        return False
                    if (not expected['$exists']) and present:
                        return False
                    continue
                return False
            if _naive(doc.get(key)) != _naive(expected) and doc.get(key) != expected:
                return False
        return True

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
            if match_idx is None:
                if not upsert:
                    continue
                new_doc = dict(set_on_insert)
                new_doc.update(set_fields)
                self.docs.append(new_doc)
                continue
            self.docs[match_idx].update(set_fields)
        return MagicMock()

    def delete_many(self, filt):
        self.delete_many_calls.append(filt)
        before = len(self.docs)
        self.docs = [d for d in self.docs if not self._matches(d, filt)]
        return MagicMock(deleted_count=before - len(self.docs))

    def aggregate(self, pipeline, allowDiskUse=False):
        docs = list(self.docs)
        for stage in pipeline:
            if '$match' in stage:
                docs = [d for d in docs if self._matches(d, stage['$match'])]
                continue
            spec = stage['$group']
            spec_id = spec.get('_id')
            grouped = {}
            for doc in docs:
                if isinstance(spec_id, str) and spec_id.startswith('$'):
                    gid = doc.get(spec_id[1:])
                else:
                    gid = spec_id
                grouped.setdefault(gid, {'_id': gid, 'expenses': []})
                if 'expenses' in spec:
                    grouped[gid]['expenses'].append(doc)
            docs = list(grouped.values())
        return docs

    def find_cost(self, **filt):
        for doc in self.docs:
            if all(_naive(doc.get(k)) == _naive(v) or doc.get(k) == v
                   for k, v in filt.items()):
                return doc.get('cost')
        return None


@unittest.skipIf(
    SnowflakeReportImporter is None,
    'diworker runtime deps missing: %s' % _IMPORT_ERROR)
class TestSnowflakeIncrementalMerge(unittest.TestCase):
    RID = 'HW/warehouse/1'
    JULY = datetime(2026, 7, 15, tzinfo=tz.utc)
    IN_WINDOW = datetime(2026, 8, 21, tzinfo=tz.utc)
    LOOKBACK = datetime(2026, 8, 18)

    def _raw_doc(self, start_date, cost, resource_id=None):
        return {
            'cloud_account_id': 'ca-1',
            'service_type': 'COMPUTE',
            'resource_id': resource_id or self.RID,
            'start_date': start_date,
            'end_date': start_date,
            'cost': cost,
            'credits_used': cost,
            'account_locator': 'HW',
        }

    def _usage_row(self, start_date, credits, rate=1.0):
        return {
            'start_date': start_date,
            'end_date': start_date,
            'service_type': 'COMPUTE',
            'resource_id': self.RID,
            'account_locator': 'HW',
            'credits_used': credits,
            'effective_rate': rate,
        }

    def _load_importer(self, store, usage_rows):
        adapter = SimpleNamespace(
            download_usage=lambda start, end, progress_callback=None: iter(
                dict(row) for row in usage_rows),
            get_import_warnings=lambda: [],
            get_import_details=lambda: {},
            _import_collectors=[],
        )
        imp = SnowflakeReportImporter.__new__(SnowflakeReportImporter)
        object.__setattr__(imp, 'cloud_acc_id', 'ca-1')
        object.__setattr__(imp, '_cloud_acc', {
            'id': 'ca-1', 'type': 'snowflake', 'config': {'cost_model': {}},
        })
        object.__setattr__(imp, '_cloud_adapter', adapter)
        object.__setattr__(imp, '_is_tenant_import_cached', False)
        object.__setattr__(imp, 'period_start', self.LOOKBACK)
        object.__setattr__(imp, 'mongo_raw', store)
        object.__setattr__(imp, 'imported_raw_dates_map', defaultdict(dict))
        object.__setattr__(imp, 'report_import_id', None)
        imp.log_import_phase = MagicMock()
        return imp

    def _load_raw(self, imp):
        now = datetime(2026, 8, 21, 16, 0)
        with patch(
                'diworker.diworker.importers.snowflake.opttime.utcnow',
                return_value=now), patch(
                'diworker.diworker.importers.base.retry_mongo_upsert',
                side_effect=lambda fn, *a, **k: fn(*a, **k)):
            imp.load_raw_data()

    def test_load_raw_empty_stream_does_not_wipe(self):
        store = _RawStore()
        store.docs.append(self._raw_doc(self.JULY, 9.99))
        imp = self._load_importer(store, [])
        self._load_raw(imp)

        self.assertEqual(store.delete_many_calls, [])
        self.assertEqual(len(store.docs), 1)
        self.assertEqual(
            store.find_cost(resource_id=self.RID, start_date=self.JULY),
            9.99)

    def test_load_raw_leaves_day_before_lookback_and_merges_inside(self):
        store = _RawStore()
        store.docs.append(self._raw_doc(self.JULY, 9.99))
        store.docs.append(self._raw_doc(self.IN_WINDOW, 1.0))
        imp = self._load_importer(store, [
            self._usage_row(self.IN_WINDOW, credits=2.5, rate=1.0),
        ])
        self._load_raw(imp)

        self.assertEqual(store.delete_many_calls, [])
        self.assertEqual(
            store.find_cost(resource_id=self.RID, start_date=self.JULY),
            9.99)
        self.assertEqual(
            store.find_cost(resource_id=self.RID, start_date=self.IN_WINDOW),
            2.5)
        self.assertEqual(len(store.docs), 2)

    def test_get_resource_ids_skips_days_before_lookback(self):
        store = _RawStore()
        store.docs.append(self._raw_doc(self.JULY, 4.0, resource_id='old-only'))
        store.docs.append(self._raw_doc(self.JULY, 1.0))
        store.docs.append(self._raw_doc(self.IN_WINDOW, 2.0))
        imp = self._load_importer(store, [])
        ids = imp.get_resource_ids('ca-1', self.LOOKBACK)
        self.assertEqual(set(ids), {self.RID})

    def test_save_clean_does_not_negate_days_before_lookback(self):
        imp = SnowflakeReportImporter.__new__(SnowflakeReportImporter)
        object.__setattr__(imp, 'cloud_acc_id', 'ca-1')
        object.__setattr__(imp, 'period_start', self.LOOKBACK)
        billed_day = datetime(2026, 8, 21)
        extra_day = datetime(2026, 8, 22)
        before = datetime(2026, 7, 15)
        rid = 'mongo-res-1'
        imp.get_resource_info_map = MagicMock(return_value={self.RID: {}})
        imp.create_resources_if_not_exist = MagicMock(return_value=[{
            'id': rid,
            'cloud_resource_id': self.RID,
        }])
        imp.get_clickhouse_expenses = MagicMock(return_value=[
            (rid, billed_day, '', 10.0, 1),
            (rid, extra_day, '', 3.05, 1),
            (rid, before, '', 99.0, 1),
        ])
        imp.update_clickhouse_expenses = MagicMock()
        imp.update_resource_expense_info = MagicMock()
        imp.log_import_phase = MagicMock()
        now = datetime(2026, 8, 25, 3, 0)
        with patch(
                'diworker.diworker.importers.base.opttime.utcnow',
                return_value=now):
            imp.save_clean_expenses('ca-1', {
                self.RID: [{
                    'start_date': billed_day,
                    'end_date': billed_day,
                    'cost': 10.0,
                    'cloud_account_id': 'ca-1',
                }],
            })
        payload = imp.update_clickhouse_expenses.call_args[0][0]
        before_rows = [row for row in payload if row[2] == before]
        extras = [row for row in payload if row[2] == extra_day]
        billed = [row for row in payload if row[2] == billed_day]
        self.assertEqual(before_rows, [])
        self.assertEqual(billed, [])
        self.assertEqual(len(extras), 1)
        self.assertEqual(extras[0][4], -1)


if __name__ == '__main__':
    unittest.main()
