from dataclasses import dataclass


@dataclass(frozen=True)
class Shift:
    shift_id: str


def require_id(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a nonempty string')
    return value


def create_shift_schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS shifts (shift_id TEXT PRIMARY KEY NOT NULL)')


def ensure_shift(db, shift_id):
    require_id(shift_id, 'shift_id')
    db.execute('INSERT INTO shifts VALUES (?) ON CONFLICT(shift_id) DO NOTHING', (shift_id,))
    return Shift(shift_id)


def require_shift(db, shift_id):
    require_id(shift_id, 'shift_id')
    row = db.execute('SELECT shift_id FROM shifts WHERE shift_id = ?', (shift_id,)).fetchone()
    if row is None:
        raise ValueError(f'unknown shift {shift_id!r}')
    return Shift(*row)
