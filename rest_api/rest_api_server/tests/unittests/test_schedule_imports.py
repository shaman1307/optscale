import uuid
from datetime import datetime, timedelta
from unittest.mock import patch
from freezegun import freeze_time

from rest_api.rest_api_server.controllers.report_import import (
    DEFAULT_QUEUE_MESSAGE_EXPIRATION_SECONDS,
    DEFAULT_NOT_PROCESSED_REPORT_THRESHOLD_SECONDS,
)
from rest_api.rest_api_server.models.db_factory import DBFactory, DBType
from rest_api.rest_api_server.models.db_base import BaseDB
from rest_api.rest_api_server.models.models import CloudAccount
from rest_api.rest_api_server.models.enums import (
    CloudTypes
)
from rest_api.rest_api_server.tests.unittests.test_api_base import TestApiBase
from rest_api.rest_api_server.utils import MAX_32_INT, encode_config
import tools.optscale_time as opttime


class TestScheduleImportsApi(TestApiBase):

    def setUp(self, version='v2'):
        super().setUp(version)
        _, self.org = self.client.organization_create(
            {'name': "organization"})
        self.org_id = self.org['id']
        _, self.org2 = self.client.organization_create(
            {'name': "organization2"})
        self.org_id2 = self.org2['id']
        patch('rest_api.rest_api_server.controllers.report_import.'
              'ReportImportBaseController.publish_task').start()
        self.import_settings = {
            'not_processed_threshold_secs': DEFAULT_NOT_PROCESSED_REPORT_THRESHOLD_SECONDS,
            'message_expiration_secs': DEFAULT_QUEUE_MESSAGE_EXPIRATION_SECONDS,
        }
        patch(
            'optscale_client.config_client.client.Client.report_imports_setting',
            return_value=self.import_settings,
        ).start()

        def fake_write(key, value, **kwargs):
            if str(key).endswith('incremental_scheduler_enabled'):
                self.import_settings['incremental_scheduler_enabled'] = value

        patch(
            'optscale_client.config_client.client.Client.write',
            side_effect=fake_write,
        ).start()

    def test_schedule_imports_without_cloud_acc(self):
        code, ret = self.client.schedule_import(0)
        self.assertEqual(code, 201)
        self.assertEqual(len(ret['report_imports']), 0)

    def test_schedule_imports_wrong_period(self):
        code, ret = self.client.schedule_import('a')
        self.assertEqual(code, 400)
        self.assertEqual(ret['error']['reason'],
                         'period should be integer')

        code, ret = self.client.schedule_import(-1)
        self.assertEqual(code, 400)
        self.assertEqual(
            ret['error']['reason'],
            'Value of "period" should be between 0 and %s' % MAX_32_INT
        )

    def test_schedule_imports_argument_required(self):
        code, ret = self.client.schedule_import(None)
        self.assertEqual(code, 400)
        self.assertEqual(ret['error']['reason'],
                         'period, organization_id or cloud_account_id is required')

    def test_schedule_unexpected(self):
        code, ret = self.client.post(self.client.schedule_import_url(),
                                     {'str': 1, 'cloud_account_id': str(uuid.uuid4())})
        self.assertEqual(code, 400)
        self.assertEqual(ret['error']['reason'],
                         'Unexpected parameters: str')

    def test_schedule_exclusive_period(self):
        code, ret = self.client.post(self.client.schedule_import_url(),
                                     {'period': 1, 'cloud_account_id': str(uuid.uuid4())})
        self.assertEqual(code, 400)
        self.assertEqual(ret['error']['reason'],
                         "period should be used exclusively")

    def test_schedule_invalid_priority(self):
        for i in [-1, 0, 99]:
            code, ret = self.client.post(self.client.schedule_import_url(),
                                         {'cloud_account_id': str(uuid.uuid4()), 'priority': i})
            self.assertEqual(code, 400)
            self.assertEqual(ret['error']['reason'],
                             "Priority should be 1...9")

    def _create_cloud_acc_object(self, import_period=None, auto_import=True,
                                 config=None, org_id=None, cloud_type=CloudTypes.AWS_CNR):
        if config is None:
            config = dict()
        if org_id is None:
            org_id = self.org_id
        db = DBFactory(DBType.Test, None).db
        engine = db.engine
        session = BaseDB.session(engine)()
        cloud_acc = CloudAccount(
            name=str(uuid.uuid4()),
            created_at=opttime.utcnow_timestamp(),
            deleted_at=0,
            config=encode_config(config),
            organization_id=org_id,
            auto_import=auto_import,
            import_period=import_period,
            type=cloud_type
        )
        session.add(cloud_acc)
        session.commit()
        return cloud_acc.id

    def test_schedule_imports(self):
        cloud_acc1 = self._create_cloud_acc_object(import_period=0)
        cloud_acc2 = self._create_cloud_acc_object(import_period=6)
        cloud_acc3 = self._create_cloud_acc_object(import_period=0)

        code, ret = self.client.schedule_import(0)
        self.assertEqual(code, 201)
        self.assertEqual(len(ret['report_imports']), 2)
        for _import in ret['report_imports']:
            self.assertIn(_import['cloud_account_id'], [cloud_acc1, cloud_acc3])

        cloud_acc4 = self._create_cloud_acc_object(
            import_period=6, auto_import=False)
        code, ret = self.client.schedule_import(6)
        self.assertEqual(code, 201)
        self.assertEqual(len(ret['report_imports']), 1)
        self.assertEqual(
            ret['report_imports'][0]['cloud_account_id'], cloud_acc2)

    def test_schedule_imports_org_id(self):
        self._create_cloud_acc_object(org_id=self.org_id)
        self._create_cloud_acc_object(org_id=self.org_id)
        self._create_cloud_acc_object(org_id=self.org_id2)
        code, ret = self.client.schedule_import(organization_id=self.org_id)
        self.assertEqual(code, 201)
        self.assertEqual(len(ret['report_imports']), 2)

    def test_schedule_imports_org_id_specify_cloud(self):
        self._create_cloud_acc_object(org_id=self.org_id)
        self._create_cloud_acc_object(org_id=self.org_id)
        self._create_cloud_acc_object(org_id=self.org_id, cloud_type=CloudTypes.GCP_CNR)
        code, ret = self.client.schedule_import(organization_id=self.org_id,
                                                cloud_account_type='gcp_cnr')
        self.assertEqual(code, 201)
        self.assertEqual(len(ret['report_imports']), 1)

    def test_schedule_imports_invalid_cloud_type(self):
        self._create_cloud_acc_object(org_id=self.org_id)
        code, ret = self.client.schedule_import(organization_id=self.org_id,
                                                cloud_account_type='invalid')
        self.assertEqual(code, 400)
        self.assertEqual(ret['error']['reason'],
                         "invalid cloud account type: invalid")

    def test_dont_schedule_for_linked_aws(self):
        main_cloud_acc = self._create_cloud_acc_object(import_period=0)
        linked_cloud_acc = self._create_cloud_acc_object(
            import_period=0, config={"linked": True})

        code, ret = self.client.schedule_import(0)
        self.assertEqual(code, 201)
        self.assertEqual(len(ret['report_imports']), 1)
        self.assertEqual(ret['report_imports'][0]['cloud_account_id'],
                         main_cloud_acc)

    def test_schedule_imports_for_deleted_org(self):
        code, org2 = self.client.organization_create({'name': 'org2'})
        self.assertEqual(code, 201)
        ca1 = self._create_cloud_acc_object(import_period=0, org_id=org2['id'])
        ca2 = self._create_cloud_acc_object(import_period=0, org_id=self.org_id)

        code, ret = self.client.schedule_import(0)
        self.assertEqual(code, 201)
        self.assertEqual(len(ret['report_imports']), 2)
        for i in ret['report_imports']:
            self.client.report_import_update(i['id'], {'state': 'failed'})
        self.delete_organization(org2['id'])

        code, ret = self.client.schedule_import(0)
        self.assertEqual(code, 201)
        self.assertEqual(len(ret['report_imports']), 1)
        self.assertEqual(ret['report_imports'][0]['cloud_account_id'], ca2)

    def test_schedule_org_id_with_ca_id(self):
        code, ret = self.client.post(self.client.schedule_import_url(),
                                     {'organization_id': self.org_id, 'cloud_account_id': str(uuid.uuid4())})
        self.assertEqual(code, 400)
        self.assertEqual(ret['error']['reason'],
                         'Cannot use organization_id with cloud_account_id')
        self.assertEqual(ret['error']['error_code'],
                         'OE0528')

    def test_schedule_org_account_type_without_org_id(self):
        code, ret = self.client.post(self.client.schedule_import_url(),
                                     {'cloud_account_type': CloudTypes.AWS_CNR.value,
                                      'cloud_account_id': str(uuid.uuid4())})
        self.assertEqual(code, 400)
        self.assertEqual(ret['error']['reason'],
                         'Cannot use cloud_account_type without organization_id')
        self.assertEqual(ret['error']['error_code'],
                         'OE0529')

    def test_create_scheduled_duplicate(self):
        code, org2 = self.client.organization_create({'name': 'org2'})
        self.assertEqual(code, 201)
        self._create_cloud_acc_object(import_period=0, org_id=org2['id'])
        code, ret = self.client.schedule_import(0)

        self.assertEqual(len(ret['report_imports']), 1)
        first_import_id = ret['report_imports'][0]['id']
        # Fresh SCHEDULED still waiting — do not enqueue a duplicate.
        code, ret = self.client.schedule_import(0)
        self.assertEqual(len(ret['report_imports']), 0)
        # Past 3h: fail the stuck SCHEDULED, then the same tick may create a
        # replacement (never while the old row was still SCHEDULED).
        with freeze_time(opttime.utcnow() + timedelta(hours=3, seconds=1)):
            code, resp = self.client.schedule_import(0)
            self.assertEqual(len(resp['report_imports']), 1)
            code, ret = self.client.schedule_import(0)
            self.assertEqual(len(ret['report_imports']), 0)
            code, stale = self.client.report_import_get(first_import_id)
            self.assertEqual(code, 200)
            self.assertEqual(stale['state'], 'failed')
            self.assertIn('stuck in scheduled', stale['state_reason'])
            for r in resp['report_imports']:
                self.client.report_import_update(r['id'], {'state': 'completed'})
            code, ret = self.client.schedule_import(0)
            self.assertEqual(len(ret['report_imports']), 1)

    def test_create_active_duplicate(self):
        code, org2 = self.client.organization_create({'name': 'org2'})
        self._create_cloud_acc_object(import_period=0, org_id=org2['id'])
        self.assertEqual(code, 201)
        code, ret = self.client.schedule_import(0)
        imp = ret['report_imports'][0]
        self.client.report_import_update(imp['id'], {'state': 'in_progress'})
        code, ret = self.client.schedule_import(0)
        self.assertEqual(len(ret['report_imports']), 0)
        base = opttime.utcnow()
        with freeze_time(base + timedelta(hours=10)):
            self.client.report_import_update(imp['id'], {})
            code, ret = self.client.schedule_import(0)
            self.assertEqual(len(ret['report_imports']), 0)
        # Past the 30m IN_PROGRESS idle threshold relative to the refresh above.
        with freeze_time(base + timedelta(hours=10, minutes=31)):
            code, ret = self.client.schedule_import(0)
            self.assertEqual(len(ret['report_imports']), 1)
            code, stale = self.client.report_import_get(imp['id'])
            self.assertEqual(code, 200)
            self.assertEqual(stale['state'], 'failed')
            self.assertIn('stuck in progress', stale['state_reason'])

    def test_schedule_specific_cloud_account_busy(self):
        cloud_acc_id = self._create_cloud_acc_object(import_period=0)
        code, ret = self.client.schedule_import(
            cloud_account_id=cloud_acc_id)
        self.assertEqual(code, 201)
        self.assertEqual(len(ret['report_imports']), 1)
        code, ret = self.client.schedule_import(
            cloud_account_id=cloud_acc_id)
        self.assertEqual(code, 409)
        self.assertEqual(ret['error']['error_code'], 'OE0574')

    def test_import_scheduler_default_enabled(self):
        code, ret = self.client.import_scheduler_get(self.org_id)
        self.assertEqual(code, 200)
        self.assertEqual(ret, {'enabled': True})

    def test_import_scheduler_stop_skips_all_period_crons_keeps_manual(self):
        period_acc = self._create_cloud_acc_object(import_period=0)
        for period in (0, 1, 6, 24):
            self._create_cloud_acc_object(import_period=period)
        code, ret = self.client.import_scheduler_update(
            self.org_id, {'enabled': False})
        self.assertEqual(code, 200)
        self.assertEqual(ret, {'enabled': False})
        code, ret = self.client.import_scheduler_get(self.org_id)
        self.assertEqual(code, 200)
        self.assertFalse(ret['enabled'])
        for period in (0, 1, 6, 24):
            code, ret = self.client.schedule_import(period)
            self.assertEqual(code, 201)
            self.assertEqual(ret['report_imports'], [])
        code, ret = self.client.schedule_import(cloud_account_id=period_acc)
        self.assertEqual(code, 201)
        self.assertEqual(len(ret['report_imports']), 1)
        self.assertEqual(
            ret['report_imports'][0]['cloud_account_id'], period_acc)

    def test_import_scheduler_start_resumes_all_period_crons(self):
        accounts = {
            period: self._create_cloud_acc_object(import_period=period)
            for period in (0, 1, 6, 24)
        }
        self.client.import_scheduler_update(self.org_id, {'enabled': False})
        self.client.import_scheduler_update(self.org_id, {'enabled': True})
        for period, acc_id in accounts.items():
            code, ret = self.client.schedule_import(period)
            self.assertEqual(code, 201)
            self.assertEqual(len(ret['report_imports']), 1)
            self.assertEqual(
                ret['report_imports'][0]['cloud_account_id'], acc_id)

    def test_import_scheduler_requires_bool_enabled(self):
        code, ret = self.client.import_scheduler_update(self.org_id, {})
        self.assertEqual(code, 400)
        code, ret = self.client.import_scheduler_update(
            self.org_id, {'enabled': 'yes'})
        self.assertEqual(code, 400)

    def test_import_scheduler_reads_docker_stopped_containers(self):
        with patch(
            'rest_api.rest_api_server.controllers.report_import.'
            'docker_control_enabled', return_value=True
        ), patch(
            'rest_api.rest_api_server.controllers.report_import.'
            'schedulers_enabled', return_value=False
        ):
            code, ret = self.client.import_scheduler_get(self.org_id)
        self.assertEqual(code, 200)
        self.assertEqual(ret, {'enabled': False})

    def test_import_scheduler_patch_starts_docker_containers(self):
        with patch(
            'rest_api.rest_api_server.controllers.report_import.'
            'docker_control_enabled', return_value=True
        ), patch(
            'rest_api.rest_api_server.controllers.report_import.'
            'set_schedulers_enabled', return_value={'enabled': True}
        ) as mock_set:
            code, ret = self.client.import_scheduler_update(
                self.org_id, {'enabled': True})
        self.assertEqual(code, 200)
        self.assertEqual(ret, {'enabled': True})
        mock_set.assert_called_once_with(True)
        self.assertNotIn(
            'incremental_scheduler_enabled', self.import_settings)

    def test_schedulers_enabled_false_when_containers_exited(self):
        from rest_api.rest_api_server.controllers import (
            import_scheduler_docker as docker_sched)
        containers = [{
            'Id': name,
            'State': 'exited',
            'Labels': {docker_sched.COMPOSE_SERVICE_LABEL: name},
        } for name in docker_sched.SCHEDULER_SERVICES]
        with patch.object(
                docker_sched, '_docker_request', return_value=containers):
            self.assertFalse(docker_sched.schedulers_enabled())
