import logging

import clickhouse_connect

from diworker.diworker.migrations.base import BaseMigration

"""
Collapse leftover invoice_month='' ClickHouse expenses that already have a
billed YYYYMM sibling for the same (cloud_account_id, resource_id, date).

The invoice_month ORDER BY migration copied existing rows with ''. Later
regenerator writes billed-month keys, which do not collapse against ''.
Calendar-date totals (Data Source last month, Resources by date) then
double-count. Billing-month queries already ignore ''.
"""

LOG = logging.getLogger(__name__)


class Migration(BaseMigration):
    def _get_clickhouse_client(self):
        user, password, host, db_name, port, secure = (
            self.config_cl.clickhouse_params())
        return clickhouse_connect.get_client(
            host=host, password=password, database=db_name, user=user,
            port=port, secure=secure)

    def upgrade(self):
        clickhouse_client = self._get_clickhouse_client()
        LOG.info(
            'Collapsing stale invoice_month=\'\' expenses that have a '
            'billed-month sibling')
        clickhouse_client.query(
            """
            INSERT INTO expenses
            SELECT
                empty.cloud_account_id,
                empty.resource_id,
                empty.date,
                sum(empty.cost * empty.sign) AS cost,
                toInt8(-1) AS sign,
                '' AS invoice_month
            FROM expenses AS empty
            INNER JOIN (
                SELECT cloud_account_id, resource_id, date
                FROM expenses
                WHERE invoice_month != ''
                GROUP BY cloud_account_id, resource_id, date
                HAVING sum(sign) > 0
            ) AS billed USING (cloud_account_id, resource_id, date)
            WHERE empty.invoice_month = ''
            GROUP BY
                empty.cloud_account_id,
                empty.resource_id,
                empty.date
            HAVING abs(sum(empty.cost * empty.sign)) > 0.0000001
            """)
        LOG.info('Collapsed stale empty invoice_month expenses')

    def downgrade(self):
        pass
