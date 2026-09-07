"""Collapse ephemeral GCP Dataproc / Composer / GKE members into one resource.

Priority matches the org cluster types: Dataproc, then Composer (Composer
nodes also carry GKE labels), then GKE. Resources without these labels are
unchanged.

Dataproc identity is the same in every project:
- Serverless batches (batch uuid/id or srvls-batch-* name) share one id per
  Airflow DAG (or dataproc/serverless when the DAG label is missing).
- Classic clusters keep goog-dataproc-cluster-uuid.
Serverless does not require goog-dataproc-cluster-uuid (SKU shuffle/DCU
rows often have only batch-uuid + airflow-dag-id).
"""
import base64
import re

DATAPROC_CLUSTER_UUID_TAG = 'goog-dataproc-cluster-uuid'
DATAPROC_CLUSTER_NAME_TAG = 'goog-dataproc-cluster-name'
DATAPROC_BATCH_UUID_TAG = 'goog-dataproc-batch-uuid'
DATAPROC_BATCH_ID_TAG = 'goog-dataproc-batch-id'
AIRFLOW_DAG_ID_TAG = 'airflow-dag-id'
AIRFLOW_DAG_DISPLAY_NAME_TAG = 'airflow-dag-display-name'
COMPOSER_UUID_TAG = 'goog-composer-environment-uuid'
COMPOSER_NAME_TAG = 'goog-composer-environment'
GKE_NAME_TAG = 'goog-k8s-cluster-name'
GKE_VOLUME_TAG = 'goog-gke-volume'
PVC_NAME_PREFIX = 'pvc-'
SRVLS_BATCH_PREFIX = 'srvls-batch-'
# Watch-only (Duplicate groups). Not used by _generate_resource_id.
DATAFLOW_JOB_ID_TAG = 'goog-dataflow-job-id'


def encode_tag_key(key):
    """Mongo resource tag keys are stored base64; raw expense tags are not."""
    return base64.b64encode(str(key).encode('utf-8')).decode('ascii')


ENCODED_AIRFLOW_DAG_ID_TAG = encode_tag_key(AIRFLOW_DAG_ID_TAG)
ENCODED_AIRFLOW_DAG_ID_FIELD = 'tags.%s' % ENCODED_AIRFLOW_DAG_ID_TAG
PLAIN_AIRFLOW_DAG_ID_FIELD = 'tags.%s' % AIRFLOW_DAG_ID_TAG
ENCODED_DATAPROC_CLUSTER_UUID_TAG = encode_tag_key(DATAPROC_CLUSTER_UUID_TAG)
ENCODED_DATAPROC_CLUSTER_UUID_FIELD = (
    'tags.%s' % ENCODED_DATAPROC_CLUSTER_UUID_TAG)
