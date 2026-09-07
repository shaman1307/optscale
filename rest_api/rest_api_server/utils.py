import base64
import enum
import hashlib
import io
import json
import logging
import os
import re
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from functools import cache
from string import ascii_letters, digits
from urllib.parse import urlencode

import cryptocode
import json_excel_converter.xlsx.formats as ExcelFormats
import netaddr
from bson import ObjectId
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from json_excel_converter import Converter as ExcelConverter
from json_excel_converter.xlsx import Writer as ExcelWriter, DEFAULT_COLUMN_WIDTH
from opentelemetry import trace
from pymongo.errors import BulkWriteError
from requests import HTTPError
from retrying import retry
from sqlalchemy.exc import InternalError, DatabaseError

from optscale_client.config_client.client import Client as ConfigClient
from rest_api.rest_api_server.exceptions import Err
from tools.cloud_adapter.exceptions import CloudAdapterBaseException
from tools.optscale_exceptions.common_exc import (
    WrongArgumentsException,
    NotFoundException,
    ConflictException,
    FailedDependency,
    ForbiddenException,
    TimeoutException,
    UnauthorizedException,
)
from tools.optscale_exceptions.http_exc import OptHTTPError
from tools.optscale_time import utcfromtimestamp, utcnow

MAX_32_INT = 2 ** 31 - 1
MAX_64_INT = 2 ** 63 - 1
BASE_POOL_EXPENSES_EXPORT_LINK_FORMAT = 'https://{0}/restapi/v2/pool_expenses_exports/{1}'
tp_executor = ThreadPoolExecutor(30)
tp_executor_context = ThreadPoolExecutor(30)
LOG = logging.getLogger(__name__)
GB = 1024 * 1024 * 1024
SECONDS_IN_HOUR = 60 * 60
FERNET_CONFIG_PREFIX = 'v2:'
_FERNET_CONFIG_KDF_INFO = b'optscale-config-fernet-v1'


def singleton(class_):
    instances = {}

    def get_instance(*args, **kwargs):
        if class_ not in instances:
            instances[class_] = class_(*args, **kwargs)
        return instances[class_]

    return get_instance


@singleton
class Config(object):

    def __init__(self):
        etcd_host = os.environ.get('HX_ETCD_HOST')
        etcd_port = int(os.environ.get('HX_ETCD_PORT'))
        self.client = ConfigClient(host=etcd_host, port=etcd_port)

    @property
    def auth_url(self):
        return self.client.auth_url()

    @property
    def keeper_url(self):
        return self.client.keeper_url()

    @property
    def cluster_secret(self):
        return self.client.cluster_secret()

    @property
    def mongo_params(self):
        return self.client.mongo_params()

    @property
    def katara_url(self):
        return self.client.katara_url()

    @property
    def clickhouse_params(self):
        return self.client.clickhouse_params()

    @property
    def insider_url(self):
        return self.client.insider_url()


def humanize_storage_size(size, precision=2):
    suffixes = ('B', 'KB', 'MB', 'GB', 'TB', 'PB', 'ZB', 'YB')
    suff_index = 0
    while size > 1024 and suff_index < len(suffixes):
        suff_index += 1
        size /= 1024.0
    return "%.*f %s" % (precision, size, suffixes[suff_index])


def timestamp_to_date(timestamp):
    return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')


def is_uuid(check_str):
    pattern = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\\Z'
    return bool(re.match(pattern, str(check_str).lower()))


def datetime_to_timestamp(dt):
    return dt.timestamp() if dt else 0


def check_ipv4_addr(address):
    if not address or not netaddr.valid_ipv4(str(address),
                                             netaddr.core.INET_PTON):
        raise ValueError("%s is not an IPv4 address" % address)


def is_valid_port(value):
    try:
        port = int(value)
    except (ValueError, TypeError):
        return False
    if 1 <= port <= 65535:
        return True
    return False


def _check_is_string(name, value):
    if not isinstance(value, str):
        raise WrongArgumentsException(Err.OE0214, [name])


def check_string(name, value):
    if value is None:
        raise_not_provided_exception(name)
    _check_is_string(name, value)
    if value.isspace():
        raise WrongArgumentsException(Err.OE0416, [name])


