"""CI entry for the unchanged fallible-streaming-iterator 0.1.9 project.

This owned-source entry never runs cargo/rustc or executes upstream CI files.
Successful reporting of a blocker is a failing project exit, not acceptance.
Actual same-original certification/reference tests are a separate explicit lane.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))

from scripts.ci_source_frontier_support import (
    diagnostic_status, load_source_lock, materialize_snapshot, run_ci, save,
    source_identity, verify_snapshot,
)

FIXTURE = ROOT / "projects/fallible-streaming-iterator-0.1.9"
REVISION = "9217bc5e381b54b4ef4c38959488a5dc993b7b81"
LOCK_SHA256 = "8f538cfd75a5af8d5737da195bbd99f59ca70846f30860ad962b901404a42428"
EDITION = 2015
TARGET = "x86_64-unknown-linux-gnu"
ORIGINAL_PATHS = frozenset((
    ".circleci/config.yml", ".gitignore", "Cargo.toml", "LICENSE-APACHE",
    "LICENSE-MIT", "README.md", "src/lib.rs",
))
DRIVERS = (
    "scripts/ci_fallible_streaming_iterator.py",
    "scripts/ci_source_frontier_support.py", "scripts/ci_validation_run.py",
    "scripts/run_process_tree_sample.py", "scripts/platform_process_watchdog.py",
    "tests/rust/test_fallible_streaming_iterator_project.py",
    "tests/rust/project_consumers/fallible_streaming_iterator.rs",
)


def load_inputs(directory=FIXTURE):
    lock, files = load_source_lock(directory, LOCK_SHA256)
    if (lock["project"] != "fallible-streaming-iterator" or lock["version"] != "0.1.9"
            or lock["repository"] != "https://github.com/sfackler/fallible-streaming-iterator"
            or lock["revision"] != REVISION or lock["edition"] != EDITION
            or lock["rust_roots"] != ["src/lib.rs"] or lock["features"] != []
            or lock["dependencies"] != [] or lock["complete_upstream_tree"] is not True
            or set(files) != ORIGINAL_PATHS):
        raise ValueError("original project identity or complete upstream source set differs")
    manifest = tomllib.loads(files["Cargo.toml"].decode("utf-8"))
    if (manifest["package"]["name"] != lock["project"]
            or manifest["package"]["version"] != lock["version"]
            or manifest["package"].get("edition", "2015") != str(EDITION)
            or manifest.get("dependencies") != {} or manifest.get("features") != {"std": []}
            or any(name in manifest for name in ("build-dependencies", "dev-dependencies", "workspace", "target"))
            or "build" in manifest["package"] or "build.rs" in files):
        raise ValueError("original manifest no longer describes this dependency-free default-feature crate")
    return lock, files


def unrun(reason):
    return {"status": "UNRUN", "blocked_by": reason}


def initial_report():
    return {
        "schema_version": 1, "project": "fallible-streaming-iterator", "version": "0.1.9",
        "upstream_revision": REVISION, "source_lock_sha256": LOCK_SHA256,
        "edition": EDITION, "features": [], "target": TARGET,
        "complete_upstream_tree": True, "complete_build_closure": False,
        "project_accepted": False, "source_rewritten": False,
        "external_compiler_used": False, "borrow_certified": False,
        "first_blocker": None,
        "source_integrity": unrun("whole original project preflight has not run"),
        "source_parse": unrun("whole original lib.rs has not reached the owned parser"),
        "public_frontend_admission": unrun("source parsing must complete first"),
        "external_reference": unrun("separate audited rustc certification/reference test entry"),
        "owned_object": unrun("requires actual same-original CheckedRustSource and owned semantic support"),
        "owned_core": unrun("original core/prelude closure is not qualified"),
        "owned_package_link": unrun("owned Rust cross-crate compilation/linking is not implemented"),
        "owned_self_test": unrun("whole original crate and separate API consumer must link and execute with PCC"),
        "upstream_self_test": unrun("separate original cfg(test) reference; its two functions are compile checks, not runtime tests"),
        "runner": unrun("bounded CI worker has not run"),
    }


def attempt(report, receipt, phase, operation, diagnostic_type):
    report[phase] = {"status": "RUNNING"}
    save(receipt, report)
    try:
        operation()
    except diagnostic_type as error:
        report[phase] = {"status": diagnostic_status(error), "exception": type(error).__name__,
                         "diagnostic": str(error), "source_path": "src/lib.rs"}
        if report["first_blocker"] is None:
            report["first_blocker"] = {"stage": phase, **report[phase]}
        save(receipt, report)
        return False
    except Exception as error:
        report[phase] = {"status": "FAIL", "exception": type(error).__name__, "diagnostic": str(error)}
        if report["first_blocker"] is None:
            report["first_blocker"] = {"stage": phase, **report[phase]}
        save(receipt, report)
        raise
    report[phase] = {"status": "PASS", "scope": "this source stage only"}
    save(receipt, report)
    return True


def run_worker(directory):
    receipt = directory / "frontier.json"
    report = initial_report()
    save(receipt, report)
    try:
        lock, files = load_inputs()
        before = source_identity(ROOT, DRIVERS)
        snapshot = directory / "source"
        materialize_snapshot(snapshot, files)
    except Exception as error:
        report["source_integrity"] = {"status": "FAIL", "exception": type(error).__name__, "diagnostic": str(error)}
        report["first_blocker"] = {"stage": "source_integrity", **report["source_integrity"]}
        save(receipt, report)
        raise
    report["inputs"] = lock["files"]
    report["pcc_source_sha256"] = before
    report["pcc_revision"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    report["source_integrity"] = {"status": "PASS", "scope": "preflight only",
                                  "files": len(files), "bytes": sum(map(len, files.values()))}
    save(receipt, report)
    try:
        from pcc.frontends.rust import RustError, compile_rust
        from pcc.frontends.rust.syntax import Parser
        source = files["src/lib.rs"].decode("utf-8")
        parsed = attempt(report, receipt, "source_parse", lambda: Parser(
            source, source_path="src/lib.rs", edition=EDITION,
        ).parse(), RustError)
        if parsed:
            # No value-only flag, fabricated token, host library or Cargo fallback.
            attempt(report, receipt, "public_frontend_admission", lambda: compile_rust(
                source, module_name="fallible_streaming_iterator", target_triple=TARGET,
                overflow_checks=False, edition=EDITION,
            ), RustError)
    finally:
        try:
            verify_snapshot(snapshot, files)
            if load_inputs()[1] != files or source_identity(ROOT, DRIVERS) != before:
                raise ValueError("original project or PCC source changed during the attempt")
        except Exception as error:
            report["source_integrity"] = {"status": "FAIL", "exception": type(error).__name__, "diagnostic": str(error)}
            save(receipt, report)
            raise
        report["source_integrity"]["scope"] = "all original bytes and compiler sources identical before and after"
        save(receipt, report)
    report["terminal"] = True
    save(receipt, report)
    return 1  # An incomplete real-project chain is never a green project gate.


def main(argv=None):
    return run_ci(ROOT, FIXTURE, Path(__file__).resolve(), initial_report, run_worker, argv)


if __name__ == "__main__":
    raise SystemExit(main())
