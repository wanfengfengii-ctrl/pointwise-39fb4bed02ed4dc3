from dataclasses import dataclass

from device.exposures import Exposure, WriteEvent, require_side, validate_raw_read
from work.database import atomic


@dataclass(frozen=True)
class TrialWrite:
    trial_id: str
    exposure: Exposure
    original_write: int
    status: str


def create_device_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS device_exposures (
        write_id INTEGER PRIMARY KEY AUTOINCREMENT,
        page INTEGER NOT NULL CHECK(page IN (0, 1)),
        lot_id TEXT NOT NULL REFERENCES lots(lot_id),
        trigger_id TEXT NOT NULL REFERENCES triggers(trigger_id),
        exposure_id TEXT NOT NULL
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS device_pages (
        page INTEGER PRIMARY KEY CHECK(page IN (0, 1)),
        active_write INTEGER REFERENCES device_exposures(write_id),
        completed_write INTEGER REFERENCES device_exposures(write_id),
        left_write INTEGER REFERENCES device_exposures(write_id),
        left_um TEXT,
        right_write INTEGER REFERENCES device_exposures(write_id),
        right_um TEXT
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS device_halves (
        write_id INTEGER NOT NULL REFERENCES device_exposures(write_id),
        side TEXT NOT NULL CHECK(side IN ('left', 'right')),
        value_um TEXT NOT NULL,
        PRIMARY KEY (write_id, side)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS device_trials (
        trial_id TEXT PRIMARY KEY NOT NULL,
        write_id INTEGER NOT NULL REFERENCES device_exposures(write_id),
        page INTEGER NOT NULL CHECK(page IN (0, 1)),
        original_write INTEGER NOT NULL REFERENCES device_exposures(write_id),
        status TEXT NOT NULL CHECK(status IN ('writing', 'pending', 'confirmed', 'abandoned'))
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS device_trials_by_page ON device_trials(page, status)')
    db.execute('''CREATE TABLE IF NOT EXISTS device_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        write_id INTEGER NOT NULL REFERENCES device_exposures(write_id),
        phase TEXT NOT NULL CHECK(phase IN ('start', 'left', 'right', 'complete', 'abandoned'))
    )''')
    db.execute('INSERT OR IGNORE INTO device_pages (page) VALUES (0), (1)')


class DeviceStore:
    def __init__(self, db):
        self.db = db

    def _event(self, write_id, phase):
        self.db.execute('INSERT INTO device_events (write_id, phase) VALUES (?, ?)', (write_id, phase))

    def begin_write(self, page, lot_id, trigger_id, exposure_id):
        with atomic(self.db):
            row = self.db.execute('''SELECT p.active_write FROM device_pages p
                JOIN triggers t ON t.trigger_id = ? AND t.lot_id = ?
                WHERE p.page = ?''', (trigger_id, lot_id, page)).fetchone()
            if row is None or row[0] is not None:
                raise ValueError('page is unavailable for this write')
            inserted = self.db.execute('''INSERT INTO device_exposures
                (page, lot_id, trigger_id, exposure_id) VALUES (?, ?, ?, ?)''',
                (page, lot_id, trigger_id, exposure_id))
            write_id = inserted.lastrowid
            self.db.execute('UPDATE device_pages SET active_write = ? WHERE page = ?', (write_id, page))
            self._event(write_id, 'start')
            return Exposure(write_id, page, lot_id, trigger_id, exposure_id)

    def _writable_write_id(self, page):
        row = self.db.execute('SELECT active_write FROM device_pages WHERE page = ?', (page,)).fetchone()
        if row is None:
            raise ValueError(f'unknown physical page {page!r}')
        if row[0] is not None:
            return row[0]
        row = self.db.execute('''SELECT write_id FROM device_trials
            WHERE page = ? AND status = 'writing\'''', (page,)).fetchone()
        return None if row is None else row[0]

    def put_half(self, exposure, side, value):
        side = require_side(side)
        value = validate_raw_read(value)
        with atomic(self.db):
            if self._writable_write_id(exposure.page) != exposure.write_id:
                raise ValueError('half does not belong to the active exposure')
            updated = self.db.execute(f'''UPDATE device_pages
                SET {side}_write = ?, {side}_um = ? WHERE page = ?
                AND ({side}_write IS NULL OR {side}_write != ?)''',
                (exposure.write_id, hex(value), exposure.page, exposure.write_id))
            if updated.rowcount != 1:
                raise ValueError('half already written or exposure is no longer active')
            self.db.execute('INSERT INTO device_halves (write_id, side, value_um) VALUES (?, ?, ?)',
                            (exposure.write_id, side, hex(value)))
            self._event(exposure.write_id, side)

    def complete_write(self, exposure):
        with atomic(self.db):
            updated = self.db.execute('''UPDATE device_pages
                SET completed_write = active_write, active_write = NULL
                WHERE page = ? AND active_write = ? AND left_write = ? AND right_write = ?''',
                (exposure.page, exposure.write_id, exposure.write_id, exposure.write_id))
            if updated.rowcount != 1:
                raise ValueError('completion requires both halves of the active exposure')
            self._event(exposure.write_id, 'complete')

    def abandon_write(self, exposure):
        with atomic(self.db):
            updated = self.db.execute('''UPDATE device_pages SET active_write = NULL,
                completed_write = NULL, left_write = NULL, left_um = NULL, right_write = NULL,
                right_um = NULL WHERE page = ? AND active_write = ?''', (exposure.page, exposure.write_id))
            if updated.rowcount != 1:
                raise ValueError('only an active exposure can be abandoned')
            self._event(exposure.write_id, 'abandoned')

    def load_pages(self):
        rows = self.db.execute('''SELECT p.page,
            a.write_id, a.page, a.lot_id, a.trigger_id, a.exposure_id,
            c.write_id, c.page, c.lot_id, c.trigger_id, c.exposure_id
            FROM device_pages p
            LEFT JOIN device_exposures a ON a.write_id = p.active_write
            LEFT JOIN device_exposures c ON c.write_id = p.completed_write
            ORDER BY p.page''')
        return tuple((row[0], self._exposure(row[1:6]), self._exposure(row[6:11])) for row in rows)

    def load_trial(self, trial_id):
        row = self.db.execute('''SELECT t.trial_id, t.original_write, t.status,
            x.write_id, x.page, x.lot_id, x.trigger_id, x.exposure_id
            FROM device_trials t JOIN device_exposures x ON x.write_id = t.write_id
            WHERE t.trial_id = ?''', (trial_id,)).fetchone()
        return None if row is None else TrialWrite(row[0], Exposure(*row[3:]), row[1], row[2])

    def begin_trial(self, trial_id, original_write_id, exposure_id):
        with atomic(self.db):
            existing = self.load_trial(trial_id)
            if existing is not None:
                if (existing.original_write, existing.exposure.exposure_id) != (original_write_id, exposure_id):
                    raise ValueError(f'conflicting parameters for trial {trial_id!r}')
                return existing.exposure
            row = self.db.execute('''SELECT x.page, x.lot_id, x.trigger_id, p.completed_write, p.active_write
                FROM device_exposures x JOIN device_pages p ON p.page = x.page
                WHERE x.write_id = ?''', (original_write_id,)).fetchone()
            if row is None or row[3] != original_write_id or row[4] is not None:
                raise ValueError('original exposure is not the current formal frame on its page')
            page, lot_id, trigger_id = row[0], row[1], row[2]
            busy = self.db.execute('''SELECT 1 FROM device_trials
                WHERE page = ? AND status IN ('writing', 'pending')''', (page,)).fetchone()
            if busy is not None:
                raise ValueError(f'page {page} already has an undecided trial')
            inserted = self.db.execute('''INSERT INTO device_exposures
                (page, lot_id, trigger_id, exposure_id) VALUES (?, ?, ?, ?)''',
                (page, lot_id, trigger_id, exposure_id))
            write_id = inserted.lastrowid
            self.db.execute('INSERT INTO device_trials VALUES (?, ?, ?, ?, ?)',
                            (trial_id, write_id, page, original_write_id, 'writing'))
            self._event(write_id, 'start')
            return Exposure(write_id, page, lot_id, trigger_id, exposure_id)

    def is_trial_write(self, exposure):
        row = self.db.execute('''SELECT 1 FROM device_trials
            WHERE write_id = ? AND status = 'writing\'''', (exposure.write_id,)).fetchone()
        return row is not None

    def finish_trial(self, exposure):
        with atomic(self.db):
            updated = self.db.execute('''UPDATE device_trials SET status = 'pending'
                WHERE write_id = ? AND status = 'writing'
                AND EXISTS (SELECT 1 FROM device_pages p
                            WHERE p.page = ? AND p.left_write = ? AND p.right_write = ?)''',
                (exposure.write_id, exposure.page, exposure.write_id, exposure.write_id))
            if updated.rowcount != 1:
                raise ValueError('completion requires both halves of the active exposure')
            self._event(exposure.write_id, 'complete')

    def confirm_trial(self, trial_id):
        with atomic(self.db):
            trial = self.load_trial(trial_id)
            if trial is None:
                raise ValueError(f'unknown trial {trial_id!r}')
            if trial.status == 'confirmed':
                return trial.exposure
            if trial.status == 'abandoned':
                raise ValueError(f'trial {trial_id!r} was already abandoned')
            if trial.status != 'pending':
                raise ValueError(f'trial {trial_id!r} is not fully written')
            updated = self.db.execute('''UPDATE device_pages SET completed_write = ?
                WHERE page = ? AND completed_write = ? AND active_write IS NULL''',
                (trial.exposure.write_id, trial.exposure.page, trial.original_write))
            if updated.rowcount != 1:
                raise ValueError('the original page is no longer holding the trial original')
            self.db.execute("UPDATE device_trials SET status = 'confirmed' WHERE trial_id = ?", (trial_id,))
            return trial.exposure

    def abandon_trial(self, trial_id):
        with atomic(self.db):
            trial = self.load_trial(trial_id)
            if trial is None:
                raise ValueError(f'unknown trial {trial_id!r}')
            if trial.status == 'abandoned':
                return trial.exposure
            if trial.status == 'confirmed':
                raise ValueError(f'trial {trial_id!r} was already confirmed')
            halves = dict(self.db.execute('SELECT side, value_um FROM device_halves WHERE write_id = ?',
                                          (trial.original_write,)))
            self.db.execute('''UPDATE device_pages
                SET left_write = ?, left_um = ?, right_write = ?, right_um = ? WHERE page = ?''',
                (trial.original_write, halves.get('left'), trial.original_write, halves.get('right'),
                 trial.exposure.page))
            self.db.execute("UPDATE device_trials SET status = 'abandoned' WHERE trial_id = ?", (trial_id,))
            self._event(trial.exposure.write_id, 'abandoned')
            return trial.exposure

    def pending_trial_pages(self):
        rows = self.db.execute('''SELECT DISTINCT page FROM device_trials
            WHERE status IN ('writing', 'pending')''')
        return {row[0] for row in rows}

    def was_trial_superseded(self, write_id):
        row = self.db.execute('''SELECT 1 FROM device_trials
            WHERE original_write = ? AND status = 'confirmed\'''', (write_id,)).fetchone()
        return row is not None

    def load_retained_half(self, write_id, side):
        side = require_side(side)
        row = self.db.execute('SELECT value_um FROM device_halves WHERE write_id = ? AND side = ?',
                              (write_id, side)).fetchone()
        return None if row is None else int(row[0], 16)

    @staticmethod
    def _exposure(fields):
        return None if fields[0] is None else Exposure(*fields)

    def latest_page(self):
        row = self.db.execute('SELECT page FROM device_exposures ORDER BY write_id DESC LIMIT 1').fetchone()
        return None if row is None else row[0]

    def load_half(self, page, side):
        side = require_side(side)
        row = self.db.execute(f'SELECT {side}_um FROM device_pages WHERE page = ?', (page,)).fetchone()
        if row is None:
            raise ValueError(f'unknown physical page {page!r}')
        return None if row[0] is None else int(row[0], 16)

    def list_events(self):
        rows = self.db.execute('''SELECT e.event_id, e.phase, x.write_id, x.page,
            x.lot_id, x.trigger_id, x.exposure_id FROM device_events e
            JOIN device_exposures x ON x.write_id = e.write_id ORDER BY e.event_id''')
        return tuple(WriteEvent(row[0], row[1], Exposure(*row[2:])) for row in rows)
