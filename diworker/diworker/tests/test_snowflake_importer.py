#!/usr/bin/env python
"""Unit tests for Snowflake report importer helpers.

Requires diworker runtime deps. On a bare host without those packages the
suite is skipped. Inside the diworker image:

  cd /src
  PYTHONPATH=/src:/src/tools /src/diworker/.venv/bin/python \\
    -m unittest diworker.diworker.tests.test_snowflake_importer -v
"""
import unittest
from datetime import datetime

try:
    from diworker.diworker.importers.snowflake import (
        META_FIELDS,
        RESOURCE_TYPE_RENAMES,
        SnowflakeReportImporter,
    )
except ImportError as exc:  # pragma: no cover - host without deps
    SnowflakeReportImporter = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


@unittest.skipIf(
    SnowflakeReportImporter is None,
    'diworker runtime deps missing: %s' % _IMPORT_ERROR)
class TestSnowflakeImporterHelpers(unittest.TestCase):
    def _importer(self):
        # Avoid BaseReportImporter.__init__ (needs full DI wiring).
        imp = SnowflakeReportImporter.__new__(SnowflakeReportImporter)
        object.__setattr__(imp, 'cloud_acc_id', 'ca-1')
        # cloud_acc is a property on BaseReportImporter; only needed for
        # recalculate paths — helpers under test use explicit cost_model args.
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

    def test_meta_fields_include_listing_and_cortex(self):
        for key in (
                'model_name', 'function_name', 'query_id',
                'listing_global_name', 'jobs'):
            self.assertIn(key, META_FIELDS)

    def test_enrich_sets_cost_from_credits(self):
        from tools.cloud_adapter.clouds.snowflake import calculate_cost
        imp = self._importer()
        record = {
            'credits_used': 4.0,
            'service_type': 'COMPUTE',
            'account_locator': 'HW',
            'start_date': datetime(2026, 7, 1),
        }
        imp._enrich_record(record, {'credit_price': 2.5})
        self.assertEqual(record['cloud_account_id'], 'ca-1')
        self.assertEqual(record['cost'], 10.0)
        self.assertEqual(
            calculate_cost(record, {'credit_price': 2.5}), 10.0)


if __name__ == '__main__':
    unittest.main()
