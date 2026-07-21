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
RECONCILE_THRESHOLD = 0.05
# Exclude recent days from reconcile; ACCOUNT_USAGE daily rows lag.
RECONCILE_LAG_DAYS = 2
SQL_DIR = Path(__file__).resolve().parent / 'snowflake_queries'


@lru_cache(maxsize=None)
def load_sql(name: str) -> str:
    path = SQL_DIR / name
    if not path.is_file():
        raise FileNotFoundError('Snowflake SQL file not found: %s' % path)
    return path.read_text(encoding='utf-8').strip()


def calculate_cost(record, cost_model):
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
    return 0.0


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


class SnowflakeUsageCollector(ABC):
    SQL_FILE: str
    SERVICE_CATEGORY: str
    SERVICE_TYPE: str

    @abstractmethod
    def fetch(self, cursor, start_ts: datetime, end_ts: datetime,
              account_locator: str) -> Iterator[dict]:
        raise NotImplementedError

    def _execute(self, cursor, params):
        cursor.execute(load_sql(self.SQL_FILE), params)
        columns = [c[0].lower() for c in cursor.description]
        for row in cursor:
            yield dict(zip(columns, row))


class WarehouseMeteringCollector(SnowflakeUsageCollector):
    SQL_FILE = 'warehouse_metering_history.sql'
    SERVICE_CATEGORY = 'compute'
    SERVICE_TYPE = 'WAREHOUSE_METERING'

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
    SERVICE_TYPE = 'DATABASE_STORAGE'

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
    SERVICE_TYPE = 'STAGE_STORAGE'

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
                'resource_id': f"{account_locator}/stages",
                'resource_name': 'stages',
                'average_bytes': int(record.get('average_stage_bytes') or 0),
            }


class PipeUsageCollector(SnowflakeUsageCollector):
    SQL_FILE = 'pipe_usage_history.sql'
    SERVICE_CATEGORY = 'serverless'
    SERVICE_TYPE = 'PIPE'

    def fetch(self, cursor, start_ts, end_ts, account_locator):
        for record in self._execute(cursor, (start_ts, end_ts)):
            start = _to_utc(record.get('start_time'))
            end = _to_utc(record.get('end_time')) or start
            pipe_id = record.get('pipe_id')
            yield {
                'start_date': start,
                'end_date': end,
                'service_category': self.SERVICE_CATEGORY,
                'service_type': self.SERVICE_TYPE,
                'account_locator': account_locator,
                'resource_id': f"{account_locator}/pipe/{pipe_id}",
                'resource_name': record.get('pipe_name'),
                'pipe_id': pipe_id,
                'credits_used': float(record.get('credits_used') or 0),
                'bytes_inserted': float(record.get('bytes_inserted') or 0),
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
    SERVICE_TYPE = 'AI_FUNCTIONS'

    def fetch(self, cursor, start_ts, end_ts, account_locator):
        for record in self._execute(cursor, (start_ts, end_ts)):
            start = _to_utc(record.get('start_time'))
            end = _to_utc(record.get('end_time')) or start
            metrics = parse_metrics(record.get('metrics'))
            query_id = record.get('query_id')
            function_name = record.get('function_name')
            model_name = record.get('model_name')
            yield {
                'start_date': start,
                'end_date': end,
                'service_category': self.SERVICE_CATEGORY,
                'service_type': self.SERVICE_TYPE,
                'account_locator': account_locator,
                'resource_id': (
                    f"{account_locator}/{query_id}/"
                    f"{function_name}/{model_name}"),
                'resource_name': model_name or function_name,
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


COLLECTORS = [
    WarehouseMeteringCollector,
    DatabaseStorageCollector,
    StageStorageCollector,
    PipeUsageCollector,
    MeteringDailyCollector,
    CortexAiFunctionsCollector,
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

    def download_usage(self, start_ts, end_ts):
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
        conn = self.connect()
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT CURRENT_ACCOUNT()')
            account_locator = str(cursor.fetchone()[0])
            # Sequential collectors (max 1 concurrent Snowflake query).
            for collector_cls in COLLECTORS:
                collector = collector_cls()
                records = 0
                credits = 0.0
                average_bytes = 0
                status = 'ok'
                message = None
                try:
                    for record in collector.fetch(
                            cursor, start_ts, end_ts, account_locator):
                        records += 1
                        used = float(record.get('credits_used') or 0)
                        if used:
                            credits += used
                            day = record['start_date']
                            if hasattr(day, 'date'):
                                day = day.date()
                            detail_credits[
                                (record.get('service_type'), day)] += used
                        average_bytes += int(
                            record.get('average_bytes') or 0)
                        yield record
                except Exception as exc:
                    warning = self._collector_failure_warning(
                        collector.SERVICE_TYPE, exc)
                    self._import_warnings.append(warning)
                    LOG.warning(warning)
                    status = (
                        'skipped' if 'skipped:' in warning else 'failed')
                    message = warning
                else:
                    LOG.info(
                        'Snowflake collector %s: records=%s credits=%.4f '
                        'tb=%.4f',
                        collector.SERVICE_TYPE, records, credits,
                        average_bytes / (1024 ** 4))
                self._import_collectors.append({
                    'service_type': collector.SERVICE_TYPE,
                    'records': records,
                    'credits': round(credits, 4),
                    'average_bytes': average_bytes,
                    'tb': round(average_bytes / (1024 ** 4), 4),
                    'status': status,
                    'message': message,
                    'finished_at': int(
                        datetime.now(timezone.utc).timestamp()),
                })

            for warning in self._reconcile_credits(
                    cursor, start_ts, end_ts, detail_credits):
                self._import_warnings.append(warning)
                LOG.warning(warning)
            cursor.close()
        finally:
            self.close()

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
                service_type = record.get('service_type')
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
                # PIPE detail maps to PIPE/SNOWPIPE daily rows.
                if service_type == 'SNOWPIPE':
                    service_type = 'PIPE'
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
            if service_type not in ('WAREHOUSE_METERING', 'PIPE'):
                continue
            if day is None or day < reconcile_start or day >= reconcile_end:
                continue
            detail_by_type[service_type] += value
        for (service_type, day), value in daily.items():
            if service_type in ('WAREHOUSE_METERING', 'PIPE'):
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

    def get_import_details(self):
        collectors = list(getattr(self, '_import_collectors', []) or [])
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
