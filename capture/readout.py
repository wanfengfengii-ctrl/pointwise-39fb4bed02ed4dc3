from capture.attempts import Attempt
from capture.provenance import make_half_reading
from device.exposures import SIDES
from device.pages import PageBank


def _deliver_now(reading, deliver):
    deliver(reading)


class Readout:
    def __init__(self, pages: PageBank, *, order=SIDES, after_copy=None, dispatch=None):
        self.pages = pages
        self.order = tuple(order)
        if len(self.order) != 2 or set(self.order) != set(SIDES):
            raise ValueError('readout order must contain left and right exactly once')
        self.after_copy = after_copy
        self.dispatch = _deliver_now if dispatch is None else dispatch

    def _copy(self, attempt, exposure, side):
        value = self.pages.read_exposure_half(exposure, side)
        if value is None:
            return None
        return make_half_reading(attempt.request_id, attempt.attempt_id, exposure, side, value)

    def _read(self, attempt, deliver):
        before = self.pages.find_complete_page(attempt.lot_id, attempt.trigger_id)
        if before is None:
            return attempt.invalidate('no_complete_frame')
        exposure = before.complete
        for side in self.order:
            reading = self._copy(attempt, exposure, side)
            if reading is None:
                return attempt.invalidate('exposure_changed')
            attempt.record_read(reading)
            if self.after_copy is not None:
                self.after_copy(reading)
            self.dispatch(reading, deliver)
        if self.pages.observe(exposure.page).complete != exposure \
                and not self.pages.trial_superseded(exposure):
            return attempt.invalidate('exposure_changed')
        return attempt.qualify(exposure)

    def run(self, attempt: Attempt, on_reading, on_decision):
        try:
            decision = self._read(attempt, on_reading)
        except BaseException:
            if attempt.decision is None:
                on_decision(attempt.invalidate('readout_interrupted'))
            raise
        on_decision(decision)
        return decision
