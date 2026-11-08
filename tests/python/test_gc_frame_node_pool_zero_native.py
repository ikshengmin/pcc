"""Execute byte-exact frame-pool reuse through a source-admitted owned runtime.

Host pcc0 owns C preprocessing, emission and linking; no external C toolchain
or implicit runtime build is accepted. This proves actual allocator/TLS bytes,
not a complete collector lifecycle, native-pcc1/bootstrap or performance.
Allocation failure injection is covered by the separate actual-source model.
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
SOURCE = ROOT / "tests/fixtures/native/gc_frame_node_pool_zero.c"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.xdist_group("gc-frame-node-pool-zero-native"),
    pytest.mark.pcc_gate(
        probe=lambda: sys.platform == "linux"
        and platform.machine().lower() in ("x86_64", "amd64"),
    ),
]


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@pytest.fixture(scope="module")
def frame_pool_binary(tmp_path_factory):
    archive = admitted_runtime()
    provenance = Path(str(archive) + ".provenance.json")
    manifest = json.loads(provenance.read_text())
    assert manifest["target_triple"] == "x86_64-unknown-linux-gnu"
    assert all(
        member["runtime_build_config"]["threads"] is True
        and member["runtime_build_config"]["refcount"] == "atomic"
        for member in manifest["members"]
    )
    assert "freestanding_gc_frame_registry.o" in {
        member["member"] for member in manifest["members"]
    }
    binary = tmp_path_factory.mktemp("gc_frame_node_pool_zero") / "frame_pool.out"
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


@pytest.mark.parametrize("backend", range(5), ids=lambda value: f"gc{value}")
def test_owned_native_frame_pool_returns_fully_zeroed_reused_nodes(frame_pool_binary, backend):
    binary, identity = frame_pool_binary
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
    assert result.stdout.startswith("FRAME_NODE_ZERO_OK "), receipt
    fields = {
        key: int(value)
        for key, value in (word.split("=") for word in result.stdout.strip().split()[1:])
    }
    # 17 * (two fresh + eight two-node pops + one post-drain fresh),
    # two oversize, 1031 capacity allocations, three TLS checks, and
    # an additional 1024 capacity pops only for the pooled collectors.
    assert fields == {
        "gc": backend, "buckets": 17, "cycles": 8,
        "byte_checks": 1359 + (1024 if backend in (3, 4) else 0),
        "reuse_checks": 272 if backend in (3, 4) else 0,
        "cached": 0,
    }, receipt
