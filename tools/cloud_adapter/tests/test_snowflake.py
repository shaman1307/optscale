#!/usr/bin/env python
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from tools.cloud_adapter.clouds.snowflake import (
    WarehouseMeteringCollector,
    calculate_cost,
    parse_metrics,
)


class TestSnowflakeHelpers(unittest.TestCase):
    def test_parse_metrics_tokens_and_pages(self):
        metrics = [
            {'key': {'metric': 'input', 'unit': 'tokens'}, 'value': 10},
            {'key': {'metric': 'output', 'unit': 'tokens'}, 'value': 5},
            {'key': {'metric': 'total', 'unit': 'tokens'}, 'value': 15},
            {'key': {'metric': 'pages', 'unit': 'pages'}, 'value': 2},
        ]
        self.assertEqual(parse_metrics(metrics), {
            'tokens_input': 10,
            'tokens_output': 5,
            'tokens_total': 15,
            'pages': 2,
        })

    def test_parse_metrics_json_string(self):
        metrics = (
            '[{"key": {"metric": "input", "unit": "tokens"}, "value": 3}]')
        self.assertEqual(parse_metrics(metrics)['tokens_input'], 3)

    def test_calculate_cost_credits_and_overrides(self):
        cost_model = {
            'credit_price': 2.0,
            'cortex_model_overrides': {'claude-4-sonnet': 3.0},
            'storage_price_per_tb_month': 30.0,
        }
        self.assertEqual(
            calculate_cost({'credits_used': 4}, cost_model), 8.0)
        self.assertEqual(
            calculate_cost(
                {'credits_used': 2, 'model_name': 'claude-4-sonnet'},
                cost_model),
            6.0)
        # 1 TiB for one day in June (30 days) => 30/30 = 1.0
        self.assertAlmostEqual(
            calculate_cost({
                'average_bytes': 1024 ** 4,
                'start_date': datetime(2026, 6, 15, tzinfo=timezone.utc),
            }, cost_model), 1.0)
        # 1 TiB for one day in July (31 days) => 30/31
        self.assertAlmostEqual(
            calculate_cost({
                'average_bytes': 1024 ** 4,
                'start_date': datetime(2026, 7, 1, tzinfo=timezone.utc),
            }, cost_model), 30.0 / 31.0)


class TestSnowflakeReconcile(unittest.TestCase):
    def test_reconcile_warns_on_large_delta(self):
        from tools.cloud_adapter.clouds.snowflake import Snowflake
        adapter = Snowflake({
            'account': 'a', 'user': 'u', 'private_key': 'k',
            'warehouse': 'w',
        })
        # Window must exceed RECONCILE_LAG_DAYS (2).
        start = datetime(2026, 6, 1, tzinfo=timezone.utc)
        end = datetime(2026, 6, 10, tzinfo=timezone.utc)
        settled = start.date()
        cursor = MagicMock()
        cursor.description = [
            ('SERVICE_TYPE',), ('USAGE_DATE',),
            ('CREDITS_USED_COMPUTE',), ('CREDITS_USED_CLOUD_SERVICES',),
            ('CREDITS_BILLED',),
        ]
        cursor.__iter__ = MagicMock(return_value=iter([
            ('WAREHOUSE_METERING', settled, 80.0, 20.0, 95.0),
        ]))
        with patch(
                'tools.cloud_adapter.clouds.snowflake.load_sql',
                return_value='SELECT 1'):
            warnings = adapter._reconcile_credits(
                cursor, start, end,
                {('WAREHOUSE_METERING', settled): 50.0})
        self.assertEqual(len(warnings), 1)
        self.assertIn('WAREHOUSE_METERING', warnings[0])
        self.assertIn('delta=', warnings[0])
        details = adapter.get_import_details()
        self.assertEqual(details['reconciliation'][0]['status'], 'mismatch')
        self.assertEqual(details['reconciliation'][0]['delta_pct'], 50.0)

    def test_reconcile_skips_short_window(self):
        from tools.cloud_adapter.clouds.snowflake import Snowflake
        adapter = Snowflake({
            'account': 'a', 'user': 'u', 'private_key': 'k',
            'warehouse': 'w',
        })
        start = datetime(2026, 6, 8, tzinfo=timezone.utc)
        end = datetime(2026, 6, 10, tzinfo=timezone.utc)
        cursor = MagicMock()
        warnings = adapter._reconcile_credits(
            cursor, start, end, {('WAREHOUSE_METERING', start.date()): 10.0})
        self.assertEqual(warnings, [])
        cursor.execute.assert_not_called()
        details = adapter.get_import_details()
        self.assertEqual(details['reconciliation'][0]['status'], 'skipped')


class TestSnowflakeConnect(unittest.TestCase):
    def test_connect_forces_utc_session(self):
        from tools.cloud_adapter.clouds.snowflake import Snowflake
        adapter = Snowflake({
            'account': 'a', 'user': 'u', 'private_key': 'k',
            'warehouse': 'w',
        })
        fake_conn = MagicMock()
        fake_cursor = MagicMock()
        fake_conn.cursor.return_value = fake_cursor
        with patch.object(
                adapter, '_load_private_key_bytes', return_value=b'key'), \
                patch.dict('sys.modules', {
                    'snowflake': MagicMock(),
                    'snowflake.connector': MagicMock(),
                }):
            import snowflake.connector as sf_connector
            sf_connector.connect = MagicMock(return_value=fake_conn)
            conn = adapter.connect()
        self.assertIs(conn, fake_conn)
        sf_connector.connect.assert_called_once()
        self.assertEqual(
            sf_connector.connect.call_args.kwargs.get('timezone'), 'UTC')
        fake_cursor.execute.assert_called_with(
            "ALTER SESSION SET TIMEZONE = 'UTC'")
        fake_cursor.close.assert_called_once()


class TestWarehouseMeteringCollector(unittest.TestCase):
    def test_fetch_normalizes_rows(self):
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 2, tzinfo=timezone.utc)
        cursor = MagicMock()
        cursor.description = [
            ('WAREHOUSE_ID',), ('WAREHOUSE_NAME',), ('START_TIME',),
            ('END_TIME',), ('CREDITS_USED',), ('CREDITS_USED_COMPUTE',),
            ('CREDITS_USED_CLOUD_SERVICES',),
        ]
        cursor.__iter__ = MagicMock(return_value=iter([
            (11, 'WH', start, end, 1.5, 1.0, 0.5),
        ]))
        with patch(
                'tools.cloud_adapter.clouds.snowflake.load_sql',
                return_value='SELECT 1'):
            rows = list(WarehouseMeteringCollector().fetch(
                cursor, start, end, 'HW44440'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['resource_id'], 'HW44440/warehouse/11')
        self.assertEqual(rows[0]['credits_used'], 1.5)
        self.assertEqual(rows[0]['service_type'], 'WAREHOUSE_METERING')


if __name__ == '__main__':
    unittest.main()
