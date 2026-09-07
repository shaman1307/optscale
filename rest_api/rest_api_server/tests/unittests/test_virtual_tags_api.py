from datetime import datetime, timezone
from unittest.mock import patch

from pymongo.errors import CursorNotFound
from sqlalchemy import text

import tools.optscale_time as opttime

from rest_api.rest_api_server.controllers.virtual_tag_apply import (
    VirtualTagApplyController)
from rest_api.rest_api_server.tests.unittests.test_api_base import TestApiBase
from rest_api.rest_api_server.utils import get_nil_uuid


class TestVirtualTagsApi(TestApiBase):
    def setUp(self, version='v2'):
        super().setUp(version)
        patch('rest_api.rest_api_server.controllers.cloud_account.'
              'CloudAccountController._configure_report').start()
        _, self.org = self.client.organization_create({'name': 'vt-org'})
        self.org_id = self.org['id']
        self.auth_user = self.gen_id()
        _, self.employee = self.client.employee_create(
            self.org_id, {
                'name': 'vt-employee',
                'auth_user_id': self.auth_user,
            })
        self.update_default_owner_for_pool(
            self.org['pool_id'], self.employee['id'])
        self._mock_auth_user(self.auth_user)
        self.user = {
            'id': self.auth_user,
            'display_name': 'vt',
            'email': 'vt@example.com',
        }
        self.p_get_user_info.return_value = self.user
        aws = {
            'name': 'proj-a',
            'type': 'aws_cnr',
            'config': {
                'access_key_id': 'key',
                'secret_access_key': 'secret',
                'config_scheme': 'create_report',
            },
        }
        _, self.ca_a = self.create_cloud_account(
            self.org_id, aws, auth_user_id=self.auth_user)
        aws['name'] = 'proj-b'
        _, self.ca_b = self.create_cloud_account(
            self.org_id, aws, auth_user_id=self.auth_user)

    def test_create_assignment_virtual_tag(self):
        code, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
        })
        self.assertEqual(code, 201)
        self.assertEqual(vt['key'], 'PRODUCT')
        self.assertEqual(vt['mode'], 'assignment')
        code, listing = self.client.virtual_tag_list(self.org_id)
        self.assertEqual(code, 200)
        self.assertEqual(len(listing['virtual_tags']), 1)
        self.assertIn(listing['virtual_tags'][0]['quarter'], listing.get('quarters') or [])

    def test_list_quarters_includes_all_org_quarters(self):
        _, first = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product Q2',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        self.assertEqual(first['quarter'], '2026Q2')
        _, second = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product Q3',
            'mode': 'assignment',
            'quarter': '2026Q3',
        })
        self.assertEqual(second['quarter'], '2026Q3')
        code, listing = self.client.virtual_tag_list(
            self.org_id, quarter='2026Q3')
        self.assertEqual(code, 200)
        self.assertEqual(listing['quarters'], ['2026Q2', '2026Q3'])
        self.assertEqual(len(listing['virtual_tags']), 1)
        self.assertEqual(listing['virtual_tags'][0]['id'], second['id'])

    def test_extract_requires_source_tag_key(self):
        code, resp = self.client.virtual_tag_create(self.org_id, {
            'key': 'App Service',
            'name': 'App Service',
            'mode': 'extract',
        })
        self.assertEqual(code, 400)
        self.assertEqual(resp['error']['error_code'], 'OE0578')

    def test_us1_or_branches_100_percent(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
        })
        code, rule = self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'PRODUCT by project',
            'branches': [
                {
                    'conditions': [{
                        'type': 'cloud_is',
                        'meta_info': self.ca_a['id'],
                    }],
                    'allocations': [{'value': 'SNS', 'share': 100}],
                },
                {
                    'conditions': [{
                        'type': 'cloud_is',
                        'meta_info': self.ca_b['id'],
                    }],
                    'allocations': [{'value': 'SBA', 'share': 100}],
                },
            ],
        })
        self.assertEqual(code, 201, rule)
        self.assertEqual(len(rule['branches']), 2)

    def test_us2_split_shares(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
        })
        code, rule = self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'PRODUCT split',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [
                    {'value': 'SNS', 'share': 50},
                    {'value': 'SBA', 'share': 50},
                ],
            }],
        })
        self.assertEqual(code, 201, rule)
        allocs = rule['branches'][0]['allocations']
        self.assertEqual(sorted(a['share'] for a in allocs), [50, 50])

    def test_reject_share_not_100(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
        })
        code, resp = self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'bad shares',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'SNS', 'share': 40}],
            }],
        })
        self.assertEqual(code, 400)
        self.assertEqual(resp['error']['error_code'], 'OE0575')

    def test_reject_split_plus_or_branch(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
        })
        code, resp = self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'split and or',
            'branches': [
                {
                    'conditions': [{
                        'type': 'cloud_is',
                        'meta_info': self.ca_a['id'],
                    }],
                    'allocations': [
                        {'value': 'SNS', 'share': 50},
                        {'value': 'SBA', 'share': 50},
                    ],
                },
                {
                    'conditions': [{
                        'type': 'cloud_is',
                        'meta_info': self.ca_b['id'],
                    }],
                    'allocations': [{'value': 'SNS', 'share': 100}],
                },
            ],
        })
        self.assertEqual(code, 400)
        self.assertEqual(resp['error']['error_code'], 'OE0576')

    def test_reject_duplicate_cloud_is_on_same_vt(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
        })
        code, _ = self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'first',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'SNS', 'share': 100}],
            }],
        })
        self.assertEqual(code, 201)
        code, resp = self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'second',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'SBA', 'share': 100}],
            }],
        })
        self.assertEqual(code, 409)
        self.assertEqual(resp['error']['error_code'], 'OE0577')

    def test_apply_does_not_change_pool_and_sets_virtual_tags(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
        })
        code, _ = self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'PRODUCT by project',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [
                    {'value': 'SNS', 'share': 50},
                    {'value': 'SBA', 'share': 50},
                ],
            }],
        })
        self.assertEqual(code, 201)
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-1',
            'name': 'inst',
            'resource_type': 'Instance',
            'pool_id': self.org['pool_id'],
            'employee_id': self.employee['id'],
        })
        self.assertEqual(code, 201, resource)
        self.assertEqual(resource['pool_id'], self.org['pool_id'])
        values = sorted(
            (row['value'], row['share'])
            for row in resource.get('virtual_tags') or [])
        self.assertEqual(values, [('SBA', 50), ('SNS', 50)])

    def test_extract_from_source_tag(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'App Service',
            'name': 'App Service',
            'mode': 'extract',
            'source_tag_key': 'service',
        })
        self.assertEqual(vt['mode'], 'extract')
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-extract',
            'name': 'db',
            'resource_type': 'Instance',
            'tags': {'service': 'mysql'},
            'pool_id': self.org['pool_id'],
            'employee_id': self.employee['id'],
        })
        self.assertEqual(code, 201, resource)
        self.assertEqual(resource['virtual_tags'], [
            {'key': 'App Service', 'value': 'mysql', 'share': 100},
        ])

    def _mark_seen(self, resource_id, start_ts, end_ts):
        self.resources_collection.update_one(
            filter={'_id': resource_id},
            update={'$set': {
                'first_seen': start_ts,
                'last_seen': end_ts,
                '_first_seen_date': datetime.fromtimestamp(start_ts),
                '_last_seen_date': datetime.fromtimestamp(end_ts),
            }})

    def test_available_filters_virtual_tag_facet(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'split',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [
                    {'value': 'SNS', 'share': 50},
                    {'value': 'SBA', 'share': 50},
                ],
            }],
        })
        start_ts = 1609459200
        end_ts = start_ts + 86400
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-filter',
            'name': 'inst',
            'resource_type': 'Instance',
            'pool_id': self.org['pool_id'],
            'employee_id': self.employee['id'],
            'first_seen': start_ts,
            'last_seen': end_ts,
        })
        self.assertEqual(code, 201, resource)
        self._mark_seen(resource['id'], start_ts, end_ts)
        code, response = self.client.available_filters_get(
            self.org_id, start_ts, end_ts, {'facets': 'virtual_tag'})
        self.assertEqual(code, 200)
        pairs = response['filter_values']['virtual_tag']
        self.assertEqual(pairs[0], {'key': None, 'value': None})
        self.assertCountEqual(pairs, [
            {'key': None, 'value': None},
            {'key': 'PRODUCT', 'value': 'SBA'},
            {'key': 'PRODUCT', 'value': 'SNS'},
        ])

    def test_clean_expenses_filter_contains_virtual_tag(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'split',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [
                    {'value': 'SNS', 'share': 50},
                    {'value': 'SBA', 'share': 50},
                ],
            }],
        })
        start_ts = 1609459200
        end_ts = start_ts + 86400
        code, matched = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-sns',
            'name': 'matched',
            'resource_type': 'Instance',
            'pool_id': self.org['pool_id'],
            'employee_id': self.employee['id'],
            'first_seen': start_ts,
            'last_seen': end_ts,
        })
        self.assertEqual(code, 201, matched)
        code, other = self.cloud_resource_create(self.ca_b['id'], {
            'cloud_resource_id': 'i-vt-other',
            'name': 'other',
            'resource_type': 'Instance',
            'pool_id': self.org['pool_id'],
            'employee_id': self.employee['id'],
            'first_seen': start_ts,
            'last_seen': end_ts,
        })
        self.assertEqual(code, 201, other)
        self._mark_seen(matched['id'], start_ts, end_ts)
        self._mark_seen(other['id'], start_ts, end_ts)
        self.expenses.append({
            'cloud_account_id': self.ca_a['id'],
            'resource_id': matched['id'],
            'date': datetime.fromtimestamp(start_ts),
            'cost': 100,
            'sign': 1,
        })
        self.expenses.append({
            'cloud_account_id': self.ca_b['id'],
            'resource_id': other['id'],
            'date': datetime.fromtimestamp(start_ts),
            'cost': 40,
            'sign': 1,
        })
        code, response = self.client.clean_expenses_get(
            self.org_id, start_ts, end_ts, {
                'virtual_tag': ['PRODUCT:SNS'],
            })
        self.assertEqual(code, 200, response)
        ids = [row['resource_id'] for row in response['clean_expenses']]
        self.assertIn(matched['id'], ids)
        self.assertNotIn(other['id'], ids)
        expense = next(
            row for row in response['clean_expenses']
            if row['resource_id'] == matched['id'])
        self.assertEqual(expense['pool_id'], self.org['pool_id'])
        values = sorted(
            (row['value'], row['share'])
            for row in expense.get('virtual_tags') or [])
        self.assertEqual(values, [('SBA', 50), ('SNS', 50)])
        self.assertAlmostEqual(expense['cost'], 50, places=4)
        self.assertAlmostEqual(response['total_cost'], 50, places=4)
        code, summary = self.client.summary_expenses_get(
            self.org_id, start_ts, end_ts, {
                'virtual_tag': ['PRODUCT:SNS'],
            })
        self.assertEqual(code, 200, summary)
        self.assertAlmostEqual(summary['total_cost'], 50, places=4)
        code, untagged = self.client.clean_expenses_get(
            self.org_id, start_ts, end_ts, {
                'virtual_tag': [get_nil_uuid()],
            })
        self.assertEqual(code, 200, untagged)
        untagged_ids = [
            row['resource_id'] for row in untagged['clean_expenses']]
        self.assertIn(other['id'], untagged_ids)
        self.assertNotIn(matched['id'], untagged_ids)
        self.assertAlmostEqual(untagged['total_cost'], 40, places=4)

    def test_organization_constraint_virtual_tag_filter(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'split',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [
                    {'value': 'SNS', 'share': 50},
                    {'value': 'SBA', 'share': 50},
                ],
            }],
        })
        now = opttime.utcnow_timestamp()
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-constraint',
            'name': 'inst',
            'resource_type': 'Instance',
            'pool_id': self.org['pool_id'],
            'employee_id': self.employee['id'],
            'first_seen': now,
            'last_seen': now,
        })
        self.assertEqual(code, 201, resource)
        self._mark_seen(resource['id'], now - 86400, now)
        code, resp = self.client.organization_constraint_create(self.org_id, {
            'name': 'vt-budget',
            'type': 'expiring_budget',
            'definition': {
                'total_budget': 1000,
                'start_date': now - 86400,
            },
            'filters': {
                'virtual_tag': ['PRODUCT:SNS'],
            },
        })
        self.assertEqual(code, 201, resp)
        self.assertEqual(resp['filters']['virtual_tag'], ['PRODUCT:SNS'])
        code, none_resp = self.client.organization_constraint_create(
            self.org_id, {
                'name': 'vt-no-tag',
                'type': 'expiring_budget',
                'definition': {
                    'total_budget': 1000,
                    'start_date': now - 86400,
                },
                'filters': {
                    'virtual_tag': [get_nil_uuid()],
                },
            })
        self.assertEqual(code, 201, none_resp)
        self.assertEqual(
            none_resp['filters']['virtual_tag'], [get_nil_uuid()])

    def test_breakdown_virtual_tag_applies_share(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'split',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [
                    {'value': 'SNS', 'share': 50},
                    {'value': 'SBA', 'share': 50},
                ],
            }],
        })
        start = datetime(2021, 1, 1, tzinfo=timezone.utc)
        start_ts = int(start.timestamp())
        end_ts = start_ts + 86400
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-bd',
            'name': 'inst',
            'resource_type': 'Instance',
            'pool_id': self.org['pool_id'],
            'employee_id': self.employee['id'],
            'first_seen': start_ts,
            'last_seen': end_ts,
        })
        self.assertEqual(code, 201, resource)
        self._mark_seen(resource['id'], start_ts, end_ts)
        self.expenses.append({
            'cloud_account_id': self.ca_a['id'],
            'resource_id': resource['id'],
            'date': start,
            'cost': 100,
            'sign': 1,
        })
        code, resp = self.client.breakdown_expenses_get(
            self.org_id, start_ts, end_ts,
            breakdown_by='virtual_tag:PRODUCT')
        self.assertEqual(code, 200, resp)
        self.assertIn('SNS', resp.get('counts', {}))
        self.assertIn('SBA', resp.get('counts', {}))
        self.assertAlmostEqual(resp['counts']['SNS']['total'], 50, places=4)
        self.assertAlmostEqual(resp['counts']['SBA']['total'], 50, places=4)
        self.assertEqual(resource['pool_id'], self.org['pool_id'])
        code, filtered = self.client.breakdown_expenses_get(
            self.org_id, start_ts, end_ts,
            breakdown_by='virtual_tag:PRODUCT',
            params={'virtual_tag': ['PRODUCT:SNS']})
        self.assertEqual(code, 200, filtered)
        self.assertIn('SNS', filtered.get('counts', {}))
        self.assertNotIn('SBA', filtered.get('counts', {}))
        self.assertAlmostEqual(filtered['counts']['SNS']['total'], 50, places=4)
        self.assertAlmostEqual(filtered.get('total', 0), 50, places=4)
        code, by_account = self.client.breakdown_expenses_get(
            self.org_id, start_ts, end_ts,
            breakdown_by='cloud_account_id',
            params={'virtual_tag': ['PRODUCT:SNS']})
        self.assertEqual(code, 200, by_account)
        self.assertAlmostEqual(by_account.get('total', 0), 50, places=4)

    def test_resource_count_virtual_tag_contains_not_fractional(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'split',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [
                    {'value': 'SNS', 'share': 50},
                    {'value': 'SBA', 'share': 50},
                ],
            }],
        })
        start_ts = 1609459200
        end_ts = start_ts + 86400
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-count',
            'name': 'inst',
            'resource_type': 'Instance',
            'pool_id': self.org['pool_id'],
            'employee_id': self.employee['id'],
            'first_seen': start_ts,
            'last_seen': end_ts,
        })
        self.assertEqual(code, 201, resource)
        stored = self.resources_collection.find_one({'_id': resource['id']})
        self.assertTrue(stored.get('virtual_tags'), stored)
        self._mark_seen(resource['id'], start_ts, end_ts)
        code, resp = self.client.resources_count_get(
            self.org_id, start_ts, end_ts,
            breakdown_by='virtual_tag:PRODUCT')
        self.assertEqual(code, 200, resp)
        self.assertEqual(resp['count'], 1)
        self.assertEqual(set(resp.get('counts', {})), {'SNS', 'SBA'}, resp)
        self.assertEqual(resp['counts']['SNS']['total'], 1)
        self.assertEqual(resp['counts']['SBA']['total'], 1)

    def test_available_filters_core_and_virtual_tag_together(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'split',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'SNS', 'share': 100}],
            }],
        })
        start_ts = 1609459200
        end_ts = start_ts + 86400
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-core-facet',
            'name': 'inst',
            'resource_type': 'Instance',
            'pool_id': self.org['pool_id'],
            'employee_id': self.employee['id'],
            'first_seen': start_ts,
            'last_seen': end_ts,
        })
        self.assertEqual(code, 201, resource)
        self._mark_seen(resource['id'], start_ts, end_ts)
        code, response = self.client.available_filters_get(
            self.org_id, start_ts, end_ts, {'facets': 'core,virtual_tag'})
        self.assertEqual(code, 200, response)
        self.assertIn('virtual_tag', response['filter_values'])
        self.assertIn('resource_type', response['filter_values'])
        pairs = response['filter_values']['virtual_tag']
        self.assertTrue(any(
            item.get('key') == 'PRODUCT' and item.get('value') == 'SNS'
            for item in pairs))

    def test_patch_source_tag_key_reapplies(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'App Service',
            'name': 'App Service',
            'mode': 'extract',
            'source_tag_key': 'service',
        })
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-rebind',
            'name': 'db',
            'resource_type': 'Instance',
            'tags': {'service': 'mysql', 'env': 'prod'},
            'pool_id': self.org['pool_id'],
            'employee_id': self.employee['id'],
        })
        self.assertEqual(code, 201, resource)
        self.assertEqual(resource['virtual_tags'], [
            {'key': 'App Service', 'value': 'mysql', 'share': 100},
        ])
        code, updated = self.client.virtual_tag_update(vt['id'], {
            'source_tag_key': 'env',
        })
        self.assertEqual(code, 200, updated)
        code, resource = self.client.cloud_resource_get(resource['id'])
        self.assertEqual(code, 200, resource)
        self.assertEqual(resource['virtual_tags'], [
            {'key': 'App Service', 'value': 'prod', 'share': 100},
        ])

    def test_virtual_tag_stats_include_forecast_and_over_limit(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'full',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'SNS', 'share': 100}],
            }],
        })
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-stats',
            'name': 'inst',
            'resource_type': 'Instance',
            'pool_id': self.org['pool_id'],
            'employee_id': self.employee['id'],
        })
        self.assertEqual(code, 201, resource)
        code, _ = self.client.virtual_tag_value_limits_update(vt['id'], {
            'value_limits': [{'value': 'SNS', 'limit': 1}],
        })
        self.assertEqual(code, 200)
        code, item = self.client.virtual_tag_get(vt['id'])
        self.assertEqual(code, 200, item)
        stats = {row['value']: row for row in item.get('stats') or []}
        self.assertIn('SNS', stats)
        self.assertIn('forecast', stats['SNS'])
        self.assertIn('over_limit', stats['SNS'])
        self.assertEqual(stats['SNS']['limit'], 1)

    def _force_stored_mode(self, virtual_tag_id, stored_mode):
        session = self.init_db_session()
        session.execute(
            text('UPDATE virtual_tag SET mode = :mode WHERE id = :id'),
            {'mode': stored_mode, 'id': virtual_tag_id})
        session.commit()
        stored = session.execute(
            text('SELECT mode FROM virtual_tag WHERE id = :id'),
            {'id': virtual_tag_id}).scalar()
        session.close()
        self.assertEqual(stored, stored_mode)

    def test_virtual_tag_mode_accepts_mysql_name_or_value(self):
        from rest_api.rest_api_server.models.enums import VirtualTagModes
        from rest_api.rest_api_server.models.types import VirtualTagMode
        col = VirtualTagMode('mode')
        for raw, expected in (
                ('ASSIGNMENT', VirtualTagModes.ASSIGNMENT),
                ('assignment', VirtualTagModes.ASSIGNMENT),
                ('EXTRACT', VirtualTagModes.EXTRACT),
                ('extract', VirtualTagModes.EXTRACT)):
            self.assertEqual(col.process_result_value(raw, None), expected)
        self.assertEqual(
            col.process_bind_param(VirtualTagModes.ASSIGNMENT, None),
            'assignment')
        self.assertEqual(
            col.process_bind_param(VirtualTagModes.EXTRACT, None),
            'extract')

    def test_list_virtual_tags_when_mysql_stores_enum_names(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
        })
        self._force_stored_mode(vt['id'], 'ASSIGNMENT')
        code, listing = self.client.virtual_tag_list(self.org_id)
        self.assertEqual(code, 200, listing)
        self.assertEqual(len(listing['virtual_tags']), 1)
        self.assertEqual(listing['virtual_tags'][0]['mode'], 'assignment')
        code, item = self.client.virtual_tag_get(vt['id'])
        self.assertEqual(code, 200, item)
        self.assertEqual(item['mode'], 'assignment')

    def test_report_import_bulk_loads_assignment_virtual_tags(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
        })
        code, _ = self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'PRODUCT by project',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'SNS', 'share': 100}],
            }],
        })
        self.assertEqual(code, 201)
        self._force_stored_mode(vt['id'], 'ASSIGNMENT')
        code, resp = self.cloud_resource_create_bulk(
            self.ca_a['id'],
            {'resources': [{
                'cloud_resource_id': 'i-bulk-vt-1',
                'name': 'inst',
                'resource_type': 'Instance',
            }]},
            behavior='skip_existing',
            return_resources=True,
            is_report_import=True)
        self.assertEqual(code, 200, resp)
        resources = resp.get('resources') or []
        self.assertEqual(len(resources), 1)
        values = [row['value'] for row in resources[0].get('virtual_tags') or []]
        self.assertEqual(values, ['SNS'])

    def test_same_key_two_quarters(self):
        code, q3 = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product Q3',
            'mode': 'assignment',
            'quarter': '2026Q3',
        })
        self.assertEqual(code, 201, q3)
        code, q2 = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product Q2',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        self.assertEqual(code, 201, q2)
        code, listing = self.client.virtual_tag_list(
            self.org_id, quarter='2026Q3')
        self.assertEqual(code, 200, listing)
        self.assertEqual(len(listing['virtual_tags']), 1)
        self.assertEqual(listing['virtual_tags'][0]['id'], q3['id'])
        code, listing = self.client.virtual_tag_list(
            self.org_id, quarter='2026Q2')
        self.assertEqual(code, 200, listing)
        self.assertEqual(listing['virtual_tags'][0]['id'], q2['id'])

    def test_copy_wipes_target_quarter(self):
        _, source = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
            'quarter': '2026Q3',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': source['id'],
            'name': 'by project',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'SNS', 'share': 100}],
            }],
        })
        _, stale = self.client.virtual_tag_create(self.org_id, {
            'key': 'OLD',
            'name': 'Old',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        code, resp = self.client.virtual_tag_copy(self.org_id, {
            'source_quarter': '2026Q3',
            'target_quarter': '2026Q2',
        })
        self.assertEqual(code, 200, resp)
        self.assertEqual(resp['copied'], 1)
        code, listing = self.client.virtual_tag_list(
            self.org_id, quarter='2026Q2')
        self.assertEqual(code, 200, listing)
        keys = [item['key'] for item in listing['virtual_tags']]
        self.assertEqual(keys, ['PRODUCT'])
        self.assertNotEqual(listing['virtual_tags'][0]['id'], source['id'])
        code, gone = self.client.virtual_tag_get(stale['id'])
        self.assertEqual(code, 404, gone)
        code, still = self.client.virtual_tag_get(source['id'])
        self.assertEqual(code, 200, still)

    def test_invoice_months_use_quarter_virtual_tags(self):
        _, q2 = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product Q2',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': q2['id'],
            'name': 'q2',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'JUNE', 'share': 100}],
            }],
        })
        _, q3 = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product Q3',
            'mode': 'assignment',
            'quarter': '2026Q3',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': q3['id'],
            'name': 'q3',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'JULY', 'share': 100}],
            }],
        })
        june = datetime(2026, 6, 15, tzinfo=timezone.utc)
        july = datetime(2026, 7, 15, tzinfo=timezone.utc)
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-quarters',
            'name': 'inst',
            'resource_type': 'Instance',
            'pool_id': self.org['pool_id'],
            'employee_id': self.employee['id'],
            'first_seen': int(june.timestamp()),
            'last_seen': int(july.timestamp()),
        })
        self.assertEqual(code, 201, resource)
        self.resources_collection.update_one(
            {'_id': resource['id']},
            {'$set': {
                'virtual_tags_by_quarter': {
                    '2026Q2': [{'key': 'PRODUCT', 'value': 'JUNE', 'share': 100}],
                    '2026Q3': [{'key': 'PRODUCT', 'value': 'JULY', 'share': 100}],
                }
            }})
        self.expenses.append({
            'cloud_account_id': self.ca_a['id'],
            'resource_id': resource['id'],
            'date': june,
            'cost': 40,
            'sign': 1,
            'invoice_month': '202606',
        })
        self.expenses.append({
            'cloud_account_id': self.ca_a['id'],
            'resource_id': resource['id'],
            'date': july,
            'cost': 60,
            'sign': 1,
            'invoice_month': '202607',
        })
        code, june_bd = self.client.breakdown_expenses_get(
            self.org_id, breakdown_by='virtual_tag:PRODUCT',
            params={'invoice_months': ['202606']})
        self.assertEqual(code, 200, june_bd)
        self.assertAlmostEqual(
            june_bd.get('counts', {}).get('JUNE', {}).get('total', 0),
            40, places=4)
        self.assertNotIn('JULY', june_bd.get('counts', {}))
        code, july_bd = self.client.breakdown_expenses_get(
            self.org_id, breakdown_by='virtual_tag:PRODUCT',
            params={'invoice_months': ['202607']})
        self.assertEqual(code, 200, july_bd)
        self.assertAlmostEqual(
            july_bd.get('counts', {}).get('JULY', {}).get('total', 0),
            60, places=4)

    def test_reapply_one_quarter_keeps_the_other(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
            'quarter': '2026Q3',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'q3',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'Q3VAL', 'share': 100}],
            }],
        })
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-keep-q2',
            'name': 'inst',
            'resource_type': 'Instance',
        })
        self.assertEqual(code, 201, resource)
        self.resources_collection.update_one(
            {'_id': resource['id']},
            {'$set': {
                'virtual_tags_by_quarter.2026Q2': [
                    {'key': 'PRODUCT', 'value': 'Q2VAL', 'share': 100}],
            }})
        code, resp = self.client.virtual_tag_rules_apply(
            self.org_id, {'quarter': '2026Q3'})
        self.assertEqual(code, 200, resp)
        stored = self.resources_collection.find_one({'_id': resource['id']})
        q2 = (stored.get('virtual_tags_by_quarter') or {}).get('2026Q2')
        q3 = (stored.get('virtual_tags_by_quarter') or {}).get('2026Q3')
        self.assertEqual(q2[0]['value'], 'Q2VAL')
        self.assertEqual(q3[0]['value'], 'Q3VAL')

    def test_reapply_q2_does_not_clobber_q3(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'q2',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'Q2VAL', 'share': 100}],
            }],
        })
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-keep-q3',
            'name': 'inst',
            'resource_type': 'Instance',
        })
        self.assertEqual(code, 201, resource)
        self.resources_collection.update_one(
            {'_id': resource['id']},
            {'$set': {
                'virtual_tags_by_quarter.2026Q3': [
                    {'key': 'PRODUCT', 'value': 'Q3VAL', 'share': 100}],
            }})
        code, resp = self.client.virtual_tag_rules_apply(
            self.org_id, {'quarter': '2026Q2'})
        self.assertEqual(code, 200, resp)
        stored = self.resources_collection.find_one({'_id': resource['id']})
        q2 = (stored.get('virtual_tags_by_quarter') or {}).get('2026Q2')
        q3 = (stored.get('virtual_tags_by_quarter') or {}).get('2026Q3')
        self.assertEqual(q2[0]['value'], 'Q2VAL')
        self.assertEqual(q3[0]['value'], 'Q3VAL')

    @patch('rest_api.rest_api_server.controllers.virtual_tag_apply.LOG')
    def test_reapply_emits_start_and_finish_progress_logs(self, mock_log):
        self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-progress',
            'name': 'inst',
            'resource_type': 'Instance',
        })
        code, resp = self.client.virtual_tag_rules_apply(
            self.org_id, {'quarter': '2026Q2'})
        self.assertEqual(code, 200, resp)
        messages = [
            call.args[0] % call.args[1:]
            for call in mock_log.info.call_args_list
            if call.args
        ]
        started = [m for m in messages if m.startswith(
            'Virtual tag apply started for org ')]
        finished = [m for m in messages if m.startswith(
            'Virtual tag apply finished for org ')]
        self.assertTrue(started, messages)
        self.assertIn('quarter 2026Q2', started[0])
        self.assertIn('total=', started[0])
        self.assertTrue(finished, messages)
        self.assertIn('processed=', finished[0])

    def test_apply_progress_idle_then_finished(self):
        code, idle = self.client.virtual_tag_rules_apply_status(
            self.org_id, '2026Q2')
        self.assertEqual(code, 200, idle)
        self.assertEqual(idle['state'], 'idle')
        self.assertEqual(idle['pct'], 0)
        self.assertEqual(idle['quarter'], '2026Q2')
        self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-progress-status',
            'name': 'inst',
            'resource_type': 'Instance',
        })
        code, apply_resp = self.client.virtual_tag_rules_apply(
            self.org_id, {'quarter': '2026Q2'})
        self.assertEqual(code, 200, apply_resp)
        code, status = self.client.virtual_tag_rules_apply_status(
            self.org_id, '2026Q2')
        self.assertEqual(code, 200, status)
        self.assertEqual(status['state'], 'finished')
        self.assertEqual(
            status['processed'], apply_resp['processed_resources'])
        self.assertEqual(status['pct'], 100)

    @patch('rest_api.rest_api_server.controllers.virtual_tag_apply.CHUNK_SIZE',
           1)
    def test_reapply_pages_all_resources(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'all',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'PAGED', 'share': 100}],
            }],
        })
        resource_ids = []
        for i in range(3):
            code, resource = self.cloud_resource_create(self.ca_a['id'], {
                'cloud_resource_id': 'i-vt-page-%s' % i,
                'name': 'inst',
                'resource_type': 'Instance',
            })
            self.assertEqual(code, 201, resource)
            resource_ids.append(resource['id'])
        code, resp = self.client.virtual_tag_rules_apply(
            self.org_id, {'quarter': '2026Q2'})
        self.assertEqual(code, 200, resp)
        self.assertEqual(resp['processed_resources'], 3)
        for resource_id in resource_ids:
            stored = self.resources_collection.find_one({'_id': resource_id})
            q2 = (stored.get('virtual_tags_by_quarter') or {}).get('2026Q2')
            self.assertEqual(q2[0]['value'], 'PAGED')

    def test_reapply_retries_cursor_not_found(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'all',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'RETRY', 'share': 100}],
            }],
        })
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-cursor-retry',
            'name': 'inst',
            'resource_type': 'Instance',
        })
        self.assertEqual(code, 201, resource)
        real_fetch = VirtualTagApplyController._fetch_resource_page
        raised = {'done': False}

        def flaky_fetch(ctrl, resource_filter, last_id):
            if not raised['done']:
                raised['done'] = True
                raise CursorNotFound('cursor id not found')
            return real_fetch(ctrl, resource_filter, last_id)

        with patch.object(VirtualTagApplyController, '_fetch_resource_page',
                          flaky_fetch):
            code, resp = self.client.virtual_tag_rules_apply(
                self.org_id, {'quarter': '2026Q2'})
        self.assertEqual(code, 200, resp)
        stored = self.resources_collection.find_one({'_id': resource['id']})
        q2 = (stored.get('virtual_tags_by_quarter') or {}).get('2026Q2')
        self.assertEqual(q2[0]['value'], 'RETRY')

    def test_resource_options_search(self):
        self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'wh-search-1',
            'name': 'ANALYTICS_WH',
            'resource_type': 'Instance',
        })
        code, resp = self.client.virtual_tag_resource_options(
            self.org_id, search='ANALYTICS',
            cloud_account_id=self.ca_a['id'])
        self.assertEqual(code, 200, resp)
        ids = [row['cloud_resource_id'] for row in resp.get('resources') or []]
        self.assertIn('wh-search-1', ids)

    def test_copy_same_quarter_rejected(self):
        self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
            'quarter': '2026Q3',
        })
        code, resp = self.client.virtual_tag_copy(self.org_id, {
            'source_quarter': '2026Q3',
            'target_quarter': '2026Q3',
        })
        self.assertEqual(code, 400, resp)

    def test_list_filters_name_value_cloud(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product catalog',
            'mode': 'assignment',
            'quarter': '2026Q3',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'by project',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'SNS', 'share': 100}],
            }],
        })
        self.client.virtual_tag_create(self.org_id, {
            'key': 'OTHER',
            'name': 'Other',
            'mode': 'assignment',
            'quarter': '2026Q3',
        })
        code, listing = self.client.virtual_tag_list(
            self.org_id, quarter='2026Q3', name='catalog',
            value='SNS', cloud_account_id=self.ca_a['id'])
        self.assertEqual(code, 200, listing)
        keys = [item['key'] for item in listing['virtual_tags']]
        self.assertEqual(keys, ['PRODUCT'])
        self.assertNotIn('allocation_values', listing['virtual_tags'][0])

    def test_allocation_values_include_other_quarters(self):
        _, q3 = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product Q3',
            'mode': 'assignment',
            'quarter': '2026Q3',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': q3['id'],
            'name': 'q3',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'JULY', 'share': 100}],
            }],
        })
        _, q2 = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product Q2',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': q2['id'],
            'name': 'q2',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'JUNE', 'share': 100}],
            }],
        })
        code, item = self.client.virtual_tag_get(q3['id'])
        self.assertEqual(code, 200, item)
        self.assertEqual(sorted(item.get('allocation_values') or []),
                         ['JULY', 'JUNE'])

    def test_value_stats_are_integers(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
            'quarter': '2026Q3',
        })
        code, item = self.client.virtual_tag_get(vt['id'])
        self.assertEqual(code, 200, item)
        for row in item.get('stats') or []:
            self.assertIsInstance(row['cost'], int)
            self.assertIsInstance(row['forecast'], int)

    def test_available_filters_split_by_invoice_month(self):
        june = datetime(2026, 6, 15, tzinfo=timezone.utc)
        july = datetime(2026, 7, 15, tzinfo=timezone.utc)
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-filters',
            'name': 'inst',
            'resource_type': 'Instance',
            'first_seen': int(june.timestamp()),
            'last_seen': int(july.timestamp()),
        })
        self.assertEqual(code, 201, resource)
        self.resources_collection.update_one(
            {'_id': resource['id']},
            {'$set': {
                '_first_seen_dt': june,
                '_last_seen_dt': july,
                'virtual_tags_by_quarter': {
                    '2026Q2': [{'key': 'PRODUCT', 'value': 'JUNE',
                                'share': 100}],
                    '2026Q3': [{'key': 'PRODUCT', 'value': 'JULY',
                                'share': 100}],
                },
            }})
        self.expenses.append({
            'cloud_account_id': self.ca_a['id'],
            'resource_id': resource['id'],
            'date': june,
            'cost': 40,
            'sign': 1,
            'invoice_month': '202606',
        })
        self.expenses.append({
            'cloud_account_id': self.ca_a['id'],
            'resource_id': resource['id'],
            'date': july,
            'cost': 60,
            'sign': 1,
            'invoice_month': '202607',
        })
        code, june = self.client.available_filters_get(
            self.org_id,
            params={'invoice_months': ['202606'], 'facets': 'virtual_tag'})
        self.assertEqual(code, 200, june)
        june_pairs = [
            (row.get('key'), row.get('value'))
            for row in (june.get('filter_values') or {}).get('virtual_tag') or []
            if row.get('key')
        ]
        self.assertIn(('PRODUCT', 'JUNE'), june_pairs)
        self.assertNotIn(('PRODUCT', 'JULY'), june_pairs)
        code, july = self.client.available_filters_get(
            self.org_id,
            params={'invoice_months': ['202607'], 'facets': 'virtual_tag'})
        self.assertEqual(code, 200, july)
        july_pairs = [
            (row.get('key'), row.get('value'))
            for row in (july.get('filter_values') or {}).get('virtual_tag') or []
            if row.get('key')
        ]
        self.assertIn(('PRODUCT', 'JULY'), july_pairs)
        self.assertNotIn(('PRODUCT', 'JUNE'), july_pairs)

    def test_organization_constraint_virtual_tag_filter(self):
        now = opttime.utcnow_timestamp()
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-constraint',
            'name': 'inst',
            'resource_type': 'Instance',
        })
        self.assertEqual(code, 201, resource)
        self.resources_collection.update_one(
            {'_id': resource['id']},
            {'$set': {
                'first_seen': now,
                'last_seen': now,
                '_first_seen_dt': datetime.fromtimestamp(now, timezone.utc),
                '_last_seen_dt': datetime.fromtimestamp(now, timezone.utc),
                'active': True,
                'virtual_tags': [
                    {'key': 'PRODUCT', 'value': 'SNS', 'share': 100}],
                'virtual_tags_by_quarter': {
                    '2026Q3': [{'key': 'PRODUCT', 'value': 'SNS',
                                'share': 100}],
                },
            }})
        params = {
            'name': 'vt constraint',
            'type': 'resource_count_anomaly',
            'definition': {
                'threshold_days': 7,
                'threshold': 30,
            },
            'filters': {
                'virtual_tag': ['PRODUCT:SNS'],
            },
        }
        code, resp = self.client.organization_constraint_create(
            self.org_id, params)
        self.assertEqual(code, 201, resp)
        self.assertEqual(
            resp.get('filters', {}).get('virtual_tag'), ['PRODUCT:SNS'])

    def test_identical_q2_q3_match_month_agnostic_baseline(self):
        _, q3 = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
            'quarter': '2026Q3',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': q3['id'],
            'name': 'by project',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'SNS', 'share': 100}],
            }],
        })
        june = datetime(2026, 6, 15, tzinfo=timezone.utc)
        july = datetime(2026, 7, 15, tzinfo=timezone.utc)
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-backfill',
            'name': 'inst',
            'resource_type': 'Instance',
            'pool_id': self.org['pool_id'],
            'employee_id': self.employee['id'],
            'first_seen': int(june.timestamp()),
            'last_seen': int(july.timestamp()),
        })
        self.assertEqual(code, 201, resource)
        code, apply_resp = self.client.virtual_tag_rules_apply(
            self.org_id, {'quarter': '2026Q3'})
        self.assertEqual(code, 200, apply_resp)
        self.expenses.append({
            'cloud_account_id': self.ca_a['id'],
            'resource_id': resource['id'],
            'date': june,
            'cost': 40,
            'sign': 1,
            'invoice_month': '202606',
        })
        self.expenses.append({
            'cloud_account_id': self.ca_a['id'],
            'resource_id': resource['id'],
            'date': july,
            'cost': 60,
            'sign': 1,
            'invoice_month': '202607',
        })
        code, june_before = self.client.breakdown_expenses_get(
            self.org_id, breakdown_by='virtual_tag:PRODUCT',
            params={'invoice_months': ['202606']})
        self.assertEqual(code, 200, june_before)
        code, july_before = self.client.breakdown_expenses_get(
            self.org_id, breakdown_by='virtual_tag:PRODUCT',
            params={'invoice_months': ['202607']})
        self.assertEqual(code, 200, july_before)
        self.assertAlmostEqual(
            june_before.get('counts', {}).get('SNS', {}).get('total', 0),
            40, places=4)
        self.assertAlmostEqual(
            july_before.get('counts', {}).get('SNS', {}).get('total', 0),
            60, places=4)
        code, copy_resp = self.client.virtual_tag_copy(self.org_id, {
            'source_quarter': '2026Q3',
            'target_quarter': '2026Q2',
        })
        self.assertEqual(code, 200, copy_resp)
        code, june_after = self.client.breakdown_expenses_get(
            self.org_id, breakdown_by='virtual_tag:PRODUCT',
            params={'invoice_months': ['202606']})
        self.assertEqual(code, 200, june_after)
        code, july_after = self.client.breakdown_expenses_get(
            self.org_id, breakdown_by='virtual_tag:PRODUCT',
            params={'invoice_months': ['202607']})
        self.assertEqual(code, 200, july_after)
        self.assertEqual(
            june_after.get('counts', {}).get('SNS'),
            june_before.get('counts', {}).get('SNS'))
        self.assertEqual(
            july_after.get('counts', {}).get('SNS'),
            july_before.get('counts', {}).get('SNS'))

    def test_name_is_rule_stores_cloud_resource_id(self):
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'wh-vt-analytics-1',
            'name': 'ANALYTICS_WH',
            'resource_type': 'Instance',
        })
        self.assertEqual(code, 201, resource)
        code, options = self.client.virtual_tag_resource_options(
            self.org_id, search='ANALYTICS',
            cloud_account_id=self.ca_a['id'])
        self.assertEqual(code, 200, options)
        match = next(
            row for row in options.get('resources') or []
            if row.get('name') == 'ANALYTICS_WH')
        self.assertEqual(match['cloud_resource_id'], 'wh-vt-analytics-1')
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
            'quarter': '2026Q3',
        })
        code, rule = self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'by warehouse id',
            'branches': [{
                'conditions': [
                    {
                        'type': 'cloud_is',
                        'meta_info': self.ca_a['id'],
                    },
                    {
                        'type': 'name_is',
                        'meta_info': match['cloud_resource_id'],
                    },
                ],
                'allocations': [{'value': 'SNS', 'share': 100}],
            }],
        })
        self.assertEqual(code, 201, rule)
        stored_meta = rule['branches'][0]['conditions']
        name_is = [row for row in stored_meta if row['type'] == 'name_is']
        self.assertEqual(name_is[0]['meta_info'], 'wh-vt-analytics-1')
        code, apply_resp = self.client.virtual_tag_rules_apply(
            self.org_id, {'quarter': '2026Q3'})
        self.assertEqual(code, 200, apply_resp)
        stored = self.resources_collection.find_one({'_id': resource['id']})
        q3 = (stored.get('virtual_tags_by_quarter') or {}).get('2026Q3') or []
        self.assertEqual(
            [row['value'] for row in q3 if row.get('key') == 'PRODUCT'],
            ['SNS'])
        code, fetched = self.client.virtual_tag_rule_get(rule['id'])
        self.assertEqual(code, 200, fetched)
        fetched_name_is = [
            row for row in fetched['branches'][0]['conditions']
            if row['type'] == 'name_is']
        self.assertEqual(fetched_name_is[0]['meta_info'], 'wh-vt-analytics-1')

    def test_backfill_legacy_virtual_tags_by_quarter(self):
        from rest_api.rest_api_server.controllers.virtual_tag_apply import (
            backfill_legacy_virtual_tags_by_quarter)
        tags = [{'key': 'PRODUCT', 'value': 'SNS', 'share': 100}]
        code, missing = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-backfill-missing',
            'name': 'inst',
            'resource_type': 'Instance',
        })
        self.assertEqual(code, 201, missing)
        self.resources_collection.update_one(
            {'_id': missing['id']},
            {'$set': {
                'virtual_tags': tags,
                'virtual_tags_by_quarter': {},
            }})
        code, keep = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-backfill-keep',
            'name': 'inst',
            'resource_type': 'Instance',
        })
        self.assertEqual(code, 201, keep)
        self.resources_collection.update_one(
            {'_id': keep['id']},
            {'$set': {
                'virtual_tags': tags,
                'virtual_tags_by_quarter': {
                    '2026Q3': [{'key': 'PRODUCT', 'value': 'KEEP',
                                'share': 100}],
                },
            }})
        updated = backfill_legacy_virtual_tags_by_quarter(
            self.resources_collection)
        self.assertGreaterEqual(updated, 1)
        missing_doc = self.resources_collection.find_one(
            {'_id': missing['id']})
        missing_map = missing_doc.get('virtual_tags_by_quarter') or {}
        self.assertEqual(missing_map.get('2026Q3'), tags)
        self.assertEqual(missing_map.get('2026Q2'), tags)
        keep_doc = self.resources_collection.find_one({'_id': keep['id']})
        keep_map = keep_doc.get('virtual_tags_by_quarter') or {}
        self.assertEqual(keep_map['2026Q3'][0]['value'], 'KEEP')
        self.assertEqual(keep_map.get('2026Q2'), tags)

    def test_report_import_writes_invoice_month_quarter_only(self):
        _, q2 = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product Q2',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': q2['id'],
            'name': 'q2',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'JUNE', 'share': 100}],
            }],
        })
        _, q3 = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product Q3',
            'mode': 'assignment',
            'quarter': '2026Q3',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': q3['id'],
            'name': 'q3',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'JULY', 'share': 100}],
            }],
        })
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-import-quarter',
            'name': 'inst',
            'resource_type': 'Instance',
        })
        self.assertEqual(code, 201, resource)
        self.resources_collection.update_one(
            {'_id': resource['id']},
            {'$set': {
                'virtual_tags_by_quarter': {
                    '2026Q3': [{'key': 'PRODUCT', 'value': 'JULY',
                                'share': 100}],
                },
                'virtual_tags': [
                    {'key': 'PRODUCT', 'value': 'JULY', 'share': 100}],
            }})
        code, resp = self.cloud_resource_create_bulk(
            self.ca_a['id'],
            {'resources': [{
                'cloud_resource_id': 'i-vt-import-quarter',
                'name': 'inst',
                'resource_type': 'Instance',
                'invoice_months': ['202606'],
            }]},
            behavior='skip_existing',
            return_resources=True,
            is_report_import=True)
        self.assertEqual(code, 200, resp)
        stored = self.resources_collection.find_one({'_id': resource['id']})
        by_q = stored.get('virtual_tags_by_quarter') or {}
        self.assertEqual(by_q['2026Q2'][0]['value'], 'JUNE')
        self.assertEqual(by_q['2026Q3'][0]['value'], 'JULY')
        self.assertEqual(stored.get('virtual_tags')[0]['value'], 'JULY')

    def test_overlapping_rules_same_tag_are_flagged(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        code, winner = self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'by cloud',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'ALPHA', 'share': 100}],
            }],
        })
        self.assertEqual(code, 201, winner)
        code, loser = self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'by name',
            'branches': [{
                'conditions': [{
                    'type': 'name_is',
                    'meta_info': 'i-vt-overlap',
                }],
                'allocations': [{'value': 'BETA', 'share': 100}],
            }],
        })
        self.assertEqual(code, 201, loser)
        self.assertTrue(winner.get('needs_apply'))
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-overlap',
            'name': 'overlap-box',
            'resource_type': 'Instance',
        })
        self.assertEqual(code, 201, resource)
        code, resp = self.client.virtual_tag_rules_apply(
            self.org_id, {'quarter': '2026Q2'})
        self.assertEqual(code, 200, resp)
        stored = self.resources_collection.find_one({'_id': resource['id']})
        q2 = (stored.get('virtual_tags_by_quarter') or {}).get('2026Q2') or []
        self.assertEqual(
            [row['value'] for row in q2 if row.get('key') == 'PRODUCT'],
            ['ALPHA'])
        overlap = resp.get('overlap_rule_ids') or {}
        self.assertCountEqual(
            overlap.get(winner['id']) or overlap.get(str(winner['id'])) or [],
            [loser['id']])
        self.assertCountEqual(
            overlap.get(loser['id']) or overlap.get(str(loser['id'])) or [],
            [winner['id']])
        code, listing = self.client.virtual_tag_rule_list(
            self.org_id, vt['id'])
        self.assertEqual(code, 200, listing)
        by_id = {
            row['id']: row for row in listing['virtual_tag_rules']}
        self.assertCountEqual(
            by_id[winner['id']]['overlap_rule_ids'], [loser['id']])
        self.assertCountEqual(
            by_id[loser['id']]['overlap_rule_ids'], [winner['id']])
        self.assertFalse(by_id[winner['id']]['needs_apply'])

    def test_same_value_overlap_is_not_flagged(self):
        _, vt = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'by cloud',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'SAME', 'share': 100}],
            }],
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': vt['id'],
            'name': 'by name',
            'branches': [{
                'conditions': [{
                    'type': 'name_is',
                    'meta_info': 'i-vt-same-value',
                }],
                'allocations': [{'value': 'SAME', 'share': 100}],
            }],
        })
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-same-value',
            'name': 'same-box',
            'resource_type': 'Instance',
        })
        self.assertEqual(code, 201, resource)
        code, resp = self.client.virtual_tag_rules_apply(
            self.org_id, {'quarter': '2026Q2'})
        self.assertEqual(code, 200, resp)
        self.assertEqual(resp.get('overlap_rule_ids') or {}, {})
        code, listing = self.client.virtual_tag_rule_list(
            self.org_id, vt['id'])
        for rule in listing['virtual_tag_rules']:
            self.assertEqual(rule.get('overlap_rule_ids') or [], [])

    def test_different_tags_on_same_resource_are_not_overlaps(self):
        _, product = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        _, team = self.client.virtual_tag_create(self.org_id, {
            'key': 'TEAM',
            'name': 'Team',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': product['id'],
            'name': 'product cloud',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'ALPHA', 'share': 100}],
            }],
        })
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': team['id'],
            'name': 'team cloud',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'ENG', 'share': 100}],
            }],
        })
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-two-tags',
            'name': 'two-tags',
            'resource_type': 'Instance',
        })
        self.assertEqual(code, 201, resource)
        code, resp = self.client.virtual_tag_rules_apply(
            self.org_id, {'quarter': '2026Q2'})
        self.assertEqual(code, 200, resp)
        self.assertEqual(resp.get('overlap_rule_ids') or {}, {})
        stored = self.resources_collection.find_one({'_id': resource['id']})
        q2 = (stored.get('virtual_tags_by_quarter') or {}).get('2026Q2') or []
        values = {
            row['key']: row['value'] for row in q2
            if row.get('key') in ('PRODUCT', 'TEAM')}
        self.assertEqual(values, {'PRODUCT': 'ALPHA', 'TEAM': 'ENG'})
        for vt_id in (product['id'], team['id']):
            code, listing = self.client.virtual_tag_rule_list(
                self.org_id, vt_id)
            self.assertEqual(code, 200, listing)
            for rule in listing['virtual_tag_rules']:
                self.assertEqual(rule.get('overlap_rule_ids') or [], [])

    def test_changed_only_reapply_skips_unchanged_rules(self):
        _, product = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        _, team = self.client.virtual_tag_create(self.org_id, {
            'key': 'TEAM',
            'name': 'Team',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        code, product_rule = self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': product['id'],
            'name': 'product a',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'OLD', 'share': 100}],
            }],
        })
        self.assertEqual(code, 201, product_rule)
        self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': team['id'],
            'name': 'team b',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_b['id'],
                }],
                'allocations': [{'value': 'ENG', 'share': 100}],
            }],
        })
        code, resource_a = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-changed-a',
            'name': 'inst-a',
            'resource_type': 'Instance',
        })
        self.assertEqual(code, 201, resource_a)
        code, resource_b = self.cloud_resource_create(self.ca_b['id'], {
            'cloud_resource_id': 'i-vt-changed-b',
            'name': 'inst-b',
            'resource_type': 'Instance',
        })
        self.assertEqual(code, 201, resource_b)
        code, first = self.client.virtual_tag_rules_apply(
            self.org_id, {'quarter': '2026Q2'})
        self.assertEqual(code, 200, first)
        self.assertGreaterEqual(first['processed_resources'], 2)
        code, noop = self.client.virtual_tag_rules_apply(
            self.org_id, {'quarter': '2026Q2', 'changed_only': True})
        self.assertEqual(code, 200, noop)
        self.assertEqual(noop['processed_resources'], 0)
        self.assertTrue(noop.get('changed_only'))
        code, updated = self.client.virtual_tag_rule_update(product_rule['id'], {
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'NEW', 'share': 100}],
            }],
        })
        self.assertEqual(code, 200, updated)
        self.assertTrue(updated.get('needs_apply'))
        code, resp = self.client.virtual_tag_rules_apply(
            self.org_id, {'quarter': '2026Q2', 'changed_only': True})
        self.assertEqual(code, 200, resp)
        self.assertEqual(resp['processed_resources'], 1)
        stored_a = self.resources_collection.find_one(
            {'_id': resource_a['id']})
        stored_b = self.resources_collection.find_one(
            {'_id': resource_b['id']})
        q2_a = (stored_a.get('virtual_tags_by_quarter') or {}).get(
            '2026Q2') or []
        q2_b = (stored_b.get('virtual_tags_by_quarter') or {}).get(
            '2026Q2') or []
        self.assertEqual(
            [row['value'] for row in q2_a if row.get('key') == 'PRODUCT'],
            ['NEW'])
        self.assertEqual(
            [row['value'] for row in q2_b if row.get('key') == 'TEAM'],
            ['ENG'])

    def test_single_virtual_tag_reapply_keeps_siblings(self):
        _, product = self.client.virtual_tag_create(self.org_id, {
            'key': 'PRODUCT',
            'name': 'Product',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        _, team = self.client.virtual_tag_create(self.org_id, {
            'key': 'TEAM',
            'name': 'Team',
            'mode': 'assignment',
            'quarter': '2026Q2',
        })
        code, product_rule = self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': product['id'],
            'name': 'product a',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'OLD', 'share': 100}],
            }],
        })
        self.assertEqual(code, 201, product_rule)
        code, team_rule = self.client.virtual_tag_rule_create(self.org_id, {
            'virtual_tag_id': team['id'],
            'name': 'team a',
            'branches': [{
                'conditions': [{
                    'type': 'cloud_is',
                    'meta_info': self.ca_a['id'],
                }],
                'allocations': [{'value': 'ENG', 'share': 100}],
            }],
        })
        self.assertEqual(code, 201, team_rule)
        code, resource = self.cloud_resource_create(self.ca_a['id'], {
            'cloud_resource_id': 'i-vt-single-a',
            'name': 'inst-a',
            'resource_type': 'Instance',
        })
        self.assertEqual(code, 201, resource)
        code, first = self.client.virtual_tag_rules_apply(
            self.org_id, {'quarter': '2026Q2'})
        self.assertEqual(code, 200, first)
        self.assertGreaterEqual(first['processed_resources'], 1)
        code, updated_product = self.client.virtual_tag_rule_update(
            product_rule['id'], {
                'branches': [{
                    'conditions': [{
                        'type': 'cloud_is',
                        'meta_info': self.ca_a['id'],
                    }],
                    'allocations': [{'value': 'NEW', 'share': 100}],
                }],
            })
        self.assertEqual(code, 200, updated_product)
        code, updated_team = self.client.virtual_tag_rule_update(
            team_rule['id'], {
                'branches': [{
                    'conditions': [{
                        'type': 'cloud_is',
                        'meta_info': self.ca_a['id'],
                    }],
                    'allocations': [{'value': 'OPS', 'share': 100}],
                }],
            })
        self.assertEqual(code, 200, updated_team)
        code, resp = self.client.virtual_tag_rules_apply(
            self.org_id, {
                'quarter': '2026Q2',
                'changed_only': True,
                'virtual_tag_id': product['id'],
            })
        self.assertEqual(code, 200, resp)
        self.assertEqual(resp['virtual_tag_id'], product['id'])
        self.assertEqual(resp['processed_resources'], 1)
        stored = self.resources_collection.find_one({'_id': resource['id']})
        q2 = (stored.get('virtual_tags_by_quarter') or {}).get('2026Q2') or []
        self.assertEqual(
            [row['value'] for row in q2 if row.get('key') == 'PRODUCT'],
            ['NEW'])
        self.assertEqual(
            [row['value'] for row in q2 if row.get('key') == 'TEAM'],
            ['ENG'])
        code, full = self.client.virtual_tag_rules_apply(
            self.org_id, {
                'quarter': '2026Q2',
                'changed_only': False,
                'virtual_tag_id': team['id'],
            })
        self.assertEqual(code, 200, full)
        self.assertEqual(full['virtual_tag_id'], team['id'])
        stored = self.resources_collection.find_one({'_id': resource['id']})
        q2 = (stored.get('virtual_tags_by_quarter') or {}).get('2026Q2') or []
        self.assertEqual(
            [row['value'] for row in q2 if row.get('key') == 'PRODUCT'],
            ['NEW'])
        self.assertEqual(
            [row['value'] for row in q2 if row.get('key') == 'TEAM'],
            ['OPS'])