def check_string_attribute(name, value, min_length=1, max_length=255,
                           check_length=True, allow_empty=False):
    if allow_empty and not value:
        return
    check_string(name, value)
    if check_length and not min_length <= len(value) <= max_length:
        count = ('max %s' % max_length if min_length == 0
                 else '%s-%s' % (min_length, max_length))
        raise WrongArgumentsException(Err.OE0215, [name, count])


def check_dict_attribute(name, value, allow_empty=False):
    if not value and not allow_empty:
        raise_not_provided_exception(name)
    if value and not isinstance(value, dict):
        raise WrongArgumentsException(Err.OE0344, [name])


def check_list_attribute(name, value, allow_empty=False):
    if not value and not allow_empty:
        raise_not_provided_exception(name)
    if value and not isinstance(value, list):
        raise WrongArgumentsException(Err.OE0385, [name])


def check_int_attribute(name, value, min_length=0, max_length=MAX_32_INT,
                        check_length=True):
    if value is None:
        raise_not_provided_exception(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise WrongArgumentsException(Err.OE0223, [name])
    if check_length and not min_length <= value <= max_length:
        raise WrongArgumentsException(
            Err.OE0224, [name, min_length, max_length])


def check_float_attribute(name, value, min_length=0, max_length=MAX_32_INT,
                          check_length=True):
    if value is None:
        raise_not_provided_exception(name)
    if not isinstance(value, float) and not isinstance(value, int):
        raise WrongArgumentsException(Err.OE0466, [name])
    if check_length and not min_length <= value <= max_length:
        raise WrongArgumentsException(
            Err.OE0224, [name, min_length, max_length])


def check_bool_attribute(name, value):
    if not isinstance(value, bool):
        raise WrongArgumentsException(Err.OE0226, [name])


def check_ipv4_attribute(name, value):
    if value is None:
        raise_not_provided_exception(name)
    _check_is_string(name, value)
    try:
        check_ipv4_addr(value)
    except ValueError:
        raise WrongArgumentsException(Err.OE0356, [name])


def check_regex_attribute(name, value):
    if value is None:
        raise_not_provided_exception(name)
    _check_is_string(name, value)
    if not any(map(lambda x: x not in {'?', '*'}, value)):
        raise WrongArgumentsException(Err.OE0496, [name])


def is_valid_meta(metadata):
    try:
        meta = json.loads(metadata)
        if not isinstance(meta, dict):
            return False
    except BaseException:
        return False
    return True


def is_email_format(check_str):
    regex = '^[a-z0-9!#$%&\'*+/=?`{|}~\\^\\-\\+_()]+(\\.[a-z0-9!#$%&\'*+/=?`{|}~\\^\\-\\+_()]+)*' \
            '@[a-z0-9-]+(\\.[a-z0-9-]+)*(\\.[a-z]{2,18})$'
    match = re.match(regex, str(check_str).lower())
    return bool(match)


def get_encryption_key():
    return Config().client.read('/encryption_key').value.encode()


def is_valid_hostname(hostname):
    """http://stackoverflow.com/a/20204811"""
    regex = '(?=^.{1,253}$)(^(((?!-)[a-zA-Z0-9-]{1,63}(?<!-))|((?!-)' \
            '[a-zA-Z0-9-]{1,63}(?<!-)\\.)+[a-zA-Z]{2,63})$)'
    match = re.match(regex, str(hostname).lower())
    return bool(match)


def _is_not_allowed_char(char):
    return char not in (ascii_letters + digits) + '_' + '-' + '.'


def is_allowed_name(name):
    not_allowed = list(filter(_is_not_allowed_char, map(lambda c: c, str(name))))
    if not not_allowed and str(name)[0].isalpha():
        return True
    return False


def strtobool(val):
    val = val.lower()
    if val not in ['true', 'false']:
        raise ValueError('Should be false or true')
    return val == 'true'


def check_duplicates(ordered_pairs):
    d = {}
    for k, v in ordered_pairs:
        if k in d:
            raise KeyError(k)
        else:
            d[k] = v
    return d


def raise_invalid_argument_exception(argument):
    raise WrongArgumentsException(Err.OE0217, [argument])


def raise_not_provided_exception(argument):
    raise WrongArgumentsException(Err.OE0216, [argument])


def raise_does_not_exist_exception(type_, argument):
    raise WrongArgumentsException(Err.OE0005, [type_, argument])


def raise_unexpected_exception(unexpected_params):
    message = ', '.join(unexpected_params)
    raise WrongArgumentsException(Err.OE0212, [message])


def validate_key_in_collection(key, collection):
    if key in collection:
        value = collection.get(key)
        if value is None:
            raise_not_provided_exception(key)


class ModelEncoder(json.JSONEncoder):
    # pylint: disable=E0202
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, enum.Enum):
            return obj.value
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, ObjectId):
            return str(obj)
        return json.JSONEncoder.default(self, obj)


