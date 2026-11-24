from dataclasses import dataclass, field

from capture.provenance import HalfReading
from device.exposures import Exposure, SIDES
from work.database import atomic


@dataclass(frozen=True)
class AttemptQualification:
    request_id: str
    attempt_id: int
    exposure: Exposure | None
    reason: str | None = None


@dataclass
class Attempt:
    attempt_id: int
    request_id: str
    lot_id: str
    trigger_id: str
    readings: dict[str, HalfReading] = field(default_factory=dict, init=False)
    decision: AttemptQualification | None = field(default=None, init=False)

    def record_read(self, reading):
        if self.decision is not None:
            raise ValueError('attempt has already terminated')
        if (reading.request_id, reading.attempt_id) != (self.request_id, self.attempt_id):
            raise ValueError('reading belongs to a different attempt')
        previous = self.readings.get(reading.side)
        if previous is not None and previous != reading:
            raise ValueError('contradictory readings for the same half')
        self.readings[reading.side] = reading

    def qualify(self, exposure):
        if self.decision is not None:
            raise ValueError('attempt has already terminated')
        if (exposure.lot_id, exposure.trigger_id) != (self.lot_id, self.trigger_id):
            raise ValueError('qualification is outside the requested scope')
        if set(self.readings) != set(SIDES) or any(
            reading.evidence.exposure != exposure for reading in self.readings.values()
        ):
            raise ValueError('qualification requires both original halves')
        self.decision = AttemptQualification(self.request_id, self.attempt_id, exposure)
        return self.decision

    def invalidate(self, reason):
        if self.decision is not None:
            raise ValueError('attempt has already terminated')
        self.decision = AttemptQualification(self.request_id, self.attempt_id, None, reason)
        return self.decision


def create_attempt_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS capture_attempts (
        attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL REFERENCES requests(request_id)
    )''')


def new_attempt(db, request_id, lot_id, trigger_id):
    with atomic(db):
        row = db.execute('SELECT lot_id, trigger_id, artifact_id FROM requests WHERE request_id = ?',
                         (request_id,)).fetchone()
        if row is None or row[:2] != (lot_id, trigger_id):
            raise ValueError('attempt requires a matching registered request')
        if row[2] is not None:
            return None
        cursor = db.execute('INSERT INTO capture_attempts (request_id) VALUES (?)', (request_id,))
        return Attempt(cursor.lastrowid, request_id, lot_id, trigger_id)
