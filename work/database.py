from contextlib import contextmanager
from itertools import count
import sqlite3


_savepoints = count(1)


def open_database(path):
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute('PRAGMA foreign_keys = ON')
    except BaseException:
        db.close()
        raise
    return db


@contextmanager
def atomic(db):
    if db.in_transaction:
        name = f'station_{next(_savepoints)}'
        db.execute(f'SAVEPOINT {name}')
        try:
            yield
            db.execute(f'RELEASE SAVEPOINT {name}')
        except BaseException:
            db.execute(f'ROLLBACK TO SAVEPOINT {name}')
            db.execute(f'RELEASE SAVEPOINT {name}')
            raise
        return

    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise
