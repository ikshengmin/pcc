"""Independent codegen ordering never weakens byte admission or task identity."""

import copy
import subprocess

import pytest

from pcc.frontends.python import pipeline_frontend_workers as workers
from pcc.frontends.python import worker_process_pool as pool
from pcc.frontends.python import worker_resource_plan as policy

MIB = 1024 ** 2


def task(size, index=0, export=10 ** 8):
    return {'class': 'same-private-invocation', 'inputs': [size, size, size, size, export, 1],
            'estimate_bytes': 0, 'report_path': 'unused', 'restartable': True,
            'diagnostic_phase': 'codegen', 'diagnostic_indices': [index],
            'diagnostic_modules': ['module' + str(index)]}


def test_bands_small_first_largest_within_band_exactly_once():
    tasks = [task(n, i) for i, n in enumerate([16, 3, 2, 8, 3, 0])]
    before = copy.deepcopy(tasks)
    order, bands = policy.resource_task_order(tasks)
    assert order == [5, 1, 4, 2, 3, 0]
    assert bands == [6, 3, 3, 5, 3, 0]
    assert sorted(order) == list(range(len(tasks)))
    assert tasks == before
    assert policy.resource_task_order(tasks) == (order, bands)
    # Full-export size must not collapse every singleton into one band.
    for item in tasks:
        item['inputs'][4] = 10 ** 12
    assert policy.resource_task_order(tasks) == (order, bands)


@pytest.mark.parametrize('change', ['phase', 'class', 'batch', 'schema', 'dependency', 'lazy', 'negative', 'nonsingleton', 'export-view'])
def test_unknown_inventory_preserves_old_order_and_readiness(change):
    tasks = [task(8), task(2)]
    if change == 'phase': tasks[0]['diagnostic_phase'] = 'export'
    elif change == 'class': tasks[0]['class'] = 'other'
    elif change == 'batch': tasks[0]['diagnostic_indices'] = [0, 1]
    elif change == 'schema': tasks[0]['inputs'] = [8]
    elif change == 'dependency': tasks[0]['depends_on'] = 1
    elif change == 'lazy': tasks[0]['input_path'] = 'future-input'
    elif change == 'negative': tasks[0]['inputs'][0] = -1
    elif change == 'export-view': tasks[0]['inputs'][4] = 17
    else: tasks[0]['inputs'][5] = 2
    expected = sorted(range(len(tasks)), key=lambda i: (-sum(tasks[i]['inputs']), i))
    order, bands = policy.resource_task_order(tasks)
    assert order == expected and bands == []
    ready = [1, 0]
    assert policy.ready_resource_cohort(ready, order, [], bands) is ready


def test_active_cohort_drains_before_next_cohort_readiness():
    tasks = [task(2), task(3), task(16)]
    order, bands = policy.resource_task_order(tasks)
    assert policy.ready_resource_cohort([2], [2], [0, 1], bands) == []
    assert policy.ready_resource_cohort([2], [2], [1], bands) == []
    assert policy.ready_resource_cohort([2], [2], [], bands) == [2]


def test_oversized_forecast_cannot_skip_lower_band_or_lower_maximum():
    tasks = [task(2), task(16)]
    order, bands = policy.resource_task_order(tasks)
    observations = [(tasks[0]['class'], list(tasks[0]['inputs']), 300 * MIB)]
    available = 256 * MIB
    # The baseline chooser prefers the unknown higher band over forecast-only.
    assert policy.choose_task(order, tasks, observations, [], 2, available, True)[0] == 1
    ready = policy.ready_resource_cohort(order, order, [], bands)
    index, demand, exclusive = policy.choose_task(ready, tasks, observations, [], 2, available, True)
    assert index == 0 and exclusive
    assert demand == policy.peak_reservation(300 * MIB) > available
    # No reduction to MAX observations or explicit/incomplete floors.
    observations.append((tasks[0]['class'], list(tasks[0]['inputs']), 10 * MIB))
    assert policy.estimated_task_bytes(tasks[0], observations) == demand
    tasks[0]['incomplete_peak_bytes'] = 400 * MIB
    assert policy.minimum_task_bytes(tasks[0]) == 400 * MIB
    assert policy.estimated_task_bytes(tasks[0], observations) == policy.peak_reservation(400 * MIB)


