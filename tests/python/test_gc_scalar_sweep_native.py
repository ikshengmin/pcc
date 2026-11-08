"""Real owned-runtime CMS logical retirement, with injected dead verdicts.

Host pcc0 self backend, no native-pcc1 or full lifecycle qualification.
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
@pytest.mark.parametrize("mode", (1, 2, 3, 4, 5, 6, 7, 8, 9, 10),
                         ids=("disabled", "threshold0", "stop-race", "same-backend-reset",
                              "missing-worker", "task-cap", "exclusions", "concurrent-allocation", "public-collect", "busy-sealed"))
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
    expected = (256 if mode == 6 else 48) if backend in (1, 2) and mode != 5 else 0
    if mode == 9:
        expected = fields["admitted"]
        assert (expected > 0) == (backend in (1, 2)), receipt
    assert fields["admitted"] == fields["sealed_pending"] == fields["retired"] == fields["released"] == expected, receipt
    assert fields["pending"] == fields["quarantine"] == fields["status"] == 0, receipt
    if expected and mode != 3:
        assert fields["worker_retired"] == expected and fields["local"] == 0, receipt
        assert fields["worker"] > 0 and fields["worker"] != fields["producer"], receipt
    if backend in (1, 2) and mode == 4:
        assert fields["reset"] == 2, receipt
    if backend in (1, 2) and mode == 8:
        assert fields["canary_allocations"] > 0, receipt
    if expected and mode not in (8, 9):
        assert fields["node_reused"] > 0, receipt
