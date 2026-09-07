#!/usr/bin/env python
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from tools.cloud_adapter.clouds.snowflake import (
    WarehouseMeteringCollector,
    AutomaticClusteringCollector,
    ListingConsumptionCollector,
    ListingAutoFulfillmentCollector,
    DataTransferCollector,
    apply_product_tag,
    calculate_cost,
    parse_metrics,
    year_quarter,
    _cortex_code_resource,
    _row_account_name,
    _row_region,
    enrich_account_name,
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

    def test_row_account_name(self):
        self.assertEqual(
            _row_account_name({'account_name': 'PUBLICIS_PROD'}),
            'PUBLICIS_PROD')
        self.assertEqual(
            _row_account_name({}, 'SESSION_ACC'),
            'SESSION_ACC')
        self.assertIsNone(_row_account_name({}))
        self.assertIsNone(_row_account_name({'account_name': '  '}))

    def test_row_region(self):
        self.assertEqual(
            _row_region({'region': 'AWS_US_EAST_1'}), 'AWS_US_EAST_1')
        self.assertEqual(
            _row_region({'snowflake_region': 'AWS_EU_WEST_1'}),
            'AWS_EU_WEST_1')
        self.assertIsNone(_row_region({}))
        self.assertIsNone(_row_region({'region': '  '}))

    def test_enrich_account_name_from_org_map(self):
        account_map = {'HW44440': 'PUBLICIS_PROD', 'CP81654': 'PUBLICIS_ADMIN'}
        # Row already has member name — keep it and learn into map.
        row = {'account_locator': 'HW44440', 'account_name': 'PUBLICIS_PROD'}
        enrich_account_name(
            row, account_map, billing_source='organization_usage')
        self.assertEqual(row['account_name'], 'PUBLICIS_PROD')
        # Locator only — fill from org ACCOUNTS map (not session admin).
        row2 = {'account_locator': 'KD54336'}
        account_map['KD54336'] = 'PUBLICIS_OTHER'
        enrich_account_name(
            row2, account_map, session_account_name='PUBLICIS_ADMIN',
            billing_source='organization_usage')
        self.assertEqual(row2['account_name'], 'PUBLICIS_OTHER')
        # Org must not fall back to session admin for unknown locators.
        row3 = {'account_locator': 'UNKNOWN01'}
        enrich_account_name(
            row3, account_map, session_account_name='PUBLICIS_ADMIN',
            billing_source='organization_usage')
        self.assertIsNone(row3.get('account_name'))

    def test_apply_product_tag(self):
        product_map = {
            ('BILLING_WH', 'COMPUTE', '2026Q2'): 'Core Infrastructure',
            ('CLIENT_DATAMART_PROD', 'AUTOMATIC_CLUSTERING', '2026Q3'): (
                'Client Datamart'),
            ('SNOWPIPE', 'SNOWPIPE', '2026Q2'): 'Core Infrastructure',
            ('AI_SERVICES', 'AI_SERVICES', '2026Q2'): 'Core Infrastructure',
            ('STAGE', 'STORAGE', '2026Q2'): 'Core Infrastructure',
        }
        compute = {
            'service_type': 'COMPUTE',
            'resource_name': 'BILLING_WH',
            'start_date': datetime(2026, 4, 15, tzinfo=timezone.utc),
        }
        apply_product_tag(compute, product_map)
        self.assertEqual(compute['tag'], 'Core Infrastructure')

        # Billing in Q1 has no mapping row → no tag.
        compute_old = {
            'service_type': 'COMPUTE',
            'resource_name': 'BILLING_WH',
            'start_date': datetime(2026, 1, 10, tzinfo=timezone.utc),
        }
        apply_product_tag(compute_old, product_map)
        self.assertNotIn('tag', compute_old)

        clustering = {
            'service_type': 'AUTOMATIC_CLUSTERING',
            'database_name': 'CLIENT_DATAMART_PROD',
            'resource_name': 'CLIENT_DATAMART_PROD.SCHEMA.TABLE',
            'start_date': datetime(2026, 7, 1, tzinfo=timezone.utc),
        }
        apply_product_tag(clustering, product_map)
        self.assertEqual(clustering['tag'], 'Client Datamart')

        pipe = {
            'service_type': 'SNOWPIPE',
            'resource_name': 'my_pipe',
            'start_date': datetime(2026, 5, 1, tzinfo=timezone.utc),
        }
        apply_product_tag(pipe, product_map)
        self.assertEqual(pipe['tag'], 'Core Infrastructure')

        ai = {
            'service_type': 'AI_SERVICES',
            'resource_name': 'model-x',
            'start_date': datetime(2026, 6, 1, tzinfo=timezone.utc),
        }
        apply_product_tag(ai, product_map)
        self.assertEqual(ai['tag'], 'Core Infrastructure')

        stage = {
            'service_type': 'STAGE',
            'resource_name': 'STAGE',
            'start_date': datetime(2026, 4, 1, tzinfo=timezone.utc),
        }
        apply_product_tag(stage, product_map)
        self.assertEqual(stage['tag'], 'Core Infrastructure')

        # Org multi-account: mapping is keyed by account_locator.
        org_map = {
            ('HW44440', 'BILLING_WH', 'COMPUTE', '2026Q2'): 'Client Datamart',
            ('CP81654', 'BILLING_WH', 'COMPUTE', '2026Q2'): 'Core Infrastructure',
        }
        hw = {
            'service_type': 'COMPUTE',
            'resource_name': 'BILLING_WH',
            'account_locator': 'HW44440',
            'start_date': datetime(2026, 4, 15, tzinfo=timezone.utc),
        }
        apply_product_tag(hw, org_map)
        self.assertEqual(hw['tag'], 'Client Datamart')
        cp = {
            'service_type': 'COMPUTE',
            'resource_name': 'BILLING_WH',
            'account_locator': 'CP81654',
            'start_date': datetime(2026, 4, 15, tzinfo=timezone.utc),
        }
        apply_product_tag(cp, org_map)
        self.assertEqual(cp['tag'], 'Core Infrastructure')

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

    def test_calculate_cost_from_effective_rate(self):
        self.assertEqual(
            calculate_cost({'credits_used': 4, 'effective_rate': 2.0}), 8.0)
        self.assertEqual(
            calculate_cost({
                'credits_used_compute': 2,
                'credits_used_cloud_services': 1,
                'effective_rate': 3.0,
                'cloud_services_effective_rate': 1.5,
            }), 2 * 3.0 + 1 * 1.5)
        # 1 TiB for one day in June (30 days) => 30/30 = 1.0
        self.assertAlmostEqual(
            calculate_cost({
                'average_bytes': 1024 ** 4,
                'start_date': datetime(2026, 6, 15, tzinfo=timezone.utc),
                'effective_rate': 30.0,
            }), 1.0)
        # 1 TiB for one day in July (31 days) => 30/31
        self.assertAlmostEqual(
            calculate_cost({
                'average_bytes': 1024 ** 4,
                'start_date': datetime(2026, 7, 1, tzinfo=timezone.utc),
                'effective_rate': 30.0,
            }), 30.0 / 31.0)
        self.assertEqual(
            calculate_cost({'bytes_transferred': 1024 ** 4}), 0.0)
        self.assertAlmostEqual(
            calculate_cost({'billable_amount': 42.5}), 42.5)
        self.assertEqual(
            calculate_cost({'credits_used': 5}), 0.0)


class TestSnowflakeReconcile(unittest.TestCase):
    def _org_adapter(self):
        from tools.cloud_adapter.clouds.snowflake import Snowflake
        return Snowflake({
            'account': 'a', 'user': 'u', 'private_key': 'k',
            'warehouse': 'w',
            'billing_source': 'organization_usage',
        })

    def test_reconcile_warns_on_large_delta(self):
        adapter = self._org_adapter()
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
        adapter = self._org_adapter()
        start = datetime(2026, 6, 8, tzinfo=timezone.utc)
        end = datetime(2026, 6, 10, tzinfo=timezone.utc)
        cursor = MagicMock()
        warnings = adapter._reconcile_credits(
            cursor, start, end, {('COMPUTE', start.date()): 10.0})
        self.assertEqual(warnings, [])
        cursor.execute.assert_not_called()
        details = adapter.get_import_details()
        self.assertEqual(details['reconciliation'][0]['status'], 'skipped')

    def test_reconcile_skips_partial_first_day(self):
        """Incremental imports start mid-day; that day must not be compared."""
        adapter = self._org_adapter()
        # start mid-day 06-01 → first full reconcile day is 06-02.
        start = datetime(2026, 6, 1, 23, 3, 31, tzinfo=timezone.utc)
        end = datetime(2026, 6, 10, tzinfo=timezone.utc)
        partial_day = start.date()
        full_day = partial_day + timedelta(days=1)
        cursor = MagicMock()
        cursor.description = [
            ('SERVICE_TYPE',), ('USAGE_DATE',),
            ('CREDITS_USED_COMPUTE',), ('CREDITS_USED_CLOUD_SERVICES',),
            ('CREDITS_BILLED',),
        ]
        cursor.__iter__ = MagicMock(return_value=iter([
            # Full first calendar day in daily would create a false mismatch
            # if included; only the next day should be compared.
            ('WAREHOUSE_METERING', partial_day, 1456.0, 0.0, 1456.0),
            ('WAREHOUSE_METERING', full_day, 50.0, 0.0, 50.0),
        ]))
        with patch(
                'tools.cloud_adapter.clouds.snowflake.load_sql',
                return_value='SELECT 1'):
            warnings = adapter._reconcile_credits(
                cursor, start, end,
                {
                    ('COMPUTE', partial_day): 0.0,
                    ('COMPUTE', full_day): 50.0,
                })
        self.assertEqual(warnings, [])
        details = adapter.get_import_details()
        row = details['reconciliation'][0]
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['detail'], 50.0)
        self.assertEqual(row['daily'], 50.0)
        self.assertEqual(row['window_start'], str(full_day))
        # Daily query must start at the first full day, not the partial one.
        self.assertEqual(
            cursor.execute.call_args.args[1][0], full_day)


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

    def test_validate_credentials_returns_region(self):
        from tools.cloud_adapter.clouds.snowflake import Snowflake
        adapter = Snowflake({
            'account': 'a', 'user': 'u', 'private_key': 'k',
            'warehouse': 'w',
        })
        fake_conn = MagicMock()
        fake_cursor = MagicMock()
        fake_cursor.fetchone.return_value = (
            'HW44440', 'svc', 'ACCOUNTADMIN', 'COMPUTE_WH', 'AWS_US_EAST_1')
        fake_conn.cursor.return_value = fake_cursor
        with patch.object(adapter, 'connect', return_value=fake_conn), \
                patch.object(adapter, 'close'), \
                patch.object(adapter, '_probe_view'):
            result = adapter.validate_credentials()
        self.assertEqual(result['account_id'], 'HW44440')
        self.assertEqual(result['region'], 'AWS_US_EAST_1')
        fake_cursor.execute.assert_any_call(
            'SELECT CURRENT_ACCOUNT(), CURRENT_USER(), '
            'CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_REGION()')

    def test_connect_retries_expired_jwt(self):
        from tools.cloud_adapter.clouds.snowflake import (
            CONNECT_JWT_RETRY_ATTEMPTS, Snowflake)
        from tools.cloud_adapter.exceptions import CloudConnectionError
        adapter = Snowflake({
            'account': 'a', 'user': 'u', 'private_key': 'k',
            'warehouse': 'w',
        })
        fake_conn = MagicMock()
        fake_conn.cursor.return_value = MagicMock()
        jwt_err = Exception(
            '250001 (08001): Failed to connect to DB: x.snowflakecomputing.com:443. '
            'JWT token is invalid.')
        with patch.object(
                adapter, '_load_private_key_bytes', return_value=b'key'), \
                patch.dict('sys.modules', {
                    'snowflake': MagicMock(),
                    'snowflake.connector': MagicMock(),
                }), \
                patch('tools.cloud_adapter.clouds.snowflake.time.sleep') as sleep:
            import snowflake.connector as sf_connector
            sf_connector.connect = MagicMock(
                side_effect=[jwt_err, jwt_err, fake_conn])
            conn = adapter.connect()
        self.assertIs(conn, fake_conn)
        self.assertEqual(sf_connector.connect.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_connect_does_not_retry_non_jwt_errors(self):
        from tools.cloud_adapter.clouds.snowflake import Snowflake
        from tools.cloud_adapter.exceptions import CloudConnectionError
        adapter = Snowflake({
            'account': 'a', 'user': 'u', 'private_key': 'k',
            'warehouse': 'w',
        })
        with patch.object(
                adapter, '_load_private_key_bytes', return_value=b'key'), \
                patch.dict('sys.modules', {
                    'snowflake': MagicMock(),
                    'snowflake.connector': MagicMock(),
                }), \
                patch('tools.cloud_adapter.clouds.snowflake.time.sleep') as sleep:
            import snowflake.connector as sf_connector
            sf_connector.connect = MagicMock(
                side_effect=Exception('warehouse suspended'))
            with self.assertRaises(CloudConnectionError):
                adapter.connect()
        self.assertEqual(sf_connector.connect.call_count, 1)
        sleep.assert_not_called()

    def test_connect_exhausted_jwt_retries(self):
        from tools.cloud_adapter.clouds.snowflake import (
            CONNECT_JWT_RETRY_ATTEMPTS, Snowflake)
        from tools.cloud_adapter.exceptions import CloudConnectionError
        adapter = Snowflake({
            'account': 'a', 'user': 'u', 'private_key': 'k',
            'warehouse': 'w',
        })
        jwt_err = Exception('JWT token is invalid.')
        with patch.object(
                adapter, '_load_private_key_bytes', return_value=b'key'), \
                patch.dict('sys.modules', {
                    'snowflake': MagicMock(),
                    'snowflake.connector': MagicMock(),
                }), \
                patch('tools.cloud_adapter.clouds.snowflake.time.sleep'):
            import snowflake.connector as sf_connector
            sf_connector.connect = MagicMock(side_effect=jwt_err)
            with self.assertRaises(CloudConnectionError):
                adapter.connect()
        self.assertEqual(
            sf_connector.connect.call_count, CONNECT_JWT_RETRY_ATTEMPTS)


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
        self.assertIsNone(rows[0].get('region'))

    def test_fetch_includes_region(self):
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 2, tzinfo=timezone.utc)
        collector = WarehouseMeteringCollector('organization_usage')
        with patch.object(collector, '_execute', return_value=[{
            'warehouse_id': 11,
            'warehouse_name': 'WH',
            'start_time': start,
            'end_time': end,
            'credits_used': 1.5,
            'credits_used_compute': 1.0,
            'credits_used_cloud_services': 0.5,
            'account_locator': 'HW44440',
            'region': 'AWS_US_EAST_1',
        }]):
            rows = list(collector.fetch(MagicMock(), start, end, 'HW44440'))
        self.assertEqual(rows[0]['region'], 'AWS_US_EAST_1')


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


