#!/usr/bin/env python
import json
import os
import sys
import time
import logging
import base64
import faulthandler
import multiprocessing as mp
from collections import Counter, defaultdict
from urllib.error import URLError, HTTPError
from urllib.parse import quote, urlparse, unquote
from urllib.request import Request, urlopen

import urllib3
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, Thread
from etcd import Lock as EtcdLock
from kombu import Exchange, Queue, Connection as QConnection
from kombu.pools import producers
from pymongo import MongoClient
from urllib3.exceptions import InsecureRequestWarning
import clickhouse_connect

from optscale_client.config_client.client import Client as ConfigClient
from optscale_client.rest_api_client.client_v2 import Client as RestClient
from tools.optscale_time.optscale_time import startday, utcfromtimestamp
from tools.optscale_telemetry import OpenTelemetryConfig

from diworker.diworker.importers.base import BaseReportImporter
from diworker.diworker.importers.factory import get_importer_class
from diworker.diworker.migrator import Migrator
from optscale_data.report_import_queue import (
    LEGACY_REPORT_IMPORT_QUEUE,
    allocate_workers,
    classify_lost_imports,
    cloud_type_from_queue_name,
    report_import_enqueue_in_progress,
    report_import_queue_for_type,
    report_import_queue_names,
    should_drain_orphan_ready,
    valid_rabbit_import_ids,
)

ACTIVITIES_EXCHANGE_NAME = 'activities-tasks'
ALERT_THRESHOLD = 60 * 60 * 24
EXCHANGE_NAME = 'billing-reports'
task_exchange = Exchange(EXCHANGE_NAME, type='direct')

ARGUMENTS = {'x-max-priority': 10}
# One queue per cloud type (+ legacy shared queue) so providers progress in
# parallel instead of competing in a single FIFO.
task_queues = [
    Queue(
        queue_name,
        task_exchange,
        routing_key=queue_name,
        queue_arguments=ARGUMENTS,
    )
    for queue_name in report_import_queue_names(include_legacy=True)
]
TYPED_QUEUES = {
    cloud_type_from_queue_name(queue.name): queue
    for queue in task_queues
    if queue.name != LEGACY_REPORT_IMPORT_QUEUE
}
LEGACY_QUEUE = next(
    queue for queue in task_queues
    if queue.name == LEGACY_REPORT_IMPORT_QUEUE
)

LOG = logging.getLogger(__name__)
ENVIRONMENT_CLOUD_TYPE = 'environment'
HEARTBEAT_INTERVAL = 300
LOST_CLEANUP_INTERVAL_SECS = 60
ORPHAN_RABBIT_DRAIN_LIMIT = 1000
DISPATCH_IDLE_SLEEP_SECS = 0.5
DISPATCH_BUSY_SLEEP_SECS = 0.05
DEFAULT_MAX_WORKERS = 10
DEFAULT_MAX_TENANT_WORKERS = 1
DEFAULT_CSV_REWRITE_DAYS = 10
MIGRATIONS_READY_FILE = '/tmp/diworker-migrations-ready'
AMQP_SOCKET_TIMEOUT_SECS = 30.0


def _amqp_connection(conn_str):
    return QConnection(
        conn_str,
        connect_timeout=AMQP_SOCKET_TIMEOUT_SECS,
        transport_options={'socket_timeout': AMQP_SOCKET_TIMEOUT_SECS},
    )


def _is_rate_limit_exc(exc):
    if getattr(exc, 'status_code', None) == 429:
        return True
    msg = str(exc).lower()
    return '429' in msg or 'toomanyrequests' in msg or 'too many requests' in msg


def _signal_exit_reason(exitcode):
    """Human-readable reason for a non-zero import subprocess exit."""
    if exitcode is None:
        return 'Import process exited unexpectedly'
    # POSIX: exit = 128 + signal
    if exitcode == 139:
        return (
            'Import process killed by SIGSEGV (native crash in cffi/SSL); '
            'isolated so other imports keep running'
        )
    if exitcode == 132:
        return (
            'Import process killed by SIGILL (illegal instruction / '
            'corrupted native code); isolated so other imports keep running'
        )
    if exitcode > 128:
        return 'Import process killed by signal %s' % (exitcode - 128)
    return 'Import process exited with code %s' % exitcode


def _import_process_entry(body, config_cl_params, diworker_settings,
                          rabbitmq_conn_str):
    """Run one report import in an isolated process (spawn).

    Native crashes (cffi SIGSEGV/SIGILL) must not take down the parent
    diworker or sibling imports that share the ThreadPoolExecutor.
    """
    urllib3.disable_warnings(InsecureRequestWarning)
    faulthandler.enable()
    logging.basicConfig(
        level=logging.INFO,
        format='[import-proc %(process)d] %(levelname)s: %(message)s',
    )
    worker = DIWorker(
        connection=None,
        rabbitmq_conn_str=rabbitmq_conn_str,
        diworker_settings=diworker_settings,
        config_params=config_cl_params,
        start_background=False,
    )
    config_cl = worker.get_config_cl(config_cl_params)
    rest_cl = worker.get_rest_cl(config_cl)
    mongo_cl = worker.get_mongo_cl(config_cl)
    clickhouse_cl = worker.get_clickhouse_cl(config_cl)
    try:
        worker.report_import(
            body, config_cl=config_cl, rest_cl=rest_cl,
            mongo_cl=mongo_cl, clickhouse_cl=clickhouse_cl)
    except Exception as exc:
        LOG.exception('Data import failed: %s', str(exc))
        sys.exit(1)
    finally:
        try:
            mongo_cl.close()
        except Exception:
            pass
        try:
            clickhouse_cl.close()
        except Exception:
            pass
        try:
            rest_cl.close()
        except Exception:
            pass


