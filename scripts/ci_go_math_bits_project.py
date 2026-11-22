"""CI-only whole math/bits target: owned admission and separate official tests.

The unchanged package/test inventory is complete. The owned testing/runtime
closure is not: a completed source-stage receipt must never imply package PASS.
Reference execution requires an explicitly supplied, matching Go 1.27.3 tree.
"""
from __future__ import annotations

from contextlib import contextmanager
import builtins
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))

from scripts.ci_source_frontier_support import (
    bounded_bytes,
    input_path,
    load_source_lock,
    materialize_snapshot,
    run_ci,
    save,
    sha256,
    source_identity,
    verify_snapshot,
)

FIXTURE = ROOT / "tests/go/upstream/go1.27.3"
LOCK_SHA256 = "01333873af479122f8e75ffbd52da4e5f312a90b059de2c17afac83b790427a8"
REVISION = "2aae1000ea1d4b28923b96db28acf4e3b99d6aff"
PACKAGE = "math/bits"
PACKAGE_DIR = "src/math/bits"
RELEASE = "go1.27.3"
SOURCE_ROOT_ENV = "PCC_GO_MATH_BITS_SOURCE_ROOT"
# Official go list -deps -test -json math/bits, linux/amd64/v1, cgo off.
# Hash of the portable JSON-lines selection, NOT a hash of package contents.
REFERENCE_BUILD_IDS_SHA256 = "220a909f4e633dcfdb0eb92c31d74ad4425bb87eb48538d9e600cfb45d804b54"
REFERENCE_BUILD_IDS_FILE = "tests/go/fixtures/math_bits_reference_build_instances.jsonl"
NORMAL = ("bits.go", "bits_errors.go", "bits_tables.go")
INTERNAL_TEST = ("export_test.go",)
EXTERNAL_TEST = ("bits_test.go", "example_math_test.go", "example_test.go")
EXCLUDED = ("bits_errors_bootstrap.go", "make_examples.go", "make_tables.go")
DEPENDENCIES = ("unsafe", "runtime", "testing", "fmt", "os", "testing/internal/testdeps")
TEST_COMMAND = ("test", "-json", "-count=1", "-timeout=60s", "-p=2", PACKAGE)
# Only an import-admission root for the public PCC package API, never a test.
ADMISSION_MAIN = 'package main;import "math/bits";func main(){_=bits.LeadingZeros(1)}'
DRIVERS = (
    "scripts/ci_go_math_bits_project.py", "scripts/ci_source_frontier_support.py",
    "scripts/go_package_test_entry.py",
    "scripts/go_std_closure.py",
    REFERENCE_BUILD_IDS_FILE,
    "tests/go/upstream/go1.27.3/pcc-linux-amd64-math-bits-normal-plan.json",
    "tests/go/upstream/go1.27.3/pcc-linux-amd64-profiling-source-pin.json",
    "scripts/cpython_conformance/run.py", "scripts/ci_validation_run.py",
    "scripts/run_process_tree_sample.py", "scripts/platform_process_watchdog.py",
)


def load_inputs(directory=FIXTURE):
    lock, files = load_source_lock(directory, LOCK_SHA256)
    names = set(NORMAL + INTERNAL_TEST + EXTERNAL_TEST + EXCLUDED)
    expected = {"LICENSE", "VERSION"} | {PACKAGE_DIR + "/" + name for name in names}
    if (lock["project"] != PACKAGE or lock["version"] != RELEASE
            or lock["revision"] != REVISION or lock["license"] != "BSD-3-Clause"
            or lock["package_inventory_complete"] is not True or set(files) != expected
            or tuple(lock["normal_go_files"]) != NORMAL
            or tuple(lock["test_go_files"]) != INTERNAL_TEST
            or tuple(lock["xtest_go_files"]) != EXTERNAL_TEST
            or set(lock["excluded_go_files"]) != set(EXCLUDED)
            or tuple(lock["required_dependencies"]) != DEPENDENCIES
            or tuple(lock["test_entry"]["command"]) != ("go", *TEST_COMMAND)):
        raise ValueError("whole math/bits identity or normal/test file selection differs")
    verify_originals(directory, files)
    declared = []
    for name in EXTERNAL_TEST:
        declared.extend(re.findall(r"(?m)^func ((?:Test|Example)[A-Za-z0-9_]*)\(",
                                   files[PACKAGE_DIR + "/" + name].decode("utf-8")))
    if declared != lock["test_entry"]["tests_and_examples"] or len(set(declared)) != len(declared):
        raise ValueError("whole original test/example entry inventory differs")
    return lock, files


def verify_originals(directory, files):
    for name, value in files.items():
        if bounded_bytes(input_path(directory, name), 1024 * 1024) != value:
            raise ValueError("original source differs: " + name)
    package = input_path(directory, PACKAGE_DIR)
    if {path.name for path in package.iterdir()} != set(NORMAL + INTERNAL_TEST + EXTERNAL_TEST + EXCLUDED):
        raise ValueError("math/bits directory has missing or unpinned inputs")


