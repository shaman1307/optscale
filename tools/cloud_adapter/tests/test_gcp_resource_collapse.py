#!/usr/bin/env python
import unittest

from tools.cloud_adapter.gcp_resource_collapse import (
    cloudsql_instance_leftover_identity,
    collapse_expense_chunk,
    collapse_gcp_resources,
    collapse_group_id,
    collapse_leftover_may_fold,
    collapsed_keeper_set,
    composer_collapse_identity,
    encode_tag_key,
    gcp_collapse_identity,
    gcp_detailed_collapse_identity,
    gcp_watch_identity,
    gke_collapse_identity,
    is_compute_disk_row,
    is_stale_serverless_dataproc_resource,
    is_unlabeled_gke_volume,
    labeled_collapse_raw_rewrite_updates,
    pick_collapsed_duplicate_keeper,
    PLAIN_COMPOSER_UUID_FIELD,
    PLAIN_DATAPROC_CLUSTER_UUID_FIELD,
    PLAIN_GKE_NAME_FIELD,
    serverless_dataproc_collapsed_raw_id,
    serverless_dataproc_raw_rewrite_updates,
    stale_serverless_dataproc_keeper_set,
    unlabeled_gke_pvc_raw_filter,
    unlabeled_gke_pvc_raw_rewrite_updates,
    unlabeled_gke_pvc_resource_filter,
    unlabeled_gke_volume_collapse_identity,
    UNLABELED_GKE_PVC_ID_SKIP_REGEX,
    deleted_collapsed_member_match,
    deleted_miscollapsed_billing_sku_match,
    is_gcp_billing_sku_id,
    is_labeled_collapse_leftover_id,
    COLLAPSED_IDENTITY_ID_REGEX,
    DATAPROC_UUID_RESOURCE_ID_REGEX,
    GCP_BILLING_SKU_ID_REGEX,
    PVC_NAME_PREFIX,
)


class TestGcpCollapseIdentity(unittest.TestCase):
    def test_ignores_empty_and_unrelated_tags(self):
        self.assertIsNone(gcp_collapse_identity(None))
        self.assertIsNone(gcp_collapse_identity({}))
        self.assertIsNone(gcp_collapse_identity({'env': 'prod', 'name': 'vm'}))

    def test_dataproc_uuid(self):
        ident = gcp_collapse_identity({
            'goog-dataproc-cluster-uuid': '5fcf5527-4bb6-4991-82f4-55b339a0a2af',
            'goog-dataproc-cluster-name': 'dataproc',
            'name': 'gke-spot-pool',
        })
        self.assertEqual(ident['resource_type'], 'Dataproc')
        self.assertEqual(
            ident['cloud_resource_id'],
            'dataproc/5fcf5527-4bb6-4991-82f4-55b339a0a2af')
        self.assertEqual(ident['name'], 'dataproc')

    def test_serverless_batch_collapses_by_dag(self):
        ident = gcp_collapse_identity({
            'goog-dataproc-cluster-uuid': '0014ed00-da95-4d7a-a75c-22459e7337ea',
            'goog-dataproc-cluster-name': 'srvls-batch-98fa3997-4d55-478b-a235-2a841bda97b0',
            'goog-dataproc-batch-uuid': '98fa3997-4d55-478b-a235-2a841bda97b0',
            'airflow-dag-id': 'sim_dataset_processing_prod',
            'airflow-dag-display-name': 'sim_dataset_processing_prod',
        })
        self.assertEqual(ident['resource_type'], 'Dataproc')
        self.assertEqual(
            ident['cloud_resource_id'],
            'dataproc/dag/sim_dataset_processing_prod')
        self.assertEqual(ident['name'], 'sim_dataset_processing_prod')
        self.assertEqual(
            ident['tag_overrides']['goog-dataproc-cluster-uuid'],
            'sim_dataset_processing_prod')

    def test_serverless_batch_collapses_encoded_mongo_tag_keys(self):
        ident = gcp_collapse_identity({
            encode_tag_key('goog-dataproc-cluster-uuid'):
                '0014ed00-da95-4d7a-a75c-22459e7337ea',
            encode_tag_key('goog-dataproc-cluster-name'):
                'srvls-batch-98fa3997-4d55-478b-a235-2a841bda97b0',
            encode_tag_key('goog-dataproc-batch-uuid'):
                '98fa3997-4d55-478b-a235-2a841bda97b0',
            encode_tag_key('airflow-dag-id'): 'sim_dataset_processing_prod',
        })
        self.assertEqual(
            ident['cloud_resource_id'],
            'dataproc/dag/sim_dataset_processing_prod')

    def test_serverless_without_dag_uses_shared_id(self):
        ident = gcp_collapse_identity({
            'goog-dataproc-cluster-uuid': '0014ed00-da95-4d7a-a75c-22459e7337ea',
            'goog-dataproc-cluster-name': 'srvls-batch-98fa3997-4d55-478b-a235-2a841bda97b0',
            'goog-dataproc-batch-uuid': '98fa3997-4d55-478b-a235-2a841bda97b0',
        })
        self.assertEqual(ident['cloud_resource_id'], 'dataproc/serverless')
        self.assertEqual(ident['name'], 'Dataproc Serverless')

    def test_sku_with_dag_but_no_cluster_uuid_collapses_to_dag(self):
        ident = gcp_collapse_identity({
            'airflow-dag-id': 'sim_products_onboarding_ace_prod',
            'goog-dataproc-batch-uuid': '10c1bc79-26ca-4c50-886c-1066b3160872',
        })
        self.assertEqual(
            ident['cloud_resource_id'],
            'dataproc/dag/sim_products_onboarding_ace_prod')

    def test_composer_beats_gke(self):
        ident = gcp_collapse_identity({
            'goog-composer-environment-uuid': '05ca77cc-4f2d-4497-9465-f0d341b9a441',
            'goog-composer-environment': 'data-processing-airflow-nonprod',
            'goog-k8s-cluster-name': 'us-central1-data-processing-05017ba6-gke',
        })
        self.assertEqual(ident['resource_type'], 'Composer')
        self.assertEqual(
            ident['cloud_resource_id'],
            'composer/05ca77cc-4f2d-4497-9465-f0d341b9a441')
        self.assertEqual(ident['name'], 'data-processing-airflow-nonprod')

    def test_gke_cluster_name(self):
        ident = gcp_collapse_identity({
            'goog-k8s-cluster-name': 'pf-sns-prod-gke',
        })
        self.assertEqual(ident['resource_type'], 'GKE')
        self.assertEqual(ident['cloud_resource_id'], 'gke/pf-sns-prod-gke')
        self.assertEqual(ident['name'], 'pf-sns-prod-gke')

    def test_skips_blank_identity_value(self):
        self.assertIsNone(gcp_collapse_identity({
            'goog-dataproc-cluster-uuid': '',
            'goog-k8s-cluster-name': '',
        }))

    def test_stale_serverless_resource_detects_uuid_id(self):
        self.assertTrue(is_stale_serverless_dataproc_resource(
            'dataproc/0014ed00-da95-4d7a-a75c-22459e7337ea',
            'srvls-batch-0014ed00-da95-4d7a-a75c-22459e7337ea'))
        self.assertFalse(is_stale_serverless_dataproc_resource(
            'dataproc/5fcf5527-4bb6-4991-82f4-55b339a0a2af',
            'dataproc'))
        self.assertFalse(is_stale_serverless_dataproc_resource(
            'dataproc/dag/sim_dataset_processing_prod',
            'sim_dataset_processing_prod'))

    def test_pick_keeper_prefers_doc_without_hash(self):
        hashed = {
            '_id': 'hashed',
            'cloud_resource_hash': 'abc',
            'last_seen': 200,
        }
        canonical = {'_id': 'plain', 'last_seen': 100}
        self.assertEqual(
            pick_collapsed_duplicate_keeper([hashed, canonical])['_id'],
            'plain')


