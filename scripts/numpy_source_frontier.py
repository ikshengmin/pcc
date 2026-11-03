#!/usr/bin/env python3
"""CI-only original NumPy C/C++ source frontier, never wheel/import acceptance.

The host-PCC lane compiles two complete unchanged upstream translation units,
including their original headers. It is intentionally independent of native
pcc1 readiness. Missing generated headers are an input frontier, not language
coverage. No typedef extraction, source rewriting, host cc or C++ stdlib is used.
The production stages stay separate and cannot be promoted by this lane.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MANIFEST = ROOT / "tests/fixtures/numpy_source_frontier.json"
STAGES = (
    "native_pcc1", "build_scripts_meson", "generated_sources",
    "c_target_compile", "cxx_target_compile", "owned_link",
    "runtime_capi_load", "numpy_import", "numpy_tests", "five_gc",
)
TIMEOUT = 90
RSS_LIMIT = 1024 ** 3


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_path(root, name):
    if (not isinstance(name, str) or "\\" in name or ":" in name
            or "\0" in name or any(part in ("", ".", "..") for part in name.split("/"))):
        raise ValueError("source inputs require canonical relative paths")
    path = root / name
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("source input escapes pinned tree")
    return path


def verify_source(root, manifest):
    """Check every tracked byte against the pinned Git tree, not just two TUs."""
    directory = manifest["directory"]
    source = source_path(root, directory)
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD:" + directory], cwd=root, text=True, timeout=30,
    ).strip()
    if tree != manifest["git_tree"]:
        raise ValueError("NumPy repository source tree drift")
    entries = subprocess.check_output(
        ["git", "ls-tree", "-r", "-z", "HEAD:" + directory], cwd=root, timeout=30,
    )
    closure = hashlib.sha256()
    count = 0
    expected_names = set()
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, expected = metadata.split()
        name = os.fsdecode(raw_name)
        expected_names.add(name)
        path = source_path(source, name)
        if kind != b"blob":
            raise ValueError("unmaterialized source dependency: " + name)
        if mode == b"120000":
            if not path.is_symlink():
                raise ValueError("source symlink changed: " + name)
            data = os.fsencode(os.readlink(path))
        else:
            if path.is_symlink():
                raise ValueError("source file replaced by symlink: " + name)
            data = path.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if actual != expected.decode():
            raise ValueError("unchanged source check failed: " + name)
        closure.update(len(raw_name).to_bytes(8, "big"))
        closure.update(raw_name)
        closure.update(hashlib.sha256(data).digest())
        count += 1
    actual_names = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*") if path.is_file() or path.is_symlink()
    }
    extras = actual_names - expected_names
    if extras:
        raise ValueError("unlocked source/generated inputs: " + repr(sorted(extras)[:5]))
    for name, expected in manifest["sha256"].items():
        if digest(source_path(source, name)) != expected:
            raise ValueError("source SHA256 mismatch: " + name)
    return {"status": "PASS", "git_tree": tree, "file_count": count,
            "content_sha256": closure.hexdigest(), "manifest_sha256": digest(MANIFEST),
            "upstream_archive_sha256": manifest["upstream_archive_sha256"],
            "scope": manifest["source_scope"]}


def initial_report(manifest):
    return {
        "schema": "pcc.numpy-source-frontier.v1", "status": "UNRUN",
        "project": {"name": manifest["name"], "version": manifest["version"],
                    "license_expression": manifest["license_expression"]},
        "claim": "bounded original-TU development evidence; no NumPy build/import acceptance",
        "production_qualified": False,
        "limits": {"per_process_tree_seconds": TIMEOUT, "tree_rss_bytes": RSS_LIMIT},
        "stages": {stage: {"status": "UNRUN", "reason": "prerequisite not established"}
                   for stage in STAGES},
        "host_pcc_development": [],
    }


def environment(root, out):
    env = dict(os.environ)
    empty = out / "empty-path"
    empty.mkdir(exist_ok=True)
    forbidden = str(out / "forbidden-external-tool")
    env.update({
        "PATH": str(empty), "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1",
        "PCC_SOURCE_ROOT": str(root), "PCC_REPO_ROOT": str(root),
        "CC": forbidden, "CXX": forbidden, "PCC_HOST_PYTHON": forbidden,
        "PCC_HOST_PCC": forbidden, "PCC_RUNTIME_CC": "pcc",
        "PCC_RUNTIME_HIGH": "py", "PCC_SELF_LINK": "pcc", "PCC_SELF_OBJ": "pcc",
        "PCC_IR_TO_OBJ_EMITTER": "pcc", "PCC_NO_AUTO_PCC1": "1",
        "PCC_DEFER_FRONTEND_CODEGEN": "0", "PCC_SELF_BACKEND_OBJECT_CACHE": "0",
        "PCC_PY_FRONTEND_IR_CACHE": "0", "PCC_DISABLE_PARALLEL": "1",
        "PCC_DISABLE_COMPILE_CACHE": "1",
    })
    # Do not clear LC_ALL or select a compatibility/native artifact from a peer.
    return env


def compile_command(source, output, includes, *, interpreter):
    return [str(interpreter), "-m", "pcc", "--backend=self", "--no-cache", "--emit-obj", str(output),
            *("--cpp-arg=-I" + str(path) for path in includes), str(source)]


def attempt_host_units(root, manifest, out, report):
    from pcc.package.build_exec import (
        _materialize_pcc_capi_include,
        _pcc_runtime_include_dir,
    )
    from scripts.platform_process_watchdog import run

    source = source_path(root, manifest["directory"])
    capi = _materialize_pcc_capi_include(out / "headers", execute=True)
    runtime = _pcc_runtime_include_dir()
    if capi is None or runtime is None:
        raise ValueError("owned PCC C-API headers unavailable")
    includes = [capi, runtime, source / "numpy/_core/include",
                source / "numpy/_core/src/common", source / "numpy/_core/src/multiarray",
                source / "numpy/_core/src/npymath", source / "numpy/_core"]
    env = environment(root, out)
    for language, relative in manifest["translation_units"].items():
        unit = source_path(source, relative)
        output = out / (language + ".o")
        row = {"language": language, "source": relative, "sha256": digest(unit),
               "mode": "host-pcc/self/object-only/development", "status": "UNRUN",
               "generated_inputs": "none; clean original source and owned PCC C-API headers",
               "target_build_qualified": False, "object_emitted": False}
        row["command"] = compile_command(unit, output, includes, interpreter=sys.executable)
        row["log"] = str(out / (language + ".log"))
        report["host_pcc_development"].append(row)
        try:
            row["process"] = run(
                row["command"], cwd=root, env=env, log_path=Path(row["log"]),
                timeout=TIMEOUT, rss_limit=RSS_LIMIT, native=False,
            )
            if not output.is_file() or output.stat().st_size == 0:
                raise RuntimeError("PCC returned success without an object")
            row.update(status="OBJECT_EMITTED", object_emitted=True,
                       object_sha256=digest(output))
        except subprocess.CalledProcessError as exc:
            row.update(status="COMPILE_FAILED", returncode=exc.returncode, error=str(exc))
        except TimeoutError as exc:
            row.update(status="TIMEOUT", error=str(exc))
        except (OSError, RuntimeError) as exc:
            row.update(status="ERROR", error=str(exc))
        finally:
            row["source_unchanged"] = digest(unit) == row["sha256"]
            if not row["source_unchanged"]:
                raise ValueError("compiler changed original NumPy source")


def check_native(compiler, receipt_path, report):
    """Fail closed until this lane consumes the strict Stage1 admission owner.

    A caller-authored source/hash JSON and a static executable header do not
    establish compiler ownership or a current-source build. The existing
    ci_stage1_bundle owner requires the complete bundle and producer workflow
    lease; this CLI has neither. Do not replace that admission with a partial
    receipt checker, and do not launch a supplied compiler as a diagnostic.
    """
    stage = report["stages"]["native_pcc1"]
    stage.update(
        status="BLOCKED",
        compiler_supplied=compiler is not None,
        receipt_supplied=receipt_path is not None,
        reason="strict current-source Stage1 bundle admission is not connected to this lane; supplied compiler and receipt are not admitted or executed",
    )
    report["stages"]["build_scripts_meson"].update(
        status="BLOCKED", reason="native compiler admission and owned Meson/generated-source execution are not established")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--host-pcc-development", action="store_true")
    parser.add_argument("--pcc1", type=Path)
    parser.add_argument("--bootstrap-receipt", type=Path)
    args = parser.parse_args(argv)
    out = args.output_dir.resolve()
    # Fresh output prevents stale .o/receipt reuse and keeps original sources intact.
    if out.exists():
        parser.error("--output-dir must be fresh")
    manifest = json.loads(MANIFEST.read_text())
    source = source_path(ROOT, manifest["directory"])
    if out.is_relative_to(source.resolve()):
        parser.error("outputs must be outside original NumPy source")
    out.mkdir(parents=True)
    report = initial_report(manifest)
    try:
        report["source"] = verify_source(ROOT, manifest)
        from scripts.bootstrap_platform import source_identity
        report["compiler_source"] = source_identity(allow_dirty=True)
        check_native(args.pcc1, args.bootstrap_receipt, report)
        if args.host_pcc_development:
            attempt_host_units(ROOT, manifest, out, report)
        after = verify_source(ROOT, manifest)
        if after != report["source"]:
            raise ValueError("NumPy source changed during frontier")
        if source_identity(allow_dirty=True) != report["compiler_source"]:
            raise ValueError("compiler source changed during frontier")
        report["source_unchanged"] = True
        report["status"] = "BLOCKED"  # Production stages deliberately remain unsatisfied.
    except Exception as exc:
        report.update(status="ERROR", error=str(exc))
    finally:
        (out / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2  # Recording an unsupported frontier is not a green NumPy gate.


if __name__ == "__main__":
    raise SystemExit(main())
