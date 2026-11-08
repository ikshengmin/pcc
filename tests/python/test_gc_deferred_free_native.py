"""Owned native terminal-free and collector-owner boundaries, no provisioning.

These controls inject terminal/candidate verdicts on real runtime allocations.
They prove neither an unmodified workload nor native-pcc1 qualification.
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
FIXTURES = ROOT / "tests/fixtures/native"
# Default -n 6 --dist=loadgroup stays unchanged; one worker owns this module's
# two linked binaries rather than rebuilding both in each of six workers.
pytestmark = pytest.mark.xdist_group("gc-deferred-free-native")


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@pytest.fixture(scope="module")
def deferred_binaries(tmp_path_factory):
    archive = admitted_runtime()
    provenance = Path(str(archive) + ".provenance.json")
    manifest = json.loads(provenance.read_text())
    assert all(member["runtime_build_config"]["threads"] is True
               and member["runtime_build_config"]["refcount"] == "atomic"
               for member in manifest["members"])
    assert "freestanding_gc_deferred_free.o" in {
        member["member"] for member in manifest["members"]
    }
    directory = tmp_path_factory.mktemp("gc_deferred_free")
    outputs = {}
    for kind in ("queue", "owners", "worker"):
        source = FIXTURES / f"gc_deferred_free_{kind}.c"
        binary = directory / f"{kind}.out"
        before = (_digest(source), _digest(archive), _digest(provenance))
        link_c_harness(source, binary, runtime_archive=archive)
        assert before == (_digest(source), _digest(archive), _digest(provenance))
        identity = dict(source_sha256=before[0], runtime_sha256=before[1],
                        provenance_sha256=before[2], binary_sha256=_digest(binary),
                        target=manifest["target_triple"], compiler="host-pcc0",
                        backend="self", runtime_provisioning=False)
        outputs[kind] = binary, identity
    return outputs


def _execute(outputs, kind, backend, args):
    binary, identity = outputs[kind]
    environment = dict(os.environ, PCC_GC_BACKEND=str(backend),
                       PCC_GC_DEBT_THRESHOLD="1099511627776",
                       PCC_TEST_NO_NATIVE_PROVISIONING="1", PCC_NO_AUTO_PCC1="1")
    if kind == "queue" and args == (5,):
        environment["PCC_GC_REFCOUNT_PROVENANCE_PROBE"] = "1"
    command = [str(binary), *(str(arg) for arg in args)]
    result = run_process_group_timeout(command, env=environment, timeout=30)
    receipt = dict(identity, command=command, gc_backend=backend,
                   returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    name = f"{kind}-gc{backend}-" + "-".join(map(str, args))
    (binary.parent / f"{name}.json").write_text(json.dumps(receipt, indent=2) + "\n")
    assert result.returncode == 0, receipt
    assert result.stderr == "", receipt
    return result.stdout


@pytest.mark.integration
@pytest.mark.parametrize("backend", range(5), ids=lambda value: f"gc{value}")
@pytest.mark.parametrize("mode", (1, 2, 3, 4, 5),
                         ids=("block-cap", "usable-cap", "carrier-resume", "excluded-flags", "terminal-provenance"))
def test_terminal_queue_native(deferred_binaries, backend, mode):
    output = _execute(deferred_binaries, "queue", backend, (mode,))
    if mode == 3 and backend in (1, 2):
        diagnostic, output = output.split("\n", 1)
        prefix = "carrier-resume acknowledged=1 overlap_observations="
        assert diagnostic.startswith(prefix), diagnostic
        # Zero is valid: OS scheduling may never run both carriers within the
        # short physical-free window. Keep the observation in the run receipt.
        assert int(diagnostic[len(prefix):]) >= 0, diagnostic
    assert output == f"deferred-queue backend={backend} mode={mode} status=0\n"


@pytest.mark.integration
@pytest.mark.parametrize("backend", (1, 2, 3, 4), ids=lambda value: f"gc{value}")
@pytest.mark.parametrize("route", (1, 2, 3, 4, 5),
                         ids=("explicit", "already-held", "direct", "automatic", "direct-sweep"))
@pytest.mark.parametrize("mode", (1, 2, 3, 4), ids=("live-graph", "dead-newborn", "resurrection", "resurrected-batch"))
def test_collection_owner_native(deferred_binaries, backend, route, mode):
    assert _execute(deferred_binaries, "owners", backend, (route, mode)) == (
        f"deferred-owners backend={backend} route={route} mode={mode} status=0\n"
    )


@pytest.mark.integration
@pytest.mark.parametrize("mode", (1, 2, 3, 4),
                         ids=("live-graph", "dead-newborn", "resurrection", "resurrected-batch"))
def test_gc0_collection_unchanged_native(deferred_binaries, mode):
    assert _execute(deferred_binaries, "owners", 0, (1, mode)) == (
        f"deferred-owners backend=0 route=1 mode={mode} status=0\n"
    )


@pytest.mark.integration
@pytest.mark.parametrize("backend", (1, 2, 3, 4), ids=lambda value: f"gc{value}")
@pytest.mark.parametrize("route", (1, 2, 3, 4, 5),
                         ids=("explicit", "already-held", "direct", "automatic", "direct-sweep"))
def test_plain_collection_owner_native(deferred_binaries, backend, route):
    """A no-finalizer owner control independent of the lifecycle reproducer."""
    assert _execute(deferred_binaries, "owners", backend, (route, 6)) == (
        f"deferred-owners backend={backend} route={route} mode=6 status=0\n"
    )


@pytest.mark.integration
@pytest.mark.parametrize("backend", (1, 2, 3, 4), ids=lambda value: f"gc{value}")
@pytest.mark.parametrize("route", (1, 4), ids=("explicit", "automatic"))
def test_leaf_batch_collection_owner_native(deferred_binaries, backend, route):
    """One fixed batch; legal conservative retention of a subset is allowed."""
    output = _execute(deferred_binaries, "owners", backend, (route, 9))
    diagnostic, output = output.split("\n", 1)
    prefix = "owner-leaf-batch "
    assert diagnostic.startswith(prefix), diagnostic
    fields = dict(item.split("=") for item in diagnostic[len(prefix):].split())
    assert int(fields["candidates"]) == 64, diagnostic
    assert int(fields["queued"]) == int(fields["drained"]), diagnostic
    if backend in (1, 2):
        assert int(fields["queued"]) + int(fields["scalar"]) > 0, diagnostic
    else:
        assert int(fields["queued"]) == int(fields["scalar"]) == 0, diagnostic
    assert output == f"deferred-owners backend={backend} route={route} mode=9 status=0\n"


@pytest.mark.integration
@pytest.mark.parametrize("backend", range(5), ids=lambda value: f"gc{value}")
@pytest.mark.parametrize("mode", (1, 2, 3), ids=("disabled", "zero-threshold", "stop-race"))
def test_existing_cms_worker_consumes_retired_blocks_native(deferred_binaries, backend, mode):
    output = _execute(deferred_binaries, "worker", backend, (mode,))
    assert output.startswith("cms-terminal ")
    fields = {key: int(value) for key, value in
              (item.split("=") for item in output.strip().split()[1:])}
    assert fields["status"] == fields["pending"] == fields["pending_bytes"] == 0, output
    assert fields["backend"] == backend and fields["mode"] == mode, output
    assert fields["queued"] == fields["drained"] == fields["handed"] == fields["worker_drained"], output
    assert fields["local"] == 0, output
    if backend in (1, 2):
        assert fields["worker_drained"] == 128 and fields["reused"] > 0, output
        assert fields["worker"] > 0 and fields["worker"] != fields["producer"], output
    else:
        assert fields["worker_drained"] == fields["worker"] == fields["reused"] == 0, output
