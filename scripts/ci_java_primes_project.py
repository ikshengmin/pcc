"""CI-only unchanged Commons Numbers Primes module and upstream-test frontier.

The optional JDK lane is a separate reference. No JAR/class file is an input
to PCC, no test is rewritten, and an incomplete chain always exits nonzero.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))

from scripts.ci_source_frontier_support import (
    load_source_lock,
    materialize_snapshot,
    run_ci,
    save,
    source_identity,
    verify_snapshot,
)

FIXTURE = ROOT / "projects/commons-numbers-1.2"
LOCK_SHA256 = "f3579348251822ff7b359e0e0619e4d112db8bfd0b54d774f6cd5b382e3a0e25"
REVISION = "77f715769e4039acb4babd9add80b78a06ca7279"
MODULE = "commons-numbers-primes"
PACKAGE = "org/apache/commons/numbers/primes/"
PRODUCTION = tuple(MODULE + "/src/main/java/" + PACKAGE + name for name in (
    "Primes.java", "SmallPrimes.java", "package-info.java",
))
TESTS = tuple(MODULE + "/src/test/java/" + PACKAGE + name for name in (
    "PrimesTest.java", "SmallPrimesTest.java", "UserGuideTest.java",
))
TARGET = "x86_64-unknown-linux-gnu"
ENTRY = {"entry_class": "org.apache.commons.numbers.primes.Primes",
         "entry": "isPrime", "descriptor": "(I)Z", "target": TARGET}
DRIVERS = (
    "scripts/ci_java_primes_project.py", "scripts/ci_source_frontier_support.py",
    "scripts/ci_validation_run.py", "scripts/run_process_tree_sample.py",
    "scripts/platform_process_watchdog.py",
    "tests/java/test_source_commons_numbers_project.py",
)
JVM_LIMITS = ("-Xms16m", "-Xmx128m", "-XX:MaxMetaspaceSize=96m")


def load_inputs(directory=FIXTURE):
    lock, files = load_source_lock(directory, LOCK_SHA256)
    if (lock["project"] != MODULE or lock["version"] != "1.2"
            or lock["revision"] != REVISION or len(files) != 17
            or lock["module_tree"] != "c49e45903a1c1155f2ba4c23657f362d6a926691"
            or tuple(lock["production_units"]) != PRODUCTION
            or tuple(lock["upstream_test_units"]) != TESTS
            or lock["complete_module_source_closure"] is not True
            or lock["complete_upstream_test_source_closure"] is not True):
        raise ValueError("complete original module/test selection differs")
    java = {name for name in files if name.endswith(".java")}
    if java != set(PRODUCTION + TESTS):
        raise ValueError("original production/test Java closure differs")
    namespace = {"m": "http://maven.apache.org/POM/4.0.0"}
    root = ET.fromstring(files["pom.xml"])
    module = ET.fromstring(files[MODULE + "/pom.xml"])
    if (root.findtext("m:version", namespaces=namespace) != "1.2"
            or root.findtext("m:parent/m:version", namespaces=namespace) != "72"
            or root.findtext("m:properties/m:maven.compiler.source", namespaces=namespace) != "1.8"
            or module.findtext("m:artifactId", namespaces=namespace) != MODULE
            or module.findtext("m:parent/m:version", namespaces=namespace) != "1.2"):
        raise ValueError("unchanged Maven version/parent/source contract differs")
    dependencies = lock["dependencies"]
    if (dependencies["state"] != "MISSING_SOURCE_AND_TOOL_CLOSURE"
            or dependencies["junit"]["source_closure_present"] is not False
            or dependencies["junit"]["binary_reference_closure_present"] is not False
            or dependencies["junit"]["reference_console_sha256"] is not None):
        raise ValueError("dependency metadata cannot authorize a source or binary closure")
    return lock, files


def unrun(reason):
    return {"status": "UNRUN", "blocked_by": reason}


def initial_report():
    return {
        "schema_version": 1, "project": MODULE, "version": "1.2",
        "upstream_revision": REVISION, "source_lock_sha256": LOCK_SHA256,
        "owner": "host-pcc0/owned Java source; not native pcc1",
        "project_accepted": False, "complete_build_closure": False,
        "source_rewritten": False, "production_bytecode_or_jar_used": False,
        "source_integrity": unrun("preflight not started"),
        "production_units": list(PRODUCTION), "upstream_test_units": list(TESTS),
        "entry_selection": {**ENTRY, "scope": "export selection only; every supplied method is checked"},
        "owned": {
            "production_compile": unrun("original production source not compiled"),
            "upstream_test_compile": unrun("production compile must complete first"),
            "upstream_test_execution": unrun("owned JUnit/library/runtime source and test-launch closure missing"),
        },
        "reference": {
            "owner": "explicit external JDK only; never a PCC production dependency",
            "production_compile": unrun("PCC_JAVA_RAW_REFERENCE not selected"),
            "upstream_test_compile": unrun("verified JUnit reference artifact closure missing"),
            "upstream_test_execution": unrun("verified JUnit reference artifact closure missing"),
            "upstream_test_units": list(TESTS),
        },
        "native_pcc1": unrun("no native compiler project evidence"),
        "five_gc": unrun("no original project managed-lifecycle execution evidence"),
    }


def original_sources(files, names):
    # Decoding does not normalize CRLF, remove package-info, annotations,
    # constructors, methods, imports, tests, comments or exception paths.
    return {name: files[name].decode("utf-8") for name in names}


def compile_original_stages(report, receipt, files, compile_sources, java_error, unsupported_java):
    for stage, names in (("production_compile", PRODUCTION),
                         ("upstream_test_compile", PRODUCTION + TESTS)):
        report["owned"][stage] = {"status": "RUNNING", "units": list(names)}
        save(receipt, report)
        try:
            compile_sources(original_sources(files, names), **ENTRY)
        except java_error as error:
            report["owned"][stage].update(
                status="UNSUPPORTED" if isinstance(error, unsupported_java) else "FAIL",
                exception=type(error).__name__, diagnostic=str(error), code=getattr(error, "code", None),
            )
            save(receipt, report)
            return
        except Exception as error:
            report["owned"][stage].update(status="FAIL", exception=type(error).__name__, diagnostic=str(error))
            save(receipt, report)
            raise
        report["owned"][stage].update(status="PASS", claim="source compilation only; no project/test execution")
        save(receipt, report)


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jdk_identity(java):
    home = java.parent.parent
    names = ("bin/java", "bin/javac", "release", "lib/modules", "lib/ct.sym", "lib/server/libjvm.so")
    return {name: file_digest(home / name) for name in names}


def reference_production(report, receipt, snapshot, directory):
    selected = os.environ.get("PCC_JAVA_RAW_REFERENCE")
    if not selected:
        return
    row = report["reference"]["production_compile"]
    row.update(status="RUNNING")
    save(receipt, report)
    java = Path(selected).resolve(strict=True)
    home = java.parent.parent
    release = (home / "release").read_text(encoding="utf-8")
    version = re.search(r'^JAVA_VERSION="([^"]+)"', release, re.MULTILINE)
    if not version or int(version.group(1).split(".")[0]) < 9:
        raise ValueError("explicit JDK9+ required for --release 8 reference compilation")
    before = jdk_identity(java)
    row.update(java_version=version.group(1), source_release=8, tools_before=before)
    classes = directory / "jdk-production-classes"
    classes.mkdir()
    command = [str(home / "bin/javac"), *("-J" + option for option in JVM_LIMITS),
               "--release", "8", "-encoding", "UTF-8", "-proc:none", "-d", str(classes),
               "-classpath", str(classes), "-sourcepath", str(snapshot / MODULE / "src/main/java"),
               *(str(snapshot / name) for name in PRODUCTION)]
    environment = dict(os.environ)
    for name in ("CLASSPATH", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS", "JDK_JAVAC_OPTIONS"):
        environment.pop(name, None)
    try:
        with (directory / "jdk-production.stdout").open("wb") as stdout, (directory / "jdk-production.stderr").open("wb") as stderr:
            result = subprocess.run(command, cwd=directory, env=environment, stdout=stdout, stderr=stderr,
                                    check=False, timeout=30)
        row.update(status="PASS" if result.returncode == 0 else "FAIL", returncode=result.returncode,
                   command=command, stdout_sha256=file_digest(directory / "jdk-production.stdout"),
                   stderr_sha256=file_digest(directory / "jdk-production.stderr"),
                   claim="external JDK production-source compilation only; upstream tests not run")
    finally:
        after = jdk_identity(java)
        row["tools_after"] = after
        if before != after:
            row.update(status="FAIL", diagnostic="selected reference JDK changed")
            save(receipt, report)
            raise ValueError("selected reference JDK changed")
        save(receipt, report)


def run_worker(directory):
    if os.environ.get("CI", "").lower() != "true":
        raise ValueError("CI-only frontier; local compiler execution is not authorized")
    if directory.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("output must be outside the repository")
    receipt = directory / "frontier.json"
    report = initial_report()
    save(receipt, report)
    lock, files = load_inputs()
    before = source_identity(ROOT, DRIVERS)
    report.update(pcc_source_sha256=before, inputs=lock["files"], dependency_closure=lock["dependencies"],
                  host={"python": sys.version, "platform": sys.platform})
    report["pcc_revision"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    snapshot = directory / "source"
    materialize_snapshot(snapshot, files)
    report["source_integrity"] = {"status": "PASS", "scope": "preflight", "files": len(files)}
    save(receipt, report)
    try:
        # Compiler imports occur only in the bounded, CI-authorized worker.
        from pcc.frontends.java import JavaError, UnsupportedJava, compile_sources
        compile_original_stages(report, receipt, files, compile_sources, JavaError, UnsupportedJava)
        # Independent reference stages never consume or replace owned output.
        reference_production(report, receipt, snapshot, directory)
    finally:
        try:
            verify_snapshot(snapshot, files)
            if source_identity(ROOT, DRIVERS) != before or load_inputs()[1] != files:
                raise ValueError("source/compiler inputs changed")
        except Exception as error:
            report["source_integrity"] = {"status": "FAIL", "exception": type(error).__name__,
                                          "diagnostic": str(error)}
            save(receipt, report)
            raise
    report["source_integrity"]["scope"] = "identical before and after every attempted stage"
    report["terminal"] = True
    save(receipt, report)
    return 1  # Missing dependency/execution closure cannot become project PASS.


def main(argv=None):
    # Raw evidence must remain outside the Git source tree, including on CI.
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    arguments, _ = parser.parse_known_args(argv)
    if arguments.output.resolve().is_relative_to(ROOT.resolve()):
        parser.error("output must be outside the repository")
    return run_ci(ROOT, FIXTURE, Path(__file__).resolve(), initial_report, run_worker, argv)


if __name__ == "__main__":
    raise SystemExit(main())
