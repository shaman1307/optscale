import json

from tools.optscale_exceptions.common_exc import (
    NotFoundException, WrongArgumentsException)
from tools.optscale_exceptions.http_exc import OptHTTPError

from rest_api.rest_api_server.controllers.report_import import (
    ReportImportScheduleAsyncController)
from rest_api.rest_api_server.handlers.v1.base import BaseAuthHandler
from rest_api.rest_api_server.handlers.v1.base_async import BaseAsyncItemHandler
from rest_api.rest_api_server.handlers.v2.base import BaseHandler
from rest_api.rest_api_server.utils import (
    run_task, ModelEncoder, check_bool_attribute, raise_not_provided_exception,
    raise_unexpected_exception)


class ImportSchedulerAsyncHandler(BaseAsyncItemHandler, BaseAuthHandler,
                                  BaseHandler):
    def _get_controller_class(self):
        return ReportImportScheduleAsyncController

    def _validate_params(self, item=None, **kwargs):
        unexpected = [key for key in kwargs if key != 'enabled']
        if unexpected:
            raise_unexpected_exception(unexpected)
        if 'enabled' not in kwargs:
            raise_not_provided_exception('enabled')
        check_bool_attribute('enabled', kwargs['enabled'])

    async def get(self, organization_id, **kwargs):
        """
        ---
        description: |
            Get whether period-based import schedulers are running.
            Reads docker state of report-import-scheduler-0/1/6/24 when
            IMPORT_SCHEDULER_DOCKER_SOCK is set; otherwise the etcd flag.
            Required permission: INFO_ORGANIZATION or CLUSTER_SECRET
        tags: [report_imports]
        summary: Import schedulers status
        parameters:
        -   name: organization_id
            in: path
            required: true
            type: string
        responses:
            200:
                description: Scheduler status
                schema:
                    type: object
                    properties:
                        enabled:
                            type: boolean
            401:
                description: |
                    Unauthorized:
                    - OE0235: Unauthorized
            403:
                description: |
                    Forbidden:
                    - OE0234: Forbidden
            404:
                description: |
                    Not found:
                    - OE0002: Organization not found
            424:
                description: |
                    Failed dependency:
                    - OE0581: Failed to control import schedulers
        security:
        - token: []
        - secret: []
        """
        if not self.check_cluster_secret(raises=False):
            await self.check_permissions(
                'INFO_ORGANIZATION', 'organization', organization_id)
        try:
            res = await run_task(
                self.controller.scheduler_status, organization_id)
        except NotFoundException as exc:
            raise OptHTTPError.from_opt_exception(404, exc)
        self.write(json.dumps(res, cls=ModelEncoder))

    async def patch(self, organization_id, **kwargs):
        """
        ---
        description: |
            Start or stop all period-based import schedulers
            (report-import-scheduler-0/1/6/24). When docker control is
            enabled this starts/stops those compose services. Manual
            imports by cloud_account_id still run.
            Required permission: MANAGE_CLOUD_CREDENTIALS
        tags: [report_imports]
        summary: Start or stop import schedulers
        parameters:
        -   name: organization_id
            in: path
            required: true
            type: string
        -   in: body
            name: body
            required: true
            schema:
                type: object
                properties:
                    enabled:
                        type: boolean
                        description: true starts the scheduler, false stops it
        responses:
            200:
                description: Updated scheduler status
                schema:
                    type: object
                    properties:
                        enabled:
                            type: boolean
            400:
                description: |
                    Wrong arguments:
                    - OE0212: Unexpected parameters
                    - OE0216: Argument is not provided
                    - OE0226: Argument should be True or False
            401:
                description: |
                    Unauthorized:
                    - OE0235: Unauthorized
            403:
                description: |
                    Forbidden:
                    - OE0234: Forbidden
            404:
                description: |
                    Not found:
                    - OE0002: Organization not found
            424:
                description: |
                    Failed dependency:
                    - OE0581: Failed to control import schedulers
        security:
        - token: []
        """
        await self.check_permissions(
            'MANAGE_CLOUD_CREDENTIALS', 'organization', organization_id)
        data = self._request_body()
        try:
            self._validate_params(**data)
            res = await run_task(
                self.controller.set_scheduler_enabled,
                organization_id, data['enabled'])
        except WrongArgumentsException as exc:
            raise OptHTTPError.from_opt_exception(400, exc)
        except NotFoundException as exc:
            raise OptHTTPError.from_opt_exception(404, exc)
        self.write(json.dumps(res, cls=ModelEncoder))
