from unittest import TestCase

from optscale_data.report_import_queue import (
    LEGACY_REPORT_IMPORT_QUEUE,
    allocate_workers,
    cloud_type_from_queue_name,
    report_import_enqueue_in_progress,
    report_import_queue_for_type,
    report_import_queue_names,
    should_drain_orphan_ready,
    valid_rabbit_import_ids,
)
from rest_api.rest_api_server.models.enums import CloudTypes


class TestReportImportQueue(TestCase):
    def test_queue_for_cloud_type(self):
        self.assertEqual(
            report_import_queue_for_type(CloudTypes.SNOWFLAKE),
            'report-imports.snowflake')
        self.assertEqual(
            report_import_queue_for_type('GCP_CNR'),
            'report-imports.gcp_cnr')
        self.assertEqual(
            report_import_queue_for_type(None),
            LEGACY_REPORT_IMPORT_QUEUE)

    def test_consumer_queue_names(self):
        names = report_import_queue_names(include_legacy=True)
        self.assertEqual(names[0], LEGACY_REPORT_IMPORT_QUEUE)
        self.assertIn('report-imports.snowflake', names)
        self.assertIn('report-imports.snowflake_tenant', names)
        self.assertIn('report-imports.gcp_cnr', names)
        self.assertEqual(len(names), len(set(names)))

    def test_queue_for_snowflake_tenant(self):
        self.assertEqual(
            report_import_queue_for_type(CloudTypes.SNOWFLAKE_TENANT),
            'report-imports.snowflake_tenant')

    def test_cloud_type_from_queue_name(self):
        self.assertEqual(
            cloud_type_from_queue_name('report-imports.gcp_cnr'), 'gcp_cnr')
        self.assertIsNone(cloud_type_from_queue_name(LEGACY_REPORT_IMPORT_QUEUE))

    def test_allocate_only_gcp(self):
        self.assertEqual(
            allocate_workers({'gcp_cnr': 90}, 10),
            {'gcp_cnr': 10})

    def test_allocate_gcp_plus_snowflake(self):
        # ceil(90/91*10)=10, ceil(1/91*10)=1 → overflow, take from largest
        self.assertEqual(
            allocate_workers({'gcp_cnr': 90, 'snowflake': 1}, 10),
            {'gcp_cnr': 9, 'snowflake': 1})

    def test_allocate_empty(self):
        self.assertEqual(allocate_workers({}, 10), {})
        self.assertEqual(allocate_workers({'gcp_cnr': 0}, 10), {})

    def test_classify_lost_in_progress_not_owned(self):
        from optscale_data.report_import_queue import classify_lost_imports
        lost = classify_lost_imports(
            unfinished=[
                {'id': 'a', 'state': 'in_progress', 'cloud_type': 'gcp_cnr'},
                {'id': 'b', 'state': 'in_progress', 'cloud_type': 'snowflake'},
            ],
            owned_ids={'b'},
            in_flight_ids=set(),
            rabbit_ready_by_type={},
            active_by_type={'gcp_cnr': 1},
        )
        self.assertEqual([item[0] for item in lost], ['a'])
        self.assertIn('Lost IN_PROGRESS', lost[0][1])

    def test_classify_lost_in_progress_skips_in_flight(self):
        # Duplicate Rabbit delivery must not false-fail a live IN_PROGRESS
        # while the original holder is still starting / running.
        from optscale_data.report_import_queue import classify_lost_imports
        lost = classify_lost_imports(
            unfinished=[
                {'id': 'live', 'state': 'in_progress', 'cloud_type': 'gcp_cnr'},
            ],
            owned_ids=set(),
            in_flight_ids={'live'},
            rabbit_ready_by_type={},
            active_by_type={},
        )
        self.assertEqual(lost, [])

    def test_classify_lost_in_progress_skips_owned(self):
        from optscale_data.report_import_queue import classify_lost_imports
        lost = classify_lost_imports(
            unfinished=[
                {'id': 'live', 'state': 'in_progress', 'cloud_type': 'gcp_cnr'},
            ],
            owned_ids={'live'},
            in_flight_ids=set(),
            rabbit_ready_by_type={},
            active_by_type={},
        )
        self.assertEqual(lost, [])

    def test_classify_lost_after_duplicate_set_discard_is_lost(self):
        """Documents pre-fix failure: set.discard after duplicate Skip."""
        from optscale_data.report_import_queue import classify_lost_imports
        owned = {'live'}
        owned.discard('live')  # duplicate holder released a set — bug
        lost = classify_lost_imports(
            unfinished=[
                {'id': 'live', 'state': 'in_progress', 'cloud_type': 'gcp_cnr'},
            ],
            owned_ids=owned,
            in_flight_ids=set(),
            rabbit_ready_by_type={},
            active_by_type={'gcp_cnr': 1},
        )
        self.assertEqual([item[0] for item in lost], ['live'])
        self.assertIn('Lost IN_PROGRESS', lost[0][1])

    def test_classify_lost_after_duplicate_refcount_release_not_lost(self):
        """Post-fix: one of two refcount holders released — still owned."""
        from optscale_data.report_import_queue import classify_lost_imports
        # Counter keys with count>0 behave as owned set for classify.
        owned = {'live'}  # remaining holder after duplicate Skip
        lost = classify_lost_imports(
            unfinished=[
                {'id': 'live', 'state': 'in_progress', 'cloud_type': 'gcp_cnr'},
            ],
            owned_ids=owned,
            in_flight_ids=set(),
            rabbit_ready_by_type={},
            active_by_type={'gcp_cnr': 1},
        )
        self.assertEqual(lost, [])

    def test_classify_lost_scheduled_empty_queue(self):
        from optscale_data.report_import_queue import classify_lost_imports
        lost = classify_lost_imports(
            unfinished=[
                {'id': 's1', 'state': 'scheduled', 'cloud_type': 'snowflake'},
                {'id': 's2', 'state': 'scheduled', 'cloud_type': 'gcp_cnr'},
                {'id': 's3', 'state': 'scheduled', 'cloud_type': 'aws_cnr'},
            ],
            owned_ids=set(),
            in_flight_ids={'s3'},
            rabbit_ready_by_type={'gcp_cnr': 5, 'snowflake': 0, 'aws_cnr': 0},
            active_by_type={},
        )
        self.assertEqual([item[0] for item in lost], ['s1'])
        self.assertIn('Lost SCHEDULED', lost[0][1])

    def test_classify_lost_scheduled_ready_empty_despite_unacked(self):
        # 2026-08-15: 42 GCP SCHEDULED hit AMQP TTL while 6 siblings were
        # still unacked IN_PROGRESS. Idle-queue (unacked>0) hid them.
        from optscale_data.report_import_queue import classify_lost_imports
        lost = classify_lost_imports(
            unfinished=[
                {'id': 's1', 'state': 'scheduled', 'cloud_type': 'gcp_cnr'},
            ],
            owned_ids=set(),
            in_flight_ids=set(),
            rabbit_ready_by_type={'gcp_cnr': 0},
            active_by_type={'gcp_cnr': 6},
            rabbit_unacked_by_type={'gcp_cnr': 6},
        )
        self.assertEqual([item[0] for item in lost], ['s1'])
        self.assertIn('Lost SCHEDULED', lost[0][1])

    def test_classify_lost_scheduled_ignores_stale_peek(self):
        # Peek "missing id" must not fail SCHEDULED while ready > 0 — that
        # raced with unacked deliveries held by workers.
        from optscale_data.report_import_queue import classify_lost_imports
        lost = classify_lost_imports(
            unfinished=[
                {'id': 'keep', 'state': 'scheduled', 'cloud_type': 'gcp_cnr'},
                {'id': 'held', 'state': 'scheduled', 'cloud_type': 'gcp_cnr'},
            ],
            owned_ids=set(),
            in_flight_ids=set(),
            rabbit_ready_by_type={'gcp_cnr': 1},
            active_by_type={},
            rabbit_message_ids_by_type={'gcp_cnr': {'keep'}},
        )
        self.assertEqual(lost, [])

    def test_classify_lost_scheduled_skips_in_flight_when_ready_empty(self):
        from optscale_data.report_import_queue import classify_lost_imports
        lost = classify_lost_imports(
            unfinished=[
                {'id': 'held', 'state': 'scheduled', 'cloud_type': 'gcp_cnr'},
                {'id': 'stuck', 'state': 'scheduled', 'cloud_type': 'gcp_cnr'},
            ],
            owned_ids=set(),
            in_flight_ids={'held'},
            rabbit_ready_by_type={'gcp_cnr': 0},
            active_by_type={'gcp_cnr': 1},
        )
        self.assertEqual([item[0] for item in lost], ['stuck'])

    def test_should_drain_orphan_ready(self):
        self.assertFalse(should_drain_orphan_ready(42, 42))
        self.assertFalse(should_drain_orphan_ready(0, 42))
        self.assertTrue(should_drain_orphan_ready(1, 0))
        self.assertTrue(should_drain_orphan_ready(43, 42))

    def test_valid_rabbit_import_ids_keeps_scheduled_not_owned(self):
        keep = valid_rabbit_import_ids(
            unfinished=[
                {'id': 'sched', 'state': 'scheduled', 'cloud_type': 'gcp_cnr'},
                {'id': 'prog', 'state': 'in_progress', 'cloud_type': 'gcp_cnr'},
                {'id': 'lost', 'state': 'scheduled', 'cloud_type': 'gcp_cnr'},
            ],
            owned_ids={'prog'},
            in_flight_ids={'inflight'},
            exclude_ids={'lost'},
        )
        # Ready copy of IN_PROGRESS is a duplicate; do not keep it.
        self.assertEqual(keep, {'sched'})

    def test_valid_rabbit_import_ids_empty_unfinished(self):
        self.assertEqual(
            valid_rabbit_import_ids([], owned_ids={'a'}, in_flight_ids=set()),
            set())

    def test_enqueue_in_progress_recent_scheduled(self):
        now = 1_000_000
        self.assertTrue(report_import_enqueue_in_progress(
            [{'id': 'new', 'state': 'scheduled', 'created_at': now - 5}],
            now_ts=now, grace_secs=60))
        self.assertFalse(report_import_enqueue_in_progress(
            [{'id': 'old', 'state': 'scheduled', 'created_at': now - 120}],
            now_ts=now, grace_secs=60))

    def test_enqueue_in_progress_ignores_in_progress_and_missing_created(self):
        now = 1_000_000
        self.assertFalse(report_import_enqueue_in_progress(
            [{'id': 'run', 'state': 'in_progress', 'created_at': now}],
            now_ts=now, grace_secs=60))
        self.assertFalse(report_import_enqueue_in_progress(
            [{'id': 'sched', 'state': 'scheduled'}],
            now_ts=now, grace_secs=60))
        self.assertFalse(report_import_enqueue_in_progress([], now_ts=now))

    def test_allocate_three_vendors_rebalance(self):
        # A third unfinished queue must steal one slot from a vendor that
        # holds more than one worker.
        result = allocate_workers(
            {'gcp_cnr': 50, 'aws_cnr': 50, 'snowflake': 1}, 10)
        self.assertEqual(sum(result.values()), 10)
        self.assertEqual(result.get('snowflake'), 1)
        self.assertEqual(result.get('gcp_cnr') + result.get('aws_cnr'), 9)
        self.assertTrue(result['gcp_cnr'] >= 1)
        self.assertTrue(result['aws_cnr'] >= 1)
