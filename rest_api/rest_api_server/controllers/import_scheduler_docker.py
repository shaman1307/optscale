import json
import logging
import os
import socket
from http.client import HTTPConnection

from tools.optscale_exceptions.common_exc import FailedDependency

from rest_api.rest_api_server.exceptions import Err

LOG = logging.getLogger(__name__)

SCHEDULER_SERVICES = (
    'report-import-scheduler-0',
    'report-import-scheduler-1',
    'report-import-scheduler-6',
    'report-import-scheduler-24',
)
COMPOSE_SERVICE_LABEL = 'com.docker.compose.service'
DOCKER_API = '/v1.41'
ENV_SOCK = 'IMPORT_SCHEDULER_DOCKER_SOCK'


class UnixHTTPConnection(HTTPConnection):
    def __init__(self, unix_socket, timeout=20):
        super().__init__('localhost', timeout=timeout)
        self.unix_socket = unix_socket

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.unix_socket)
        self.sock = sock


def docker_sock_path():
    return os.environ.get(ENV_SOCK) or ''


def docker_control_enabled():
    path = docker_sock_path()
    return bool(path) and os.path.exists(path)


def _docker_request(method, path, expected=(200, 204, 304)):
    conn = UnixHTTPConnection(docker_sock_path())
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status not in expected:
            snippet = body.decode('utf-8', errors='replace')[:300]
            raise FailedDependency(
                Err.OE0581,
                ['%s %s -> %s: %s' % (method, path, resp.status, snippet)])
        if not body:
            return None
        return json.loads(body)
    except FailedDependency:
        raise
    except Exception as exc:
        raise FailedDependency(Err.OE0581, [str(exc)])
    finally:
        conn.close()


def list_scheduler_containers():
    found = {name: [] for name in SCHEDULER_SERVICES}
    containers = _docker_request(
        'GET', '%s/containers/json?all=1' % DOCKER_API) or []
    for container in containers:
        labels = container.get('Labels') or {}
        service = labels.get(COMPOSE_SERVICE_LABEL)
        if service in found:
            found[service].append(container)
    return found


def schedulers_enabled():
    found = list_scheduler_containers()
    return all(
        any(container.get('State') == 'running' for container in found[name])
        for name in SCHEDULER_SERVICES
    )


def set_schedulers_enabled(enabled):
    found = list_scheduler_containers()
    missing = [name for name in SCHEDULER_SERVICES if not found[name]]
    if missing:
        raise FailedDependency(
            Err.OE0581,
            ['compose services not found: %s' % ', '.join(missing)])
    action = 'start' if enabled else 'stop'
    for name in SCHEDULER_SERVICES:
        for container in found[name]:
            state = container.get('State')
            if enabled and state == 'running':
                continue
            if not enabled and state != 'running':
                continue
            _docker_request(
                'POST',
                '%s/containers/%s/%s' % (
                    DOCKER_API, container.get('Id'), action),
                expected=(204, 304))
    LOG.info(
        'Import schedulers %s via docker: %s',
        'started' if enabled else 'stopped',
        ', '.join(SCHEDULER_SERVICES))
    return {'enabled': bool(enabled)}