def unrun(reason):
    return {"status": "UNRUN", "blocked_by": reason}


def initial_report():
    return {
        "schema_version": 1, "project": PACKAGE, "version": RELEASE,
        "upstream_revision": REVISION, "source_lock_sha256": LOCK_SHA256,
        "owner": "host-pcc0/owned Go frontend; NOT native pcc1",
        "project_accepted": False, "complete_build_closure": False,
        "source_rewritten": False, "source_integrity": {"status": "UNRUN"},
        "package_selection": {"normal": list(NORMAL), "internal_test": list(INTERNAL_TEST),
                              "external_test": list(EXTERNAL_TEST), "excluded": list(EXCLUDED)},
        "owned": {
            "package_frontend": {"status": "UNRUN"},
            "dependency_closure": unrun("owned source/build closure is missing: " + ", ".join(DEPENDENCIES)),
            "test_entry": unrun("original test declarations have not been registered"),
            "test_source_bindings": unrun("verified normal package sources and generated entry required"),
            "test_source_parse": unrun("exact build-instance source bytes and import edges required"),
            "test_source_analysis": unrun("declared instance imports require the owned semantic pass"),
            "test_build": unrun("package admission, original dependency closure and test entry required"),
            "tests_and_examples": unrun("whole upstream test binary has not been built"),
        },
        "external_reference": {
            "owner": "official Go, never an owned-compiler fallback", "required_version": RELEASE,
            "status": "UNRUN", "prerequisite": {"status": "UNRUN"},
            "dependency_graph": {"status": "UNRUN"}, "tests_and_examples": {"status": "UNRUN"},
        },
    }


