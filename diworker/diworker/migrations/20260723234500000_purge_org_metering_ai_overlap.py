import logging
import time

import clickhouse_connect
from optscale_client.rest_api_client.client_v2 import Client as RestClient

from diworker.diworker.migrations.base import BaseMigration

"""
Purge ORGANIZATION_USAGE metering AI rows for account locators that already
have an ACCOUNT_USAGE Snowflake data source (detailed Cortex collectors).

METERING_DAILY historically imported AI_FUNCTIONS / SNOWFLAKE_COCO_* aggregates
which double-count Cortex spend already stored on the account_usage source.
Other member accounts without account_usage keep their metering AI rows.
"""

LOG = logging.getLogger(__name__)
BILLING_SOURCE_ACCOUNT_USAGE = 'account_usage'
BILLING_SOURCE_ORGANIZATION_USAGE = 'organization_usage'
AI_METERING_SERVICE_TYPES = frozenset({
    'AI_SERVICES',
    'AI_FUNCTIONS',
    'AI_INFERENCE',
    'SNOWFLAKE_INTELLIGENCE',
    'SNOWFLAKE_COCO_SNOWSIGHT',
    'SNOWFLAKE_COCO_CLI',
    'SNOWFLAKE_COCO_DESKTOP',
    'CORTEX_AGENTS',
    'CORTEX_CODE_CLI',
    'CORTEX_CODE_SNOWSIGHT',
    'CORTEX_CODE_DESKTOP',
})


def _is_ai_metering_service(service_type: str) -> bool:
    if not service_type:
        return False
    if service_type in AI_METERING_SERVICE_TYPES:
        return True
    return (
        service_type.startswith('CORTEX')
        or service_type.startswith('SNOWFLAKE_COCO')
        or service_type.startswith('AI_')
    )


def _resource_locator(cloud_resource_id: str) -> str:
    if not cloud_resource_id or '/' not in cloud_resource_id:
        return ''
    return cloud_resource_id.split('/', 1)[0]


def _is_metering_ai_resource(resource, owned_locators) -> bool:
    cloud_resource_id = resource.get('cloud_resource_id') or ''
    if '/metering/' not in cloud_resource_id:
        return False
    locator = _resource_locator(cloud_resource_id)
    if locator not in owned_locators:
        return False
    service = cloud_resource_id.rsplit('/', 1)[-1]
    if _is_ai_metering_service(service):
        return True
    resource_type = resource.get('resource_type') or ''
    return _is_ai_metering_service(resource_type)


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

    def _org_usage_targets(self):
        """Yield (org_usage_cloud_account_id, owned_locators)."""
        _, organizations = self.rest_cl.organization_list({
            'with_connected_accounts': True,
            'is_demo': False,
        })
        for org in organizations.get('organizations') or []:
            _, accounts = self.rest_cl.cloud_account_list(
                org['id'], type='snowflake')
            snowflake_accounts = accounts.get('cloud_accounts') or []
            owned_locators = {
                str(acc.get('account_id'))
                for acc in snowflake_accounts
                if (self._billing_source(acc) == BILLING_SOURCE_ACCOUNT_USAGE
                    and acc.get('account_id'))
            }
            if not owned_locators:
                continue
            for acc in snowflake_accounts:
                if self._billing_source(acc) != BILLING_SOURCE_ORGANIZATION_USAGE:
                    continue
                LOG.info(
                    'Org %s: ORGANIZATION_USAGE %s (%s) will drop metering AI '
                    'for locators %s',
                    org['id'], acc['id'], acc.get('name'),
                    sorted(owned_locators))
                yield acc['id'], frozenset(owned_locators)

    def _purge_cloud_account(self, cloud_account_id, owned_locators):
        active = list(self.mongo_resources.find(
            {
                'cloud_account_id': cloud_account_id,
                'deleted_at': 0,
            },
            {
                '_id': 1,
                'cloud_resource_id': 1,
                'resource_type': 1,
            },
        ))
        drop_ids = [
            resource['_id'] for resource in active
            if _is_metering_ai_resource(resource, owned_locators)
        ]
        now = int(time.time())
        if drop_ids:
            soft = self.mongo_resources.update_many(
                {'_id': {'$in': drop_ids}},
                {'$set': {'deleted_at': now}},
            )
            LOG.info(
                'Soft-deleted %s org metering AI resources on %s',
                soft.modified_count, cloud_account_id)
            drop_str_ids = [str(x) for x in drop_ids]
            self.clickhouse_cl.query(
                'ALTER TABLE expenses DELETE WHERE '
                'cloud_account_id = %(ca_id)s '
                'AND resource_id IN %(drop_ids)s',
                parameters={
                    'ca_id': cloud_account_id,
                    'drop_ids': drop_str_ids,
                })
            LOG.info(
                'Queued ClickHouse purge for %s metering AI resources on %s',
                len(drop_str_ids), cloud_account_id)
        raw = self.mongo_raw.delete_many({
            'cloud_account_id': cloud_account_id,
            'account_locator': {'$in': list(owned_locators)},
            'service_type': {'$in': list(AI_METERING_SERVICE_TYPES)},
        })
        # Also catch CORTEX% / SNOWFLAKE_COCO% not listed above.
        raw2 = self.mongo_raw.delete_many({
            'cloud_account_id': cloud_account_id,
            'account_locator': {'$in': list(owned_locators)},
            'service_type': {
                '$regex': '^(CORTEX|SNOWFLAKE_COCO|AI_)',
            },
        })
        LOG.info(
            'Deleted %s+%s org metering AI raw expenses on %s',
            raw.deleted_count, raw2.deleted_count, cloud_account_id)

    def upgrade(self):
        targets = list(self._org_usage_targets())
        if not targets:
            LOG.info(
                'No ORGANIZATION_USAGE Snowflake accounts with sibling '
                'ACCOUNT_USAGE locators; nothing to purge')
            return
        for cloud_account_id, owned_locators in targets:
            self._purge_cloud_account(cloud_account_id, owned_locators)

    def downgrade(self):
        pass
