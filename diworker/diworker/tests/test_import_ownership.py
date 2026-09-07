#!/usr/bin/env python
"""Ownership refcount for live report imports (duplicate Rabbit deliveries).

Production failure (2026-08-12, pf-da-shared-prod / pf-sns-prod):
  1. Worker A claims report_import_id X, sets IN_PROGRESS, streams BQ.
  2. Duplicate Rabbit delivery for X starts worker B.
  3. B sees state=in_progress, Skip, exits 0.
  4. B's finally discarded X from a *set* of owners — removing A's ownership.
  5. Heartbeat classify_lost_imports → Lost IN_PROGRESS → fail + schedule_import.
  6. New import wiped the usage day and restarted (BQ quota burn / empty Aug).

Fix: Counter refcounts for active_report_import_ids / in_flight_import_ids;
IN_PROGRESS is also kept if still in_flight.
"""
import unittest
from collections import Counter
from threading import Lock
from unittest.mock import Mock, patch

from optscale_data.report_import_queue import classify_lost_imports


def _lost_in_progress(owned_ids, in_flight_ids=None, import_id='live-import'):
    return classify_lost_imports(
        unfinished=[{
            'id': import_id,
            'state': 'in_progress',
            'cloud_type': 'gcp_cnr',
            'cloud_account_id': 'ca-1',
        }],
        owned_ids=owned_ids,
        in_flight_ids=in_flight_ids or set(),
        rabbit_ready_by_type={},
        active_by_type={'gcp_cnr': 1},
    )