@contextmanager
def owned_compilation():
    """Confine the compiler call; official tool use remains outside this scope."""
    from unittest.mock import patch
    original_import = builtins.__import__

    def owned_import(name, *args, **kwargs):
        if name == "llvmlite" or name.startswith("llvmlite."):
            raise RuntimeError("external LLVM import forbidden in owned Go admission")
        return original_import(name, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise RuntimeError("external process forbidden in owned Go admission")

    with patch.object(builtins, "__import__", owned_import), patch.object(subprocess, "Popen", forbidden):
        yield


def verify_owned_normal_sources(root, files):
    from pcc.frontends.go.source_plan import load_go_source_plan
    from scripts.go_std_closure import load_linux_normal_plan

    raw, digest = load_linux_normal_plan()
    plan = load_go_source_plan(root, raw, expected_sha256=digest)
    normal_paths = tuple(f"{PACKAGE_DIR}/{name}" for name in NORMAL)
    if (plan.release != RELEASE or PACKAGE not in plan.packages
            or plan.packages[PACKAGE].go != normal_paths
            or any(plan.original_files[path] != files[path] for path in normal_paths)):
        raise ValueError("verified Go plan disagrees with unchanged math/bits normal originals")
    return plan, {
        "status": "SOURCES_VERIFIED", "claim": "normal-package original bytes only",
        "target": plan.target, "plan_sha256": plan.lock_sha256, "roots": list(plan.roots),
        "packages": len(plan.packages), "files": len(plan.original_files),
        "source_bytes": sum(map(len, plan.original_files.values())),
        "unresolved": [{"package": name, "owner": owner, "files": list(paths)}
                       for name, owner, paths in plan.unresolved(plan.packages)],
    }


def bind_owned_test_sources(plan, project_files, generated):
    """Bind original bytes to exact test build-instance IDs, not to an executable."""
    raw = (ROOT / REFERENCE_BUILD_IDS_FILE).read_bytes()
    if sha256(raw) != REFERENCE_BUILD_IDS_SHA256:
        raise ValueError("pinned Go test build-instance selection SHA256 mismatch")
    suffix = " [" + PACKAGE + ".test]"
    internal, external, entry = PACKAGE + suffix, PACKAGE + "_test" + suffix, PACKAGE + ".test"
    test_paths = {f"{PACKAGE_DIR}/{name}" for name in INTERNAL_TEST + EXTERNAL_TEST}
    roles = (("GoFiles", "go", "go"), ("SFiles", "assembly", "asm"), ("HFiles", "headers", "header"))
    result = {}
    for line in raw.splitlines():
        row = json.loads(line)
        key, directory = row["ImportPath"], row["SourceDir"]
        remapped = set(row["ImportMap"].values())
        imports = {dep: dep for dep in row["Imports"] if dep not in remapped}
        imports.update(row["ImportMap"])
        if len(imports) != len(row["Imports"]):
            raise ValueError("ambiguous compiler import identity in test instance: " + key)
        if key == entry:
            sources = [{"role": "generated", "path": "_testmain.go", "sha256": sha256(generated)}]
        else:
            ordinary = plan.packages.get(directory)
            if ordinary is None:
                raise ValueError("normal source package is missing for test instance: " + key)
            sources = []
            for field, attribute, role in roles:
                names = tuple(f"src/{directory}/{name}" for name in row[field])
                if field == "GoFiles" and key in (internal, external):
                    expected_names = NORMAL + INTERNAL_TEST if key == internal else EXTERNAL_TEST
                    expected = tuple(f"{PACKAGE_DIR}/{name}" for name in expected_names)
                else:
                    expected = getattr(ordinary, attribute)
                if names != expected:
                    raise ValueError("normal source selection differs for test instance: " + key)
                for path in names:
                    files = project_files if path in test_paths else plan.original_files
                    if path not in files:
                        raise ValueError("normal source is missing for test instance: " + path)
                    sources.append({"role": role, "path": path, "sha256": sha256(files[path])})
        result[key] = {"sources": sources, "imports": imports}
    return result


def bound_test_units(instance, sources, plan, project_files, generated):
    root = PACKAGE + ".test"
    test_paths = {f"{PACKAGE_DIR}/{name}" for name in INTERNAL_TEST + EXTERNAL_TEST}
    units = []
    for source in sources:
        role, path = source["role"], source["path"]
        if role == "generated":
            if instance != root or path != "_testmain.go":
                raise ValueError("unexpected generated Go source for instance: " + instance)
            raw = generated
        elif role == "go":
            files = project_files if path in test_paths else plan.original_files
            raw = files.get(path)
        else:
            continue  # Syntax and semantics cannot admit assembly or headers.
        if raw is None or sha256(raw) != source["sha256"]:
            raise ValueError("bound Go source bytes changed: " + path)
        units.append((path, raw.decode("utf-8")))
    return tuple(units)


def parse_first_bound_test_dependency(bindings, plan, project_files, generated):
    """Follow the real generated main's first import in the exact variant graph."""
    from pcc.frontends.go.packages import parse_package
    from pcc.frontends.go.syntax import GoDiagnostic

    root = PACKAGE + ".test"
    parsed = []

    def parse(instance):
        if instance not in bindings:
            raise ValueError("missing exact test-source binding: " + instance)
        row = bindings[instance]
        units = bound_test_units(instance, row["sources"], plan, project_files, generated)
        program = parse_package(units, require_main=instance == root)
        actual = {}
        for imported in program.imports:
            if imported.path not in row["imports"]:
                raise ValueError("Go source import is not bound in " + instance + ": " + imported.path)
            actual[imported.path] = row["imports"][imported.path]
        parsed.append({"instance": instance, "package": program.token.text, "go_files": len(units),
                       "declared_imports": actual,
                       "non_source_imports": sorted(row["imports"].keys() - actual.keys())})
        return program

    instance = root
    try:
        main = parse(instance)
        if not main.imports:
            raise ValueError("generated Go test main has no dependency to parse")
        instance = parsed[0]["declared_imports"][main.imports[0].path]
        parse(instance)
    except GoDiagnostic as error:
        return {"status": "BLOCKED", "parsed": parsed, "blocked_instance": instance,
                "diagnostic": str(error), "filename": error.filename, "line": error.line,
                "column": error.column, "code": error.code}
    return {"status": "PARSED", "parsed": parsed, "claim": "root and first exact dependency syntax only"}


def analyze_bound_test_instances(bindings, plan, project_files, generated):
    from pcc.frontends.go.packages import SourceInstance, analyze_source_instances
    from pcc.frontends.go.source_plan import runtime_owner
    from pcc.frontends.go.syntax import GoDiagnostic

    root = PACKAGE + ".test"
    suffix = " [" + root + "]"
    instances = {}
    for key, row in bindings.items():
        logical = "main" if key == root else key.removesuffix(suffix)
        units = () if runtime_owner(logical) else bound_test_units(
            key, row["sources"], plan, project_files, generated)
        instances[key] = SourceInstance(logical, units, row["imports"])
    try:
        result = analyze_source_instances(instances, main_instance=root, target_triple=plan.target)
    except GoDiagnostic as error:
        return {"status": "BLOCKED", "diagnostic": str(error), "filename": error.filename,
                "line": error.line, "column": error.column, "code": error.code}
    return {"status": "SOURCE_ANALYSIS_ONLY", "analyzed_instances": sorted(result),
            "claim": "declared imports only; no runtime ABI or native-code admission"}


def run_owned(report, files, directory, *, source_root=None):
    row = report["owned"]
    row["package_frontend"] = {"status": "RUNNING"}
    save(directory / "frontier.json", report)
    (directory / "admission-main.go").write_text(ADMISSION_MAIN, encoding="utf-8")
    try:
        with owned_compilation():
            from pcc.frontends.go import GoDiagnostic, compile_go_packages
            inputs = {"main": [("admission-main.go", ADMISSION_MAIN)], PACKAGE: [
                (name, files[PACKAGE_DIR + "/" + name].decode("utf-8")) for name in NORMAL
            ]}
            module = compile_go_packages(inputs, target_triple="x86_64-unknown-linux-gnu")
    except GoDiagnostic as error:
        row["package_frontend"] = {
            "status": "UNSUPPORTED" if getattr(error, "kind", None) == "unsupported" else "FAIL",
            "diagnostic": str(error), "code": getattr(error, "code", None),
            "filename": error.filename, "line": error.line, "column": error.column,
        }
    else:
        raw = str(module).encode("utf-8")
        (directory / "package.ll").write_bytes(raw)
        row["package_frontend"] = {"status": "PASS", "ir_sha256": sha256(raw),
                                   "claim": "all normal production source files to IR only; NOT upstream tests"}
    save(directory / "frontier.json", report)
    if row["package_frontend"]["status"] != "PASS":
        return
    from scripts.go_package_test_entry import build_entry
    def units(names):
        return tuple((name, files[PACKAGE_DIR + "/" + name].decode("utf-8")) for name in names)
    row["test_entry"] = {"status": "RUNNING"}
    save(directory / "frontier.json", report)
    try:
        with owned_compilation():
            generated, entries = build_entry(PACKAGE, units(INTERNAL_TEST), units(EXTERNAL_TEST))
            from pcc.frontends.go.syntax import GoParser
            GoParser().parse(generated, "_testmain.go", require_main=True)
    except ValueError as error:
        row["test_entry"] = {"status": "UNSUPPORTED" if getattr(error, "kind", None) == "unsupported" else "FAIL",
                             "diagnostic": str(error)}
        save(directory / "frontier.json", report)
        return
    raw_entry = generated.encode()
    (directory / "owned-testmain.go").write_bytes(raw_entry)
    row["test_entry"] = {
        "status": "PASS", "claim": "PCC-generated testing.MainStart source only, no test binary or execution",
        "sha256": sha256(raw_entry), "registrations": {key: [item[0] for item in group]
                                                       for key, group in entries.items()},
        "inputs": {name: sha256(files[PACKAGE_DIR + "/" + name]) for name in INTERNAL_TEST + EXTERNAL_TEST},
    }
    save(directory / "frontier.json", report)
    # Supply every original project file selected for this test build, not a
    # handwritten main or fake os/testing implementation. The four-target
    # union under pcc-std-closure-lock is NOT a Linux/amd64 build selection.
    # Keep missing target-specific sources visible to the real compiler.
    packages = {"main": (("_testmain.go", generated),), PACKAGE: units(NORMAL + INTERNAL_TEST),
                PACKAGE + "_test": units(EXTERNAL_TEST)}
    if source_root is not None:
        # This pinned recipe has ordinary import paths only. Never replace a
        # locked normal package with its test augmentation or conflate test
        # variants before a test-specific source/ABI binding exists.
        try:
            with owned_compilation():
                plan, closure = verify_owned_normal_sources(source_root, files)
        except (ValueError, OSError) as error:
            closure = {"status": "BLOCKED", "diagnostic": str(error)}
        row["dependency_closure"] = closure
        if closure["status"] == "SOURCES_VERIFIED":
            try:
                bindings = bind_owned_test_sources(plan, files, raw_entry)
            except (ValueError, OSError) as error:
                row["test_source_bindings"] = {"status": "BLOCKED", "diagnostic": str(error)}
            else:
                raw = json.dumps(bindings, sort_keys=True, separators=(",", ":")).encode()
                (directory / "owned-test-source-bindings.json").write_bytes(raw)
                row["test_source_bindings"] = {
                    "status": "BOUND", "instances": len(bindings), "sha256": sha256(raw),
                    "claim": "original and generated source identity only; no compiler or ABI admission",
                }
                try:
                    with owned_compilation():
                        row["test_source_parse"] = parse_first_bound_test_dependency(bindings, plan, files, raw_entry)
                except (ValueError, OSError) as error:
                    row["test_source_parse"] = {"status": "BLOCKED", "diagnostic": str(error)}
                if row["test_source_parse"]["status"] == "PARSED":
                    try:
                        with owned_compilation():
                            row["test_source_analysis"] = analyze_bound_test_instances(bindings, plan, files, raw_entry)
                    except (ValueError, OSError) as error:
                        row["test_source_analysis"] = {"status": "BLOCKED", "diagnostic": str(error)}
        binding = row["test_source_bindings"]
        parsed = row["test_source_parse"]
        analysis = row["test_source_analysis"]
        row["test_build"] = {
            "status": "BLOCKED", "phase": "original test-instance source admission; no native test build",
            "source_packages": list(packages),
            "diagnostic": closure.get("diagnostic", binding.get("diagnostic", parsed.get("diagnostic",
                analysis.get("diagnostic", "native runtime/assembly owners remain unresolved")))),
        }
        save(directory / "frontier.json", report)
        return
    try:
        with owned_compilation():
            module = compile_go_packages(packages, target_triple="x86_64-unknown-linux-gnu")
    except GoDiagnostic as error:
        status = ("BLOCKED" if error.message.startswith("import source unavailable: ")
                  else "UNSUPPORTED" if getattr(error, "kind", None) == "unsupported" else "FAIL")
        row["test_build"] = {
            "status": status, "phase": "original project graph to owned frontend",
            "source_packages": list(packages), "diagnostic": str(error), "code": getattr(error, "code", None),
            "filename": error.filename, "line": error.line, "column": error.column,
        }
    else:
        test_ir = str(module).encode()
        (directory / "package-tests.ll").write_bytes(test_ir)
        row["test_build"] = {"status": "IR_ONLY", "ir_sha256": sha256(test_ir),
                             "claim": "no native test binary or execution"}
    save(directory / "frontier.json", report)


def process(command, directory, name, environment, timeout):
    from scripts.cpython_conformance.run import run_process
    out, err = directory / (name + ".stdout"), directory / (name + ".stderr")
    try:
        result = run_process(command, cwd=directory, stdout=out, stderr=err,
                             timeout=timeout, env=environment)
    except OSError as error:
        result = {"status": "BLOCKED", "diagnostic": str(error)}
    else:
        result["status"] = ("PASS" if result["returncode"] == 0 and not result["timed_out"]
                            else "FAIL")
        result.update(stdout_sha256=sha256(out.read_bytes()), stderr_sha256=sha256(err.read_bytes()))
    save(directory / (name + ".receipt.json"), {"command": command, **result})
    return result


def decode_graph(raw):
    text, decoder, values = raw.decode("utf-8"), json.JSONDecoder(), []
    while text.strip():
        value, end = decoder.raw_decode(text.lstrip())
        values.append(value)
        text = text.lstrip()[end:]
    return values


def validate_graph(rows, goroot):
    """Validate the actual reference graph, not a fabricated owned dependency map."""
    if not rows or any(row.get("Error") or row.get("DepsErrors") or row.get("Incomplete") for row in rows):
        raise ValueError("official package-test dependency graph is incomplete")
    paths = {row["ImportPath"].split(" [", 1)[0] for row in rows}
    if not set((*DEPENDENCIES, PACKAGE, PACKAGE + "_test", PACKAGE + ".test")) <= paths:
        raise ValueError("official package-test entry or required dependency is missing")
    selections = (("bits", False, NORMAL), ("bits", True, NORMAL + INTERNAL_TEST),
                  ("bits_test", True, EXTERNAL_TEST))
    for name, for_test, wanted in selections:
        selected = [row for row in rows if row.get("Name") == name
                    and bool(row.get("ForTest")) == for_test
                    and row["ImportPath"].split(" [", 1)[0] in (PACKAGE, PACKAGE + "_test")]
        if len(selected) != 1 or set(selected[0].get("GoFiles", [])) != set(wanted):
            raise ValueError("official normal/internal/external test file selection differs")
    sources = {}
    for row in rows:
        if row["ImportPath"] == PACKAGE + ".test":
            continue  # _testmain.go is generated by cmd/go, not an original source file.
        directory = Path(row.get("Dir", "")).resolve()
        if not directory.is_relative_to((goroot / "src").resolve()):
            raise ValueError("reference dependency escaped the explicit GOROOT: " + row["ImportPath"])
        for field in ("GoFiles", "CgoFiles", "CFiles", "CXXFiles", "HFiles", "SFiles", "SysoFiles", "EmbedFiles"):
            for name in row.get(field, []):
                path = input_path(directory, name)
                sources[path.relative_to(goroot).as_posix()] = sha256(path.read_bytes())
    return sources


def reference_build_ids(rows, goroot, cache_root):
    """Freeze exact per-instance source selection and dependency bindings.

    validate_graph separately reads the actual files: identical bytes from a
    shared directory do not say which instance the external tests imported.
    cmd/go's generated source is in a private cache and is compared separately.
    """
    if not isinstance(rows, list) or any(type(row) is not dict or
            not all(type(row.get(k)) is str for k in ("ImportPath", "Name", "Dir")) for row in rows):
        raise ValueError("invalid official reference build-instance record")
    ids = {row["ImportPath"] for row in rows}
    if (len(ids) != len(rows) or any(row.get("Incomplete") or row.get("DepsErrors") or row.get("Error")
                                     for row in rows)):
        raise ValueError("incomplete or duplicate official reference build instance")
    suffix = " [" + PACKAGE + ".test]"
    source_root = goroot / "src"
    normalized, maps, variants = [], 0, 0
    for row in sorted(rows, key=lambda item: item["ImportPath"]):
        key = row["ImportPath"]
        variant = key.endswith(suffix)
        logical = key.removesuffix(suffix)
        if row.get("ForTest") != (PACKAGE if variant else None):
            raise ValueError("inconsistent official ForTest: " + key)
        variants += variant
        imports, mapping = row.get("Imports", []), row.get("ImportMap", {})
        if (type(imports) is not list or not all(type(s) is str for s in imports)
                or len(set(imports)) != len(imports) or not set(imports) <= ids
                or type(mapping) is not dict or not all(type(s) is str for s in mapping.values())
                or not set(mapping.values()) <= set(imports)
                or mapping != {dst.removesuffix(suffix): dst for dst in imports if dst.endswith(suffix)}):
            raise ValueError("incomplete or inconsistent official ImportMap/Imports: " + key)
        if any(row.get(k) for k in ("CgoFiles", "CFiles", "CXXFiles", "MFiles", "FFiles", "SwigFiles",
                                    "SwigCXXFiles", "SysoFiles", "EmbedFiles")):
            raise ValueError("unqualified official build input role: " + key)
        maps += len(mapping)
        source_dir = Path(row["Dir"]).relative_to(source_root).as_posix()
        expected_dir = PACKAGE if key in {PACKAGE + ".test", PACKAGE + "_test" + suffix} else logical
        if source_dir != expected_dir:
            raise ValueError("official build instance source directory differs: " + key)
        for field in ("GoFiles", "SFiles", "HFiles"):
            names = row.get(field, [])
            if (type(names) is not list or not all(type(n) is str for n in names)
                    or len(names) != len(set(names))):
                raise ValueError("invalid official build source selection: " + key)
        go = row.get("GoFiles", [])
        if key == PACKAGE + ".test":
            if (len(go) != 1 or not Path(go[0]).is_absolute() or ".." in Path(go[0]).parts
                    or Path(go[0]) == cache_root or not Path(go[0]).is_relative_to(cache_root)):
                raise ValueError("official generated entry outside the private cache")
            go = ["<generated-test-entry>"]
        normalized.append({"ImportPath": key, "Name": row["Name"], "ForTest": row.get("ForTest"),
                           "SourceDir": source_dir, "GoFiles": go, "SFiles": row.get("SFiles", []),
                           "HFiles": row.get("HFiles", []), "Imports": sorted(imports), "ImportMap": mapping})
    # Rows and map keys have no semantic ordering. File selection is kept per
    # instance/role in original order. Neither a runner path nor its tmp cache
    # key contributes to this identity.
    raw = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in normalized).encode()
    digest = sha256(raw)
    if digest != REFERENCE_BUILD_IDS_SHA256:
        raise ValueError("official package-test build-instance selection differs from Go 1.27.3 pin")
    return {"sha256": digest, "instances": len(ids), "test_variants": variants, "import_map_edges": maps}


