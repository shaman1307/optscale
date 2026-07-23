import calendar
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterator
import json

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

from tools.cloud_adapter.clouds.base import CloudBase
from tools.cloud_adapter.exceptions import (
    CloudConnectionError,
    CloudSettingNotSupported,
    InvalidParameterException,
)
from tools.cloud_adapter.utils import CloudParameter

LOG = logging.getLogger(__name__)
DEFAULT_CURRENCY = 'USD'
DEFAULT_CREDIT_PRICE = 0.0
DEFAULT_STORAGE_PRICE_PER_TB_MONTH = 23.0
DEFAULT_TRANSFER_PRICE_PER_TB = 0.0
RECONCILE_THRESHOLD = 0.05
# Exclude recent days from reconcile; ACCOUNT_USAGE daily rows lag.
RECONCILE_LAG_DAYS = 2
SQL_DIR = Path(__file__).resolve().parent / 'snowflake_queries'
# METERING_DAILY_HISTORY names → OptScale service_type used by detail collectors.
DAILY_TO_DETAIL_SERVICE = {
    'WAREHOUSE_METERING': 'COMPUTE',
    'PIPE': 'SNOWPIPE',
    'SNOWPIPE': 'SNOWPIPE',
    'AUTO_CLUSTERING': 'AUTOMATIC_CLUSTERING',
}
DETAIL_RECONCILE_TYPES = frozenset(DAILY_TO_DETAIL_SERVICE.values())
# Generic product-mapping resource names for account-level / grouped services.
PRODUCT_LOOKUP_NAME = {
    'SNOWPIPE': 'SNOWPIPE',
    'AI_SERVICES': 'AI_SERVICES',
    'STAGE': 'STAGE',
}
# Budget table may store a different RESOURCE_TYPE than OptScale service_type.
PRODUCT_LOOKUP_TYPE = {
    'STAGE': 'STORAGE',
}


@lru_cache(maxsize=None)
def load_sql(name: str) -> str:
    path = SQL_DIR / name
    if not path.is_file():
        raise FileNotFoundError('Snowflake SQL file not found: %s' % path)
    return path.read_text(encoding='utf-8').strip()


def calculate_cost(record, cost_model):
    # Marketplace / prepaid monetary charges already in currency.
    if record.get('billable_amount') is not None:
        return float(record['billable_amount'] or 0)
    if record.get('credits_used') is not None:
        price = (
            cost_model.get('cortex_model_overrides', {}).get(
                record.get('model_name'))
            or cost_model.get('service_type_overrides', {}).get(
                record.get('service_type'))
            or cost_model.get('credit_price', DEFAULT_CREDIT_PRICE)
        )
        return float(record['credits_used']) * float(price)
    if record.get('average_bytes') is not None:
        tb = float(record['average_bytes']) / (1024 ** 4)
        start_date = record.get('start_date') or datetime.now(timezone.utc)
        if hasattr(start_date, 'date'):
            start_date = start_date.date()
        days_in_month = calendar.monthrange(
            start_date.year, start_date.month)[1]
        daily_rate = float(cost_model.get(
            'storage_price_per_tb_month',
            DEFAULT_STORAGE_PRICE_PER_TB_MONTH)) / float(days_in_month)
        return tb * daily_rate
    if record.get('bytes_transferred') is not None:
        tb = float(record['bytes_transferred']) / (1024 ** 4)
        return tb * float(cost_model.get(
            'transfer_price_per_tb', DEFAULT_TRANSFER_PRICE_PER_TB) or 0)
    return 0.0


def _day_start(value):
    if value is None:
        return None
    if hasattr(value, 'year') and not hasattr(value, 'hour'):
        return datetime(
            value.year, value.month, value.day, tzinfo=timezone.utc)
    return _to_utc(value)


def parse_metrics(metrics_array) -> dict:
    result = {
        'tokens_input': 0,
        'tokens_output': 0,
        'tokens_total': 0,
        'pages': 0,
    }
    if isinstance(metrics_array, str):
        try:
            metrics_array = json.loads(metrics_array)
        except (TypeError, ValueError, json.JSONDecodeError):
            return result
    for item in metrics_array or []:
        if not isinstance(item, dict):
            continue
        key = item.get('key') or {}
        if isinstance(key, str):
            try:
                key = json.loads(key)
            except (TypeError, ValueError, json.JSONDecodeError):
                key = {}
        if not isinstance(key, dict):
            continue
        metric = key.get('metric')
        unit = key.get('unit')
        value = item.get('value') or 0
        if unit == 'tokens':
            if metric == 'input':
                result['tokens_input'] = value
            elif metric == 'output':
                result['tokens_output'] = value
            elif metric == 'total':
                result['tokens_total'] = value
        elif unit == 'pages':
            result['pages'] = value
    return result


