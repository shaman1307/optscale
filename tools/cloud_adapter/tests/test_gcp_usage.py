#!/usr/bin/env python
"""Unit tests for GCP BigQuery usage query window."""
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from tools.cloud_adapter.clouds.gcp import Gcp


class TestGcpGetUsage(unittest.TestCase):
    def _adapter(self):
        gcp = Gcp.__new__(Gcp)
        gcp.config = {
            'project_id': 'proj-1',
            'billing_data': {
                'project_id': 'billing-proj',
                'dataset_name': 'billing_dataset',
                'table_name': 'gcp_billing_export_v1_ABC',
            },
        }
        client = MagicMock()
        client.query.return_value = 'query-job'
        # Bypass cached_property that builds a real BigQuery client.
        gcp.__dict__['bigquery_client'] = client
        return gcp

    def test_get_usage_queries_partition_range_not_single_day(self):
        gcp = self._adapter()
        start = datetime(2026, 4, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 30, tzinfo=timezone.utc)

        result = gcp.get_usage(start, end)

        self.assertEqual(result, 'query-job')
        query = gcp.bigquery_client.query.call_args[0][0]
        self.assertIn(
            'billing._PARTITIONTIME >= TIMESTAMP("%s")' % start, query)
        self.assertIn(
            'billing._PARTITIONTIME < TIMESTAMP("%s")' % end, query)
        # Late rows keep their usage_start; do not filter it out of fresh partitions.
        self.assertNotIn('billing.usage_start_time >=', query)
        self.assertNotIn('timedelta', query)
        # Real projects stay on usage window; invoice.month is selected only.
        self.assertNotIn('billing.invoice.month IN', query)
        self.assertIn('as invoice_month', query)
        self.assertNotIn(
            'TIMESTAMP_TRUNC(billing._PARTITIONTIME, DAY) = TIMESTAMP',
            query)
        self.assertIn('ORDER BY', query)
        self.assertIn('billing.project.id = "proj-1"', query)
        order_by = query.split('ORDER BY', 1)[1]
        self.assertIn('start_date', order_by)
        self.assertIn('service', order_by)
        self.assertIn('sku_id', order_by)
        self.assertIn('sku', order_by)
        self.assertNotIn('service.description', order_by)
        self.assertNotIn('sku.description', order_by)
        self.assertNotIn('TO_JSON_STRING', order_by)
        self.assertNotIn('UNNEST(tags)', order_by)
        self.assertNotIn('invoice_month', order_by)

    def test_get_usage_does_not_pad_partitions(self):
        gcp = self._adapter()
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        end = datetime(2026, 8, 2, tzinfo=timezone.utc)
        gcp.get_usage(start, end)
        query = gcp.bigquery_client.query.call_args[0][0]
        from datetime import timedelta
        pad_start = start - timedelta(days=60)
        pad_end = end + timedelta(days=60)
        self.assertNotIn(
            'billing._PARTITIONTIME >= TIMESTAMP("%s")' % pad_start, query)
        self.assertNotIn(
            'billing._PARTITIONTIME < TIMESTAMP("%s")' % pad_end, query)
        self.assertIn(
            'billing._PARTITIONTIME >= TIMESTAMP("%s")' % start, query)
        self.assertIn(
            'billing._PARTITIONTIME < TIMESTAMP("%s")' % end, query)

    def test_unassigned_project_filters_null_project_id(self):
        # Legacy bucket is drained: must not import all null-project rows.
        gcp = self._adapter()
        gcp.config['project_id'] = Gcp.UNASSIGNED_PROJECT_ID

        gcp.get_usage(
            datetime(2026, 6, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        query = gcp.bigquery_client.query.call_args[0][0]
        self.assertIn('FALSE', query)
        self.assertNotIn('billing.project.id IS NULL', query)

    def test_virtual_service_project_filters_coalesce_bucket(self):
        gcp = self._adapter()
        gcp.config['project_id'] = Gcp.virtual_service_project_id(
            'Compute Engine')

        gcp.get_usage(
            datetime(2026, 6, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        query = gcp.bigquery_client.query.call_args[0][0]
        self.assertIn('billing.project.id IS NULL', query)
        self.assertIn(
            'COALESCE(billing.project.name, billing.service.description) '
            '= "Compute Engine"',
            query)
        self.assertNotIn('billing.project.id = "', query)
        self.assertIn('billing.invoice.month IN ("202606")', query)
        self.assertIn('billing.invoice.month as invoice_month', query)
        self.assertIn(
            'billing._PARTITIONTIME >= TIMESTAMP("%s")' % datetime(
                2026, 5, 1, tzinfo=timezone.utc),
            query)
        order_by = query.split('ORDER BY', 1)[1]
        self.assertIn('invoice_month', order_by)

    def test_get_usage_month_by_resource_type_is_grouped_sum(self):
        gcp = self._adapter()
        job = MagicMock()
        job.result.return_value = [
            {'resource_type': 'Instance', 'billed_sum': 12.5, 'resource_count': 3},
        ]
        gcp.bigquery_client.query.return_value = job
        month_start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        month_end = datetime(2026, 9, 1, tzinfo=timezone.utc)
        partition_end = datetime(2026, 8, 14, tzinfo=timezone.utc)

        rows = gcp.get_usage_month_by_resource_type(
            month_start, month_end, partition_end)

        self.assertEqual(rows[0]['resource_type'], 'Instance')
        self.assertEqual(rows[0]['billed_sum'], 12.5)
        self.assertEqual(rows[0]['resource_count'], 3)
        query = gcp.bigquery_client.query.call_args[0][0]
        self.assertIn('GROUP BY resource_type', query)
        self.assertIn('SUM(billed) AS billed_sum', query)
        self.assertIn('COUNT(DISTINCT resource_key)', query)
        self.assertIn('LIKE "%instance%"', query)
        self.assertIn('LIKE "%pd capacity%"', query)
        self.assertIn(
            'billing.usage_start_time >= TIMESTAMP("%s")' % month_start, query)
        self.assertIn(
            'billing._PARTITIONTIME < TIMESTAMP("%s")' % partition_end, query)
        self.assertNotIn('ORDER BY', query)

    def test_virtual_get_usage_month_by_resource_type_uses_invoice_month(self):
        gcp = self._adapter()
        gcp.config['project_id'] = Gcp.virtual_service_project_id('Support')
        job = MagicMock()
        job.result.return_value = []
        gcp.bigquery_client.query.return_value = job

        gcp.get_usage_month_by_resource_type(
            datetime(2026, 8, 1, tzinfo=timezone.utc),
            datetime(2026, 9, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 14, tzinfo=timezone.utc),
        )
        query = gcp.bigquery_client.query.call_args[0][0]
        self.assertIn('billing.invoice.month IN ("202608")', query)
        self.assertNotIn('billing.usage_start_time >=', query)
        self.assertIn(
            'billing._PARTITIONTIME >= TIMESTAMP("%s")' % datetime(
                2026, 7, 1, tzinfo=timezone.utc),
            query)


    def test_previous_calendar_month_start(self):
        self.assertEqual(
            Gcp.previous_calendar_month_start(
                datetime(2026, 8, 1, tzinfo=timezone.utc)),
            datetime(2026, 7, 1, tzinfo=timezone.utc))
        self.assertEqual(
            Gcp.previous_calendar_month_start(
                datetime(2026, 1, 15, tzinfo=timezone.utc)),
            datetime(2025, 12, 1, tzinfo=timezone.utc))
    def test_invoice_months_for_window(self):
        months = Gcp.invoice_months_for_window(
            datetime(2026, 6, 15, tzinfo=timezone.utc),
            datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(months, ['202606', '202607'])

    def test_remap_timestamp_to_invoice_month(self):
        ts = datetime(2026, 8, 1, 14, 30, tzinfo=timezone.utc)
        remapped = Gcp.remap_timestamp_to_invoice_month(ts, '202607')
        self.assertEqual(
            remapped, datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc))

    def test_remap_clamps_day_to_invoice_month_length(self):
        ts = datetime(2026, 3, 31, 10, 0, tzinfo=timezone.utc)
        remapped = Gcp.remap_timestamp_to_invoice_month(ts, '202602')
        self.assertEqual(
            remapped, datetime(2026, 2, 28, 10, 0, tzinfo=timezone.utc))


if __name__ == '__main__':
    unittest.main()
