from device.exposures import Exposure, WriteEvent, require_side, validate_raw_read
from work.database import atomic


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

    def put_half(self, exposure, side, value):
        side = require_side(side)
        value = validate_raw_read(value)
        with atomic(self.db):
            updated = self.db.execute(f'''UPDATE device_pages
                SET {side}_write = ?, {side}_um = ? WHERE page = ? AND active_write = ?
                AND ({side}_write IS NULL OR {side}_write != ?)''',
                (exposure.write_id, hex(value), exposure.page, exposure.write_id, exposure.write_id))
            if updated.rowcount != 1:
                raise ValueError('half already written or exposure is no longer active')
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
