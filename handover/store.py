from dataclasses import dataclass
import sqlite3

from handover.exceptions import LimitException
from handover.summary import ShiftSummary
from intake.repository import ReceiptRepository
from work.database import atomic


@dataclass(frozen=True)
class ShiftReport:
    report_id: str
    shift_id: str
    summary: ShiftSummary
    exceptions: tuple[LimitException, ...]


def create_handover_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS shift_reports (
        report_id TEXT PRIMARY KEY NOT NULL,
        shift_id TEXT NOT NULL REFERENCES shifts(shift_id),
        total_um TEXT NOT NULL
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS report_items (
        report_id TEXT NOT NULL REFERENCES shift_reports(report_id),
        position INTEGER NOT NULL,
        artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
        PRIMARY KEY (report_id, position),
        UNIQUE (report_id, artifact_id)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS report_exceptions (
        report_id TEXT NOT NULL,
        position INTEGER NOT NULL,
        artifact_id TEXT NOT NULL,
        side TEXT NOT NULL,
        threshold_um TEXT NOT NULL,
        PRIMARY KEY (report_id, position),
        FOREIGN KEY (report_id, artifact_id) REFERENCES report_items(report_id, artifact_id),
        FOREIGN KEY (artifact_id, side) REFERENCES artifact_halves(artifact_id, side)
    )''')


class HandoverStore:
    def __init__(self, db):
        self.db = db

    def find_report(self, report_id):
        header = self.db.execute('SELECT shift_id, total_um FROM shift_reports WHERE report_id = ?',
                                 (report_id,)).fetchone()
        if header is None:
            return None
        item_rows = self.db.execute('SELECT artifact_id FROM report_items WHERE report_id = ? ORDER BY position',
                                    (report_id,))
        repository = ReceiptRepository(self.db)
        artifacts = {}
        for (artifact_id,) in item_rows:
            artifact = repository.load_artifact(artifact_id)
            if artifact is None:
                raise sqlite3.IntegrityError('frozen report refers to a missing signed artifact')
            artifacts[artifact_id] = artifact
        exception_rows = self.db.execute('''SELECT artifact_id, side, threshold_um
            FROM report_exceptions WHERE report_id = ? ORDER BY position''', (report_id,))
        exceptions = []
        for artifact_id, side, threshold in exception_rows:
            measurement = artifacts[artifact_id].measurement
            half = next(half for half in measurement.halves if half.side == side)
            exceptions.append(LimitException(artifact_id, measurement.rule_id, int(threshold, 16),
                                             half.value_um, half.evidence))
        summary = ShiftSummary(tuple(artifacts.values()), int(header[1], 16))
        return ShiftReport(report_id, header[0], summary, tuple(exceptions))

    def save_report(self, report):
        with atomic(self.db):
            self.db.execute('INSERT INTO shift_reports VALUES (?, ?, ?)',
                            (report.report_id, report.shift_id, hex(report.summary.total_um)))
            self.db.executemany('INSERT INTO report_items VALUES (?, ?, ?)',
                                ((report.report_id, position, artifact.receipt.artifact_id)
                                 for position, artifact in enumerate(report.summary.artifacts)))
            self.db.executemany('INSERT INTO report_exceptions VALUES (?, ?, ?, ?, ?)',
                                ((report.report_id, position, entry.artifact_id, entry.evidence.side,
                                  hex(entry.threshold_um)) for position, entry in enumerate(report.exceptions)))
        return report
