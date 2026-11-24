from handover.exceptions import find_limit_exceptions
from handover.store import HandoverStore, ShiftReport
from handover.summary import summarize_shift
from intake.receipts import ARTIFACT_PREFIX
from intake.repository import ReceiptRepository
from work.database import atomic
from work.shifts import require_id, require_shift


def close_shift(db, shift_id, report_id):
    require_id(report_id, 'report_id')
    if report_id.startswith(ARTIFACT_PREFIX):
        raise ValueError('report ID uses the measurement namespace')
    with atomic(db):
        shift = require_shift(db, shift_id)
        store = HandoverStore(db)
        existing = store.find_report(report_id)
        if existing is not None:
            if existing.shift_id != shift.shift_id:
                raise ValueError(f'report {report_id!r} already belongs to another shift')
            return existing
        summary = summarize_shift(ReceiptRepository(db).list_committed(shift.shift_id))
        exceptions = find_limit_exceptions(db, summary.artifacts)
        return store.save_report(ShiftReport(report_id, shift.shift_id, summary, exceptions))
