"""Cleared ordinary generators, actual owned-runtime lifetime evidence.

Specification: docs/project-intent.md, five-GC lifetime equality (finalizers,
weakrefs, resurrection and suspended frames). Host pcc0, owned self backend,
threaded atomic no-libpython runtime; not native-pcc1/bootstrap qualification.
Public collection has no injected verdicts. Controlled modes inject candidate
flags and a same-stop-epoch completed-mark verdict, then use the real bounded
three-pass sweep and CMS worker. Real weakref/finalizer reachability rechecks
remain enabled.
Started-generator checks distinguish proven first-epoch retirement from real
conservative retention. The former requires an observed negative-owner claim
with cleared slots; the latter requires the live verdict and intact fields.
Both join the entire observer thread before fresh public collections prove
eventual retirement.
Each child independently must have a missing index or a verified live verdict;
the first sweep's reclaimed count is checked against this actual partition.
The old one-epoch death assertion was wrong when a real snapshot still held
the generator. Finally resurrection must actually occur before its explicit
root is released; it is never replaced by a direct finalizer call.
The C resume thunk covers the native generator ABI; Python finally lowering is
covered separately by test_generator_finalizer_accounting_native.
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
pytestmark = pytest.mark.xdist_group("gc-cleared-gen-sweep-native")


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@pytest.fixture(scope="module")
def cleared_gen_binary(tmp_path_factory):
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
    source = ROOT / "tests/fixtures/native/gc_cleared_gen_sweep_worker.c"
    binary = tmp_path_factory.mktemp("gc_cleared_gen_sweep") / "cleared_gen.out"
    before = tuple(_digest(path) for path in (source, archive, provenance))
    link_c_harness(source, binary, runtime_archive=archive)
    assert before == tuple(_digest(path) for path in (source, archive, provenance))
    return binary, dict(
        source_sha256=before[0], runtime_sha256=before[1],
        provenance_sha256=before[2], binary_sha256=_digest(binary),
        target=manifest["target_triple"], compiler="host-pcc0",
        backend="self", runtime_provisioning=False,
    )


def _run(cleared_gen_binary, backend, mode):
    binary, identity = cleared_gen_binary
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
    assert result.stdout.startswith("cleared-gen-sweep "), receipt
    fields = {
        key: int(value)
        for key, value in (
            item.split("=") for item in result.stdout.strip().split()[1:]
        )
    }
    assert fields["backend"] == backend and fields["mode"] == mode, receipt
    assert fields["status"] == fields["double_frees"] == 0, receipt
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
def test_public_collect_retires_ordinary_generators(cleared_gen_binary, backend):
    fields, receipt = _run(cleared_gen_binary, backend, 1)
    if backend in (1, 2):
        # Total queue deaths need not all be generators. Independently prove
        # most of the actual ordinary-generator addresses lost their index.
        assert fields["admitted"] > 512 and fields["public_gen_gone"] > 512, receipt
    else:
        assert fields["admitted"] == 0, receipt
    assert fields["bounded_calls"] == fields["observed_gen"] == 0, receipt


@pytest.mark.integration
@pytest.mark.parametrize("backend", (1, 2), ids=lambda value: f"gc{value}")
@pytest.mark.parametrize(
    "mode", (2, 3, 4, 5, 6, 7),
    ids=("unstarted", "completed", "already-finalized", "suspended-pending-error",
         "finally-resurrection", "weak-callback-resurrection"),
)
def test_controlled_bounded_sweep_clears_owned_gen_edges_before_retirement(
    cleared_gen_binary, backend, mode,
):
    fields, receipt = _run(cleared_gen_binary, backend, mode)
    count = 1 if mode >= 5 else 12
    assert fields["rejected"] == count, receipt
    assert 1 <= fields["bounded_calls"] <= count * 4 + 1, receipt
    if mode < 5:
        assert fields["observed_gen"] == fields["admitted"] == count, receipt
        assert fields["tracking_checks"] == count, receipt
        assert fields["children_gone"] == 3 * count, receipt
        assert fields["finalizers"] == fields["closes"] == 0, receipt
        assert fields["pending_preserved"] == 0, receipt
        assert fields["weak"] == fields["resurrection_checks"] == 0, receipt
        if mode == 2:
            assert fields["tracked_delta"] == 4 * count, receipt
    elif mode in (5, 6):
        assert 0 <= fields["first_children_dead_mask"] <= 7, receipt
        assert fields["first_children_gone"] == fields["first_children_dead_mask"].bit_count(), receipt
        assert fields["actual_reclaimed"] == fields["observed_gen"] + fields["first_children_gone"], receipt
        assert fields["public_gen_gone"] == 1 and fields["children_gone"] == 3, receipt
        assert fields["finalizer_total"] == fields["closes"] == 1, receipt
        # Public collection may retire other scalars as well. These total
        # worker counters are deliberately not attributed solely to GEN.
        assert fields["admitted"] > 0, receipt
        assert fields["pending_preserved"] == int(mode == 5), receipt
        if mode == 5:
            assert fields["finalizers"] == 1, receipt
            if fields["first_epoch_admitted"] == 1:
                assert fields["observed_gen"] == fields["tracking_checks"] == 1, receipt
                assert fields["retained_gen"] == 0, receipt
            else:
                assert fields["first_epoch_admitted"] == 0, receipt
                assert fields["observed_gen"] == fields["tracking_checks"] == 0, receipt
                assert fields["retained_gen"] == 1, receipt
                assert fields["first_children_dead_mask"] & 3 == 0, receipt
                assert fields["root_gen"] > 0 and fields["pin_gen"] & 1, receipt
            assert fields["diagnostic_pending"] == 1, receipt
            assert 1 <= fields["public_collections"] <= 4, receipt
            assert fields["weak"] == fields["resurrection_checks"] == 0, receipt
        else:
            assert fields["first_epoch_admitted"] == fields["observed_gen"] == 0, receipt
            assert fields["retained_gen"] == 1 and fields["tracking_checks"] == 0, receipt
            assert fields["finalizers"] in (0, 1), receipt
            assert fields["first_children_dead_mask"] & 3 == 0, receipt
            if fields["finalizers"] == 0:
                assert fields["first_children_gone"] == 0, receipt
            assert 2 <= fields["public_collections"] <= 8, receipt
            assert fields["weak"] == fields["resurrection_checks"] == 1, receipt
    else:
        assert fields["observed_gen"] == fields["admitted"] == 0, receipt
        assert fields["weak"] == fields["resurrection_checks"] == 1, receipt
        assert fields["children_gone"] == fields["finalizers"] == 0, receipt
        # Weak resurrection keeps the explicit owner and its fields intact;
        # the retained object can subsequently be closed once.
        assert fields["closes"] == 1, receipt