class TestListingConsumptionCollector(unittest.TestCase):
    def test_fetch_normalizes_rows_and_zero_cost(self):
        event_date = datetime(2026, 7, 1).date()
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 2, tzinfo=timezone.utc)
        cursor = MagicMock()
        cursor.description = [
            ('EVENT_DATE',), ('EXCHANGE_NAME',), ('SNOWFLAKE_REGION',),
            ('LISTING_NAME',), ('LISTING_DISPLAY_NAME',),
            ('LISTING_GLOBAL_NAME',), ('SHARE_NAME',),
            ('CONSUMER_ACCOUNT_LOCATOR',), ('CONSUMER_ACCOUNT_NAME',),
            ('CONSUMER_ORGANIZATION',), ('CONSUMER_NAME',),
            ('JOBS',), ('UNIQUE_USERS_1D',), ('REGION_GROUP',),
        ]
        cursor.__iter__ = MagicMock(return_value=iter([
            (event_date, 'ex', 'AWS_US_EAST_1', 'lst', 'My Listing',
             'GZ123.LISTING', 'SHARE1', 'CONS01', 'cons_acc',
             'org', 'Consumer Co', 42, 3, 'PUBLIC'),
        ]))
        with patch(
                'tools.cloud_adapter.clouds.snowflake.load_sql',
                return_value='SELECT 1'):
            rows = list(ListingConsumptionCollector().fetch(
                cursor, start, end, 'HW44440'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]['resource_id'],
            'HW44440/listing_consumption/GZ123.LISTING/SHARE1/CONS01')
        self.assertEqual(rows[0]['service_type'], 'LISTING_CONSUMPTION')
        self.assertEqual(rows[0]['jobs'], 42.0)
        self.assertEqual(calculate_cost(rows[0], {}), 0.0)


