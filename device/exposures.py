from dataclasses import dataclass


SIDES = ('left', 'right')


@dataclass(frozen=True)
class Exposure:
    write_id: int
    page: int
    lot_id: str
    trigger_id: str
    exposure_id: str

    @property
    def source_key(self):
        return self.write_id, self.page


@dataclass(frozen=True)
class WriteEvent:
    event_id: int
    phase: str
    exposure: Exposure


def validate_raw_read(value):
    if type(value) is not int:
        raise ValueError('a raw reading must be an integer in micrometers')
    return value


def require_side(side):
    if side not in SIDES:
        raise ValueError(f'unknown half {side!r}')
    return side