def reference_test_entry(rows, cache_root, own_path, expected_owned_sha256):
    """Compare real cmd/go output only after owned discovery; never feed it to PCC."""
    from pcc.frontends.go.syntax import scan
    entries = [row for row in rows if row.get("ImportPath") == PACKAGE + ".test"]
    if len(entries) != 1 or entries[0].get("Name") != "main" or len(entries[0].get("GoFiles", ())) != 1:
        raise ValueError("official Go generated package-test entry is missing")
    item = Path(entries[0]["GoFiles"][0])
    if not item.is_absolute() or not item.is_relative_to(cache_root):
        raise ValueError("official Go generated test source is outside its private cache")
    raw = bounded_bytes(input_path(cache_root, item.relative_to(cache_root).as_posix()), 1024 * 1024)
    own = bounded_bytes(input_path(own_path.parent, own_path.name), 1024 * 1024)
    if sha256(own) != expected_owned_sha256:
        raise ValueError("PCC generated test entry changed after source admission")
    def projected(text):
        return [(tok.kind, tok.text) for tok in scan(text.decode("utf-8"), "_testmain.go")]
    tokens = projected(raw)
    if tokens != projected(own):
        raise ValueError("PCC generated package-test entry differs from the official Go token stream")
    return {"status": "PASS", "owned_sha256": sha256(own), "official_sha256": sha256(raw),
            "token_count": len(tokens), "claim": "identical complete Go tokens, not an owned execution"}


