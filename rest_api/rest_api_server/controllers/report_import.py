import logging
import uuid
import tools.optscale_time as opttime
from sqlalchemy import and_, true, or_, exists, func
import boto3
from tools.optscale_exceptions.common_exc import (
    NotFoundException, FailedDependency, WrongArgumentsException,
    ConflictException
)
from boto3.session import Config as BotoConfig
from kombu import Connection as QConnection, Exchange, Queue
from kombu.pools import producers
from tools.cloud_adapter.cloud import Cloud as CloudAdapter

from rest_api.rest_api_server.exceptions import Err
from rest_api.rest_api_server.models.enums import ImportStates, CloudTypes
from rest_api.rest_api_server.models.models import (ReportImport, CloudAccount,
                                                    Organization)
from rest_api.rest_api_server.controllers.base import BaseController
from rest_api.rest_api_server.controllers.base_async import BaseAsyncControllerWrapper
from rest_api.rest_api_server.controllers.import_scheduler_docker import (
    docker_control_enabled,
    schedulers_enabled,
    set_schedulers_enabled,
)
from rest_api.rest_api_server.controllers.checklist import ChecklistController
from rest_api.rest_api_server.utils import (raise_unexpected_exception,
                                            check_int_attribute)
from optscale_data.report_import_queue import report_import_queue_for_type

ACTIVE_IMPORT_THRESHOLD = 1800  # 30 min
DEFAULT_NOT_PROCESSED_REPORT_THRESHOLD_SECONDS = 10800  # 3 hrs
# Waiting GCP tenant reloads exceed 3h (workers busy on large projects).
# 2026-08-15: 42 SCHEDULED messages expired at 3h while 6 siblings ran.
DEFAULT_QUEUE_MESSAGE_EXPIRATION_SECONDS = 86400  # 24 hrs
LOG = logging.getLogger(__name__)
QUEUE_PRIORITY_ARGUMENTS = {'x-max-priority': 10}
INCREMENTAL_SCHEDULER_ETCD_KEY = (
    '/restapi/report_imports/incremental_scheduler_enabled')
_SCHEDULER_DISABLED_VALUES = {'false', '0', 'no', ''}