PLAIN_DATAPROC_CLUSTER_UUID_FIELD = 'tags.%s' % DATAPROC_CLUSTER_UUID_TAG
PLAIN_DATAPROC_BATCH_UUID_FIELD = 'tags.%s' % DATAPROC_BATCH_UUID_TAG
PLAIN_DATAPROC_BATCH_ID_FIELD = 'tags.%s' % DATAPROC_BATCH_ID_TAG
ENCODED_COMPOSER_UUID_TAG = encode_tag_key(COMPOSER_UUID_TAG)
ENCODED_COMPOSER_UUID_FIELD = 'tags.%s' % ENCODED_COMPOSER_UUID_TAG
PLAIN_COMPOSER_UUID_FIELD = 'tags.%s' % COMPOSER_UUID_TAG
ENCODED_GKE_NAME_TAG = encode_tag_key(GKE_NAME_TAG)
ENCODED_GKE_NAME_FIELD = 'tags.%s' % ENCODED_GKE_NAME_TAG
PLAIN_GKE_NAME_FIELD = 'tags.%s' % GKE_NAME_TAG
ENCODED_GKE_VOLUME_TAG = encode_tag_key(GKE_VOLUME_TAG)
ENCODED_GKE_VOLUME_FIELD = 'tags.%s' % ENCODED_GKE_VOLUME_TAG
PLAIN_GKE_VOLUME_FIELD = 'tags.%s' % GKE_VOLUME_TAG
# Skip ids already owned by Dataproc / Composer / labeled GKE / SQL / Run.
UNLABELED_GKE_PVC_ID_SKIP_REGEX = (
    r'^(?!(gke|composer|dataproc|cloudsql|cloudrun|function)/)'
)
CLOUD_SQL_BACKUP_ID_TAG = 'cloud_sql_backup_id'
CLOUD_SQL_BACKUP_NAME_RE = re.compile(r'^(.+)-backup-\d+$')
CLOUD_RUN_SERVICE_TAG = 'goog-cloud-run-service'
CLOUD_RUN_SERVICE_ALT_TAG = 'run.googleapis.com/service'
CLOUD_FUNCTION_NAME_TAG = 'goog-cloudfunctions-function'
CLOUD_FUNCTION_DEPLOY_TAG = 'deployment-function'
_SQLADMIN_INSTANCE_RE = re.compile(
    r'//(?:sqladmin|cloudsql)\.googleapis\.com/projects/[^/]+/'
    r'(?:locations/[^/]+/)?instances/([^/]+)(?:/.*)?$'
)
_RUN_SERVICE_RE = re.compile(
    r'//run\.googleapis\.com/projects/[^/]+/locations/[^/]+/'
    r'services/([^/]+)(?:/.*)?$'
)
_FUNCTION_RE = re.compile(
    r'//cloudfunctions\.googleapis\.com/projects/[^/]+/locations/[^/]+/'
    r'functions/([^/]+)(?:/.*)?$'
)
_DATAFLOW_JOB_RE = re.compile(
    r'//dataflow\.googleapis\.com/projects/[^/]+/'
    r'(?:locations/[^/]+/)?jobs/([^/]+)(?:/.*)?$'
)
_ALLOYDB_CLUSTER_RE = re.compile(
    r'//alloydb\.googleapis\.com/projects/[^/]+/'
    r'(?:locations/[^/]+/)?clusters/([^/]+)(?:/.*)?$'
)
_BIGTABLE_RE = re.compile(
    r'//bigtableadmin\.googleapis\.com/projects/[^/]+/instances/([^/]+)'
    r'(?:/.*)?$|'
    r'//bigtable\.googleapis\.com/projects/[^/]+/instances/([^/]+)(?:/.*)?$'
)
_FILESTORE_RE = re.compile(
    r'//file\.googleapis\.com/projects/[^/]+/'
    r'(?:locations/[^/]+/)?instances/([^/]+)(?:/.*)?$'
)
_DATAFUSION_RE = re.compile(
    r'//datafusion\.googleapis\.com/projects/[^/]+/'
    r'(?:locations/[^/]+/)?instances/([^/]+)(?:/.*)?$'
)
_METASTORE_RE = re.compile(
    r'//metastore\.googleapis\.com/projects/[^/]+/'
    r'(?:locations/[^/]+/)?services/([^/]+)(?:/.*)?$'
)
_WATCH_GLOBAL_NAME_RULES = (
    ('Dataflow', 'dataflow', _DATAFLOW_JOB_RE),
    ('AlloyDB', 'alloydb', _ALLOYDB_CLUSTER_RE),
    ('Bigtable', 'bigtable', _BIGTABLE_RE),
    ('Filestore', 'filestore', _FILESTORE_RE),
    ('Data Fusion', 'datafusion', _DATAFUSION_RE),
    ('Dataproc Metastore', 'dataproc/metastore', _METASTORE_RE),
)
_WATCH_ID_PREFIXES = (
    ('dataproc/metastore/', 'Dataproc Metastore', 'dataproc/metastore'),
    ('dataflow/', 'Dataflow', 'dataflow'),
    ('alloydb/', 'AlloyDB', 'alloydb'),
    ('bigtable/', 'Bigtable', 'bigtable'),
    ('filestore/', 'Filestore', 'filestore'),
    ('datafusion/', 'Data Fusion', 'datafusion'),
)
_WATCH_TAG_KEYS = (DATAFLOW_JOB_ID_TAG,)

_COLLAPSE_TAG_KEYS = (
    DATAPROC_CLUSTER_UUID_TAG,
    DATAPROC_CLUSTER_NAME_TAG,
    DATAPROC_BATCH_UUID_TAG,
    DATAPROC_BATCH_ID_TAG,
    AIRFLOW_DAG_ID_TAG,
    AIRFLOW_DAG_DISPLAY_NAME_TAG,
    COMPOSER_UUID_TAG,
    COMPOSER_NAME_TAG,
    GKE_NAME_TAG,
    GKE_VOLUME_TAG,
    CLOUD_SQL_BACKUP_ID_TAG,
    CLOUD_RUN_SERVICE_TAG,
    CLOUD_RUN_SERVICE_ALT_TAG,
    CLOUD_FUNCTION_NAME_TAG,
    CLOUD_FUNCTION_DEPLOY_TAG,
)

# Pre-DAG collapse wrote dataproc/<cluster-uuid> for every serverless batch.
DATAPROC_UUID_RESOURCE_ID_REGEX = (
    r'^dataproc/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
    r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
)
DATAPROC_UUID_RESOURCE_ID_RE = re.compile(DATAPROC_UUID_RESOURCE_ID_REGEX)
# Billing fallback when detailed export has no compute/GCS identity.
# Same shape as sku.id (e.g. 9E4E-F9A7-5EAE). Not a discovery leftover.
GCP_BILLING_SKU_ID_REGEX = (
    r'^[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}$'
)
GCP_BILLING_SKU_ID_RE = re.compile(GCP_BILLING_SKU_ID_REGEX)

# (resource_type, identity tag, display-name tag) after Dataproc.
GCP_COLLAPSE_RULES = (
    ('Composer', COMPOSER_UUID_TAG, COMPOSER_NAME_TAG),
    ('GKE', GKE_NAME_TAG, GKE_NAME_TAG),
)


def _tag_str(tags, key):
    value = tags.get(key)
    if value is None:
        return ''
    return str(value).strip()


def is_dataproc_serverless(tags):
    """True when labels mark a Dataproc Serverless batch, not a GCE cluster."""
    if not isinstance(tags, dict):
        return False
    if _tag_str(tags, DATAPROC_BATCH_UUID_TAG) or _tag_str(tags, DATAPROC_BATCH_ID_TAG):
        return True
    return _tag_str(tags, DATAPROC_CLUSTER_NAME_TAG).startswith(SRVLS_BATCH_PREFIX)


