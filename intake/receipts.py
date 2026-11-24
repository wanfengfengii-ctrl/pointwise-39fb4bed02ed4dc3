from dataclasses import dataclass


ARTIFACT_PREFIX = 'measurement:'


@dataclass(frozen=True)
class Receipt:
    request_id: str
    artifact_id: str
    receipt_id: str


@dataclass(frozen=True)
class RequestBinding:
    request_id: str
    lot_id: str
    trigger_id: str
    artifact_id: str | None


def new_receipt(request_id):
    return Receipt(request_id, f'{ARTIFACT_PREFIX}{request_id}', f'receipt:{request_id}')


def restore_receipt(request_id, artifact_id, receipt_id):
    return Receipt(request_id, artifact_id, receipt_id)
