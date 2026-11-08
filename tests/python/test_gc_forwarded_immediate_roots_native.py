"""Real GC3/GC4 forwarding at both public immediate-root store ABIs.

This complements the production-body model and the finalizer/weakref/pin/
C-extension/static-literal/GC4 shell suites; it does not replace those gates.
The C caller is emitted and linked by PCC's owned C helper, with no host cc,
libpython target, fabricated heap layout or synthetic forwarding/stop flag.
Specification: docs/project-intent.md, shared five-GC slot/root contract.
"""
from pathlib import Path
import hashlib
import json
import os

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.process_timeout import run_process_group_timeout


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("fixtures") / "gc_forwarded_immediate_roots.c"


def _run(binary, backend, abi, mode, value, stderr_path):
    return run_process_group_timeout(
        [str(binary), abi, mode, value],
        timeout=20,
        stderr_path=stderr_path,
        env=dict(
            os.environ,
            PATH="/nonexistent",
            PCC_GC_BACKEND=str(backend),
            PCC_THREADS="1",
            PCC_REFCOUNT_KIND="atomic",
            PCC_GC_REFCOUNT_PROVENANCE_PROBE="0",
            PCC_GC_TRACING_ONLY="0",
            PCC_GC4_TRACING_ONLY="0",
            PCC_LOG="",
        ),
    )


@pytest.mark.integration
def test_forwarded_immediate_root_native_matrix(tmp_path, pcc_runtime_archive):
    # One owned compile/link, then 16 separately bounded native processes.
    # The caller retains the ordinary 90s/1-GiB/six-worker language guard.
    binary = tmp_path / "forwarded_roots"
    # Archive/source verification and forbidden provisioning/build subprocesses
    # live in this helper. Missing prerequisites must fail rather than skip.
    compile_owned_c_with_runtime(
        FIXTURE,
        binary,
        runtime_archive=Path(pcc_runtime_archive),
        cpp_args=("-I", str(ROOT / "pcc/runtime/src")),
    )
    receipt = {
        "source_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "runtime_sha256": hashlib.sha256(Path(pcc_runtime_archive).read_bytes()).hexdigest(),
        "runs": [],
    }
    for backend in (3, 4):
        for abi in ("root", "take"):
            for mode, value in (
                ("semantics", "null"),
                ("handoff", "null"),
                ("handoff", "low"),
                ("handoff", "high"),
            ):
                case = f"gc{backend}-{abi}-{mode}-{value}"
                result = _run(
                    binary, backend, abi, mode, value, tmp_path / f"{case}.stderr",
                )
                (tmp_path / f"{case}.stdout").write_text(result.stdout)
                receipt["runs"].append({
                    "case": case,
                    "backend": backend,
                    "abi": abi,
                    "mode": mode,
                    "value": value,
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                })
                # Persist every outcome before assertions, including failures.
                (tmp_path / "forwarded-roots.json").write_text(
                    json.dumps(receipt, indent=2) + "\n",
                )
                assert result.returncode == 0 and result.stderr == "", (case, result)
                prefix = "FORWARDED_IMMEDIATE_ROOTS_OK "
                assert result.stdout.startswith(prefix) and result.stdout.count("\n") == 1, (
                    case, result,
                )
                fields = dict(field.split("=", 1) for field in result.stdout[len(prefix):].split())
                assert fields["gc"] == str(backend) and fields["take"] == str(int(abi == "take"))
                assert fields["mode"] == mode and fields["value"] == value
                assert int(fields["forwarding_before"]) > 0
                assert int(fields["forwarding_after"]) >= 0
                assert fields["count_free"] == fields["conservative"] == "1"
                assert fields["probe"] == fields["log"] == fields["graph"] == fields["lease"] == "0"
                assert int(fields["queue_witnesses"]) == int(mode == "handoff")
                if mode == "handoff":
                    assert int(fields["collections"]) > 0
                else:
                    assert fields["collections"] == "0"
    assert len(receipt["runs"]) == 16