def tags_for_collapse(tags):
    """Plaintext GCP labels, decoding Mongo base64 keys when needed."""
    if not isinstance(tags, dict) or not tags:
        return {}
    if any(key in tags for key in _COLLAPSE_TAG_KEYS):
        return tags
    encoded_to_plain = {encode_tag_key(key): key for key in _COLLAPSE_TAG_KEYS}
    out = dict(tags)
    for encoded, plain in encoded_to_plain.items():
        if encoded in tags and plain not in out:
            out[plain] = tags[encoded]
    return out


def _dataproc_serverless_identity(tags):
    dag = _tag_str(tags, AIRFLOW_DAG_ID_TAG)
    if dag:
        name = _tag_str(tags, AIRFLOW_DAG_DISPLAY_NAME_TAG) or dag
        cloud_resource_id = 'dataproc/dag/%s' % dag
        stable = dag
    else:
        name = 'Dataproc Serverless'
        cloud_resource_id = 'dataproc/serverless'
        stable = 'serverless'
    return {
        'resource_type': 'Dataproc',
        'cloud_resource_id': cloud_resource_id,
        'name': name,
        'tag_overrides': {
            DATAPROC_CLUSTER_UUID_TAG: stable,
            DATAPROC_CLUSTER_NAME_TAG: name,
        },
    }


def gcp_collapse_identity(tags):
    """Return collapsed identity from plaintext GCP labels, or None."""
    tags = tags_for_collapse(tags)
    if not tags:
        return None
    cluster_uuid = _tag_str(tags, DATAPROC_CLUSTER_UUID_TAG)
    if cluster_uuid:
        if is_dataproc_serverless(tags):
            return _dataproc_serverless_identity(tags)
        name = _tag_str(tags, DATAPROC_CLUSTER_NAME_TAG) or cluster_uuid
        return {
            'resource_type': 'Dataproc',
            'cloud_resource_id': 'dataproc/%s' % cluster_uuid,
            'name': name,
        }
    if is_dataproc_serverless(tags):
        return _dataproc_serverless_identity(tags)
    for resource_type, id_key, name_key in GCP_COLLAPSE_RULES:
        value = _tag_str(tags, id_key)
        if not value:
            continue
        name = _tag_str(tags, name_key) or value
        return {
            'resource_type': resource_type,
            'cloud_resource_id': '%s/%s' % (resource_type.lower(), value),
            'name': name,
        }
    return None


def _prefixed_identity(resource_type, prefix, value):
    name = str(value or '').strip().rstrip('/')
    if not name:
        return None
    return {
        'resource_type': resource_type,
        'cloud_resource_id': '%s/%s' % (prefix, name),
        'name': name,
    }


def gcp_detailed_collapse_identity(row):
    """Cloud SQL / Cloud Run / Functions / SQL-backup identity from billing.

    Uses resource.global_name (detailed export) or a snapshot name ending in
    -backup-<epoch>. Bare SKU rows without those fields stay on sku.id.
    Ordinary PD snapshots without a backup marker are unchanged.
    """
    if not isinstance(row, dict):
        return None
    gname = str(row.get('resource_global_name') or '')
    match = _SQLADMIN_INSTANCE_RE.search(gname)
    if match:
        return _prefixed_identity('Cloud SQL', 'cloudsql', match.group(1))
    match = _RUN_SERVICE_RE.search(gname)
    if match:
        return _prefixed_identity('Cloud Run', 'cloudrun', match.group(1))
    match = _FUNCTION_RE.search(gname)
    if match:
        return _prefixed_identity(
            'Cloud Run Functions', 'function', match.group(1))
    backup = cloudsql_backup_collapse_identity(row)
    if backup:
        return backup
    return serverless_ip_collapse_identity(row)


def cloudsql_backup_collapse_identity(row):
    """Fold Compute SQL backups onto cloudsql/<instance>, never ordinary PD."""
    if not isinstance(row, dict):
        return None
    tags = tags_for_collapse(row.get('tags') or {})
    name = _short_resource_name(row)
    sku = str(row.get('sku') or '').lower()
    gname = str(row.get('resource_global_name') or '')
    tagged = bool(_tag_str(tags, CLOUD_SQL_BACKUP_ID_TAG))
    backup_name = CLOUD_SQL_BACKUP_NAME_RE.match(name) if name else None
    if not tagged and not backup_name:
        return None
    if not tagged:
        if 'snapshot' not in sku and '/snapshots/' not in gname:
            return None
    if backup_name:
        return _prefixed_identity('Cloud SQL', 'cloudsql', backup_name.group(1))
    return None


def serverless_ip_collapse_identity(row):
    """Fold serverless-ipv4 onto Run/Function only when an owner label exists.

    Unlabeled IPs stay IP Address. Do not invent cloudrun/ips/<project>.
    Ordinary named IPs and GKE gateways without goog-k8s-cluster-name stay.
    """
    if not isinstance(row, dict):
        return None
    name = _short_resource_name(row).lower()
    if 'serverless-ipv4' not in name:
        return None
    tags = tags_for_collapse(row.get('tags') or {})
    service = (
        _tag_str(tags, CLOUD_RUN_SERVICE_TAG)
        or _tag_str(tags, CLOUD_RUN_SERVICE_ALT_TAG))
    if service:
        return _prefixed_identity('Cloud Run', 'cloudrun', service)
    function = (
        _tag_str(tags, CLOUD_FUNCTION_NAME_TAG)
        or _tag_str(tags, CLOUD_FUNCTION_DEPLOY_TAG))
    if function:
        return _prefixed_identity(
            'Cloud Run Functions', 'function', function)
    return None


