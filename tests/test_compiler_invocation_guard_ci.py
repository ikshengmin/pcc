"""Keep invocation containment covered without starting native bootstrap CI."""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
import re
import shlex
import textwrap
import tomllib
from xml.etree import ElementTree

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ".github/workflows/compiler-invocation-guard.yml"
WORKFLOW = (ROOT / WORKFLOW_PATH).read_text(encoding="utf-8")
GUARD_TEST = "tests/python/test_compiler_invocation_guard.py"
SAMPLER_TESTS = (
    "tests/python/test_process_tree_sample_tool.py",
    "tests/python/test_process_tree_manifest_evidence.py",
)
CONTROL_TEST = "tests/test_compiler_invocation_guard_ci.py"


def _step(name):
    return WORKFLOW.split(f"      - name: {name}\n", 1)[1].split("\n      - ", 1)[0]


def _event_paths(workflow, event):
    block = workflow.split(f"  {event}:\n", 1)[1].split("\n  pull_request:", 1)[0]
    block = block.split("\npermissions:", 1)[0]
    return re.findall(r'^      - "([^"]+)"$', block, re.MULTILINE)


@pytest.mark.parametrize("event", ("push", "pull_request"))
def test_host_workflow_tracks_guard_helpers_and_test_configuration(event):
    paths = _event_paths(WORKFLOW, event)
    assert len(paths) == len(set(paths))
    assert set(paths) == {
        "scripts/compiler_invocation_guard.py",
        "scripts/run_process_tree_sample.py",
        "scripts/run_pcc_compile_ab.py",
        GUARD_TEST,
        *SAMPLER_TESTS,
        CONTROL_TEST,
        "conftest.py",
        "tests/conftest.py",
        "tests/xdist_cpu_budget.py",
        "scripts/pytest_live_report.py",
        "tests/native_provisioning.py",
        "tests/runtime_build_cache.py",
        "tests/runtime_fixture_provenance.py",
        "run.py",
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        WORKFLOW_PATH,
    }
    assert "    branches: [master]\n" in WORKFLOW.split("  pull_request:", 1)[0]


def test_host_workflow_uses_normal_pytest_scheduling_and_resource_controls():
    options = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    assert shlex.split(options["addopts"]) == ["-n", "6", "--dist=loadgroup", "-m", "not integration"]
    step = _step("Run guard regressions with the normal six-worker pytest default")
    command = shlex.split(step.split("        run: |\n", 1)[1].replace("\\\n", ""))
    assert command == [
        "env", "-u", "LC_ALL", "uv", "run", "--frozen", "--no-sync", "--python", "3.13",
        "python", "-m", "pytest", "-x", "-vv", "--tb=short",
        "--junitxml=build/compiler-invocation-guard.xml", GUARD_TEST, *SAMPLER_TESTS, CONTROL_TEST,
    ]
    assert "        timeout-minutes: 2\n" in step
    assert "--noconftest" not in WORKFLOW
    assert "--confcutdir" not in WORKFLOW
    assert 'PCC_TEST_NO_NATIVE_PROVISIONING: "1"' in WORKFLOW
    assert 'PCC_NO_AUTO_PCC1: "1"' in WORKFLOW
    assert 'PCC_PYTEST_AUTO_CLEAN: "0"' in WORKFLOW


def test_host_workflow_has_no_native_build_or_duplicate_stage1_trigger():
    jobs = WORKFLOW.split("\njobs:\n", 1)[1]
    assert re.findall(r"^  ([a-zA-Z0-9_-]+):$", jobs, re.MULTILINE) == ["linux-host"]
    assert "    runs-on: ubuntu-24.04\n" in jobs
    assert "    timeout-minutes: 10\n" in jobs
    install = _step("Install host test dependencies without the native build hook")
    assert install.strip() == "run: env -u LC_ALL uv sync --frozen --dev --no-install-project --python 3.13"
    checkout = jobs.split("      - uses: actions/checkout@v4\n", 1)[1].split("\n      - ", 1)[0]
    assert checkout.split("          sparse-checkout: |\n", 1)[1].split() == [
        ".github/workflows", "pcc", "utils", "scripts", "tests",
    ]
    parity = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    for event in ("push", "pull_request"):
        for path in (WORKFLOW_PATH, GUARD_TEST, CONTROL_TEST, "scripts/compiler_invocation_guard.py"):
            assert not any(fnmatchcase(path, pattern) for pattern in _event_paths(parity, event))
    for command in ("scripts/bootstrap.py", "ci_worker_size_prior_gate.py", "python -m build"):
        assert command not in jobs


def test_host_workflow_requires_real_pidfd_support():
    step = _step("Require working Linux process handles")
    assert "shell: env -u LC_ALL uv run --frozen --no-sync --python 3.13 python {0}" in step
    probe = textwrap.dedent(step.split("        run: |\n", 1)[1])
    assert "os.pidfd_open(os.getpid(), 0)" in probe
    assert "signal.pidfd_send_signal(descriptor, 0)" in probe
    assert "finally:\n    os.close(descriptor)" in probe


@pytest.mark.parametrize("result", ("pass", "skipped", "deselected", "empty", "missing_sampler", "missing_manifests"))
def test_host_workflow_receipt_check_rejects_missing_or_skipped_guard_tests(monkeypatch, result):
    suite = ElementTree.Element("testsuite")
    if result != "empty":
        ElementTree.SubElement(suite, "testcase", classname="tests.test_compiler_invocation_guard_ci")
    if result in ("pass", "skipped", "missing_sampler", "missing_manifests"):
        case = ElementTree.SubElement(suite, "testcase", classname="tests.python.test_compiler_invocation_guard")
        if result == "skipped":
            ElementTree.SubElement(case, "skipped")
    for index, path in enumerate(SAMPLER_TESTS):
        if result in ("pass", "skipped") or (result == "missing_sampler" and index == 1) or (result == "missing_manifests" and index == 0):
            ElementTree.SubElement(suite, "testcase", classname=path[:-3].replace("/", "."))
    monkeypatch.setattr(ElementTree, "parse", lambda path: ElementTree.ElementTree(suite))
    step = _step("Require executed guard tests without skips")
    check = textwrap.dedent(step.split("        run: |\n", 1)[1])
    if result == "pass":
        exec(check, {})
    else:
        with pytest.raises(AssertionError, match="guard tests were not executed|must not skip tests"):
            exec(check, {})
