"""Real host and native prior scheduling; run under a hard tree watchdog."""

import hashlib
import json
import os
from pathlib import Path
import platform
import site
import sys

import pytest

from pcc.frontends.python import worker_resource_plan as policy
from tests.python.owned_regression_support import explicit_owned_runtime
from tests.python.process_timeout import run_process_group_timeout


ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "tests/fixtures/native/worker_size_priors.py"
CASES = ("width", "growth")


def _fixture_failure_evidence(directory):
    # Only this synthetic fixture's bounded protocol files are copied into
    # execution.json, which the existing CI artifact pattern already keeps.
    # Tokens here identify synthetic attempts, never user credentials.
    names = ["budget.tsv", "admission.tsv", "peer-report-ready", "windows-stage1-phase"]
    for index in range(3):
        names.extend(["rss" + str(index), "attempts" + str(index)])
        for attempt in (1, 2):
            suffix = str(index) + "." + str(attempt)
            names.extend(["started." + suffix, "complete." + suffix])
    evidence = {}
    for name in names:
        try:
            with (directory / name).open("r", encoding="utf-8", errors="replace") as stream:
                text = stream.read(8193)
            evidence[name] = {"text": text[:8192], "truncated": len(text) > 8192}
        except FileNotFoundError:
            continue
        except OSError as error:
            evidence[name] = {"read_error": type(error).__name__ + ": " + str(error)}
    return evidence


