import logging
import time

import clickhouse_connect
from optscale_client.rest_api_client.client_v2 import Client as RestClient

from diworker.diworker.migrations.base import BaseMigration

"""
Remove ACCOUNT_USAGE billing that ORGANIZATION_USAGE already covers.

ACCOUNT_USAGE collectors are now Cortex / Intelligence / Reader only.
Shared usage (compute, storage, pipes, metering, transfer, marketplace)
lives exclusively under ORGANIZATION_USAGE.

For every OptScale org that has both billing_source types, purge overlapping
resources / raw_expenses / ClickHouse expenses from ACCOUNT_USAGE data sources.
"""

LOG = logging.getLogger(__name__)
BILLING_SOURCE_ACCOUNT_USAGE = 'account_usage'
BILLING_SOURCE_ORGANIZATION_USAGE = 'organization_usage'
KEEP_SERVICE_TYPES = frozenset({
    'AI_SERVICES',
    'READER_ACCOUNT',
    # Legacy labels before rename to AI_SERVICES.
    'AI_FUNCTIONS',
    'CORTEX_AGENTS',
    'CORTEX_CODE_CLI',
    'CORTEX_CODE_SNOWSIGHT',
    'CORTEX_CODE_DESKTOP',
    'SNOWFLAKE_INTELLIGENCE',
    'SNOWFLAKE_COCO_SNOWSIGHT',
})
EXCLUSIVE_RESOURCE_ID_RE = (
    r'.*/(reader|ai_functions|cortex_|snowflake_intelligence)/')


class Migration(BaseMigration):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._clickhouse_cl = None

    @property
    def mongo_resources(self):
        return self.db.resources

    @property
    def mongo_raw(self):
        return self.db.raw_expenses

    @property
    def rest_cl(self):
        if self._rest_cl is None:
            self._rest_cl = RestClient(
                url=self.config_cl.restapi_url(),
                secret=self.config_cl.cluster_secret())
        return self._rest_cl

    @property
    def clickhouse_cl(self):
        if self._clickhouse_cl is None:
            user, password, host, db_name, port, secure = (
                self.config_cl.clickhouse_params())
            self._clickhouse_cl = clickhouse_connect.get_client(
                host=host, password=password, database=db_name, user=user,
                port=port, secure=secure)
        return self._clickhouse_cl

    @staticmethod
    def _billing_source(cloud_account):
        cfg = cloud_account.get('config') or {}
        value = cfg.get('billing_source') or BILLING_SOURCE_ACCOUNT_USAGE
        return str(value).strip().lower()

    def _account_usage_ids_covered_by_org(self):
        """ACCOUNT_USAGE cloud account ids in orgs that also have org_usage."""
        result = []
        _, organizations = self.rest_cl.organization_list({
            'with_connected_accounts': True,
            'is_demo': False,
        })
        for org in organizations.get('organizations') or []:
            _, accounts = self.rest_cl.cloud_account_list(
                org['id'], type='snowflake')
            snowflake_accounts = accounts.get('cloud_accounts') or []
            has_org_usage = any(
                self._billing_source(acc) == BILLING_SOURCE_ORGANIZATION_USAGE
                for acc in snowflake_accounts)
            if not has_org_usage:
                continue
            for acc in snowflake_accounts:
                if self._billing_source(acc) == BILLING_SOURCE_ACCOUNT_USAGE:
                    result.append(acc['id'])
                    LOG.info(
                        'Org %s: ACCOUNT_USAGE %s (%s) will be purged of '
                        'ORGANIZATION_USAGE-covered data',
                        org['id'], acc['id'], acc.get('name'))
        return result

    @staticmethod
    def _keep_resource(resource):
        resource_type = resource.get('resource_type')
        if resource_type in KEEP_SERVICE_TYPES:
            return True
        cloud_resource_id = resource.get('cloud_resource_id') or ''
        if cloud_resource_id and (
                '/reader/' in cloud_resource_id
                or '/ai_functions/' in cloud_resource_id
                or '/cortex_' in cloud_resource_id
                or '/snowflake_intelligence/' in cloud_resource_id):
            return True
        meta = resource.get('meta') or {}
        if meta.get('service_category') == 'cortex_ai':
            return True
        if meta.get('service_type') in KEEP_SERVICE_TYPES:
            return True
        return False

    def _purge_cloud_account(self, cloud_account_id):
        active = list(self.mongo_resources.find(
            {
                'cloud_account_id': cloud_account_id,
                'deleted_at': 0,
            },
            {
                '_id': 1,
                'cloud_resource_id': 1,
                'resource_type': 1,
                'meta': 1,
            },
        ))
        keep_ids = []
        drop_ids = []
        for resource in active:
            if self._keep_resource(resource):
                keep_ids.append(str(resource['_id']))
            else:
                drop_ids.append(resource['_id'])
        now = int(time.time())
        if drop_ids:
            soft = self.mongo_resources.update_many(
                {'_id': {'$in': drop_ids}},
                {'$set': {'deleted_at': now}},
            )
            LOG.info(
                'Soft-deleted %s overlapping resources on %s',
                soft.modified_count, cloud_account_id)
        # One mutation: drop all expenses except exclusive resource ids.
        if keep_ids:
            self.clickhouse_cl.query(
                'ALTER TABLE expenses DELETE WHERE '
                'cloud_account_id = %(ca_id)s '
                'AND resource_id NOT IN %(keep_ids)s',
                parameters={
                    'ca_id': cloud_account_id,
                    'keep_ids': keep_ids,
                })
        else:
            self.clickhouse_cl.query(
                'ALTER TABLE expenses DELETE WHERE '
                'cloud_account_id = %(ca_id)s',
                parameters={'ca_id': cloud_account_id})
        LOG.info(
            'Queued ClickHouse purge for ACCOUNT_USAGE %s '
            '(keep_resources=%s)',
            cloud_account_id, len(keep_ids))
        raw = self.mongo_raw.delete_many({
            'cloud_account_id': cloud_account_id,
            'service_type': {'$nin': list(KEEP_SERVICE_TYPES)},
        })
        LOG.info(
            'Deleted %s overlapping raw expenses on %s '
            '(kept active resources: %s)',
            raw.deleted_count, cloud_account_id, len(keep_ids))

    def upgrade(self):
        account_ids = self._account_usage_ids_covered_by_org()
        if not account_ids:
            LOG.info(
                'No ACCOUNT_USAGE Snowflake accounts covered by '
                'ORGANIZATION_USAGE; nothing to purge')
            return
        for cloud_account_id in account_ids:
            self._purge_cloud_account(cloud_account_id)

    def downgrade(self):
        pass
