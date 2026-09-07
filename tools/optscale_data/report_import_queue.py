"""Report-import RabbitMQ queue naming and worker allocation.

Imports are published to a per-cloud-type queue so one provider (e.g. GCP)
cannot starve another (e.g. Snowflake) in a shared FIFO. Priority 1..10 still
applies inside each typed queue.

Legacy queue name ``report-imports`` is kept for in-flight messages published
before the split; consumers should subscribe to it until it is drained.

Worker slots are shared across typed queues and reassigned when a worker
becomes free. Targets use ceil(depth_i / sum(depth) * n_workers); when the
sum of ceils exceeds the pool, slots are taken from vendors that currently
hold the most workers (preferring those with more than one).
"""

import math
import time

LEGACY_REPORT_IMPORT_QUEUE = 'report-imports'
REPORT_IMPORT_QUEUE_PREFIX = 'report-imports'

# Keep in sync with rest_api.rest_api_server.models.enums.CloudTypes values.
REPORT_IMPORT_CLOUD_TYPES = (
    'aws_cnr',
    'alibaba_cnr',
    'azure_cnr',
    'azure_tenant',
    'kubernetes_cnr',
    'environment',
    'gcp_cnr',
    'gcp_tenant',
    'nebius',
    'databricks',
    'snowflake',
    'snowflake_tenant',
)


def report_import_queue_for_type(cloud_type):
    """Return the RabbitMQ queue/routing key for a cloud account type."""
    if cloud_type is None:
        return LEGACY_REPORT_IMPORT_QUEUE
    if hasattr(cloud_type, 'value'):
        cloud_type = cloud_type.value
    cloud_type = str(cloud_type).strip().lower()
    if not cloud_type:
        return LEGACY_REPORT_IMPORT_QUEUE
    return '{prefix}.{cloud_type}'.format(
        prefix=REPORT_IMPORT_QUEUE_PREFIX, cloud_type=cloud_type)


def report_import_queue_names(include_legacy=True):
    """All queue names a consumer should listen on."""
    names = [
        report_import_queue_for_type(cloud_type)
        for cloud_type in REPORT_IMPORT_CLOUD_TYPES
    ]
    if include_legacy:
        names = [LEGACY_REPORT_IMPORT_QUEUE] + names
    return names


def cloud_type_from_queue_name(queue_name):
    """Inverse of report_import_queue_for_type for typed queues."""
    if not queue_name or queue_name == LEGACY_REPORT_IMPORT_QUEUE:
        return None
    prefix = REPORT_IMPORT_QUEUE_PREFIX + '.'
    if queue_name.startswith(prefix):
        return queue_name[len(prefix):]
    return None


def allocate_workers(depths, n_workers):
    """Allocate ``n_workers`` across non-empty vendor queues by depth.

    ``depths`` maps cloud_type -> unfinished import count (SCHEDULED +
    IN_PROGRESS). Empty queues are omitted (0 workers). Each non-empty
    queue gets ceil(depth / total * n_workers). If the sum exceeds
    ``n_workers``, take one slot at a time from the vendor that currently
    has the most workers (preferring counts > 1) until the pool fits.

    Remaining slots (if any after ceil) go to the deepest queues.
    """
    n_workers = int(n_workers)
    if n_workers <= 0:
        return {}
    depths = {
        str(cloud_type).strip().lower(): int(depth)
        for cloud_type, depth in (depths or {}).items()
        if depth and int(depth) > 0 and str(cloud_type).strip()
    }
    if not depths:
        return {}

    total = sum(depths.values())
    targets = {
        cloud_type: int(math.ceil(depth / total * n_workers))
        for cloud_type, depth in depths.items()
    }

    while sum(targets.values()) > n_workers:
        candidates = [
            cloud_type for cloud_type, slots in targets.items()
            if slots > 1
        ]
        if not candidates:
            # With a small number of vendor queues this should not happen
            # for n_workers >= number of non-empty queues.
            candidates = list(targets.keys())
        victim = max(candidates, key=lambda cloud_type: (
            targets[cloud_type], depths[cloud_type], cloud_type))
        targets[victim] -= 1
        if targets[victim] <= 0:
            del targets[victim]

    while sum(targets.values()) < n_workers:
        beneficiary = max(
            depths.keys(),
            key=lambda cloud_type: (
                depths[cloud_type],
                targets.get(cloud_type, 0),
                cloud_type,
            ),
        )
        targets[beneficiary] = targets.get(beneficiary, 0) + 1

    return targets


# Skip lost/orphan cleanup while rest_api is still inserting+publishing.
# 2026-08-14: heartbeat drained ready messages mid-bulk GCP reload and
# acked four live SCHEDULED ids that were not in the pre-enqueue REST list.
ENQUEUE_CLEANUP_GRACE_SECS = 60


