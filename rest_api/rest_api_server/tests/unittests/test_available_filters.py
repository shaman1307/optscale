import tools.optscale_time as opttime
from datetime import datetime, timezone
from unittest.mock import patch

from rest_api.rest_api_server.models.db_base import BaseDB
from rest_api.rest_api_server.models.db_factory import DBFactory, DBType
from rest_api.rest_api_server.models.models import Employee, Pool
from rest_api.rest_api_server.tests.unittests.test_api_base import TestApiBase


class TestAvailableFiltersApi(TestApiBase):
    # common negative cases. Positive tests can be found in expenses
    # tests cases

    def setUp(self, version='v2'):
        super().setUp(version)
        _, self.org = self.client.organization_create(
            {'name': "organization"})
        self.org_id = self.org['id']
        patch('rest_api.rest_api_server.controllers.cloud_account.'
              'CloudAccountController._configure_report').start()
        cloud_acc1 = {
            'name': 'aws1',
            'type': 'aws_cnr',
            'config': {
                'access_key_id': 'key',
                'secret_access_key': 'secret',
                'config_scheme': 'create_report'
            }
        }

        self.auth_user_id_1 = self.gen_id()
        self.auth_user_id_2 = self.gen_id()
        self.auth_user_id_3 = self.gen_id()
        _, self.employee1 = self.client.employee_create(
            self.org_id, {'name': 'name1', 'auth_user_id': self.auth_user_id_1})
        _, self.cloud_acc1 = self.create_cloud_account(
            self.org_id, cloud_acc1, auth_user_id=self.auth_user_id_1)
        self.start_ts = int(datetime(2020, 4, 1, 0, 0).timestamp())
        self.end_ts = int(datetime(2020, 4, 2, 23, 59).timestamp())

    @staticmethod
    def get_all_org_employees(organization_id):
        db = DBFactory(DBType.Test, None).db
        engine = db.engine
        session = BaseDB.session(engine)()
        employees = session.query(Employee).filter(
            Employee.organization_id == organization_id,
            Employee.deleted_at == 0).all()
        return list(employees)

    @staticmethod
    def get_all_org_pools(organization_id):
        db = DBFactory(DBType.Test, None).db
        engine = db.engine
        session = BaseDB.session(engine)()
        pools = session.query(Pool).filter(
            Pool.organization_id == organization_id,
            Pool.deleted_at == 0).all()
        return list(pools)

    def test_available_filters_value(self):
        resource = {
            'cloud_resource_id': self.gen_id(),
            'name': 'name',
            'resource_type': 'Instance',
            'employee_id': self.employee1['id'],
            'pool_id': self.org['pool_id'],
            'last_seen': self.start_ts,
            'first_seen': self.end_ts,
            'region': 'eu-central-1',
            'meta': {},
        }
        _, res = self.cloud_resource_create(self.cloud_acc1['id'], resource)
        self.resources_collection.update_one(
            filter={'_id': res['id']},
            update={'$set': {
                '_first_seen_dt': datetime.fromtimestamp(self.start_ts),
                '_last_seen_dt': datetime.fromtimestamp(self.end_ts),
                'constraint_violated': True,
                'active': True}})
        filters = {
            'cloud_account_id': self.cloud_acc1['id'],
        }
        max_timestamp = int(datetime.max.replace(
            tzinfo=timezone.utc).timestamp()) - 1
        code, response = self.client.available_filters_get(
            self.org_id, 0, max_timestamp, filters)
        self.assertEqual(code, 200)
        self.assertEqual(response['filter_values']['active'], [True])

        filters = {
            'cloud_account_id': self.cloud_acc1['id'],
            'constraint_violated': [False],
        }
        code, response = self.client.available_filters_get(
            self.org_id, 0, max_timestamp, filters)
        self.assertEqual(code, 200)
        self.assertFalse('active' in response['filter_values'])

        filters = {
            'cloud_account_id': self.cloud_acc1['id'],
            'constraint_violated': [False, True],
        }
        code, response = self.client.available_filters_get(
            self.org_id, 0, max_timestamp, filters)
        self.assertEqual(code, 200)
        self.assertEqual(response['filter_values']['active'], [True])

    def test_available_filters_unexpected_filters(self):
        self.end_ts = int(datetime(2020, 4, 2, 23, 59).timestamp())
        filters = {
            'not_a_region': 'us-east',
        }
        code, response = self.client.available_filters_get(
            self.org_id, self.start_ts, self.end_ts, filters)
        self.assertEqual(code, 400)
        self.assertEqual(response['error']['error_code'], 'OE0212')

    def test_available_filters_dates_values(self):
        filters = {
            'cloud_account_id': [self.cloud_acc1['id']],
        }
        min_timestamp = 0
        max_timestamp = int(datetime.max.replace(
            tzinfo=timezone.utc).timestamp()) - 1
        code, response = self.client.available_filters_get(
            self.org_id, min_timestamp, max_timestamp, filters)
        self.assertEqual(code, 200)
        code, response = self.client.available_filters_get(
            self.org_id, min_timestamp - 1, max_timestamp, filters)
        self.assertEqual(code, 400)
        self.assertEqual(response['error']['error_code'], 'OE0224')
        code, response = self.client.available_filters_get(
            self.org_id, min_timestamp, max_timestamp + 1, filters)
        self.assertEqual(code, 400)
        self.assertEqual(response['error']['error_code'], 'OE0224')

        code, response = self.client.available_filters_get(
            self.org_id, min_timestamp - 1, 0, filters)
        self.assertEqual(code, 400)
        self.assertEqual(response['error']['error_code'], 'OE0224')

    def test_available_filters_limit(self):
        time = opttime.utcnow_timestamp()
        code, response = self.client.available_filters_get(
            self.org_id, time, time + 1, {'limit': 1})
        self.assertEqual(code, 400)
        self.assertEqual(response['error']['error_code'], 'OE0212')

    def test_invalid_organization(self):
        day_in_month = datetime(2020, 1, 14, tzinfo=timezone.utc)
        time = int(day_in_month.timestamp())
        valid_aws_cloud_acc = {
            'name': 'my cloud_acc',
            'type': 'aws_cnr',
            'config': {
                'access_key_id': 'key',
                'secret_access_key': 'secret',
                'config_scheme': 'create_report'
            }
        }
        code, cloud_acc1 = self.create_cloud_account(
            self.org_id, valid_aws_cloud_acc)
        self.assertEqual(code, 201)
        _, organization2 = self.client.organization_create(
            {'name': "organization2"})
        _, employee2 = self.client.employee_create(
            organization2['id'],
            {'name': 'name2', 'auth_user_id': self.auth_user_id_1})
        code, cloud_acc2 = self.create_cloud_account(
            organization2['id'], valid_aws_cloud_acc)
        self.assertEqual(code, 201)
        filters = {
            'cloud_account_id': [cloud_acc1['id']]
        }
        code, response = self.client.available_filters_get(
            organization2['id'], time, time + 1, filters)
        self.assertEqual(code, 404)
        self.verify_error_code(response, 'OE0470')

    def test_available_filters_default(self):
        employees = self.get_all_org_employees(self.org_id)
        cloud_accounts = self.get_all_org_cloud_accounts(self.org_id)
        pools = self.get_all_org_pools(self.org_id)
        code, response = self.client.available_filters_get(
            self.org_id, 0, 1)
        self.assertEqual(code, 200)
        self.assertEqual(len(response['filter_values']['pool']), len(pools))
        self.assertEqual(len(response['filter_values']['cloud_account']),
                         len(cloud_accounts))
        self.assertEqual(len(response['filter_values']['owner']),
                         len(employees))

    def test_available_filters_default_values_if_filtered_by_entity(self):
        filters = {
            'pool_id': [self.org['pool_id']],
        }
        min_timestamp = 0
        max_timestamp = int(datetime.max.replace(
            tzinfo=timezone.utc).timestamp()) - 1
        code, response = self.client.available_filters_get(
            self.org_id, min_timestamp, max_timestamp, filters)
        self.assertEqual(code, 200)
        self.assertEqual(response['filter_values']['pool'], [
            {
                'id': self.org['pool_id'],
                'name': self.org['name'],
                'purpose': 'business_unit',
                'parent_id': None
            }
        ])

    def test_available_filters_no_cloud_account(self):
        _, org = self.client.organization_create(
            {'name': "organization1"})
        max_timestamp = int(datetime.max.replace(
            tzinfo=timezone.utc).timestamp()) - 1
        code, response = self.client.available_filters_get(
            org['id'], 0, max_timestamp, {})
        self.assertEqual(code, 200)
        self.assertEqual(response['filter_values'], {})

    def test_available_filters_facets_core_empty_tags(self):
        resource = {
            'cloud_resource_id': self.gen_id(),
            'name': 'name',
            'resource_type': 'Instance',
            'employee_id': self.employee1['id'],
            'pool_id': self.org['pool_id'],
            'last_seen': self.end_ts,
            'first_seen': self.start_ts,
            'region': 'eu-central-1',
            'service_name': 'AmazonEC2',
            'tags': {'env': 'prod'},
            'meta': {'flavor': 't2.micro'},
        }
        _, res = self.cloud_resource_create(self.cloud_acc1['id'], resource)
        self.resources_collection.update_one(
            filter={'_id': res['id']},
            update={'$set': {
                '_first_seen_dt': datetime.fromtimestamp(self.start_ts),
                '_last_seen_dt': datetime.fromtimestamp(self.end_ts),
            }})
        code, response = self.client.available_filters_get(
            self.org_id, self.start_ts, self.end_ts)
        self.assertEqual(code, 200)
        filter_values = response['filter_values']
        self.assertTrue(any(
            ca['id'] == self.cloud_acc1['id']
            for ca in filter_values['cloud_account']))
        self.assertTrue(any(
            pool['id'] == self.org['pool_id']
            for pool in filter_values['pool']))
        self.assertTrue(any(
            sn.get('name') == 'AmazonEC2'
            for sn in filter_values['service_name'] if isinstance(sn, dict)))
        self.assertEqual(filter_values.get('tag'), [])
        self.assertEqual(filter_values.get('without_tag'), [])
        self.assertEqual(filter_values.get('meta'), [])

    def test_available_filters_facets_tag(self):
        resource = {
            'cloud_resource_id': self.gen_id(),
            'name': 'name',
            'resource_type': 'Instance',
            'employee_id': self.employee1['id'],
            'pool_id': self.org['pool_id'],
            'last_seen': self.end_ts,
            'first_seen': self.start_ts,
            'region': 'eu-central-1',
            'tags': {'env': 'prod', 'team': 'finops'},
            'meta': {},
        }
        _, res = self.cloud_resource_create(self.cloud_acc1['id'], resource)
        self.resources_collection.update_one(
            filter={'_id': res['id']},
            update={'$set': {
                '_first_seen_dt': datetime.fromtimestamp(self.start_ts),
                '_last_seen_dt': datetime.fromtimestamp(self.end_ts),
            }})
        code, response = self.client.available_filters_get(
            self.org_id, self.start_ts, self.end_ts, {'facets': 'tag'})
        self.assertEqual(code, 200)
        filter_values = response['filter_values']
        self.assertCountEqual(filter_values['tag'], ['env', 'team'])
        self.assertCountEqual(filter_values['without_tag'], ['env', 'team'])
        self.assertFalse('cloud_account' in filter_values)

    def test_available_filters_facets_meta(self):
        resource = {
            'cloud_resource_id': self.gen_id(),
            'name': 'name',
            'resource_type': 'Instance',
            'employee_id': self.employee1['id'],
            'pool_id': self.org['pool_id'],
            'last_seen': self.end_ts,
            'first_seen': self.start_ts,
            'region': 'eu-central-1',
            'tags': {},
            'meta': {'flavor': 't2.micro', 'vpc_id': 'vpc-1'},
        }
        _, res = self.cloud_resource_create(self.cloud_acc1['id'], resource)
        self.resources_collection.update_one(
            filter={'_id': res['id']},
            update={'$set': {
                '_first_seen_dt': datetime.fromtimestamp(self.start_ts),
                '_last_seen_dt': datetime.fromtimestamp(self.end_ts),
            }})
        code, response = self.client.available_filters_get(
            self.org_id, self.start_ts, self.end_ts, {'facets': 'meta'})
        self.assertEqual(code, 200)
        meta_keys = response['filter_values']['meta']
        self.assertIn('flavor', meta_keys)
        self.assertIn('vpc_id', meta_keys)

    def test_available_filters_invalid_facets(self):
        code, response = self.client.available_filters_get(
            self.org_id, self.start_ts, self.end_ts, {'facets': 'nope'})
        self.assertEqual(code, 400)
        self.assertEqual(response['error']['error_code'], 'OE0212')

    def test_available_filters_snowflake_region(self):
        """Top-level region on snowflake resources feeds the region facet."""
        patch(
            'tools.cloud_adapter.clouds.snowflake.Snowflake.validate_credentials',
            return_value={
                'account_id': 'HW44440',
                'warnings': [],
                'region': 'AWS_US_EAST_1',
            }).start()
        code, sf_acc = self.client.cloud_account_create(self.org_id, {
            'name': 'snowflake filters',
            'type': 'snowflake',
            'config': {
                'account': 'PUBLICIS-PROD',
                'user': 'svc',
                'private_key': (
                    '-----BEGIN PRIVATE KEY-----\n'
                    'MIIEvQIBADANBgkq\n'
                    '-----END PRIVATE KEY-----'),
                'role': 'ACCOUNTADMIN',
                'warehouse': 'COMPUTE_WH',
                'billing_source': 'account_usage',
            },
        })
        self.assertEqual(code, 201)
        resource = {
            'cloud_resource_id': self.gen_id(),
            'name': 'WH',
            'resource_type': 'COMPUTE',
            'employee_id': self.employee1['id'],
            'pool_id': self.org['pool_id'],
            'last_seen': self.end_ts,
            'first_seen': self.start_ts,
            'region': 'AWS_US_EAST_1',
            'meta': {},
        }
        _, res = self.cloud_resource_create(sf_acc['id'], resource)
        self.resources_collection.update_one(
            filter={'_id': res['id']},
            update={'$set': {
                '_first_seen_dt': datetime.fromtimestamp(self.start_ts),
                '_last_seen_dt': datetime.fromtimestamp(self.end_ts),
            }})
        code, response = self.client.available_filters_get(
            self.org_id, self.start_ts, self.end_ts, {
                'cloud_account_id': sf_acc['id'],
                'facets': 'core',
            })
        self.assertEqual(code, 200)
        regions = response['filter_values'].get('region') or []
        region_names = [
            r.get('name') if isinstance(r, dict) else r for r in regions]
        self.assertIn('AWS_US_EAST_1', region_names)
        cloud_types = {
            r.get('cloud_type') for r in regions if isinstance(r, dict)}
        self.assertIn('snowflake', cloud_types)

    def test_available_filters_virtual_tag_facet(self):
        resource = {
            'cloud_resource_id': self.gen_id(),
            'name': 'vt-name',
            'resource_type': 'Instance',
            'employee_id': self.employee1['id'],
            'pool_id': self.org['pool_id'],
            'last_seen': self.end_ts,
            'first_seen': self.start_ts,
            'region': 'eu-central-1',
            'meta': {},
        }
        _, res = self.cloud_resource_create(self.cloud_acc1['id'], resource)
        self.resources_collection.update_one(
            filter={'_id': res['id']},
            update={'$set': {
                '_first_seen_dt': datetime.fromtimestamp(self.start_ts),
                '_last_seen_dt': datetime.fromtimestamp(self.end_ts),
                'virtual_tags_by_quarter': {
                    '2026Q3': [{'key': 'PRODUCT', 'value': 'SNS',
                                'share': 100}],
                },
            }})
        code, response = self.client.available_filters_get(
            self.org_id, self.start_ts, self.end_ts, {
                'facets': 'virtual_tag',
            })
        self.assertEqual(code, 200, response)
        pairs = [
            (row.get('key'), row.get('value'))
            for row in (response.get('filter_values') or {}).get(
                'virtual_tag') or []
            if row.get('key')
        ]
        self.assertIn(('PRODUCT', 'SNS'), pairs)
