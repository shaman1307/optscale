#!/usr/bin/env python
"""Tests for SnowflakeTenant children listing."""
import unittest
from unittest.mock import MagicMock, patch

from tools.cloud_adapter.clouds.snowflake_tenant import SnowflakeTenant
from tools.cloud_adapter.enums import CloudTypes


class TestSnowflakeTenantChildren(unittest.TestCase):
    def _tenant(self):
        tenant = SnowflakeTenant.__new__(SnowflakeTenant)
        tenant.config = {
            'account': 'xy12345',
            'user': 'svc',
            'private_key': 'PEM',
            'warehouse': 'WH',
            'billing_source': 'organization_usage',
        }
        return tenant

    def test_get_children_configs_includes_all_locators(self):
        tenant = self._tenant()
        with patch.object(
                tenant, '_list_accounts',
                return_value={
                    'AAA111': {'name': 'ORG_ADMIN', 'region': 'AWS_US_EAST_1'},
                    'BBB222': {'name': 'PROD', 'region': 'AWS_EU_WEST_1'},
                }):
            configs = tenant.get_children_configs()
        by_loc = {c['config']['account_locator']: c for c in configs}
        self.assertEqual(by_loc['AAA111']['name'], 'ORG_ADMIN')
        self.assertEqual(by_loc['BBB222']['name'], 'PROD')
        self.assertEqual(
            by_loc['AAA111']['config']['region'], 'AWS_US_EAST_1')
        self.assertEqual(
            by_loc['BBB222']['config']['region'], 'AWS_EU_WEST_1')
        self.assertTrue(all(
            c['type'] == CloudTypes.SNOWFLAKE.value for c in configs))

    def test_get_children_configs_disambiguates_duplicate_names(self):
        tenant = self._tenant()
        with patch.object(
                tenant, '_list_accounts',
                return_value={
                    'QU34323': {
                        'name': 'PROFITERO_DATA_FOR_MONSTER_BEVERAGE'},
                    'YT11624': {
                        'name': 'PROFITERO_DATA_FOR_MONSTER_BEVERAGE'},
                    'HW44440': {'name': 'PROD'},
                }):
            configs = tenant.get_children_configs()
        by_loc = {c['config']['account_locator']: c for c in configs}
        self.assertEqual(
            by_loc['QU34323']['name'],
            'PROFITERO_DATA_FOR_MONSTER_BEVERAGE (QU34323)')
        self.assertEqual(
            by_loc['YT11624']['name'],
            'PROFITERO_DATA_FOR_MONSTER_BEVERAGE (YT11624)')
        self.assertEqual(by_loc['HW44440']['name'], 'PROD')
        names = [c['name'] for c in configs]
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn('region', by_loc['HW44440']['config'])

    def test_billing_source_must_be_organization_usage(self):
        tenant = self._tenant()
        tenant.config['billing_source'] = 'account_usage'
        with self.assertRaises(Exception):
            _ = tenant.billing_source

    WH_QUOTA_ERR = (
        "090073 (22000): Warehouse 'INFRASTRUCTURE_TEST_WH' cannot be resumed "
        "because resource monitor 'INFRASTRUCTURE_TEST_RM' has exceeded its quota."
    )

    def test_list_accounts_failsover_to_backup_warehouse(self):
        tenant = SnowflakeTenant({
            'account': 'a',
            'user': 'u',
            'private_key': 'k',
            'warehouse': 'INFRASTRUCTURE_TEST_WH',
            'backup_warehouse': 'COMPUTE_WH',
            'billing_source': 'organization_usage',
        })
        fake_cursor = MagicMock()
        fake_cursor.description = [
            ('ACCOUNT_LOCATOR',), ('ACCOUNT_NAME',), ('REGION',)]
        fake_cursor.__iter__.side_effect = lambda: iter([
            ('AAA111', 'ORG_ADMIN', 'AWS_US_EAST_1'),
        ])
        calls = {'n': 0}

        def execute_once(*_a, **_k):
            calls['n'] += 1
            if calls['n'] == 1:
                raise Exception(self.WH_QUOTA_ERR)

        fake_cursor.execute.side_effect = execute_once
        fake_conn = MagicMock()
        fake_conn.cursor.return_value = fake_cursor
        with patch.object(tenant, 'connect', return_value=fake_conn), \
                patch.object(tenant, 'close'):
            accounts = tenant._list_accounts()
        self.assertEqual(accounts['AAA111']['name'], 'ORG_ADMIN')
        self.assertEqual(tenant.warehouse, 'COMPUTE_WH')
        self.assertTrue(tenant._used_backup_warehouse)

    def test_list_accounts_quota_without_backup_raises(self):
        from tools.cloud_adapter.exceptions import CloudConnectionError
        tenant = SnowflakeTenant({
            'account': 'a',
            'user': 'u',
            'private_key': 'k',
            'warehouse': 'INFRASTRUCTURE_TEST_WH',
            'billing_source': 'organization_usage',
        })
        fake_cursor = MagicMock()
        fake_cursor.execute.side_effect = Exception(self.WH_QUOTA_ERR)
        fake_conn = MagicMock()
        fake_conn.cursor.return_value = fake_cursor
        with patch.object(tenant, 'connect', return_value=fake_conn), \
                patch.object(tenant, 'close'):
            with self.assertRaises(CloudConnectionError) as ctx:
                tenant._list_accounts()
        self.assertIn('090073', str(ctx.exception))
        self.assertFalse(tenant._used_backup_warehouse)


if __name__ == '__main__':
    unittest.main()