def report_import_enqueue_in_progress(unfinished, now_ts=None, grace_secs=None):
    """True when a bulk/manual schedule is still writing SCHEDULED rows.

    rest_api ``create()`` commits Maria then publishes Rabbit per account.
    Heartbeat cleanup must not fail Maria rows or ack Rabbit messages in
    that window: the REST unfinished list lags the queue by seconds.
    """
    if now_ts is None:
        now_ts = time.time()
    if grace_secs is None:
        grace_secs = ENQUEUE_CLEANUP_GRACE_SECS
    cutoff = float(now_ts) - float(grace_secs)
    for item in unfinished or ():
        state = str((item or {}).get('state') or '').strip().lower()
        if state != 'scheduled':
            continue
        created_at = (item or {}).get('created_at')
        if created_at is None:
            continue
        try:
            created_ts = float(created_at)
        except (TypeError, ValueError):
            continue
        if created_ts >= cutoff:
            return True
    return False


def valid_rabbit_import_ids(unfinished, owned_ids=None, in_flight_ids=None,
                            exclude_ids=None):
    """Import ids whose *ready* Rabbit messages must be kept.

    Keep SCHEDULED unfinished rows. Live IN_PROGRESS / in-flight deliveries
    are unacked on the worker channel, not ready — a ready copy of an owned
    import is a duplicate and must be acked. ``owned_ids`` / ``in_flight_ids``
    are ignored (call-site compat). ``exclude_ids`` drops rows just marked
    lost/failed in the same cleanup pass.
    """
    _ = owned_ids, in_flight_ids
    keep = set()
    exclude = set(exclude_ids or ())
    for item in unfinished or ():
        import_id = (item or {}).get('id')
        if not import_id or import_id in exclude:
            continue
        state = str((item or {}).get('state') or '').strip().lower()
        if state == 'scheduled':
            keep.add(import_id)
    return keep


def should_drain_orphan_ready(ready, scheduled_count):
    """True when ready messages outnumber live SCHEDULED rows.

    Orphan drain must not basic_get the waiting queue every minute: that
    requeues every SCHEDULED message and raced with the 3h AMQP TTL.
    Drain only when there are extra ready copies (duplicates of an already
    IN_PROGRESS job, or leftovers after Maria rows were failed).
    """
    return int(ready or 0) > int(scheduled_count or 0)


def classify_lost_imports(unfinished, owned_ids, in_flight_ids,
                          rabbit_ready_by_type, active_by_type,
                          rabbit_message_ids_by_type=None,
                          rabbit_unacked_by_type=None):
    """Return ``[(import_id, reason), ...]`` for lost (not merely stale) rows.

    Lost means the DB row no longer has a live owner / queue message:

    - ``IN_PROGRESS`` not in ``owned_ids`` / ``in_flight_ids`` (diworker
      restarted or worker died; in_flight covers claim races)
    - ``SCHEDULED`` not in ``owned_ids`` / ``in_flight_ids`` when that
      vendor's ready count is 0. Sibling IN_PROGRESS unacked/active slots
      must not hide this: 2026-08-15 GCP messages hit the 3h AMQP TTL
      while 6 workers were still busy, and the idle-queue heuristic left
      42 SCHEDULED rows with no Rabbit message. Peek-based "id missing
      while ready > 0" is still not used — ready peeks cannot see unacked
      in-flight SCHEDULED deliveries.

    Time-based stale kills (3h / 30m) stay in rest_api ``fail_stale_imports``.
    """
    owned_ids = set(owned_ids or ())
    in_flight_ids = set(in_flight_ids or ())
    rabbit_ready_by_type = {
        str(k).lower(): int(v)
        for k, v in (rabbit_ready_by_type or {}).items()
    }
    active_by_type = {
        str(k).lower(): int(v)
        for k, v in (active_by_type or {}).items()
    }
    # Call-site compat: unacked/peek/active_by_type are not used to decide
    # SCHEDULED loss (in_flight/owned already protect live deliveries).
    _ = rabbit_message_ids_by_type, rabbit_unacked_by_type, active_by_type
    lost = []
    for item in unfinished or ():
        import_id = (item or {}).get('id')
        if not import_id:
            continue
        state = str((item or {}).get('state') or '').strip().lower()
        cloud_type = str((item or {}).get('cloud_type') or '').strip().lower()
        if state in ('in_progress',):
            # in_flight covers the brief window after basic_get before the
            # executor claims owned_ids; also protects against a duplicate
            # delivery that has not yet released its in_flight claim.
            if import_id in owned_ids or import_id in in_flight_ids:
                continue
            lost.append((
                import_id,
                'Lost IN_PROGRESS: no live diworker owns this import '
                '(worker crashed or diworker was restarted).',
            ))
            continue
        if state != 'scheduled':
            continue
        if import_id in owned_ids or import_id in in_flight_ids:
            continue
        if not cloud_type:
            continue
        if rabbit_ready_by_type.get(cloud_type, 0) > 0:
            continue
        lost.append((
            import_id,
            'Lost SCHEDULED: no ready Rabbit message for this import '
            '(queue message acked/expired/lost).',
        ))
    return lost
