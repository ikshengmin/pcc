"""Real-runtime pending-verdict controls; no automatic runtime provisioning.

Synthetic candidates make finalizer ordering deterministic. These cases do
not substitute for the original typed-loop-unbox workload or a Darwin run.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

import pytest

from tests.owned_runtime_c_fixture import admitted_runtime, link_c_harness
from tests.python.process_timeout import run_process_group_timeout


ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests/fixtures/native/gc_tracing_pending_verdict.c"
MODES = (
    (1, "live-transitive", (0, 0, 0)),
    (2, "dead-self-cycle", (1, 1, 0)),
    (3, "self-resurrection", (1, 1, 0)),
    (4, "resurrected-finalizer-batch", (2, 2, 0)),
    (5, "gc-start-callback", (0, 0, 2)),
    (6, "nonpositive-budget", (0, 0, 0)),
    (7, "partial-budget-finalizer-once", (2, 2, 0)),
)


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@pytest.fixture(scope="module")
def _verdict_binary(tmp_path_factory):
    # This generic owned C helper requires an explicit archive and verifies
    # runtime source/codegen/configuration, and denies external build tools.
    archive = admitted_runtime()
    provenance = Path(str(archive) + ".provenance.json")
    manifest = json.loads(provenance.read_text(encoding="utf-8"))
    assert manifest["members"]
    assert all(member["runtime_build_config"]["threads"] is True
               and member["runtime_build_config"]["refcount"] == "atomic"
               for member in manifest["members"])
    directory = tmp_path_factory.mktemp("gc_tracing_sweep_verdict")
    binary = directory / "sweep_verdict.out"
    source_sha = _digest(HARNESS)
    archive_sha = _digest(archive)
    provenance_sha = _digest(provenance)
    link_c_harness(HARNESS, binary)
    assert _digest(HARNESS) == source_sha
    assert _digest(archive) == archive_sha
    assert _digest(provenance) == provenance_sha
    identity = {
        "schema": "pcc.tracing-sweep-verdict.v1",
        "compiler": "host-pcc0",
        "backend": "self",
        "source_sha256": source_sha,
        "runtime_sha256": archive_sha,
        "runtime_provenance_sha256": provenance_sha,
        "binary_sha256": _digest(binary),
        "target_triple": manifest["target_triple"],
        "synthetic_pending_verdict": True,
        "runtime_provisioning": False,
    }
    (directory / "build.json").write_text(json.dumps(identity, indent=2) + "\n")
    return binary, identity


@pytest.mark.integration
@pytest.mark.parametrize("mode,name,counts", MODES, ids=[mode[1] for mode in MODES])
@pytest.mark.parametrize("backend", (2, 1, 3, 4), ids=lambda backend: f"gc{backend}")
def test_pending_verdict_native(_verdict_binary, backend, mode, name, counts):
    binary, identity = _verdict_binary
    environment = dict(os.environ, PCC_GC_BACKEND=str(backend),
                       PCC_GC_DEBT_THRESHOLD="1099511627776",
                       PCC_TEST_NO_NATIVE_PROVISIONING="1", PCC_NO_AUTO_PCC1="1")
    environment.pop("LC_ALL", None)
    result = run_process_group_timeout([str(binary), str(mode)], env=environment, timeout=15)
    receipt = dict(identity, gc_backend=backend, mode=mode, case=name,
                   command=[str(binary), str(mode)], returncode=result.returncode,
                   stdout=result.stdout, stderr=result.stderr)
    (binary.parent / f"gc{backend}-{name}.json").write_text(json.dumps(receipt, indent=2) + "\n")
    assert result.returncode == 0, receipt
    assert result.stderr == "", receipt
    finalizers, weak_callbacks, gc_callbacks = counts
    assert result.stdout == (
        f"sweep-verdict:ok backend={backend} mode={mode} finalizers={finalizers} "
        f"weak_callbacks={weak_callbacks} gc_callbacks={gc_callbacks}\n"
    ), receipt


@pytest.mark.integration
def test_cycle_failure_diagnostic_branches_compile_and_keep_blocked_first(tmp_path):
    # Compile the actual C failure/snapshot/report branches. This scalar-only
    # probe is separate from the unchanged graph modes above and cannot prove
    # the cause of a GC failure or identify the five collected objects.
    source = HARNESS.read_text()
    run = source.split("static int run_mode(void) {", 1)[1].split("static void report_cycle_failure", 1)[0]
    callbacks = re.findall(r"if \(callback_error != 0\) \{.*?\n    \}", run, re.S)
    cycle = re.findall(
        r"if \(finalizers != 1 \|\| weak_callbacks != 1 \|\| collected != 1 \|\| known_first != 0\)"
        r" \{.*?\n        \}", run, re.S,
    )
    reporting = "static void report_cycle_failure" + source.split(
        "static void report_cycle_failure", 1)[1].split("int main(", 1)[0]
    emit = re.findall(r"if \(status != 0\) \{.*?\n    \}", source.split("int main(", 1)[1], re.S)
    assert len(callbacks) == 2 and len(cycle) == len(emit) == 1
    harness = tmp_path / "cycle_diagnostics.c"
    binary = tmp_path / "cycle_diagnostics.out"
    declarations = (
        "#include <stdio.h>\n"
        "static int phase = 4, mode = 7, callback_error, finalizers = 2, weak_callbacks = 1;\n"
        "static int finalizer_calls[2] = {1, 1}, weak_callback_calls[2] = {1, 0};\n"
        "static long long collected = 1, failure_collected;\n"
        "static int known_first, failure_check, failure_known_first;\n"
    )
    branches = "\n".join(
        f"static int branch_{index}(void) {{\n{block}\nreturn 0;\n}}"
        for index, block in enumerate((*callbacks, *cycle))
    )
    main = """
