import logging

from tools.optscale_exceptions.common_exc import NotFoundException

from rest_api.rest_api_server.controllers.base import (
    BaseController, ClickHouseMixin)
from rest_api.rest_api_server.controllers.base_async import (
    BaseAsyncControllerWrapper)
from rest_api.rest_api_server.exceptions import Err
from rest_api.rest_api_server.models.enums import CloudTypes
from rest_api.rest_api_server.models.models import Organization, CloudAccount

LOG = logging.getLogger(__name__)


class InvoiceMonthController(BaseController, ClickHouseMixin):
    def get(self, organization_id):
        organization = self.session.query(Organization).filter(
            Organization.id == organization_id,
            Organization.deleted.is_(False),
        ).one_or_none()
        if not organization:
            raise NotFoundException(
                Err.OE0002, ['Organization', organization_id])
        gcp_ids = [
            row[0] for row in self.session.query(CloudAccount.id).filter(
                CloudAccount.organization_id == organization_id,
                CloudAccount.deleted.is_(False),
                CloudAccount.type.in_((
                    CloudTypes.GCP_CNR, CloudTypes.GCP_TENANT))
            ).all()
        ]
        if not gcp_ids:
            return {'invoice_months': []}
        rows = self.execute_clickhouse(
            query="""
                SELECT DISTINCT invoice_month
                FROM expenses
                WHERE cloud_account_id IN %(cloud_account_ids)s
                    AND invoice_month != ''
                ORDER BY invoice_month
            """,
            parameters={'cloud_account_ids': gcp_ids},
        )
        return {
            'invoice_months': [str(row[0]) for row in rows if row[0]]
        }


class InvoiceMonthAsyncController(BaseAsyncControllerWrapper):
    def _get_controller_class(self):
        return InvoiceMonthController
