"""CPython's own Lib/test compiled natively by pcc (scripts/cpython_conformance).

The harness runs every Lib/test module under the reference CPython to get the
cases and their results, then compiles the same module natively with pcc
(no libpython) and replays the same cases. The rate is the share of the cases
CPython passes (PASS or expected failure) that also pass natively; a module
that does not compile, crashes or times out keeps all its cases as failures.
Modules whose CPython baseline does not import have no denominator and are
listed separately.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

from common import REPO, read_json, run_logged


def _measured_revision(run_dir: Path, harness_identity: dict) -> str | None:
    """The pcc commit an imported run measured: its harness identity, or the
    first line of ``source-revision.txt`` beside a run made from an exported
    tree (whose harness could not ask Git)."""
    if harness_identity.get("pcc_head"):
        return harness_identity["pcc_head"]
    recorded = run_dir / "source-revision.txt"
    if recorded.is_file():
        return recorded.read_text(encoding="utf-8").split()[0]
    return None


def _summary(run_dir: Path, identity: dict) -> dict:
    summary = read_json(run_dir / "summary.json")
    totals = summary["totals"]
    harness_identity = read_json(run_dir / "identity.json")
    measured = _measured_revision(run_dir, harness_identity)
    if measured:
        identity = dict(identity, pcc={"revision": measured, "source": "harness run",
                                       "modified_paths": None})
    # The run's own completion time, not when this summary was made.
    finished = datetime.datetime.fromtimestamp((run_dir / "summary.json").stat().st_mtime,
                                               datetime.timezone.utc)
    identity = dict(identity, started_utc=finished.isoformat(timespec="seconds"))
    return {
        "stage": "cpython",
        "identity": identity,
        "run_dir": str(run_dir),
        "harness_identity": {
            "key": summary.get("identity"),
            "cpython_describe": harness_identity.get("cpython_describe"),
            "cpython_head": harness_identity.get("cpython_head"),
            "runtime_archive_sha256": harness_identity.get("runtime_archive_sha256"),
            "pcc_head": harness_identity.get("pcc_head"),
        },
        "modules": totals.get("modules"),
        "baseline_cases": totals["baseline_cases"],
        "baseline_success": totals["baseline_success"],
        "native_success": totals["native_success"],
        "rate": summary["compatibility_rate"],
        "modules_without_baseline": len(summary["modules_without_baseline"]),
        "modules_compile_failed": len(summary["modules_compile_failed"]),
        "modules_native_incomplete": len(summary["modules_native_incomplete"]),
        "top_compile_failures": summary["compile_failure_categories"][:10],
        "top_case_failures": summary["case_failure_categories"][:10],
        "best_modules": sorted(
            (row for row in summary["per_module"] if row[2] > 0),
            key=lambda row: (-row[2], row[0]),
        )[:15],
    }


def run(options, out: Path, identity: dict) -> dict:
    if options.cpython_run:
        run_dir = Path(options.cpython_run).expanduser().resolve()
    else:
        if not options.cpython_src:
            raise SystemExit("error: the cpython stage needs --cpython-src or --cpython-run")
        run_dir = out / "cpython-run"
        command = [
            sys.executable, str(REPO / "scripts" / "cpython_conformance" / "run.py"),
            "--cpython-src", str(Path(options.cpython_src).expanduser()),
            "--out", str(run_dir), "--python", options.python,
            "--jobs", str(options.jobs),
        ]
        if options.runtime_archive:
            command += ["--runtime-archive", options.runtime_archive]
        if options.resume and (run_dir / "identity.json").is_file():
            command.append("--resume")
        completed = run_logged(command, log=out / "cpython.log",
                               timeout=options.cpython_timeout, cwd=REPO)
        if completed is None or completed.returncode != 0:
            raise SystemExit(f"error: the CPython Lib/test run failed; see {out / 'cpython.log'}")
    if not (run_dir / "summary.json").is_file():
        raise SystemExit(f"error: {run_dir} has no summary.json")
    return _summary(run_dir, identity)