def cloudsql_instance_leftover_identity(cloud_resource_id, resource_type=None):
    """Prefix a live Cloud SQL instance leftover that is not a billing SKU."""
    if (resource_type or '') != 'Cloud SQL':
        return None
    rid = str(cloud_resource_id or '').strip()
    if not rid or rid.startswith('cloudsql/'):
        return collapsed_identity_from_resource_id(rid) if rid else None
    if is_gcp_billing_sku_id(rid) or rid.isdigit():
        return None
    return _prefixed_identity('Cloud SQL', 'cloudsql', rid)


def collapsed_identity_from_resource_id(cloud_resource_id):
    """Keeper type/name from a collapsed cloud_resource_id prefix."""
    rid = str(cloud_resource_id or '')
    if rid.startswith('gke/'):
        return gke_collapse_identity(rid.split('/', 1)[1])
    if rid.startswith('composer/'):
        return composer_collapse_identity(rid.split('/', 1)[1])
    if rid.startswith('dataproc/'):
        rest = rid.split('/', 1)[1]
        if rest.startswith('dag/'):
            dag = rest[4:]
            return gcp_collapse_identity({
                AIRFLOW_DAG_ID_TAG: dag,
                DATAPROC_BATCH_UUID_TAG: 'placeholder',
            })
        if rest == 'serverless':
            return gcp_collapse_identity({
                DATAPROC_BATCH_UUID_TAG: 'placeholder',
            })
        return dataproc_collapse_identity(rest)
    if rid.startswith('cloudsql/'):
        return _prefixed_identity('Cloud SQL', 'cloudsql', rid.split('/', 1)[1])
    if rid.startswith('cloudrun/'):
        return _prefixed_identity('Cloud Run', 'cloudrun', rid.split('/', 1)[1])
    if rid.startswith('function/'):
        return _prefixed_identity(
            'Cloud Run Functions', 'function', rid.split('/', 1)[1])
    return None


def collapsed_identity_family(cloud_resource_id):
    """dataproc|composer|gke|cloudsql|cloudrun|function, or None."""
    ident = collapsed_identity_from_resource_id(cloud_resource_id)
    if not ident:
        return None
    return str(ident['cloud_resource_id']).split('/', 1)[0]


def is_foreign_collapsed_identity(cloud_resource_id, ident):
    """True when crid is already a keeper of a different collapse family."""
    other = collapsed_identity_family(cloud_resource_id)
    keeper = (ident or {}).get('cloud_resource_id')
    if not other or not keeper:
        return False
    return other != str(keeper).split('/', 1)[0]


def gcp_row_collapse_identity(row):
    """Tag identity first, then detailed-export SQL/Run/backup identity."""
    ident = gcp_collapse_identity((row or {}).get('tags') or {})
    if ident:
        return ident
    return gcp_detailed_collapse_identity(row or {})


def tags_for_watch(tags):
    """Plaintext labels for Duplicate-groups watch keys (base64 Mongo keys)."""
    out = tags_for_collapse(tags)
    if not isinstance(tags, dict) or not tags:
        return out
    encoded_to_plain = {encode_tag_key(key): key for key in _WATCH_TAG_KEYS}
    for encoded, plain in encoded_to_plain.items():
        if encoded in tags and plain not in out:
            out[plain] = tags[encoded]
    return out


def _watch_match_group(match):
    if not match:
        return ''
    for value in match.groups():
        if value:
            return str(value).strip()
    return ''


def gcp_watch_identity(doc):
    """Duplicate-groups key for families with no collapse yet.

    Dataflow / AlloyDB / Bigtable / Filestore / Data Fusion / Spark Metastore
    are not rewritten by _generate_resource_id. If they land as leftover SKU
    + object (or ephemeral Dataflow workers), Duplicate groups must go
    non-zero. Returns None when no shared watch key exists.
    """
    if not isinstance(doc, dict):
        return None
    tags = tags_for_watch(doc.get('tags') or {})
    job = _tag_str(tags, DATAFLOW_JOB_ID_TAG)
    if job:
        return _prefixed_identity('Dataflow', 'dataflow', job)
    rid = str(doc.get('cloud_resource_id') or '')
    for prefix, resource_type, ident_prefix in _WATCH_ID_PREFIXES:
        if rid.startswith(prefix) and len(rid) > len(prefix):
            return _prefixed_identity(resource_type, ident_prefix, rid[len(prefix):])
    for field in (doc.get('resource_global_name'), doc.get('name'), rid):
        text = str(field or '')
        if not text:
            continue
        for resource_type, prefix, regex in _WATCH_GLOBAL_NAME_RULES:
            value = _watch_match_group(regex.search(text))
            if value:
                return _prefixed_identity(resource_type, prefix, value)
    return None