def _environment(owner, collector):
    environment = dict(os.environ)
    environment.pop("PCC_TEST_WINDOWS_STAGE1_PHASE", None)
    # Keep the outer watchdog's budget. As in the existing resource driver,
    # its synchronized state is not falsely presented as this child's owner
    # state; this component uses the policy's local driver/worker accounting.
    assert int(environment.get("PCC_WORKER_TREE_BUDGET_BYTES", "0")) > 0
    for key in (policy.TREE_STATE_ENV, policy.RESOURCE_REPORT_ENV, policy.RESOURCE_TOKEN_ENV):
        environment.pop(key, None)
    environment.update({
        "PCC_TEST_RESOURCE_OWNER": owner,
        "PCC_GC_BACKEND": str(collector),
        "PCC_PY_FRONTEND_WORKER_TIMING": "0",
        "PYTHONPATH": str(ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PCC_TEST_NO_NATIVE_PROVISIONING": "1",
        "PCC_NO_AUTO_PCC1": "1",
    })
    if owner == "pcc":
        environment.update({
            "PATH": "", "PCC_HOST_PYTHON": "/nonexistent/host-python",
            "PCC_HOST_PCC": "/nonexistent/host-pcc",
            "PCC_RUNTIME_CC": "/nonexistent/runtime-compiler",
        })
    return environment


def _run(prefix, case, directory, owner, collector, compiler_parameter=None):
    directory.mkdir()
    environment = _environment(owner, collector)
    windows_phase = (sys.platform == "win32" and compiler_parameter == "pcc0"
                     and owner == "pcc" and collector == 0 and case == "width")
    if windows_phase:
        environment["PCC_TEST_WINDOWS_STAGE1_PHASE"] = "1"
    command = [*prefix, case, str(directory)]
    result = run_process_group_timeout(command, env=environment, timeout=20)
    (directory / "stdout").write_text(result.stdout)
    (directory / "stderr").write_text(result.stderr)
    record = {
        "owner": owner, "requested_collector": collector, "case": case,
        "command": command, "returncode": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr,
        "outer_tree_budget_bytes": int(environment["PCC_WORKER_TREE_BUDGET_BYTES"]),
        "accounting_scope": "local_driver_and_workers_under_outer_tree_watchdog",
        "native_execution": owner == "pcc", "pcc1_stage2_dispatch": False,
        "windows_stage1_phase_witness": windows_phase,
    }
    if (result.returncode, result.stdout, result.stderr) != (
        0, "WORKER_SIZE_PRIOR_OK " + case + "\n", "",
    ):
        record["failure_evidence"] = _fixture_failure_evidence(directory)
    (directory / "execution.json").write_text(json.dumps(record, indent=2) + "\n")
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "WORKER_SIZE_PRIOR_OK " + case + "\n", "",
    ), record
    assert (directory / "complete").read_text() == owner + "\t" + str(collector)
    expected_attempts = 3 if case == "width" else 4
    starts = sorted(directory.glob("started.*"))
    assert len(starts) == expected_attempts
    admissions = [line.split("\t") for line in (directory / "admission.tsv").read_text().splitlines()
                  if line.startswith("start\t")]
    assert len(admissions) == expected_attempts
    actual_collectors = []
    pids, tokens = [], []
    for path in starts:
        fields = path.read_text().split("\t")
        assert len(fields) == 4 and fields[2] == owner
        pids.append(int(fields[0]))
        tokens.append(fields[1])
        actual_collectors.append(int(fields[3]))
        _, index, attempt = path.name.split(".")
        launched = [row for row in admissions if row[1] == index]
        assert int(launched[int(attempt) - 1][2]) == int(fields[0]), "launcher PID differs from worker PID"
    assert len(set(pids)) == expected_attempts and len(set(tokens)) == expected_attempts
    assert actual_collectors == [collector] * expected_attempts
    record["observed_child_collectors"] = actual_collectors
    record["child_pids"] = pids
    record["status"] = "PASS"
    (directory / "execution.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


@pytest.mark.parametrize("case", CASES)
@pytest.mark.pcc_gate(env="PCC_WORKER_TREE_BUDGET_BYTES")
def test_size_prior_real_host_processes(case, tmp_path):
    _run([sys.executable, "-B", str(DRIVER)], case, tmp_path / case, "cpython", 0)


@pytest.mark.pcc_gate(env="PCC_WORKER_TREE_BUDGET_BYTES")
def test_resource_host_self_spawn_preserves_interpreter_identity_and_venv(tmp_path):
    script = tmp_path / "host_identity.py"
    script.write_text('''
import json
import os
from pathlib import Path
import shlex
import site
import sys

import pytest
from pcc.frontends.python import worker_process_pool as pool


def main():
    root, depth = Path(sys.argv[1]), int(sys.argv[2])
    sites = [os.path.normcase(path) for path in site.getsitepackages()]
    record = {
        "pid": os.getpid(), "executable": sys.executable,
        "base_executable": sys._base_executable,
        "prefix": sys.prefix, "base_prefix": sys.base_prefix,
        "site_packages": [path for path in sys.path if os.path.normcase(path) in sites],
        "pytest_origin": pytest.__file__,
        "environment_value": os.environ["PCC_TEST_ENVIRONMENT_VALUE"],
    }
    if depth < 2:
        command = shlex.join([sys.executable, "-B", __file__, str(root), str(depth + 1)])
        pid = pool._start_resource_worker([pool._command_spec(command)], 0)
        record["child_pid"] = pid
        try:
            assert pool._HOST_WORKERS[pid].wait(timeout=6) == 0
            pool._retire_resource_worker(pid)
        finally:
            if pid in pool._HOST_WORKERS:
                pool._stop_resource_worker(pid)
        assert pool._HOST_WORKERS == {}
    (root / ("identity." + str(depth) + ".json")).write_text(json.dumps(record))


main()
''', encoding="utf-8")
    environment = _environment("cpython", 0)
    environment["PCC_TEST_ENVIRONMENT_VALUE"] = "unchanged= value"
    result = run_process_group_timeout(
        [sys.executable, "-B", str(script), str(tmp_path), "0"],
        env=environment, timeout=20,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result
    identities = [json.loads((tmp_path / ("identity." + str(depth) + ".json")).read_text())
                  for depth in range(3)]
    sites = [os.path.normcase(path) for path in site.getsitepackages()]
    expected = {
        "executable": sys.executable, "base_executable": sys._base_executable,
        "prefix": sys.prefix, "base_prefix": sys.base_prefix,
        "site_packages": [path for path in sys.path if os.path.normcase(path) in sites],
        "pytest_origin": pytest.__file__, "environment_value": "unchanged= value",
    }
    assert expected["site_packages"], "host dependency path must be exercised"
    assert len({record["pid"] for record in identities}) == 3
    for depth, record in enumerate(identities):
        assert {key: record[key] for key in expected} == expected
        if depth < 2:
            assert record["child_pid"] == identities[depth + 1]["pid"]


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=lambda: (
    (sys.platform.startswith("linux") and platform.machine().lower() in ("x86_64", "amd64", "aarch64", "arm64"))
    or (sys.platform == "darwin" and platform.machine().lower() == "arm64")
    or (sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64"))
))
def test_size_prior_native_processes_five_collectors(
    tmp_path, explicit_owned_runtime, python_program_compiler, request, capfd,
):
    """Execute the actual policy and same-binary children on each host target.

    Runtime construction and compiler provisioning are forbidden by the shared
    fixture. This proves a native resource component, not pcc1 Stage2 dispatch.
    Observed collector IDs come from the owned ABI in every actual child.
    """
    binary = tmp_path / ("prior-worker.exe" if sys.platform == "win32" else "prior-worker.out")
    receipt_path = tmp_path / "native-worker-size-priors.json"
    receipt = {
        "status": "COMPILING", "platform": sys.platform,
        "machine": platform.machine(),
        "compiler_parameter": request.node.callspec.params["python_program_compiler"],
        "compiler_module": python_program_compiler.__module__,
        "backend": "self", "libpython": "off", "ir_scaffold": "on",
        "source_sha256": hashlib.sha256(DRIVER.read_bytes()).hexdigest(),
        "runtime_archive_sha256": hashlib.sha256(explicit_owned_runtime.read_bytes()).hexdigest(),
        "scope": "native prior/LPT resource component with real self-spawned children",
        "pcc1_stage2_dispatch": False, "executions": [],
    }

    def save():
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

    save()
    try:
        python_program_compiler(
            str(DRIVER), str(binary), backend="self", libpython_mode="off",
            ir_scaffold_mode="on", runtime_archive=str(explicit_owned_runtime),
        )
    except Exception as error:
        receipt["status"] = "COMPILE_FAILED"
        receipt["error"] = type(error).__name__ + ": " + str(error)
        save()
        raise
    finally:
        captured = capfd.readouterr()
        (tmp_path / "compile.stdout").write_text(captured.out)
        (tmp_path / "compile.stderr").write_text(captured.err)
    contents = binary.read_bytes()
    assert (contents.startswith(b"MZ") if sys.platform == "win32" else
            contents.startswith(b"\xcf\xfa\xed\xfe") if sys.platform == "darwin" else
            contents.startswith(b"\x7fELF")), "unexpected native executable format"
    receipt["binary_sha256"] = hashlib.sha256(contents).hexdigest()
    receipt["status"] = "RUNNING"
    save()
    for collector in range(5):
        for case in CASES:
            try:
                result = _run(
                    [str(binary)], case, tmp_path / ("gc" + str(collector) + "-" + case),
                    "pcc", collector, request.node.callspec.params["python_program_compiler"],
                )
            except BaseException as error:
                receipt["status"] = "NATIVE_EXECUTION_FAILED"
                receipt["failed_case"] = case
                receipt["failed_collector"] = collector
                receipt["error"] = type(error).__name__ + ": " + str(error)
                save()
                raise
            receipt["executions"].append(result)
            save()
    assert len(receipt["executions"]) == 10
    receipt["status"] = "PASS"
    save()
