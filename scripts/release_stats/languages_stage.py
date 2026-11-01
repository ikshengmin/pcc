"""C, C++, Go, Java and Rust: official C suites and the language test suites.

Official C suites (``projects/``), compiled and run by pcc alone with the
same owned path the C tests use (``tests/*_cases.py``; no cc, no clang):

- c-testsuite: a case passes when it exits 0 and prints its ``.expected``;
- gcc torture execute: a case passes when it exits 0 (failures call abort).

For C++, Go, Java and Rust there is no official conformance suite in the
repository yet. Their own test suites (``tests/<language>``) run as the
status page prescribes, ordinary and integration selections separately; the
counts are reported as the bounded language subsets they are, together with
the platform, because the native-execution adapters of some selections
require Linux x86-64.
"""

from __future__ import annotations

import concurrent.futures
import os
import platform
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from common import REPO, run_logged


LANGUAGES = ("cpp", "go", "java", "rust")
C_SUITES = {
    "c-testsuite": REPO / "projects" / "c-testsuite",
    "gcc-torture-execute": REPO / "projects" / "gcc-torture-execute",
}


def _c_case(suite: str, case: Path, timeout: int) -> dict:
    if suite == "c-testsuite":
        from tests.c_testsuite_cases import read_expected_output, run_pcc
    else:
        from tests.gcc_torture_cases import run_pcc
    outcome = run_pcc(case, REPO, timeout)
    passed = outcome.returncode == 0
    if passed and suite == "c-testsuite":
        passed = outcome.stdout == read_expected_output(case)
    stage = next((item.stage for item in outcome.stages if item.returncode != 0), "")
    return {"case": case.name, "passed": passed, "returncode": outcome.returncode,
            "failed_stage": "" if passed else stage}


def run_c_suites(options, stage_dir: Path) -> dict:
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    suites = {}
    for suite, directory in C_SUITES.items():
        cases = sorted(directory.glob("*.c"))
        rows = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=options.jobs) as pool:
            futures = [pool.submit(_c_case, suite, case, options.c_case_timeout)
                       for case in cases]
            for future in concurrent.futures.as_completed(futures):
                rows.append(future.result())
        rows.sort(key=lambda row: row["case"])
        failed_stages: dict[str, int] = {}
        for row in rows:
            if not row["passed"]:
                failed_stages[row["failed_stage"] or "output"] = (
                    failed_stages.get(row["failed_stage"] or "output", 0) + 1)
        suites[suite] = {
            "cases": len(rows),
            "passed": sum(1 for row in rows if row["passed"]),
            "failed_by_stage": failed_stages,
            "failed_cases": [row["case"] for row in rows if not row["passed"]],
        }
        print(f"{suite}: {suites[suite]['passed']} / {len(rows)}", flush=True)
    return suites


def _junit_counts(path: Path) -> dict:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    if not path.is_file():
        return counts
    for case in ElementTree.parse(path).getroot().iter("testcase"):
        if case.find("failure") is not None:
            counts["failed"] += 1
        elif case.find("error") is not None:
            counts["errors"] += 1
        elif case.find("skipped") is not None:
            counts["skipped"] += 1
        else:
            counts["passed"] += 1
    return counts


def run_language_suites(options, stage_dir: Path) -> dict:
    languages = {}
    environment = dict(os.environ, PCC_NO_AUTO_PCC1="1")
    for language in LANGUAGES:
        entry = {}
        for lane, marker in (("ordinary", "not integration"), ("integration", "integration")):
            junit = stage_dir / f"{language}-{lane}.xml"
            run_logged(
                [sys.executable, "-m", "pytest", "-x", "--maxfail=0", "-q",
                 f"tests/{language}", "-m", marker, f"--junitxml={junit}"],
                log=stage_dir / f"{language}-{lane}.log", timeout=options.suite_timeout,
                cwd=REPO, env=environment,
            )
            entry[lane] = _junit_counts(junit)
        languages[language] = entry
        print(f"tests/{language}: {entry}", flush=True)
    return languages


def run(options, out: Path, identity: dict) -> dict:
    stage_dir = out / "languages"
    stage_dir.mkdir(parents=True, exist_ok=True)
    return {
        "stage": "languages",
        "identity": identity,
        "platform": f"{platform.system()} {platform.machine()}",
        "c_suites": run_c_suites(options, stage_dir),
        "language_suites": run_language_suites(options, stage_dir),
    }
