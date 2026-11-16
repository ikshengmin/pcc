"""Owned execution of committed high-water retention, without RSS inflation."""

import hashlib
import json
import os
import sys

import pytest

from tests.python.owned_regression_support import explicit_owned_runtime
from tests.python.process_timeout import run_process_group_timeout


PROGRAM = r'''
import os
import sys
from pcc.frontends.python import pipeline_frontend_workers as workers
from pcc.frontends.python import worker_resource_plan as policy


def write_report(path, pid, token, peak):
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(policy.RESOURCE_REPORT_SCHEMA + "\n" + str(pid)
                     + "\nseeded\n1\n" + str(peak) + "\n" + token + "\n")


def main():
    assert sys.implementation.name == "pcc"
    collector = workers._worker_collector()
    assert collector == int(os.environ["PCC_GC_BACKEND"])
    path = sys.argv[1] + "/rss"
    pid = os.getpid()
    token = "first-attempt"
    os.environ[policy.RESOURCE_REPORT_ENV] = path
    os.environ[policy.RESOURCE_TOKEN_ENV] = token
    assert policy._previous_worker_resource_peak(path, pid, token) == 0
    policy.publish_worker_resource("started")
    first = policy.read_worker_resource(path, pid, token)
    assert first is not None and first[0] == "started" and 0 < first[1] <= first[2]
    # A controlled prior protocol value, not a claim to have allocated this
    # memory. No scheduler/guard consumes this private component-test file.
    seeded_peak = 1 << 50
    assert first[2] < seeded_peak
    write_report(path, pid, token, seeded_peak)
    policy.publish_worker_resource("complete")
    final = policy.read_worker_resource(path, pid, token)
    assert final is not None and final[0] == "complete"
    assert 0 < final[1] < seeded_peak and final[2] == seeded_peak
    assert policy._previous_worker_resource_peak(path, pid + 1, token) == 0
    assert policy._previous_worker_resource_peak(path, pid, "next-attempt") == 0
    os.environ[policy.RESOURCE_TOKEN_ENV] = "next-attempt"
    policy.publish_worker_resource("started")
    following = policy.read_worker_resource(path, pid, "next-attempt")
    assert following is not None and 0 < following[1] <= following[2] < seeded_peak
    with open(path, "w", encoding="utf-8") as stream:
        stream.write("broken\n")
    rejected = False
    try:
        policy.publish_worker_resource("complete")
    except policy.WorkerMemoryError:
        rejected = True
    assert rejected
    with open(path, "r", encoding="utf-8") as stream:
        assert stream.read() == "broken\n"
    print("HIGH_WATER_OK " + str(collector))


main()
'''


@pytest.mark.integration
def test_committed_high_water_native_five_collectors(
    tmp_path, explicit_owned_runtime, python_program_compiler, request,
):
    source = tmp_path / "high_water.py"
    source.write_text(PROGRAM)
    binary = tmp_path / ("high_water.exe" if sys.platform == "win32" else "high_water.out")
    python_program_compiler(
        str(source), str(binary), backend="self", ir_scaffold_mode="on",
        runtime_archive=str(explicit_owned_runtime),
    )
    content = binary.read_bytes()
    assert (content.startswith(b"MZ") if sys.platform == "win32" else
            content.startswith(b"\xcf\xfa\xed\xfe") if sys.platform == "darwin" else
            content.startswith(b"\x7fELF"))
    receipt = {
        "compiler_parameter": request.node.callspec.params["python_program_compiler"],
        "compiler_module": python_program_compiler.__module__,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "binary_sha256": hashlib.sha256(content).hexdigest(),
        "runtime_archive_sha256": hashlib.sha256(explicit_owned_runtime.read_bytes()).hexdigest(),
        "scope": "controlled prior-report retention; not a physical RSS measurement",
        "executions": [],
    }
    receipt_path = tmp_path / "high-water-execution.json"
    for collector in range(5):
        directory = tmp_path / ("gc" + str(collector))
        directory.mkdir()
        environment = os.environ.copy()
        environment.update({
            "PCC_GC_BACKEND": str(collector), "PATH": "",
            "PCC_HOST_PYTHON": "/nonexistent/host-python",
            "PCC_HOST_PCC": "/nonexistent/host-pcc",
            "PCC_RUNTIME_CC": "/nonexistent/runtime-compiler",
            "PCC_TEST_NO_NATIVE_PROVISIONING": "1", "PCC_NO_AUTO_PCC1": "1",
        })
        result = run_process_group_timeout([str(binary), str(directory)], env=environment, timeout=20)
        receipt["executions"].append({
            "collector": collector, "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr,
        })
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        assert (result.returncode, result.stdout, result.stderr) == (
            0, "HIGH_WATER_OK " + str(collector) + "\n", "",
        ), receipt_path.read_text()