def gen_id():
    return str(uuid.uuid4())


def now_timestamp():
    return int(utcnow().timestamp())


def safe_string(str_, length=20):
    regex = re.compile('[^a-zA-Z0-9 _-]')
    safe_name = regex.sub('', str_)
    return safe_name[:length]


class RetriableException(Exception):
    pass


def should_retry(exception):
    if isinstance(exception, RetriableException):
        return True
    return False


async def run_task(func, *args, **kwargs):
    try:
        res = await func(*args, **kwargs)
    except WrongArgumentsException as ex:
        raise OptHTTPError.from_opt_exception(400, ex)
    except ForbiddenException as ex:
        raise OptHTTPError.from_opt_exception(403, ex)
    except NotFoundException as ex:
        raise OptHTTPError.from_opt_exception(404, ex)
    except ConflictException as ex:
        raise OptHTTPError.from_opt_exception(409, ex)
    except FailedDependency as ex:
        raise OptHTTPError.from_opt_exception(424, ex)
    except CloudAdapterBaseException as ex:
        # TODO: Better handling for cloud exceptions
        raise OptHTTPError(424, Err.OE0433, [str(ex)])
    except TimeoutException as ex:
        raise OptHTTPError.from_opt_exception(503, ex)
    except InternalError as exc:
        if "Deadlock found when trying to get lock" in str(exc):
            LOG.warning('Deadlock found, raising 503: %s', str(exc))
            raise OptHTTPError(503, Err.OE0003, [str(exc)])
        raise
    except DatabaseError as exc:
        if 'Lock wait timeout exceeded' in str(exc):
            LOG.warning('Lock timeout, raising 503 to retry: %s', str(exc))
            raise OptHTTPError(503, Err.OE0003, [str(exc)])
        raise
    except RetriableException as exc:
        LOG.warning('Retry count reached: %s', str(exc))
        raise OptHTTPError(503, Err.OE0003, [str(exc)])
    return res


def bytes_to_gb(num_bytes):
    return num_bytes / GB


def seconds_to_hour(num_seconds):
    return num_seconds / SECONDS_IN_HOUR


def get_http_error_info(exception):
    # TODO: research a better approach for auth (and others) error handling
    try:
        return json.loads(exception.response.text)['error']
    except Exception:
        return {
            'status_code': exception.response.status_code,
            'error_code': Err.OE0435.name,
            'reason': 'Service call error: %s' % str(exception),
            'params': [str(exception)],
        }


def query_url(**query):
    query = {
        key: value for key, value in query.items() if value is not None
    }
    encoded_query = urlencode(query, doseq=True)
    return "?" + encoded_query


def get_nil_uuid():
    return str(uuid.UUID(int=0))


def encode_string(val, decode=False):
    if len(val) == 0:
        return val
    method = base64.b64decode if decode else base64.b64encode
    return method(val.encode('utf-8')).decode('utf-8')


def encoded_tags(tags, decode=False):
    return encoded_map(tags, decode)


def encoded_map(map, decode=False):
    if not map:
        return {}
    new_map = {}
    for k, v in map.items():
        new_key = encode_string(k, decode)
        new_map[new_key] = v
    return new_map


def update_tags(db_value, value, is_report_import=False, decode=True):
    resource_tags = encoded_tags(
        value, decode=True) if decode else value
    db_resource_tags = encoded_tags(
        db_value, decode=True) if decode else db_value
    if db_resource_tags:
        db_update = {db_key: db_value
                     for db_key, db_value in db_resource_tags.items()
                     if db_key not in set(resource_tags.keys()) and (
                         db_key.startswith('aws:') or is_report_import)}
        if db_update:
            resource_tags.update(db_update)
    if decode:
        value = encoded_tags(resource_tags)
    return value