def collapse_group_id(doc):
    """Duplicate-group key: foldable leftover keeper, else cloud_resource_id.

    Uncollapsed Composer/Dataproc/GKE SKUs and numeric discovery leftovers
    share a keeper even when cloud_resource_id still differs. Inherited
    cluster labels on Artifact Registry / Pub/Sub / buckets / Instance SKUs
    must not group — those are not foldable leftovers.
    Same-id twins still group via cloud_resource_id.
    Watch families (Dataflow / AlloyDB / …) group for Duplicate groups only.
    """
    crid = str((doc or {}).get('cloud_resource_id') or '')
    ident = gcp_row_collapse_identity(doc)
    if ident:
        keeper = ident['cloud_resource_id']
        if crid == keeper:
            return keeper
        # Unlabeled dummy raw: family type / numeric leftover may fold; inherited
        # tags on Instance/Bucket/Pub/Sub must not (cf57077).
        if collapse_leftover_may_fold(
                crid, ident, {'tags': {}},
                mongo_resource_type=(doc or {}).get('resource_type')):
            return keeper
        return crid
    watch = gcp_watch_identity(doc)
    if watch:
        return watch['cloud_resource_id']
    return crid


def serverless_dataproc_raw_rewrite_filter(cloud_account_id):
    """Raw serverless Dataproc rows not yet on dataproc/dag or dataproc/serverless.

    Matches leftover dataproc/<uuid> ids from the first collapse and SKU-keyed
    shuffle/DCU rows that never had a cluster-uuid.
    """
    return {
        'cloud_account_id': cloud_account_id,
        '$or': [
            {'resource_id': {'$regex': DATAPROC_UUID_RESOURCE_ID_REGEX}},
            {'resource_id': {
                '$regex': UNLABELED_GKE_PVC_ID_SKIP_REGEX,
            }},
        ],
        '$and': [{'$or': [
            {('tags.%s' % DATAPROC_BATCH_UUID_TAG): {
                '$exists': True, '$nin': [None, '']}},
            {('tags.%s' % DATAPROC_BATCH_ID_TAG): {
                '$exists': True, '$nin': [None, '']}},
            {('tags.%s' % DATAPROC_CLUSTER_NAME_TAG): {
                '$regex': '^%s' % SRVLS_BATCH_PREFIX}},
        ]}],
    }


SERVERLESS_DATAPROC_DAG_RAW_FIELD = 'tags.%s' % AIRFLOW_DAG_ID_TAG


def serverless_dataproc_collapsed_raw_id(dag_id):
    """Same cloud_resource_id gcp_collapse_identity uses for a serverless batch."""
    tags = {
        DATAPROC_CLUSTER_UUID_TAG: 'placeholder',
        DATAPROC_BATCH_UUID_TAG: 'placeholder',
    }
    dag = _tag_str({AIRFLOW_DAG_ID_TAG: dag_id}, AIRFLOW_DAG_ID_TAG) if dag_id is not None else ''
    if dag:
        tags[AIRFLOW_DAG_ID_TAG] = dag
    ident = gcp_collapse_identity(tags)
    return ident['cloud_resource_id']


def serverless_dataproc_raw_rewrite_updates(cloud_account_id, dag_values):
    """Mongo 3.6-safe $set objects (not a 4.2+ aggregation pipeline).

    One update per stored DAG value, then leftover uuid-keyed serverless
    rows (no DAG) become dataproc/serverless. DAG rows must run first:
    after $set they no longer match the uuid resource_id filter.
    """
    base = serverless_dataproc_raw_rewrite_filter(cloud_account_id)
    updates = []
    seen = set()
    for raw in dag_values or []:
        if raw in (None, ''):
            continue
        dag = _tag_str({AIRFLOW_DAG_ID_TAG: raw}, AIRFLOW_DAG_ID_TAG)
        if not dag:
            continue
        try:
            key = raw
            hash(key)
        except TypeError:
            key = str(raw)
        if key in seen:
            continue
        seen.add(key)
        filt = dict(base)
        filt[SERVERLESS_DATAPROC_DAG_RAW_FIELD] = raw
        updates.append((
            filt,
            {'$set': {'resource_id': serverless_dataproc_collapsed_raw_id(dag)}},
        ))
    updates.append((
        base,
        {'$set': {'resource_id': serverless_dataproc_collapsed_raw_id('')}},
    ))
    return updates


def stale_serverless_dataproc_keeper_set(dag):
    """Identity fields for the one live Mongo doc kept per serverless DAG.

    OptResourceUnique forbids many live docs with the same cloud_resource_id.
    Extra srvls-batch uuid docs must be soft-deleted, not rekeyed in bulk.
    """
    dag = _tag_str({AIRFLOW_DAG_ID_TAG: dag}, AIRFLOW_DAG_ID_TAG)
    if dag:
        name = dag
    else:
        name = 'Dataproc Serverless'
    return {
        'cloud_resource_id': serverless_dataproc_collapsed_raw_id(dag),
        'resource_type': 'Dataproc',
        'name': name,
    }


def collapsed_keeper_set(ident):
    """Identity fields written onto the one live Mongo keeper."""
    if not ident:
        return None
    return {
        'cloud_resource_id': ident['cloud_resource_id'],
        'resource_type': ident['resource_type'],
        'name': ident['name'],
    }


def dataproc_collapse_identity(uuid_value):
    """Classic Dataproc cluster identity (not serverless DAG collapse)."""
    return gcp_collapse_identity({DATAPROC_CLUSTER_UUID_TAG: uuid_value})