def mock_pool(tmp_path, monkeypatch, failure=False, growth=False):
    tasks = [task(n, i) for i, n in enumerate([70, 50, 40, 12, 10, 9, 300])]
    for i, item in enumerate(tasks): item['report_path'] = str(tmp_path / ('rss' + str(i)))
    monkeypatch.delenv(policy.TREE_STATE_ENV, raising=False)
    monkeypatch.delenv('PCC_PY_FRONTEND_WORKER_TIMING', raising=False)
    clock = [0.0]
    live = {}
    records = []
    attempts = {}
    stopped = []
    monkeypatch.setattr(pool.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(pool.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(workers, '_coordinator_rss_bytes', lambda: MIB)

    def start(specs, index):
        assert specs[index][0] == ['command' + str(index)]
        attempts[index] = attempts.get(index, 0) + 1
        pid = 100 + len(records)
        live[pid] = [index, clock[0] + (0.08 if index == 4 else 0.04)]
        records.append(('start', index, clock[0], pid))
        return pid

    def peak(index):
        return (170 if growth and index == 4 else 16) * MIB

    def report(path, pid, token):
        index, end = live[pid]
        # Grow after the same-band peer has been launched.
        value = peak(index) if index != 4 or clock[0] >= end - 0.04 else 16 * MIB
        return ('complete' if clock[0] >= end else 'live', value, value)

    def poll(pid):
        index, end = live[pid]
        return (1 if failure and index == 4 else 0) if clock[0] >= end else pool._WORKER_RUNNING

    def retire(pid):
        records.append(('retire', live[pid][0], clock[0], pid))
        # Keep report identity readable for the required post-success read.

    def stop(pid):
        stopped.append(pid)
        records.append(('stop', live[pid][0], clock[0], pid))

    monkeypatch.setattr(pool, '_start_resource_worker', start)
    monkeypatch.setattr(pool, '_poll_resource_worker', poll)
    monkeypatch.setattr(pool, '_retire_resource_worker', retire)
    monkeypatch.setattr(pool, '_stop_resource_worker', stop)
    monkeypatch.setattr(policy, 'read_worker_resource', report)
    commands = ['command' + str(i) for i in range(len(tasks))]
    observations = []
    budget = MIB + policy.RSS_HEADROOM_BYTES + 400 * MIB
    error = None
    try:
        pool.run_resource_worker_processes(commands, tasks, 2, budget, observations=observations)
    except subprocess.CalledProcessError as exc:
        error = exc
    return tasks, records, attempts, observations, error, stopped


def test_mock_pool_preserves_command_result_identity_and_drains(tmp_path, monkeypatch):
    tasks, records, attempts, observations, error, stopped = mock_pool(tmp_path, monkeypatch)
    assert error is None and not stopped
    assert attempts == {i: 1 for i in range(len(tasks))}
    assert len(observations) == len(tasks)
    bands = policy.resource_task_order(tasks)[1]
    starts = [r for r in records if r[0] == 'start']
    assert [r[1] for r in starts] == [3, 4, 5, 1, 2, 0, 6]
    for event, index, when, pid in starts:
        prior_band_finishes = [r[2] for r in records if r[0] == 'retire' and bands[r[1]] < bands[index]]
        assert not prior_band_finishes or max(prior_band_finishes) <= when
    assert [t['diagnostic_indices'] for t in tasks] == [[i] for i in range(len(tasks))]


def test_mock_pool_failure_does_not_advance_larger_band(tmp_path, monkeypatch):
    tasks, records, attempts, observations, error, stopped = mock_pool(tmp_path, monkeypatch, failure=True)
    assert error is not None and error.cmd == 'command4'
    assert set(attempts) == {3, 4, 5}
    assert len(observations) < len(tasks)


def test_mock_peak_growth_retries_same_band_before_advancing(tmp_path, monkeypatch):
    tasks, records, attempts, observations, error, stopped = mock_pool(tmp_path, monkeypatch, growth=True)
    assert error is None
    assert attempts[5] == 2
    assert all(count == 1 for index, count in attempts.items() if index != 5)
    assert len(stopped) == 1 and len(observations) == len(tasks)
    retry_start = [r for r in records if r[0] == 'start' and r[1] == 5][-1]
    grown_retire = next(r for r in records if r[0] == 'retire' and r[1] == 4)
    retry_retire = [r for r in records if r[0] == 'retire' and r[1] == 5][-1]
    next_band = next(r for r in records if r[0] == 'start' and r[1] == 1)
    assert grown_retire[2] <= retry_start[2] < retry_retire[2] <= next_band[2]
    assert tasks[5]['incomplete_peak_bytes'] == 16 * MIB


def test_small_real_children_keep_cohort_barrier_and_original_outputs(tmp_path, monkeypatch):
    import shlex
    import sys

    monkeypatch.delenv(policy.TREE_STATE_ENV, raising=False)
    tasks = [task(n, i) for i, n in enumerate([70, 12, 10, 9])]
    commands = []
    for index, item in enumerate(tasks):
        item['report_path'] = str(tmp_path / ('rss' + str(index)))
        output = tmp_path / ('original-output' + str(index))
        code = (
            'import time;from pathlib import Path;'
            'from pcc.frontends.python.worker_resource_plan import publish_worker_resource as report;'
            'payload=bytearray(4*1024**2);report("allocated");time.sleep(0.15);'
            + 'Path(' + repr(str(output)) + ').write_text(' + repr(str(index)) + ');report("complete")'
        )
        commands.append(shlex.join([sys.executable, '-B', '-c', code]))
    trace = tmp_path / 'trace'
    observations = []
    pool.run_resource_worker_processes(
        commands, tasks, 2,
        workers._coordinator_rss_bytes() + policy.RSS_HEADROOM_BYTES + 512 * MIB,
        observations=observations, trace_path=str(trace),
    )
    rows = [line.split('\t') for line in trace.read_text().splitlines()]
    launches = [int(row[1]) for row in rows if row[0] in ('start', 'calibrate')]
    assert launches == [1, 2, 3, 0]
    later = next(i for i, row in enumerate(rows) if row[0] in ('start', 'calibrate') and row[1] == '0')
    assert all(next(i for i, row in enumerate(rows) if row[:2] == ['retire', str(index)]) < later for index in (1, 2, 3))
    assert all((tmp_path / ('original-output' + str(index))).read_text() == str(index) for index in range(4))
    assert len(observations) == 4 and pool._HOST_WORKERS == {}