class TestImportOwnershipRefcount(unittest.TestCase):
    def _worker(self):
        from diworker.diworker.main import DIWorker

        worker = DIWorker.__new__(DIWorker)
        worker.active_reports_lock = Lock()
        worker.active_report_import_ids = Counter()
        worker.in_flight_import_ids = Counter()
        return worker

    def test_set_discard_false_lost_documents_old_bug(self):
        """With a set, one duplicate Skip discard clears the live owner."""
        import_id = 'same-import'
        owned = set()
        owned.add(import_id)  # holder A
        owned.add(import_id)  # holder B (no-op on set)
        owned.discard(import_id)  # B finishes Skip — wrongly drops A
        self.assertEqual(owned, set())
        lost = _lost_in_progress(owned, import_id=import_id)
        self.assertEqual([item[0] for item in lost], [import_id])
        self.assertIn('Lost IN_PROGRESS', lost[0][1])

    def test_duplicate_skip_release_does_not_false_lost(self):
        """Regression: duplicate Skip must not make heartbeat requeue live work."""
        worker = self._worker()
        import_id = 'same-import'
        body = {'report_import_id': import_id}

        # A: claimed by parent _process_task (+ still in_flight until ack).
        worker._track_in_flight(body)
        worker._claim_import_id(worker.active_report_import_ids, import_id)

        # B: duplicate delivery.
        worker._track_in_flight(body)
        worker._claim_import_id(worker.active_report_import_ids, import_id)
        self.assertEqual(worker.active_report_import_ids[import_id], 2)
        self.assertEqual(worker.in_flight_import_ids[import_id], 2)

        # B Skip: already in_progress → release ownership + untrack (like finally).
        worker._release_import_id(worker.active_report_import_ids, import_id)
        worker._untrack_in_flight(body)

        owned = set(worker.active_report_import_ids)
        in_flight = set(worker.in_flight_import_ids)
        self.assertEqual(owned, {import_id})
        self.assertEqual(in_flight, {import_id})

        lost = _lost_in_progress(owned, in_flight, import_id=import_id)
        self.assertEqual(
            lost, [],
            'duplicate Skip must not classify live IN_PROGRESS as lost '
            '(would fail+requeue and wipe the usage day)')

        # A finishes for real.
        worker._release_import_id(worker.active_report_import_ids, import_id)
        worker._untrack_in_flight(body)
        self.assertNotIn(import_id, worker.active_report_import_ids)
        self.assertNotIn(import_id, worker.in_flight_import_ids)
        lost_after = _lost_in_progress(
            set(worker.active_report_import_ids),
            set(worker.in_flight_import_ids),
            import_id=import_id)
        self.assertEqual([item[0] for item in lost_after], [import_id])

    def test_duplicate_release_keeps_live_owner(self):
        """Second delivery must not drop ownership of the first holder."""
        worker = self._worker()
        import_id = 'same-import'

        worker._claim_import_id(worker.active_report_import_ids, import_id)
        worker._claim_import_id(worker.active_report_import_ids, import_id)
        self.assertEqual(worker.active_report_import_ids[import_id], 2)

        # Duplicate worker finishes (e.g. Skip: already in_progress).
        worker._release_import_id(worker.active_report_import_ids, import_id)
        self.assertIn(import_id, worker.active_report_import_ids)
        self.assertEqual(worker.active_report_import_ids[import_id], 1)

        owned = set(worker.active_report_import_ids)
        self.assertEqual(owned, {import_id})

        worker._release_import_id(worker.active_report_import_ids, import_id)
        self.assertNotIn(import_id, worker.active_report_import_ids)

    def test_in_flight_refcount_survives_duplicate_untrack(self):
        worker = self._worker()
        body = {'report_import_id': 'inflight-1'}
        worker._track_in_flight(body)
        worker._track_in_flight(body)
        worker._untrack_in_flight(body)
        self.assertIn('inflight-1', worker.in_flight_import_ids)
        worker._untrack_in_flight(body)
        self.assertNotIn('inflight-1', worker.in_flight_import_ids)

    def test_release_missing_id_is_noop(self):
        worker = self._worker()
        worker._release_import_id(worker.active_report_import_ids, 'missing')
        self.assertEqual(worker.active_report_import_ids, Counter())

    def test_cleanup_would_not_requeue_while_refcount_held(self):
        """Mirror _cleanup_lost_imports_locked requeue predicate."""
        worker = self._worker()
        import_id = '1a496497-0e08-49c5-a9c1-104202f69467'
        ca_id = 'f0f44b0c-7379-48e1-af6c-9f61446206d1'
        body = {'report_import_id': import_id}

        worker._track_in_flight(body)
        worker._claim_import_id(worker.active_report_import_ids, import_id)
        # Duplicate Skip path.
        worker._track_in_flight(body)
        worker._claim_import_id(worker.active_report_import_ids, import_id)
        worker._release_import_id(worker.active_report_import_ids, import_id)
        worker._untrack_in_flight(body)

        unfinished = [{
            'id': import_id,
            'state': 'in_progress',
            'cloud_type': 'gcp_cnr',
            'cloud_account_id': ca_id,
        }]
        owned_ids = set(worker.active_report_import_ids)
        in_flight_ids = set(worker.in_flight_import_ids)
        lost = classify_lost_imports(
            unfinished, owned_ids, in_flight_ids, {}, {'gcp_cnr': 1})
        requeue_account_ids = set()
        for lost_id, reason in lost:
            if 'Lost IN_PROGRESS' in reason:
                item = next(i for i in unfinished if i['id'] == lost_id)
                requeue_account_ids.add(item['cloud_account_id'])
        self.assertEqual(requeue_account_ids, set())

    def _cleanup_worker(self):
        worker = self._worker()
        worker.cleanup_lock = Lock()
        worker.active_by_type = {}
        worker._rabbit_ready_by_type = Mock(return_value={})
        worker._rabbit_unacked_by_type = Mock(return_value={})
        worker._cleanup_orphan_rabbit_messages = Mock(return_value=0)
        return worker

    def test_cleanup_skips_during_recent_enqueue(self):
        worker = self._cleanup_worker()
        rest = Mock()
        rest.report_import_queue_stats.return_value = (200, {
            'depths': {'gcp_cnr': 1},
            'unfinished': [{
                'id': 'new-sched',
                'state': 'scheduled',
                'cloud_type': 'gcp_cnr',
                'cloud_account_id': 'ca-1',
                'created_at': 1_000_000,
            }],
        })
        with patch(
                'diworker.diworker.main.time.time', return_value=1_000_005):
            failed = worker._cleanup_lost_imports(rest)
        self.assertEqual(failed, 0)
        rest.report_import_update.assert_not_called()
        worker._cleanup_orphan_rabbit_messages.assert_not_called()
        worker._rabbit_ready_by_type.assert_not_called()

    def test_cleanup_skips_if_enqueue_starts_during_rabbit_io(self):
        worker = self._cleanup_worker()
        rest = Mock()
        old = {
            'id': 'old-sched',
            'state': 'scheduled',
            'cloud_type': 'gcp_cnr',
            'cloud_account_id': 'ca-1',
            'created_at': 1_000_000 - 120,
        }
        fresh = {
            'id': 'new-sched',
            'state': 'scheduled',
            'cloud_type': 'gcp_cnr',
            'cloud_account_id': 'ca-2',
            'created_at': 1_000_000,
        }
        rest.report_import_queue_stats.side_effect = [
            (200, {'depths': {'gcp_cnr': 1}, 'unfinished': [old]}),
            (200, {'depths': {'gcp_cnr': 2}, 'unfinished': [old, fresh]}),
        ]
        with patch(
                'diworker.diworker.main.time.time', return_value=1_000_005):
            failed = worker._cleanup_lost_imports(rest)
        self.assertEqual(failed, 0)
        rest.report_import_update.assert_not_called()
        worker._cleanup_orphan_rabbit_messages.assert_not_called()
        worker._rabbit_ready_by_type.assert_called_once()

    def test_cleanup_republishes_lost_scheduled_while_siblings_run(self):
        """AMQP TTL can drop waiting SCHEDULED while other GCP jobs run."""
        worker = self._cleanup_worker()
        worker._republish_scheduled_import = Mock()
        rest = Mock()
        rest.report_import_queue_stats.return_value = (200, {
            'depths': {'gcp_cnr': 2},
            'unfinished': [
                {
                    'id': 'live',
                    'state': 'in_progress',
                    'cloud_type': 'gcp_cnr',
                    'cloud_account_id': 'ca-live',
                    'created_at': 1_000_000 - 120,
                },
                {
                    'id': 'stuck',
                    'state': 'scheduled',
                    'cloud_type': 'gcp_cnr',
                    'cloud_account_id': 'ca-stuck',
                    'created_at': 1_000_000 - 120,
                },
            ],
        })
        worker.active_report_import_ids['live'] = 1
        worker.active_by_type['gcp_cnr'] = 1
        worker._rabbit_ready_by_type = Mock(return_value={'gcp_cnr': 0})
        worker._rabbit_unacked_by_type = Mock(return_value={'gcp_cnr': 1})
        with patch(
                'diworker.diworker.main.time.time', return_value=1_000_000):
            failed = worker._cleanup_lost_imports(rest)
        self.assertEqual(failed, 0)
        rest.report_import_update.assert_not_called()
        rest.schedule_import.assert_not_called()
        worker._republish_scheduled_import.assert_called_once_with(
            'stuck', 'gcp_cnr')
        worker._cleanup_orphan_rabbit_messages.assert_called_once()
        _, kwargs = worker._cleanup_orphan_rabbit_messages.call_args
        self.assertEqual(kwargs.get('scheduled_by_type'), {'gcp_cnr': 1})


if __name__ == '__main__':
    unittest.main()
