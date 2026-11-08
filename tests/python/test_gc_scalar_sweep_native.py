"""Bounded CMS retirement chunks over real owned-runtime allocations.

Host pcc0 self backend, no native-pcc1 or full lifecycle qualification.
All modes but public-collect inject dead verdicts. Quarantine mode additionally
invalidates the first pending chunk's eligibility after sealing.
Specification: docs/project-intent.md, five-GC lifetime equality.
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
pytestmark = pytest.mark.xdist_group("gc-scalar-sweep-native")


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@pytest.fixture(scope="module")
def scalar_binary(tmp_path_factory):
    archive = admitted_runtime()
    provenance = Path(str(archive) + ".provenance.json")
    manifest = json.loads(provenance.read_text())
    assert all(member["runtime_build_config"]["threads"] is True
               and member["runtime_build_config"]["refcount"] == "atomic"
               for member in manifest["members"])
    assert "freestanding_gc_scalar_sweep.o" in {member["member"] for member in manifest["members"]}
    source = ROOT / "tests/fixtures/native/gc_scalar_sweep_worker.c"
    binary = tmp_path_factory.mktemp("gc_scalar_sweep") / "scalar.out"
    before = tuple(_digest(path) for path in (source, archive, provenance))
    link_c_harness(source, binary, runtime_archive=archive)
    assert before == tuple(_digest(path) for path in (source, archive, provenance))
    return binary, dict(source_sha256=before[0], runtime_sha256=before[1],
                        provenance_sha256=before[2], binary_sha256=_digest(binary),
                        target=manifest["target_triple"], compiler="host-pcc0",
                        backend="self", runtime_provisioning=False)


@pytest.mark.integration
@pytest.mark.parametrize("backend", range(5), ids=lambda value: f"gc{value}")
@pytest.mark.parametrize(
    "mode", range(1, 16),
    ids=("disabled", "threshold0", "stop-race", "same-backend-reset",
         "missing-worker", "object-chunks", "exclusions", "concurrent-allocation",
         "public-collect", "busy-sealed", "byte-chunks", "multichunk-stop",
         "repeated-epochs", "node-pool-overflow", "quarantined-first-chunk"),
)
def test_cms_retires_scalar_nodes_and_provenance_after_resume(scalar_binary, backend, mode):
    binary, identity = scalar_binary
    command = [str(binary), str(mode)]
    environment = dict(os.environ, PCC_GC_BACKEND=str(backend),
                       PCC_GC_DEBT_THRESHOLD="1099511627776",
                       PCC_TEST_NO_NATIVE_PROVISIONING="1", PCC_NO_AUTO_PCC1="1")
    result = run_process_group_timeout(command, env=environment, timeout=30)
    receipt = dict(identity, command=command, gc_backend=backend,
                   returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    (binary.parent / f"gc{backend}-mode{mode}.json").write_text(json.dumps(receipt, indent=2) + "\n")
    assert result.returncode == 0 and result.stderr == "", receipt
    assert result.stdout.startswith("scalar-sweep "), receipt
    fields = {key: int(value) for key, value in
              (item.split("=") for item in result.stdout.strip().split()[1:])}
    enabled = backend in (1, 2) and mode != 5
    per_epoch = 8705 if mode == 14 else (777 if mode == 6 or mode >= 11 else 48)
    epochs = 3 if mode == 13 else 1
    expected = per_epoch * epochs if enabled else 0
    quarantined = 256 if enabled and mode == 15 else 0
    if mode == 9:
        expected = fields["admitted"]
        if enabled:
            # Real mark/root/sweep, without injected candidate flags. Allow
            # conservative roots, but require more than two chunks of deaths.
            assert expected > 512, receipt
        else:
            assert expected == 0, receipt
    assert fields["backend"] == backend and fields["mode"] == mode, receipt
    assert fields["admitted"] == fields["sealed_pending"] == expected, receipt
    assert fields["retired"] == fields["released"] == expected - quarantined, receipt
    assert fields["worker_retired"] + fields["local"] == expected - quarantined, receipt
    assert fields["pending"] == fields["quarantine"] == quarantined, receipt
    assert fields["pending_bytes"] == quarantined * fields["usable"], receipt
    assert fields["state"] == (4 if quarantined else 0), receipt
    assert fields["status"] == fields["double_frees"] == 0, receipt
    assert fields["epochs"] == epochs, receipt
    if expected and mode not in (3, 12, 15):
        assert fields["worker_retired"] == expected and fields["local"] == 0, receipt
        assert fields["worker"] > 0 and fields["worker"] != fields["producer"], receipt
    if fields["worker_retired"]:
        assert fields["worker"] > 0 and fields["worker"] != fields["producer"], receipt
    if backend in (1, 2) and mode == 4:
        assert fields["reset"] == 2, receipt
    if backend in (1, 2) and mode == 8:
        assert fields["canary_allocations"] > 0, receipt
    if expected and mode not in (8, 9):
        assert fields["node_reused"] > 0, receipt
    if backend != 0:
        assert fields["list_checks"] > 0, receipt
    if expected:
        assert 0 < fields["peak_objects"] <= 256, receipt
        assert 0 < fields["peak_bytes"] <= 65536, receipt
        assert fields["chunks"] >= (expected + 255) // 256, receipt
        if mode == 9:
            assert fields["peak_pending"] == expected, receipt
            assert fields["peak_pending_bytes"] >= expected * 64, receipt
        else:
            usable = fields["usable"]
            assert usable >= (1024 if mode == 11 else 64), receipt
            per_chunk = min(256, 65536 // usable)
            assert fields["chunks"] == epochs * ((per_epoch + per_chunk - 1) // per_chunk), receipt
            assert fields["peak_objects"] == min(per_epoch, per_chunk), receipt
            assert fields["peak_bytes"] == fields["peak_objects"] * usable, receipt
            assert fields["peak_pending"] == per_epoch, receipt
            assert fields["peak_pending_bytes"] == per_epoch * usable, receipt
            assert fields["claimed_bytes"] == expected * usable, receipt
            assert fields["observers"] == expected, receipt
        assert fields["overflow_nodes"] == (8705 - 8192 if mode == 14 else 0), receipt
        if mode == 11:
            assert fields["chunks"] >= 12 and fields["peak_objects"] < 256, receipt
        if mode == 13:
            assert fields["reset"] == epochs, receipt
            assert fields["list_checks"] >= 4 * epochs, receipt
        if mode == 15:
            assert fields["quarantine_checks"] == quarantined, receipt
            assert fields["stop_checks"] == 1, receipt
    else:
        # GC0/3/4 and missing-worker retain their synchronous paths. Merely
        # surviving the fixture is insufficient: none may consume this queue.
        for name in ("chunks", "peak_objects", "peak_bytes", "overflow_nodes",
                     "peak_pending", "peak_pending_bytes", "claimed_bytes", "observers"):
            assert fields[name] == 0, receipt
