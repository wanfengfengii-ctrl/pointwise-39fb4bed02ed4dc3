from dataclasses import dataclass

from intake.repository import SignedArtifact


@dataclass(frozen=True)
class ShiftSummary:
    artifacts: tuple[SignedArtifact, ...]
    total_um: int


def summarize_shift(artifacts):
    ordered = tuple(sorted(artifacts, key=lambda artifact: artifact.measurement.exposure.write_id))
    return ShiftSummary(ordered, sum(artifact.measurement.total_um for artifact in ordered))
