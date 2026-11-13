"""Supplement complete-marker WeakSet coverage with explicit execution owners.

The peer corpus is unchanged. Its original native marker cases remain selected.
The supplemental binaries append the same read-only collector query used by
bootstrap_platform's canary. No environment label stands in for observed GC.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from scripts.ci_pytest_markers import ROOT, admitted_stage1, digest, save

COLLECTOR_TRAILER = '''
from pcc.unsafe import gc_backend_current
print("PCC_OBSERVED_GC", gc_backend_current())
'''

HOST_COMPILE = '''
import hashlib
import json
from pathlib import Path
import sys
from pcc.frontends.python.pipeline import compile_python
assert sys.implementation.name == "cpython"
implementation = Path(compile_python.__code__.co_filename).resolve()
assert implementation == Path(sys.argv[4]).resolve()
Path(sys.argv[5]).write_text(json.dumps({
    "executable": sys.executable,
    "implementation": str(implementation),
    "implementation_sha256": hashlib.sha256(implementation.read_bytes()).hexdigest(),
    "python": sys.version,
}))
compile_python(sys.argv[1], sys.argv[2], backend="self", ir_scaffold_mode="on",
               runtime_archive=sys.argv[3])
'''


def compiler_environment(environment, runtime, owner):
    if owner not in ("pcc0", "pcc1"):
        raise ValueError("unknown compiler owner")
    env = dict(environment)
    # A fresh non-pytest interpreter cannot install tests.pcc1_route. Remove
    # its selectors as well, rather than relying on a misleading [pcc0] id.
    for name in ("PCC_TEST_COMPILER", "PCC_PLATFORM_TEST_COMPILER",
                 "PCC_CURRENT_PCC1", "PCC_REQUIRE_CURRENT_PCC1", "PCC_TEST_COMPILER_LOG",
                 "PCC_TEST_COMPILER_STRICT", "PCC_TEST_COMPILER_ROUTED",
                 "PCC_PYTHON_IR_PASSES"):
        env.pop(name, None)
    env.update(PCC_RUNTIME_ARCHIVE=str(runtime), PCC_THREADED_RUNTIME_ARCHIVE=str(runtime),
               PCC_NO_AUTO_PCC1="1", PCC_TEST_NO_NATIVE_PROVISIONING="1",
               PCC_RUNTIME_CC="/usr/bin/false", PCC_HOST_PCC="/usr/bin/false",
               PCC_DEBUG_WORKER_RERAISE="1")
    # Host dependency discovery legitimately uses CPython subprocess probes.
    # Native ownership must still fail closed rather than call host Python.
    env["PCC_HOST_PYTHON"] = sys.executable if owner == "pcc0" else "/usr/bin/false"
    return env


def compiler_command(owner, compiler, source, binary, runtime, host_receipt):
    if owner == "pcc0":
        return [sys.executable, "-c", HOST_COMPILE, str(source), str(binary),
                str(runtime), str(ROOT / "pcc/frontends/python/pipeline.py"), str(host_receipt)]
    if owner == "pcc1":
        return [str(compiler), "--backend", "self", "--ir-scaffold", "on",
                str(source), "-o", str(binary)]
    raise ValueError("unknown compiler owner")


def check_output(result, expected, backend):
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert result.stderr == "", result.stderr
    assert result.stdout == expected + f"PCC_OBSERVED_GC {backend}\n", result.stdout


def save_timeout_output(directory, error):
    """Keep subprocess.run's captured Windows timeout bytes, if available."""
    for name in ("stdout", "stderr"):
        value = getattr(error, name, None)
        if value is not None:
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            (directory / ("compile." + name)).write_text(value, encoding="utf-8")


def timeout_fields(error):
    fields = {"returncode": None, "outcome": "timeout", "timeout_s": error.timeout}
    for name in ("stdout", "stderr"):
        value = getattr(error, name, None) or ""
        fields[name] = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    return fields


