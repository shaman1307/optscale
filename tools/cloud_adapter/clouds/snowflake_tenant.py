"""Snowflake organization (tenant) adapter.

Lists member accounts from ORGANIZATION_USAGE.ACCOUNTS and exposes them as
child snowflake cloud accounts (including the session/connect account).
Billing import runs on the tenant once; expenses are stamped onto children.
"""
import logging

from tools.cloud_adapter.clouds.snowflake import (
    BILLING_SOURCE_ORGANIZATION_USAGE,
    Snowflake,
    load_sql,
)
from tools.cloud_adapter.enums import CloudTypes
from tools.cloud_adapter.exceptions import (
    CloudConnectionError,
    InvalidParameterException,
)
from tools.cloud_adapter.utils import CloudParameter

LOG = logging.getLogger(__name__)


class SnowflakeTenant(Snowflake):
    BILLING_CREDS = [
        CloudParameter(name='account', type=str, required=True),
        CloudParameter(name='user', type=str, required=True),
        CloudParameter(name='private_key', type=str, required=True,
                       protected=True, check_len=False),
        CloudParameter(name='role', type=str, required=False,
                       default='ACCOUNTADMIN'),
        CloudParameter(name='warehouse', type=str, required=True),
        CloudParameter(name='backup_warehouse', type=str, required=False),
        CloudParameter(name='billing_source', type=str, required=False,
                       default=BILLING_SOURCE_ORGANIZATION_USAGE),
        # Service parameters
        CloudParameter(name='skipped_subscriptions', type=dict, required=False),
        # Set by resource-observer; not a user credential.
        CloudParameter(name='last_children_sync_at', type=int, required=False),
        # Detected via CURRENT_REGION() on validate.
        CloudParameter(name='region', type=str, required=False),
    ]

    @property
    def billing_source(self):
        value = (
            self.config.get('billing_source')
            or BILLING_SOURCE_ORGANIZATION_USAGE)
        value = str(value).strip().lower()
        if value != BILLING_SOURCE_ORGANIZATION_USAGE:
            raise InvalidParameterException(
                'snowflake_tenant requires billing_source=%s'
                % BILLING_SOURCE_ORGANIZATION_USAGE)
        return value

    def discovery_calls_map(self):
        return {}

    def _list_accounts(self):
        """Return {account_locator: {name, region}} for org members."""
        last_exc = None
        for _attempt in range(2):
            conn = self.connect()
            try:
                cursor = conn.cursor()
                cursor.execute(load_sql(
                    'accounts.sql', BILLING_SOURCE_ORGANIZATION_USAGE))
                columns = [c[0].lower() for c in cursor.description]
                accounts = {}
                for row in cursor:
                    record = dict(zip(columns, row))
                    loc = record.get('account_locator')
                    name = record.get('account_name')
                    if not loc or not name:
                        continue
                    region = record.get('region')
                    entry = {'name': str(name)}
                    if region is not None and str(region).strip() != '':
                        entry['region'] = str(region)
                    accounts[str(loc)] = entry
                cursor.close()
                return accounts
            except Exception as exc:
                last_exc = exc
                if self._try_failover_to_backup_warehouse(exc):
                    continue
                raise CloudConnectionError(
                    'Failed to list Snowflake organization accounts: %s' % exc
                ) from exc
            finally:
                self.close()
        raise CloudConnectionError(
            'Failed to list Snowflake organization accounts: %s' % last_exc
        ) from last_exc

    def get_children_configs(self):
        accounts = self._list_accounts()
        # Snowflake can list multiple locators with the same account_name;
        # OptScale CA names are unique per org, so disambiguate duplicates.
        name_counts = {}
        for info in accounts.values():
            account_name = info['name']
            name_counts[account_name] = name_counts.get(account_name, 0) + 1
        configs = []
        for account_locator, info in accounts.items():
            account_name = info['name']
            name = account_name
            if name_counts.get(account_name, 0) > 1:
                name = '%s (%s)' % (account_name, account_locator)
            child_config = {
                'account_locator': account_locator,
            }
            if info.get('region'):
                child_config['region'] = info['region']
            configs.append({
                'name': name,
                'config': child_config,
                'type': CloudTypes.SNOWFLAKE.value,
            })
        return configs