def _to_utc(value):
    """Normalize Snowflake timestamps to UTC.

    Session timezone is forced to UTC on connect, so naive datetimes from the
    connector are treated as UTC.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return value


def year_quarter(value=None) -> str:
    """Return Snowflake-style YEARQUARTER, e.g. 2026Q3."""
    dt = value or datetime.now(timezone.utc)
    if hasattr(dt, 'date') and not hasattr(dt, 'month'):
        dt = datetime(dt.year, 1, 1, tzinfo=timezone.utc)
    quarter = (dt.month - 1) // 3 + 1
    return f'{dt.year}Q{quarter}'


def product_lookup_name(record) -> str | None:
    """RESOURCE_NAME used to join RESOURCE_PRODUCT_MAPPING."""
    service_type = record.get('service_type')
    if service_type in PRODUCT_LOOKUP_NAME:
        return PRODUCT_LOOKUP_NAME[service_type]
    if service_type == 'AUTOMATIC_CLUSTERING':
        return record.get('database_name') or record.get('resource_name')
    return record.get('resource_name')


def product_lookup_type(record) -> str | None:
    """RESOURCE_TYPE used to join RESOURCE_PRODUCT_MAPPING."""
    service_type = record.get('service_type')
    if not service_type:
        return None
    return PRODUCT_LOOKUP_TYPE.get(service_type, service_type)


def apply_product_tag(record, product_map: dict):
    name = product_lookup_name(record)
    resource_type = product_lookup_type(record)
    if not name or not resource_type or not product_map:
        return
    product = product_map.get((str(name).upper(), str(resource_type).upper()))
    if product:
        record['tag'] = product


def _cortex_code_resource(account_locator, channel, record):
    """Stable per-user resource id/name for Cortex Code usage."""
    labels = {
        'cli': 'Cortex Code · CLI',
        'snowsight': 'Cortex Code · Snowsight',
        'desktop': 'Cortex Code · Desktop',
    }
    label = labels[channel]
    user_id = record.get('user_id')
    user_name = record.get('user_name')
    if user_name:
        resource_name = f'{label} · {user_name}'
    elif user_id is not None:
        resource_name = f'{label} · user {user_id}'
    else:
        resource_name = label
    uid = user_id if user_id is not None else 'unknown'
    resource_id = f'{account_locator}/cortex_code_{channel}/user/{uid}'
    return resource_id, resource_name


class SnowflakeUsageCollector(ABC):
    SQL_FILE: str
    SERVICE_CATEGORY: str
    SERVICE_TYPE: str
    # Collector identity for import details when SERVICE_TYPE is grouped.
    SOURCE: str | None = None

    @abstractmethod
    def fetch(self, cursor, start_ts: datetime, end_ts: datetime,
              account_locator: str) -> Iterator[dict]:
        raise NotImplementedError

    def _execute(self, cursor, params):
        # Larger fetch batches reduce round-trips on high-volume collectors.
        try:
            cursor.arraysize = max(getattr(cursor, 'arraysize', 0) or 0, 5000)
        except Exception:
            pass
        cursor.execute(load_sql(self.SQL_FILE), params)
        columns = [c[0].lower() for c in cursor.description]
        for row in cursor:
            yield dict(zip(columns, row))


class WarehouseMeteringCollector(SnowflakeUsageCollector):
    SQL_FILE = 'warehouse_metering_history.sql'
    SERVICE_CATEGORY = 'compute'
    SERVICE_TYPE = 'COMPUTE'
    SOURCE = 'WAREHOUSE_METERING'

    def fetch(self, cursor, start_ts, end_ts, account_locator):
        for record in self._execute(cursor, (start_ts, end_ts)):
            start = _to_utc(record.get('start_time'))
            end = _to_utc(record.get('end_time')) or start
            warehouse_id = record.get('warehouse_id')
            yield {
                'start_date': start,
                'end_date': end,
                'service_category': self.SERVICE_CATEGORY,
                'service_type': self.SERVICE_TYPE,
                'account_locator': account_locator,
                'resource_id': f"{account_locator}/warehouse/{warehouse_id}",
                'resource_name': record.get('warehouse_name'),
                'warehouse_id': warehouse_id,
                'credits_used': float(record.get('credits_used') or 0),
                'credits_used_compute': float(
                    record.get('credits_used_compute') or 0),
                'credits_used_cloud_services': float(
                    record.get('credits_used_cloud_services') or 0),
            }


class DatabaseStorageCollector(SnowflakeUsageCollector):
    SQL_FILE = 'database_storage_usage_history.sql'
    SERVICE_CATEGORY = 'storage'
    SERVICE_TYPE = 'STORAGE'
    SOURCE = 'DATABASE_STORAGE'

    def fetch(self, cursor, start_ts, end_ts, account_locator):
        for record in self._execute(
                cursor, (start_ts.date(), end_ts.date())):
            usage_date = record.get('usage_date')
            if hasattr(usage_date, 'year'):
                start = datetime(
                    usage_date.year, usage_date.month, usage_date.day,
                    tzinfo=timezone.utc)
            else:
                start = _to_utc(usage_date)
            database_id = record.get('database_id')
            avg_db = int(record.get('average_database_bytes') or 0)
            avg_fs = int(record.get('average_failsafe_bytes') or 0)
            yield {
                'start_date': start,
                'end_date': start,
                'service_category': self.SERVICE_CATEGORY,
                'service_type': self.SERVICE_TYPE,
                'account_locator': account_locator,
                'resource_id': f"{account_locator}/database/{database_id}",
                'resource_name': record.get('database_name'),
                'database_id': database_id,
                'average_bytes': avg_db + avg_fs,
            }


class StageStorageCollector(SnowflakeUsageCollector):
    SQL_FILE = 'stage_storage_usage_history.sql'
    SERVICE_CATEGORY = 'storage'
    SERVICE_TYPE = 'STAGE'
    SOURCE = 'STAGE_STORAGE'

    def fetch(self, cursor, start_ts, end_ts, account_locator):
        for record in self._execute(
                cursor, (start_ts.date(), end_ts.date())):
            usage_date = record.get('usage_date')
            if hasattr(usage_date, 'year'):
                start = datetime(
                    usage_date.year, usage_date.month, usage_date.day,
                    tzinfo=timezone.utc)
            else:
                start = _to_utc(usage_date)
            # ACCOUNT_USAGE stage view is account-level aggregate.
            yield {
                'start_date': start,
                'end_date': start,
                'service_category': self.SERVICE_CATEGORY,
                'service_type': self.SERVICE_TYPE,
                'account_locator': account_locator,
                'resource_id': f"{account_locator}/STAGE",
                'resource_name': 'STAGE',
                'average_bytes': int(record.get('average_stage_bytes') or 0),
            }


class PipeUsageCollector(SnowflakeUsageCollector):
    SQL_FILE = 'pipe_usage_history.sql'
    SERVICE_CATEGORY = 'serverless'
    SERVICE_TYPE = 'SNOWPIPE'
    SOURCE = 'PIPE'

    def fetch(self, cursor, start_ts, end_ts, account_locator):
        for record in self._execute(cursor, (start_ts, end_ts)):
            start = _to_utc(record.get('start_time'))
            end = _to_utc(record.get('end_time')) or start
            pipe_id = record.get('pipe_id')
            pipe_name = record.get('pipe_name')
            catalog = record.get('pipe_catalog') or '_'
            schema = record.get('pipe_schema') or '_'
            # Key by catalog/schema/name so recreations (new pipe_id, same
            # name) collapse into one OptScale resource. Fallback to pipe_id
            # when name is missing (account-level aggregate rows).
            if pipe_name:
                resource_id = (
                    f"{account_locator}/snowpipe/{catalog}/{schema}/"
                    f"{pipe_name}")
                resource_name = pipe_name
            else:
                resource_id = f"{account_locator}/snowpipe/{pipe_id}"
                resource_name = str(pipe_id)
            yield {
                'start_date': start,
                'end_date': end,
                'service_category': self.SERVICE_CATEGORY,
                'service_type': self.SERVICE_TYPE,
                'account_locator': account_locator,
                'resource_id': resource_id,
                'resource_name': resource_name,
                'pipe_id': pipe_id,
                'pipe_catalog': record.get('pipe_catalog'),
                'pipe_schema': record.get('pipe_schema'),
                'credits_used': float(record.get('credits_used') or 0),
                'bytes_inserted': float(record.get('bytes_inserted') or 0),
            }


class AutomaticClusteringCollector(SnowflakeUsageCollector):
    # SQL aggregates event-level history to one row per table per day —
    # year windows otherwise return millions of rows for little billing value.
    SQL_FILE = 'automatic_clustering_history.sql'
    SERVICE_CATEGORY = 'serverless'
    SERVICE_TYPE = 'AUTOMATIC_CLUSTERING'
    SOURCE = 'AUTO_CLUSTERING'

    def fetch(self, cursor, start_ts, end_ts, account_locator):
        for record in self._execute(cursor, (start_ts, end_ts)):
            start = _to_utc(record.get('start_time'))
            end = _to_utc(record.get('end_time')) or start
            table_id = record.get('table_id')
            database_name = record.get('database_name')
            table_name = record.get('table_name')
            yield {
                'start_date': start,
                'end_date': end,
                'service_category': self.SERVICE_CATEGORY,
                'service_type': self.SERVICE_TYPE,
                'account_locator': account_locator,
                'resource_id': (
                    f"{account_locator}/automatic_clustering/{table_id}"),
                'resource_name': (
                    f"{database_name}.{record.get('schema_name')}."
                    f"{table_name}" if database_name and table_name
                    else table_name or str(table_id)),
                'table_id': table_id,
                'table_name': table_name,
                'schema_name': record.get('schema_name'),
                'database_id': record.get('database_id'),
                'database_name': database_name,
                'credits_used': float(record.get('credits_used') or 0),
                'num_bytes_reclustered': float(
                    record.get('num_bytes_reclustered') or 0),
                'num_rows_reclustered': float(
                    record.get('num_rows_reclustered') or 0),
            }


class MeteringDailyCollector(SnowflakeUsageCollector):
    SQL_FILE = 'metering_daily_history.sql'
    SERVICE_CATEGORY = 'serverless'
    SERVICE_TYPE = 'METERING_DAILY'

    def fetch(self, cursor, start_ts, end_ts, account_locator):
        for record in self._execute(
                cursor, (start_ts.date(), end_ts.date())):
            usage_date = record.get('usage_date')
            if hasattr(usage_date, 'year'):
                start = datetime(
                    usage_date.year, usage_date.month, usage_date.day,
                    tzinfo=timezone.utc)
            else:
                start = _to_utc(usage_date)
            service_type = record.get('service_type') or self.SERVICE_TYPE
            yield {
                'start_date': start,
                'end_date': start,
                'service_category': self.SERVICE_CATEGORY,
                'service_type': service_type,
                'account_locator': account_locator,
                'resource_id': f"{account_locator}/metering/{service_type}",
                'resource_name': service_type,
                'credits_used': float(record.get('credits_billed') or 0),
                'credits_used_compute': float(
                    record.get('credits_used_compute') or 0),
                'credits_used_cloud_services': float(
                    record.get('credits_used_cloud_services') or 0),
            }


class CortexAiFunctionsCollector(SnowflakeUsageCollector):
    SQL_FILE = 'cortex_ai_functions_usage_history.sql'
    SERVICE_CATEGORY = 'cortex_ai'
    SERVICE_TYPE = 'AI_SERVICES'
    SOURCE = 'AI_FUNCTIONS'

    def fetch(self, cursor, start_ts, end_ts, account_locator):
        for record in self._execute(cursor, (start_ts, end_ts)):
            if record.get('is_completed') is False:
                continue
            start = _to_utc(record.get('start_time'))
            end = _to_utc(record.get('end_time')) or start
            metrics = parse_metrics(record.get('metrics'))
            query_id = record.get('query_id')
            function_name = record.get('function_name') or 'unknown'
            model_name = record.get('model_name') or 'unknown'
            if model_name != 'unknown' and function_name != 'unknown':
                resource_name = f'{function_name} · {model_name}'
            else:
                resource_name = (
                    model_name if model_name != 'unknown'
                    else function_name)
            yield {
                'start_date': start,
                'end_date': end,
                'service_category': self.SERVICE_CATEGORY,
                'service_type': self.SERVICE_TYPE,
                'account_locator': account_locator,
                'resource_id': (
                    f"{account_locator}/ai_functions/"
                    f"{function_name}/{model_name}"),
                'resource_name': resource_name,
                'function_name': function_name,
                'model_name': model_name,
                'query_id': query_id,
                'warehouse_id': record.get('warehouse_id'),
                'user_id': record.get('user_id'),
                'role_names': record.get('role_names'),
                'query_tag': record.get('query_tag'),
                'credits_used': float(record.get('credits') or 0),
                'is_completed': bool(record.get('is_completed')),
                'metrics': metrics,
                **metrics,
            }


class CortexAgentCollector(SnowflakeUsageCollector):
    SQL_FILE = 'cortex_agent_usage_history.sql'
    SERVICE_CATEGORY = 'cortex_ai'
    SERVICE_TYPE = 'AI_SERVICES'
    SOURCE = 'CORTEX_AGENTS'

    def fetch(self, cursor, start_ts, end_ts, account_locator):
        for record in self._execute(cursor, (start_ts, end_ts)):
            start = _to_utc(record.get('start_time'))
            end = _to_utc(record.get('end_time')) or start
            agent_id = record.get('agent_id')
            agent_name = record.get('agent_name')
            user_id = record.get('user_id')
            user_name = record.get('user_name')
            request_id = record.get('request_id')
            if agent_name:
                resource_name = agent_name
                resource_id = f"{account_locator}/cortex_agents/{agent_id}"
            elif agent_id not in (None, 0, '0'):
                resource_name = f"Cortex Agent {agent_id}"
                resource_id = f"{account_locator}/cortex_agents/{agent_id}"
            elif user_name:
                resource_name = f"Cortex Agent · {user_name}"
                resource_id = (
                    f"{account_locator}/cortex_agents/user/{user_id}")
            else:
                resource_name = (
                    f"Cortex Agent · user {user_id}"
                    if user_id is not None else 'Cortex Agent')
                resource_id = (
                    f"{account_locator}/cortex_agents/user/"
                    f"{user_id if user_id is not None else 'unknown'}")
            yield {
                'start_date': start,
                'end_date': end,
                'service_category': self.SERVICE_CATEGORY,
                'service_type': self.SERVICE_TYPE,
                'account_locator': account_locator,
                'resource_id': resource_id,
                'resource_name': resource_name,
                'agent_id': agent_id,
                'agent_name': agent_name,
                'request_id': request_id,
                'user_id': user_id,
                'user_name': user_name,
                'credits_used': float(record.get('token_credits') or 0),
                'tokens_total': float(record.get('tokens') or 0),
                'tokens_granular': record.get('tokens_granular'),
                'credits_granular': record.get('credits_granular'),
                'metadata': record.get('metadata'),
            }


class CortexCodeCliCollector(SnowflakeUsageCollector):
    SQL_FILE = 'cortex_code_cli_usage_history.sql'
    SERVICE_CATEGORY = 'cortex_ai'
    SERVICE_TYPE = 'AI_SERVICES'
    SOURCE = 'CORTEX_CODE_CLI'

    def fetch(self, cursor, start_ts, end_ts, account_locator):
        for record in self._execute(cursor, (start_ts, end_ts)):
            start = _to_utc(record.get('usage_time'))
            resource_id, resource_name = _cortex_code_resource(
                account_locator, 'cli', record)
            yield {
                'start_date': start,
                'end_date': start,
                'service_category': self.SERVICE_CATEGORY,
                'service_type': self.SERVICE_TYPE,
                'account_locator': account_locator,
                'resource_id': resource_id,
                'resource_name': resource_name,
                'request_id': record.get('request_id'),
                'user_id': record.get('user_id'),
                'user_name': record.get('user_name'),
                'credits_used': float(record.get('token_credits') or 0),
                'tokens_total': float(record.get('tokens') or 0),
                'tokens_granular': record.get('tokens_granular'),
                'credits_granular': record.get('credits_granular'),
                'metadata': record.get('metadata'),
            }


class CortexCodeSnowsightCollector(SnowflakeUsageCollector):
    SQL_FILE = 'cortex_code_snowsight_usage_history.sql'
    SERVICE_CATEGORY = 'cortex_ai'
    SERVICE_TYPE = 'AI_SERVICES'
    SOURCE = 'CORTEX_CODE_SNOWSIGHT'

    def fetch(self, cursor, start_ts, end_ts, account_locator):
        for record in self._execute(cursor, (start_ts, end_ts)):
            start = _to_utc(record.get('usage_time'))
            resource_id, resource_name = _cortex_code_resource(
                account_locator, 'snowsight', record)
            yield {
                'start_date': start,
                'end_date': start,
                'service_category': self.SERVICE_CATEGORY,
                'service_type': self.SERVICE_TYPE,
                'account_locator': account_locator,
                'resource_id': resource_id,
                'resource_name': resource_name,
                'request_id': record.get('request_id'),
                'user_id': record.get('user_id'),
                'user_name': record.get('user_name'),
                'credits_used': float(record.get('token_credits') or 0),
                'tokens_total': float(record.get('tokens') or 0),
                'tokens_granular': record.get('tokens_granular'),
                'credits_granular': record.get('credits_granular'),
                'metadata': record.get('metadata'),
            }


class CortexCodeDesktopCollector(SnowflakeUsageCollector):
    SQL_FILE = 'cortex_code_desktop_usage_history.sql'
    SERVICE_CATEGORY = 'cortex_ai'
    SERVICE_TYPE = 'AI_SERVICES'
    SOURCE = 'CORTEX_CODE_DESKTOP'

    def fetch(self, cursor, start_ts, end_ts, account_locator):
        for record in self._execute(cursor, (start_ts, end_ts)):
            start = _to_utc(record.get('usage_time'))
            resource_id, resource_name = _cortex_code_resource(
                account_locator, 'desktop', record)
            yield {
                'start_date': start,
                'end_date': start,
                'service_category': self.SERVICE_CATEGORY,
                'service_type': self.SERVICE_TYPE,
                'account_locator': account_locator,
                'resource_id': resource_id,
                'resource_name': resource_name,
                'request_id': record.get('request_id'),
                'user_id': record.get('user_id'),
                'user_name': record.get('user_name'),
                'credits_used': float(record.get('token_credits') or 0),
                'tokens_total': float(record.get('tokens') or 0),
                'tokens_granular': record.get('tokens_granular'),
                'credits_granular': record.get('credits_granular'),
                'metadata': record.get('metadata'),
            }


class SnowflakeIntelligenceCollector(SnowflakeUsageCollector):
    SQL_FILE = 'snowflake_intelligence_usage_history.sql'
    SERVICE_CATEGORY = 'cortex_ai'
    SERVICE_TYPE = 'AI_SERVICES'
    SOURCE = 'SNOWFLAKE_INTELLIGENCE'

    def fetch(self, cursor, start_ts, end_ts, account_locator):
        for record in self._execute(cursor, (start_ts, end_ts)):
            start = _to_utc(record.get('start_time'))
            end = _to_utc(record.get('end_time')) or start
            intel_id = record.get('snowflake_intelligence_id')
            intel_name = record.get('snowflake_intelligence_name')
            request_id = record.get('request_id')
            if intel_name:
                resource_name = intel_name
            elif intel_id is not None:
                resource_name = f"Snowflake Intelligence {intel_id}"
            else:
                resource_name = 'Snowflake Intelligence'
            resource_id = (
                f"{account_locator}/snowflake_intelligence/"
                f"{intel_id if intel_id is not None else 'unknown'}")
            yield {
                'start_date': start,
                'end_date': end,
                'service_category': self.SERVICE_CATEGORY,
                'service_type': self.SERVICE_TYPE,
                'account_locator': account_locator,
                'resource_id': resource_id,
                'resource_name': resource_name,
                'request_id': request_id,
                'agent_id': record.get('agent_id'),
                'agent_name': record.get('agent_name'),
                'user_id': record.get('user_id'),
                'user_name': record.get('user_name'),
                'credits_used': float(record.get('token_credits') or 0),
                'tokens_total': float(record.get('tokens') or 0),
                'tokens_granular': record.get('tokens_granular'),
                'credits_granular': record.get('credits_granular'),
                'metadata': record.get('metadata'),
            }


class DataTransferCollector(SnowflakeUsageCollector):
    SQL_FILE = 'data_transfer_history.sql'
    SERVICE_CATEGORY = 'transfer'
    SERVICE_TYPE = 'DATA_TRANSFER'

    def fetch(self, cursor, start_ts, end_ts, account_locator):
        for record in self._execute(cursor, (start_ts, end_ts)):
            start = _to_utc(record.get('start_time'))
            end = _to_utc(record.get('end_time')) or start
            source_region = record.get('source_region') or 'unknown'
            target_region = record.get('target_region') or 'unknown'
            transfer_type = record.get('transfer_type') or 'TRANSFER'
            # Hourly uniqueness via start timestamp in resource_id.
            yield {
                'start_date': start,
                'end_date': end,
                'service_category': self.SERVICE_CATEGORY,
                'service_type': self.SERVICE_TYPE,
                'account_locator': account_locator,
                'resource_id': (
                    f"{account_locator}/data_transfer/"
                    f"{source_region}/{target_region}/{transfer_type}/"
                    f"{int(start.timestamp()) if start else 0}"),
                'resource_name': (
                    f"{transfer_type}:{source_region}->{target_region}"),
                'source_region': source_region,
                'target_region': target_region,
                'transfer_type': transfer_type,
                'bytes_transferred': float(
                    record.get('bytes_transferred') or 0),
            }


class MarketplacePaidUsageCollector(SnowflakeUsageCollector):
    SQL_FILE = 'marketplace_paid_usage_daily.sql'
    SERVICE_CATEGORY = 'data_sharing'
    SERVICE_TYPE = 'MARKETPLACE_PAID'

    def fetch(self, cursor, start_ts, end_ts, account_locator):
        for record in self._execute(
                cursor, (start_ts.date(), end_ts.date())):
            start = _day_start(record.get('usage_date'))
            listing = record.get('listing_global_name') or 'listing'
            charge_type = record.get('charge_type') or 'CHARGE'
            yield {
                'start_date': start,
                'end_date': start,
                'service_category': self.SERVICE_CATEGORY,
                'service_type': self.SERVICE_TYPE,
                'account_locator': account_locator,
                'resource_id': (
                    f"{account_locator}/marketplace/"
                    f"{listing}/{charge_type}"),
                'resource_name': (
                    record.get('listing_display_name') or listing),
                'listing_global_name': listing,
                'charge_type': charge_type,
                'units': float(record.get('units') or 0),
                'unit_price': float(record.get('unit_price') or 0),
                'currency': record.get('currency') or DEFAULT_CURRENCY,
                'billable_amount': float(record.get('charge') or 0),
            }


class ReaderWarehouseMeteringCollector(SnowflakeUsageCollector):
    SQL_FILE = 'reader_warehouse_metering_history.sql'
    SERVICE_CATEGORY = 'data_sharing'
    SERVICE_TYPE = 'READER_ACCOUNT'

    def fetch(self, cursor, start_ts, end_ts, account_locator):
        for record in self._execute(cursor, (start_ts, end_ts)):
            start = _to_utc(record.get('start_time'))
            end = _to_utc(record.get('end_time')) or start
            reader = record.get('reader_account_name') or 'reader'
            warehouse_id = record.get('warehouse_id')
            yield {
                'start_date': start,
                'end_date': end,
                'service_category': self.SERVICE_CATEGORY,
                'service_type': self.SERVICE_TYPE,
                'account_locator': account_locator,
                'resource_id': (
                    f"{account_locator}/reader/{reader}/"
                    f"warehouse/{warehouse_id}"),
                'resource_name': record.get('warehouse_name') or str(
                    warehouse_id),
                'reader_account_name': reader,
                'warehouse_id': warehouse_id,
                'credits_used': float(record.get('credits_used') or 0),
                'credits_used_compute': float(
                    record.get('credits_used_compute') or 0),
                'credits_used_cloud_services': float(
                    record.get('credits_used_cloud_services') or 0),
            }


# Listing consumption is provider analytics (jobs), not billable credits —
# SQL kept for probes; not registered in COLLECTORS.


COLLECTORS = [
    WarehouseMeteringCollector,
    DatabaseStorageCollector,
    StageStorageCollector,
    PipeUsageCollector,
    AutomaticClusteringCollector,
    MeteringDailyCollector,
    CortexAiFunctionsCollector,
    CortexAgentCollector,
    CortexCodeCliCollector,
    CortexCodeSnowsightCollector,
    CortexCodeDesktopCollector,
    SnowflakeIntelligenceCollector,
    DataTransferCollector,
    MarketplacePaidUsageCollector,
    ReaderWarehouseMeteringCollector,
]


class Snowflake(CloudBase):
    SUPPORTS_REPORT_UPLOAD = False

    BILLING_CREDS = [
        CloudParameter(name='account', type=str, required=True),
        CloudParameter(name='user', type=str, required=True),
        CloudParameter(name='private_key', type=str, required=True,
                       protected=True, check_len=False),
        CloudParameter(name='role', type=str, required=False,
                       default='ACCOUNTADMIN'),
        CloudParameter(name='warehouse', type=str, required=True),
    ]

    def __init__(self, cloud_config, *args, **kwargs):
        self.config = cloud_config
        self._currency = DEFAULT_CURRENCY
        self._connection = None

    @property
    def account(self):
        return self.config.get('account')

    @property
    def user(self):
        return self.config.get('user')

    @property
    def private_key(self):
        return self.config.get('private_key')

    @property
    def role(self):
        return self.config.get('role') or 'ACCOUNTADMIN'

    @property
    def warehouse(self):
        return self.config.get('warehouse')

    def _load_private_key_bytes(self):
        raw = self.private_key
        if not raw:
            raise InvalidParameterException('private_key is required')
        if isinstance(raw, str):
            raw = raw.replace('\\n', '\n').encode('utf-8')
        try:
            pkey = serialization.load_pem_private_key(
                raw, password=None, backend=default_backend())
        except Exception as exc:
            raise InvalidParameterException(
                'Invalid private_key PEM: %s' % exc) from exc
        return pkey.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def connect(self):
        if self._connection is not None:
            return self._connection
        try:
            import snowflake.connector
        except ImportError as exc:
            raise CloudConnectionError(
                'snowflake-connector-python is not installed') from exc
        try:
            self._connection = snowflake.connector.connect(
                account=self.account,
                user=self.user,
                private_key=self._load_private_key_bytes(),
                role=self.role,
                warehouse=self.warehouse,
                timezone='UTC',
            )
            # Force UTC so TIMESTAMP_LTZ filters/binds match OptScale months.
            cursor = self._connection.cursor()
            try:
                cursor.execute("ALTER SESSION SET TIMEZONE = 'UTC'")
            finally:
                cursor.close()
        except Exception as exc:
            self.close()
            raise CloudConnectionError(
                'Snowflake connection failed: %s' % exc) from exc
        return self._connection

    def close(self):
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None

    def _probe_view(self, cursor, view_name, warnings):
        try:
            cursor.execute(
                load_sql('probe_view.sql').format(view_name=view_name))
            cursor.fetchone()
        except Exception as exc:
            warnings.append(
                'Unable to query %s: %s' % (view_name, exc))

    def validate_credentials(self, org_id=None):
        warnings = []
        conn = self.connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT CURRENT_ACCOUNT(), CURRENT_USER(), '
                'CURRENT_ROLE(), CURRENT_WAREHOUSE()')
            account_locator, user, role, warehouse = cursor.fetchone()
            if not warehouse:
                raise CloudConnectionError(
                    'Warehouse %s is not available for the session'
                    % self.warehouse)
            self._probe_view(
                cursor,
                'SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY',
                warnings)
            self._probe_view(
                cursor,
                'SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AI_FUNCTIONS_USAGE_HISTORY',
                warnings)
            cursor.close()
        except CloudConnectionError:
            raise
        except Exception as exc:
            raise CloudConnectionError(str(exc)) from exc
        finally:
            self.close()
        LOG.info(
            'Snowflake credentials validated for account=%s user=%s role=%s',
            account_locator, user, role)
        return {
            'account_id': str(account_locator),
            'warnings': warnings,
        }

    def download_usage(self, start_ts, end_ts, progress_callback=None):
        if isinstance(start_ts, str):
            start_ts = datetime.fromisoformat(start_ts.replace('Z', '+00:00'))
        if isinstance(end_ts, str):
            end_ts = datetime.fromisoformat(end_ts.replace('Z', '+00:00'))
        if start_ts.tzinfo is None:
            start_ts = start_ts.replace(tzinfo=timezone.utc)
        if end_ts.tzinfo is None:
            end_ts = end_ts.replace(tzinfo=timezone.utc)

        self._import_warnings = []
        self._import_collectors = []
        self._import_reconciliation = []
        detail_credits = defaultdict(float)
        # Seed one in_progress row per service_type so UI can show live status.
        seeded = set()
        for collector_cls in COLLECTORS:
            service_type = collector_cls.SERVICE_TYPE
            if service_type in seeded:
                continue
            seeded.add(service_type)
            self._import_collectors.append({
                'service_type': service_type,
                'source': service_type,
                'records': 0,
                'credits': 0.0,
                'average_bytes': 0,
                'tb': 0.0,
                'status': 'in_progress',
                'message': None,
                'finished_at': None,
            })
        if progress_callback:
            progress_callback()
        conn = self.connect()
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT CURRENT_ACCOUNT()')
            account_locator = str(cursor.fetchone()[0])
            product_map = self._load_product_map(cursor, account_locator)
            # Sequential collectors (max 1 concurrent Snowflake query).
            for collector_cls in COLLECTORS:
                collector = collector_cls()
                records = 0
                credits = 0.0
                average_bytes = 0
                # Storage snapshots are point-in-time per day — report only the
                # latest loaded day (sum across DBs/stages that day), not the
                # sum of every day in the import window.
                storage_bytes_by_day = defaultdict(int)
                status = 'ok'
                message = None
                source = collector.SOURCE or collector.SERVICE_TYPE
                is_storage = collector.SERVICE_TYPE in ('STORAGE', 'STAGE')
                try:
                    for record in collector.fetch(
                            cursor, start_ts, end_ts, account_locator):
                        apply_product_tag(record, product_map)
                        records += 1
                        used = float(record.get('credits_used') or 0)
                        if used:
                            credits += used
                            day = record['start_date']
                            if hasattr(day, 'date'):
                                day = day.date()
                            detail_credits[
                                (record.get('service_type'), day)] += used
                        bytes_used = int(record.get('average_bytes') or 0)
                        if bytes_used:
                            if is_storage:
                                day = record.get('start_date')
                                if hasattr(day, 'date'):
                                    day = day.date()
                                if day is not None:
                                    storage_bytes_by_day[day] += bytes_used
                            else:
                                average_bytes += bytes_used
                        yield record
                except Exception as exc:
                    warning = self._collector_failure_warning(
                        source, exc)
                    self._import_warnings.append(warning)
                    LOG.warning(warning)
                    status = (
                        'skipped' if 'skipped:' in warning else 'failed')
                    message = warning
                else:
                    if is_storage and storage_bytes_by_day:
                        last_day = max(storage_bytes_by_day)
                        average_bytes = storage_bytes_by_day[last_day]
                    LOG.info(
                        'Snowflake collector %s: records=%s credits=%.4f '
                        'tb=%.4f',
                        source, records, credits,
                        average_bytes / (1024 ** 4))
                self._finish_collector_stat(
                    collector.SERVICE_TYPE, source, records, credits,
                    average_bytes, status, message)
                if progress_callback:
                    progress_callback()

            for warning in self._reconcile_credits(
                    cursor, start_ts, end_ts, detail_credits):
                self._import_warnings.append(warning)
                LOG.warning(warning)
            cursor.close()
        finally:
            self.close()

    def _finish_collector_stat(
            self, service_type, source, records, credits, average_bytes,
            status, message):
        finished_at = int(datetime.now(timezone.utc).timestamp())
        tb = round(average_bytes / (1024 ** 4), 4)
        credits = round(credits, 4)
        for row in self._import_collectors:
            if row.get('service_type') != service_type:
                continue
            if row.get('status') == 'in_progress':
                row.update({
                    'source': source,
                    'records': records,
                    'credits': credits,
                    'average_bytes': average_bytes,
                    'tb': tb,
                    'status': status,
                    'message': message,
                    'finished_at': finished_at,
                })
                return
            # Extra source for a grouped type (e.g. AI_SERVICES).
            row['records'] = int(row.get('records') or 0) + records
            row['credits'] = round(
                float(row.get('credits') or 0) + credits, 4)
            row['average_bytes'] = (
                int(row.get('average_bytes') or 0) + average_bytes)
            row['tb'] = round(
                int(row.get('average_bytes') or 0) / (1024 ** 4), 4)
            if status != 'ok':
                row['status'] = status
                row['message'] = message or row.get('message')
            row['finished_at'] = finished_at
            return
        self._import_collectors.append({
            'service_type': service_type,
            'source': source,
            'records': records,
            'credits': credits,
            'average_bytes': average_bytes,
            'tb': tb,
            'status': status,
            'message': message,
            'finished_at': finished_at,
        })

    def _load_product_map(self, cursor, account_locator):
        """Load PRODUCT tags keyed by (RESOURCE_NAME, RESOURCE_TYPE)."""
        product_map = {}
        yq = year_quarter()
        try:
            cursor.execute(
                load_sql('resource_product_mapping.sql'),
                (account_locator, yq))
            columns = [c[0].lower() for c in cursor.description]
            for row in cursor:
                record = dict(zip(columns, row))
                name = record.get('resource_name')
                rtype = record.get('resource_type')
                product = record.get('product')
                if not name or not rtype or not product:
                    continue
                product_map[(str(name).upper(), str(rtype).upper())] = product
            LOG.info(
                'Snowflake product map loaded: account=%s yearquarter=%s '
                'entries=%s', account_locator, yq, len(product_map))
        except Exception as exc:
            warning = (
                'Snowflake product mapping skipped: unable to query '
                'RESOURCE_PRODUCT_MAPPING (%s)' % exc)
            self._import_warnings.append(warning)
            LOG.warning(warning)
        return product_map

    @staticmethod
    def _collector_failure_warning(service_type, exc):
        message = str(exc)
        lowered = message.lower()
        if any(token in lowered for token in (
                'does not exist', 'not authorized', 'insufficient privileges',
                'object does not exist', 'unknown user-defined function')):
            return (
                'Snowflake collector %s skipped: view unavailable or '
                'missing privileges (%s)' % (service_type, message))
        return 'Snowflake collector %s failed: %s' % (service_type, message)

    def _reconcile_credits(self, cursor, start_ts, end_ts, detail_credits):
        """Compare dedicated collectors vs METERING_DAILY_HISTORY.

        Warnings only — never blocks import. Threshold: 5%.
        Excludes the last RECONCILE_LAG_DAYS (ACCOUNT_USAGE latency).
        """
        warnings = []
        reconciliation = []
        # Compare only settled days; recent ACCOUNT_USAGE rows lag.
        reconcile_end = end_ts.date() - timedelta(days=RECONCILE_LAG_DAYS)
        reconcile_start = start_ts.date()
        if reconcile_end <= reconcile_start:
            LOG.info(
                'Snowflake reconciliation skipped: window too short after '
                'excluding last %s lag day(s)', RECONCILE_LAG_DAYS)
            reconciliation.append({
                'service_type': None,
                'detail': 0.0,
                'daily': 0.0,
                'delta_pct': 0.0,
                'status': 'skipped',
                'window_start': str(reconcile_start),
                'window_end': str(reconcile_end),
                'message': (
                    'Window too short after excluding last %s lag day(s)'
                    % RECONCILE_LAG_DAYS),
            })
            self._import_reconciliation = reconciliation
            return warnings
        try:
            cursor.execute(
                load_sql('metering_daily_reconcile.sql'),
                (reconcile_start, reconcile_end))
            columns = [c[0].lower() for c in cursor.description]
            daily = defaultdict(float)
            for row in cursor:
                record = dict(zip(columns, row))
                service_type = DAILY_TO_DETAIL_SERVICE.get(
                    record.get('service_type'), record.get('service_type'))
                usage_date = record.get('usage_date')
                if hasattr(usage_date, 'year') and not hasattr(
                        usage_date, 'hour'):
                    day = usage_date
                else:
                    day = _to_utc(usage_date)
                    if day is not None:
                        day = day.date()
                if day is None or day < reconcile_start or day >= reconcile_end:
                    continue
                # Prefer pre-adjustment credits so detail collectors
                # (CREDITS_USED) align with daily history.
                compute = float(record.get('credits_used_compute') or 0)
                cloud_svc = float(
                    record.get('credits_used_cloud_services') or 0)
                if compute or cloud_svc:
                    daily[(service_type, day)] += compute + cloud_svc
                else:
                    daily[(service_type, day)] += float(
                        record.get('credits_billed') or 0)
        except Exception as exc:
            message = (
                'Snowflake reconciliation skipped: unable to query '
                'METERING_DAILY_HISTORY (%s)' % exc)
            warnings.append(message)
            reconciliation.append({
                'service_type': None,
                'detail': 0.0,
                'daily': 0.0,
                'delta_pct': 0.0,
                'status': 'skipped',
                'window_start': str(reconcile_start),
                'window_end': str(reconcile_end),
                'message': message,
            })
            self._import_reconciliation = reconciliation
            return warnings

        detail_by_type = defaultdict(float)
        daily_by_type = defaultdict(float)
        for (service_type, day), value in detail_credits.items():
            if service_type not in DETAIL_RECONCILE_TYPES:
                continue
            if day is None or day < reconcile_start or day >= reconcile_end:
                continue
            detail_by_type[service_type] += value
        for (service_type, day), value in daily.items():
            if service_type in DETAIL_RECONCILE_TYPES:
                daily_by_type[service_type] += value

        for service_type in sorted(
                set(detail_by_type) | set(daily_by_type)):
            detail_total = detail_by_type.get(service_type, 0.0)
            daily_total = daily_by_type.get(service_type, 0.0)
            baseline = max(detail_total, daily_total, 1e-9)
            delta = abs(detail_total - daily_total)
            ratio = delta / baseline
            delta_pct = round(ratio * 100, 2)
            status = 'mismatch' if ratio > RECONCILE_THRESHOLD else 'ok'
            entry = {
                'service_type': service_type,
                'detail': round(detail_total, 4),
                'daily': round(daily_total, 4),
                'delta_pct': delta_pct,
                'status': status,
                'window_start': str(reconcile_start),
                'window_end': str(reconcile_end),
                'message': None,
            }
            reconciliation.append(entry)
            if status == 'mismatch':
                warnings.append(
                    'Snowflake reconciliation mismatch for %s: '
                    'detail=%.4f daily=%.4f delta=%.2f%% '
                    '(threshold=%.0f%%, window=%s..%s)' % (
                        service_type, detail_total, daily_total,
                        delta_pct, RECONCILE_THRESHOLD * 100,
                        reconcile_start, reconcile_end))
            else:
                LOG.info(
                    'Snowflake reconciliation OK for %s: detail=%.4f '
                    'daily=%.4f delta=%.2f%% (window=%s..%s)',
                    service_type, detail_total, daily_total, delta_pct,
                    reconcile_start, reconcile_end)
        self._import_reconciliation = reconciliation
        return warnings

    def get_import_warnings(self):
        return list(getattr(self, '_import_warnings', []) or [])

    @staticmethod
    def _aggregate_collectors(collectors):
        """Collapse collectors that share service_type (e.g. AI_SERVICES)."""
        # Higher rank wins when merging sources; keep in_progress below ok so a
        # finished source does not get stuck as in_progress.
        status_rank = {
            'in_progress': 0, 'ok': 1, 'skipped': 2, 'failed': 3,
        }
        order = []
        by_type = {}
        for row in collectors or []:
            service_type = row.get('service_type') or 'UNKNOWN'
            if service_type not in by_type:
                order.append(service_type)
                by_type[service_type] = {
                    'service_type': service_type,
                    'records': 0,
                    'credits': 0.0,
                    'average_bytes': 0,
                    'tb': 0.0,
                    'status': row.get('status') or 'ok',
                    'message': row.get('message'),
                    'finished_at': row.get('finished_at'),
                }
            agg = by_type[service_type]
            agg['records'] += int(row.get('records') or 0)
            agg['credits'] += float(row.get('credits') or 0)
            agg['average_bytes'] += int(row.get('average_bytes') or 0)
            finished_at = row.get('finished_at')
            if finished_at is not None and (
                    agg['finished_at'] is None
                    or int(finished_at) > int(agg['finished_at'])):
                agg['finished_at'] = finished_at
            status = row.get('status') or 'ok'
            if status_rank.get(status, 0) > status_rank.get(
                    agg['status'], 0):
                agg['status'] = status
                if row.get('message'):
                    agg['message'] = row.get('message')
            elif (status == agg['status'] and row.get('message')
                  and not agg['message']):
                agg['message'] = row.get('message')
        aggregated = []
        for service_type in order:
            agg = by_type[service_type]
            agg['credits'] = round(agg['credits'], 4)
            agg['tb'] = round(agg['average_bytes'] / (1024 ** 4), 4)
            aggregated.append(agg)
        return aggregated

    def get_import_details(self):
        collectors = self._aggregate_collectors(
            getattr(self, '_import_collectors', []) or [])
        reconciliation = list(
            getattr(self, '_import_reconciliation', []) or [])
        warnings = self.get_import_warnings()
        if not collectors and not reconciliation and not warnings:
            return None
        return {
            'collectors': collectors,
            'reconciliation': reconciliation,
            'warnings': warnings,
        }

    def configure_report(self):
        if DEFAULT_CURRENCY != self._currency:
            raise CloudSettingNotSupported(
                "Account currency '%s' doesn’t match organization"
                " currency '%s'" % (DEFAULT_CURRENCY, self._currency))
        return {
            'config_updates': {},
            'warnings': [],
        }

    def set_currency(self, currency):
        self._currency = currency

    def configure_last_import_modified_at(self):
        pass

    def get_regions_coordinates(self, load=True):
        return {}

    def discovery_calls_map(self):
        return {}