def composer_collapse_identity(uuid_value):
    return gcp_collapse_identity({COMPOSER_UUID_TAG: uuid_value})


def gke_collapse_identity(cluster_name):
    return gcp_collapse_identity({GKE_NAME_TAG: cluster_name})


def _short_resource_name(row):
    name = str((row or {}).get('resource_name') or (row or {}).get('name') or '')
    name = name.strip()
    if not name:
        return ''
    return name.rstrip('/').rsplit('/', 1)[-1]


def is_compute_disk_row(row):
    """True for Compute PD rows (detailed disk path or PD Capacity SKU)."""
    if not isinstance(row, dict):
        return False
    gname = str(row.get('resource_global_name') or '')
    if '/disk/' in gname or '/disks/' in gname:
        return True
    return 'pd capacity' in str(row.get('sku') or '').lower()


def is_unlabeled_gke_volume(row):
    """GKE PVC / goog-gke-volume disk that has no cluster-name collapse tag.

    Billing often omits goog-k8s-cluster-name on PD Capacity for PVCs, so
    labeled GKE collapse never sees them. Do not match standalone disks.
    """
    if not isinstance(row, dict):
        return False
    if gcp_collapse_identity(row.get('tags') or {}):
        return False
    if not is_compute_disk_row(row):
        return False
    tags = tags_for_collapse(row.get('tags') or {})
    if GKE_VOLUME_TAG in tags:
        return True
    return _short_resource_name(row).startswith(PVC_NAME_PREFIX)


def unlabeled_gke_volume_collapse_identity(row, fallback_cluster):
    """Fold an unlabeled GKE PVC onto gke/<cluster> when fallback is unique."""
    cluster = str(fallback_cluster or '').strip()
    if not cluster or not is_unlabeled_gke_volume(row):
        return None
    return gke_collapse_identity(cluster)


def _unlabeled_gke_pvc_exclude_labeled_conditions():
    """Do not steal Dataproc / Composer / labeled-GKE members."""
    conditions = []
    for field in (
            PLAIN_DATAPROC_CLUSTER_UUID_FIELD,
            PLAIN_COMPOSER_UUID_FIELD,
            PLAIN_GKE_NAME_FIELD,
    ):
        conditions.append({
            '$or': [
                {field: {'$exists': False}},
                {field: {'$in': [None, '']}},
            ]
        })
    return conditions


def unlabeled_gke_pvc_raw_filter(cloud_account_id):
    """Raw PD rows still keyed as numeric/SKU ids, not a collapsed identity."""
    return {
        'cloud_account_id': cloud_account_id,
        '$and': [
            {'resource_id': {'$regex': UNLABELED_GKE_PVC_ID_SKIP_REGEX}},
            {'$or': [
                {'resource_name': {'$regex': '^%s' % PVC_NAME_PREFIX}},
                {'resource_name': {'$regex': '/%s' % PVC_NAME_PREFIX}},
                {PLAIN_GKE_VOLUME_FIELD: {'$exists': True}},
            ]},
            {'$or': [
                {'sku': {'$regex': 'pd capacity', '$options': 'i'}},
                {'resource_global_name': {'$regex': '/disks?/'}},
            ]},
        ] + _unlabeled_gke_pvc_exclude_labeled_conditions(),
    }


def unlabeled_gke_pvc_raw_rewrite_updates(cloud_account_id, cluster):
    """Mongo 3.6-safe $set: unlabeled GKE PVCs → gke/<cluster>."""
    ident = gke_collapse_identity(cluster)
    if not ident:
        return []
    return [(
        unlabeled_gke_pvc_raw_filter(cloud_account_id),
        {'$set': {'resource_id': ident['cloud_resource_id']}},
    )]


def unlabeled_gke_pvc_resource_filter(cloud_account_id):
    """Live Volume docs created from unlabeled GKE PVCs."""
    return {
        'cloud_account_id': cloud_account_id,
        'deleted_at': 0,
        'resource_type': 'Volume',
        'cloud_resource_id': {'$regex': UNLABELED_GKE_PVC_ID_SKIP_REGEX},
        '$or': [
            {'name': {'$regex': '^%s' % PVC_NAME_PREFIX}},
            {PLAIN_GKE_VOLUME_FIELD: {'$exists': True}},
            {ENCODED_GKE_VOLUME_FIELD: {'$exists': True}},
        ],
    }


def labeled_collapse_raw_filter(
        cloud_account_id, tag_field, exclude_tag_fields=(),
        skip_id_prefix=None):
    """Raw rows that still carry a Composer/GKE label (plaintext tags)."""
    conditions = [
        {tag_field: {'$exists': True, '$nin': [None, '']}},
    ]
    for field in exclude_tag_fields or ():
        conditions.append({
            '$or': [
                {field: {'$exists': False}},
                {field: {'$in': [None, '']}},
            ]
        })
    # Never steal raw from another identity keeper (Cloud Run with inherited
    # GKE node-pool tags must stay cloudrun/<service>, not gke/<cluster>).
    conditions.append({
        'resource_id': {
            '$regex': COLLAPSED_IDENTITY_RAW_SKIP_REGEX,
        },
    })
    if skip_id_prefix:
        # Negative lookahead: Mongo 3.6 $not cannot wrap $regex.
        conditions.append({
            'resource_id': {
                '$regex': '^(?!%s/)' % re.escape(skip_id_prefix),
            },
        })
    if len(conditions) == 1:
        filt = {'cloud_account_id': cloud_account_id}
        filt.update(conditions[0])
        return filt
    return {
        'cloud_account_id': cloud_account_id,
        '$and': conditions,
    }


