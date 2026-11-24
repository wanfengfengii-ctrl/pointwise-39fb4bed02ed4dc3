from dataclasses import dataclass
from itertools import count

from device.exposures import Exposure


@dataclass(frozen=True, eq=False)
class PageLoan:
    token: int
    exposure: Exposure


class LoanBook:
    def __init__(self):
        self._ids = count(1)
        self._active = {}
        self._counts = {}

    def acquire(self, exposure):
        loan = PageLoan(next(self._ids), exposure)
        self._active[loan.token] = loan
        page = exposure.page
        self._counts[page] = self._counts.get(page, 0) + 1
        return loan

    def contains(self, loan):
        return self._active.get(loan.token) is loan

    def release(self, loan):
        if not self.contains(loan):
            return False
        del self._active[loan.token]
        page = loan.exposure.page
        self._counts[page] -= 1
        if self._counts[page] == 0:
            del self._counts[page]
        return True

    def is_pinned(self, page):
        return page in self._counts
