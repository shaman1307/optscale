#!/usr/bin/env python
"""GCP credential validation for shared-billing / non-responsive projects."""
import unittest
from unittest.mock import MagicMock, patch

from tools.cloud_adapter.clouds.gcp import Gcp
from tools.cloud_adapter.exceptions import CloudConnectionError


class TestGcpValidateCredentialsSoftCloud(unittest.TestCase):
    def _adapter(self, project_id='spend-proj', billing_project='billing-proj'):
        gcp = Gcp.__new__(Gcp)
        gcp.config = {
            'project_id': project_id,
            'billing_data': {
                'project_id': billing_project,
                'dataset_name': 'billing_export',
                'table_name': 'gcp_billing_export_v1_ABC',
            },
            'credentials': {'type': 'service_account'},
        }
        gcp._currency = 'USD'
        return gcp

    def test_bigquery_client_uses_billing_project(self):
        gcp = self._adapter()
        with patch(
                'tools.cloud_adapter.clouds.gcp.bigquery.Client'
                '.from_service_account_info') as from_info:
            from_info.return_value = MagicMock()
            _ = gcp.bigquery_client
        from_info.assert_called_once()
        self.assertEqual(
            from_info.call_args.kwargs['project'], 'billing-proj')

    def test_shared_billing_keeps_ca_when_compute_fails(self):
        gcp = self._adapter()
        with patch.object(gcp, '_validate_project_id'), \
                patch.object(gcp, '_validate_billing_config'), \
                patch.object(gcp, '_validate_billing_type'), \
                patch.object(
                    gcp, '_validate_cloud_connection',
                    side_effect=CloudConnectionError('403 compute')), \
                patch.object(gcp, '_test_bigquery_connection'):
            result = gcp.validate_credentials()
        self.assertEqual(result['account_id'], 'spend-proj')
        self.assertEqual(len(result['warnings']), 1)
        self.assertIn('Cloud API check failed', result['warnings'][0])

    def test_same_project_still_fails_on_compute_error(self):
        gcp = self._adapter(
            project_id='same-proj', billing_project='same-proj')
        with patch.object(gcp, '_validate_project_id'), \
                patch.object(gcp, '_validate_billing_config'), \
                patch.object(gcp, '_validate_billing_type'), \
                patch.object(
                    gcp, '_validate_cloud_connection',
                    side_effect=CloudConnectionError('403 compute')), \
                patch.object(gcp, '_test_bigquery_connection') as bq_test:
            with self.assertRaises(CloudConnectionError):
                gcp.validate_credentials()
            bq_test.assert_not_called()

    def test_unassigned_virtual_project_soft_fails_compute(self):
        gcp = self._adapter(project_id=Gcp.UNASSIGNED_PROJECT_ID)
        with patch.object(gcp, '_validate_project_id'), \
                patch.object(gcp, '_validate_billing_config'), \
                patch.object(gcp, '_validate_billing_type'), \
                patch.object(
                    gcp, '_validate_cloud_connection',
                    side_effect=CloudConnectionError('no such project')), \
                patch.object(gcp, '_test_bigquery_connection'):
            result = gcp.validate_credentials()
        self.assertEqual(result['account_id'], Gcp.UNASSIGNED_PROJECT_ID)
        self.assertTrue(result['warnings'])

    def test_service_virtual_project_soft_fails_compute(self):
        pid = Gcp.virtual_service_project_id('Compute Engine')
        gcp = self._adapter(project_id=pid)
        with patch.object(gcp, '_validate_project_id'), \
                patch.object(gcp, '_validate_billing_config'), \
                patch.object(gcp, '_validate_billing_type'), \
                patch.object(
                    gcp, '_validate_cloud_connection',
                    side_effect=CloudConnectionError('no such project')), \
                patch.object(gcp, '_test_bigquery_connection'):
            result = gcp.validate_credentials()
        self.assertEqual(result['account_id'], pid)
        self.assertTrue(result['warnings'])
        self.assertEqual(gcp.discovery_calls_map(), {})

if __name__ == '__main__':
    unittest.main()
