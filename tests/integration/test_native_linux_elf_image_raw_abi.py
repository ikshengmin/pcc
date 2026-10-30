"""Linux x86-64 host-pcc0 -> real owned native mapper ABI, not Python import.

Requires an explicitly supplied source-matched PCC_RUNTIME_ARCHIVE; never
provisions a compiler/runtime or uses system cc/libdl/LLVM. Existing process-
tree guards bound compilation to 300 s and aggregate execution to 30 s, each 4 GiB.
The caller must already hold the normal shared heavy-build lock; these stage
watchdogs do not acquire it. With that lock held, select this gate using:

    uv run pytest -x -m integration tests/integration/test_native_linux_elf_image_raw_abi.py

This arithmetic/raw-storage component is not a pcc1, public dlopen, managed
extension-import, NumPy, leak-freedom or five-GC acceptance test.
"""

import json
import os
from pathlib import Path
import platform
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "tests/fixtures/native/elf_image_raw_abi.py"


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=lambda: sys.platform == "linux" and platform.machine() == "x86_64"
                     and sys.implementation.name == "cpython")
def test_linux_elf_image_raw_abi_pcc0(tmp_path):
    from scripts.platform_process_watchdog import run

    selected = os.environ.get("PCC_RUNTIME_ARCHIVE")
    assert selected, "Set PCC_RUNTIME_ARCHIVE to a source-matched owned archive; this gate never builds one"
    assert not os.environ.get("PCC_TEST_COMPILER"), "this gate is explicitly pcc0-only, not native compiler routing"
    environment = dict(os.environ)
    environment.update({
        "PYTHONPATH": str(ROOT), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PCC_SOURCE_ROOT": str(ROOT), "PCC_REPO_ROOT": str(ROOT), "PCC_RUNTIME_DIR": str(ROOT / "pcc/runtime"),
        "PCC_RUNTIME_ARCHIVE": str(Path(selected).resolve(strict=True)),
        "PCC_WITH_THREADS": "1", "PCC_REFCOUNT_KIND": "atomic", "PCC_GC_BACKEND": "0",
        "PCC_RUNTIME_CC": "pcc", "PCC_RUNTIME_HIGH": "py", "PCC_SELF_LINK": "pcc",
        "PCC_SELF_OBJ": "pcc", "PCC_IR_TO_OBJ_EMITTER": "pcc",
        "PCC_TEST_NO_NATIVE_PROVISIONING": "1", "PCC_NO_AUTO_PCC1": "1",
        "PCC_PY_FRONTEND_IR_CACHE": "0", "PCC_SELF_BACKEND_OBJECT_CACHE": "0",
        "PCC_PY_FRONTEND_JOBS": "1", "PCC_SELF_BACKEND_JOBS": "1",
    })
    artifact = tmp_path / "artifacts"
    receipt = {"status": "BUILDING", "compiler_owner": "host-pcc0", "target": "linux-x86_64",
               "compile_timeout_s": 300, "native_aggregate_timeout_s": 30, "tree_cap_bytes": 4 * 1024**3}
    receipt_path = tmp_path / "gate.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    try:
        receipt["build_guard"] = run(
            [sys.executable, "-B", str(DRIVER), "build", str(artifact)], cwd=ROOT, env=environment,
            log_path=tmp_path / "build.log", timeout=300, rss_limit=4 * 1024**3,
        )
        receipt["status"] = "EXECUTING"
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        receipt["execution_guard"] = run(
            [sys.executable, "-B", str(DRIVER), "execute", str(artifact)], cwd=ROOT, env=environment,
            log_path=tmp_path / "execute.log", timeout=30, rss_limit=4 * 1024**3,
        )
        executions = json.loads((artifact / "execution.json").read_text())
        assert executions["status"] == "PASS" and len(executions["results"]) == 2
        assert all(row["returncode"] == 0 and not row["stderr_hex"] and row["target_path"] == ""
                   for row in executions["results"])
        receipt["status"] = "PASS"
    except BaseException as error:
        receipt.update(status="FAILED", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
