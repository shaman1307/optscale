import json

from tools.optscale_exceptions.common_exc import NotFoundException
from tools.optscale_exceptions.http_exc import OptHTTPError

from rest_api.rest_api_server.controllers.invoice_month import (
    InvoiceMonthAsyncController)
from rest_api.rest_api_server.handlers.v1.base import BaseAuthHandler
from rest_api.rest_api_server.handlers.v1.base_async import BaseAsyncItemHandler
from rest_api.rest_api_server.handlers.v2.base import BaseHandler
from rest_api.rest_api_server.utils import run_task, ModelEncoder


class InvoiceMonthsAsyncHandler(BaseAsyncItemHandler, BaseAuthHandler,
                                BaseHandler):
    def _get_controller_class(self):
        return InvoiceMonthAsyncController

    async def get(self, organization_id):
        """
        ---
        description: |
            List GCP invoice months stored in ClickHouse for the organization.
            Required permission: INFO_ORGANIZATION or CLUSTER_SECRET
        tags: [expenses]
        summary: List available GCP invoice months
        parameters:
        -   name: organization_id
            in: path
            description: Organization id
            required: true
            type: string
        responses:
            200:
                description: Distinct invoice months (YYYYMM)
                schema:
                    type: object
                    properties:
                        invoice_months:
                            type: array
                            items:
                                type: string
                            example: ["202607", "202608"]
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
        security:
        - token: []
        - secret: []
        """
        if not self.check_cluster_secret(raises=False):
            await self.check_permissions(
                'INFO_ORGANIZATION', 'organization', organization_id)
        try:
            res = await run_task(self.controller.get, organization_id)
        except NotFoundException as exc:
            raise OptHTTPError.from_opt_exception(404, exc)
        self.write(json.dumps(res, cls=ModelEncoder))
