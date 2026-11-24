from dataclasses import dataclass

from capture.attempts import create_attempt_schema
from device.storage import create_device_schema
from handover.store import create_handover_schema
from intake.repository import create_intake_schema
from work.database import atomic
from work.limits import create_limit_schema
from work.lots import create_lot_schema, open_lot
from work.shifts import create_shift_schema
from work.triggers import create_trigger_schema


@dataclass(frozen=True)
class StationConfig:
    shift_id: str
    lot_id: str
    triggers: tuple[str, ...]
    threshold_um: int


def initialize_station(db, config=None):
    with atomic(db):
        create_shift_schema(db)
        create_limit_schema(db)
        create_lot_schema(db)
        create_trigger_schema(db)
        create_device_schema(db)
        create_intake_schema(db)
        create_attempt_schema(db)
        create_handover_schema(db)
        if config is not None:
            return open_lot(db, config.shift_id, config.lot_id, config.triggers, config.threshold_um)
    return None