class ReportImportBaseController(BaseController):
    def _get_model_type(self):
        return ReportImport

    RETRY_POLICY = {'max_retries': 15, 'interval_start': 0,
                    'interval_step': 1, 'interval_max': 3}

    def create(self, cloud_account_id, import_file=None, recalculate=False, priority=1):
        # Fail stale unfinished rows first so a dead SCHEDULED/IN_PROGRESS
        # cannot permanently block the account; callers still skip via
        # check_unprocessed_imports when a fresh unfinished row remains.
        self.fail_stale_imports(cloud_account_id)
        report_import = super().create(
            cloud_account_id=cloud_account_id,
            import_file=import_file,
            is_recalculation=recalculate
        )
        cloud_type = None
        if report_import.cloud_account is not None:
            cloud_type = report_import.cloud_account.type
        try:
            self.publish_task(
                {'report_import_id': report_import.id},
                priority,
                cloud_type=cloud_type,
            )
        except Exception as exc:
            # Avoid orphan SCHEDULED rows that can never be claimed.
            reason = 'Failed to enqueue import task: {0}'.format(exc)
            LOG.exception(
                'Failed to publish report import %s: %s',
                report_import.id, exc)
            self.edit(
                report_import.id,
                state=ImportStates.FAILED.value,
                state_reason=reason,
            )
            raise
        if recalculate:
            self._publish_report_import_activity(
                report_import, 'recalculation_started')
        return report_import

    def _import_age_thresholds(self):
        # Use utcnow_timestamp() (not naive datetime.timestamp()) so cutoffs
        # match created_at/updated_at, which are stored as UTC epoch seconds.
        now_ts = opttime.utcnow_timestamp()
        report_imports_setting = {}
        if self._config is not None:
            report_imports_setting = self._config.report_imports_setting() or {}
        scheduled_threshold_secs = int(
            report_imports_setting.get(
                'not_processed_threshold_secs',
                DEFAULT_NOT_PROCESSED_REPORT_THRESHOLD_SECONDS
            )
        )
        return {
            'now_ts': now_ts,
            'scheduled_threshold_secs': scheduled_threshold_secs,
            'active_threshold_secs': ACTIVE_IMPORT_THRESHOLD,
            'scheduled_cutoff': now_ts - scheduled_threshold_secs,
            'active_cutoff': now_ts - ACTIVE_IMPORT_THRESHOLD,
        }

    def check_unprocessed_imports(self, cloud_account_id):
        """True when a still-valid unfinished import blocks a new enqueue.

        Call fail_stale_imports first. After that, only fresh SCHEDULED
        (< not_processed_threshold, default 3h) and live IN_PROGRESS
        (< ACTIVE_IMPORT_THRESHOLD) remain — those must not be duplicated.
        """
        thresholds = self._import_age_thresholds()
        return self.session.query(
            exists().where(and_(
                ReportImport.cloud_account_id == cloud_account_id,
                ReportImport.deleted_at.is_(False),
                or_(
                    and_(
                        ReportImport.state == ImportStates.SCHEDULED,
                        ReportImport.created_at >= thresholds['scheduled_cutoff']
                    ),
                    and_(
                        ReportImport.state == ImportStates.IN_PROGRESS,
                        ReportImport.updated_at >= thresholds['active_cutoff']
                    )
                )
            ))
        ).scalar()

    def check_in_progress_import(self, cloud_account_id):
        """True when a live import worker is still processing this account."""
        thresholds = self._import_age_thresholds()
        return self.session.query(
            exists().where(and_(
                ReportImport.cloud_account_id == cloud_account_id,
                ReportImport.deleted_at.is_(False),
                ReportImport.state == ImportStates.IN_PROGRESS,
                ReportImport.updated_at >= thresholds['active_cutoff']
            ))
        ).scalar()

    def unfinished_import_depths_by_type(self):
        """Count SCHEDULED + IN_PROGRESS imports grouped by cloud account type.

        Used by diworker to proportion worker slots across vendor queues.
        """
        rows = self.session.query(
            CloudAccount.type,
            func.count(ReportImport.id),
        ).join(
            ReportImport,
            ReportImport.cloud_account_id == CloudAccount.id,
        ).filter(
            and_(
                ReportImport.deleted.is_(False),
                CloudAccount.deleted.is_(False),
                ReportImport.state.in_([
                    ImportStates.SCHEDULED,
                    ImportStates.IN_PROGRESS,
                ]),
            )
        ).group_by(CloudAccount.type).all()
        depths = {}
        for cloud_type, count in rows:
            if not count:
                continue
            type_key = (
                cloud_type.value if hasattr(cloud_type, 'value')
                else str(cloud_type)
            )
            depths[type_key] = int(count)
        return depths

    def unfinished_imports(self):
        """List unfinished imports for lost-import cleanup (id/state/type)."""
        rows = self.session.query(
            ReportImport.id,
            ReportImport.state,
            ReportImport.cloud_account_id,
            ReportImport.created_at,
            CloudAccount.type,
        ).join(
            CloudAccount,
            ReportImport.cloud_account_id == CloudAccount.id,
        ).filter(
            and_(
                ReportImport.deleted.is_(False),
                CloudAccount.deleted.is_(False),
                ReportImport.state.in_([
                    ImportStates.SCHEDULED,
                    ImportStates.IN_PROGRESS,
                ]),
            )
        ).all()
        unfinished = []
        for import_id, state, cloud_account_id, created_at, cloud_type in rows:
            type_key = (
                cloud_type.value if hasattr(cloud_type, 'value')
                else str(cloud_type)
            )
            state_key = state.value if hasattr(state, 'value') else str(state)
            unfinished.append({
                'id': import_id,
                'state': state_key,
                'cloud_account_id': cloud_account_id,
                'cloud_type': str(type_key).lower(),
                'created_at': created_at,
            })
        return unfinished

    def _stale_import_reason(self, report_import, thresholds):
        """Build a diagnostic reason from import state and age."""
        now_ts = thresholds['now_ts']
        if report_import.state == ImportStates.SCHEDULED:
            age_secs = max(0, int(now_ts - (report_import.created_at or 0)))
            threshold_mins = thresholds['scheduled_threshold_secs'] // 60
            reason = (
                'Import stuck in scheduled for {age} minutes '
                '(threshold {threshold}m). Likely never claimed by diworker '
                'or the queue message expired before processing started.'
            ).format(age=age_secs // 60, threshold=threshold_mins)
        else:
            idle_secs = max(0, int(now_ts - (report_import.updated_at or 0)))
            threshold_mins = thresholds['active_threshold_secs'] // 60
            reason = (
                'Import stuck in progress with no updates for {idle} minutes '
                '(threshold {threshold}m). Likely diworker crashed, was OOM-'
                'killed, or otherwise stopped updating this task.'
            ).format(idle=idle_secs // 60, threshold=threshold_mins)

        previous = (report_import.state_reason or '').strip()
        if previous:
            reason = '{reason} Previous reason: {previous}'.format(
                reason=reason, previous=previous)
        return reason

    def fail_stale_imports(self, cloud_account_id):
        """Mark stale unfinished imports as FAILED with a diagnostic reason.

        SCHEDULED older than not_processed_threshold (default 3h) and
        IN_PROGRESS idle longer than ACTIVE_IMPORT_THRESHOLD are killed so a
        later schedule tick may enqueue a replacement. Fresh SCHEDULED rows
        are left alone — check_unprocessed_imports blocks duplicates until
        they age out or complete.
        """
        thresholds = self._import_age_thresholds()
        stale_imports = self.session.query(ReportImport).filter(
            and_(
                ReportImport.cloud_account_id == cloud_account_id,
                ReportImport.deleted_at.is_(False),
                or_(
                    and_(
                        ReportImport.state == ImportStates.SCHEDULED,
                        ReportImport.created_at < thresholds['scheduled_cutoff']
                    ),
                    and_(
                        ReportImport.state == ImportStates.IN_PROGRESS,
                        ReportImport.updated_at < thresholds['active_cutoff']
                    )
                )
            )
        ).all()
        for stale_import in stale_imports:
            reason = self._stale_import_reason(stale_import, thresholds)
            LOG.warning(
                'Failing stale report import %s for cloud account %s: %s',
                stale_import.id, cloud_account_id, reason)
            self.edit(
                stale_import.id,
                state=ImportStates.FAILED.value,
                state_reason=reason,
            )
        return stale_imports

    def _publish_report_import_activity(self, report_import, action,
                                        level='INFO', error_reason=None):
        cloud_account = report_import.cloud_account
        meta = {
            'object_name': cloud_account.name,
            'cloud_account_id': cloud_account.id,
            'level': level
        }
        if error_reason:
            meta.update({'error_reason': error_reason})
        self.publish_activities_task(
            cloud_account.organization_id, report_import.id, 'report_import',
            action, meta, 'report_import.{action}'.format(action=action),
            add_token=True)

    def is_initial_completed_report(self, updated_report):
        completed_import = self.session.query(ReportImport).filter(
            and_(
                ReportImport.cloud_account_id == updated_report.cloud_account_id,
                ReportImport.state == ImportStates.COMPLETED,
                ReportImport.deleted.is_(False)
            )
        ).order_by(ReportImport.created_at).first()
        return completed_import and completed_import.id == updated_report.id

    def edit(self, item_id, **kwargs):
        kwargs['updated_at'] = opttime.utcnow_timestamp()
        updated_report = super().edit(item_id, **kwargs)
        state = kwargs.get('state')
        if updated_report.is_recalculation:
            if state == ImportStates.COMPLETED.value:
                self._publish_report_import_activity(updated_report,
                                                     'recalculation_completed')
            if state == ImportStates.FAILED.value:
                error_reason = kwargs.get('state_reason', '')
                self._publish_report_import_activity(
                    updated_report, 'recalculation_failed',
                    error_reason=error_reason, level='ERROR')
            return updated_report

        if state == ImportStates.COMPLETED.value:
            is_initial_report = self.is_initial_completed_report(
                updated_report)
            if is_initial_report:
                LOG.info('Scheduling checklist run')
                ChecklistController(
                    self.session, self._config, self.token).schedule_next_run(
                    updated_report.cloud_account.organization_id)
            if is_initial_report or updated_report.import_file:
                self._publish_report_import_activity(
                    updated_report, 'report_import_completed')
        elif state == ImportStates.FAILED.value:
            error_reason = kwargs.get('state_reason', '')
            self._publish_report_import_activity(
                updated_report, 'report_import_failed',
                error_reason=error_reason, level='ERROR')
        return updated_report

    def publish_task(self, task_params, priority=1, cloud_type=None):
        queue_conn = QConnection('amqp://{user}:{pass}@{host}:{port}'.format(
            **self._config.read_branch('/rabbit')),
            transport_options=self.RETRY_POLICY)

        task_exchange = Exchange('billing-reports', type='direct')
        # Route each cloud type to its own queue so a flood of one provider
        # (e.g. GCP) cannot starve another (e.g. Snowflake) behind it.
        queue_name = report_import_queue_for_type(cloud_type)
        task_queue = Queue(
            queue_name,
            task_exchange,
            routing_key=queue_name,
            queue_arguments=QUEUE_PRIORITY_ARGUMENTS,
        )
        report_imports_setting = (
            self._config.report_imports_setting() or {})
        expiration = float(
            report_imports_setting.get(
                'message_expiration_secs',
                DEFAULT_QUEUE_MESSAGE_EXPIRATION_SECONDS
            )
        )
        with producers[queue_conn].acquire(block=True) as producer:
            producer.publish(
                task_params,
                serializer='json',
                exchange=task_exchange,
                declare=[task_exchange, task_queue],
                routing_key=queue_name,
                retry=True,
                retry_policy=self.RETRY_POLICY,
                expiration=expiration,
                priority=priority,
            )


class ReportImportScheduleController(ReportImportBaseController):

    def _get_cloud_accounts(self, import_period, org_id,
                            cloud_account_id, cloud_account_type):
        org_subq = self.session.query(Organization.id).filter(
            Organization.deleted.is_(False)
        ).subquery()

        if import_period is not None:
            q = self.session.query(CloudAccount).filter(
                CloudAccount.organization_id.in_(org_subq),
                CloudAccount.deleted.is_(False),
                CloudAccount.auto_import == true(),
                CloudAccount.import_period == import_period,
            )
        else:
            q = self.session.query(CloudAccount).filter(
                or_(
                    CloudAccount.id == cloud_account_id,
                    CloudAccount.organization_id == org_id
                ),
                # ignoring auto import, because starting with this params only manually
                CloudAccount.deleted.is_(False),
                CloudAccount.organization_id.in_(org_subq),
            )
            if cloud_account_type:
                q = q.filter(CloudAccount.type == cloud_account_type)
        res = q.all()
        return res

    @staticmethod
    def _check_args(org_id,
                    cloud_account_id,
                    cloud_account_type,
                    priority):
        if org_id and cloud_account_id:
            raise WrongArgumentsException(
                Err.OE0528, []
            )
        if cloud_account_type and not org_id:
            raise WrongArgumentsException(
                Err.OE0529, []
            )
        if priority is not None and priority not in range(1, 10):
            raise WrongArgumentsException(Err.OE0530, [])
        if cloud_account_type is not None:
            try:
                CloudTypes(cloud_account_type)
            except ValueError:
                raise WrongArgumentsException(Err.OE0533, [cloud_account_type])

    def schedule(self, **kwargs):
        period = kwargs.pop('period', None)
        organization_id = kwargs.pop("organization_id", None)
        cloud_account_type = kwargs.pop("cloud_account_type", None)
        cloud_account_id = kwargs.pop("cloud_account_id", None)
        priority = kwargs.pop("priority", 1)
        if period is not None:
            # if import period is set there should be no other parameters
            if (organization_id is not None or
                    cloud_account_id is not None or
                    cloud_account_type is not None):
                raise WrongArgumentsException(Err.OE0531, [])
            check_int_attribute('period', period)
        if period is None and organization_id is None and cloud_account_id is None:
            raise WrongArgumentsException(Err.OE0532, [])
        if kwargs:
            raise_unexpected_exception(kwargs.keys())
        self._check_args(
            organization_id, cloud_account_id, cloud_account_type, priority)
        # report-import-scheduler-0/1/6/24 POST with period only. Manual
        # reimport uses cloud_account_id / organization_id and must keep
        # working when the schedulers are paused.
        if period is not None and not self.is_incremental_scheduler_enabled():
            LOG.info(
                'Skipping period=%s schedule: import schedulers are stopped',
                period)
            return []
        if cloud_account_type is not None:
            cloud_account_type = CloudTypes(cloud_account_type)

        cloud_accounts = self._get_cloud_accounts(
            period, organization_id, cloud_account_id, cloud_account_type)

        result = []
        for ca in cloud_accounts:
            if ca.type == CloudTypes.AWS_CNR:
                decoded_cfg = ca.decoded_config
                if decoded_cfg.get('linked', False):
                    continue
            # 1) Kill SCHEDULED >3h / idle IN_PROGRESS. 2) Skip if a fresh
            # unfinished import still exists. 3) Otherwise enqueue a new one.
            # Never enqueue while an un-killed SCHEDULED is still waiting.
            self.fail_stale_imports(ca.id)
            if self.check_unprocessed_imports(ca.id):
                # Manual schedule for a specific account must fail loudly so
                # the UI can show that another import is already running.
                if cloud_account_id is not None:
                    raise ConflictException(Err.OE0574, [])
                continue
            result.append(self.create(ca.id, priority=priority))
        return result

    def is_incremental_scheduler_enabled(self):
        settings = {}
        if self._config is not None:
            try:
                settings = self._config.report_imports_setting() or {}
            except Exception:
                settings = {}
        raw = settings.get('incremental_scheduler_enabled', 'true')
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() not in _SCHEDULER_DISABLED_VALUES

    def _require_organization(self, organization_id):
        org = self.session.query(Organization).filter(
            Organization.id == organization_id,
            Organization.deleted.is_(False),
        ).one_or_none()
        if org is None:
            raise NotFoundException(
                Err.OE0002, [Organization.__name__, organization_id])
        return org

    def scheduler_status(self, organization_id):
        self._require_organization(organization_id)
        if docker_control_enabled():
            return {'enabled': schedulers_enabled()}
        return {'enabled': self.is_incremental_scheduler_enabled()}

    def set_scheduler_enabled(self, organization_id, enabled):
        self._require_organization(organization_id)
        if docker_control_enabled():
            return set_schedulers_enabled(enabled)
        if self._config is None:
            return {'enabled': bool(enabled)}
        self._config.write(
            key=INCREMENTAL_SCHEDULER_ETCD_KEY,
            value='true' if enabled else 'false')
        LOG.info(
            'Incremental scheduler %s (org %s)',
            'started' if enabled else 'stopped', organization_id)
        return {'enabled': bool(enabled)}


class ExpensesRecalculationScheduleController(ReportImportBaseController):
    def schedule(self, cloud_account_id):
        cloud_acc = self.session.query(CloudAccount).filter(
            CloudAccount.deleted.is_(False),
            CloudAccount.type.in_([
                CloudTypes.KUBERNETES_CNR, CloudTypes.ENVIRONMENT,
                CloudTypes.DATABRICKS, CloudTypes.SNOWFLAKE,
                CloudTypes.SNOWFLAKE_TENANT]),
            CloudAccount.id == cloud_account_id
        ).one_or_none()

        if cloud_acc:
            return self.create(cloud_acc.id, recalculate=True)


class ReportImportFileController(ReportImportBaseController):
    BUCKET_NAME = 'report-imports'
    MAX_BUFFER_SIZE = 5 * 1024 * 1024

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.count = 1
        self.buffer = bytes()
        self._s3_client = None
        self.filename = str(uuid.uuid4())
        self.parts = []
        self.mpu_id = None

    @property
    def s3_client(self):
        if self._s3_client is None:
            s3_params = self._config.read_branch('/minio')
            self._s3_client = boto3.client(
                's3',
                endpoint_url='http://{}:{}'.format(
                    s3_params['host'], s3_params['port']),
                aws_access_key_id=s3_params['access'],
                aws_secret_access_key=s3_params['secret'],
                config=BotoConfig(s3={'addressing_style': 'path'})
            )
            try:
                self._s3_client.create_bucket(Bucket=self.BUCKET_NAME)
            except self._s3_client.exceptions.BucketAlreadyOwnedByYou:
                pass
        return self._s3_client

    def initialize_upload(self, cloud_account_id):
        cloud_acc = self.get_cloud_account(cloud_account_id)
        if cloud_acc is None:
            raise NotFoundException(
                Err.OE0002, [CloudAccount.__name__, cloud_account_id])
        adapter_cls = CloudAdapter.get_adapter_type(cloud_acc.type.value)
        if not adapter_cls or not adapter_cls.SUPPORTS_REPORT_UPLOAD:
            raise FailedDependency(Err.OE0434, [cloud_acc.type.value])
        mpu = self.s3_client.create_multipart_upload(Bucket=self.BUCKET_NAME,
                                                     Key=self.filename)
        self.mpu_id = mpu['UploadId']

    def add_chunk(self, chunk):
        self.buffer += chunk
        if len(self.buffer) >= self.MAX_BUFFER_SIZE:
            self.flush_buffer()

    def flush_buffer(self):
        part = self.s3_client.upload_part(
            Body=self.buffer,
            Bucket=self.BUCKET_NAME,
            Key=self.filename,
            UploadId=self.mpu_id,
            PartNumber=self.count,
        )
        self.parts.append({'PartNumber': self.count, 'ETag': part['ETag']})
        self.count += 1
        self.buffer = bytes()

    def get_cloud_account(self, cloud_account_id):
        data_set = self.session.query(
            CloudAccount, Organization
        ).outerjoin(Organization, and_(
            Organization.id == CloudAccount.organization_id,
            Organization.deleted.is_(False)
        )).filter(and_(
            CloudAccount.id == cloud_account_id,
            CloudAccount.deleted.is_(False)
        )).one_or_none()
        if data_set:
            cloud_acc, org = data_set
            if cloud_acc and not org:
                raise NotFoundException(
                    Err.OE0005, [Organization.__name__, cloud_acc.organization_id])

            return cloud_acc

    def _validate(self, item, is_new=True, **kwargs):
        self.check_cloud_account(item.cloud_account_id)

    def complete_upload(self, cloud_account_id):
        if len(self.buffer) != 0:
            self.flush_buffer()
        self.s3_client.complete_multipart_upload(
            Bucket=self.BUCKET_NAME,
            Key=self.filename,
            UploadId=self.mpu_id,
            MultipartUpload={'Parts': self.parts},
        )
        report_import = self.create(
            cloud_account_id=cloud_account_id,
            import_file='{}/{}'.format(self.BUCKET_NAME, self.filename)
        )
        return report_import

    def list(self, cloud_account_id, show_completed=False, show_active=False):
        self.check_cloud_account(cloud_account_id)
        query = self.session.query(self.model_type).filter(
            self.model_type.deleted_at.is_(False),
            self.model_type.cloud_account_id == cloud_account_id,
        )
        if not show_completed:
            query = query.filter(
                self.model_type.state != ImportStates.COMPLETED
            )
        if show_active:
            ts = opttime.utcnow_timestamp() - ACTIVE_IMPORT_THRESHOLD
            query = query.filter(
                self.model_type.state == ImportStates.IN_PROGRESS,
                self.model_type.updated_at >= ts
            )
        return query.all()

    def get(self, item_id, **kwargs):
        report = super().get(item_id, **kwargs)
        if report:
            self.check_cloud_account(report.cloud_account_id)
        return report

    def check_cloud_account(self, cloud_account_id):
        cc = self.get_cloud_account(cloud_account_id)
        if cc is None:
            raise NotFoundException(
                Err.OE0002, [CloudAccount.__name__, cloud_account_id])


class ReportImportAsyncController(BaseAsyncControllerWrapper):
    def _get_controller_class(self):
        return ReportImportFileController


class ReportImportScheduleAsyncController(BaseAsyncControllerWrapper):
    def _get_controller_class(self):
        return ReportImportScheduleController


class ReportImportQueueStatsAsyncController(BaseAsyncControllerWrapper):
    def _get_controller_class(self):
        return ReportImportBaseController