class TestGcpDetailedCollapseIdentity(unittest.TestCase):
    def test_sqladmin_global_name_becomes_cloudsql_instance(self):
        ident = gcp_detailed_collapse_identity({
            'resource_global_name': (
                '//sqladmin.googleapis.com/projects/pf-da-shared-prod/'
                'instances/cfg-db'),
            'resource_name': 'cfg-db',
            'sku_id': '93DA-3F55-CB04',
            'tags': {},
        })
        self.assertEqual(ident['resource_type'], 'Cloud SQL')
        self.assertEqual(ident['cloud_resource_id'], 'cloudsql/cfg-db')
        self.assertEqual(ident['name'], 'cfg-db')

    def test_cloud_sql_sku_without_global_name_stays_unidentified(self):
        self.assertIsNone(gcp_detailed_collapse_identity({
            'sku_id': '93DA-3F55-CB04',
            'sku': 'Cloud SQL for PostgreSQL: Zonal - RAM in Americas',
            'tags': {},
        }))

    def test_sql_backup_snapshot_name_becomes_cloudsql_instance(self):
        ident = gcp_detailed_collapse_identity({
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/global/snapshots/'
                '5295581089999983207'),
            'resource_name': (
                'a-244966926347-s-6bf71ce3a8428ad3-backup-1779707006387'),
            'sku': 'Storage PD Snapshot',
            'tags': {'cloud_sql_backup_id': '612a141a-125e-4d12-a3ab-288f1dd95cb9'},
        })
        self.assertEqual(
            ident['cloud_resource_id'],
            'cloudsql/a-244966926347-s-6bf71ce3a8428ad3')

    def test_ordinary_pd_snapshot_is_not_sql_backup(self):
        self.assertIsNone(gcp_detailed_collapse_identity({
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/global/snapshots/'
                '4758898924634663632'),
            'resource_name': 'cfg-db',
            'sku': 'Storage PD Snapshot',
            'tags': {},
        }))

    def test_run_service_global_name(self):
        ident = gcp_detailed_collapse_identity({
            'resource_global_name': (
                '//run.googleapis.com/projects/ci-retail-media-nonprod/'
                'locations/us-central1/services/sim-config-nonprod'),
            'resource_name': 'sim-config-nonprod',
            'sku_id': '011E-3072-CBDB',
            'tags': {},
        })
        self.assertEqual(ident['cloud_resource_id'], 'cloudrun/sim-config-nonprod')
        self.assertEqual(ident['resource_type'], 'Cloud Run')

    def test_cloud_function_global_name(self):
        ident = gcp_detailed_collapse_identity({
            'resource_global_name': (
                '//cloudfunctions.googleapis.com/projects/pf-arch-sandbox/'
                'locations/us-central1/functions/fun_001_hello_world'),
            'resource_name': 'fun_001_hello_world',
            'sku_id': '8E10-82EB-6917',
            'tags': {},
        })
        self.assertEqual(
            ident['cloud_resource_id'], 'function/fun_001_hello_world')
        self.assertEqual(ident['resource_type'], 'Cloud Run Functions')

    def test_unlabeled_ip_is_not_run_or_sql(self):
        self.assertIsNone(gcp_detailed_collapse_identity({
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/regions/us-central1/'
                'addresses/952981511495052626'),
            'resource_name': 'serverless-ipv4-1753273277343418364',
            'sku': 'Static Ip Charge',
            'tags': {},
        }))

    def test_labeled_serverless_ip_collapses_to_cloud_run(self):
        ident = gcp_detailed_collapse_identity({
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/regions/us-central1/'
                'addresses/952981511495052626'),
            'resource_name': 'serverless-ipv4-1753273277343418364',
            'sku': 'Static Ip Charge',
            'tags': {'goog-cloud-run-service': 'sim-config-nonprod'},
        })
        self.assertEqual(ident['cloud_resource_id'], 'cloudrun/sim-config-nonprod')
        self.assertEqual(ident['resource_type'], 'Cloud Run')

    def test_named_ip_with_run_label_is_not_glued(self):
        self.assertIsNone(gcp_detailed_collapse_identity({
            'resource_name': 'gke-gcp-gateway',
            'sku': 'Static Ip Charge',
            'tags': {'goog-cloud-run-service': 'sim-config-nonprod'},
        }))

    def test_cloud_sql_instance_leftover_prefixes_id(self):
        ident = cloudsql_instance_leftover_identity('cfg-db', 'Cloud SQL')
        self.assertEqual(ident['cloud_resource_id'], 'cloudsql/cfg-db')
        self.assertIsNone(cloudsql_instance_leftover_identity(
            '93DA-3F55-CB04', 'Cloud SQL'))
        self.assertIsNone(cloudsql_instance_leftover_identity(
            'cfg-db', 'Instance'))


