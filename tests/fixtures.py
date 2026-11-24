from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from capture.requests import collect
from device.pages import PageBank
from device.recorder import Recorder, record_frame
from device.storage import DeviceStore
from intake.acceptance import Acceptance
from intake.repository import ReceiptRepository
from work.bootstrap import StationConfig, initialize_station
from work.database import open_database


INITIAL = StationConfig('S1', 'L1', ('T1', 'T2'), 10)


class PauseWrite(Exception):
    pass


class StationTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'station.sqlite'
        self.db = self.open_again()
        self.lot = initialize_station(self.db, INITIAL)
        self.store = DeviceStore(self.db)
        self.pages = PageBank(self.store)
        self.recorder = Recorder(self.db, self.pages)
        self.repository = ReceiptRepository(self.db)
        self.acceptance = Acceptance(self.repository)

    def open_again(self):
        db = open_database(self.path)
        self.addCleanup(db.close)
        return db

    def record(self, exposure='F7', left=7, right=11, trigger='T1', on_step=None):
        return record_frame(self.recorder, trigger, exposure, left, right, on_step)

    def prepare_reuse(self):
        old = self.record()
        neighbor = self.record('neighbor', 2, 3, 'T2')
        self.assertNotEqual(old.page, neighbor.page)
        return old

    def collect(self, request='R1', lot='L1', trigger='T1', reader=None):
        return collect(self.acceptance, self.pages, request, lot, trigger, readout=reader)

    def assert_signed(self, result, exposure, values, total):
        self.assertEqual(result.status, 'signed')
        artifact = result.artifact
        self.assertIsNotNone(artifact)
        measurement = artifact.measurement
        self.assertEqual(tuple(half.side for half in measurement.halves), ('left', 'right'))
        self.assertEqual(tuple(half.value_um for half in measurement.halves), values)
        self.assertEqual(measurement.total_um, total)
        self.assertEqual(tuple(half.evidence.side for half in measurement.halves), ('left', 'right'))
        self.assertEqual(tuple(half.evidence.exposure for half in measurement.halves), (exposure, exposure))
        self.assertEqual(artifact.receipt.request_id, measurement.request_id)
        self.assertTrue(artifact.receipt.receipt_id)
        return artifact

    def assert_pending(self, result, request='R1'):
        self.assertEqual(result.status, 'pending')
        self.assertIsNone(result.artifact)
        self.assertIsNone(self.repository.lookup_request(request).artifact_id)


def signed_counts(db):
    return tuple(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                 for table in ('artifacts', 'artifact_halves', 'receipts'))


def reject_receipt_commit(db):
    # The trigger runs after receipt insertion. The missing reference is checked at COMMIT.
    db.execute('CREATE TABLE commit_guard_parent (id TEXT PRIMARY KEY NOT NULL)')
    db.execute('''CREATE TABLE commit_guard_child (id TEXT REFERENCES commit_guard_parent(id)
        DEFERRABLE INITIALLY DEFERRED)''')
    db.execute('''CREATE TRIGGER reject_receipt_commit AFTER INSERT ON receipts BEGIN
        INSERT INTO commit_guard_child VALUES (NEW.receipt_id);
    END''')


def allow_receipt_commit(db):
    db.execute('DROP TRIGGER reject_receipt_commit')
