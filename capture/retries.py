from enum import StrEnum

from capture.attempts import Attempt


class RetryDisposition(StrEnum):
    OFFLINE = 'offline'
    NO_COMPLETE_FRAME = 'no_complete_frame'
    UNQUALIFIED = 'unqualified'
    AWAITING_READBACK = 'awaiting_readback'
    PUBLISH_FAILED = 'publish_failed'
    SUPERSEDED = 'superseded'


def decide_retry(attempt: Attempt | None, *, publish_failed=False, offline=False):
    if publish_failed:
        return RetryDisposition.PUBLISH_FAILED
    if offline:
        return RetryDisposition.OFFLINE
    if attempt is None:
        return RetryDisposition.SUPERSEDED
    decision = attempt.decision
    if decision is not None and decision.exposure is None:
        if decision.reason == 'no_complete_frame':
            return RetryDisposition.NO_COMPLETE_FRAME
        return RetryDisposition.UNQUALIFIED
    return RetryDisposition.AWAITING_READBACK