class TestUnlabeledGkeVolume(unittest.TestCase):
    _PVC = {
        'resource_name': 'pvc-537c475a-c155-45f4-b89c-dfec36a5d939',
        'resource_global_name': (
            '//compute.googleapis.com/projects/54380973644/'
            'zones/us-central1-a/disk/7175957273002431882'),
        'sku': 'Storage PD Capacity',
        'tags': {},
    }

    def test_pvc_without_cluster_label_is_unlabeled_gke_volume(self):
        self.assertTrue(is_compute_disk_row(self._PVC))
        self.assertTrue(is_unlabeled_gke_volume(self._PVC))

    def test_pvc_path_name_is_unlabeled_gke_volume(self):
        row = dict(self._PVC)
        row['resource_name'] = (
            'projects/54380973644/disks/'
            'pvc-537c475a-c155-45f4-b89c-dfec36a5d939')
        self.assertTrue(is_unlabeled_gke_volume(row))

    def test_goog_gke_volume_tag_is_unlabeled_gke_volume(self):
        row = {
            'resource_name': 'gke-primary-pool-pvc',
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/zones/z/disks/99'),
            'sku': 'Storage PD Capacity',
            'tags': {'goog-gke-volume': ''},
        }
        self.assertTrue(is_unlabeled_gke_volume(row))

    def test_standalone_pd_is_not_unlabeled_gke_volume(self):
        row = {
            'resource_name': 'calcdsnl1-data',
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/zones/z/disk/2534772272498265879'),
            'sku': 'Storage PD Capacity',
            'tags': {},
        }
        self.assertTrue(is_compute_disk_row(row))
        self.assertFalse(is_unlabeled_gke_volume(row))

    def test_instance_is_not_unlabeled_gke_volume(self):
        row = {
            'resource_name': 'gke-pf-da-shared-nonprod-gke-primary-pool-abc',
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/zones/z/instances/1'),
            'sku': 'N2D AMD Instance Core running in Americas',
            'tags': {},
        }
        self.assertFalse(is_compute_disk_row(row))
        self.assertFalse(is_unlabeled_gke_volume(row))

    def test_snapshot_is_not_unlabeled_gke_volume(self):
        row = {
            'resource_name': 'pvc-537c475a-c155-45f4-b89c-dfec36a5d939',
            'sku': 'Storage PD Snapshot',
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/global/snapshots/1'),
            'tags': {},
        }
        self.assertFalse(is_compute_disk_row(row))
        self.assertFalse(is_unlabeled_gke_volume(row))

    def test_bucket_is_not_unlabeled_gke_volume(self):
        row = {
            'resource_name': 'pvc-bucket',
            'resource_global_name': (
                '//storage.googleapis.com/projects/_/buckets/pvc-bucket'),
            'sku': 'Standard Storage',
            'tags': {},
        }
        self.assertFalse(is_unlabeled_gke_volume(row))

    def test_ip_address_is_not_unlabeled_gke_volume(self):
        row = {
            'resource_name': 'pvc-ip',
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/regions/r/addresses/9'),
            'sku': 'Static Ip Charge',
            'tags': {},
        }
        self.assertFalse(is_unlabeled_gke_volume(row))

    def test_image_is_not_unlabeled_gke_volume(self):
        row = {
            'resource_name': 'pvc-image',
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/global/images/9'),
            'sku': 'Storage Image',
            'tags': {},
        }
        self.assertFalse(is_unlabeled_gke_volume(row))

    def test_labeled_gke_volume_is_not_unlabeled(self):
        row = dict(self._PVC)
        row['tags'] = {'goog-k8s-cluster-name': 'pf-da-shared-nonprod-gke'}
        self.assertFalse(is_unlabeled_gke_volume(row))
        self.assertEqual(
            gcp_collapse_identity(row['tags'])['cloud_resource_id'],
            'gke/pf-da-shared-nonprod-gke')

    def test_dataproc_volume_stays_dataproc(self):
        row = dict(self._PVC)
        row['tags'] = {
            'goog-dataproc-cluster-uuid': '5fcf5527-4bb6-4991-82f4-55b339a0a2af',
            'goog-dataproc-cluster-name': 'dataproc',
        }
        self.assertFalse(is_unlabeled_gke_volume(row))
        self.assertEqual(
            gcp_collapse_identity(row['tags'])['resource_type'], 'Dataproc')

    def test_composer_volume_stays_composer(self):
        row = dict(self._PVC)
        row['tags'] = {
            'goog-composer-environment-uuid': 'e8711ea5-ec4c-4ece-bad1-e54b3b6a923c',
        }
        self.assertFalse(is_unlabeled_gke_volume(row))
        self.assertEqual(
            gcp_collapse_identity(row['tags'])['resource_type'], 'Composer')

    def test_fallback_collapses_pvc_to_gke(self):
        ident = unlabeled_gke_volume_collapse_identity(
            self._PVC, 'pf-da-shared-nonprod-gke')
        self.assertEqual(ident['resource_type'], 'GKE')
        self.assertEqual(
            ident['cloud_resource_id'], 'gke/pf-da-shared-nonprod-gke')

    def test_no_fallback_does_not_collapse_pvc(self):
        self.assertIsNone(unlabeled_gke_volume_collapse_identity(self._PVC, None))
        self.assertIsNone(unlabeled_gke_volume_collapse_identity(self._PVC, ''))

    def test_fallback_does_not_collapse_standalone_disk(self):
        row = {
            'resource_name': 'calcdsnl1-data',
            'resource_global_name': (
                '//compute.googleapis.com/projects/1/zones/z/disk/1'),
            'sku': 'Storage PD Capacity',
            'tags': {},
        }
        self.assertIsNone(
            unlabeled_gke_volume_collapse_identity(
                row, 'pf-da-shared-nonprod-gke'))

    def test_raw_and_resource_filters_skip_already_collapsed(self):
        raw = unlabeled_gke_pvc_raw_filter('ca-1')
        self.assertEqual(raw['cloud_account_id'], 'ca-1')
        self.assertEqual(
            raw['$and'][0]['resource_id']['$regex'],
            UNLABELED_GKE_PVC_ID_SKIP_REGEX)
        self.assertIn('gke', UNLABELED_GKE_PVC_ID_SKIP_REGEX)
        self.assertIn('composer', UNLABELED_GKE_PVC_ID_SKIP_REGEX)
        self.assertIn('dataproc', UNLABELED_GKE_PVC_ID_SKIP_REGEX)
        labeled_fields = [
            cond['$or'][0]
            for cond in raw['$and'][3:]
        ]
        self.assertIn({PLAIN_DATAPROC_CLUSTER_UUID_FIELD: {'$exists': False}},
                      labeled_fields)
        self.assertIn({PLAIN_COMPOSER_UUID_FIELD: {'$exists': False}},
                      labeled_fields)
        self.assertIn({PLAIN_GKE_NAME_FIELD: {'$exists': False}},
                      labeled_fields)
        res = unlabeled_gke_pvc_resource_filter('ca-1')
        self.assertEqual(res['resource_type'], 'Volume')
        self.assertEqual(
            res['cloud_resource_id']['$regex'],
            UNLABELED_GKE_PVC_ID_SKIP_REGEX)
        self.assertEqual(res['$or'][0]['name']['$regex'], '^pvc-')

    def test_deleted_collapsed_member_match_covers_four_types(self):
        filt = deleted_collapsed_member_match('ca-1')
        self.assertEqual(filt['cloud_account_id'], 'ca-1')
        self.assertEqual(filt['deleted_at'], {'$gt': 0})
        clauses = filt['$or']
        crids = [c['cloud_resource_id']['$regex'] for c in clauses
                 if 'cloud_resource_id' in c]
        self.assertIn(COLLAPSED_IDENTITY_ID_REGEX, crids)
        self.assertIn(DATAPROC_UUID_RESOURCE_ID_REGEX, crids)
        self.assertTrue(any(
            c.get('resource_type') == 'Volume'
            and c.get('name', {}).get('$regex') == '^%s' % PVC_NAME_PREFIX
            for c in clauses))
        self.assertTrue(any('goog-dataproc-cluster-uuid' in str(c) for c in clauses))
        self.assertTrue(any('goog-composer-environment-uuid' in str(c) for c in clauses))
        self.assertTrue(any('goog-k8s-cluster-name' in str(c) for c in clauses))
        self.assertNotIn('cloud_resource_id', filt)

    def test_billing_sku_id_is_not_a_labeled_leftover(self):
        self.assertTrue(is_gcp_billing_sku_id('9E4E-F9A7-5EAE'))
        self.assertFalse(is_labeled_collapse_leftover_id('9E4E-F9A7-5EAE'))
        self.assertTrue(is_labeled_collapse_leftover_id('7804579355146023165'))
        self.assertFalse(is_gcp_billing_sku_id('7804579355146023165'))
        self.assertFalse(is_gcp_billing_sku_id('composer/abc'))

    def test_deleted_miscollapsed_billing_sku_match(self):
        filt = deleted_miscollapsed_billing_sku_match('ca-1')
        self.assertEqual(filt['deleted_at'], {'$gt': 0})
        self.assertEqual(
            filt['cloud_resource_id'], {'$regex': GCP_BILLING_SKU_ID_REGEX})

    def test_raw_rewrite_sets_unique_gke_id(self):
        updates = unlabeled_gke_pvc_raw_rewrite_updates(
            'ca-1', 'pf-da-shared-nonprod-gke')
        self.assertEqual(len(updates), 1)
        filt, update = updates[0]
        self.assertEqual(filt, unlabeled_gke_pvc_raw_filter('ca-1'))
        self.assertEqual(
            update,
            {'$set': {'resource_id': 'gke/pf-da-shared-nonprod-gke'}})
        self.assertIsInstance(update, dict)
        self.assertNotIsInstance(update, list)

    def test_raw_rewrite_skips_empty_cluster(self):
        self.assertEqual(unlabeled_gke_pvc_raw_rewrite_updates('ca-1', None), [])
        self.assertEqual(unlabeled_gke_pvc_raw_rewrite_updates('ca-1', ''), [])