def generate_discovered_cluster_resources_stat(
        newly_discovered_resources, cluster_map, cluster_key='cluster_id'):
    newly_discovered_stat = {}
    for r in newly_discovered_resources:
        cloud_account_id = r['cloud_account_id']
        if not newly_discovered_stat.get(cloud_account_id):
            newly_discovered_stat[cloud_account_id] = {
                'total': 0, 'clusters': set(), 'clustered': 0}
        stat = newly_discovered_stat[cloud_account_id]
        stat['total'] += 1

        cluster = cluster_map.get(r.get(cluster_key))
        if cluster:
            stat['clustered'] += 1
            stat['clusters'].add(r.get('cluster_id'))
    for statistic in list(newly_discovered_stat.values()):
        if 'clusters' in statistic:
            statistic['clusters'] = len(statistic['clusters'])
    return newly_discovered_stat


def _retry_on_mongo_error(exc):
    if isinstance(exc, BulkWriteError):
        # retry if we got error in mongo upsert
        return True
    return False


@retry(retry_on_exception=_retry_on_mongo_error, wait_fixed=2000,
       stop_max_attempt_number=10)
def retry_mongo_upsert(method, *args, **kwargs):
    return method(*args, **kwargs)


def object_to_xlsx(obj):
    with io.BytesIO() as f:
        conv = ExcelConverter()
        conv.convert(obj, ExcelWriter(
            file=f,
            header_formats=(
                ExcelFormats.Bold,
            ),
            column_widths={
                DEFAULT_COLUMN_WIDTH: 30
            },
        ))
        f.seek(0)
        result = f.read()
    return result


def convert_to_safe_filename(name, replace=' ', char_limit=200):
    if not name or not isinstance(name, str):
        return
    # replace spaces
    for r in replace:
        name = name.replace(r, '_')

    # keep only valid ascii chars
    cleaned_filename = unicodedata.normalize('NFKD', name).encode(
        'ASCII', 'ignore').decode()

    # keep only whitelisted chars
    cleaned_filename = ''.join(c for c in cleaned_filename if
                               _is_not_allowed_char(c) is False)
    return cleaned_filename[:char_limit]


def gen_fingerprint(ssh_public_key):
    key = base64.b64decode(ssh_public_key.strip().split()[1].encode('ascii'))
    fp_plain = hashlib.md5(key).hexdigest()
    return ':'.join(a + b for a, b in zip(fp_plain[::2], fp_plain[1::2]))


def get_root_directory_path():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get_encryption_salt():
    return Config().client.encryption_salt()


@cache
def _get_config_fernet():
    """Derive the Fernet key once per process from the etcd master secret.

    The etcd value is high-entropy random bytes, so HKDF is the correct
    primitive: it provides domain separation via ``info`` without the
    work-factor overhead of a password-based KDF.
    """
    master = _get_encryption_salt()
    if isinstance(master, str):
        master = master.encode('utf-8')
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_FERNET_CONFIG_KDF_INFO,
    ).derive(master)
    return Fernet(base64.urlsafe_b64encode(derived))


def encode_config(config_dict):
    payload = json.dumps(config_dict).encode('utf-8')
    token = _get_config_fernet().encrypt(payload).decode('utf-8')
    return FERNET_CONFIG_PREFIX + token


def decode_config(encoded_str):
    if encoded_str is None:
        return None
    if encoded_str.startswith(FERNET_CONFIG_PREFIX):
        token = encoded_str[len(FERNET_CONFIG_PREFIX):].encode('utf-8')
        try:
            data = _get_config_fernet().decrypt(token)
        except InvalidToken as exc:
            raise ValueError('invalid Fernet config token') from exc
        return json.loads(data.decode('utf-8'))
    # Legacy cryptocode
    return json.loads(cryptocode.decrypt(encoded_str, _get_encryption_salt()))


def get_bi_encryption_key():
    return Config().client.read('/bi_settings/encryption_key').value.encode()


def encrypt_bi_meta(value):
    encryption_key = get_bi_encryption_key()
    fernet = Fernet(encryption_key)
    return fernet.encrypt(value.encode()).decode()


def decrypt_bi_meta(value):
    encryption_key = get_bi_encryption_key()
    fernet = Fernet(encryption_key)
    return fernet.decrypt(value.encode()).decode()


