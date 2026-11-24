from capture.readout import Readout
from capture.requests import collect
from handover.history import read_artifact
from handover.register import close_shift
from intake.acceptance import Acceptance
from intake.repository import ReceiptRepository
from tests.fixtures import StationTest, signed_counts
from work.lots import open_lot


class TestStationBoundaries(StationTest):
    def test_only_latest_active_attempt_can_publish_across_connections(self):
        self.prepare_reuse()
        queued = []

        def dispatch(reading, deliver):
            if reading.side == 'right':
                queued.append((reading, deliver))
            else:
                deliver(reading)

        self.assert_pending(self.collect(reader=Readout(self.pages, dispatch=dispatch)))
        new = self.record('F8', 13, 19)
        other_db = self.open_again()
        other = Acceptance(ReceiptRepository(other_db))
        second = collect(other, self.pages, 'R1', 'L1', 'T1', readout=Readout(self.pages, dispatch=dispatch))
        self.assert_pending(second)
        (old_half, old_deliver), (new_half, new_deliver) = queued
        self.assertGreater(new_half.attempt_id, old_half.attempt_id)
        old_deliver(old_half)
        self.assertEqual(signed_counts(other_db), (0, 0, 0))
        new_deliver(new_half)
        self.assert_signed(self.collect(), new, (13, 19), 32)
        self.assertEqual(signed_counts(other_db), (1, 2, 1))

    def test_qualification_survives_page_reuse_after_read_interval(self):
        old = self.prepare_reuse()
        queued = []

        def dispatch(reading, deliver):
            if reading.side == 'right':
                queued.append((reading, deliver))
            else:
                deliver(reading)

        self.assert_pending(self.collect(reader=Readout(self.pages, dispatch=dispatch)))
        replacement = self.record('F8', 13, 19)
        self.assertEqual(replacement.page, old.page)
        ((right, deliver),) = queued
        deliver(right)
        self.assert_signed(self.collect(), old, (7, 11), 18)

    def test_zero_and_exact_large_integers(self):
        large = 2 ** 80
        open_lot(self.db, 'S2', 'L2', ('T3',), large - 1)
        source = self.record('wide', 0, large, 'T3')
        signed = self.assert_signed(self.collect('wide', 'L2', 'T3'), source, (0, large), large)
        report = close_shift(self.db, 'S2', 'wide-shift')
        self.assertEqual(report.summary.total_um, large)
        self.assertEqual([(entry.evidence.side, entry.threshold_um, entry.value_um) for entry in report.exceptions],
                         [('right', large - 1, large)])
        db = self.open_again()
        self.assertEqual(read_artifact(db, signed.receipt.artifact_id), signed)
        self.assertEqual(read_artifact(db, 'wide-shift'), report)
