from dataclasses import dataclass


@dataclass(frozen=True)
class LimitRule:
    rule_id: str
    threshold_um: int


def create_limit_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS limit_rules (
        rule_id TEXT PRIMARY KEY NOT NULL,
        threshold_um TEXT NOT NULL
    )''')


def validate_threshold(value):
    if type(value) is not int:
        raise ValueError('threshold_um must be an integer')
    return value


def freeze_limit(db, lot_id, threshold_um):
    rule = LimitRule(f'rule:{lot_id}', validate_threshold(threshold_um))
    db.execute('INSERT INTO limit_rules VALUES (?, ?)', (rule.rule_id, hex(rule.threshold_um)))
    return rule


def load_limit(db, rule_id):
    row = db.execute('SELECT threshold_um FROM limit_rules WHERE rule_id = ?', (rule_id,)).fetchone()
    if row is None:
        raise LookupError(f'unknown frozen limit {rule_id!r}')
    return LimitRule(rule_id, int(row[0], 16))