class TestCollapseGcpResources(unittest.TestCase):
    def test_passes_through_without_collapse_tags(self):
        resources = [
            {'cloud_resource_id': '1', 'resource_type': 'Instance',
             'name': 'vm', 'tags': {'env': 'prod'}},
        ]
        self.assertEqual(collapse_gcp_resources(resources), resources)

    def test_dedupes_instance_and_volume(self):
        uuid = '5fcf5527-4bb6-4991-82f4-55b339a0a2af'
        tags = {
            'goog-dataproc-cluster-uuid': uuid,
            'goog-dataproc-cluster-name': 'dataproc',
        }
        out = collapse_gcp_resources([
            {'cloud_resource_id': '111', 'resource_type': 'Instance',
             'name': 'dataproc-sw-aaa', 'tags': tags,
             'cloud_resource_hash': 'hash-a',
             'first_seen': 10, 'last_seen': 20, 'active': False},
            {'cloud_resource_id': '222', 'resource_type': 'Volume',
             'name': 'dataproc-sw-aaa', 'tags': tags,
             'cloud_resource_hash': 'hash-b',
             'first_seen': 5, 'last_seen': 30, 'active': True},
            {'cloud_resource_id': '333', 'resource_type': 'Instance',
             'name': 'other', 'tags': {'env': 'prod'}},
        ])
        self.assertEqual(len(out), 2)
        collapsed = out[0]
        self.assertEqual(collapsed['cloud_resource_id'], 'dataproc/%s' % uuid)
        self.assertEqual(collapsed['resource_type'], 'Dataproc')
        self.assertEqual(collapsed['name'], 'dataproc')
        self.assertEqual(collapsed['first_seen'], 5)
        self.assertEqual(collapsed['last_seen'], 30)
        self.assertTrue(collapsed['active'])
        self.assertNotIn('cloud_resource_hash', collapsed)
        self.assertEqual(out[1]['cloud_resource_id'], '333')

    def test_dedupes_serverless_batches_by_dag(self):
        out = collapse_gcp_resources([
            {
                'cloud_resource_id': 'dataproc/aaa',
                'resource_type': 'Dataproc',
                'name': 'srvls-batch-aaa',
                'tags': {
                    'goog-dataproc-cluster-uuid': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
                    'goog-dataproc-cluster-name': 'srvls-batch-aaa',
                    'goog-dataproc-batch-uuid': 'aaa',
                    'airflow-dag-id': 'sim_dataset_processing_prod',
                },
            },
            {
                'cloud_resource_id': 'dataproc/bbb',
                'resource_type': 'Dataproc',
                'name': 'srvls-batch-bbb',
                'tags': {
                    'goog-dataproc-cluster-uuid': 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
                    'goog-dataproc-cluster-name': 'srvls-batch-bbb',
                    'goog-dataproc-batch-uuid': 'bbb',
                    'airflow-dag-id': 'sim_dataset_processing_prod',
                },
            },
            {
                'cloud_resource_id': 'dataproc/ccc',
                'resource_type': 'Dataproc',
                'name': 'srvls-batch-ccc',
                'tags': {
                    'goog-dataproc-cluster-uuid': 'cccccccc-cccc-cccc-cccc-cccccccccccc',
                    'goog-dataproc-cluster-name': 'srvls-batch-ccc',
                    'goog-dataproc-batch-uuid': 'ccc',
                    'airflow-dag-id': 'sim_snowflakes_datamart_execution_prod',
                },
            },
        ])
        self.assertEqual(len(out), 2)
        ids = {row['cloud_resource_id'] for row in out}
        self.assertEqual(ids, {
            'dataproc/dag/sim_dataset_processing_prod',
            'dataproc/dag/sim_snowflakes_datamart_execution_prod',
        })
        for row in out:
            self.assertEqual(
                row['tags']['goog-dataproc-cluster-uuid'],
                row['name'])

    def test_raw_rewrite_updates_are_36_safe_set_objects(self):
        self.assertEqual(
            serverless_dataproc_collapsed_raw_id('sim_dag'),
            'dataproc/dag/sim_dag')
        self.assertEqual(
            serverless_dataproc_collapsed_raw_id(''),
            'dataproc/serverless')
        updates = serverless_dataproc_raw_rewrite_updates(
            'ca-1', ['sim_dag', '', None])
        self.assertEqual(len(updates), 2)
        for _filt, update in updates:
            self.assertIsInstance(update, dict)
            self.assertNotIsInstance(update, list)
            self.assertIn('$set', update)
        self.assertEqual(
            updates[0][1]['$set']['resource_id'],
            'dataproc/dag/sim_dag')
        self.assertEqual(
            updates[1][1]['$set']['resource_id'],
            'dataproc/serverless')

    def test_stale_keeper_set_is_one_identity_per_dag(self):
        self.assertEqual(
            stale_serverless_dataproc_keeper_set('sim_dag'),
            {
                'cloud_resource_id': 'dataproc/dag/sim_dag',
                'resource_type': 'Dataproc',
                'name': 'sim_dag',
            })
        self.assertEqual(
            stale_serverless_dataproc_keeper_set(''),
            {
                'cloud_resource_id': 'dataproc/serverless',
                'resource_type': 'Dataproc',
                'name': 'Dataproc Serverless',
            })

    def test_labeled_raw_rewrite_updates_are_36_safe_set_objects(self):
        uuid = '05ca77cc-4f2d-4497-9465-f0d341b9a441'
        updates = labeled_collapse_raw_rewrite_updates(
            'ca-1', PLAIN_COMPOSER_UUID_FIELD, [uuid, '', None],
            composer_collapse_identity, skip_id_prefix='composer')
        self.assertEqual(len(updates), 1)
        filt, update = updates[0]
        self.assertIsInstance(update, dict)
        self.assertNotIsInstance(update, list)
        self.assertEqual(
            update, {'$set': {'resource_id': 'composer/%s' % uuid}})
        self.assertEqual(filt[PLAIN_COMPOSER_UUID_FIELD], uuid)
        self.assertEqual(filt['cloud_account_id'], 'ca-1')
        self.assertIn('$and', filt)

        gke_updates = labeled_collapse_raw_rewrite_updates(
            'ca-1', PLAIN_GKE_NAME_FIELD, ['pf-sns-prod-gke'],
            gke_collapse_identity,
            exclude_tag_fields=(
                PLAIN_DATAPROC_CLUSTER_UUID_FIELD, PLAIN_COMPOSER_UUID_FIELD),
            skip_id_prefix='gke')
        self.assertEqual(len(gke_updates), 1)
        gke_filt, gke_update = gke_updates[0]
        self.assertEqual(
            gke_update, {'$set': {'resource_id': 'gke/pf-sns-prod-gke'}})
        self.assertEqual(gke_filt[PLAIN_GKE_NAME_FIELD], 'pf-sns-prod-gke')

    def test_collapsed_keeper_set_strips_tag_overrides(self):
        ident = gcp_collapse_identity({
            'goog-composer-environment-uuid': 'abc',
            'goog-composer-environment': 'airflow-prod',
        })
        self.assertEqual(
            collapsed_keeper_set(ident),
            {
                'cloud_resource_id': 'composer/abc',
                'resource_type': 'Composer',
                'name': 'airflow-prod',
            })

    def test_dedupes_gke_nodes_by_cluster_name(self):
        tags = {'goog-k8s-cluster-name': 'pf-sns-prod-gke'}
        out = collapse_gcp_resources([
            {'cloud_resource_id': '111', 'resource_type': 'Instance',
             'name': 'gke-node-a', 'tags': tags,
             'cloud_resource_hash': 'hash-a',
             'first_seen': 10, 'last_seen': 20},
            {'cloud_resource_id': '222', 'resource_type': 'Volume',
             'name': 'gke-disk-a', 'tags': tags,
             'first_seen': 5, 'last_seen': 30},
        ])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['cloud_resource_id'], 'gke/pf-sns-prod-gke')
        self.assertEqual(out[0]['resource_type'], 'GKE')
        self.assertEqual(out[0]['first_seen'], 5)
        self.assertEqual(out[0]['last_seen'], 30)

    def test_collapse_expense_chunk_merges_sku_keys(self):
        chunk = {
            'sku-1': [{'tags': {
                'goog-k8s-cluster-name': 'pf-sns-prod-gke'}, 'cost': 1}],
            'sku-2': [{'tags': {
                'goog-composer-environment-uuid':
                    '05ca77cc-4f2d-4497-9465-f0d341b9a441',
                'goog-k8s-cluster-name': 'us-central1-composer-gke',
            }, 'cost': 2}],
            'sku-3': [{'tags': {}, 'cost': 3}],
        }
        out = collapse_expense_chunk(chunk)
        self.assertEqual(set(out), {
            'gke/pf-sns-prod-gke',
            'composer/05ca77cc-4f2d-4497-9465-f0d341b9a441',
            'sku-3',
        })
        self.assertEqual(len(out['gke/pf-sns-prod-gke']), 1)

    def test_collapse_expense_chunk_keeps_cloudrun_id_despite_gke_tags(self):
        chunk = {
            'cloudrun/pf-dedup-api-service': [{'tags': {
                'goog-k8s-cluster-name': 'pf-data-enrichment-nonprod-gke',
            }, 'cost': 1.3}],
        }
        out = collapse_expense_chunk(chunk)
        self.assertEqual(set(out), {'cloudrun/pf-dedup-api-service'})