class DIWorker:
    """Pulls report-import tasks with dynamic per-vendor worker quotas.

    Targets are ceil(depth_i / sum(depth) * max_workers) from DB unfinished
    counts (SCHEDULED + IN_PROGRESS). When a worker frees, quotas are
    recomputed and the next slot goes to the most under-allocated vendor.
    """

    def __init__(self, connection, rabbitmq_conn_str, diworker_settings,
                 config_params, start_background=True):
        self.connection = connection
        self.rabbitmq_conn_str = rabbitmq_conn_str
        self.diworker_settings = diworker_settings
        self.config_cl_params = config_params
        # Refcounts: a duplicate Rabbit delivery for the same import_id must
        # not discard ownership of the still-running holder (that false "lost"
        # mark failed+requeued live imports and re-wiped Mongo day windows).
        self.active_report_import_ids = Counter()
        self.active_by_type = defaultdict(int)
        # Import ids between basic_get and ack/requeue (may still be SCHEDULED).
        self.in_flight_import_ids = Counter()
        self.active_reports_lock = Lock()
        # Kombu Connection is not thread-safe: MainThread basic_get and worker
        # ack/close must not race on the same TCP connection (frame_end hangs).
        self.amqp_lock = Lock()
        self.running = True
        self.max_workers = int(
            self.diworker_settings.get(
                'max_report_imports_workers',
                DEFAULT_MAX_WORKERS
            )
        )
        self.cleanup_lock = Lock()
        self.thread = None
        self.executor = None
        if start_background:
            self.thread = Thread(
                target=self.heartbeat, args=(self.config_cl_params,))
            self.thread.start()
            self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
            self._declare_queues()

    def _declare_queues(self):
        with self.amqp_lock:
            with self.connection.channel() as channel:
                for queue in task_queues:
                    queue.declare(channel=channel)

    def _reconnect_amqp(self):
        """Recreate the shared RabbitMQ connection (caller holds amqp_lock)."""
        try:
            self.connection.close()
        except Exception:
            pass
        self.connection = _amqp_connection(self.rabbitmq_conn_str)
        self.connection.ensure_connection(max_retries=3)
        with self.connection.channel() as channel:
            for queue in task_queues:
                queue.declare(channel=channel)

    def heartbeat(self, config_params):
        config_cl = self.get_config_cl(config_params)
        rest_cl = self.get_rest_cl(config_cl)
        next_cleanup = 0
        next_hb = 0
        while self.running:
            now = time.time()
            if now >= next_cleanup:
                try:
                    self._cleanup_lost_imports(rest_cl)
                except Exception:
                    LOG.exception('Lost import cleanup failed')
                next_cleanup = now + LOST_CLEANUP_INTERVAL_SECS
            if now >= next_hb:
                with self.active_reports_lock:
                    report_import_ids = list(self.active_report_import_ids)
                for report_import_id in report_import_ids:
                    try:
                        rest_cl.report_import_update(report_import_id, {})
                    except Exception as e:
                        LOG.warning(
                            "Heartbeat update failed for %s: %s",
                            report_import_id, e)
                next_hb = now + HEARTBEAT_INTERVAL
            time.sleep(1)
        rest_cl.close()

    def _claim_import_id(self, counter, report_import_id):
        """Increment ownership/in-flight refcount for report_import_id."""
        if not report_import_id:
            return
        with self.active_reports_lock:
            counter[report_import_id] += 1

    def _release_import_id(self, counter, report_import_id):
        """Decrement refcount; drop key when the last holder releases."""
        if not report_import_id:
            return
        with self.active_reports_lock:
            if counter[report_import_id] <= 1:
                counter.pop(report_import_id, None)
            else:
                counter[report_import_id] -= 1

    def _track_in_flight(self, body):
        self._claim_import_id(
            self.in_flight_import_ids,
            (body or {}).get('report_import_id'))

    def _untrack_in_flight(self, body):
        self._release_import_id(
            self.in_flight_import_ids,
            (body or {}).get('report_import_id'))

    def _rabbit_ready_by_type(self):
        """Ready message counts for typed report-import queues."""
        counts = {}
        with self.amqp_lock:
            with self.connection.channel() as channel:
                for cloud_type, queue in TYPED_QUEUES.items():
                    try:
                        declared = channel.queue_declare(
                            queue=queue.name, passive=True)
                        if isinstance(declared, (tuple, list)):
                            counts[cloud_type] = int(declared[1])
                        else:
                            counts[cloud_type] = int(
                                getattr(declared, 'message_count', 0))
                    except Exception as exc:
                        LOG.warning(
                            'Passive declare failed for %s: %s',
                            queue.name, exc)
        return counts

    def _peek_rabbit_ready_ids(self, cloud_types=None):
        """Peek ready message import ids via RabbitMQ management API.

        Returns ``{cloud_type: set(import_id)}`` for queues that were peeked
        successfully. Missing keys mean peek failed for that type — callers
        should fall back to the empty-queue heuristic.
        """
        parsed = urlparse(self.rabbitmq_conn_str)
        user = unquote(parsed.username or 'guest')
        password = unquote(parsed.password or 'guest')
        host = parsed.hostname or 'rabbitmq'
        auth = base64.b64encode(
            ('%s:%s' % (user, password)).encode('utf-8')).decode('ascii')
        wanted = {
            str(cloud_type).lower()
            for cloud_type in (cloud_types or TYPED_QUEUES.keys())
        }
        result = {}
        for cloud_type, queue in TYPED_QUEUES.items():
            if cloud_type not in wanted:
                continue
            url = (
                'http://{host}:15672/api/queues/%2F/{queue}/get'.format(
                    host=host, queue=quote(queue.name, safe=''))
            )
            body = json.dumps({
                'count': 1000,
                'ackmode': 'ack_requeue_true',
                'encoding': 'auto',
            }).encode('utf-8')
            request = Request(
                url, data=body, method='POST',
                headers={
                    'Authorization': 'Basic %s' % auth,
                    'Content-Type': 'application/json',
                },
            )
            try:
                with urlopen(request, timeout=15) as response:
                    messages = json.loads(response.read().decode('utf-8'))
            except (URLError, HTTPError, TimeoutError, ValueError,
                    json.JSONDecodeError) as exc:
                LOG.warning(
                    'Rabbit peek failed for %s: %s', queue.name, exc)
                continue
            ids = set()
            for message in messages or ():
                payload = message.get('payload')
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        payload = {}
                report_import_id = (payload or {}).get('report_import_id')
                if report_import_id:
                    ids.add(report_import_id)
            result[cloud_type] = ids
        return result

    def _rabbit_unacked_by_type(self, cloud_types=None):
        """Unacked counts via RabbitMQ management API (ready peeks miss these)."""
        parsed = urlparse(self.rabbitmq_conn_str)
        user = unquote(parsed.username or 'guest')
        password = unquote(parsed.password or 'guest')
        host = parsed.hostname or 'rabbitmq'
        auth = base64.b64encode(
            ('%s:%s' % (user, password)).encode('utf-8')).decode('ascii')
        wanted = {
            str(cloud_type).lower()
            for cloud_type in (cloud_types or TYPED_QUEUES.keys())
        }
        counts = {}
        for cloud_type, queue in TYPED_QUEUES.items():
            if cloud_type not in wanted:
                continue
            url = (
                'http://{host}:15672/api/queues/%2F/{queue}'.format(
                    host=host, queue=quote(queue.name, safe=''))
            )
            request = Request(
                url,
                headers={'Authorization': 'Basic %s' % auth},
            )
            try:
                with urlopen(request, timeout=15) as response:
                    data = json.loads(response.read().decode('utf-8'))
                counts[cloud_type] = int(
                    data.get('messages_unacknowledged') or 0)
            except (URLError, HTTPError, TimeoutError, ValueError,
                    json.JSONDecodeError, TypeError) as exc:
                LOG.warning(
                    'Rabbit unacked stats failed for %s: %s', queue.name, exc)
        return counts

    def _cleanup_orphan_rabbit_messages(self, valid_ids,
                                        scheduled_by_type=None):
        """Ack Rabbit messages whose report_import_id is not keepable.

        After lost-import cleanup, DB rows may be failed while the AMQP
        payload remains. Dispatcher only pulls when REST depths > 0, so
        orphans would sit forever unless drained here.

        Do not basic_get a queue whose ready count is still explained by
        live SCHEDULED rows — that requeued every waiting message every
        minute and coincided with the 3h AMQP TTL dropping 42 GCP tasks.
        """
        valid_ids = set(valid_ids or ())
        scheduled_by_type = {
            str(k).lower(): int(v)
            for k, v in (scheduled_by_type or {}).items()
        }
        try:
            rabbit_ready = self._rabbit_ready_by_type()
        except Exception as exc:
            LOG.warning('Orphan rabbit cleanup: ready counts failed: %s', exc)
            return 0
        queues = list(TYPED_QUEUES.items())
        queues.append(('legacy', LEGACY_QUEUE))
        purged = 0
        kept = 0
        for cloud_type, queue in queues:
            ready = int(rabbit_ready.get(cloud_type, 0) or 0)
            if cloud_type != 'legacy' and not should_drain_orphan_ready(
                    ready, scheduled_by_type.get(cloud_type, 0)):
                continue
            if cloud_type == 'legacy':
                try:
                    with self.amqp_lock:
                        with self.connection.channel() as channel:
                            declared = channel.queue_declare(
                                queue=queue.name, passive=True)
                            if isinstance(declared, (tuple, list)):
                                ready = int(declared[1])
                            else:
                                ready = int(
                                    getattr(declared, 'message_count', 0))
                except Exception as exc:
                    LOG.warning(
                        'Orphan rabbit cleanup: legacy ready failed: %s', exc)
                    ready = 0
            if ready <= 0:
                continue
            limit = min(ready, ORPHAN_RABBIT_DRAIN_LIMIT)
            drained = []
            for _ in range(limit):
                got = self._basic_get(queue)
                if got is None:
                    break
                drained.append(got)
            for body, message, channel in drained:
                report_import_id = (body or {}).get('report_import_id')
                if report_import_id and report_import_id in valid_ids:
                    self._requeue_message(message)
                    kept += 1
                else:
                    self._ack_message(message)
                    purged += 1
                    LOG.info(
                        'Purged orphan Rabbit message for report import %s '
                        'from %s',
                        report_import_id or '<missing>', queue.name)
                self._close_channel(channel)
        if purged or kept:
            LOG.info(
                'Orphan Rabbit cleanup purged=%s kept=%s', purged, kept)
        return purged

    def _cleanup_lost_imports(self, rest_cl):
        """Fail lost DB rows and purge orphan Rabbit messages.

        Distinct from rest_api fail_stale_imports (age thresholds). Runs on
        startup and periodically from the heartbeat thread.
        """
        if not self.cleanup_lock.acquire(blocking=False):
            return 0
        try:
            return self._cleanup_lost_imports_locked(rest_cl)
        finally:
            self.cleanup_lock.release()

    def _cleanup_lost_imports_locked(self, rest_cl):
        try:
            _, resp = rest_cl.report_import_queue_stats()
        except Exception as exc:
            LOG.warning('Lost cleanup: queue stats failed: %s', exc)
            # Do not purge Rabbit without a successful REST view of
            # unfinished imports — would risk dropping live SCHEDULED tasks.
            return 0
        unfinished = resp.get('unfinished') or []
        now_ts = time.time()
        if report_import_enqueue_in_progress(unfinished, now_ts=now_ts):
            LOG.info(
                'Lost import cleanup skipped: report import enqueue '
                'in progress')
            return 0
        try:
            rabbit_ready = self._rabbit_ready_by_type()
        except Exception as exc:
            LOG.warning(
                'Lost cleanup: rabbit ready counts failed: %s', exc)
            rabbit_ready = {}
        scheduled_types = {
            str(item.get('cloud_type') or '').lower()
            for item in unfinished
            if str(item.get('state') or '').lower() == 'scheduled'
            and item.get('cloud_type')
        }
        rabbit_unacked = {}
        if scheduled_types:
            try:
                rabbit_unacked = self._rabbit_unacked_by_type(
                    scheduled_types)
            except Exception as exc:
                LOG.warning(
                    'Lost cleanup: rabbit unacked failed: %s', exc)
                rabbit_unacked = {}
        with self.active_reports_lock:
            owned_ids = set(self.active_report_import_ids)
            in_flight_ids = set(self.in_flight_import_ids)
            active_by_type = dict(self.active_by_type)
        # Rabbit I/O takes seconds; rest_api may still be publishing.
        try:
            _, resp = rest_cl.report_import_queue_stats()
        except Exception as extra:
            LOG.warning('Lost cleanup: queue stats refresh failed: %s', extra)
            return 0
        unfinished = resp.get('unfinished') or []
        now_ts = time.time()
        if report_import_enqueue_in_progress(unfinished, now_ts=now_ts):
            LOG.info(
                'Lost import cleanup skipped: report import enqueue '
                'in progress')
            return 0
        lost = classify_lost_imports(
            unfinished, owned_ids, in_flight_ids, rabbit_ready,
            active_by_type,
            rabbit_unacked_by_type=rabbit_unacked) if unfinished else []
        failed = 0
        lost_ids = set()
        requeue_account_ids = set()
        republish_scheduled = []
        unfinished_by_id = {
            (item or {}).get('id'): item for item in (unfinished or ())
            if (item or {}).get('id')
        }
        for report_import_id, reason in lost:
            item = unfinished_by_id.get(report_import_id) or {}
            # SCHEDULED row is still valid — only the AMQP payload is gone.
            # Do not fail it; republish the same import id.
            if 'Lost SCHEDULED' in reason:
                republish_scheduled.append((report_import_id, item))
                continue
            try:
                rest_cl.report_import_update(
                    report_import_id,
                    {'state': 'failed', 'state_reason': reason},
                )
                failed += 1
                lost_ids.add(report_import_id)
                LOG.warning(
                    'Failed lost report import %s: %s',
                    report_import_id, reason)
                ca_id = item.get('cloud_account_id')
                # Crash/restart left IN_PROGRESS without an owner — re-enqueue
                # so first-load / long Clean jobs are not silently dropped.
                if ca_id and 'Lost IN_PROGRESS' in reason:
                    requeue_account_ids.add(ca_id)
            except Exception as exc:
                LOG.warning(
                    'Failed to mark lost import %s: %s',
                    report_import_id, exc)
        if failed:
            LOG.info('Lost import cleanup marked %s import(s) failed', failed)
        scheduled_by_type = {}
        for item in unfinished or ():
            if str((item or {}).get('state') or '').lower() != 'scheduled':
                continue
            cloud_type = str((item or {}).get('cloud_type') or '').lower()
            if not cloud_type:
                continue
            scheduled_by_type[cloud_type] = (
                scheduled_by_type.get(cloud_type, 0) + 1)
        # Purge orphans BEFORE re-queue: otherwise the freshly published
        # Rabbit message is not in valid_ids (stale unfinished snapshot) and
        # gets deleted immediately — leaving a stuck SCHEDULED row.
        valid_ids = valid_rabbit_import_ids(
            unfinished, owned_ids, in_flight_ids, exclude_ids=lost_ids)
        try:
            self._cleanup_orphan_rabbit_messages(
                valid_ids, scheduled_by_type=scheduled_by_type)
        except Exception:
            LOG.exception('Orphan Rabbit cleanup failed')
        for report_import_id, item in republish_scheduled:
            cloud_type = str(item.get('cloud_type') or '').lower()
            try:
                self._republish_scheduled_import(report_import_id, cloud_type)
                LOG.warning(
                    'Re-published lost SCHEDULED report import %s to %s',
                    report_import_id, report_import_queue_for_type(cloud_type))
            except Exception as exc:
                LOG.warning(
                    'Failed to re-publish lost SCHEDULED %s: %s',
                    report_import_id, exc)
        for cloud_account_id in sorted(requeue_account_ids):
            try:
                code, resp = rest_cl.schedule_import(
                    cloud_account_id=cloud_account_id, priority=8)
                LOG.info(
                    'Re-queued lost IN_PROGRESS for %s (http=%s)',
                    cloud_account_id, code)
            except Exception as exc:
                LOG.warning(
                    'Failed to re-queue lost import for %s: %s',
                    cloud_account_id, exc)
        return failed

    def _republish_scheduled_import(self, report_import_id, cloud_type,
                                    priority=8):
        """Put an existing SCHEDULED import back on its typed Rabbit queue.

        Used when the AMQP TTL expired (or the message was acked) but the
        MariaDB row is still SCHEDULED. No expiration: the DB row is the
        source of truth until the worker claims it.
        """
        if not report_import_id or not cloud_type:
            return
        queue_name = report_import_queue_for_type(cloud_type)
        task_queue = Queue(
            queue_name,
            task_exchange,
            routing_key=queue_name,
            queue_arguments=ARGUMENTS,
        )
        queue_conn = QConnection(self.rabbitmq_conn_str)
        with producers[queue_conn].acquire(block=True) as producer:
            producer.publish(
                {'report_import_id': report_import_id},
                serializer='json',
                exchange=task_exchange,
                declare=[task_exchange, task_queue],
                routing_key=queue_name,
                retry=True,
                priority=priority,
            )

    @staticmethod
    def get_config_cl(config_params):
        return ConfigClient(**config_params)

    @staticmethod
    def get_rest_cl(config_cl):
        url = config_cl.restapi_url()
        secret = config_cl.cluster_secret()
        return RestClient(url=url, verify=False, secret=secret)

    @staticmethod
    def get_mongo_cl(config_cl):
        mongo_params = config_cl.mongo_params()
        return MongoClient(mongo_params[0])

    @staticmethod
    def get_clickhouse_cl(config_cl):
        user, password, host, db_name, port, secure = (
            config_cl.clickhouse_params())
        return clickhouse_connect.get_client(
            host=host, password=password, database=db_name, user=user,
            port=port, secure=secure)

    def publish_activities_task(self, organization_id, object_id, object_type,
                                action, routing_key, meta=None):
        task = {
            'organization_id': organization_id,
            'object_id': object_id,
            'object_type': object_type,
            'action': action,
            'meta': meta
        }
        queue_conn = QConnection(self.rabbitmq_conn_str)
        published_exchange = Exchange(ACTIVITIES_EXCHANGE_NAME, type='topic')
        with producers[queue_conn].acquire(block=True) as producer:
            producer.publish(
                task,
                serializer='json',
                exchange=published_exchange,
                declare=[published_exchange],
                routing_key=routing_key,
                retry=True
            )

    def _fetch_depths(self, rest_cl):
        try:
            _, resp = rest_cl.report_import_queue_stats()
            depths = resp.get('depths') or {}
            return {
                str(cloud_type).lower(): int(depth)
                for cloud_type, depth in depths.items()
                if depth
            }
        except Exception as exc:
            LOG.warning('Failed to fetch import queue depths: %s', exc)
            with self.active_reports_lock:
                return {
                    cloud_type: count
                    for cloud_type, count in self.active_by_type.items()
                    if count
                }

    def _basic_get(self, queue):
        """Fetch one message without blocking.

        Returns ``(body, message, channel)`` or None. The channel must stay
        open until the message is acked/rejected — closing it early requeues
        the message and causes head-of-line thrashing on the same task.
        """
        for attempt in range(2):
            channel = None
            with self.amqp_lock:
                try:
                    channel = self.connection.channel()
                    bound = queue(channel)
                    message = bound.get(no_ack=False)
                    if message is None:
                        channel.close()
                        return None
                    body = message.payload
                    if isinstance(body, (bytes, bytearray)):
                        body = json.loads(body)
                    return body, message, channel
                except Exception as exc:
                    LOG.warning(
                        'basic_get failed for %s (attempt %s): %s',
                        queue.name, attempt + 1, exc)
                    if channel is not None:
                        try:
                            channel.close()
                        except Exception:
                            pass
                    if attempt == 0:
                        try:
                            self._reconnect_amqp()
                        except Exception as reconnect_exc:
                            LOG.warning(
                                'AMQP reconnect failed: %s', reconnect_exc)
                            return None
        return None

    def _ack_message(self, message):
        if message is None:
            return
        with self.amqp_lock:
            try:
                message.ack()
            except Exception as exc:
                LOG.warning('Failed to ack message: %s', exc)

    def _requeue_message(self, message):
        if message is None:
            return
        with self.amqp_lock:
            try:
                message.requeue()
            except Exception as exc:
                LOG.warning('Failed to requeue message: %s', exc)
                try:
                    message.reject(requeue=True)
                except Exception:
                    pass

    def _close_channel(self, channel):
        if channel is None:
            return
        with self.amqp_lock:
            try:
                channel.close()
            except Exception:
                pass

    def _pick_next_cloud_type(self, targets):
        """Return cloud_type most under its target, or None."""
        with self.active_reports_lock:
            active = dict(self.active_by_type)
            busy = sum(active.values())
        if busy >= self.max_workers:
            return None
        best = None
        best_key = None
        for cloud_type, target in targets.items():
            deficit = target - active.get(cloud_type, 0)
            if deficit <= 0:
                continue
            key = (deficit, target, cloud_type)
            if best_key is None or key > best_key:
                best_key = key
                best = cloud_type
        return best

    def _reserve_slot(self, cloud_type):
        with self.active_reports_lock:
            if sum(self.active_by_type.values()) >= self.max_workers:
                return False
            self.active_by_type[cloud_type] += 1
            return True

    def _release_slot(self, cloud_type):
        with self.active_reports_lock:
            if cloud_type in self.active_by_type:
                self.active_by_type[cloud_type] -= 1
                if self.active_by_type[cloud_type] <= 0:
                    del self.active_by_type[cloud_type]

    def _start_message(self, body, message, cloud_type, channel):
        if not self._reserve_slot(cloud_type):
            try:
                self._requeue_message(message)
            finally:
                self._untrack_in_flight(body)
                self._close_channel(channel)
            return False

        def _done(future):
            self._release_slot(cloud_type)
            try:
                future.result()
            except Exception:
                pass

        try:
            future = self.executor.submit(
                self._process_task, body, message, channel)
            future.add_done_callback(_done)
            return True
        except Exception:
            self._release_slot(cloud_type)
            self._untrack_in_flight(body)
            self._close_channel(channel)
            raise

    def _dispatch_once(self, rest_cl):
        depths = self._fetch_depths(rest_cl)
        targets = allocate_workers(depths, self.max_workers)
        if targets:
            with self.active_reports_lock:
                active_snapshot = dict(self.active_by_type)
            LOG.info(
                'Worker targets=%s depths=%s active=%s',
                targets, depths, active_snapshot)

        started = 0
        # Fill under-allocated vendor queues first.
        while True:
            cloud_type = self._pick_next_cloud_type(targets)
            if cloud_type is None:
                break
            queue = TYPED_QUEUES.get(cloud_type)
            if queue is None:
                break
            got = self._basic_get(queue)
            if got is None:
                # No rabbit message for this type right now; avoid tight loop
                # on the same empty queue in this cycle.
                targets.pop(cloud_type, None)
                if not targets:
                    break
                continue
            body, message, channel = got
            self._track_in_flight(body)
            if self._start_message(body, message, cloud_type, channel):
                started += 1
            else:
                break

        # Slow-drain legacy queue when typed quotas are satisfied / empty.
        with self.active_reports_lock:
            free = self.max_workers - sum(self.active_by_type.values())
        if free > 0:
            got = self._basic_get(LEGACY_QUEUE)
            if got is not None:
                body, message, channel = got
                self._track_in_flight(body)
                cloud_type = self._cloud_type_for_task(body, rest_cl) or 'legacy'
                if self._start_message(body, message, cloud_type, channel):
                    started += 1

        return started

    def _cloud_type_for_task(self, body, rest_cl):
        report_import_id = (body or {}).get('report_import_id')
        if not report_import_id:
            return None
        try:
            _, import_dict = rest_cl.report_import_get(report_import_id)
            cloud_account_id = import_dict.get('cloud_account_id')
            if not cloud_account_id:
                return None
            _, ca = rest_cl.cloud_account_get(cloud_account_id)
            return (ca.get('type') or '').lower() or None
        except Exception as exc:
            LOG.warning('Failed to resolve cloud type for %s: %s',
                        report_import_id, exc)
            return None

    def run(self):
        LOG.info(
            'DIWorker dispatcher started (max_workers=%s)', self.max_workers)
        config_cl = self.get_config_cl(self.config_cl_params)
        rest_cl = self.get_rest_cl(config_cl)
        try:
            try:
                self._cleanup_lost_imports(rest_cl)
            except Exception:
                LOG.exception('Initial lost import cleanup failed')
            while self.running:
                try:
                    started = self._dispatch_once(rest_cl)
                except Exception:
                    LOG.exception('Dispatch cycle failed')
                    started = 0
                time.sleep(
                    DISPATCH_BUSY_SLEEP_SECS if started
                    else DISPATCH_IDLE_SLEEP_SECS
                )
        finally:
            rest_cl.close()

    def report_import(self, task, config_cl, rest_cl, mongo_cl, clickhouse_cl):
        report_import_id = task.get('report_import_id')
        if not report_import_id:
            raise Exception('invalid task received: {}'.format(task))

        # REST must not run under active_reports_lock: a slow/hung API call
        # would freeze the MainThread dispatcher (slot pick / reserve).
        _, import_dict = rest_cl.report_import_get(report_import_id)
        if import_dict.get('state') != 'scheduled':
            LOG.info(
                'Skip task for report import %s in state %s',
                report_import_id, import_dict.get('state'))
            return
        cloud_acc_id = import_dict.get('cloud_account_id')
        # If another unfinished import already exists for this account
        # (enqueue race / legacy duplicate message), fail this task.
        # Do not keep a local "one worker per account" lock — worker
        # slots are shared by vendor depth only.
        _, resp = rest_cl.report_import_list(
            cloud_acc_id, show_active=True)
        imports = list(filter(
            lambda x: x['id'] != report_import_id, resp['report_imports']))
        if imports:
            reason = (
                'Import cancelled due another import: %s' % imports[0]['id'])
            rest_cl.report_import_update(
                report_import_id,
                {'state': 'failed', 'state_reason': reason}
            )
            return
        is_recalculation = import_dict.get('is_recalculation', False)
        self._claim_import_id(self.active_report_import_ids, report_import_id)
        LOG.info('Starting processing for task: %s, purpose %s',
                 task, 'recalculation ' if is_recalculation else 'import')
        rest_cl.report_import_update(
            report_import_id, {'state': 'in_progress'})

        importer_params = {
            'cloud_account_id': cloud_acc_id,
            'rest_cl': rest_cl,
            'config_cl': config_cl,
            'mongo_raw': mongo_cl.restapi['raw_expenses'],
            'mongo_resources': mongo_cl.restapi['resources'],
            'clickhouse_cl': clickhouse_cl,
            'import_file': import_dict.get('import_file'),
            'recalculate': is_recalculation,
            'max_tenant_concurrent': int(self.diworker_settings.get(
                'max_tenant_import_workers', DEFAULT_MAX_TENANT_WORKERS)),
            'csv_rewrite_days': int(self.diworker_settings.get(
                'csv_rewrite_days') or DEFAULT_CSV_REWRITE_DAYS)
        }
        importer = None
        ca = None
        previous_attempt_ts = 0
        try:
            _, ca = rest_cl.cloud_account_get(
                importer_params.get('cloud_account_id'))
            organization_id = ca.get('organization_id')
            _, org = rest_cl.organization_get(organization_id)
            if org.get('disabled'):
                reason = ('Import cancelled due to disabled '
                          'organization: %s') % report_import_id
                rest_cl.report_import_update(
                    report_import_id,
                    {'state': 'failed', 'state_reason': reason}
                )
                return
            start_last_import_ts = ca.get('last_import_at', 0)
            previous_attempt_ts = ca.get('last_import_attempt_at', 0)
            cc_type = ca.get('type')
            export_scheme = ca['config'].get('expense_import_scheme')
            importer = get_importer_class(cc_type, export_scheme)(
                **importer_params)
            importer.report_import_id = report_import_id
            importer.import_report()
            completed_payload = {'state': 'completed'}
            get_details = getattr(importer, 'get_import_details', None)
            if callable(get_details):
                details = get_details()
                if details:
                    completed_payload['details'] = details
            rest_cl.report_import_update(report_import_id, completed_payload)
            if start_last_import_ts == 0 and cc_type != ENVIRONMENT_CLOUD_TYPE:
                all_reports_finished = True
                _, resp = rest_cl.cloud_account_list(organization_id)
                for acc in resp['cloud_accounts']:
                    if (acc['type'] != ENVIRONMENT_CLOUD_TYPE and
                            acc['last_import_at'] == 0):
                        all_reports_finished = False
                        break
                if all_reports_finished:
                    self.publish_activities_task(
                        organization_id, organization_id, 'organization',
                        'report_import_passed',
                        'organization.report_import.passed')
        except Exception as exc:
            if hasattr(exc, 'details'):
                # pylint: disable=E1101
                LOG.error('Mongo exception details: %s', exc.details)
            reason = str(exc)
            try:
                failed_payload = {'state': 'failed', 'state_reason': reason}
                get_details = getattr(importer, 'get_import_details', None)
                if importer is not None and callable(get_details):
                    details = get_details()
                    if details:
                        failed_payload['details'] = details
                rest_cl.report_import_update(report_import_id, failed_payload)
            except Exception as mark_exc:
                LOG.error(
                    'Failed to mark report import %s as failed after %s: %s',
                    report_import_id, reason, mark_exc)
            now = int(time.time())
            if not importer:
                importer = BaseReportImporter(**importer_params)
            try:
                importer.update_cloud_import_attempt(now, reason)
            except Exception as attempt_exc:
                LOG.error(
                    'Failed to record import attempt error for %s: %s',
                    cloud_acc_id, attempt_exc)
            self.send_report_failed_email(
                ca, previous_attempt_ts, now,
                is_throttled=_is_rate_limit_exc(exc))
            raise
        finally:
            self._release_import_id(
                self.active_report_import_ids, report_import_id)

    def send_report_failed_email(self, cloud_account, previous_attempt_ts,
                                 now, is_throttled=False):
        if not cloud_account:
            return
        last_import_at = cloud_account['last_import_at']
        if not last_import_at:
            last_import_at = cloud_account['created_at']
        if now - last_import_at < ALERT_THRESHOLD:
            return
        if last_import_at < previous_attempt_ts:
            if startday(utcfromtimestamp(previous_attempt_ts)) == startday(
                    utcfromtimestamp(now)):
                return
        action = ('report_import_throttled' if is_throttled
                  else 'report_import_failed')
        self.publish_activities_task(
            cloud_account['organization_id'], cloud_account['id'],
            'cloud_account', action,
            'organization.report_import.failed')

    def _handle_native_crash(self, body, exitcode):
        """Mark crashed import failed and re-enqueue so work is not lost."""
        report_import_id = (body or {}).get('report_import_id')
        if not report_import_id:
            return
        reason = _signal_exit_reason(exitcode)
        config_cl = self.get_config_cl(self.config_cl_params)
        rest_cl = self.get_rest_cl(config_cl)
        cloud_account_id = None
        try:
            _, import_dict = rest_cl.report_import_get(report_import_id)
            cloud_account_id = import_dict.get('cloud_account_id')
            state = str(import_dict.get('state') or '').lower()
            if state in ('scheduled', 'in_progress'):
                rest_cl.report_import_update(
                    report_import_id,
                    {'state': 'failed', 'state_reason': reason},
                )
                LOG.error(
                    'Marked crashed import %s failed: %s',
                    report_import_id, reason)
        except Exception as exc:
            LOG.warning(
                'Failed to mark crashed import %s: %s',
                report_import_id, exc)
        if cloud_account_id:
            try:
                code, resp = rest_cl.schedule_import(
                    cloud_account_id=cloud_account_id, priority=8)
                LOG.info(
                    'Re-queued import for %s after native crash '
                    '(http=%s resp=%s)',
                    cloud_account_id, code, resp)
            except Exception as exc:
                LOG.warning(
                    'Failed to re-queue import for %s after crash: %s',
                    cloud_account_id, exc)
        try:
            rest_cl.close()
        except Exception:
            pass

    def _process_task(self, body, message, channel):
        # Keep heartbeat ownership in the parent while the child process runs.
        # Use refcounts: a duplicate delivery that skips must not drop the
        # live holder from owned_ids (heartbeat would false-fail+requeue).
        report_import_id = (body or {}).get('report_import_id')
        self._claim_import_id(self.active_report_import_ids, report_import_id)
        proc = None
        try:
            # spawn: safe from a multi-threaded parent; isolates cffi/OpenSSL
            # crashes so one import cannot SIGSEGV the whole diworker.
            ctx = mp.get_context('spawn')
            proc = ctx.Process(
                target=_import_process_entry,
                args=(
                    body,
                    self.config_cl_params,
                    dict(self.diworker_settings),
                    self.rabbitmq_conn_str,
                ),
                name='import-%s' % ((report_import_id or 'unknown')[:8],),
            )
            proc.start()
            while proc.is_alive():
                proc.join(timeout=5)
            if proc.exitcode not in (0, None):
                LOG.error(
                    'Import subprocess crashed exit=%s task=%s',
                    proc.exitcode, body)
                # Application failures exit(1) after marking FAILED themselves.
                # Signal deaths (128+N) leave IN_PROGRESS — recover here.
                if proc.exitcode > 128:
                    self._handle_native_crash(body, proc.exitcode)
        except Exception as exc:
            LOG.exception('Failed to run import subprocess: %s', str(exc))
            if proc is not None and proc.is_alive():
                try:
                    proc.terminate()
                    proc.join(timeout=10)
                except Exception:
                    pass
        finally:
            self._release_import_id(
                self.active_report_import_ids, report_import_id)
            self._untrack_in_flight(body)
            self._ack_message(message)
            self._close_channel(channel)


