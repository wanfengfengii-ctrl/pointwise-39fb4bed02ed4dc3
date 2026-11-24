from dataclasses import dataclass

from work.shifts import require_id


@dataclass(frozen=True)
class TriggerOwner:
    trigger_id: str
    lot_id: str


def create_trigger_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS triggers (
        trigger_id TEXT PRIMARY KEY NOT NULL,
        lot_id TEXT NOT NULL REFERENCES lots(lot_id)
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS triggers_by_lot ON triggers(lot_id, trigger_id)')


def normalize_triggers(values):
    if isinstance(values, (str, bytes)):
        raise ValueError('triggers must be a collection of trigger IDs')
    try:
        triggers = tuple(require_id(value, 'trigger_id') for value in values)
    except TypeError as error:
        raise ValueError('triggers must be a collection of trigger IDs') from error
    if not triggers or len(set(triggers)) != len(triggers):
        raise ValueError('trigger roster must contain distinct trigger IDs')
    return tuple(sorted(triggers))


def register_triggers(db, lot_id, triggers):
    for trigger_id in triggers:
        owner = find_trigger_owner(db, trigger_id)
        if owner is not None:
            raise ValueError(f'trigger {trigger_id!r} is already registered to {owner.lot_id!r}')
    db.executemany('INSERT INTO triggers VALUES (?, ?)', ((trigger, lot_id) for trigger in triggers))


def find_trigger_owner(db, trigger_id):
    require_id(trigger_id, 'trigger_id')
    row = db.execute('SELECT lot_id FROM triggers WHERE trigger_id = ?', (trigger_id,)).fetchone()
    return None if row is None else TriggerOwner(trigger_id, row[0])


def list_lot_triggers(db, lot_id):
    rows = db.execute('SELECT trigger_id FROM triggers WHERE lot_id = ? ORDER BY trigger_id', (lot_id,))
    return tuple(row[0] for row in rows)
