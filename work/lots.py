from dataclasses import dataclass

from work.database import atomic
from work.limits import freeze_limit, load_limit, validate_threshold
from work.shifts import ensure_shift, require_id
from work.triggers import list_lot_triggers, normalize_triggers, register_triggers


@dataclass(frozen=True)
class LotRegistration:
    shift_id: str
    lot_id: str
    triggers: tuple[str, ...]
    rule_id: str
    threshold_um: int


def create_lot_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS lots (
        lot_id TEXT PRIMARY KEY NOT NULL,
        shift_id TEXT NOT NULL REFERENCES shifts(shift_id),
        rule_id TEXT NOT NULL UNIQUE REFERENCES limit_rules(rule_id)
    )''')


def load_lot(db, lot_id):
    require_id(lot_id, 'lot_id')
    row = db.execute('SELECT shift_id, rule_id FROM lots WHERE lot_id = ?', (lot_id,)).fetchone()
    if row is None:
        return None
    shift_id, rule_id = row
    return LotRegistration(shift_id, lot_id, list_lot_triggers(db, lot_id), rule_id,
                           load_limit(db, rule_id).threshold_um)


def open_lot(db, shift_id, lot_id, triggers, threshold_um):
    require_id(shift_id, 'shift_id')
    require_id(lot_id, 'lot_id')
    roster = normalize_triggers(triggers)
    threshold = validate_threshold(threshold_um)
    with atomic(db):
        existing = load_lot(db, lot_id)
        if existing is not None:
            if (existing.shift_id, existing.triggers, existing.threshold_um) != (shift_id, roster, threshold):
                raise ValueError(f'conflicting registration for lot {lot_id!r}')
            return existing
        ensure_shift(db, shift_id)
        rule = freeze_limit(db, lot_id, threshold)
        db.execute('INSERT INTO lots VALUES (?, ?, ?)', (lot_id, shift_id, rule.rule_id))
        register_triggers(db, lot_id, roster)
        return LotRegistration(shift_id, lot_id, roster, rule.rule_id, threshold)
