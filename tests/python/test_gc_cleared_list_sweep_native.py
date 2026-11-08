"""Real cyclic LIST shells, owned-runtime lifetime and synchronous items release.

Specification: docs/project-intent.md five-GC lifetime equality and unchanged
container ownership. Host pcc0, owned self backend, threaded atomic no-libpython
runtime only; not native-pcc1/bootstrap qualification. Public collection runs
without candidate mutation, after joining the complete creator stack.
Controlled modes inject candidate flags and a same-stop-epoch mark verdict;
production weakref invalidation, PASS0, resurrection rechecks, PASS1 and PASS2
are not replaced. Budget-one calls prove exact target claims, tracking removal,
unchanged RC and still-live header provenance while BUILDING. Allocator live
usable bytes independently prove synchronous raw-array release before seal.
Both flag-only and real materialized merged tracking representations are used.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.owned_runtime_c_fixture import admitted_runtime, link_c_harness
from tests.python.process_timeout import run_process_group_timeout

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.xdist_group("gc-cleared-list-sweep-native")


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@pytest.fixture(scope="module")
def cleared_list_binary(tmp_path_factory):
    archive = admitted_runtime()
    provenance = Path(str(archive) + ".provenance.json")
    manifest = json.loads(provenance.read_text())
    assert all(
        member["runtime_build_config"]["threads"] is True
        and member["runtime_build_config"]["refcount"] == "atomic"
        for member in manifest["members"]
    )
    assert "freestanding_gc_scalar_sweep.o" in {
        member["member"] for member in manifest["members"]
    }
    source = ROOT / "tests/fixtures/native/gc_cleared_list_sweep_worker.c"
    binary = tmp_path_factory.mktemp("gc_cleared_list_sweep") / "cleared_list.out"
    before = tuple(_digest(path) for path in (source, archive, provenance))
    link_c_harness(source, binary, runtime_archive=archive)
    assert before == tuple(_digest(path) for path in (source, archive, provenance))
    return binary, dict(
        source_sha256=before[0], runtime_sha256=before[1],
        provenance_sha256=before[2], binary_sha256=_digest(binary),
        target=manifest["target_triple"], compiler="host-pcc0",
        backend="self", runtime_provisioning=False,
    )


def _run(cleared_list_binary, backend, mode):
    binary, identity = cleared_list_binary
    command = [str(binary), str(mode)]
    result = run_process_group_timeout(
        command,
        env=dict(
            os.environ, PCC_GC_BACKEND=str(backend),
            PCC_GC_DEBT_THRESHOLD="1099511627776",
            PCC_TEST_NO_NATIVE_PROVISIONING="1", PCC_NO_AUTO_PCC1="1",
        ),
        timeout=30,
    )
    receipt = dict(
        identity, command=command, gc_backend=backend, mode=mode,
        returncode=result.returncode, stdout=result.stdout, stderr=result.stderr,
    )
    (binary.parent / f"gc{backend}-mode{mode}.json").write_text(
        json.dumps(receipt, indent=2) + "\n"
    )
    assert result.returncode == 0 and result.stderr == "", receipt
    assert result.stdout.startswith("cleared-list-sweep "), receipt
    fields = {
        key: int(value)
        for key, value in (
            item.split("=") for item in result.stdout.strip().split()[1:]
        )
    }
    assert fields["backend"] == backend and fields["mode"] == mode, receipt
    assert fields["status"] == fields["first_failure"] == fields["setup_status"] == fields["double_frees"] == 0, receipt
    assert fields["tracking_flags"] == int(backend <= 2), receipt
    assert fields["tracking_view"] == 0, receipt
    assert fields["retired"] == fields["released"] == fields["admitted"], receipt
    assert fields["worker_retired"] == fields["admitted"] and fields["local"] == 0, receipt
    assert fields["pending"] == fields["state"] == fields["quarantine"] == 0, receipt
    assert fields["sentinel_checks"] == 2, receipt
    if fields["admitted"]:
        assert fields["worker"] > 0 and fields["worker"] != fields["producer"], receipt
        assert 0 < fields["peak_objects"] <= 256, receipt
        assert 0 < fields["peak_bytes"] <= 65536, receipt
        assert fields["chunks"] >= (fields["admitted"] + 255) // 256, receipt
    else:
        assert fields["chunks"] == fields["peak_objects"] == fields["peak_bytes"] == 0, receipt
    return fields, receipt


@pytest.mark.integration
@pytest.mark.parametrize("backend", range(5), ids=lambda value: f"gc{value}")
def test_public_collect_retires_populated_cyclic_lists(cleared_list_binary, backend):
    fields, receipt = _run(cleared_list_binary, backend, 1)
    if backend in (1, 2):
        # Queue counters can include incidental deaths. Independently verify
        # the actual target LIST addresses disappeared from the object index.
        assert fields["admitted"] > 512 and fields["public_list_gone"] > 512, receipt
        assert fields["chunks"] >= 2, receipt
    else:
        assert fields["admitted"] == 0, receipt
    assert fields["bounded_calls"] == fields["observed_list"] == 0, receipt
    assert fields["rejected"] == fields["item_release_checks"] == 0, receipt


@pytest.mark.integration
@pytest.mark.parametrize("backend", (1, 2), ids=lambda value: f"gc{value}")
@pytest.mark.parametrize("mode", (2, 3), ids=("flags", "materialized-merged"))
def test_bounded_list_sweep_releases_items_before_seal(
    cleared_list_binary, backend, mode,
):
    fields, receipt = _run(cleared_list_binary, backend, mode)
    count = 12
    assert fields["rejected"] == fields["observed_list"] == fields["admitted"] == count, receipt
    assert fields["bounded_calls"] == count + 1, receipt
    assert fields["tracking_checks"] == fields["tracked_delta"] == count, receipt
    assert fields["item_release_checks"] == count, receipt
    assert fields["items_released_bytes"] > 0, receipt
    assert fields["materialized_checks"] == (count if mode == 3 else 0), receipt
    assert fields["finalizers"] == fields["public_list_gone"] == 0, receipt
