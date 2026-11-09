"""Execute terminal-root marking and the actual GC3 zero-edge conservative scan.

Consumes an explicitly source-admitted owned runtime. Host pcc0 owns C
compilation/linking. Does not prove full relocation, bootstrap or throughput.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import sys

import pytest

from tests.owned_runtime_c_fixture import admitted_runtime, link_c_harness_bounded
from tests.python.process_timeout import run_process_group_timeout


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tests/fixtures/native/gc_terminal_root_mark.c"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.xdist_group("gc-terminal-root-mark-native"),
    pytest.mark.pcc_gate(
        probe=lambda: sys.platform == "linux"
        and platform.machine().lower() in ("x86_64", "amd64"),
    ),
]


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@pytest.fixture(scope="module")
def terminal_root_binary(tmp_path_factory):
    archive = admitted_runtime()
    provenance = Path(str(archive) + ".provenance.json")
    manifest = json.loads(provenance.read_text())
    assert manifest["target_triple"] == "x86_64-unknown-linux-gnu"
    assert all(
        member["runtime_build_config"]["threads"] is True
        and member["runtime_build_config"]["refcount"] == "atomic"
        for member in manifest["members"]
    )
    assert "freestanding_gc_conservative_roots.o" in {
        member["member"] for member in manifest["members"]
    }
    binary = tmp_path_factory.mktemp("gc_terminal_root_mark") / "terminal_root.out"
    inputs = (SOURCE, archive, provenance)
    before = tuple(_digest(path) for path in inputs)
    compiled = link_c_harness_bounded(
        SOURCE, binary, runtime_archive=archive, timeout=60,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    assert before == tuple(_digest(path) for path in inputs)
    return binary, {
        "source_sha256": before[0], "runtime_sha256": before[1],
        "provenance_sha256": before[2], "binary_sha256": _digest(binary),
        "compiler": "host-pcc0", "backend": "self",
        "target": manifest["target_triple"], "runtime_provisioning": False,
    }


@pytest.mark.parametrize("backend", (3, 4), ids=lambda value: f"gc{value}")
def test_owned_native_terminal_root_marking(terminal_root_binary, backend):
    binary, identity = terminal_root_binary
    command = [str(binary)]
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
        identity, command=command, gc_backend=backend,
        returncode=result.returncode, stdout=result.stdout, stderr=result.stderr,
    )
    (binary.parent / f"gc{backend}.json").write_text(json.dumps(receipt, indent=2) + "\n")
    assert result.returncode == 0 and result.stderr == "", receipt
    scan_receipt = "ZERO_EDGE_SCAN_OK cases=4\n" if backend == 3 else ""
    assert result.stdout == scan_receipt + f"TERMINAL_ROOT_OK gc={backend} cases=24\n", receipt
