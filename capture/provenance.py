from dataclasses import dataclass

from device.exposures import Exposure, require_side, validate_raw_read


@dataclass(frozen=True)
class HalfEvidence:
    exposure: Exposure
    side: str


@dataclass(frozen=True)
class HalfReading:
    request_id: str
    attempt_id: int
    value_um: int
    evidence: HalfEvidence

    @property
    def side(self):
        return self.evidence.side


def make_half_reading(request_id, attempt_id, exposure, side, value_um):
    evidence = HalfEvidence(exposure, require_side(side))
    return HalfReading(request_id, attempt_id, validate_raw_read(value_um), evidence)
