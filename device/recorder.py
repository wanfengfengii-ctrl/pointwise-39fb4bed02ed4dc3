from device.exposures import SIDES, validate_raw_read
from device.pages import PageBank
from work.shifts import require_id
from work.triggers import find_trigger_owner


class Recorder:
    def __init__(self, db, pages: PageBank):
        self.db = db
        self.pages = pages

    def record_frame(self, trigger_id, exposure_id, left_um, right_um, on_step=None):
        owner = find_trigger_owner(self.db, trigger_id)
        if owner is None:
            raise ValueError(f'unknown trigger {trigger_id!r}')
        require_id(exposure_id, 'exposure_id')
        readings = tuple(validate_raw_read(value) for value in (left_um, right_um))
        exposure = self.pages.start_write(owner.lot_id, owner.trigger_id, exposure_id)
        if on_step is not None:
            on_step('start', exposure)
        for side, value in zip(SIDES, readings, strict=True):
            self.pages.write_half(exposure, side, value)
            if on_step is not None:
                on_step(side, exposure)
        self.pages.finish_write(exposure)
        if on_step is not None:
            on_step('complete', exposure)
        return exposure


def record_frame(recorder, trigger_id, exposure_id, left_um, right_um, on_step=None):
    return recorder.record_frame(trigger_id, exposure_id, left_um, right_um, on_step)
