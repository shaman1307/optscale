import logging

from diworker.diworker.migrations.base import BaseMigration
import clickhouse_connect

"""
Add invoice_month to ClickHouse expenses and include it in ORDER BY.

CollapsingMergeTree cannot MODIFY ORDER BY in place. Recreate the table
and copy existing rows with invoice_month = ''.
"""

LOG = logging.getLogger(__name__)
NEW_TABLE = 'expenses_invoice_month'
OLD_TABLE = 'expenses_pre_invoice_month'


class Migration(BaseMigration):
    def _get_clickhouse_client(self):
        user, password, host, db_name, port, secure = (
            self.config_cl.clickhouse_params())
        return clickhouse_connect.get_client(
            host=host, password=password, database=db_name, user=user,
            port=port, secure=secure)

    def upgrade(self):
        clickhouse_client = self._get_clickhouse_client()
        LOG.info('Creating expenses table with invoice_month')
        clickhouse_client.query('DROP TABLE IF EXISTS %s' % NEW_TABLE)
        clickhouse_client.query(
            """
            CREATE TABLE {new_table} (
                cloud_account_id String,
                resource_id String,
                date DateTime,
                cost Float64,
                sign Int8,
                invoice_month String)
            ENGINE = CollapsingMergeTree(sign)
            PARTITION BY toYYYYMM(date)
            ORDER BY (cloud_account_id, date, resource_id, invoice_month)
            """.format(new_table=NEW_TABLE))
        LOG.info('Copying expenses into %s', NEW_TABLE)
        clickhouse_client.query(
            """
            INSERT INTO {new_table}
            SELECT cloud_account_id, resource_id, date, cost, sign, ''
            FROM expenses
            """.format(new_table=NEW_TABLE))
        LOG.info('Swapping expenses tables')
        clickhouse_client.query('DROP TABLE IF EXISTS %s' % OLD_TABLE)
        clickhouse_client.query(
            'RENAME TABLE expenses TO {old}, {new} TO expenses'.format(
                old=OLD_TABLE, new=NEW_TABLE))
        clickhouse_client.query('DROP TABLE IF EXISTS %s' % OLD_TABLE)

    def downgrade(self):
        clickhouse_client = self._get_clickhouse_client()
        clickhouse_client.query('DROP TABLE IF EXISTS %s' % NEW_TABLE)
        clickhouse_client.query(
            """
            CREATE TABLE {new_table} (
                cloud_account_id String,
                resource_id String,
                date DateTime,
                cost Float64,
                sign Int8)
            ENGINE = CollapsingMergeTree(sign)
            PARTITION BY toYYYYMM(date)
            ORDER BY (cloud_account_id, date, resource_id)
            """.format(new_table=NEW_TABLE))
        clickhouse_client.query(
            """
            INSERT INTO {new_table}
            SELECT cloud_account_id, resource_id, date, cost, sign
            FROM expenses
            """.format(new_table=NEW_TABLE))
        clickhouse_client.query('DROP TABLE IF EXISTS %s' % OLD_TABLE)
        clickhouse_client.query(
            'RENAME TABLE expenses TO {old}, {new} TO expenses'.format(
                old=OLD_TABLE, new=NEW_TABLE))
        clickhouse_client.query('DROP TABLE IF EXISTS %s' % OLD_TABLE)
