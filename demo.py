from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3
import sys

from capture.readout import Readout
from capture.requests import collect
from capture.retries import RetryDisposition
from device.pages import PageBank
from device.recorder import Recorder, record_frame
from device.storage import DeviceStore
from handover.history import read_artifact
from handover.register import close_shift
from intake.acceptance import Acceptance
from intake.repository import ReceiptRepository
from work.bootstrap import StationConfig, initialize_station
from work.database import open_database


class BusinessMismatch(RuntimeError):
    pass


def need(condition, marker):
    if not condition:
        raise BusinessMismatch(marker)


def record(recorder, sample):
    return record_frame(recorder, sample['trigger'], sample['exposure'], sample['left'], sample['right'])


def view(artifact):
    measurement = artifact.measurement
    return {'values_um': [half.value_um for half in measurement.halves],
            'total_um': measurement.total_um,
            'sources': [[half.evidence.exposure.exposure_id, half.evidence.side] for half in measurement.halves]}


def run_demo(path, sample):
    lot_id = sample['lot_id']
    trigger = sample['old']['trigger']
    shift = sample['shift_id']
    expected = {'values_um': [13, 19], 'total_um': 32,
                'sources': [['F8', 'left'], ['F8', 'right']]}
    with closing(open_database(path)) as db:
        initialize_station(db, StationConfig(shift, lot_id, tuple(sample['triggers']), sample['threshold_um']))
        pages = PageBank(DeviceStore(db))
        recorder = Recorder(db, pages)
        repository = ReceiptRepository(db)
        acceptance = Acceptance(repository)
        old = record(recorder, sample['old'])
        neighbor = record(recorder, sample['neighbor'])
        need(old.page != neighbor.page, 'v4_missing_second_page')
        replacement = []

        def after_copy(reading):
            if reading.side == 'left':
                need(reading.value_um == 7, 'v4_unexpected_initial_left')
                replacement.append(record(recorder, sample['new']))

        first = collect(acceptance, pages, 'R1', lot_id, trigger, readout=Readout(pages, after_copy=after_copy))
        need(len(replacement) == 1, 'v4_recording_not_advanced')
        (new,) = replacement
        need(pages.observe(new.page).complete == new, 'v4_replacement_not_complete')
        if first.artifact is not None and view(first.artifact)['values_um'] == [7, 19]:
            raise BusinessMismatch('v4_torn_frame_26um')
        if first.reason == RetryDisposition.PUBLISH_FAILED:
            raise RuntimeError('station could not commit initial publication')
        if new.page == old.page:
            need(first.status == 'pending' and first.artifact is None, 'v4_reused_page_signed')
            need(not repository.list_committed(shift), 'v4_pending_became_visible')
            request = 'R1'
            expected_total = 32
            expected_sides = ['left', 'right']
        else:
            expected_old = {'values_um': [7, 11], 'total_um': 18, 'sources': [['F7', 'left'], ['F7', 'right']]}
            need(first.artifact is not None and view(first.artifact) == expected_old, 'v4_pinned_frame_mismatch')
            request = 'R2'
            expected_total = 50
            expected_sides = ['right', 'left', 'right']
        stable = collect(acceptance, pages, request, lot_id, trigger)
        if stable.reason == RetryDisposition.PUBLISH_FAILED:
            raise RuntimeError('station could not commit stable publication')
        artifact = stable.artifact
        need(stable.status == 'signed' and artifact is not None, 'v4_stable_retry_pending')
        actual = view(artifact)
        need(actual == expected, 'v4_stable_retry_mismatch')
        need(all(half.evidence.exposure == new for half in artifact.measurement.halves), 'v4_stable_source_mismatch')
        need(collect(acceptance, pages, request, lot_id, trigger).artifact == artifact, 'v4_receipt_replay_mismatch')
        report = close_shift(db, shift, 'night')
        expected_artifacts = (artifact,) if new.page == old.page else (first.artifact, artifact)
        need(report.summary.artifacts == expected_artifacts, 'v7_shift_item_mismatch')
        expected_downstream = {'shift_total_um': expected_total, 'limit_sides': expected_sides,
                               'signed_items': len(expected_artifacts), 'offline_equal': True}

    with closing(open_database(path)) as db:
        offline = read_artifact(db, artifact.receipt.artifact_id)
        offline_report = read_artifact(db, 'night')
        offline_replay = collect(Acceptance(ReceiptRepository(db)), None, request, lot_id, trigger)
        downstream = {'shift_total_um': offline_report.summary.total_um if offline_report else None,
                      'limit_sides': [entry.evidence.side for entry in offline_report.exceptions] if offline_report else [],
                      'signed_items': len(offline_report.summary.artifacts) if offline_report else 0,
                      'offline_equal': offline == artifact and offline_report == report and offline_replay.artifact == artifact}
        need(downstream == expected_downstream, 'v7_offline_handover_mismatch')

    return {'case_id': 'v4',
            'input': {'F7': [sample['old']['left'], sample['old']['right']],
                      'F8': [sample['new']['left'], sample['new']['right']],
                      'threshold_um': sample['threshold_um'], 'same_physical_page': new.page == old.page},
            'expected': expected, 'actual': actual,
            'downstream_expected': expected_downstream, 'downstream_actual': downstream,
            'code_paths': ['device/pages.py', 'capture/readout.py', 'intake/acceptance.py',
                           'intake/repository.py', 'handover/register.py', 'handover/history.py'],
            'oracle': 'Add the complete F8 halves 13 and 19. Each exceeds 10. If F7 was protected on its own page, add its 18 and count its right half only.'}


def main():
    try:
        source = Path(__file__).resolve().parent / 'samples' / 'shift.json'
        sample = json.loads(source.read_text(encoding='utf-8'))
        with TemporaryDirectory() as folder:
            evidence = run_demo(Path(folder) / 'station.sqlite', sample)
    except BusinessMismatch as error:
        print(f'POINTWISE_BUSINESS_FAILURE {error}', file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error, RuntimeError) as error:
        print(f'POINTWISE_RUNTIME_ERROR {error}', file=sys.stderr)
        return 2
    print('POINTWISE_SCENARIO ' + json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
