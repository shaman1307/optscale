#!/usr/bin/env python
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from tools.cloud_adapter.clouds.snowflake import (
    WarehouseMeteringCollector,
    AutomaticClusteringCollector,
    apply_product_tag,
    calculate_cost,
    parse_metrics,
    year_quarter,
    _cortex_code_resource,
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

    def test_year_quarter(self):
        self.assertEqual(
            year_quarter(datetime(2026, 7, 22, tzinfo=timezone.utc)),
            '2026Q3')
        self.assertEqual(
            year_quarter(datetime(2026, 1, 1, tzinfo=timezone.utc)),
            '2026Q1')

    def test_apply_product_tag(self):
        product_map = {
            ('BILLING_WH', 'COMPUTE'): 'Core Infrastructure',
            ('CLIENT_DATAMART_PROD', 'AUTOMATIC_CLUSTERING'): 'Client Datamart',
            ('SNOWPIPE', 'SNOWPIPE'): 'Core Infrastructure',
            ('AI_SERVICES', 'AI_SERVICES'): 'Core Infrastructure',
            ('STAGE', 'STORAGE'): 'Core Infrastructure',
        }
        compute = {
            'service_type': 'COMPUTE',
            'resource_name': 'BILLING_WH',
        }
        apply_product_tag(compute, product_map)
        self.assertEqual(compute['tag'], 'Core Infrastructure')

        clustering = {
            'service_type': 'AUTOMATIC_CLUSTERING',
            'database_name': 'CLIENT_DATAMART_PROD',
            'resource_name': 'CLIENT_DATAMART_PROD.SCHEMA.TABLE',
        }
        apply_product_tag(clustering, product_map)
        self.assertEqual(clustering['tag'], 'Client Datamart')

        pipe = {'service_type': 'SNOWPIPE', 'resource_name': 'my_pipe'}
        apply_product_tag(pipe, product_map)
        self.assertEqual(pipe['tag'], 'Core Infrastructure')

        ai = {'service_type': 'AI_SERVICES', 'resource_name': 'model-x'}
        apply_product_tag(ai, product_map)
        self.assertEqual(ai['tag'], 'Core Infrastructure')

        stage = {'service_type': 'STAGE', 'resource_name': 'STAGE'}
        apply_product_tag(stage, product_map)
        self.assertEqual(stage['tag'], 'Core Infrastructure')

    def test_cortex_code_resource_uses_user_name(self):
        resource_id, resource_name = _cortex_code_resource(
            'HW44440', 'snowsight', {
                'user_id': 42,
                'user_name': 'alice@example.com',
                'request_id': '0d191191-007e-494a-ac9e-6052ee56',
            })
        self.assertEqual(
            resource_id, 'HW44440/cortex_code_snowsight/user/42')
        self.assertEqual(
            resource_name,
            'Cortex Code · Snowsight · alice@example.com')

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
        self.assertAlmostEqual(
            calculate_cost({
                'bytes_transferred': 1024 ** 4,
            }, {'transfer_price_per_tb': 10.0}), 10.0)
        self.assertAlmostEqual(
            calculate_cost({'billable_amount': 42.5}, cost_model), 42.5)


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
                {('COMPUTE', settled): 50.0})
        self.assertEqual(len(warnings), 1)
        self.assertIn('COMPUTE', warnings[0])
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
            cursor, start, end, {('COMPUTE', start.date()): 10.0})
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
        self.assertEqual(rows[0]['service_type'], 'COMPUTE')


class TestAutomaticClusteringCollector(unittest.TestCase):
    def test_fetch_normalizes_rows(self):
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 2, tzinfo=timezone.utc)
        cursor = MagicMock()
        cursor.description = [
            ('START_TIME',), ('END_TIME',), ('CREDITS_USED',),
            ('NUM_BYTES_RECLUSTERED',), ('NUM_ROWS_RECLUSTERED',),
            ('TABLE_ID',), ('TABLE_NAME',), ('SCHEMA_ID',),
            ('SCHEMA_NAME',), ('DATABASE_ID',), ('DATABASE_NAME',),
        ]
        cursor.__iter__ = MagicMock(return_value=iter([
            (start, end, 0.5, 10, 2, 99, 'T1', 1, 'S1', 7, 'DB1'),
        ]))
        with patch(
                'tools.cloud_adapter.clouds.snowflake.load_sql',
                return_value='SELECT 1'):
            rows = list(AutomaticClusteringCollector().fetch(
                cursor, start, end, 'HW44440'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]['resource_id'],
            'HW44440/automatic_clustering/99')
        self.assertEqual(rows[0]['service_type'], 'AUTOMATIC_CLUSTERING')
        self.assertEqual(rows[0]['database_name'], 'DB1')
        self.assertEqual(rows[0]['credits_used'], 0.5)


class TestAggregateCollectors(unittest.TestCase):
    def test_collapses_ai_services(self):
        from tools.cloud_adapter.clouds.snowflake import Snowflake
        rows = Snowflake._aggregate_collectors([
            {
                'service_type': 'AI_SERVICES', 'source': 'AI_FUNCTIONS',
                'records': 0, 'credits': 0.0, 'average_bytes': 0,
                'tb': 0.0, 'status': 'ok', 'message': None,
                'finished_at': 100,
            },
            {
                'service_type': 'AI_SERVICES', 'source': 'CORTEX_CODE_SNOWSIGHT',
                'records': 99, 'credits': 10.5387, 'average_bytes': 0,
                'tb': 0.0, 'status': 'ok', 'message': None,
                'finished_at': 200,
            },
            {
                'service_type': 'COMPUTE', 'source': 'WAREHOUSE_METERING',
                'records': 10, 'credits': 1.5, 'average_bytes': 0,
                'tb': 0.0, 'status': 'ok', 'message': None,
                'finished_at': 150,
            },
        ])
        self.assertEqual(len(rows), 2)
        ai = rows[0]
        self.assertEqual(ai['service_type'], 'AI_SERVICES')
        self.assertEqual(ai['records'], 99)
        self.assertEqual(ai['credits'], 10.5387)
        self.assertEqual(ai['finished_at'], 200)
        self.assertEqual(ai['status'], 'ok')
        self.assertNotIn('source', ai)


if __name__ == '__main__':
    unittest.main()
