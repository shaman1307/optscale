import uuid
from unittest.mock import patch

from rest_api.rest_api_server.tests.unittests.test_api_base import TestApiBase
import tools.optscale_time as opttime


class TestResourceDuplicatesApi(TestApiBase):
    def setUp(self, version='v2'):
        super().setUp(version)
        _, self.org = self.client.organization_create({'name': 'dup_org'})
        self.org_id = self.org['id']
        self.user_id = self.gen_id()
        _, self.employee = self.client.employee_create(
            self.org_id, {'name': 'Dup Tester', 'auth_user_id': self.user_id})
        patch('rest_api.rest_api_server.controllers.cloud_account.'
              'CloudAccountController._configure_report').start()
        patch('tools.cloud_adapter.clouds.gcp_tenant.GcpTenant'
              '._test_bigquery_connection').start()

        tenant_body = {
            'name': 'gcp tenant',
            'type': 'gcp_tenant',
            'config': {
                'credentials': {
                    'project_id': 'hystax',
                    'type': 'service_account',
                    'private_key_id': 'redacted',
                    'private_key': 'redacted',
                    'client_id': 'test_client',
                },
                'billing_data': {
                    'dataset_name': 'billing_data',
                    'table_name': 'gcp_billing_export_v1',
                },
            }
        }
        code, self.tenant = self.create_cloud_account(
            self.org_id, tenant_body, auth_user_id=self.user_id)
        self.assertEqual(code, 201)

        patch('tools.cloud_adapter.clouds.gcp_tenant.GcpTenant'
              '.get_children_configs',
              return_value=[
                  {
                      'name': 'child 1',
                      'config': {'project_id': 'project_1'},
                      'type': 'gcp_cnr',
                  },
                  {
                      'name': 'child 2',
                      'config': {'project_id': 'project_2'},
                      'type': 'gcp_cnr',
                  },
              ]).start()
        patch('tools.cloud_adapter.clouds.gcp.Gcp.validate_credentials',
              side_effect=[
                  {'account_id': 'project_1', 'warnings': []},
                  {'account_id': 'project_2', 'warnings': []},
              ]).start()
        code, _ = self.client.observe_resources(self.org_id)
        self.assertEqual(code, 204)
        code, resp = self.client.cloud_account_list(self.org_id)
        self.assertEqual(code, 200)
        self.children = [
            c for c in resp['cloud_accounts'] if c['id'] != self.tenant['id']
        ]
        self.assertEqual(len(self.children), 2)
        self.child_1 = self.children[0]
        self.child_2 = self.children[1]
        # Allow intentional duplicates: production unique index prevents them,
        # but this endpoint detects cases where the index was bypassed/corrupted.
        self.resources_collection.drop_index('OptResourceUnique')

    def _insert_resource(self, cloud_account_id, cloud_resource_id, name,
                         deleted_at=0, tags=None, resource_type='Instance',
                         cluster_type_id=None):
        doc = {
            '_id': str(uuid.uuid4()),
            'cloud_account_id': cloud_account_id,
            'cloud_resource_id': cloud_resource_id,
            'name': name,
            'resource_type': resource_type,
            'organization_id': self.org_id,
            'created_at': opttime.utcnow_timestamp(),
            'deleted_at': deleted_at,
            'active': True,
            'tags': tags or {},
        }
        if cluster_type_id:
            doc['cluster_type_id'] = cluster_type_id
        self.resources_collection.insert_one(doc)

    def test_no_duplicates(self):
        self._insert_resource(
            self.child_1['id'], 'projects/p1/instances/a', 'a')
        self._insert_resource(
            self.child_1['id'], 'projects/p1/instances/b', 'b')
        code, res = self.client.resource_duplicates_list(self.child_1['id'])
        self.assertEqual(code, 200)
        self.assertEqual(res['count'], 0)
        self.assertEqual(res['duplicate_groups'], [])
        self.assertEqual(res['cloud_account_id'], self.child_1['id'])

    def test_leaf_duplicates(self):
        rid = 'projects/p1/instances/dup'
        self._insert_resource(self.child_1['id'], rid, 'dup-1')
        self._insert_resource(self.child_1['id'], rid, 'dup-2')
        self._insert_resource(
            self.child_1['id'], 'projects/p1/instances/ok', 'ok')
        code, res = self.client.resource_duplicates_list(self.child_1['id'])
        self.assertEqual(code, 200)
        self.assertEqual(res['count'], 1)
        group = res['duplicate_groups'][0]
        self.assertEqual(group['cloud_resource_id'], rid)
        self.assertEqual(group['count'], 2)
        self.assertEqual(group['cloud_account_id'], self.child_1['id'])
        self.assertEqual(len(group['resources']), 2)

    def test_tenant_aggregates_children(self):
        rid_1 = 'projects/p1/instances/x'
        rid_2 = 'projects/p2/instances/y'
        self._insert_resource(self.child_1['id'], rid_1, 'x-1')
        self._insert_resource(self.child_1['id'], rid_1, 'x-2')
        self._insert_resource(self.child_2['id'], rid_2, 'y-1')
        self._insert_resource(self.child_2['id'], rid_2, 'y-2')
        self._insert_resource(self.child_2['id'], rid_2, 'y-3')
        code, res = self.client.resource_duplicates_list(self.tenant['id'])
        self.assertEqual(code, 200)
        self.assertEqual(res['count'], 2)
        by_rid = {g['cloud_resource_id']: g for g in res['duplicate_groups']}
        self.assertEqual(by_rid[rid_1]['count'], 2)
        self.assertEqual(by_rid[rid_2]['count'], 3)

    def test_deleted_resources_ignored(self):
        rid = 'projects/p1/instances/gone'
        self._insert_resource(self.child_1['id'], rid, 'live')
        self._insert_resource(
            self.child_1['id'], rid, 'deleted',
            deleted_at=opttime.utcnow_timestamp())
        code, res = self.client.resource_duplicates_list(self.child_1['id'])
        self.assertEqual(code, 200)
        self.assertEqual(res['count'], 0)

    def test_unsupported_type(self):
        aws_creds = {
            'name': 'aws',
            'type': 'aws_cnr',
            'config': {
                'access_key_id': 'key',
                'secret_access_key': 'secret',
                'config_scheme': 'create_report',
            }
        }
        code, aws = self.create_cloud_account(
            self.org_id, aws_creds, auth_user_id=self.user_id)
        self.assertEqual(code, 201)
        code, res = self.client.resource_duplicates_list(aws['id'])
        self.assertEqual(code, 400)
        self.verify_error_code(res, 'OE0436')

    def test_nonexistent_cloud_account(self):
        code, res = self.client.resource_duplicates_list(str(uuid.uuid4()))
        self.assertEqual(code, 404)
        self.verify_error_code(res, 'OE0002')

    def test_refresh_persists_snapshot(self):
        rid = 'projects/p1/instances/snap'
        self._insert_resource(self.child_1['id'], rid, 's1')
        self._insert_resource(self.child_1['id'], rid, 's2')
        code, res = self.client.resource_duplicates_refresh(self.child_1['id'])
        self.assertEqual(code, 200)
        self.assertEqual(res['count'], 1)
        snap = self.mongo_client.restapi.resource_duplicate_checks.find_one(
            {'cloud_account_id': self.child_1['id']})
        self.assertIsNotNone(snap)
        self.assertEqual(snap['count'], 1)

    def test_uncollapsed_composer_sku_shares_keeper_group(self):
        uuid = 'e8711ea5-ec4c-4ece-bad1-e54b3b6a923c'
        tags = {'goog-composer-environment-uuid': uuid}
        self._insert_resource(
            self.child_1['id'], 'composer/%s' % uuid, 'airflow-prod',
            tags=tags, resource_type='Composer')
        self._insert_resource(
            self.child_1['id'], '25C6-4E91-086B',
            'Cloud Composer GOOGLE-API Data Transfer Out',
            tags=tags, resource_type='Cloud Composer')
        self._insert_resource(
            self.child_1['id'], 'D7FD-FC38-57D9',
            'Small Cloud Composer Environment Fee',
            tags={'env': 'prod'}, resource_type='Cloud Composer')
        code, res = self.client.resource_duplicates_list(self.child_1['id'])
        self.assertEqual(code, 200)
        self.assertEqual(res['count'], 1)
        group = res['duplicate_groups'][0]
        self.assertEqual(group['cloud_resource_id'], 'composer/%s' % uuid)
        self.assertEqual(group['count'], 2)
        names = {row['name'] for row in group['resources']}
        self.assertEqual(names, {
            'airflow-prod',
            'Cloud Composer GOOGLE-API Data Transfer Out',
        })

    def test_uncollapsed_group_decodes_mongo_tag_keys(self):
        from tools.cloud_adapter.gcp_resource_collapse import encode_tag_key
        uuid = '05ca77cc-4f2d-4497-9465-f0d341b9a441'
        tags = {encode_tag_key('goog-composer-environment-uuid'): uuid}
        self._insert_resource(
            self.child_1['id'], 'composer/%s' % uuid, 'airflow-prod',
            tags=tags, resource_type='Composer')
        self._insert_resource(
            self.child_1['id'], '25C6-4E91-086B',
            'Cloud Composer GOOGLE-API Data Transfer Out',
            tags=tags, resource_type='Cloud Composer')
        code, res = self.client.resource_duplicates_list(self.child_1['id'])
        self.assertEqual(code, 200)
        self.assertEqual(res['count'], 1)
        self.assertEqual(
            res['duplicate_groups'][0]['cloud_resource_id'],
            'composer/%s' % uuid)

    def test_inherited_cluster_tags_are_not_duplicate_groups(self):
        uuid = 'e8711ea5-ec4c-4ece-bad1-e54b3b6a923c'
        tags = {'goog-composer-environment-uuid': uuid}
        self._insert_resource(
            self.child_1['id'], 'composer/%s' % uuid, 'airflow-prod',
            tags=tags, resource_type='Composer')
        self._insert_resource(
            self.child_1['id'], '72BD-5309-1C4C',
            'Artifact Registry Storage',
            tags=tags, resource_type='Artifact Registry')
        self._insert_resource(
            self.child_1['id'], 'A03E-E620-7389', 'gke-primary-pool',
            tags={'goog-k8s-cluster-name': 'pf-sns-prod-gke'},
            resource_type='Instance')
        code, res = self.client.resource_duplicates_list(self.child_1['id'])
        self.assertEqual(code, 200)
        self.assertEqual(res['count'], 0)

    def test_cluster_parent_with_cloud_account_is_not_a_duplicate(self):
        uuid_ = 'e8711ea5-ec4c-4ece-bad1-e54b3b6a923c'
        tags = {'goog-composer-environment-uuid': uuid_}
        self._insert_resource(
            self.child_1['id'], 'composer/%s' % uuid_, 'airflow-prod',
            tags=tags, resource_type='Composer')
        self._insert_resource(
            self.child_1['id'], 'Composer/%s' % uuid_, None,
            tags=tags, resource_type='Composer',
            cluster_type_id=str(uuid.uuid4()))
        code, res = self.client.resource_duplicates_list(self.child_1['id'])
        self.assertEqual(code, 200)
        self.assertEqual(res['count'], 0)

    def test_uncollapsed_dataproc_serverless_sku_without_cluster_uuid(self):
        tags = {
            'goog-dataproc-batch-uuid': '10c1bc79-26ca-4c50-886c-1066b3160872',
            'airflow-dag-id': 'sim_products_onboarding_ace_prod',
        }
        self._insert_resource(
            self.child_1['id'], 'dataproc/dag/sim_products_onboarding_ace_prod',
            'sim_products_onboarding_ace_prod',
            tags=tags, resource_type='Dataproc')
        self._insert_resource(
            self.child_1['id'], 'A9D4-0464-05DA',
            'Data Compute Unit (milli) Hours - Dataproc Serverless Batch',
            tags=tags, resource_type='Dataproc')
        code, res = self.client.resource_duplicates_list(self.child_1['id'])
        self.assertEqual(code, 200)
        self.assertEqual(res['count'], 1)
        self.assertEqual(
            res['duplicate_groups'][0]['cloud_resource_id'],
            'dataproc/dag/sim_products_onboarding_ace_prod')

    def test_watch_dataflow_workers_are_duplicate_groups(self):
        tags = {'goog-dataflow-job-id': '2026-08-24_job'}
        self._insert_resource(
            self.child_1['id'], '7804579355146023165', 'df-worker-a',
            tags=tags, resource_type='Instance')
        self._insert_resource(
            self.child_1['id'], '9E4E-F9A7-5EAE',
            'Dataflow vCPU Time',
            tags=tags, resource_type='Dataflow')
        self._insert_resource(
            self.child_1['id'], 'AAAA-BBBB-CCCC',
            'Dataflow Shuffle',
            resource_type='Dataflow')
        code, res = self.client.resource_duplicates_list(self.child_1['id'])
        self.assertEqual(code, 200)
        self.assertEqual(res['count'], 1)
        group = res['duplicate_groups'][0]
        self.assertEqual(group['cloud_resource_id'], 'dataflow/2026-08-24_job')
        self.assertEqual(group['count'], 2)

    def test_list_details_attributes_duplicate_groups_to_project(self):
        rid = 'projects/p1/instances/list-health'
        self._insert_resource(self.child_1['id'], rid, 's1')
        self._insert_resource(self.child_1['id'], rid, 's2')
        code, res = self.client.resource_duplicates_refresh(self.tenant['id'])
        self.assertEqual(code, 200)
        self.assertEqual(res['count'], 1)
        code, cloud_acc_list = self.client.cloud_account_list(
            self.org_id, details=True)
        self.assertEqual(code, 200)
        by_id = {c['id']: c for c in cloud_acc_list['cloud_accounts']}
        self.assertEqual(
            by_id[self.child_1['id']]['details']['duplicate_groups'], 1)
        self.assertEqual(
            by_id[self.child_2['id']]['details']['duplicate_groups'], 0)
        self.assertEqual(
            by_id[self.tenant['id']]['details']['duplicate_groups'], 1)
        self.assertFalse(
            by_id[self.child_1['id']]['details']['cost_mismatch'])