def labeled_collapse_raw_rewrite_updates(
        cloud_account_id, tag_field, values, ident_for_value,
        exclude_tag_fields=(), skip_id_prefix=None):
    """Mongo 3.6-safe $set objects, one update per stored label value."""
    base = labeled_collapse_raw_filter(
        cloud_account_id, tag_field, exclude_tag_fields, skip_id_prefix)
    updates = []
    seen = set()
    for raw in values or []:
        if raw in (None, ''):
            continue
        ident = ident_for_value(raw)
        if not ident:
            continue
        try:
            key = raw
            hash(key)
        except TypeError:
            key = str(raw)
        if key in seen:
            continue
        seen.add(key)
        filt = dict(base)
        filt[tag_field] = raw
        updates.append((
            filt,
            {'$set': {'resource_id': ident['cloud_resource_id']}},
        ))
    return updates


def is_gcp_billing_sku_id(cloud_resource_id):
    """True when cloud_resource_id is a billing sku.id, not a discovery id."""
    return bool(GCP_BILLING_SKU_ID_RE.match(str(cloud_resource_id or '')))


def is_labeled_collapse_leftover_id(cloud_resource_id):
    """Discovery instance/disk leftover rekey may fold onto a keeper."""
    rid = str(cloud_resource_id or '')
    return bool(rid) and rid.isdigit()


def collapse_leftover_may_fold(cloud_resource_id, ident, raw_row,
                               mongo_resource_type=None):
    """Whether a live Mongo leftover may fold onto ident.

    Numeric discovery ids always fold. Billing sku.id / bucket names fold
    when raw expenses already moved off that id, or raw still carries the
    same collapse tags, or the Mongo doc is already typed as the collapse
    family (Composer / Dataproc / GKE) even if leftover raw rows are
    unlabeled. Another keeper id (cloudrun/, composer/, …) never folds
    onto a different identity, even with inherited cluster tags. Inherited
    cluster tags on an Instance/Volume SKU whose raw has no identity must
    not fold (that zeroed ClickHouse in cf57077).
    """
    crid = str(cloud_resource_id or '')
    keeper = (ident or {}).get('cloud_resource_id')
    if not crid or not keeper:
        return False
    if crid == keeper:
        return True
    other = collapsed_identity_from_resource_id(crid)
    if other and other.get('cloud_resource_id') != keeper:
        return False
    if is_labeled_collapse_leftover_id(crid):
        return True
    if raw_row is None:
        return True
    actual = gcp_collapse_identity((raw_row or {}).get('tags') or {})
    if actual and actual.get('cloud_resource_id') == keeper:
        return True
    family = {
        'Composer', 'Cloud Composer', 'Dataproc', 'GKE',
        'Cloud SQL', 'Cloud Run', 'Cloud Run Functions',
    }
    return (mongo_resource_type or '') in family


def collapsed_labeled_member_match(
        cloud_account_id, encoded_field, plain_field):
    """Live Mongo docs tagged with a Composer/GKE label (encoded or plain)."""
    return {
        'cloud_account_id': cloud_account_id,
        'deleted_at': 0,
        '$or': [
            {encoded_field: {'$exists': True, '$nin': [None, '']}},
            {plain_field: {'$exists': True, '$nin': [None, '']}},
        ],
    }


COLLAPSED_IDENTITY_ID_REGEX = (
    r'^(dataproc|composer|gke|cloudsql|cloudrun|function)/'
)
# Mongo 3.6-safe negative lookahead: do not rewrite raw already keyed as a
# keeper (cloudrun/ onto gke/, composer/ onto dataproc/, …).
COLLAPSED_IDENTITY_RAW_SKIP_REGEX = (
    r'^(?!(dataproc|composer|gke|cloudsql|cloudrun|function)/)'
)


def collapsed_identity_duplicate_match(cloud_account_id):
    return {
        'cloud_account_id': cloud_account_id,
        'deleted_at': 0,
        'cloud_resource_id': {'$regex': COLLAPSED_IDENTITY_ID_REGEX},
    }


def deleted_collapsed_member_match(cloud_account_id):
    """Soft-deleted Dataproc / Composer / GKE / unlabeled PVC members.

    Keepers use dataproc|composer|gke/ ids. Collapsed members often keep the
    original cloud_resource_id (node, pvc numeric, dataproc uuid), so the
    same-id twin lookup cannot find them. Billing sku.id docs are skipped in
    Python (Mongo 3.6 cannot $not a regex).
    """
    tag_exists = {'$exists': True, '$nin': [None, '']}
    return {
        'cloud_account_id': cloud_account_id,
        'deleted_at': {'$gt': 0},
        '$or': [
            {'cloud_resource_id': {'$regex': COLLAPSED_IDENTITY_ID_REGEX}},
            {'cloud_resource_id': {'$regex': DATAPROC_UUID_RESOURCE_ID_REGEX}},
            {PLAIN_DATAPROC_CLUSTER_UUID_FIELD: tag_exists},
            {ENCODED_DATAPROC_CLUSTER_UUID_FIELD: tag_exists},
            {PLAIN_COMPOSER_UUID_FIELD: tag_exists},
            {ENCODED_COMPOSER_UUID_FIELD: tag_exists},
            {PLAIN_GKE_NAME_FIELD: tag_exists},
            {ENCODED_GKE_NAME_FIELD: tag_exists},
            {'resource_type': 'Volume', 'name': {'$regex': '^%s' % PVC_NAME_PREFIX}},
            {PLAIN_GKE_VOLUME_FIELD: {'$exists': True}},
            {ENCODED_GKE_VOLUME_FIELD: {'$exists': True}},
        ],
    }


