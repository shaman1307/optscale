#!/usr/bin/env python
"""Tests for virtual GCP CAs covering null project.id billing rows."""
import unittest
from unittest.mock import MagicMock, patch

from tools.cloud_adapter.clouds.gcp import Gcp
from tools.cloud_adapter.clouds.gcp_tenant import GcpTenant
from tools.cloud_adapter.model import InstanceResource


class TestGcpTenantUnassignedProject(unittest.TestCase):
    def _tenant(self):
        tenant = GcpTenant.__new__(GcpTenant)
        tenant.config = {
            'credentials': {'client_id': 'cid', 'type': 'service_account'},
            'billing_data': {
                'project_id': 'billing-proj',
                'dataset_name': 'billing_export',
                'table_name': 'gcp_billing_export_v1_ABC',
            },
        }
        tenant.__dict__['bigquery_client'] = MagicMock()
        return tenant

    def test_list_projects_maps_null_to_service_buckets(self):
        tenant = self._tenant()
        compute_pid = Gcp.virtual_service_project_id('Compute Engine')
        support_pid = Gcp.virtual_service_project_id('Support')
        # (project_id, name, export_time)
        tenant.bigquery_client.query.return_value.result.return_value = [
            ('proj-a', 'Project A', 2),
            (compute_pid, 'Compute Engine', 1),
            (support_pid, 'Support', 1),
        ]
        with patch.object(tenant, '_list_null_project_buckets',
                          return_value=[]):
            projects = tenant._list_projects()
        self.assertEqual(projects['proj-a'], 'Project A')
        self.assertEqual(projects[compute_pid], 'Compute Engine')
        self.assertEqual(projects[support_pid], 'Support')
        self.assertNotIn(Gcp.UNASSIGNED_PROJECT_ID, projects)

    def test_list_projects_adds_virtual_from_long_lookback(self):
        tenant = self._tenant()
        tenant.bigquery_client.query.return_value.result.return_value = [
            ('proj-a', 'Project A', 1),
        ]
        with patch.object(
                tenant, '_list_null_project_buckets',
                return_value=['Compute Engine', 'Support']):
            projects = tenant._list_projects()
        compute_pid = Gcp.virtual_service_project_id('Compute Engine')
        support_pid = Gcp.virtual_service_project_id('Support')
        self.assertEqual(projects[compute_pid], 'Compute Engine')
        self.assertEqual(projects[support_pid], 'Support')

    def test_children_configs_include_service_buckets(self):
        tenant = self._tenant()
        compute_pid = Gcp.virtual_service_project_id('Compute Engine')
        with patch.object(
                tenant, '_list_projects',
                return_value={
                    'proj-a': 'Project A',
                    compute_pid: 'Compute Engine',
                }):
            configs = tenant.get_children_configs()
        by_pid = {c['config']['project_id']: c for c in configs}
        self.assertEqual(by_pid[compute_pid]['name'], 'Compute Engine')

    def test_list_null_project_buckets_uses_invoice_month(self):
        tenant = self._tenant()
        tenant.bigquery_client.query.return_value.result.return_value = [
            ('Compute Engine',),
            ('Support',),
        ]
        buckets = tenant._list_null_project_buckets(lookback_days=60)
        self.assertEqual(buckets, ['Compute Engine', 'Support'])
        query = tenant.bigquery_client.query.call_args[0][0]
        self.assertIn('invoice.month IN (', query)
        self.assertIn('_PARTITIONTIME >= TIMESTAMP(', query)
        self.assertIn('_PARTITIONTIME < TIMESTAMP(', query)
        self.assertNotIn('TIMESTAMP_TRUNC(_PARTITIONTIME, DAY)', query)


class TestGcpVirtualBillingFilter(unittest.TestCase):
    def _adapter(self, project_id):
        gcp = Gcp.__new__(Gcp)
        gcp.config = {
            'project_id': project_id,
            'billing_data': {
                'project_id': 'billing-proj',
                'dataset_name': 'billing_dataset',
                'table_name': 'gcp_billing_export_v1_ABC',
            },
        }
        return gcp

    def test_service_bucket_filters_null_project_and_coalesce_name(self):
        pid = Gcp.virtual_service_project_id('Compute Engine')
        gcp = self._adapter(pid)
        sql = gcp._billing_project_filter_sql()
        self.assertIn('billing.project.id IS NULL', sql)
        self.assertIn(
            'COALESCE(billing.project.name, billing.service.description) '
            '= "Compute Engine"',
            sql)

    def test_legacy_unassigned_matches_nothing(self):
        gcp = self._adapter(Gcp.UNASSIGNED_PROJECT_ID)
        self.assertEqual(gcp._billing_project_filter_sql(), 'FALSE')

    def test_discovery_empty_for_virtual_service_ca(self):
        pid = Gcp.virtual_service_project_id('Support')
        gcp = self._adapter(pid)
        self.assertEqual(gcp.discovery_calls_map(), {})

    def test_discovery_map_safe_without_credentials(self):
        # Scheduler historically built adapters with type only.
        gcp = Gcp({'type': 'gcp_cnr'})
        self.assertFalse(gcp.is_virtual_billing_project)
        self.assertIn(InstanceResource, gcp.discovery_calls_map())


if __name__ == '__main__':
    unittest.main()