class TestCollapseGroupAndLeftoverFold(unittest.TestCase):
    def test_labeled_raw_rewrite_skips_existing_identity_keepers(self):
        from tools.cloud_adapter.gcp_resource_collapse import (
            COLLAPSED_IDENTITY_RAW_SKIP_REGEX,
        )
        updates = labeled_collapse_raw_rewrite_updates(
            'ca-1', PLAIN_GKE_NAME_FIELD, ['pf-data-enrichment-nonprod-gke'],
            gke_collapse_identity, skip_id_prefix='gke')
        filt = updates[0][0]

        def _regexes(obj):
            found = []
            if isinstance(obj, dict):
                rid = obj.get('resource_id')
                if isinstance(rid, dict) and rid.get('$regex'):
                    found.append(rid['$regex'])
                for value in obj.values():
                    found.extend(_regexes(value))
            elif isinstance(obj, list):
                for value in obj:
                    found.extend(_regexes(value))
            return found

        self.assertIn(COLLAPSED_IDENTITY_RAW_SKIP_REGEX, _regexes(filt))

    def test_foreign_identity_not_folded_across_families(self):
        from tools.cloud_adapter.gcp_resource_collapse import (
            is_foreign_collapsed_identity,
            stale_serverless_dataproc_keeper_set,
        )
        gke = gke_collapse_identity('pf-data-enrichment-nonprod-gke')
        self.assertTrue(is_foreign_collapsed_identity(
            'cloudrun/pf-dedup-api-service', gke))
        self.assertFalse(is_foreign_collapsed_identity(
            'gke/pf-data-enrichment-nonprod-gke', gke))
        self.assertFalse(is_foreign_collapsed_identity(
            '7804579355146023165', gke))
        dag = stale_serverless_dataproc_keeper_set('sim_dag')
        self.assertFalse(is_foreign_collapsed_identity(
            'dataproc/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', dag))

    def test_collapse_group_id_uses_keeper_for_labeled_sku(self):
        uuid = 'e8711ea5-ec4c-4ece-bad1-e54b3b6a923c'
        self.assertEqual(
            collapse_group_id({
                'cloud_resource_id': '25C6-4E91-086B',
                'resource_type': 'Cloud Composer',
                'tags': {'goog-composer-environment-uuid': uuid},
            }),
            'composer/%s' % uuid)
        self.assertEqual(
            collapse_group_id({
                'cloud_resource_id': 'composer/%s' % uuid,
                'resource_type': 'Composer',
                'tags': {'goog-composer-environment-uuid': uuid},
            }),
            'composer/%s' % uuid)
        self.assertEqual(
            collapse_group_id({
                'cloud_resource_id': 'projects/p1/instances/a',
                'tags': {},
            }),
            'projects/p1/instances/a')

    def test_collapse_group_id_skips_inherited_cluster_tags(self):
        uuid = 'e8711ea5-ec4c-4ece-bad1-e54b3b6a923c'
        tags = {'goog-composer-environment-uuid': uuid}
        self.assertEqual(
            collapse_group_id({
                'cloud_resource_id': '72BD-5309-1C4C',
                'resource_type': 'Artifact Registry',
                'tags': tags,
            }),
            '72BD-5309-1C4C')
        self.assertEqual(
            collapse_group_id({
                'cloud_resource_id': 'A03E-E620-7389',
                'resource_type': 'Instance',
                'tags': {'goog-k8s-cluster-name': 'pf-sns-prod-gke'},
            }),
            'A03E-E620-7389')
        self.assertEqual(
            collapse_group_id({
                'cloud_resource_id': '4DBF-185F-A415',
                'resource_type': 'Cloud Storage',
                'tags': tags,
            }),
            '4DBF-185F-A415')
        self.assertEqual(
            collapse_group_id({
                'cloud_resource_id': '7804579355146023165',
                'resource_type': 'Instance',
                'tags': {'goog-k8s-cluster-name': 'pf-sns-prod-gke'},
            }),
            'gke/pf-sns-prod-gke')

    def test_leftover_fold_skips_sku_whose_raw_has_no_identity(self):
        ident = composer_collapse_identity(
            'e8711ea5-ec4c-4ece-bad1-e54b3b6a923c')
        self.assertFalse(collapse_leftover_may_fold(
            '25C6-4E91-086B', ident, {'tags': {}}))

    def test_leftover_fold_allows_sku_when_raw_already_rewritten(self):
        ident = composer_collapse_identity(
            'e8711ea5-ec4c-4ece-bad1-e54b3b6a923c')
        self.assertTrue(collapse_leftover_may_fold(
            '25C6-4E91-086B', ident, None))

    def test_leftover_fold_allows_sku_when_raw_has_identity(self):
        uuid = 'e8711ea5-ec4c-4ece-bad1-e54b3b6a923c'
        ident = composer_collapse_identity(uuid)
        self.assertTrue(collapse_leftover_may_fold(
            '25C6-4E91-086B', ident,
            {'tags': {'goog-composer-environment-uuid': uuid}}))

    def test_leftover_fold_allows_composer_sku_with_unlabeled_raw(self):
        uuid = 'e8711ea5-ec4c-4ece-bad1-e54b3b6a923c'
        ident = composer_collapse_identity(uuid)
        self.assertTrue(collapse_leftover_may_fold(
            '25C6-4E91-086B', ident, {'tags': {}},
            mongo_resource_type='Cloud Composer'))
        self.assertFalse(collapse_leftover_may_fold(
            'A03E-E620-7389', ident, {'tags': {}},
            mongo_resource_type='Instance'))
        ident = gke_collapse_identity('pf-sns-prod-gke')
        self.assertTrue(collapse_leftover_may_fold(
            '7804579355146023165', ident, {'tags': {}}))
        self.assertFalse(collapse_leftover_may_fold(
            'cloudrun/pf-dedup-api-service', ident, {'tags': {}},
            mongo_resource_type='Cloud Run'))