def test_events(raw, expected):
    """Require every original Test/Example, no skipped cases, and package success."""
    events = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if any(row.get("Action") in ("skip", "fail") for row in events):
        raise ValueError("upstream test/example failed or was skipped")
    events = [row for row in events if row.get("Package") == PACKAGE]
    started = [row["Test"] for row in events if row.get("Action") == "run" and "Test" in row]
    passed = [row["Test"] for row in events if row.get("Action") == "pass" and "Test" in row]
    if (set(started) != set(expected) or set(passed) != set(expected)
            or len(started) != len(expected) or len(passed) != len(expected)
            or sum(row.get("Action") == "pass" and "Test" not in row for row in events) != 1):
        raise ValueError("whole original test/example execution is incomplete")
    return {"status": "PASS", "executed": passed, "scope": "whole package official reference only"}


def reference_tools(goroot):
    directory = input_path(goroot, "pkg/tool/linux_amd64")
    names = {path.name for path in directory.iterdir()}
    if not {"asm", "compile", "link", "vet"} <= names:
        raise ValueError("explicit Go 1.27.3 compiler/linker/assembler/vet tools are missing")
    paths = ["bin/go", *("pkg/tool/linux_amd64/" + name for name in sorted(names))]
    return {name: sha256(input_path(goroot, name).read_bytes()) for name in paths}


