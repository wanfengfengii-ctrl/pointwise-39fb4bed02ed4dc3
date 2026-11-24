from dataclasses import dataclass

from capture.attempts import new_attempt
from capture.readout import Readout
from capture.retries import RetryDisposition, decide_retry
from intake.acceptance import Acceptance
from intake.repository import PublishError, SignedArtifact


@dataclass(frozen=True)
class CollectResult:
    status: str
    artifact: SignedArtifact | None = None
    reason: RetryDisposition | None = None


def collect(acceptance: Acceptance, pages, request_id, lot_id, trigger_id, readout=None):
    binding = acceptance.begin_request(request_id, lot_id, trigger_id)
    repository = acceptance.repository
    if binding.artifact_id is not None:
        return CollectResult('signed', repository.load_artifact(binding.artifact_id))
    if pages is None:
        return CollectResult('pending', reason=decide_retry(None, offline=True))
    if readout is not None and readout.pages is not pages:
        raise ValueError('readout must use the supplied station pages')
    attempt = new_attempt(repository.db, request_id, lot_id, trigger_id)
    active = attempt is not None and acceptance.bind_attempt(binding, attempt)
    publish_failed = False
    if active:
        reader = Readout(pages) if readout is None else readout
        try:
            reader.run(attempt, acceptance.accept_reading, acceptance.accept_decision)
        except PublishError:
            publish_failed = True
    current = repository.lookup_request(request_id)
    if current.artifact_id is not None:
        return CollectResult('signed', repository.load_artifact(current.artifact_id))
    reason = decide_retry(attempt if active else None, publish_failed=publish_failed)
    return CollectResult('pending', reason=reason)