VIRTUAL_TAG_BREAKDOWN_PREFIX = 'virtual_tag:'
QUARTER_RE = re.compile(r'^\d{4}Q[1-4]$')


def invoice_month_to_quarter(yyyymm):
    if yyyymm is None or yyyymm == '':
        return None
    value = str(yyyymm)
    if len(value) < 6 or not value[:6].isdigit():
        return None
    year = int(value[:4])
    month = int(value[4:6])
    if month < 1 or month > 12:
        return None
    return '%sQ%s' % (year, (month - 1) // 3 + 1)


def current_quarter(now=None):
    now = now or datetime.now(timezone.utc)
    return invoice_month_to_quarter('%04d%02d' % (now.year, now.month))


def check_quarter(value, name='quarter'):
    if value is None or value == '':
        raise_not_provided_exception(name)
    value = str(value)
    if not QUARTER_RE.match(value):
        raise WrongArgumentsException(Err.OE0218, [name, value])
    return value


def quarters_for_invoice_months(invoice_months):
    quarters = []
    seen = set()
    for month in invoice_months or []:
        quarter = invoice_month_to_quarter(month)
        if quarter and quarter not in seen:
            seen.add(quarter)
            quarters.append(quarter)
    return quarters


def import_quarters_from_payload(resource=None, invoice_month=None):
    """Quarters to write on resource import. Default: current calendar quarter."""
    months = []
    if invoice_month:
        months.append(invoice_month)
    if resource:
        extra = resource.get('invoice_months')
        if extra is None:
            extra = []
        elif isinstance(extra, str):
            extra = [extra]
        months.extend(extra)
        if resource.get('invoice_month'):
            months.append(resource.get('invoice_month'))
    return quarters_for_invoice_months(months) or [current_quarter()]


def virtual_tags_for_quarter(resource, quarter):
    """Allocations for a quarter; fall back to legacy virtual_tags."""
    by_quarter = resource.get('virtual_tags_by_quarter') or {}
    if isinstance(by_quarter, dict) and quarter in by_quarter:
        return by_quarter.get(quarter) or []
    return resource.get('virtual_tags') or []


def merge_virtual_tags_for_quarters(resource, quarters):
    if not quarters:
        quarters = [current_quarter()]
    merged = []
    seen = set()
    for quarter in quarters:
        for alloc in virtual_tags_for_quarter(resource, quarter):
            if not isinstance(alloc, dict):
                continue
            ident = (
                alloc.get('key'), alloc.get('value'),
                int(alloc.get('share') or 0))
            if ident in seen:
                continue
            seen.add(ident)
            merged.append({
                'key': alloc.get('key'),
                'value': alloc.get('value'),
                'share': int(alloc.get('share') or 0),
            })
    return merged


def is_virtual_tag_breakdown(breakdown_by):
    return bool(breakdown_by) and str(breakdown_by).startswith(
        VIRTUAL_TAG_BREAKDOWN_PREFIX)


def virtual_tag_breakdown_key(breakdown_by):
    return str(breakdown_by).split(':', 1)[1]


def resource_virtual_tag_cost_share(virtual_tags, vt_params):
    """Fraction of cost (0..1) attributed to the VT filter.

    No filter or more than one VT key → 1.0 (full billed cost).
    One key → sum(matching allocation shares) / 100.
    """
    if not vt_params:
        return 1.0
    keys = set()
    values_by_key = {}
    key_only = set()
    nil_uuid = get_nil_uuid()
    for raw in vt_params:
        if raw is None or str(raw) == nil_uuid:
            continue
        token = str(raw)
        if ':' not in token:
            keys.add(token)
            key_only.add(token)
            continue
        key, value = token.split(':', 1)
        keys.add(key)
        values_by_key.setdefault(key, set()).add(value)
    if len(keys) != 1:
        return 1.0
    key = next(iter(keys))
    wanted = None if key in key_only else values_by_key.get(key)
    total = 0.0
    for alloc in virtual_tags or []:
        if not isinstance(alloc, dict) or alloc.get('key') != key:
            continue
        if wanted is not None and alloc.get('value') not in wanted:
            continue
        total += float(alloc.get('share') or 0)
    return total / 100.0


def virtual_tag_filter_values_for_key(vt_params, key):
    """Allowed values for a VT key, or None when every value of the key counts."""
    if not vt_params or not key:
        return None
    wanted = set()
    key_only = False
    matched = False
    for raw in vt_params:
        if raw is None:
            continue
        token = str(raw)
        if token == key:
            matched = True
            key_only = True
            continue
        prefix = '%s:' % key
        if token.startswith(prefix):
            matched = True
            wanted.add(token[len(prefix):])
    if not matched or key_only:
        return None
    return wanted


def load_virtual_tag_cost_shares(collection, resource_ids, vt_params,
                                 invoice_months=None):
    """Map resource id → cost fraction, or id → {invoice_month: fraction}."""
    if not vt_params or not resource_ids:
        return {}
    quarters = quarters_for_invoice_months(invoice_months)
    mixed = len(quarters) > 1
    result = {}
    for doc in collection.find(
            {'_id': {'$in': list(resource_ids)}},
            ['virtual_tags', 'virtual_tags_by_quarter']):
        if mixed:
            by_month = {}
            for month in invoice_months:
                quarter = invoice_month_to_quarter(month)
                by_month[month] = resource_virtual_tag_cost_share(
                    virtual_tags_for_quarter(doc, quarter), vt_params)
            result[doc['_id']] = by_month
            continue
        quarter = quarters[0] if quarters else current_quarter()
        result[doc['_id']] = resource_virtual_tag_cost_share(
            virtual_tags_for_quarter(doc, quarter), vt_params)
    return result


def build_virtual_tag_mongo_filter(vt_params, nil_uuid, quarters=None):
    """Match resources whose VT list (per quarter) contains key[+value]."""
    vt_filter = []
    quarter_keys = list(quarters or [])
    for value in vt_params or []:
        if value == nil_uuid:
            empty = [
                {'virtual_tags': {'$exists': False}},
                {'virtual_tags': None},
                {'virtual_tags': []},
            ]
            for quarter in quarter_keys:
                field = 'virtual_tags_by_quarter.%s' % quarter
                empty.extend([
                    {field: {'$exists': False}},
                    {field: None},
                    {field: []},
                ])
            vt_filter.append({'$or': empty})
            continue
        if ':' not in str(value):
            match = {'key': value}
        else:
            key, tag_value = str(value).split(':', 1)
            match = {'key': key, 'value': tag_value}
        paths = ['virtual_tags']
        paths.extend(
            'virtual_tags_by_quarter.%s' % quarter
            for quarter in quarter_keys)
        vt_filter.append({
            '$or': [
                {path: {'$elemMatch': match}}
                for path in paths
            ]
        })
    if not vt_filter:
        return None
    return {'$or': vt_filter}


class SupportedFiltersMixin(object):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.list_filters = [
            'owner_id', 'pool_id', 'cloud_account_id', 'service_name',
            'region', 'resource_type', 'created_by_kind',
            'created_by_name', 'k8s_namespace', 'k8s_node',
            'k8s_service', 'account_locator', 'tag', 'without_tag',
            'traffic_from', 'traffic_to', '_id', 'meta', 'virtual_tag'
        ]
        self.bool_filters = [
            'active', 'recommendations', 'constraint_violated'
        ]
        self.str_filters = [
            'name_like', 'cloud_resource_id_like'
        ]
        self.int_filters = [
            'first_seen_gte', 'first_seen_lte', 'last_seen_gte',
            'last_seen_lte']


def _get_http_error_message(ex):
    try:
        return json.loads(ex.response.text)['message']
    except Exception:
        return str(ex)


def handle_http_exc(func):
    def inner(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except HTTPError as ex:
            # must not be raised in real world
            if ex.response.status_code == 400:
                # track possible difference in validation
                raise WrongArgumentsException(
                    Err.OE0287, [_get_http_error_message(ex)])
            elif ex.response.status_code == 401:
                # track possible token related problems
                if _get_http_error_message(ex) == 'Token is disabled':
                    raise ForbiddenException(Err.OE0234, [])
                raise UnauthorizedException(
                    Err.OE0543, [_get_http_error_message(ex)])
            elif ex.response.status_code == 403:
                raise ForbiddenException(Err.OE0234, [])
            raise
    return inner


def timestamp_to_day_start(timestamp) -> datetime:
    return utcfromtimestamp(timestamp).replace(
        hour=0, minute=0, second=0, microsecond=0)
