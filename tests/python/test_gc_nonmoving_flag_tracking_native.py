"""Owned native origin/admission matrix for GC1/2 flag-only tracking.

Specification: docs/project-intent.md (five-GC lifetime/identity contract).
Host pcc0 owns C preprocessing, emission and linking against the admitted real
threaded runtime. This is not native-pcc1/bootstrap or public C-API GC parity.
GC4's empty tracking view remains a separately asserted existing limitation.

The default requires the optimized GC1/2 flag representation. An explicit
PCC_TEST_NONMOVING_TRACKING_MODE=nodes permits comparison with an older runtime;
the required-mode assertion rejects accidentally accepting the old node path.
Both modes require identical origin/count/snapshot/referrer behavior, and only
flag mode requires no persistent tracked GCNode. ObjectNodes remain required.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from pcc.runtime.py.py_abi_constants import (
    PYLISTOBJECT_CAPACITY_OFFSET,
    PYLISTOBJECT_ITEMS_OFFSET,
    PYLISTOBJECT_LENGTH_OFFSET,
    PY_FLAG_GC_DEALLOCATING,
    PY_FLAG_GC_TRACKED,
    PY_FLAG_IMMORTAL,
)
from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.process_timeout import run_process_group_timeout


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tests/fixtures/native/gc_nonmoving_flag_tracking.c"
pytestmark = [pytest.mark.integration, pytest.mark.xdist_group("gc-nonmoving-flag-tracking")]


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@pytest.fixture(scope="module")
def nonmoving_tracking_binary(tmp_path_factory, threaded_pcc_runtime_archive):
    archive = Path(threaded_pcc_runtime_archive)
    provenance = Path(str(archive) + ".provenance.json")
    manifest = json.loads(provenance.read_text())
    assert all(
        member["runtime_build_config"]["threads"] is True
        and member["runtime_build_config"]["refcount"] == "atomic"
        for member in manifest["members"]
    )
    mode = os.environ.get("PCC_TEST_NONMOVING_TRACKING_MODE", "flags")
    assert mode in ("nodes", "flags"), mode
    binary = tmp_path_factory.mktemp("gc_nonmoving_flag_tracking") / "tracking.out"
    inputs = (SOURCE, archive, provenance)
    before = tuple(_digest(path) for path in inputs)
    compile_owned_c_with_runtime(
        SOURCE, binary, runtime_archive=archive,
        cpp_args=(
            f"-DTEST_TRACKED={PY_FLAG_GC_TRACKED}",
            f"-DTEST_IMMORTAL={PY_FLAG_IMMORTAL}",
            f"-DTEST_DEALLOCATING={PY_FLAG_GC_DEALLOCATING}",
            f"-DTEST_LIST_ITEMS={PYLISTOBJECT_ITEMS_OFFSET}",
            f"-DTEST_LIST_CAPACITY={PYLISTOBJECT_CAPACITY_OFFSET}",
            f"-DTEST_LIST_LENGTH={PYLISTOBJECT_LENGTH_OFFSET}",
        ),
    )
    assert before == tuple(_digest(path) for path in inputs)
    return binary, mode, {
        "source_sha256": before[0], "runtime_sha256": before[1],
        "provenance_sha256": before[2], "binary_sha256": _digest(binary),
        "compiler": "host-pcc0", "backend": "self", "threads": True,
        "refcount_build": "atomic", "runtime_provisioning_by_compile_helper": False,
        "target": manifest["target_triple"],
    }


def _run(fixture, backend: int, scenario: int, workers: int):
    binary, mode, identity = fixture
    required_flag_mode = int(backend == 0 or (backend in (1, 2) and mode == "flags"))
    command = [str(binary), str(scenario), str(workers), str(required_flag_mode)]
    result = run_process_group_timeout(
        command, timeout=30,
        env=dict(
            os.environ, PCC_GC_BACKEND=str(backend), PCC_THREADS="1",
            PCC_REFCOUNT_KIND="atomic", PCC_GC_DEBT_THRESHOLD="1099511627776",
            PCC_TEST_NO_NATIVE_PROVISIONING="1", PCC_NO_AUTO_PCC1="1",
            PATH="/nonexistent",
        ),
    )
    receipt = dict(
        identity, command=command, gc_backend=backend, scenario=scenario,
        workers=workers, expected_flag_mode=required_flag_mode,
        returncode=result.returncode, stdout=result.stdout, stderr=result.stderr,
    )
    label = f"gc{backend}-scenario{scenario}-workers{workers}-{mode}"
    (binary.parent / f"{label}.json").write_text(json.dumps(receipt, indent=2) + "\n")
    assert result.returncode == 0 and result.stderr == "", receipt
    assert result.stdout.startswith("NONMOVING_TRACKING_OK "), receipt
    fields = {
        key: int(value)
        for key, value in (word.split("=") for word in result.stdout.strip().split()[1:])
    }
    assert fields["gc"] == backend and fields["scenario"] == scenario, receipt
    assert fields["workers"] == workers and fields["flag_mode"] == required_flag_mode, receipt
    return fields, receipt


@pytest.mark.parametrize("backend", range(5), ids=lambda backend: f"gc{backend}")
def test_native_tracking_origins_and_introspection(nonmoving_tracking_binary, backend):
    fields, receipt = _run(nonmoving_tracking_binary, backend, 0, 1)
    assert fields["origins"] == 4 and fields["raw_calls"] == fields["pending"] == 0, receipt


@pytest.mark.parametrize("backend", (1, 2), ids=lambda backend: f"gc{backend}")
@pytest.mark.parametrize("workers", (1, 4), ids=lambda workers: f"workers{workers}")
def test_fresh_raw_pthreads_enter_through_tracking(nonmoving_tracking_binary, backend, workers):
    fields, receipt = _run(nonmoving_tracking_binary, backend, 1, workers)
    assert fields["raw_calls"] == workers * 4, receipt
    assert fields["origins"] == fields["pending"] == 0, receipt


@pytest.mark.parametrize("backend", (1, 2), ids=lambda backend: f"gc{backend}")
def test_pending_generator_retirement_is_not_snapshotted(nonmoving_tracking_binary, backend):
    fields, receipt = _run(nonmoving_tracking_binary, backend, 2, 1)
    assert 0 < fields["pending"] <= 32, receipt
    assert fields["origins"] == fields["raw_calls"] == 0, receipt


@pytest.mark.parametrize("backend", (1, 2), ids=lambda backend: f"gc{backend}")
def test_terminal_list_retires_retained_items_in_both_tracking_views(nonmoving_tracking_binary, backend):
    fields, receipt = _run(nonmoving_tracking_binary, backend, 3, 1)
    assert fields["terminal"] == 4, receipt
    assert fields["origins"] == fields["raw_calls"] == fields["pending"] == 0, receipt
