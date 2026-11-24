"""Native float conversion preserves receiver ownership and exception cleanup."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest

from tests.python.owned_regression_support import explicit_owned_runtime
from tests.python.process_timeout import run_process_group_timeout


MODES = ("direct", "threaded")
EVENTS = [
    "callback-enter", "nested", "callback-exit",
    "invalid-return", "cleanup-enter", "nested", "cleanup-exit",
    "receiver-finalized", "outer-preserved",
]
PROGRAM = """\
import gc
import sys
from threading import Thread

converter = float
events = []
errors = []
counts = [0, 0]
completed = []

class Nested:
    def __float__(self):
        events.append("nested")
        return 7.25

class Number:
    def __float__(self):
        events.append("callback-enter")
        assert converter(Nested()) == 7.25
        events.append("callback-exit")
        return 3.5

class InvalidReturn:
    def __del__(self):
        counts[0] += 1
        events.append("cleanup-enter")
        if counts[1] != 0:
            errors.append("receiver finalized before invalid result cleanup")
        if converter(Nested()) != 7.25:
            errors.append("nested conversion during cleanup")
        events.append("cleanup-exit")

class BadNumber:
    def __float__(self):
        events.append("invalid-return")
        return InvalidReturn()
    def __del__(self):
        counts[1] += 1
        events.append("receiver-finalized")

def reraiser():
    raise

def conversions():
    try:
        raise ValueError("outer-marker")
    except ValueError:
        assert converter(Number()) == 3.5
        caught = False
        try:
            converter(BadNumber())
        except TypeError:
            caught = True
        assert caught
        gc.collect()
        try:
            reraiser()
        except ValueError as error:
            assert str(error) == "outer-marker"
            events.append("outer-preserved")
        else:
            assert False
    completed.append(1)

def worker():
    try:
        conversions()
    except BaseException as error:
        errors.append(type(error).__name__ + ": " + str(error))

def main():
    mode = sys.argv[1]
    gc.disable()
    try:
        if mode == "direct":
            worker()
        else:
            assert mode == "threaded"
            thread = Thread(target=worker)
            thread.start()
            thread.join()
        assert errors == [], errors
        assert completed == [1]
        assert counts == [1, 1]
        before = len(events)
        gc.collect()
        assert counts == [1, 1] and len(events) == before
        for event in events:
            print(event)
        print("FLOAT_CALLBACK_CLEANUP_OK", mode)
    finally:
        gc.enable()
main()
"""


def _expected(mode):
    return "\n".join([*EVENTS, f"FLOAT_CALLBACK_CLEANUP_OK {mode}"]) + "\n"


def _record_float_execution(receipt, current):
    executions = receipt["executions"]
    assert len(executions) < len(MODES)
    assert current["execution_mode"] == MODES[len(executions)]
    executions.append(dict(current))
    if any(row["status"] != "PASS" for row in executions):
        receipt["status"] = "NATIVE_EXECUTION_FAILED"
    elif len(executions) == len(MODES):
        receipt["status"] = "PASS"
    else:
        receipt["status"] = "RUNNING"


@pytest.mark.integration
@pytest.mark.parametrize("python_program_compiler", ("pcc0", "pcc1"), indirect=True)
def test_canonical_float_callback_cleanup(
    python_program_compiler, request, explicit_owned_runtime, tmp_path, capfd,
):
    source = tmp_path / "float_callback_cleanup.py"
    binary = tmp_path / "float_callback_cleanup.out"
    source.write_text(PROGRAM, encoding="utf-8")
    mode = request.node.callspec.params["python_program_compiler"]
    if python_program_compiler.__module__ == "tests.pcc1_route":
        mode = "pcc1-routed"
    receipt = {
        "status": "RUNNING", "compiler_mode": mode, "executions": [],
        "backend": "self", "libpython": "off",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "runtime_archive": str(explicit_owned_runtime),
        "runtime_sha256": hashlib.sha256(explicit_owned_runtime.read_bytes()).hexdigest(),
    }
    receipt_path = tmp_path / "float-callback-cleanup.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    try:
        python_program_compiler(
            str(source), str(binary), backend="self",
            ir_scaffold_mode="on", runtime_archive=str(explicit_owned_runtime),
        )
    except Exception as error:
        receipt.update(status="COMPILE_FAILED", error=type(error).__name__ + ": " + str(error))
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        raise
    finally:
        captured = capfd.readouterr()
        (tmp_path / "compiler.stdout").write_text(captured.out)
        (tmp_path / "compiler.stderr").write_text(captured.err)
    assert binary.is_file()
    receipt["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    failures = []
    for scenario in MODES:
        reference = subprocess.run(
            [sys.executable, str(source), scenario], capture_output=True, text=True, timeout=20,
        )
        environment = dict(
            os.environ, PATH="", PCC_HOST_PYTHON="/nonexistent/host-python",
            PCC_HOST_PCC="/nonexistent/host-pcc", PCC_TEST_NO_NATIVE_PROVISIONING="1",
            PCC_NO_AUTO_PCC1="1",
        )
        result = run_process_group_timeout([str(binary), scenario], env=environment, timeout=20)
        (tmp_path / f"float-callback-{scenario}.stdout").write_text(result.stdout)
        (tmp_path / f"float-callback-{scenario}.stderr").write_text(result.stderr)
        expected = (0, _expected(scenario), "")
        success = (
            (reference.returncode, reference.stdout, reference.stderr) == expected
            and (result.returncode, result.stdout, result.stderr) == expected
        )
        current = dict(
            execution_mode=scenario, returncode=result.returncode,
            stdout=result.stdout, stderr=result.stderr, reference_stdout=reference.stdout,
            reference_stderr=reference.stderr, reference_returncode=reference.returncode,
            status="PASS" if success else "NATIVE_EXECUTION_FAILED",
        )
        _record_float_execution(receipt, current)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        if not success:
            failures.append(current)
    assert not failures, failures