class TestNoDoubleCounting(unittest.TestCase):
    def test_account_and_org_service_types_overlap_only_on_ai_group(self):
        """Shared billing types must not appear on both billing sources.

        ACCOUNT_USAGE may share the AI_SERVICES label across Cortex collectors
        and LISTING_CONSUMPTION / READER_ACCOUNT; ORGANIZATION_USAGE owns
        COMPUTE/STORAGE/etc. Intersection of exclusive sets should be empty
        for billable org types.
        """
        from tools.cloud_adapter.clouds.snowflake import (
            COLLECTORS_ACCOUNT_USAGE,
            COLLECTORS_ORGANIZATION_USAGE,
            ACCOUNT_USAGE_EXCLUSIVE_SERVICE_TYPES,
        )
        account_types = {
            cls.SERVICE_TYPE for cls in COLLECTORS_ACCOUNT_USAGE}
        org_types = {
            cls.SERVICE_TYPE for cls in COLLECTORS_ORGANIZATION_USAGE}
        overlap = account_types & org_types
        self.assertEqual(
            overlap, set(),
            'billing sources must not share service_type labels: %s' % overlap)
        self.assertIn('AI_SERVICES', ACCOUNT_USAGE_EXCLUSIVE_SERVICE_TYPES)
        self.assertIn('LISTING_CONSUMPTION', account_types)
        self.assertIn('COMPUTE', org_types)
        self.assertNotIn('COMPUTE', account_types)
        self.assertNotIn('AI_SERVICES', org_types)

    def test_metering_daily_sql_excludes_dedicated_types(self):
        from tools.cloud_adapter.clouds.snowflake import (
            load_sql, BILLING_SOURCE_ORGANIZATION_USAGE)
        sql = load_sql(
            'metering_daily_history.sql',
            BILLING_SOURCE_ORGANIZATION_USAGE)
        sql_upper = sql.upper()
        for token in (
                'WAREHOUSE_METERING', 'PIPE', 'SNOWPIPE',
                'AUTO_CLUSTERING', 'DATABASE_STORAGE', 'STAGE',
                'REPLICATION'):
            self.assertIn(token, sql_upper)
        self.assertIn('RATE_SHEET_DAILY', sql_upper)
        # Leftovers include AI_INFERENCE and CORTEX_* (priced via rate sheet).
        # REPLICATION is owned by ListingAutoFulfillmentCollector (currency).
        not_in = sql_upper.split('NOT IN')[1].split(')')[0]
        self.assertIn('REPLICATION', not_in)
        self.assertNotIn('AI_INFERENCE', not_in)
        self.assertNotIn('AI_SERVICES', not_in)
        self.assertNotIn("NOT LIKE 'CORTEX", sql_upper)
        self.assertNotIn('NOT LIKE "CORTEX', sql_upper)

    def test_data_transfer_sql_excludes_listing_replication(self):
        from tools.cloud_adapter.clouds.snowflake import (
            load_sql, BILLING_SOURCE_ORGANIZATION_USAGE)
        sql = load_sql(
            'data_transfer_history.sql',
            BILLING_SOURCE_ORGANIZATION_USAGE)
        sql_upper = sql.upper()
        self.assertIn("TRANSFER_TYPE <> 'REPLICATION'", sql_upper)

    def test_database_storage_sql_skips_listing_storage_days(self):
        from tools.cloud_adapter.clouds.snowflake import (
            load_sql, BILLING_SOURCE_ORGANIZATION_USAGE)
        sql = load_sql(
            'database_storage_usage_history.sql',
            BILLING_SOURCE_ORGANIZATION_USAGE)
        sql_upper = sql.upper()
        self.assertIn('LISTING_AUTO_FULFILLMENT_USAGE_HISTORY', sql_upper)
        self.assertIn('NOT EXISTS', sql_upper)
        self.assertIn("SERVICE_TYPE = 'STORAGE'", sql_upper)

    def test_calculate_cost_prefers_billable_amount(self):
        record = {
            'billable_amount': 12.5,
            'credits_used': 100,
            'effective_rate': 3.0,
        }
        self.assertEqual(calculate_cost(record), 12.5)


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


