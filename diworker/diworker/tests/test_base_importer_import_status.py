#!/usr/bin/env python
"""Unit tests for clearing last_import_attempt_error on success/reimport."""
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

try:
    from diworker.diworker.importers.base import (
        BaseReportImporter,
        CSVBaseReportImporter,
    )
except ImportError as exc:  # pragma: no cover - host without deps
    BaseReportImporter = None
    CSVBaseReportImporter = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


@unittest.skipIf(
    BaseReportImporter is None,
    f'BaseReportImporter unavailable: {_IMPORT_ERROR}',
)
class TestImportStatusFields(unittest.TestCase):
    def test_update_cloud_import_time_clears_attempt_error(self):
        rest_cl = MagicMock()
        imp = object.__new__(BaseReportImporter)
        imp.cloud_acc_id = 'ca-1'
        imp.rest_cl = rest_cl

        imp.update_cloud_import_time(100)

        rest_cl.cloud_account_update.assert_called_once_with(
            'ca-1',
            {
                'last_import_at': 100,
                'last_import_attempt_at': 100,
                'last_import_attempt_error': None,
            },
        )

    def test_update_cloud_import_attempt_allows_clearing_error(self):
        rest_cl = MagicMock()
        imp = object.__new__(BaseReportImporter)
        imp.cloud_acc_id = 'ca-1'
        imp.rest_cl = rest_cl

        imp.update_cloud_import_attempt(50, error=None)

        rest_cl.cloud_account_update.assert_called_once_with(
            'ca-1',
            {
                'last_import_attempt_at': 50,
                'last_import_attempt_error': None,
            },
        )

    def test_csv_update_cloud_import_time_clears_attempt_error(self):
        rest_cl = MagicMock()
        imp = object.__new__(CSVBaseReportImporter)
        imp.detected_cloud_accounts = ['ca-a', 'ca-b']
        imp.last_import_modified_at = 77
        imp.rest_cl = rest_cl

        imp.update_cloud_import_time(200)

        self.assertEqual(rest_cl.cloud_account_update.call_count, 2)
        for ca_id in ('ca-a', 'ca-b'):
            rest_cl.cloud_account_update.assert_any_call(
                ca_id,
                {
                    'last_import_at': 200,
                    'last_import_modified_at': 77,
                    'last_import_attempt_at': 200,
                    'last_import_attempt_error': None,
                },
            )


@unittest.skipIf(
    BaseReportImporter is None,
    f'BaseReportImporter unavailable: {_IMPORT_ERROR}',
)
class TestClickHousePeriodClear(unittest.TestCase):
    def test_clear_deletes_from_period_start_and_waits(self):
        imp = object.__new__(BaseReportImporter)
        imp.cloud_acc_id = 'ca-1'
        imp.period_start = datetime(2026, 8, 1)
        clickhouse_cl = MagicMock()
        clickhouse_cl.query.side_effect = [
            MagicMock(),  # ALTER DELETE
            MagicMock(result_rows=[(1,)]),  # pending mutations
            MagicMock(result_rows=[(10,)]),  # leftover rows
            MagicMock(result_rows=[(0,)]),  # mutations done
            MagicMock(result_rows=[(0,)]),  # leftover gone
        ]
        imp.clickhouse_cl = clickhouse_cl
        with patch(
                'diworker.diworker.importers.base.time.sleep',
                return_value=None):
            imp._clear_clickhouse_expenses_from_period_start()
        alter = clickhouse_cl.query.call_args_list[0]
        self.assertIn('ALTER TABLE expenses DELETE', alter[0][0])
        self.assertEqual(alter[1]['parameters']['ca_id'], 'ca-1')
        self.assertEqual(
            alter[1]['parameters']['from_dt'], datetime(2026, 8, 1))


if __name__ == '__main__':
    unittest.main()
