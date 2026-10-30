"""Execute tracking shard transitions and cold snapshots with owned artifacts.

GC4 already bypasses the default tracked-object table; its empty snapshots and
zero cached-node pool are checked separately, without claiming GC API parity.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from pcc.frontends.python.pipeline import compile_python
from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.process_timeout import run_process_group_timeout


REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests/fixtures/native/tracked_shard_transitions.c"


PROGRAM = '''import gc
import os
import threading
import weakref

backend = int(os.environ["PCC_GC_BACKEND"])
workers = int(os.environ["TRACKED_SHARD_WORKERS"])
events = []
saved = []
weak_refs = []
completed = []


def contains_identity(values, target):
    for value in values:
        if value is target:
            return True
    return False


def inspect(target, holder):
    objects = gc.get_objects()
    referrers = gc.get_referrers(target)
    if backend == 4:
        assert len(objects) == 0
        assert len(referrers) == 0
    else:
        assert contains_identity(objects, target)
        assert contains_identity(objects, holder)
        assert contains_identity(referrers, holder)
    assert not contains_identity(objects, objects)


class Survivor:
    def __init__(self, label):
        self.label = label
        self.loop = self

    def __del__(self):
        # GC0 runs this under its collector lock; snapshots must reenter
        # without losing the outer owner or prematurely resuming the world.
        objects = gc.get_objects()
        referrers = gc.get_referrers(self)
        if backend == 4:
            assert len(objects) == 0
            assert len(referrers) == 0
        else:
            assert isinstance(objects, list)
            assert isinstance(referrers, list)
        saved.append(self)
        events.append(self.label)


def worker(number):
    for index in range(8):
        target = []
        holder = [target]
        inspect(target, holder)
    token = Survivor(number)
    completed.append(number)


def main():
    gc.disable()
    threads = []
    for index in range(workers):
        thread = threading.Thread(target=worker, args=(index,))
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join()
    threads = []
    thread = None
    assert sorted(completed) == list(range(workers))
    gc.collect()
    assert sorted(events) == list(range(workers))
    assert len(saved) == workers
    assert sorted([value.label for value in saved]) == list(range(workers))
    # Cyclic collection may clear preexisting weakrefs before resurrection.
    # Observe final death with fresh weakrefs to the resurrected objects.
    for value in saved:
        weak_refs.append(weakref.ref(value))
    value = None
    saved.clear()
    gc.collect()
    assert sorted(events) == list(range(workers))
    for reference in weak_refs:
        assert reference() is None
    gc.enable()
    print("TRACKED_SHARDS_PY_OK", backend, workers)


main()
'''


def _execute_matrix(binary: Path, tmp_path: Path, prefix: str, expected):
    receipt = {"binary": str(binary),
               "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
               "runs": []}
    result_path = tmp_path / (prefix + ".json")
    for backend in range(5):
        for workers in (1, 4):
            environment = dict(os.environ, PCC_GC_BACKEND=str(backend),
                               PCC_THREADS="1", PCC_REFCOUNT_KIND="atomic",
                               TRACKED_SHARD_WORKERS=str(workers))
            arguments = [str(binary), str(workers)] if prefix == "native" else [str(binary)]
            result = run_process_group_timeout(arguments, env=environment, timeout=90)
            label = f"{prefix}-gc{backend}-workers{workers}"
            (tmp_path / (label + ".stdout")).write_text(result.stdout)
            (tmp_path / (label + ".stderr")).write_text(result.stderr)
            receipt["runs"].append({"backend": backend, "workers": workers,
                                    "returncode": result.returncode,
                                    "stdout": result.stdout, "stderr": result.stderr})
            result_path.write_text(json.dumps(receipt, indent=2) + "\n")
            assert result.returncode == 0, (label, result.stdout, result.stderr)
            assert result.stderr == "", (label, result.stderr)
            assert result.stdout == expected(backend, workers), (label, result.stdout)


@pytest.mark.integration
def test_native_tracking_shard_transitions(tmp_path, threaded_pcc_runtime_archive):
    binary = tmp_path / "tracked_shard_transitions"
    compile_owned_c_with_runtime(FIXTURE, binary, runtime_archive=threaded_pcc_runtime_archive)
    _execute_matrix(binary, tmp_path, "native", lambda backend, workers:
                    f"TRACKED_SHARDS_OK gc={backend} workers={workers} pool={0 if backend == 4 else 4096}\n")


@pytest.mark.integration
def test_python_snapshots_and_resurrection(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "tracked_snapshots.py"
    source.write_text(PROGRAM)
    binary = tmp_path / "tracked_snapshots"
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(threaded_pcc_runtime_archive))
    _execute_matrix(binary, tmp_path, "python", lambda backend, workers:
                    f"TRACKED_SHARDS_PY_OK {backend} {workers}\n")