def verify_reference_tools(goroot, before):
    if reference_tools(goroot) != before:
        raise ValueError("official Go driver/tool binaries changed during package tests")


def run_reference(report, lock, files, directory):
    row = report["external_reference"]
    go_name, root_name = os.environ.get("PCC_GO_MATH_BITS_REFERENCE"), os.environ.get("PCC_GO_MATH_BITS_GOROOT")
    if not go_name or not root_name:
        row.update(status="BLOCKED", prerequisite={"status": "BLOCKED", "diagnostic":
            "explicit PCC_GO_MATH_BITS_REFERENCE and PCC_GO_MATH_BITS_GOROOT for Go 1.27.3 required"})
        return
    go, goroot = Path(go_name), Path(root_name)
    if not go.is_absolute() or not goroot.is_absolute() or not go.is_file():
        raise ValueError("official Go tool and GOROOT must be explicit existing absolute paths")
    go, goroot = go.resolve(), goroot.resolve()
    if go != (goroot / "bin/go").resolve():
        raise ValueError("official tool is not the explicit GOROOT/bin/go")
    verify_originals(goroot, files)
    tools_before = reference_tools(goroot)
    row["prerequisite"]["tool_sha256"] = tools_before
    save(directory / "frontier.json", report)
    try:
        _run_reference(report, lock, files, directory, go, goroot, tools_before)
    finally:
        try:
            verify_reference_tools(goroot, tools_before)
        except (OSError, ValueError) as error:
            row["prerequisite"].update(status="FAIL", tools_unchanged=False, diagnostic=str(error))
            row["status"] = "FAIL"
            raise
        else:
            row["prerequisite"]["tools_unchanged"] = True
    if row["status"] == "UNRUN":
        row["status"] = row["tests_and_examples"]["status"]


