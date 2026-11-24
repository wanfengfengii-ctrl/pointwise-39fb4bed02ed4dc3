from capture.readout import Readout
from capture.retries import RetryDisposition
from handover.history import read_artifact
from handover.register import close_shift
from tests.fixtures import PauseWrite, StationTest, allow_receipt_commit, reject_receipt_commit, signed_counts


class TestReceive(StationTest):
    def test_stable_frame(self):
        exposure = self.record()
        signed = self.assert_signed(self.collect(), exposure, (7, 11), 18)
        self.assertEqual(signed.measurement.rule_id, self.lot.rule_id)
        self.assertEqual(read_artifact(self.db, signed.receipt.artifact_id), signed)
        self.assertEqual(signed_counts(self.db), (1, 2, 1))

    def test_exact_trigger(self):
        expected = self.prepare_reuse()
        self.assert_signed(self.collect(), expected, (7, 11), 18)

    def test_replay_keeps_receipt(self):
        old = self.prepare_reuse()
        signed = self.assert_signed(self.collect(), old, (7, 11), 18)
        self.record('F8', 13, 19)
        self.assertEqual(self.collect().artifact, signed)
        self.assertEqual(signed_counts(self.db), (1, 2, 1))

    def test_right_before_left(self):
        exposure = self.record()
        delivered = []

        def dispatch(reading, deliver):
            delivered.append(reading.side)
            deliver(reading)

        readout = Readout(self.pages, order=('right', 'left'), dispatch=dispatch)
        self.assert_signed(self.collect(reader=readout), exposure, (7, 11), 18)
        self.assertEqual(delivered, ['right', 'left'])

    def test_reused_page(self):
        old = self.prepare_reuse()
        recorded = []

        def after_copy(reading):
            if reading.side == 'left':
                self.assertEqual(reading.value_um, 7)
                recorded.append(self.record('F8', 13, 19))

        first = self.collect(reader=Readout(self.pages, after_copy=after_copy))
        (new,) = recorded
        self.assertEqual(self.pages.observe(new.page).complete, new)
        if new.page == old.page:
            self.assert_pending(first)
            self.assertEqual(signed_counts(self.db), (0, 0, 0))
            self.assert_signed(self.collect(), new, (13, 19), 32)
            self.assertEqual(signed_counts(self.db), (1, 2, 1))
        else:
            self.assert_signed(first, old, (7, 11), 18)
            self.assert_signed(self.collect('R2'), new, (13, 19), 32)
            self.assertEqual(signed_counts(self.db), (2, 4, 2))

    def test_in_progress_write(self):
        old = self.prepare_reuse()
        writing = []

        def pause_before_complete(phase, exposure):
            if phase == 'right':
                writing.append(exposure)
                raise PauseWrite

        def after_copy(reading):
            if reading.side == 'left':
                with self.assertRaises(PauseWrite):
                    self.record('F8', 13, 19, on_step=pause_before_complete)
                (new,) = writing
                stamp = self.pages.observe(new.page)
                self.assertEqual(stamp.active, new)
                self.assertEqual(stamp.completed if new.page == old.page else self.pages.observe(old.page).complete, old)
                self.assertIsNone(stamp.complete)
                self.assertEqual(self.pages.read_half(new.page, 'right'), 19)

        first = self.collect(reader=Readout(self.pages, after_copy=after_copy))
        (new,) = writing
        if new.page == old.page:
            self.assert_pending(first)
            self.assertIsNone(self.pages.find_complete_page('L1', 'T1'))
            self.assert_pending(self.collect())
            self.assertEqual(signed_counts(self.db), (0, 0, 0))
            next_request = 'R1'
        else:
            self.assert_signed(first, old, (7, 11), 18)
            next_request = 'R2'
        self.pages.finish_write(new)
        self.assert_signed(self.collect(next_request), new, (13, 19), 32)

    def test_record_while_pending(self):
        old = self.prepare_reuse()
        held = []

        def defer(reading, deliver):
            if reading.side == 'right':
                held.append((reading, deliver))
            else:
                deliver(reading)

        self.assert_pending(self.collect(reader=Readout(self.pages, dispatch=defer)))
        new = self.record('F8', 13, 19)
        self.assertGreater(new.write_id, old.write_id)
        self.assertIn(('complete', new), [(event.phase, event.exposure) for event in self.store.list_events()])
        self.assertIsNone(self.repository.lookup_request('R1').artifact_id)
        self.assert_signed(self.collect(), new, (13, 19), 32)
        ((reading, deliver),) = held
        deliver(reading)
        self.assertEqual(signed_counts(self.db), (1, 2, 1))

    def _late_half(self):
        old = self.prepare_reuse()
        held = []
        new_exposures = []

        def pause_old(reading, deliver):
            if reading.side == 'right':
                held.append((reading, deliver))
                new_exposures.append(self.record('F8', 13, 19))
            else:
                deliver(reading)

        first = self.collect(reader=Readout(self.pages, dispatch=pause_old))
        self.assert_pending(first)
        (new,) = new_exposures
        ((late, deliver_late),) = held
        self.assertEqual((late.value_um, late.evidence.exposure), (11, old))
        delivered = []

        def interleave(reading, deliver):
            self.assertNotEqual(reading.attempt_id, late.attempt_id)
            deliver(reading)
            if reading.side == 'left':
                self.assertEqual(reading.value_um, 13)
                deliver_late(late)
                self.assertIsNone(self.repository.lookup_request('R1').artifact_id)
                self.assertEqual(signed_counts(self.db), (0, 0, 0))
                delivered.append(late)

        signed = self.assert_signed(self.collect(reader=Readout(self.pages, dispatch=interleave)), new, (13, 19), 32)
        self.assertEqual(delivered, [late])
        self.assertEqual(signed_counts(self.db), (1, 2, 1))
        return signed, late, deliver_late

    def test_late_half(self):
        signed, _, _ = self._late_half()
        self.assertEqual(read_artifact(self.db, signed.receipt.artifact_id), signed)

    def test_late_after_signed(self):
        signed, late, deliver = self._late_half()
        deliver(late)
        self.assertEqual(self.collect().artifact, signed)
        self.assertEqual(read_artifact(self.open_again(), signed.receipt.artifact_id), signed)
        self.assertEqual(signed_counts(self.db), (1, 2, 1))

    def test_publish_retry(self):
        exposure = self.record()
        reject_receipt_commit(self.db)
        failed = self.collect()
        self.assert_pending(failed)
        self.assertEqual(failed.reason, RetryDisposition.PUBLISH_FAILED)
        self.assertIsNone(read_artifact(self.db, 'measurement:R1'))
        self.assertEqual(signed_counts(self.db), (0, 0, 0))
        allow_receipt_commit(self.db)
        signed = self.assert_signed(self.collect(), exposure, (7, 11), 18)
        with self.assertRaises(ValueError):
            self.collect(trigger='T2')
        self.assertEqual(self.collect().artifact, signed)
        self.assertEqual(signed_counts(self.db), (1, 2, 1))

    def test_rollback_visible_rows(self):
        exposure = self.record()
        observer = self.open_again()
        reject_receipt_commit(self.db)
        failed = self.collect()
        self.assertEqual(failed.reason, RetryDisposition.PUBLISH_FAILED)
        self.assertEqual(signed_counts(observer), (0, 0, 0))
        self.assertEqual(observer.execute('SELECT count(*) FROM commit_guard_child').fetchone(), (0,))
        self.assertIsNone(read_artifact(observer, 'measurement:R1'))
        before = close_shift(observer, 'S1', 'before-retry')
        self.assertEqual((before.summary.artifacts, before.summary.total_um, before.exceptions), ((), 0, ()))
        allow_receipt_commit(self.db)
        signed = self.assert_signed(self.collect(), exposure, (7, 11), 18)
        self.assertEqual(read_artifact(observer, signed.receipt.artifact_id), signed)
        self.assertEqual(signed_counts(observer), (1, 2, 1))
