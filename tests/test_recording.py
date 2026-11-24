from device.exposures import Exposure
from device.pages import PageBank
from device.recorder import Recorder, record_frame
from device.storage import DeviceStore
from handover.history import read_artifact
from handover.register import close_shift
from tests.fixtures import INITIAL, PauseWrite, StationTest
from work.bootstrap import StationConfig, initialize_station
from work.limits import load_limit
from work.lots import load_lot, open_lot
from work.shifts import require_shift
from work.triggers import find_trigger_owner


class TestRecording(StationTest):
    def test_initialize_and_record(self):
        self.assertEqual(initialize_station(self.db, INITIAL), self.lot)
        self.assertEqual(load_lot(self.db, 'L1'), self.lot)
        old = self.record()
        self.assertEqual(old, Exposure(1, 0, 'L1', 'T1', 'F7'))
        self.assertEqual(old.source_key, (1, 0))
        self.assertEqual(self.pages.find_complete_page('L1', 'T1').complete, old)
        self.assertEqual((self.pages.read_half(old.page, 'left'), self.pages.read_half(old.page, 'right')), (7, 11))
        events = self.store.list_events()
        self.assertEqual([event.phase for event in events], ['start', 'left', 'right', 'complete'])
        self.assertEqual([event.event_id for event in events], [1, 2, 3, 4])
        self.assertTrue(all(event.exposure == old for event in events))

    def test_initialize_repeat(self):
        extra = open_lot(self.db, 'S1', 'L2', ('T3',), 9)
        old = self.record()
        self.assert_pending(self.collect('P2', trigger='T2'), 'P2')
        signed = self.assert_signed(self.collect(), old, (7, 11), 18)
        report = close_shift(self.db, 'S1', 'before-restart')
        db = self.open_again()
        same_config = StationConfig('S1', 'L1', ('T2', 'T1'), 10)
        self.assertEqual(initialize_station(db, same_config), self.lot)
        self.assertEqual(load_lot(db, 'L2'), extra)
        self.assertEqual(find_trigger_owner(db, 'T3').lot_id, 'L2')
        self.assertEqual(load_limit(db, self.lot.rule_id).threshold_um, 10)
        self.assertEqual(read_artifact(db, signed.receipt.artifact_id), signed)
        self.assertEqual(read_artifact(db, report.report_id), report)
        self.assertEqual(db.execute('SELECT request_id, artifact_id FROM requests WHERE request_id = ?', ('P2',)).fetchone(), ('P2', None))
        pages = PageBank(DeviceStore(db))
        self.assertEqual(pages.find_complete_page('L1', 'T1').complete, old)
        new = record_frame(Recorder(db, pages), 'T3', 'F9', 5, 8)
        self.assertEqual((new.write_id, new.lot_id, new.trigger_id), (2, 'L2', 'T3'))

    def test_initialize_conflict(self):
        old = self.record()
        events = self.store.list_events()
        db = self.open_again()
        with self.assertRaises(ValueError):
            initialize_station(db, StationConfig('S1', 'L1', ('T1', 'T2'), 12))
        self.assertEqual(load_lot(db, 'L1'), self.lot)
        self.assertEqual(load_limit(db, self.lot.rule_id).threshold_um, 10)
        store = DeviceStore(db)
        self.assertEqual(store.list_events(), events)
        self.assertEqual(PageBank(store).observe(old.page).complete, old)
        self.assertEqual(initialize_station(db, INITIAL), self.lot)

    def test_incomplete_write(self):
        stopped = []

        def pause_on_left(phase, exposure):
            if phase == 'left':
                stopped.append(exposure)
                raise PauseWrite

        with self.assertRaises(PauseWrite):
            self.record(on_step=pause_on_left)
        (exposure,) = stopped
        self.assertIsNone(self.pages.find_complete_page('L1', 'T1'))
        self.assertEqual(self.pages.observe(exposure.page).active, exposure)
        self.assertEqual(self.pages.read_half(exposure.page, 'left'), 7)
        self.assertIsNone(self.pages.read_half(exposure.page, 'right'))
        with self.assertRaises(ValueError):
            self.pages.finish_write(exposure)
        reopened = PageBank(DeviceStore(self.open_again()))
        self.assertEqual(reopened.observe(exposure.page).active, exposure)
        self.assertIsNone(reopened.find_complete_page('L1', 'T1'))
        reopened.write_half(exposure, 'right', 11)
        reopened.finish_write(exposure)
        self.assertEqual(reopened.find_complete_page('L1', 'T1').complete, exposure)
        self.assertEqual([e.phase for e in self.store.list_events()], ['start', 'left', 'right', 'complete'])

    def test_loan_other_page(self):
        old = self.prepare_reuse()
        loan = self.pages.acquire_loan(old.page, old)
        another = self.pages.acquire_loan(old.page, old)
        self.assertIsNotNone(loan)
        self.assertIsNotNone(another)
        try:
            new = self.record('F8', 13, 19)
            self.assertNotEqual(new.page, old.page)
            self.assertEqual(self.pages.observe(new.page).complete, new)
            self.assertEqual(self.pages.read_half(old.page, 'left', loan), 7)
            self.assertTrue(self.pages.release_loan(loan))
            self.assertFalse(self.pages.release_loan(loan))
            self.assertEqual(self.pages.read_half(old.page, 'right', another), 11)
            later = self.record('F9', 23, 29)
            self.assertEqual(later.page, new.page)
        finally:
            self.pages.release_loan(loan)
            self.pages.release_loan(another)
        reused = self.record('F10', 31, 37)
        self.assertEqual(reused.page, old.page)
        self.assertIsNone(self.pages.acquire_loan(old.page, old))
        self.assertIn(('complete', new), [(event.phase, event.exposure) for event in self.store.list_events()])

    def test_unknown_trigger(self):
        old = self.record()
        with self.assertRaisesRegex(ValueError, 'unknown trigger'):
            self.record('X', 1, 2, 'unregistered')
        self.assertEqual(self.pages.find_complete_page('L1', 'T1').complete, old)
        self.assertEqual(self.pages.read_half(old.page, 'right'), 11)

    def test_reject_before_reuse(self):
        old = self.prepare_reuse()
        stamp = self.pages.observe(old.page)
        events = self.store.list_events()
        with self.assertRaises(ValueError):
            self.record('X', 1, 2, 'unregistered')
        self.assertEqual(self.pages.observe(old.page), stamp)
        self.assertEqual(self.store.list_events(), events)
        self.assertEqual((self.pages.read_half(old.page, 'left'), self.pages.read_half(old.page, 'right')), (7, 11))
        self.assertEqual(self.record('F8', 13, 19).page, old.page)

    def test_invalid_inputs_preserve_existing_work(self):
        old = self.prepare_reuse()
        for invalid in (True, None, 7.0, '7'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.record('invalid', invalid, 19)
        self.assertEqual(self.pages.observe(old.page).complete, old)
        with self.assertRaises(ValueError):
            open_lot(self.db, 'S2', 'L2', ('T2', 'T3'), 5)
        with self.assertRaises(ValueError):
            require_shift(self.db, 'S2')
        self.assertIsNone(load_lot(self.db, 'L2'))
        self.assertIsNone(find_trigger_owner(self.db, 'T3'))
        with self.assertRaises(ValueError):
            open_lot(self.db, 'S1', 'L2', ('T3',), False)
        self.assertIsNone(load_lot(self.db, 'L2'))