class TestGcpWatchIdentity(unittest.TestCase):
    def test_dataflow_job_tag_groups_workers_and_sku(self):
        tags = {'goog-dataflow-job-id': '2026-08-24_12_00_00-job'}
        self.assertEqual(
            collapse_group_id({
                'cloud_resource_id': '7804579355146023165',
                'resource_type': 'Instance',
                'tags': tags,
            }),
            'dataflow/2026-08-24_12_00_00-job')
        self.assertEqual(
            collapse_group_id({
                'cloud_resource_id': '9E4E-F9A7-5EAE',
                'resource_type': 'Dataflow',
                'tags': tags,
            }),
            'dataflow/2026-08-24_12_00_00-job')

    def test_watch_does_not_collapse_import_identity(self):
        tags = {'goog-dataflow-job-id': '2026-08-24_12_00_00-job'}
        self.assertIsNone(gcp_collapse_identity(tags))
        self.assertIsNone(gcp_detailed_collapse_identity({
            'resource_global_name': (
                '//dataflow.googleapis.com/projects/p/locations/'
                'us-central1/jobs/my-streaming-job'),
            'sku_id': '9E4E-F9A7-5EAE',
            'tags': {},
        }))
        self.assertEqual(
            gcp_watch_identity({
                'cloud_resource_id': '9E4E-F9A7-5EAE',
                'resource_global_name': (
                    '//dataflow.googleapis.com/projects/p/locations/'
                    'us-central1/jobs/my-streaming-job'),
            })['cloud_resource_id'],
            'dataflow/my-streaming-job')

    def test_watch_families_group_sku_leftover_with_object(self):
        cases = (
            (
                'alloydb/analytics',
                '//alloydb.googleapis.com/projects/p/locations/us-central1/'
                'clusters/analytics',
                'AlloyDB',
            ),
            (
                'bigtable/orders',
                '//bigtable.googleapis.com/projects/p/instances/orders',
                'Bigtable',
            ),
            (
                'filestore/nfs-share',
                '//file.googleapis.com/projects/p/locations/us-central1/'
                'instances/nfs-share',
                'Filestore',
            ),
            (
                'datafusion/etl',
                '//datafusion.googleapis.com/projects/p/locations/us-central1/'
                'instances/etl',
                'Data Fusion',
            ),
            (
                'dataproc/metastore/hive',
                '//metastore.googleapis.com/projects/p/locations/us-central1/'
                'services/hive',
                'Dataproc Metastore',
            ),
        )
        for keeper, gname, rtype in cases:
            self.assertEqual(
                collapse_group_id({
                    'cloud_resource_id': keeper,
                    'resource_type': rtype,
                    'tags': {},
                }),
                keeper)
            self.assertEqual(
                collapse_group_id({
                    'cloud_resource_id': 'AAAA-BBBB-CCCC',
                    'resource_type': rtype,
                    'resource_global_name': gname,
                    'tags': {},
                }),
                keeper)

    def test_watch_ignores_unrelated_sku_meters(self):
        self.assertEqual(
            collapse_group_id({
                'cloud_resource_id': '9E4E-F9A7-5EAE',
                'resource_type': 'Dataflow',
                'tags': {},
            }),
            '9E4E-F9A7-5EAE')
        self.assertNotEqual(
            collapse_group_id({
                'cloud_resource_id': 'dataproc/5fcf5527-4bb6-4991-82f4-55b339a0a2af',
                'resource_type': 'Dataproc',
                'tags': {},
            }),
            'dataproc/metastore/5fcf5527-4bb6-4991-82f4-55b339a0a2af')

    def test_dataflow_tag_does_not_merge_save_bulk(self):
        tags = {'goog-dataflow-job-id': 'job-1'}
        out = collapse_gcp_resources([
            {'cloud_resource_id': '111', 'name': 'worker-a',
             'resource_type': 'Instance', 'tags': tags},
            {'cloud_resource_id': '222', 'name': 'worker-b',
             'resource_type': 'Instance', 'tags': tags},
        ])
        self.assertEqual(len(out), 2)


if __name__ == '__main__':
    unittest.main()
