from dataclasses import dataclass, field
from threading import RLock

from capture.attempts import Attempt, AttemptQualification
from capture.provenance import HalfReading
from device.exposures import SIDES
from intake.measurements import assemble_measurement
from intake.receipts import RequestBinding
from intake.repository import ReceiptRepository
from work.lots import load_lot


@dataclass
class AcceptanceDraft:
    binding: RequestBinding
    attempt_id: int
    halves: dict[str, HalfReading] = field(default_factory=dict)
    qualification: AttemptQualification | None = None


class Acceptance:
    def __init__(self, repository: ReceiptRepository):
        self.repository = repository
        self._drafts = {}
        self._lock = RLock()

    def begin_request(self, request_id, lot_id, trigger_id):
        return self.repository.bind_request(request_id, lot_id, trigger_id)

    def bind_attempt(self, binding: RequestBinding, attempt: Attempt):
        if (attempt.request_id, attempt.lot_id, attempt.trigger_id) != (
            binding.request_id, binding.lot_id, binding.trigger_id
        ):
            raise ValueError('attempt is outside the request binding')
        with self._lock:
            if not self.repository.activate_attempt(binding, attempt.attempt_id):
                return False
            self._drafts[binding.request_id] = AcceptanceDraft(binding, attempt.attempt_id)
            return True

    def _matching_draft(self, request_id, attempt_id):
        draft = self._drafts.get(request_id)
        if draft is not None and draft.attempt_id == attempt_id:
            return draft
        return None

    def accept_reading(self, reading: HalfReading):
        with self._lock:
            draft = self._matching_draft(reading.request_id, reading.attempt_id)
            if draft is None:
                return None
            old = draft.halves.get(reading.side)
            if reading.side not in SIDES or (old is not None and old != reading):
                del self._drafts[reading.request_id]
                return None
            draft.halves[reading.side] = reading
            return self._advance(draft)

    def accept_decision(self, decision: AttemptQualification):
        with self._lock:
            draft = self._matching_draft(decision.request_id, decision.attempt_id)
            if draft is None:
                return None
            if decision.exposure is None or (draft.qualification is not None and draft.qualification != decision):
                del self._drafts[decision.request_id]
                return None
            draft.qualification = decision
            return self._advance(draft)

    def _advance(self, draft):
        if draft.qualification is None or set(draft.halves) != set(SIDES):
            return None
        binding = draft.binding
        exposure = draft.qualification.exposure
        try:
            if (exposure.lot_id, exposure.trigger_id) != (binding.lot_id, binding.trigger_id):
                return None
            readings = tuple(draft.halves.values())
            if any(reading.evidence.exposure != exposure for reading in readings):
                return None
            lot = load_lot(self.repository.db, binding.lot_id)
            measurement = assemble_measurement(binding.request_id, binding.lot_id, binding.trigger_id,
                                               lot.rule_id, readings)
            return self.repository.publish(binding, draft.attempt_id, measurement)
        finally:
            del self._drafts[binding.request_id]