def _run_reference(report, lock, files, directory, go, goroot, tools_before):
    row = report["external_reference"]
    environment = dict(os.environ)
    environment.update(GOROOT=str(goroot), GOTOOLCHAIN="local", GOENV="off", GOWORK="off",
                       GOPROXY="off", GOSUMDB="off", GO111MODULE="off", CGO_ENABLED="0",
                       GOFLAGS="", GOEXPERIMENT="", GODEBUG="", GOCACHEPROG="",
                       GOOS="linux", GOARCH="amd64", GOAMD64="v1", GOMAXPROCS="2")
    for variable, name in (("GOCACHE", "go-cache"), ("GOTMPDIR", "go-tmp"), ("GOPATH", "go-path")):
        path = directory / name
        path.mkdir()
        environment[variable] = str(path)
    save(directory / "reference-environment.json", {name: environment[name] for name in (
        "GOROOT", "GOTOOLCHAIN", "GOENV", "GOWORK", "GOPROXY", "GOSUMDB", "GO111MODULE",
        "CGO_ENABLED", "GOFLAGS", "GOEXPERIMENT", "GODEBUG", "GOCACHEPROG", "GOOS", "GOARCH",
        "GOAMD64", "GOMAXPROCS", "GOCACHE", "GOTMPDIR", "GOPATH",
    )})
    version = process([str(go), "version"], directory, "reference-version", environment, 5)
    if (version["status"] != "PASS" or (directory / "reference-version.stdout").read_bytes().strip()
            != b"go version go1.27.3 linux/amd64"):
        row.update(status="BLOCKED", prerequisite={"status": "BLOCKED",
                   "diagnostic": "official Go must report exactly go1.27.3 linux/amd64", "process": version,
                   "tool_sha256": tools_before})
        return
    row["prerequisite"] = {"status": "PASS", "version_process": version, "tool_sha256": tools_before}
    save(directory / "frontier.json", report)
    graph = process([str(go), "list", "-deps", "-test", "-json", PACKAGE], directory,
                    "reference-dependencies", environment, 25)
    row["dependency_graph"] = {"status": "RUNNING", "process": graph}
    if graph["status"] != "PASS":
        row["dependency_graph"]["status"] = "BLOCKED"
        row["status"] = "BLOCKED"
        return
    rows = decode_graph((directory / "reference-dependencies.stdout").read_bytes())
    before_ids = reference_build_ids(rows, goroot, Path(environment["GOCACHE"]))
    before = validate_graph(rows, goroot)
    from scripts.go_std_closure import (
        load_std_closure,
        target_selection_gaps,
        verify_linux_profiling_selection,
    )
    std_lock, _ = load_std_closure()
    row["dependency_graph"]["locked_source_gaps"] = target_selection_gaps(before, files, std_lock)
    row["dependency_graph"]["profiling_source_pin"] = {
        "matched_files": verify_linux_profiling_selection(before, before_ids["sha256"]),
        "scope": "source identities only; no owned admission",
    }
    if report["owned"]["test_entry"]["status"] == "PASS":
        row["test_entry_comparison"] = reference_test_entry(
            rows, Path(environment["GOCACHE"]), directory / "owned-testmain.go", report["owned"]["test_entry"]["sha256"])
    row["dependency_graph"].update(
        status="PASS", source_sha256=before, build_instances=before_ids,
        identity_scope="observed reference GOROOT closure; package originals are independently pinned",
    )
    save(directory / "frontier.json", report)
    row["tests_and_examples"] = {"status": "RUNNING"}
    save(directory / "frontier.json", report)
    result = process([str(go), *TEST_COMMAND], directory, "reference-tests", environment, 80)
    row["tests_and_examples"]["process"] = result
    verify_originals(goroot, files)
    row["dependency_graph"]["status"] = "RUNNING"
    recheck = process([str(go), "list", "-deps", "-test", "-json", PACKAGE], directory,
                      "reference-dependencies-after", environment, 10)
    row["dependency_graph"]["recheck"] = recheck
    if recheck["status"] != "PASS":
        raise ValueError("official dependency graph could not be rechecked")
    after_rows = decode_graph((directory / "reference-dependencies-after.stdout").read_bytes())
    after_ids = reference_build_ids(after_rows, goroot, Path(environment["GOCACHE"]))
    if after_ids != before_ids:
        raise ValueError("official package-test build instances changed during whole-package tests")
    if report["owned"]["test_entry"]["status"] == "PASS" and reference_test_entry(
            after_rows, Path(environment["GOCACHE"]), directory / "owned-testmain.go", report["owned"]["test_entry"]["sha256"]) != row["test_entry_comparison"]:
        raise ValueError("official generated test entry changed during the upstream tests")
    if validate_graph(after_rows, goroot) != before:
        raise ValueError("official dependency source changed during whole-package tests")
    row["dependency_graph"].update(status="PASS", rechecked_build_instances=after_ids)
    if result["status"] == "PASS":
        row["tests_and_examples"].update(test_events((directory / "reference-tests.stdout").read_bytes(),
                                                   lock["test_entry"]["tests_and_examples"]))
    else:
        row["tests_and_examples"]["status"] = result["status"]


