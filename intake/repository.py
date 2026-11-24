from dataclasses import dataclass
import sqlite3

from capture.provenance import HalfEvidence
from device.exposures import Exposure
from intake.measurements import Measurement, MeasurementHalf
from intake.receipts import Receipt, RequestBinding, new_receipt, restore_receipt
from work.database import atomic
from work.shifts import require_id
from work.triggers import find_trigger_owner


@dataclass(frozen=True)
class SignedArtifact:
    measurement: Measurement
    receipt: Receipt


class PublishError(RuntimeError):
    pass


def create_intake_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS requests (
        request_id TEXT PRIMARY KEY NOT NULL,
        lot_id TEXT NOT NULL REFERENCES lots(lot_id),
        trigger_id TEXT NOT NULL REFERENCES triggers(trigger_id),
        active_attempt INTEGER REFERENCES capture_attempts(attempt_id),
        artifact_id TEXT UNIQUE REFERENCES artifacts(artifact_id)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS artifacts (
        sign_order INTEGER PRIMARY KEY AUTOINCREMENT,
        artifact_id TEXT NOT NULL UNIQUE,
        request_id TEXT NOT NULL UNIQUE REFERENCES requests(request_id),
        rule_id TEXT NOT NULL REFERENCES limit_rules(rule_id),
        total_um TEXT NOT NULL
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS artifact_halves (
        artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
        side TEXT NOT NULL CHECK(side IN ('left', 'right')),
        value_um TEXT NOT NULL,
        write_id INTEGER NOT NULL,
        page INTEGER NOT NULL,
        source_lot TEXT NOT NULL,
        source_trigger TEXT NOT NULL,
        exposure_id TEXT NOT NULL,
        PRIMARY KEY (artifact_id, side)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS receipts (
        receipt_id TEXT PRIMARY KEY NOT NULL,
        artifact_id TEXT NOT NULL UNIQUE REFERENCES artifacts(artifact_id)
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS requests_by_lot ON requests(lot_id)')


class ReceiptRepository:
    def __init__(self, db):
        self.db = db

    def bind_request(self, request_id, lot_id, trigger_id):
        require_id(request_id, 'request_id')
        require_id(lot_id, 'lot_id')
        require_id(trigger_id, 'trigger_id')
        with atomic(self.db):
            existing = self.lookup_request(request_id)
            if existing is not None:
                if (existing.lot_id, existing.trigger_id) != (lot_id, trigger_id):
                    raise ValueError(f'conflicting parameters for request {request_id!r}')
                return existing
            owner = find_trigger_owner(self.db, trigger_id)
            if owner is None or owner.lot_id != lot_id:
                raise ValueError('trigger does not belong to the requested lot')
            self.db.execute('INSERT INTO requests (request_id, lot_id, trigger_id) VALUES (?, ?, ?)',
                            (request_id, lot_id, trigger_id))
            return RequestBinding(request_id, lot_id, trigger_id, None)

    def lookup_request(self, request_id):
        row = self.db.execute('SELECT lot_id, trigger_id, artifact_id FROM requests WHERE request_id = ?',
                              (request_id,)).fetchone()
        return None if row is None else RequestBinding(request_id, *row)

    def activate_attempt(self, binding, attempt_id):
        with atomic(self.db):
            cursor = self.db.execute('''UPDATE requests SET active_attempt = ?
                WHERE request_id = ? AND lot_id = ? AND trigger_id = ? AND artifact_id IS NULL
                AND (active_attempt IS NULL OR active_attempt < ?)
                AND EXISTS (SELECT 1 FROM capture_attempts a
                            WHERE a.attempt_id = ? AND a.request_id = requests.request_id)''',
                (attempt_id, binding.request_id, binding.lot_id, binding.trigger_id, attempt_id, attempt_id))
            return cursor.rowcount == 1

    def publish(self, binding, attempt_id, measurement):
        scope = (binding.request_id, binding.lot_id, binding.trigger_id)
        if (measurement.request_id, measurement.lot_id, measurement.trigger_id) != scope:
            raise ValueError('measurement differs from the registered request')
        try:
            with atomic(self.db):
                row = self.db.execute('''SELECT active_attempt, artifact_id FROM requests
                    WHERE request_id = ? AND lot_id = ? AND trigger_id = ?''', scope).fetchone()
                if row is None:
                    raise ValueError('publication requires a matching request')
                if row[1] is not None:
                    return self.load_artifact(row[1])
                if row[0] != attempt_id:
                    return None
                receipt = new_receipt(binding.request_id)
                self._write_measurement(receipt.artifact_id, measurement)
                self.db.execute('INSERT INTO receipts VALUES (?, ?)', (receipt.receipt_id, receipt.artifact_id))
                self.db.execute('UPDATE requests SET artifact_id = ? WHERE request_id = ?',
                                (receipt.artifact_id, binding.request_id))
                return SignedArtifact(measurement, receipt)
        except sqlite3.Error as error:
            raise PublishError(f'publication failed for {binding.request_id!r}') from error

    def _write_measurement(self, artifact_id, measurement):
        self.db.execute('''INSERT INTO artifacts (artifact_id, request_id, rule_id, total_um)
            VALUES (?, ?, ?, ?)''', (artifact_id, measurement.request_id, measurement.rule_id,
                                    hex(measurement.total_um)))
        for half in measurement.halves:
            source = half.evidence.exposure
            self.db.execute('INSERT INTO artifact_halves VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                            (artifact_id, half.side, hex(half.value_um), source.write_id, source.page,
                             source.lot_id, source.trigger_id, source.exposure_id))

    def load_artifact(self, artifact_id):
        row = self.db.execute('''SELECT r.request_id, r.lot_id, r.trigger_id, a.rule_id, a.total_um, s.receipt_id
            FROM artifacts a JOIN requests r ON r.request_id = a.request_id AND r.artifact_id = a.artifact_id
            JOIN receipts s ON s.artifact_id = a.artifact_id WHERE a.artifact_id = ?''', (artifact_id,)).fetchone()
        if row is None:
            return None
        half_rows = self.db.execute('''SELECT side, value_um, write_id, page, source_lot, source_trigger, exposure_id
            FROM artifact_halves WHERE artifact_id = ? ORDER BY side''', (artifact_id,))
        halves = tuple(MeasurementHalf(half[0], int(half[1], 16), HalfEvidence(Exposure(*half[2:]), half[0]))
                       for half in half_rows)
        if tuple(half.side for half in halves) != ('left', 'right'):
            raise sqlite3.IntegrityError('committed artifact is missing an original half')
        return SignedArtifact(Measurement(*row[:4], halves, int(row[4], 16)),
                              restore_receipt(row[0], artifact_id, row[5]))

    def list_committed(self, shift_id):
        rows = self.db.execute('''SELECT a.artifact_id FROM artifacts a
            JOIN requests r ON r.request_id = a.request_id AND r.artifact_id = a.artifact_id
            JOIN receipts s ON s.artifact_id = a.artifact_id
            JOIN lots l ON l.lot_id = r.lot_id WHERE l.shift_id = ? ORDER BY a.sign_order''', (shift_id,))
        return tuple(self.load_artifact(row[0]) for row in rows)
