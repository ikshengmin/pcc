"""CI-only original orjson Rust/C source frontier, not an extension build/import.

No Cargo, build.rs, procedural macro, external compiler, Python extension or
upstream test is executed here. Owned parsing of locked originals is measured;
all later extension/runtime stages remain explicitly UNRUN.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))

from scripts.ci_source_frontier_support import (
    diagnostic_status,
    load_source_lock,
    materialize_snapshot,
    run_ci,
    save,
    sha256,
    source_identity,
    verify_snapshot,
)

FIXTURE = ROOT / "projects/orjson-3.13.0"
LOCK_SHA256 = "d6497472fb5d57c557a6a96235fe9da9b5b7f9f53dbe60cc2d5ae9aba3355153"
UPSTREAM_REVISION = "bd00937c5c46ab20eb5d7fe1cfc034b906c4578f"
RUST_ROOTS = ("src/lib.rs", "build.rs")
C_UNIT = "include/yyjson/yyjson.c"
TARGET = "x86_64-unknown-linux-gnu"
DRIVERS = (
    "scripts/ci_orjson_source_frontier.py",
    "scripts/ci_source_frontier_support.py",
    "scripts/ci_validation_run.py",
    "scripts/run_process_tree_sample.py",
    "scripts/platform_process_watchdog.py",
)
DEFINES = (
    ("YYJSON_DISABLE_INCR_READER", "1"),
    ("YYJSON_DISABLE_NON_STANDARD", "1"),
    ("YYJSON_DISABLE_UTF8_VALIDATION", "1"),
    ("YYJSON_DISABLE_UTILS", "1"),
    ("YYJSON_DISABLE_WRITER", "1"),
    ("YYJSON_READER_DEPTH_LIMIT", "1024"),
)


def load_inputs(directory=FIXTURE):
    lock, files = load_source_lock(directory, LOCK_SHA256)
    if (lock["project"] != "orjson" or lock["version"] != "3.13.0"
            or lock["revision"] != UPSTREAM_REVISION or lock["edition"] != 2024
            or lock["rust_version"] != "1.95" or len(files) != 15
            or tuple(lock["rust_roots"]) != RUST_ROOTS or lock["c_unit"] != C_UNIT
            or tuple(map(tuple, lock["c_configuration"]["defines"])) != DEFINES):
        raise ValueError("orjson identity or bounded frontier selection differs")
    cargo = tomllib.loads(files["Cargo.toml"].decode())
    python = tomllib.loads(files["pyproject.toml"].decode())
    packages = tomllib.loads(files["Cargo.lock"].decode())["package"]
    versions = {item["name"]: item["version"] for item in packages}
    if (cargo["package"]["name"] != "orjson" or cargo["package"]["version"] != "3.13.0"
            or cargo["package"]["edition"] != "2024" or cargo["package"]["rust-version"] != "1.95"
            or cargo["package"]["license"] != lock["license"]
            or cargo["lib"]["crate-type"] != ["cdylib"]
            or cargo["features"]["default"] != []
            or python["project"]["version"] != "3.13.0"
            or python["project"]["license"] != lock["license"]
            or python["build-system"] != {"build-backend": "maturin", "requires": ["maturin>=1,<2"]}
            or versions["orjson"] != "3.13.0"
            or versions["pyo3-ffi"] != "0.29.3" or versions["pyo3-build-config"] != "0.29.3"
            or "pyo3" in cargo["dependencies"]):
        raise ValueError("original manifest/lock disagrees with extension identity")
    # These are unexecuted source declarations, not a fabricated Cargo result.
    build = files["build.rs"].decode()
    if (tuple(re.findall(r'\.define\("([^"]+)", "([^"]+)"\)', build)) != DEFINES
            or '.file("include/yyjson/yyjson.c")' not in build
            or '.include("include/yyjson")' not in build
            or "pyo3_build_config::get()" not in build
            or 'pyo3_build_config::PythonImplementation::CPython' not in build):
        raise ValueError("original build.rs C configuration/ABI provenance differs")
    return lock, files


def unrun(reason):
    return {"status": "UNRUN", "blocked_by": reason}


def initial_report():
    return {
        "schema_version": 2,
        "project": "orjson",
        "version": "3.13.0",
        "upstream_revision": UPSTREAM_REVISION,
        "source_lock_sha256": LOCK_SHA256,
        "owner": "host-pcc0/owned-source-frontier; NOT native pcc1 or extension execution",
        "project_accepted": False,
        "complete_build_closure": False,
        "source_rewritten": False,
        "build_script_executed": False,
        "external_compiler_used": False,
        "borrow_certified": False,
        "high_level_pyo3_qualified": False,
        "options": {
            "rust_parser_edition": 2024,
            "public_rust_api_edition": 2024,
            "rust_edition_scope": "explicit source option only; NOT complete edition semantics or Cargo crate context",
            "target": TARGET,
            "rust_overflow_checks": False,
            "rust_option_scope": "public admission diagnostic only, NOT Cargo release/dev profile acceptance",
            "c_defines": [list(item) for item in DEFINES],
            "c_configuration_owner": "unchanged build.rs declarations; build.rs was NOT executed",
            "c_header_owner": "same pinned yyjson.h plus PCC-owned utils/fake_libc_include",
        },
        "source_integrity": {"status": "UNRUN"},
        "rust": {name: {
            "source_parse": {"status": "UNRUN"},
            "public_frontend_admission": {"status": "UNRUN"},
            "frontend_object": unrun("explicit edition does not establish Cargo crate context or genuine checked-source admission"),
        } for name in RUST_ROOTS},
        "c_dependency": {
            "path": C_UNIT,
            "preprocess": {"status": "UNRUN"},
            "source_parse": {"status": "UNRUN"},
            "frontend_object": unrun("bounded dependency lane stops at owned preprocessing/parsing"),
        },
        "owned_cargo_graph": unrun("owned dependency/feature/build orchestration and source closure not established"),
        "build_rs_execution": unrun("owned Cargo/build dependencies and real interpreter ABI configuration missing"),
        "procedural_macros": unrun(
            "feature reachability unresolved; lock includes no-panic/proc-macro2/syn, not proof they run by default"
        ),
        "core_alloc_std": unrun("original standard-library/intrinsics/platform closure not qualified"),
        "rust_c_dependency_objects": unrun("Rust and C object closures have not been built"),
        "complete_build": unrun("owned Cargo/build.rs/core/alloc/std/dependency chain incomplete"),
        "shared_extension_link": unrun("complete owned Rust/C object/archive closure missing"),
        "python_capi_load": unrun("shared extension absent; CPython ABI cfg must not be fabricated for PCC"),
        "pcc_native_import": unrun("no PCC-native extension/load/C-API ownership evidence"),
        "dumps": unrun("PCC-native import missing"),
        "loads": unrun("PCC-native import missing"),
        "upstream_tests": unrun(
            "complete original suite/dependencies/data and PCC-native extension missing; selected files not run"
        ),
        "five_gc": {
            "status": "UNRUN",
            "backends": {str(index): unrun("PCC-native extension lifecycle/roots/ownership evidence missing")
                         for index in range(5)},
        },
        "external_reference": {
            "status": "UNRUN",
            "required_rust_version": "1.95",
            "prior_pcc_reference_version": "1.91.1",
            "prior_reference_compatibility": "BELOW_UPSTREAM_MSRV",
            "toolchain_observed_in_this_run": False,
            "reason": "retained prior reference is too old; no downgrade, provisioning or compiler invocation",
        },
    }


def attempt(report, receipt, row, phase, operation, diagnostic_types):
    row[phase] = {"status": "RUNNING"}
    save(receipt, report)
    try:
        value = operation()
    except diagnostic_types as error:
        row[phase] = {"status": diagnostic_status(error), "exception": type(error).__name__,
                      "diagnostic": str(error)}
        save(receipt, report)
        return False, None
    except Exception as error:
        row[phase] = {"status": "FAIL", "exception": type(error).__name__, "diagnostic": str(error)}
        save(receipt, report)
        raise
    row[phase] = {"status": "PASS", "claim": "this bounded source stage only; NOT complete extension acceptance"}
    save(receipt, report)
    return True, value


def run_worker(directory):
    receipt = directory / "frontier.json"
    report = initial_report()
    report["source_integrity"] = {"status": "RUNNING"}
    save(receipt, report)
    try:
        lock, files = load_inputs()
    except Exception as error:
        report["source_integrity"] = {"status": "FAIL", "exception": type(error).__name__, "diagnostic": str(error)}
        save(receipt, report)
        raise
    before = source_identity(ROOT, DRIVERS)
    report["pcc_source_sha256"] = before
    report["pcc_revision"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    report["host"] = {"python": sys.version, "executable": sys.executable, "platform": sys.platform}
    report["inputs"] = lock["files"]
    report["source_integrity"] = {
        "status": "PASS", "scope": "preflight only", "files": len(files),
        "bytes": sum(map(len, files.values())),
    }
    save(receipt, report)
    # Imports are intentionally confined to the guarded CI worker.
    from pcc.frontends.rust import RustError, compile_rust
    from pcc.frontends.rust.syntax import Parser
    from pcc.frontends.c.parse import make_c_parser
    from pcc.frontends.c.parse.plyparser import ParseError
    from pcc.frontends.c.preprocessor import Preprocessor

    # Do not expose a live fixture directory to include search: an unpinned
    # math.h/stdio.h beside yyjson.h could otherwise shadow owned headers.
    c_files = {name: files[name] for name in (C_UNIT, "include/yyjson/yyjson.h")}
    c_snapshot = directory / "source"
    materialize_snapshot(c_snapshot, c_files)
    report["materialized_c_inputs"] = {name: sha256(value) for name, value in c_files.items()}
    save(receipt, report)

    for name in RUST_ROOTS:
        source = files[name].decode("utf-8")
        row = report["rust"][name]
        attempt(report, receipt, row, "source_parse",
                lambda: Parser(source, source_path=name, edition=2024).parse(), (RustError,))
        # Bind the locked edition at the real public gate, without a checking
        # flag/token. Edition identity does not grant Cargo/borrow admission.
        attempt(report, receipt, row, "public_frontend_admission",
                lambda: compile_rust(source, module_name="original_extension_root",
                                     target_triple=TARGET, overflow_checks=False,
                                     edition=2024), (RustError,))

    def preprocess_original_c():
        verify_snapshot(c_snapshot, c_files)
        include = c_snapshot / "include/yyjson"
        preprocessor = Preprocessor(
            base_dir=str(include), include_dirs=[str(include)],
            defines=dict(DEFINES), target_triple=TARGET, language="c",
        )
        expected = (ROOT / "utils/fake_libc_include").resolve()
        if [Path(path).resolve() for path in preprocessor.system_include_dirs] != [expected]:
            raise ValueError("C preprocessor selected headers outside the frozen owned source tree")
        return preprocessor.preprocess(files[C_UNIT].decode("utf-8"))

    c_row = report["c_dependency"]
    success, preprocessed = attempt(report, receipt, c_row, "preprocess",
                                   preprocess_original_c, (RuntimeError, ValueError))
    if success:
        artifact = directory / "yyjson.preprocessed.c"
        artifact.write_bytes(preprocessed.encode("utf-8"))
        c_row["preprocess"].update(output_sha256=sha256(artifact.read_bytes()), artifact=artifact.name)
        attempt(report, receipt, c_row, "source_parse",
                lambda: make_c_parser().parse(preprocessed, filename=C_UNIT), (ParseError,))
    else:
        c_row["source_parse"] = unrun("owned C preprocessing failed")
    save(receipt, report)
    try:
        verify_snapshot(c_snapshot, c_files)
        if source_identity(ROOT, DRIVERS) != before or load_inputs()[1] != files:
            raise ValueError("source/compiler/header inputs changed")
    except Exception as error:
        report["source_integrity"] = {
            "status": "FAIL", "exception": type(error).__name__, "diagnostic": str(error),
        }
        save(receipt, report)
        return 2
    report["source_integrity"]["scope"] = "identical before and after all attempted source stages"
    report["terminal"] = True
    save(receipt, report)
    return 1  # Later stages are UNRUN, never whole orjson acceptance.


def main(argv=None):
    return run_ci(ROOT, FIXTURE, Path(__file__).resolve(), initial_report, run_worker, argv)


if __name__ == "__main__":
    raise SystemExit(main())