def deleted_miscollapsed_billing_sku_match(cloud_account_id):
    """SKU resources rekey treated as cluster members because of inherited tags."""
    tag_exists = {'$exists': True, '$nin': [None, '']}
    return {
        'cloud_account_id': cloud_account_id,
        'deleted_at': {'$gt': 0},
        'cloud_resource_id': {'$regex': GCP_BILLING_SKU_ID_REGEX},
        '$or': [
            {PLAIN_DATAPROC_CLUSTER_UUID_FIELD: tag_exists},
            {ENCODED_DATAPROC_CLUSTER_UUID_FIELD: tag_exists},
            {PLAIN_COMPOSER_UUID_FIELD: tag_exists},
            {ENCODED_COMPOSER_UUID_FIELD: tag_exists},
            {PLAIN_GKE_NAME_FIELD: tag_exists},
            {ENCODED_GKE_NAME_FIELD: tag_exists},
        ],
    }


def pick_collapsed_duplicate_keeper(docs):
    """Prefer the canonical collapse doc (no resource hash), then newest."""
    if not docs:
        return None

    def sort_key(doc):
        hashed = 0 if not doc.get('cloud_resource_hash') else 1
        last_seen = doc.get('last_seen') or 0
        return (hashed, -int(last_seen), str(doc.get('_id') or ''))

    return min(docs, key=sort_key)


def is_stale_serverless_dataproc_resource(cloud_resource_id, name):
    """True for leftover dataproc/<uuid> docs named srvls-batch-*."""
    if not cloud_resource_id or not name:
        return False
    if not str(name).startswith(SRVLS_BATCH_PREFIX):
        return False
    return bool(DATAPROC_UUID_RESOURCE_ID_RE.match(str(cloud_resource_id)))


def stale_serverless_dataproc_resource_filter(cloud_account_id):
    return {
        'cloud_account_id': cloud_account_id,
        'deleted_at': 0,
        'name': {'$regex': '^%s' % SRVLS_BATCH_PREFIX},
        'cloud_resource_id': {'$regex': DATAPROC_UUID_RESOURCE_ID_REGEX},
    }


def _apply_tag_overrides(resource, overrides):
    if not overrides:
        return resource
    tags = dict(resource.get('tags') or {})
    tags.update(overrides)
    resource['tags'] = tags
    return resource


def _merge_collapsed_resource(base, extra):
    merged = dict(base)
    for field, picker in (('first_seen', min), ('last_seen', max)):
        left, right = base.get(field), extra.get(field)
        if left is None:
            merged[field] = right
        elif right is None:
            merged[field] = left
        else:
            merged[field] = picker(left, right)
    merged['active'] = bool(base.get('active') or extra.get('active'))
    tags = dict(base.get('tags') or {})
    tags.update(extra.get('tags') or {})
    merged['tags'] = tags
    return merged


def collapse_gcp_resources(resources):
    """Rewrite and dedupe a save_bulk payload by collapse identity."""
    if not resources:
        return resources
    result = []
    index_by_id = {}
    for resource in resources:
        identity = gcp_collapse_identity(resource.get('tags') or {})
        if not identity:
            result.append(resource)
            continue
        rewritten = dict(resource)
        rewritten['cloud_resource_id'] = identity['cloud_resource_id']
        rewritten['resource_type'] = identity['resource_type']
        rewritten['name'] = identity['name']
        rewritten.pop('cloud_resource_hash', None)
        _apply_tag_overrides(rewritten, identity.get('tag_overrides'))
        cid = identity['cloud_resource_id']
        prev = index_by_id.get(cid)
        if prev is None:
            index_by_id[cid] = len(result)
            result.append(rewritten)
            continue
        merged = _merge_collapsed_resource(result[prev], rewritten)
        result[prev] = _apply_tag_overrides(
            merged, identity.get('tag_overrides'))
    return result


def collapse_expense_chunk(chunk):
    """Re-key raw expense groups by Composer/GKE/Dataproc/SQL/Run identity."""
    if not chunk:
        return chunk
    collapsed = {}
    for r_id, expenses in chunk.items():
        tags = {}
        ident = None
        for expense in expenses:
            tags.update(expense.get('tags') or {})
        ident = gcp_collapse_identity(tags)
        if not ident:
            for expense in expenses:
                ident = gcp_detailed_collapse_identity(expense)
                if ident:
                    break
        # Rows already keyed as a keeper stay on that id. Inherited GKE
        # labels on Cloud Run must not remap cloudrun/<service> to gke/.
        if collapsed_identity_from_resource_id(r_id):
            key = r_id
        else:
            key = ident['cloud_resource_id'] if ident else r_id
        collapsed.setdefault(key, []).extend(expenses)
    return collapsed