int main(int argc, char **argv) {
    if (argc != 3) return 100;
    int status;
    int fault = argv[2][0] - '0';
    callback_error = fault != 0 ? 9 : 0;
    if (argv[1][0] == '0') status = branch_0();
    else if (argv[1][0] == '1') status = branch_1();
    else {
        mode = 2;
        callback_error = 0;
        finalizers = fault == 1 ? 0 : 1;
        weak_callbacks = fault == 2 ? 0 : 1;
        collected = fault == 3 ? 0 : 1;
        known_first = fault == 4 ? 1 : 0;
        weak_callback_calls[0] = 1;
        status = branch_2();
    }
""" + emit[0] + "\nreturn status;\n}\n"
    harness.write_text(declarations + branches + reporting + main)
    link_c_harness(harness, binary)
    for index in range(3):
        for fault in range(5 if index == 2 else 2):
            result = run_process_group_timeout([str(binary), str(index), str(fault)], timeout=15)
            status = (146 if index == 2 else 142) if fault else 0
            assert result.stderr == ""
            assert result.returncode == status
            if not fault:
                assert result.stdout == ""
                continue
            mode = 2 if index == 2 else 7
            first = f"sweep-verdict:blocked status={status} phase=4 mode={mode}\n"
            if index < 2:
                detail = (
                    f"sweep-verdict:detail status=142 check={index + 1} phase=4 mode=7 "
                    "final_callback_error=9 expected_callback_error=0 finalizers=2 weak_callbacks=1 "
                    "finalizer_first=1 finalizer_second=1 weak_first=1 weak_second=0\n"
                )
            else:
                detail = (
                    "sweep-verdict:detail status=146 check=3 phase=4 mode=2 "
                    f"finalizers={int(fault != 1)} expected_finalizers=1 "
                    f"weak_callbacks={int(fault != 2)} expected_weak_callbacks=1 "
                    f"collected={int(fault != 3)} expected_collected=1 "
                    f"known_first={int(fault == 4)} expected_known_first=0 "
                    "final_callback_error=0 finalizer_first=1 weak_first=1\n"
                )
            assert result.stdout == first + detail
