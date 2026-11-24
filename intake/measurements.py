from dataclasses import dataclass

from capture.provenance import HalfEvidence, HalfReading
from device.exposures import SIDES, validate_raw_read


@dataclass(frozen=True)
class MeasurementHalf:
    side: str
    value_um: int
    evidence: HalfEvidence


@dataclass(frozen=True)
class Measurement:
    request_id: str
    lot_id: str
    trigger_id: str
    rule_id: str
    halves: tuple[MeasurementHalf, MeasurementHalf]
    total_um: int

    @property
    def exposure(self):
        return self.halves[0].evidence.exposure


def assemble_measurement(request_id, lot_id, trigger_id, rule_id, readings):
    by_side: dict[str, HalfReading] = {reading.side: reading for reading in readings}
    if len(readings) != 2 or set(by_side) != set(SIDES):
        raise ValueError('a signed measurement requires left and right')
    halves = tuple(MeasurementHalf(side, validate_raw_read(by_side[side].value_um), by_side[side].evidence)
                   for side in SIDES)
    return Measurement(request_id, lot_id, trigger_id, rule_id, halves,
                       sum(half.value_um for half in halves))
