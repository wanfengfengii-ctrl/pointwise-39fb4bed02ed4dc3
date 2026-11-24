"""Replay same-page trials using the station API and the existing shift recording."""
import argparse
from contextlib import closing, contextmanager
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import check_project
from capture.readout import Readout
from capture.requests import collect
from device.pages import PageBank
from device.recorder import Recorder
from device.storage import DeviceStore
from handover.history import read_artifact
from handover.register import close_shift
from intake.acceptance import Acceptance
from intake.repository import PublishError, ReceiptRepository
from tests.fixtures import allow_receipt_commit, reject_receipt_commit, signed_counts
from work.bootstrap import StationConfig, initialize_station
from work.database import open_database

SAMPLE = json.loads(Path(__file__).with_name("samples").joinpath("shift.json").read_text(encoding="utf-8"))
LOT, SHIFT = SAMPLE["lot_id"], SAMPLE["shift_id"]
OLD, NEW, NEIGHBOR = (SAMPLE[name] for name in ("old", "new", "neighbor"))
TRIGGER = OLD["trigger"]
OLD_VALUES, NEW_VALUES = ((item["left"], item["right"]) for item in (OLD, NEW))


@contextmanager
def station(path):
    with closing(open_database(path)) as db:
        initialize_station(db, StationConfig(SHIFT, LOT, tuple(SAMPLE["triggers"]), SAMPLE["threshold_um"]))
        pages = PageBank(DeviceStore(db))
        recorder = Recorder(db, pages)
        old = recorder.record_frame(TRIGGER, OLD["exposure"], *OLD_VALUES)
        neighbor = recorder.record_frame(NEIGHBOR["trigger"], NEIGHBOR["exposure"], NEIGHBOR["left"], NEIGHBOR["right"])
        assert neighbor.page != old.page
        yield db, pages, recorder, Acceptance(ReceiptRepository(db)), old


def raises(error, operation, *args):
    try:
        operation(*args)
    except error:
        return
    raise AssertionError(f"{operation.__name__} did not raise {error.__name__}")


def take(acceptance, pages, request_id, reader=None):
    return collect(acceptance, pages, request_id, LOT, TRIGGER, readout=reader)


def expect(result, source, values):
    assert result.status == "signed", result
    artifact = result.artifact
    expected = tuple((side, value, source, side) for side, value in zip(("left", "right"), values, strict=True))
    assert tuple((half.side, half.value_um, half.evidence.exposure, half.evidence.side)
                 for half in artifact.measurement.halves) == expected, artifact
    assert artifact.measurement.total_um == sum(values)
    assert artifact.receipt.receipt_id and artifact.receipt.request_id == artifact.measurement.request_id
    return artifact


def begin(pages, old, name, label=NEW["exposure"]):
    trial = pages.begin_trial(name, old.write_id, label)
    assert (trial.page, trial.lot_id, trial.trigger_id, trial.exposure_id) == (old.page, old.lot_id, old.trigger_id, label)
    assert trial.write_id != old.write_id
    return trial


def ready(pages, old, name, values=NEW_VALUES):
    trial = begin(pages, old, name)
    for side, value in zip(("left", "right"), values, strict=True):
        pages.write_half(trial, side, value)
    pages.finish_write(trial)
    return trial


def pending_and_abandoned(path):
    with station(path) as (_, pages, recorder, acceptance, old):
        trial = begin(pages, old, "draft")
        pages.write_half(trial, "left", NEW_VALUES[0])
        assert pages.read_half(old.page, "left") == NEW_VALUES[0]
        expect(take(acceptance, pages, "during-write"), old, OLD_VALUES)
        raises(ValueError, pages.confirm_trial, "draft")
        raises(ValueError, pages.begin_trial, "second", old.write_id, "second-frame")
        neighbor = recorder.record_frame(NEIGHBOR["trigger"], "continue-neighbor", 3, 5)
        assert neighbor.page != old.page
        pages.write_half(trial, "right", NEW_VALUES[1])
        pages.finish_write(trial)
        expect(take(acceptance, pages, "awaiting-decision"), old, OLD_VALUES)
        assert pages.abandon_trial("draft") == trial
        assert pages.abandon_trial("draft") == trial
        assert begin(pages, old, "draft") == trial
        raises(ValueError, pages.begin_trial, "draft", old.write_id, "different-frame")
        raises(ValueError, pages.confirm_trial, "draft")
        expect(take(acceptance, pages, "after-abandon"), old, OLD_VALUES)
        assert begin(pages, old, "next").write_id != trial.write_id


