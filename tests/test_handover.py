from capture.requests import collect
from handover.history import read_artifact
from handover.register import close_shift
from intake.acceptance import Acceptance
from intake.repository import ReceiptRepository
from tests.fixtures import StationTest
from work.lots import open_lot


class TestHandover(StationTest):
    def test_close_and_reopen(self):
        exposure = self.record()
        signed = self.assert_signed(self.collect(), exposure, (7, 11), 18)
        report = close_shift(self.db, 'S1', 'night')
        self.assertEqual(report.summary.artifacts, (signed,))
        self.assertEqual(report.summary.total_um, 18)
        (entry,) = report.exceptions
        self.assertEqual((entry.artifact_id, entry.threshold_um, entry.value_um), (signed.receipt.artifact_id, 10, 11))
        self.assertEqual((entry.evidence.side, entry.evidence.exposure), ('right', exposure))
        self.db.close()
        db = self.open_again()
        self.assertEqual(read_artifact(db, 'night'), report)
        self.assertEqual(read_artifact(db, signed.receipt.artifact_id), signed)
        self.assertEqual(close_shift(db, 'S1', 'night'), report)

    def test_threshold_equality(self):
        source = self.record('equal', 10, 11)
        self.assert_signed(self.collect(), source, (10, 11), 21)
        report = close_shift(self.db, 'S1', 'equality')
        self.assertEqual(report.summary.total_um, 21)
        self.assertEqual([(entry.evidence.side, entry.value_um, entry.threshold_um) for entry in report.exceptions],
                         [('right', 11, 10)])
        self.assertEqual(read_artifact(self.db, 'equality'), report)

    def test_frozen_rules_per_lot(self):
        second = open_lot(self.db, 'S1', 'L2', ('T3',), 12)
        old = self.record()
        new = self.record('second', 11, 12, 'T3')
        newer_signed = self.assert_signed(self.collect('R2', 'L2', 'T3'), new, (11, 12), 23)
        older_signed = self.assert_signed(self.collect(), old, (7, 11), 18)
        same_exposure = self.assert_signed(self.collect('R3'), old, (7, 11), 18)
        report = close_shift(self.db, 'S1', 'two-rules')
        self.assertEqual(report.summary.artifacts, (older_signed, same_exposure, newer_signed))
        self.assertEqual(report.summary.total_um, 59)
        self.assertEqual(newer_signed.measurement.rule_id, second.rule_id)
        self.assertEqual([(entry.artifact_id, entry.rule_id, entry.evidence.side) for entry in report.exceptions],
                         [(older_signed.receipt.artifact_id, self.lot.rule_id, 'right'),
                          (same_exposure.receipt.artifact_id, self.lot.rule_id, 'right')])

    def test_offline_original_source(self):
        old = self.prepare_reuse()
        signed = self.assert_signed(self.collect(), old, (7, 11), 18)
        report = close_shift(self.db, 'S1', 'original')
        new = self.record('F8', 13, 19)
        self.assertEqual(new.page, old.page)
        self.assertEqual(self.pages.observe(new.page).complete, new)
        self.db.close()
        db = self.open_again()
        restored = read_artifact(db, signed.receipt.artifact_id)
        self.assertEqual(restored, signed)
        self.assertEqual(restored.measurement.exposure, old)
        self.assertEqual(read_artifact(db, report.report_id), report)
        self.assertEqual(report.exceptions[0].evidence.exposure, old)
        replay = collect(Acceptance(ReceiptRepository(db)), None, 'R1', 'L1', 'T1')
        self.assertEqual(replay.artifact, signed)

    def test_exclude_pending(self):
        self.assert_pending(self.collect())
        report = close_shift(self.db, 'S1', 'empty')
        self.assertEqual((report.summary.artifacts, report.summary.total_um, report.exceptions), ((), 0, ()))
        self.assertIsNone(read_artifact(self.db, 'R1'))
        self.assertIsNone(read_artifact(self.db, 'measurement:R1'))
        self.assertIsNone(read_artifact(self.db, 'unknown'))

    def test_new_receipt_after_close(self):
        self.assert_pending(self.collect())
        empty = close_shift(self.db, 'S1', 'original')
        source = self.record()
        signed = self.assert_signed(self.collect(), source, (7, 11), 18)
        db = self.open_again()
        self.assertEqual(close_shift(db, 'S1', 'original'), empty)
        self.assertEqual(read_artifact(db, 'original'), empty)
        self.assertEqual(empty.summary.artifacts, ())
        self.assertEqual(close_shift(db, 'S1', 'later').summary.artifacts, (signed,))
        open_lot(db, 'S2', 'other', ('T3',), 8)
        with self.assertRaises(ValueError):
            close_shift(db, 'S2', 'original')
        with self.assertRaises(ValueError):
            close_shift(db, 'unregistered', 'another')
        self.assertEqual(read_artifact(db, 'original'), empty)