def run_case(tmp_path, runtime, owner, program_name, program, cases):
    """Run only inside the existing admitted marker owner and its tree guard."""
    from tests.python.process_timeout import (
        run_process_group_timeout,
        run_process_tree_timeout,
    )

    manifest_path = Path(os.environ["PCC_CI_STAGE1_MANIFEST"])
    compiler = admitted_stage1(ROOT, manifest_path, runtime)
    manifest = json.loads(manifest_path.read_text())
    directory = Path(os.environ["PCC_MARKER_REPORT_DIR"]) / "weakset-owners" / (owner + "-" + program_name)
    directory.mkdir(parents=True, exist_ok=False)
    source = tmp_path / (program_name + ".py")
    source.write_text(program + COLLECTOR_TRAILER, encoding="utf-8")
    binary = tmp_path / (program_name + (".exe" if sys.platform == "win32" else ""))
    host_receipt = directory / "host-implementation.json"
    command = compiler_command(owner, compiler, source, binary, runtime, host_receipt)
    env = compiler_environment(os.environ, runtime, owner)
    receipt = {"status": "RUNNING", "owner": owner, "source": manifest["source"],
               "producer": manifest["producer"], "platform": manifest["platform"],
               "manifest_sha256": digest(manifest_path), "compiler_command": command,
               "compiler_sha256": digest(command[0]), "runtime": str(runtime),
               "runtime_sha256": digest(runtime), "program_sha256": digest(source),
               "compiler_environment": {key: env.get(key) for key in (
                   "PCC_RUNTIME_ARCHIVE", "PCC_THREADED_RUNTIME_ARCHIVE", "PCC_NO_AUTO_PCC1",
                   "PCC_TEST_NO_NATIVE_PROVISIONING", "PCC_RUNTIME_CC", "PCC_HOST_PYTHON",
                   "PCC_HOST_PCC", "PCC_TEST_COMPILER", "PCC_CURRENT_PCC1", "PCC_PYTHON_IR_PASSES")},
               "runs": [], "compile_timeout_s": 400,
               "guard": "existing complete-marker process-tree guard and remaining integration budget"}
    save(directory / "receipt.json", receipt)
    compile_finished = False
    try:
        # Native workers may start their own sessions. The shared POSIX tree
        # helper captures partial output and cleans those descendants too;
        # Windows streams stderr durably while the outer marker Job Object
        # owns descendant cleanup. Its timeout output is saved below as well.
        if sys.platform == "win32":
            result = run_process_group_timeout(command, cwd=ROOT, env=env, timeout=400,
                                               stderr_path=directory / "compile.stderr")
        else:
            result = run_process_tree_timeout(command, cwd=ROOT, env=env, timeout=400,
                                              evidence=directory / "compile")
        compile_finished = True
        (directory / "compile.stdout").write_text(result.stdout, encoding="utf-8")
        (directory / "compile.stderr").write_text(result.stderr, encoding="utf-8")
        assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
        assert binary.is_file(), "compiler did not emit an executable"
        if owner == "pcc0":
            host = json.loads(host_receipt.read_text())
            assert host["implementation_sha256"] == digest(ROOT / "pcc/frontends/python/pipeline.py")
            receipt["host_implementation"] = host
        receipt["binary_sha256"] = digest(binary)
        for backend, mode, expected in cases:
            argv = [str(binary), *mode]
            row = {"command": argv, "requested_gc": backend, "outcome": "RUNNING"}
            receipt["runs"].append(row)
            save(directory / "receipt.json", receipt)
            try:
                result = run_process_group_timeout(argv, cwd=ROOT,
                                                   env=dict(env, PCC_GC_BACKEND=str(backend)), timeout=20)
            except subprocess.TimeoutExpired as error:
                row.update(timeout_fields(error))
                raise
            row.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr,
                       outcome="returned")
            save(directory / "receipt.json", receipt)
            check_output(result, expected, backend)
            row["observed_gc"] = backend
        assert digest(binary) == receipt["binary_sha256"]
        assert digest(command[0]) == receipt["compiler_sha256"]
        assert digest(runtime) == receipt["runtime_sha256"]
        admitted_stage1(ROOT, manifest_path, runtime)
        receipt["status"] = "PASS"
    except BaseException as error:
        if isinstance(error, subprocess.TimeoutExpired) and not compile_finished:
            save_timeout_output(directory, error)
        receipt.update(status="FAIL", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        save(directory / "receipt.json", receipt)
