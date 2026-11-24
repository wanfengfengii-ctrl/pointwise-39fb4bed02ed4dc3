from dataclasses import dataclass

from capture.provenance import HalfEvidence
from work.limits import load_limit


@dataclass(frozen=True)
class LimitException:
    artifact_id: str
    rule_id: str
    threshold_um: int
    value_um: int
    evidence: HalfEvidence


def find_limit_exceptions(db, artifacts):
    rules = {}
    exceptions = []
    for artifact in artifacts:
        measurement = artifact.measurement
        rule_id = measurement.rule_id
        if rule_id not in rules:
            rules[rule_id] = load_limit(db, rule_id)
        threshold = rules[rule_id].threshold_um
        for half in measurement.halves:
            if half.value_um > threshold:
                exceptions.append(LimitException(artifact.receipt.artifact_id, rule_id, threshold,
                                                 half.value_um, half.evidence))
    return tuple(exceptions)
