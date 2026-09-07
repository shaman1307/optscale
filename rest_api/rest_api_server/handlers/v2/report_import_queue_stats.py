import json
import logging

from rest_api.rest_api_server.controllers.report_import import (
    ReportImportQueueStatsAsyncController)
from rest_api.rest_api_server.handlers.v1.base import BaseAuthHandler
from rest_api.rest_api_server.handlers.v1.base_async import BaseAsyncCollectionHandler
from rest_api.rest_api_server.utils import run_task, ModelEncoder

LOG = logging.getLogger(__name__)


class ReportImportQueueStatsAsyncHandler(BaseAsyncCollectionHandler,
                                         BaseAuthHandler):
    def _get_controller_class(self):
        return ReportImportQueueStatsAsyncController

    def post(self, *args, **kwargs):
        self.raise405()

    async def get(self):
        """
        ---
        description: |
            Unfinished report-import counts by cloud account type
            (SCHEDULED + IN_PROGRESS). Used by diworker to allocate workers
            and to detect lost imports (unfinished list).
            Required permission: CLUSTER SECRET
        tags: [report_imports]
        summary: Report import queue depths by vendor
        responses:
            200:
                description: Queue depths and unfinished imports
                schema:
                    type: object
                    properties:
                        depths:
                            type: object
                            additionalProperties:
                                type: integer
                        unfinished:
                            type: array
                            items:
                                type: object
                                properties:
                                    id:
                                        type: string
                                    state:
                                        type: string
                                    cloud_type:
                                        type: string
                                    created_at:
                                        type: integer
            401:
                description: |
                    Unauthorized:
                    - OE0237: This resource requires authorization
            403:
                description: |
                    Forbidden:
                    - OE0236: Bad secret
        security:
        - secret: []
        """
        self.check_cluster_secret()
        depths = await run_task(self.controller.unfinished_import_depths_by_type)
        unfinished = await run_task(self.controller.unfinished_imports)
        self.write(json.dumps(
            {'depths': depths, 'unfinished': unfinished}, cls=ModelEncoder))