if __name__ == '__main__':
    urllib3.disable_warnings(InsecureRequestWarning)
    faulthandler.enable()
    logging.basicConfig(
        level=logging.INFO,
        format='[%(threadName)s] %(levelname)s: %(message)s'
    )

    config_cl_params = {
        'host': os.environ.get('HX_ETCD_HOST'),
        'port': int(os.environ.get('HX_ETCD_PORT'))
    }
    config_cl = ConfigClient(**config_cl_params)
    config_cl.wait_configured()
    migrator = Migrator(config_cl, 'restapi', 'diworker/diworker/migrations')
    with EtcdLock(config_cl, 'diworker_migrations'):
        migrator.migrate()
    with open(MIGRATIONS_READY_FILE, 'w') as ready_file:
        ready_file.write('ready\n')
    LOG.info("starting worker")
    conn_str = 'amqp://{user}:{pass}@{host}:{port}'.format(
        **config_cl.read_branch('/rabbit'))
    dw_settings = config_cl.diworker_settings()
    with _amqp_connection(conn_str) as conn:
        config = OpenTelemetryConfig(
            service_name=os.getenv("OTEL_SERVICE_NAME", "diworker"),
            service_version=os.getenv("OTEL_SERVICE_VERSION", "local"),
            otel_config=config_cl.read_branch("/opentelemetry"),
            service_config=config_cl.read_branch("diworker/opentelemetry"),
        )
        config.setup_open_telemetry()
        try:
            worker = DIWorker(conn, conn_str, dw_settings, config_cl_params)
            worker.run()
        except KeyboardInterrupt:
            worker.running = False
            worker.executor.shutdown(wait=True)
            worker.thread.join()
            LOG.info("Shutdown worker")
