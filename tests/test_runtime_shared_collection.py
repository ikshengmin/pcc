"""Regress same-basename collection with the real pytest prepend importer.

The controlled child tree has no compiler imports or repository conftests.
Both old and fixed shapes use six workers and the same 90-second/1-GiB guard.
"""

import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _run_collection_shape(directory, packaged, reverse):
    tests = directory / "tests"
    go = tests / "go"
    shared = tests / "runtime_shared"
    go.mkdir(parents=True)
    shared.mkdir()
    # Copy the actual package boundaries, not a hand-written replacement.
    (tests / "__init__.py").write_bytes((ROOT / "tests/__init__.py").read_bytes())
    if packaged:
        (shared / "__init__.py").write_bytes(
            (ROOT / "tests/runtime_shared/__init__.py").read_bytes()
        )
    for parent, expected in (
        (go, "test_managed_contracts"),
        (shared, "tests.runtime_shared.test_managed_contracts" if packaged
         else "test_managed_contracts"),
    ):
        (parent / "test_managed_contracts.py").write_text(
            "def test_namespace():\n"
            f"    assert __name__ == {expected!r}\n",
            encoding="utf-8",
        )
    config = directory / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    report = directory / "junit.xml"
    paths = ["tests/go/test_managed_contracts.py",
             "tests/runtime_shared/test_managed_contracts.py"]
    if reverse:
        paths.reverse()
    command = [sys.executable, "-m", "pytest", "-x", "-vv", "--tb=short",
               "-p", "xdist.plugin", "-n", "6", "--dist=loadgroup",
               "--import-mode=prepend", "-c", str(config),
               "--confcutdir", str(directory), "--junitxml", str(report), *paths]
    env = dict(os.environ)
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH",
                 "PY_IGNORE_IMPORTMISMATCH"):
        env.pop(name, None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    log = directory / "target.stdout"
    if sys.platform == "win32":
        from scripts.platform_process_watchdog import run

        try:
            run(command, cwd=directory, env=env, log_path=log,
                timeout=90, rss_limit=1073741824)
            returncode = 0
        except subprocess.CalledProcessError as error:
            returncode = error.returncode
    else:
        guarded = [sys.executable, str(ROOT / "scripts/run_process_tree_sample.py"),
                   "--result", str(directory / "result.json"),
                   "--samples", str(directory / "samples.tsv"),
                   "--stdout", str(log), "--stderr", str(directory / "target.stderr"),
                   "--cwd", str(directory), "--timeout", "90",
                   "--max-tree-rss-bytes", "1073741824", "--no-performance-lock",
                   "--", *command]
        returncode = subprocess.run(guarded, cwd=directory, env=env, check=False).returncode
    output = log.read_text(encoding="utf-8")
    assert "created: 6/6 workers" in output, output
    return returncode, output, ET.parse(report).getroot()


@pytest.mark.xdist_group(name="runtime-shared-collection")
@pytest.mark.parametrize("reverse", [False, True], ids=["go-first", "shared-first"])
def test_package_boundary_fixes_real_same_basename_collection(tmp_path, reverse):
    code, output, report = _run_collection_shape(tmp_path / "before", False, reverse)
    assert code == 2, output
    assert "import file mismatch" in output, output
    assert list(report.iter("error")), output

    code, output, report = _run_collection_shape(tmp_path / "after", True, reverse)
    assert code == 0, output
    assert "import file mismatch" not in output, output
    assert not list(report.iter("error")) and not list(report.iter("failure")), output
    assert not list(report.iter("skipped")), output
    cases = list(report.iter("testcase"))
    assert len(cases) == 2, output
    assert {case.get("classname") for case in cases} == {
        "tests.go.test_managed_contracts", "tests.runtime_shared.test_managed_contracts",
    }, output
