from handover.store import HandoverStore
from intake.receipts import ARTIFACT_PREFIX
from intake.repository import ReceiptRepository
from work.shifts import require_id


def read_artifact(db, identifier):
    require_id(identifier, 'artifact identifier')
    if identifier.startswith(ARTIFACT_PREFIX):
        return ReceiptRepository(db).load_artifact(identifier)
    return HandoverStore(db).find_report(identifier)