class TestWarehouseUnavailable(unittest.TestCase):
    WH_QUOTA_ERR = (
        "090073 (22000): 01c623b2-000a-88d1-0002-11be0225402e: "
        "Warehouse 'INFRASTRUCTURE_TEST_WH' cannot be resumed because "
        "resource monitor 'INFRASTRUCTURE_TEST_RM' has exceeded its quota."
    )

    def _adapter(self):
        from tools.cloud_adapter.clouds.snowflake import Snowflake
        return Snowflake({
            'account': 'a', 'user': 'u', 'private_key': 'k',
            'warehouse': 'INFRASTRUCTURE_TEST_WH',
            'billing_source': 'organization_usage',
        })

    def test_detects_resource_monitor_quota_error(self):
        from tools.cloud_adapter.clouds.snowflake import Snowflake
        self.assertTrue(
            Snowflake._is_warehouse_unavailable_error(
                Exception(self.WH_QUOTA_ERR)))
        self.assertFalse(
            Snowflake._is_warehouse_unavailable_error(
                Exception('Object does not exist or not authorized')))

    def test_download_usage_fails_on_warehouse_quota(self):
        from tools.cloud_adapter.clouds.snowflake import (
            WarehouseMeteringCollector)
        from tools.cloud_adapter.exceptions import CloudConnectionError
        adapter = self._adapter()
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        end = datetime(2026, 8, 2, tzinfo=timezone.utc)
        fake_cursor = MagicMock()
        fake_cursor.fetchone.return_value = ('LOC', 'ADMIN')
        fake_conn = MagicMock()
        fake_conn.cursor.return_value = fake_cursor

        def boom(*_a, **_k):
            raise Exception(self.WH_QUOTA_ERR)

        with patch.object(adapter, 'connect', return_value=fake_conn), \
                patch.object(adapter, 'close'), \
                patch.object(
                    adapter, '_load_product_map', return_value={}), \
                patch.object(
                    adapter, '_load_account_name_map', return_value={}), \
                patch.object(
                    adapter, '_collectors',
                    return_value=[WarehouseMeteringCollector]), \
                patch.object(
                    WarehouseMeteringCollector, 'fetch', side_effect=boom):
            with self.assertRaises(CloudConnectionError) as ctx:
                list(adapter.download_usage(start, end))
        self.assertIn('warehouse unavailable', str(ctx.exception).lower())
        self.assertIn('090073', str(ctx.exception))

    def test_download_usage_fails_over_to_backup_warehouse(self):
        from tools.cloud_adapter.clouds.snowflake import (
            Snowflake, WarehouseMeteringCollector)
        adapter = Snowflake({
            'account': 'a', 'user': 'u', 'private_key': 'k',
            'warehouse': 'INFRASTRUCTURE_TEST_WH',
            'backup_warehouse': 'COMPUTE_WH',
            'billing_source': 'organization_usage',
        })
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        end = datetime(2026, 8, 2, tzinfo=timezone.utc)
        fake_cursor = MagicMock()
        fake_cursor.fetchone.return_value = ('LOC', 'ADMIN')
        fake_conn = MagicMock()
        fake_conn.cursor.return_value = fake_cursor
        calls = {'n': 0}

        def fetch_once(*_a, **_k):
            calls['n'] += 1
            if calls['n'] == 1:
                raise Exception(self.WH_QUOTA_ERR)
            return iter([{
                'service_type': 'COMPUTE',
                'start_date': start,
                'credits_used': 1.0,
                'average_bytes': 0,
            }])

        with patch.object(adapter, 'connect', return_value=fake_conn), \
                patch.object(adapter, 'close'), \
                patch.object(
                    adapter, '_load_product_map', return_value={}), \
                patch.object(
                    adapter, '_load_account_name_map', return_value={}), \
                patch.object(
                    adapter, '_reconcile_credits', return_value=[]), \
                patch.object(
                    adapter, '_collectors',
                    return_value=[WarehouseMeteringCollector]), \
                patch.object(
                    WarehouseMeteringCollector, 'fetch',
                    side_effect=fetch_once):
            rows = list(adapter.download_usage(start, end))
        self.assertEqual(len(rows), 1)
        self.assertEqual(adapter.warehouse, 'COMPUTE_WH')
        self.assertTrue(adapter._used_backup_warehouse)

    def test_missing_view_still_soft_fails(self):
        from tools.cloud_adapter.clouds.snowflake import (
            WarehouseMeteringCollector)
        adapter = self._adapter()
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        end = datetime(2026, 8, 2, tzinfo=timezone.utc)
        fake_cursor = MagicMock()
        fake_cursor.fetchone.return_value = ('LOC', 'ADMIN')
        fake_conn = MagicMock()
        fake_conn.cursor.return_value = fake_cursor

        def missing_view(*_a, **_k):
            raise Exception('Object does not exist or not authorized')

        with patch.object(adapter, 'connect', return_value=fake_conn), \
                patch.object(adapter, 'close'), \
                patch.object(
                    adapter, '_load_product_map', return_value={}), \
                patch.object(
                    adapter, '_load_account_name_map', return_value={}), \
                patch.object(
                    adapter, '_collectors',
                    return_value=[WarehouseMeteringCollector]), \
                patch.object(
                    adapter, '_reconcile_credits', return_value=[]), \
                patch.object(
                    WarehouseMeteringCollector, 'fetch',
                    side_effect=missing_view):
            list(adapter.download_usage(start, end))
        warnings = adapter.get_import_warnings()
        self.assertEqual(len(warnings), 1)
        self.assertIn('skipped:', warnings[0])