def before_first_read(path):
    with station(path) as (_, pages, _, acceptance, old):
        acceptance.begin_request("queued", LOT, TRIGGER)
        trial = ready(pages, old, "equal", OLD_VALUES)
        assert pages.confirm_trial("equal") == trial
        expect(take(acceptance, pages, "queued", Readout(pages, order=("right", "left"))), trial, OLD_VALUES)


def cross_confirmation(path, fail_commit=False, start_after_first=False):
    queued, fresh, replacements = [], [], []
    with station(path) as (db, pages, recorder, acceptance, old):
        trial = None if start_after_first else ready(pages, old, "trial")

        def advance(reading):
            nonlocal trial
            if reading.side != "left":
                return
            assert reading.evidence.exposure == old and reading.value_um == OLD_VALUES[0]
            if trial is None:
                trial = ready(pages, old, "trial")
            assert pages.confirm_trial("trial") == trial
            fresh.append(expect(take(acceptance, pages, "new-reader"), trial, NEW_VALUES))
            for step in range(2):
                later = recorder.record_frame(TRIGGER, f"later-{step}", 23, 29)
                if later.page == old.page:
                    replacements.append(later)
                    break
            else:
                raise AssertionError("the original physical page could not be reused")
            assert pages.confirm_trial("trial") == trial
            raises(ValueError, pages.abandon_trial, "trial")
            if fail_commit:
                reject_receipt_commit(db)

        def dispatch(reading, deliver):
            if reading.side == "right":
                queued.append((reading, deliver))
            else:
                deliver(reading)

        reader = Readout(pages, after_copy=advance, dispatch=dispatch)
        pending = take(acceptance, pages, "in-flight", reader)
        assert pending.status == "pending" and pending.artifact is None
        ((right, deliver),) = queued
        assert right.evidence.exposure == old and right.value_um == OLD_VALUES[1]
        (new_artifact,) = fresh
        if fail_commit:
            raises(PublishError, deliver, right)
            with closing(open_database(path)) as observer:
                assert signed_counts(observer) == (1, 2, 1)
                assert ReceiptRepository(observer).lookup_request("in-flight").artifact_id is None
            allow_receipt_commit(db)
            expect(take(acceptance, pages, "in-flight"), replacements[0], (23, 29))
            return None
        deliver(right)
        old_artifact = expect(take(acceptance, None, "in-flight"), old, OLD_VALUES)
        report = close_shift(db, SHIFT, "trial-handover")
        assert report.summary.artifacts == (old_artifact, new_artifact)
        assert report.summary.total_um == 50

    result = []
    with closing(open_database(path)) as offline:
        assert read_artifact(offline, "trial-handover") == report
        for artifact in (old_artifact, new_artifact):
            saved = read_artifact(offline, artifact.receipt.artifact_id)
            assert saved == artifact
            assert take(Acceptance(ReceiptRepository(offline)), None, artifact.receipt.request_id).artifact == saved
            measurement = saved.measurement
            source = measurement.halves[0].evidence.exposure
            result.append({"request": measurement.request_id, "values_um": [half.value_um for half in measurement.halves],
                           "total_um": measurement.total_um, "write_id": source.write_id, "page": source.page,
                           "receipt_id": saved.receipt.receipt_id})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("verify", "demo"))
    mode = parser.parse_args().mode
    if mode == "verify" and check_project.main():
        return 1
    with TemporaryDirectory() as directory:
        folder = Path(directory)
        results = cross_confirmation(folder / "cross.sqlite")
        if mode == "verify":
            pending_and_abandoned(folder / "discard.sqlite")
            before_first_read(folder / "unstarted.sqlite")
            cross_confirmation(folder / "rollback.sqlite", fail_commit=True)
            cross_confirmation(folder / "start-in-flight.sqlite", start_after_first=True)
    print("POINTWISE_SCENARIO " + json.dumps({"case_id": "same-page-trial", "signed": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
