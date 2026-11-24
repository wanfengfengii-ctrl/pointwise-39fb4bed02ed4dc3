from dataclasses import dataclass
from threading import RLock

from device.exposures import Exposure
from device.loans import LoanBook
from device.storage import DeviceStore


@dataclass(frozen=True)
class PageStamp:
    page: int
    active: Exposure | None
    completed: Exposure | None

    @property
    def complete(self):
        return self.completed if self.active is None else None


class PageBusy(RuntimeError):
    pass


class PageBank:
    def __init__(self, store: DeviceStore):
        self.store = store
        self._lock = RLock()
        self._loans = LoanBook()

    def _stamps(self):
        return tuple(PageStamp(*row) for row in self.store.load_pages())

    def find_complete_page(self, lot_id, trigger_id):
        with self._lock:
            candidates = [stamp for stamp in self._stamps()
                          if stamp.complete is not None
                          and (stamp.complete.lot_id, stamp.complete.trigger_id) == (lot_id, trigger_id)]
            return max(candidates, key=lambda stamp: stamp.complete.write_id, default=None)

    def observe(self, page):
        if type(page) is not int or page not in (0, 1):
            raise ValueError(f'unknown physical page {page!r}')
        with self._lock:
            return self._stamps()[page]

    def read_half(self, page, side, loan=None):
        with self._lock:
            if loan is not None and (loan.exposure.page != page or not self._loans.contains(loan)):
                raise ValueError('short read loan is inactive or belongs to another page')
            return self.store.load_half(page, side)

    def start_write(self, lot_id, trigger_id, exposure_id):
        with self._lock:
            recent = self.store.latest_page()
            preferred = 0 if recent is None else 1 - recent
            stamps = self._stamps()
            for page in (preferred, 1 - preferred):
                if stamps[page].active is None and not self._loans.is_pinned(page):
                    return self.store.begin_write(page, lot_id, trigger_id, exposure_id)
            raise PageBusy('both recording pages are writing or pinned')

    def write_half(self, exposure, side, value):
        with self._lock:
            if self.observe(exposure.page).active != exposure:
                raise ValueError('half does not belong to the active exposure')
            self.store.put_half(exposure, side, value)

    def finish_write(self, exposure):
        with self._lock:
            if self.observe(exposure.page).active != exposure:
                raise ValueError('completion does not belong to the active exposure')
            self.store.complete_write(exposure)

    def abandon_write(self, exposure):
        with self._lock:
            if self.observe(exposure.page).active != exposure:
                raise ValueError('abandonment does not belong to the active exposure')
            self.store.abandon_write(exposure)

    def acquire_loan(self, page, exposure):
        with self._lock:
            if exposure is None or self.observe(page).complete != exposure:
                return None
            return self._loans.acquire(exposure)

    def release_loan(self, loan):
        with self._lock:
            return self._loans.release(loan)