def run_worker(directory):
    receipt, report = directory / "frontier.json", initial_report()
    save(receipt, report)
    lock, files = load_inputs()
    before = source_identity(ROOT, DRIVERS)
    report["pcc_source_sha256"] = before
    report["pcc_revision"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
        check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    report["inputs"] = lock["files"]
    report["test_entry"] = lock["test_entry"]
    snapshot = directory / "source"
    materialize_snapshot(snapshot, files)
    report["source_integrity"] = {"status": "PASS", "scope": "preflight", "package_inventory_complete": True}
    save(receipt, report)
    run_owned(report, files, directory, source_root=os.environ.get(SOURCE_ROOT_ENV))
    if report["owned"]["test_entry"]["status"] == "PASS":
        registrations = report["owned"]["test_entry"]["registrations"]
        if registrations["tests"] + registrations["examples"] != lock["test_entry"]["tests_and_examples"]:
            raise ValueError("owned test entry differs from pinned whole test/example inventory")
    try:
        run_reference(report, lock, files, directory)
    except (OSError, ValueError) as error:
        reference = report["external_reference"]
        reference.update(status="FAIL" if any(reference[name]["status"] == "FAIL" for name in
                         ("prerequisite", "dependency_graph", "tests_and_examples")) else "BLOCKED",
                         diagnostic=str(error))
        for name in ("prerequisite", "dependency_graph", "tests_and_examples"):
            if reference[name]["status"] == "RUNNING":
                reference[name].update(status="FAIL", diagnostic=str(error))
                reference["status"] = "FAIL"
        if reference["prerequisite"]["status"] == "UNRUN":
            reference["prerequisite"].update(status="BLOCKED", diagnostic=str(error))
    verify_snapshot(snapshot, files)
    if source_identity(ROOT, DRIVERS) != before or load_inputs()[1] != files:
        raise ValueError("original package/test/compiler inputs changed")
    report["source_integrity"]["scope"] = "identical before and after all attempted stages"
    report["terminal"] = True
    save(receipt, report)
    return 1  # Owned native test binary/execution is still incomplete even if the oracle passes.


def main(argv=None):
    return run_ci(ROOT, FIXTURE, Path(__file__).resolve(), initial_report, run_worker, argv)


if __name__ == "__main__":
    raise SystemExit(main())