class TestListingAndTransferNaming(unittest.TestCase):
    def test_listing_auto_fulfillment_name_is_sf_service_type(self):
        collector = ListingAutoFulfillmentCollector('organization_usage')
        day = datetime(2026, 7, 1).date()
        with patch.object(collector, '_execute', return_value=[{
            'usage_date': day,
            'account_locator': 'HW44440',
            'account_name': 'PUBLICIS_PROD',
            'service_type': 'REPLICATION',
            'currency': 'USD',
            'estimated_usage_in_currency': 12.5,
            'provider_account_locator': 'PROV01',
        }]):
            rows = list(collector.fetch(
                MagicMock(),
                datetime(2026, 7, 1, tzinfo=timezone.utc),
                datetime(2026, 7, 2, tzinfo=timezone.utc),
                'HW44440'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['service_type'], 'LISTING_AUTO_FULFILLMENT')
        self.assertEqual(rows[0]['resource_name'], 'REPLICATION')
        self.assertEqual(rows[0]['sf_service_type'], 'REPLICATION')
        self.assertEqual(
            rows[0]['resource_id'],
            'HW44440/listing_auto_fulfillment/REPLICATION')

    def test_data_transfer_name_is_transfer_type(self):
        collector = DataTransferCollector('organization_usage')
        day = datetime(2026, 7, 1).date()
        with patch.object(collector, '_execute', return_value=[{
            'start_time': day,
            'end_time': day,
            'account_locator': 'HW44440',
            'account_name': 'PUBLICIS_PROD',
            'source_region': 'AWS_US_EAST_1',
            'target_region': 'AWS_EU_WEST_1',
            'transfer_type': 'EXTERNAL',
            'bytes_transferred': 1024,
            'region': 'AWS_US_EAST_1',
        }]):
            rows = list(collector.fetch(
                MagicMock(),
                datetime(2026, 7, 1, tzinfo=timezone.utc),
                datetime(2026, 7, 2, tzinfo=timezone.utc),
                'HW44440'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['service_type'], 'DATA_TRANSFER')
        self.assertEqual(rows[0]['resource_name'], 'EXTERNAL')
        self.assertEqual(rows[0]['region'], 'AWS_US_EAST_1')
        # Regions stay in id so same transfer_type cannot collide.
        self.assertIn('AWS_US_EAST_1/AWS_EU_WEST_1/EXTERNAL/',
                      rows[0]['resource_id'])

    def test_data_transfer_same_type_different_regions_keep_distinct_ids(self):
        collector = DataTransferCollector('organization_usage')
        day = datetime(2026, 7, 1).date()
        with patch.object(collector, '_execute', return_value=[
            {
                'start_time': day,
                'end_time': day,
                'account_locator': 'HW44440',
                'source_region': 'AWS_US_EAST_1',
                'target_region': 'AWS_EU_WEST_1',
                'transfer_type': 'EXTERNAL',
                'bytes_transferred': 1,
            },
            {
                'start_time': day,
                'end_time': day,
                'account_locator': 'HW44440',
                'source_region': 'AWS_US_WEST_2',
                'target_region': 'AWS_EU_CENTRAL_1',
                'transfer_type': 'EXTERNAL',
                'bytes_transferred': 2,
            },
        ]):
            rows = list(collector.fetch(
                MagicMock(),
                datetime(2026, 7, 1, tzinfo=timezone.utc),
                datetime(2026, 7, 2, tzinfo=timezone.utc),
                'HW44440'))
        self.assertEqual(len(rows), 2)
        self.assertEqual({r['resource_name'] for r in rows}, {'EXTERNAL'})
        self.assertEqual(len({r['resource_id'] for r in rows}), 2)


if __name__ == '__main__':
    unittest.main()
