"""Unsafe-runtime and pcc-Python archive regression tests.

These checks cover the pcc.unsafe lowering path, pcc-Python runtime
replacement archive membership, and a few C harnesses that link against
the pcc-Python archive directly.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

from pcc.diagnostics.dependency_verdict import probe_artifact_dependency

import pytest


REPO_ROOT = Path(__file__).absolute().parents[2]
SPIKE_SRC = REPO_ROOT / "pcc" / "runtime" / "py" / "py_tuple_spike.py"
PY_RUNTIME_DIR = REPO_ROOT / "pcc" / "runtime"
PY_RUNTIME_PY_DIR = PY_RUNTIME_DIR / "py"


def _pcc_binary() -> str:
    candidate = Path(sys.executable).parent / "pcc"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("pcc")
    if found is None:
        pytest.fail("pcc CLI not on PATH")
    return found


def _make_reference_env(**values: str) -> dict[str, str]:
    """Environment for the Make reference runtime selector.

    The in-process owned builder is the default runtime build. The selector
    tests below exercise the explicit PCC_RUNTIME_BUILD=make reference path
    and the checks it injects, so they select that path instead of
    inheriting whatever the default is.
    """
    return {"PCC_RUNTIME_BUILD": "make", **values}


def _active_python_runtime_modules() -> list[str]:
    return _make_var_words("PY_MODULES")


def _make_var_words(name: str) -> list[str]:
    makefile = PY_RUNTIME_DIR / "Makefile"
    lines = makefile.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        if not (
            line.startswith(f"{name} =")
            or line.startswith(f"{name} +=")
        ):
            i += 1
            continue
        line = line.split("=", 1)[1].strip()
        while True:
            continued = line.endswith("\\")
            if continued:
                line = line[:-1].strip()
            if line:
                out.extend(line.split())
            if not continued:
                break
            i += 1
            if i >= len(lines):
                break
            line = lines[i].strip()
        i += 1
    if not out:
        raise AssertionError(f"{name} line not found in py_runtime Makefile")
    return out


def test_no_libpython_runtime_selector_defaults_to_pcc_python_archive():
    from pcc.frontends.python import pipeline

    with mock.patch.dict(os.environ, _make_reference_env(), clear=True):
        with mock.patch(
            "pcc.frontends.python.pipeline.os.path.isfile",
            return_value=True,
        ):
            with mock.patch(
                "pcc.frontends.python.pipeline._runtime_archive_stale",
                return_value=False,
            ):
                archive = pipeline._ensure_runtime(
                    False,
                )

    assert Path(archive).name == "libpy_runtime_pcc_py.a"


def test_runtime_selector_ignores_retired_c_runtime_selectors():
    from pcc.frontends.python import pipeline

    with mock.patch(
        "pcc.frontends.python.pipeline.os.path.isfile",
        return_value=True,
    ):
        with mock.patch(
            "pcc.frontends.python.pipeline._runtime_archive_stale",
            return_value=False,
        ):
            with mock.patch.dict(
                os.environ,
                _make_reference_env(PCC_RUNTIME_CC="cc", PCC_RUNTIME_HIGH="c"),
                clear=True,
            ):
                archive = pipeline._ensure_runtime(False)

    assert Path(archive).name == "libpy_runtime_pcc_py.a"


def test_resolve_pcc_binary_prefers_current_stage_binary(tmp_path):
    from pcc.frontends.python import pipeline

    stage = tmp_path / "pcc1"
    stage.write_text("#!/bin/sh\n", encoding="utf-8")
    stage.chmod(0o755)

    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch.object(sys, "argv", [str(stage), "--backend", "self"]):
            assert pipeline._resolve_pcc_binary() == str(stage)


def test_resolve_pcc_binary_ignores_pytest_argv0(tmp_path):
    from pcc.frontends.python import pipeline

    pytest_bin = tmp_path / "pytest"
    pytest_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    pytest_bin.chmod(0o755)
    pcc_bin = tmp_path / "pcc"
    pcc_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    pcc_bin.chmod(0o755)

    with mock.patch.dict(os.environ, {"PATH": str(tmp_path)}, clear=True):
        with mock.patch.object(sys, "argv", [str(pytest_bin), "-q"]):
            with mock.patch.object(sys, "executable", str(tmp_path / "python")):
                assert pipeline._resolve_pcc_binary() == str(pcc_bin)


def test_active_python_runtime_modules_do_not_call_substrate_helpers():
    # Modules allowed to call the substrate's ``py_mem_alloc`` /
    # ``py_mem_free`` directly. The rule is "active runtime modules
    # don't reach into substrate", but a small set of modules manage
    # their own raw-buffer working storage and the alternative
    # (routing every alloc through an indirect wrapper) would force
    # an extra ABI hop for every codepath that needs scratch memory.
    # ``py_obj_stubs`` was the original allowed caller; ``py_str_accessors``
    # is the second: ``str.ljust`` / ``str.rjust`` / ``str.center`` /
    # ``str.zfill`` / ``str.rsplit`` all allocate transient padded /
    # split buffers, and the byte-level work is closer to memory
    # management than to higher-level Python semantics.
    #
    # The substrate's py_subs_* entry points are its published C ABI: shared
    # state it owns (the user type-tag counter, the canonical object root)
    # is reached only through them. Other modules may bind an exported entry
    # with extern(); naming anything else py_subs_* reaches into substrate
    # internals.
    allowed_py_mem_callers = {"py_obj_stubs", "py_str_accessors"}
    substrate = ast.parse((PY_RUNTIME_PY_DIR / "py_substrate.py").read_text(encoding="utf-8"))
    published = {
        decorator.args[0].value
        for function in ast.walk(substrate)
        if isinstance(function, ast.FunctionDef)
        for decorator in function.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id in ("c_abi_export", "c_abi_typed_export")
        and decorator.args
        and isinstance(decorator.args[0], ast.Constant)
        and str(decorator.args[0].value).startswith("py_subs_")
    }
    assert {"py_subs_alloc_user_tag", "py_subs_object_root"} <= published
    offenders: list[str] = []
    for module in _active_python_runtime_modules():
        if module == "py_substrate":
            continue
        path = PY_RUNTIME_PY_DIR / f"{module}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bound: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            name = first.value
            if name.startswith("py_subs_"):
                is_extern = isinstance(node.func, ast.Name) and node.func.id == "extern"
                if not is_extern or name not in published:
                    offenders.append(f"{path.relative_to(REPO_ROOT)} reaches {name}")
                bound.add(name)
            if name.startswith("py_mem_") and module not in allowed_py_mem_callers:
                offenders.append(f"{path.relative_to(REPO_ROOT)} calls {name}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id.startswith("py_mem_"):
                if module not in allowed_py_mem_callers:
                    offenders.append(f"{path.relative_to(REPO_ROOT)} uses {node.id}")
            if isinstance(node, ast.Name) and node.id.startswith("py_subs_"):
                if node.id not in bound:
                    offenders.append(f"{path.relative_to(REPO_ROOT)} uses unbound {node.id}")

    assert offenders == []


def _runtime_archive_plan() -> str:
    """The commands Make would run to rebuild the pcc-Python archive.

    ``make -B -n`` executes the outer lock recipe's ``+$(MAKE)`` and prints
    the inner archive rule fully expanded, so these checks read what Make
    runs, not how the Makefile spells it.
    """
    plan = subprocess.run(
        ["make", "-B", "-n", "libpy_runtime_pcc_py.a"],
        cwd=PY_RUNTIME_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert plan.returncode == 0, plan.stderr
    return plan.stdout


def test_python_runtime_archive_requires_provenance_for_exact_python_objects():
    from pcc.frontends.python.owned_runtime_build import runtime_modules
    from pcc.frontends.python.pipeline_targets import host_target_triple

    # The owned builder's target inventory is the one membership source.
    objects = [
        "build_py/" + module + ".o"
        for module in runtime_modules(str(PY_RUNTIME_DIR), host_target_triple(), True)
    ]
    assert objects and len(set(objects)) == len(objects)
    plan = _runtime_archive_plan()

    archived = re.findall(r"(?m)^\s*\S*ar rcs libpy_runtime_pcc_py\.a\.tmp ([^;\n]+)", plan)
    assert [sorted(entry.split()) for entry in archived] == [sorted(objects)]
    assembled = re.findall(
        r"runtime_archive_provenance assemble [^;\n]*? --ar \S+ ([^;\n]+)", plan
    )
    assert [sorted(entry.split()) for entry in assembled] == [sorted(objects)]
    for obj in objects:
        # Each object is emitted with its receipt, and the archive requires
        # every receipt before it archives the objects.
        assert "--provenance " + obj + ".provenance.json" in plan
        assert "test -f " + obj + ".provenance.json" in plan


def test_pcc_python_archive_has_no_libpython_object(pcc_runtime_archive):
    # The archive built from the current runtime source, not a leftover file.
    archive = pcc_runtime_archive
    # Structured artifact verdict: a missing prebuilt archive is an explicit
    # UNAVAILABLE prerequisite, never evidence about the archive's contents;
    # only the executed ``ar t`` probe below can claim runtime behavior
    # (AUD-P2-DEPENDENCY-RUNTIME-ARCHIVE-VERDICT).
    verdict = probe_artifact_dependency(archive, kind="runtime-archive")
    if not verdict.available:
        pytest.fail(verdict.skip_reason())

    result = subprocess.run(
        ["ar", "t", str(archive)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    members = set(result.stdout.splitlines())
    assert "py_process.o" in members
    assert "py_substrate.o" in members
    assert "py_libpython.o" not in members


def test_no_libpython_pcc_python_archive_staleness_ignores_libpython_bridge(tmp_path):
    from pcc.frontends.python import pipeline

    runtime_dir = tmp_path / "py_runtime"
    src_dir = runtime_dir / "src"
    include_dir = runtime_dir / "include"
    py_dir = runtime_dir / "py"
    src_dir.mkdir(parents=True)
    include_dir.mkdir()
    py_dir.mkdir()

    archive = runtime_dir / "libpy_runtime_pcc_py.a"
    archive.write_text("archive", encoding="utf-8")
    Path(str(archive) + ".provenance.json").write_text("{}\n", encoding="utf-8")
    (runtime_dir / "Makefile").write_text("all:\n", encoding="utf-8")
    (include_dir / "py_runtime.h").write_text("/* header */\n", encoding="utf-8")
    (src_dir / "py_obj.c").write_text("/* normal no-lib source */\n", encoding="utf-8")
    (src_dir / "py_libpython.c").write_text("/* optional bridge */\n", encoding="utf-8")
    (py_dir / "py_obj.py").write_text("# pcc-python runtime\n", encoding="utf-8")

    stamp = Path(pipeline._runtime_archive_target_stamp(str(archive)))
    stamp.write_text(pipeline._runtime_archive_target_id() + "\n", encoding="utf-8")

    old = 1_700_000_000
    current = old + 10
    newer = current + 10
    for path in [
        runtime_dir / "Makefile",
        include_dir / "py_runtime.h",
        src_dir / "py_obj.c",
        py_dir / "py_obj.py",
        stamp,
    ]:
        os.utime(path, (old, old))
    os.utime(archive, (current, current))
    os.utime(src_dir / "py_libpython.c", (newer, newer))

    with mock.patch.object(pipeline, "_PY_RUNTIME_DIR", str(runtime_dir)):
        # This test isolates the py_libpython.c bridge-skip behavior. The
        # archive-vs-pcc-compiler-source dependency is orthogonal (it scans the
        # real _PCC_DIR, which the test does not stub), so neutralize it here.
        with mock.patch.object(
            pipeline,
            "_runtime_archive_compiler_sources_newer_than",
            return_value=False,
        ):
                with mock.patch.object(
                    pipeline,
                    "_runtime_archive_provenance_valid",
                    return_value=True,
                ):
                    with mock.patch.object(
                        pipeline,
                        "_runtime_archive_codegen_stale",
                        return_value=False,
                    ):
                        assert pipeline._runtime_archive_stale(str(archive)) is False


def test_pcc_python_archive_staleness_ignores_c_sources(tmp_path):
    from pcc.frontends.python import pipeline

    runtime_dir = tmp_path / "py_runtime"
    src_dir = runtime_dir / "src"
    include_dir = runtime_dir / "include"
    py_dir = runtime_dir / "py"
    src_dir.mkdir(parents=True)
    include_dir.mkdir()
    py_dir.mkdir()

    archive = runtime_dir / "libpy_runtime_pcc_py.a"
    archive.write_text("archive", encoding="utf-8")
    Path(str(archive) + ".provenance.json").write_text("{}\n", encoding="utf-8")
    (runtime_dir / "Makefile").write_text("all:\n", encoding="utf-8")
    header = include_dir / "py_runtime.h"
    header.write_text("/* header */\n", encoding="utf-8")
    c_source = src_dir / "py_gc_index_table.c"
    c_source.write_text("/* no C object is archived */\n", encoding="utf-8")
    py_source = py_dir / "py_obj_gc.py"
    py_source.write_text("# active pcc-Python semantic runtime\n", encoding="utf-8")

    stamp = Path(pipeline._runtime_archive_target_stamp(str(archive)))
    stamp.write_text(pipeline._runtime_archive_target_id() + "\n", encoding="utf-8")

    old = 1_700_000_000
    current = old + 10
    newer = current + 10
    for path in [runtime_dir / "Makefile", py_source, stamp]:
        os.utime(path, (old, old))
    os.utime(archive, (current, current))
    os.utime(header, (newer, newer))
    os.utime(c_source, (newer, newer))

    with mock.patch.object(pipeline, "_PY_RUNTIME_DIR", str(runtime_dir)):
        with mock.patch.object(
            pipeline,
            "_runtime_archive_compiler_sources_newer_than",
            return_value=False,
        ):
            with mock.patch.object(
                pipeline,
                "_runtime_archive_provenance_valid",
                return_value=True,
            ):
                with mock.patch.object(
                    pipeline,
                    "_runtime_archive_codegen_stale",
                    return_value=False,
                ):
                    assert pipeline._runtime_archive_stale(str(archive)) is False
                    os.utime(py_source, (newer, newer))
                    assert pipeline._runtime_archive_stale(str(archive)) is True


def test_pcc_python_archive_requires_valid_provenance_before_wheel_shortcut(tmp_path):
    from pcc.frontends.python import pipeline

    archive = tmp_path / "libpy_runtime_pcc_py.a"
    archive.write_bytes(b"archive")
    Path(pipeline._runtime_archive_target_stamp(str(archive))).write_text(
        pipeline._runtime_archive_target_id() + "\n",
        encoding="utf-8",
    )
    Path(str(archive) + ".wheel").write_text(
        "pcc.runtime-wheel-artifact.v1\n"
        + pipeline._runtime_archive_target_id()
        + "\nsha256:"
        + ("a" * 64)
        + "\n",
        encoding="utf-8",
    )

    assert pipeline._runtime_archive_stale(str(archive)) is True
    Path(str(archive) + ".provenance.json").write_text("{}\n", encoding="utf-8")
    assert pipeline._runtime_archive_stale(str(archive)) is True


def test_pcc_emitted_archive_staleness_tracks_compiler_sources(tmp_path):
    from pcc.frontends.python import pipeline

    runtime_dir = tmp_path / "py_runtime"
    include_dir = runtime_dir / "include"
    src_dir = runtime_dir / "src"
    py_dir = runtime_dir / "py"
    include_dir.mkdir(parents=True)
    src_dir.mkdir()
    py_dir.mkdir()

    archive = runtime_dir / "libpy_runtime_pcc_py.a"
    archive.write_text("archive", encoding="utf-8")
    (runtime_dir / "Makefile").write_text("all:\n", encoding="utf-8")
    (include_dir / "py_runtime.h").write_text("/* header */\n", encoding="utf-8")
    (src_dir / "py_gc_backend.c").write_text("/* c runtime source */\n", encoding="utf-8")
    (py_dir / "py_gc_backend.py").write_text("# pcc-python runtime source\n", encoding="utf-8")

    pcc_dir = tmp_path / "pcc"
    codegen_dir = pcc_dir / "py_frontend" / "codegen"
    codegen_dir.mkdir(parents=True)
    codegen_file = codegen_dir / "user_function_lowering.py"
    codegen_file.write_text("# compiler source\n", encoding="utf-8")

    stamp = Path(pipeline._runtime_archive_target_stamp(str(archive)))
    stamp.write_text(pipeline._runtime_archive_target_id() + "\n", encoding="utf-8")

    old = 1_700_000_000
    current = old + 10
    newer = current + 10
    for path in [
        runtime_dir / "Makefile",
        include_dir / "py_runtime.h",
        src_dir / "py_gc_backend.c",
        py_dir / "py_gc_backend.py",
        stamp,
    ]:
        os.utime(path, (old, old))
    os.utime(archive, (current, current))
    os.utime(codegen_file, (newer, newer))

    with mock.patch.object(pipeline, "_PY_RUNTIME_DIR", str(runtime_dir)):
        with mock.patch.object(pipeline, "_PCC_DIR", str(pcc_dir)):
            assert pipeline._runtime_archive_stale(str(archive)) is True


def test_pcc_python_codegen_checksum_staleness_forces_full_rebuild(tmp_path):
    from pcc.frontends.python import pipeline

    runtime_dir = tmp_path / "py_runtime"
    runtime_dir.mkdir()
    archive = runtime_dir / "libpy_runtime_pcc_py.a"
    archive.write_bytes(b"archive")
    Path(str(archive) + ".provenance.json").write_text("{}\n", encoding="utf-8")
    (runtime_dir / "Makefile").write_text("all:\n", encoding="utf-8")
    calls: list[list[str]] = []
    freshness = {"stale": True}

    def fake_runtime_make(make_cmd, *, verbose):
        calls.append(list(make_cmd))
        freshness["stale"] = False

    with mock.patch.dict(
        os.environ,
        _make_reference_env(PCC_RUNTIME_CC="pcc", PCC_RUNTIME_HIGH="py"),
        clear=True,
    ):
        with mock.patch.object(pipeline, "_PY_RUNTIME_DIR", str(runtime_dir)):
            with mock.patch.object(
                pipeline,
                "_PY_RUNTIME_ARCHIVE_PCC_PY",
                str(archive),
            ):
                with mock.patch.object(
                    pipeline,
                    "_runtime_archive_provenance_valid",
                    return_value=True,
                ):
                    with mock.patch.object(
                        pipeline,
                        "_runtime_archive_codegen_stale",
                        side_effect=lambda _archive: freshness["stale"],
                    ):
                        with mock.patch.object(
                            pipeline,
                            "_runtime_archive_target_matches",
                            return_value=True,
                        ):
                            with mock.patch.object(
                                pipeline,
                                "_runtime_archive_compiler_sources_newer_than",
                                return_value=False,
                            ):
                                with mock.patch.object(
                                    pipeline,
                                    "_run_runtime_make",
                                    side_effect=fake_runtime_make,
                                ):
                                    selected = pipeline._ensure_runtime(
                                        False,
                                    )

    assert selected == str(archive)
    assert calls
    assert "-B" in calls[0]
    assert any(part.startswith("-j") for part in calls[0])


def test_pcc_python_rebuild_fails_if_codegen_freshness_remains_unprovable(
    tmp_path,
):
    from pcc.frontends.python import pipeline

    runtime_dir = tmp_path / "py_runtime"
    runtime_dir.mkdir()
    archive = runtime_dir / "libpy_runtime_pcc_py.a"
    archive.write_bytes(b"archive")
    Path(str(archive) + ".provenance.json").write_text("{}\n", encoding="utf-8")
    (runtime_dir / "Makefile").write_text("all:\n", encoding="utf-8")

    with mock.patch.dict(
        os.environ,
        _make_reference_env(PCC_RUNTIME_CC="pcc", PCC_RUNTIME_HIGH="py"),
        clear=True,
    ):
        with mock.patch.object(pipeline, "_PY_RUNTIME_DIR", str(runtime_dir)):
            with mock.patch.object(
                pipeline,
                "_PY_RUNTIME_ARCHIVE_PCC_PY",
                str(archive),
            ):
                with mock.patch.object(
                    pipeline,
                    "_runtime_archive_provenance_valid",
                    return_value=True,
                ):
                    with mock.patch.object(
                        pipeline,
                        "_runtime_archive_codegen_stale",
                        return_value=True,
                    ):
                        with mock.patch.object(
                            pipeline,
                            "_runtime_archive_target_matches",
                            return_value=True,
                        ):
                            with mock.patch.object(
                                pipeline,
                                "_runtime_archive_compiler_sources_newer_than",
                                return_value=False,
                            ):
                                with mock.patch.object(pipeline, "_run_runtime_make"):
                                    with mock.patch.object(
                                        pipeline,
                                        "_write_runtime_archive_target_stamp",
                                    ) as write_stamp:
                                        with pytest.raises(
                                            pipeline.PyPipelineError,
                                            match="stale codegen provenance",
                                        ):
                                            pipeline._ensure_runtime(
                                                False,
                                            )

    write_stamp.assert_not_called()


def test_explicit_pcc_python_archive_does_not_compare_current_codegen(tmp_path):
    from pcc.frontends.python import pipeline

    archive = tmp_path / "libpy_runtime_pcc_py.a"
    archive.write_bytes(b"archive")
    Path(str(archive) + ".provenance.json").write_text("{}\n", encoding="utf-8")

    with mock.patch.dict(
        os.environ,
        _make_reference_env(PCC_RUNTIME_ARCHIVE=str(archive)),
        clear=True,
    ):
        with mock.patch.object(
            pipeline,
            "_runtime_archive_provenance_valid",
            return_value=True,
        ):
            with mock.patch.object(
                pipeline,
                "_runtime_archive_codegen_stale",
                side_effect=AssertionError("explicit archive must not compare checkout"),
            ):
                selected = pipeline._ensure_runtime(False)

    assert selected == str(archive)


def test_pcc_python_runtime_build_rejects_missing_provenance(tmp_path):
    from pcc.frontends.python import pipeline

    runtime_dir = tmp_path / "py_runtime"
    runtime_dir.mkdir()
    archive = runtime_dir / "libpy_runtime_pcc_py.a"
    archive.write_bytes(b"archive")
    (runtime_dir / "Makefile").write_text("all:\n", encoding="utf-8")
    Path(pipeline._runtime_archive_target_stamp(str(archive))).write_text(
        pipeline._runtime_archive_target_id() + "\n",
        encoding="utf-8",
    )

    with mock.patch.dict(
        os.environ,
        _make_reference_env(PCC_RUNTIME_CC="pcc", PCC_RUNTIME_HIGH="py"),
        clear=True,
    ):
        with mock.patch.object(pipeline, "_PY_RUNTIME_DIR", str(runtime_dir)):
            with mock.patch.object(
                pipeline, "_PY_RUNTIME_ARCHIVE_PCC_PY", str(archive)
            ):
                with mock.patch.object(
                    pipeline,
                    "_runtime_archive_compiler_sources_newer_than",
                    return_value=False,
                ):
                    with mock.patch.object(pipeline, "_run_runtime_make"):
                        with pytest.raises(
                            pipeline.PyPipelineError,
                            match="missing provenance manifest",
                        ):
                            pipeline._ensure_runtime(False)


def _production_runtime_ir() -> list[tuple[str, str]]:
    """(module IR file, text) of the archive the default builder links.

    The owned builder keeps each member's IR beside the archive; reading
    those, not a stale build directory, checks what programs actually link.
    """
    from pcc.frontends.python.owned_runtime_build import ensure_target_runtime
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.native_provisioning import require_native_provisioning_allowed

    require_native_provisioning_allowed()
    archive = ensure_target_runtime(str(PY_RUNTIME_DIR), host_target_triple())
    ir_files = sorted(Path(str(archive) + ".objects").glob("*.ll"))
    assert ir_files, archive
    return [(path.name, path.read_text(encoding="utf-8")) for path in ir_files]


_MODVAR_GLOBAL = re.compile(
    r"^@(\.modvar\.[^\s=]+) = (?:internal |dso_local |private )*global \S+ (\S+)", re.M
)
_IR_DEFINE = re.compile(r"^define [^@]*@([^\s(]+)\(", re.M)


def _module_top_only_globals(name: str, text: str) -> tuple[list[str], int]:
    """Module globals whose value only the module's top-level code sets.

    Nothing calls a runtime module's ``_pcc_py_module_top_<mod>``: the archive
    links without running module top-level code. A global that the top level
    assigns a value other than its static initializer, and that no function
    assigns, reads that static initializer whenever runtime code loads it.
    Also returns how many top-level stores were checked.
    """
    static = {match.group(1): match.group(2) for match in _MODVAR_GLOBAL.finditer(text)}
    top_stores: dict[str, set[str]] = {}
    other_stores: set[str] = set()
    other_loads: set[str] = set()
    for match in _IR_DEFINE.finditer(text):
        body = text[match.start():text.find("\n}", match.start())]
        top = match.group(1).startswith("_pcc_py_module_top_")
        for store in re.finditer(r"store \S+ ([^,]+), ptr @(\.modvar\.[^\s,]+)", body):
            if top:
                top_stores.setdefault(store.group(2), set()).add(store.group(1).strip())
            else:
                other_stores.add(store.group(2))
        if not top:
            for load in re.finditer(r"load (?:atomic )?\S+, ptr @(\.modvar\.[^\s,]+)", body):
                other_loads.add(load.group(1))
    offenders = []
    checked = 0
    for global_name, values in sorted(top_stores.items()):
        if global_name.endswith("_initialized") or global_name not in static:
            continue
        checked += 1
        if values == {static[global_name]}:
            continue
        if global_name in other_loads and global_name not in other_stores:
            offenders.append(f"{name}: {global_name} static={static[global_name]} top={sorted(values)}")
    return offenders, checked


def test_pcc_python_runtime_objects_do_not_depend_on_module_main_globals():
    offenders: list[str] = []
    checked = 0
    for name, text in _production_runtime_ir():
        found, count = _module_top_only_globals(name, text)
        offenders.extend(found)
        checked += count
    # Static initialization covers the module constants the runtime reads.
    assert checked > 0
    assert offenders == []


def test_module_top_only_global_detection_reports_a_top_level_value():
    text = (
        "@.modvar.m.X = global i64 0\n"
        "@.modvar.m.X_initialized = global i1 0\n"
        "@.modvar.m.Y = global i64 7\n"
        "define external void @_pcc_py_module_top_m() {\n"
        "  store i64 5, ptr @.modvar.m.X\n"
        "  store i1 1, ptr @.modvar.m.X_initialized\n"
        "  store i64 7, ptr @.modvar.m.Y\n"
        "  ret void\n}\n"
        "define i64 @reader() {\n"
        "  %x = load i64, ptr @.modvar.m.X\n"
        "  %y = load i64, ptr @.modvar.m.Y\n"
        "  ret i64 %x\n}\n"
    )
    assert _module_top_only_globals("m.ll", text) == (
        ["m.ll: .modvar.m.X static=0 top=['5']"], 2,
    )


def test_pcc_python_runtime_library_ir_has_no_program_main():
    offenders = [
        name for name, text in _production_runtime_ir()
        if re.search(r"define\s+i32\s+@main\(", text)
    ]
    assert offenders == []


def test_pcc_python_runtime_makefile_uses_python_library_mode_not_text_stripping():
    from pcc.frontends.python.owned_runtime_build import runtime_modules
    from pcc.frontends.python.pipeline_targets import host_target_triple

    plan = _runtime_archive_plan()
    for module in runtime_modules(str(PY_RUNTIME_DIR), host_target_triple(), True):
        # Every member's IR comes from library-mode compilation of its source.
        emits = [
            line for line in plan.splitlines()
            if "--emit-llvm=build_py/" + module + ".ll" in line
        ]
        assert len(emits) == 1, module
        assert " --python-library " in emits[0], emits[0]
        assert emits[0].rstrip().endswith("py/" + module + ".py"), emits[0]
    assert "awk 'BEGIN{s=0}" not in plan


def test_python_library_mode_emits_module_without_program_main(tmp_path):
    from pcc.frontends.python.pipeline import compile_python

    src = tmp_path / "libmod.py"
    out_ll = tmp_path / "libmod.ll"
    src.write_text(
        "def exported() -> int:\n"
        "    return 7\n",
        encoding="utf-8",
    )

    compile_python(
        str(src),
        str(out_ll),
        emit_llvm_only=True,
        python_library=True,
    )

    ir_text = out_ll.read_text(encoding="utf-8")
    assert "define i32 @main(" not in ir_text
    assert re.search(
        r"define(?:\s+external)?\s+void\s+@_pcc_py_module_top_libmod\(",
        ir_text,
    )


def test_python_library_mode_statically_initializes_scalar_literals(tmp_path):
    from pcc.frontends.python.pipeline import compile_python

    src = tmp_path / "library_constants.py"
    out_ll = tmp_path / "library_constants.ll"
    src.write_text(
        "from pcc.extern import c_abi_typed_export\n"
        "\n"
        "POSITIVE = 48\n"
        "NEGATIVE = -103\n"
        "ENABLED = True\n"
        "\n"
        "@c_abi_typed_export('read_positive', 'i64', ())\n"
        "def read_positive() -> int:\n"
        "    return POSITIVE\n",
        encoding="utf-8",
    )

    compile_python(
        str(src),
        str(out_ll),
        emit_llvm_only=True,
        python_library=True,
    )

    ir_text = out_ll.read_text(encoding="utf-8")
    # Compare initializer values, not the printer's spelling of them.
    assert _global_initializer(ir_text, ".modvar.library_constants.POSITIVE") == ("i64", 48)
    assert _global_initializer(ir_text, ".modvar.library_constants.NEGATIVE") == ("i64", -103)
    assert _global_initializer(ir_text, ".modvar.library_constants.ENABLED") == ("i1", 1)
    assert _global_initializer(
        ir_text, ".modvar.library_constants.POSITIVE_initialized"
    ) == ("i1", 0)


def _global_initializer(ir_text: str, name: str) -> tuple[str, int]:
    """(type, integer value) of one IR global's scalar initializer."""
    match = re.search(
        r"^@" + re.escape(name) + r" = (?:internal |dso_local |private )*global (i\d+) (\S+)$",
        ir_text,
        re.M,
    )
    assert match is not None, name
    value = match.group(2)
    spelled = {"true": 1, "false": 0}
    return match.group(1), spelled[value] if value in spelled else int(value)


def test_python_library_copied_runtime_source_suppresses_implicit_frame_roots(
    tmp_path,
):
    from pcc.frontends.python.pipeline import compile_python

    runtime_copy_py = tmp_path / "py_runtime_pcc_py" / "py"
    runtime_copy_py.mkdir(parents=True)
    src = runtime_copy_py / "frame_probe.py"
    out_ll = tmp_path / "frame_probe.ll"
    src.write_text(
        "from pcc.extern import c_abi_export\n"
        "from pcc.unsafe import null, ptr_is_null\n"
        "\n"
        "@c_abi_export('pcc_gc_note_frame_leave')\n"
        "def pcc_gc_note_frame_leave(slots) -> None:\n"
        "    prev = null()\n"
        "    node = null()\n"
        "    if ptr_is_null(slots) != 0:\n"
        "        return\n"
        "    node = slots\n"
        "    prev = node\n"
        "    return\n",
        encoding="utf-8",
    )

    compile_python(
        str(src),
        str(out_ll),
        emit_llvm_only=True,
        python_library=True,
    )

    ir_text = out_ll.read_text(encoding="utf-8")
    assert "call void @pcc_gc_frame_enter" not in ir_text


def _assert_member_compiled_from_source(archive, module: str, tmp_path) -> str:
    """The archive's ``<module>.o`` is the pcc-Python compilation of its source.

    The archive's provenance manifest binds the member's bytes to the current
    ``py/<module>.py`` compiled as pcc-Python without a host C compiler, and
    the object defines every C-ABI export that source declares. Returns
    ``nm -g`` output so callers can check further ABI symbols.
    """
    import hashlib
    import json

    subprocess.run(
        ["ar", "x", str(archive), module + ".o"], check=True, timeout=30, cwd=str(tmp_path),
    )
    manifest = json.loads(Path(str(archive) + ".provenance.json").read_text(encoding="utf-8"))
    records = [record for record in manifest["members"] if record["member"] == module + ".o"]
    assert len(records) == 1, module
    record = records[0]
    source_path = PY_RUNTIME_PY_DIR / (module + ".py")
    assert record["source"] == "pcc/runtime/py/" + module + ".py"
    assert (record["source_kind"], record["uses_host_cc"]) == ("pcc-python", False)
    assert record["source_sha256"] == hashlib.sha256(source_path.read_bytes()).hexdigest()
    assert record["object_sha256"] == hashlib.sha256(
        (tmp_path / (module + ".o")).read_bytes()
    ).hexdigest()
    result = subprocess.run(
        ["nm", "-g", module + ".o"], capture_output=True, text=True, timeout=30, cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    defined = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[-2] != "U":
            name = fields[-1]
            defined.add(name[1:] if sys.platform == "darwin" and name.startswith("_") else name)
    source = ast.parse(source_path.read_text(encoding="utf-8"))
    exports = {
        decorator.args[0].value
        for function in ast.walk(source)
        if isinstance(function, ast.FunctionDef)
        for decorator in function.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id in ("c_abi_export", "c_abi_typed_export")
        and decorator.args
        and isinstance(decorator.args[0], ast.Constant)
    }
    assert exports, module
    assert exports <= defined, sorted(exports - defined)
    return result.stdout


def test_pcc_python_archive_uses_python_py_substrate_object(tmp_path, pcc_runtime_archive):
    out = _assert_member_compiled_from_source(pcc_runtime_archive, "py_substrate", tmp_path)
    assert "_py_None" in out
    assert "_py_True" in out
    assert "_py_False" in out
    assert "_PY_EXC_BUILTIN_NAMES" in out
    assert "_PY_OBJECT_NAME" in out
    assert "_py_tls_current_exc_storage" in out
    assert "_py_tls_exc_get" in out


def test_pcc_python_archive_uses_python_py_process_object(tmp_path, pcc_runtime_archive):
    out = _assert_member_compiled_from_source(pcc_runtime_archive, "py_process", tmp_path)
    assert "_py_runtime_program_argc" in out
    assert "_py_runtime_program_argv" in out
    assert "_py_runtime_program_args_hook" in out
    assert "_py_set_program_args" in out


def test_pcc_python_archive_uses_python_py_coroutine_object(tmp_path, pcc_runtime_archive):
    out = _assert_member_compiled_from_source(pcc_runtime_archive, "py_coroutine", tmp_path)
    assert "_py_coroutine_run" in out
    assert "_py_await" in out


def test_pcc_python_archive_uses_python_py_func_object(tmp_path, pcc_runtime_archive):
    out = _assert_member_compiled_from_source(pcc_runtime_archive, "py_func", tmp_path)
    assert "_py_func_new" in out
    assert "_py_func_call" in out
    assert "_py_dealloc_func" in out


def test_pcc_python_archive_uses_python_py_re_object(tmp_path, pcc_runtime_archive):
    out = _assert_member_compiled_from_source(pcc_runtime_archive, "py_re", tmp_path)
    assert "_py_re_match" in out


def test_pcc_python_archive_uses_python_py_dunder_object(tmp_path, pcc_runtime_archive):
    out = _assert_member_compiled_from_source(pcc_runtime_archive, "py_dunder", tmp_path)
    assert "_py_int_to_str_obj" in out
    assert "_py_int_format_hex" in out
    assert "_py_user_str_dispatch" in out


def test_pcc_python_archive_uses_python_py_file_object(tmp_path, pcc_runtime_archive):
    out = _assert_member_compiled_from_source(pcc_runtime_archive, "py_file", tmp_path)
    assert "_py_file_open" in out
    assert "_py_file_read_all" in out
    assert "_py_file_write" in out


def test_pcc_python_archive_uses_python_py_os_substrate_object(tmp_path, pcc_runtime_archive):
    out = _assert_member_compiled_from_source(pcc_runtime_archive, "py_os_substrate", tmp_path)
    assert "_py_path_stat_kind" in out
    assert "_py_path_stat_mtime" in out
    assert "_py_os_getcwd_str" in out


def test_pcc_python_archive_uses_python_py_process_substrate_object(tmp_path, pcc_runtime_archive):
    out = _assert_member_compiled_from_source(pcc_runtime_archive, "py_process_substrate", tmp_path)
    assert "_py_subprocess_check_output" in out
    assert "_py_os_listdir" in out
    assert "_py_tempdir_cleanup" in out


def test_pcc_python_archive_uses_python_py_int_object(tmp_path, pcc_runtime_archive):
    out = _assert_member_compiled_from_source(pcc_runtime_archive, "py_int", tmp_path)
    assert "_py_bigint_divmod" in out


def test_pcc_python_runtime_bigint_divmod_matches_python(tmp_path, pcc_runtime_archive):
    # The archive built from the current runtime source, not a leftover file.
    archive = pcc_runtime_archive
    # Structured artifact verdict: a missing prebuilt archive is an explicit
    # UNAVAILABLE prerequisite, never evidence about the archive's contents;
    # only the executed ``ar t`` probe below can claim runtime behavior
    # (AUD-P2-DEPENDENCY-RUNTIME-ARCHIVE-VERDICT).
    verdict = probe_artifact_dependency(archive, kind="runtime-archive")
    if not verdict.available:
        pytest.fail(verdict.skip_reason())

    harness = tmp_path / "bigint_divmod_harness.c"
    harness.write_text(
        """
        #include "pcc/runtime/src/py_internal.h"
        #include <stdio.h>
        #include <stdlib.h>

        static int run_case(const char *as, const char *bs) {
            PyIntObject *a = py_bigint_from_cstr(as);
            PyIntObject *b = py_bigint_from_cstr(bs);
            PyIntObject *q = NULL;
            PyIntObject *r = NULL;
            if (!a || !b) return 10;
            if (py_bigint_divmod(a, b, &q, &r) != 0) return 11;
            char *qs = py_bigint_to_cstr(q);
            char *rs = py_bigint_to_cstr(r);
            if (!qs || !rs) return 12;
            puts(qs);
            puts(rs);
            free(qs);
            free(rs);
            free(q);
            free(r);
            free(a);
            free(b);
            return 0;
        }

        int main(void) {
            int rc = 0;
            rc |= run_case("5", "10");
            rc |= run_case("10", "5");
            rc |= run_case("0", "123");
            rc |= run_case("1267650600228229401496703205376", "10000000000");
            rc |= run_case("-1267650600228229401496703205376", "10000000000");
            rc |= run_case("1267650600228229401496703205376", "-10000000000");
            return rc;
        }
        """,
        encoding="utf-8",
    )
    exe = tmp_path / "bigint_divmod_harness"
    compile_res = subprocess.run(
        [
            "cc",
            "-I", str(REPO_ROOT),
            str(harness),
            str(archive),
            "-o", str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    assert compile_res.returncode == 0, compile_res.stderr

    run_res = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(REPO_ROOT),
    )
    assert run_res.returncode == 0
    assert run_res.stdout == (
        "0\n"
        "5\n"
        "2\n"
        "0\n"
        "0\n"
        "0\n"
        "126765060022822940149\n"
        "6703205376\n"
        "-126765060022822940150\n"
        "3296794624\n"
        "-126765060022822940150\n"
        "-3296794624\n"
    )


def test_pcc_python_traceback_archive_formats_exception(tmp_path, pcc_runtime_archive):
    # The archive built from the current runtime source, not a leftover file.
    archive = pcc_runtime_archive
    # Structured artifact verdict: a missing prebuilt archive is an explicit
    # UNAVAILABLE prerequisite, never evidence about the archive's contents;
    # only the executed ``ar t`` probe below can claim runtime behavior
    # (AUD-P2-DEPENDENCY-RUNTIME-ARCHIVE-VERDICT).
    verdict = probe_artifact_dependency(archive, kind="runtime-archive")
    if not verdict.available:
        pytest.fail(verdict.skip_reason())

    harness = tmp_path / "traceback_harness.c"
    harness.write_text(
        """
        #include "pcc/runtime/include/py_runtime.h"
        int main(void) {
            PyObject *exc = py_exc_new(2, "boom");
            py_exc_append_frame(exc, "fn", "file.py", 12);
            py_exc_print_unhandled(exc);
            return 0;
        }
        """,
        encoding="utf-8",
    )
    exe = tmp_path / "traceback_harness"
    compile_res = subprocess.run(
        [
            "cc",
            "-I", str(REPO_ROOT),
            str(harness),
            str(archive),
            "-o", str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    assert compile_res.returncode == 0, compile_res.stderr

    run_res = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(REPO_ROOT),
    )
    assert run_res.returncode == 0
    assert run_res.stdout == ""
    assert run_res.stderr == (
        "Traceback (most recent call last):\n"
        "  File \"file.py\", line 12, in fn\n"
        "ValueError: boom\n"
    )


def test_pcc_python_relocate_copy_rejects_oversized_copy(tmp_path, pcc_runtime_archive):
    # The archive built from the current runtime source, not a leftover file.
    archive = pcc_runtime_archive
    # Structured artifact verdict: a missing prebuilt archive is an explicit
    # UNAVAILABLE prerequisite, never evidence about the archive's contents;
    # only the executed ``ar t`` probe below can claim runtime behavior
    # (AUD-P2-DEPENDENCY-RUNTIME-ARCHIVE-VERDICT).
    verdict = probe_artifact_dependency(archive, kind="runtime-archive")
    if not verdict.available:
        pytest.fail(verdict.skip_reason())

    harness = tmp_path / "relocate_copy_size_harness.c"
    harness.write_text(
        """
        #include "pcc/runtime/include/py_runtime.h"
        #include <stdio.h>
        #include <stdlib.h>

        int main(void) {
            setenv("PCC_GC_BACKEND", "4", 1);
            /* Leaf-tag scalars (FLOAT/STR/...) are deliberately malloc'd and
             * excluded from the zpage relocation graph by the rework, so they
             * are never relocation candidates. Use a non-leaf zpage-resident
             * tag (TUPLE) to exercise the relocate-copy size guard. */
            PyObject *old = pcc_gc_alloc(64, PY_TYPE_TUPLE, 0);
            if (old == NULL) return 2;
            /* Constructors publish a completed object; a fresh one is
             * never relocated (half-initialized fields). */
            pcc_gc_publish_initialized(old);
            printf("selected=%lld\\n", (long long)pcc_gc_select_relocation_set(1));
            PyObject *moved = pcc_gc_relocate_copy(old, 96);
            printf("oversize_null=%d\\n", moved == NULL);
            if (moved != NULL) {
                pcc_gc_release(moved);
            }
            pcc_gc_release(old);
            return 0;
        }
        """,
        encoding="utf-8",
    )
    exe = tmp_path / "relocate_copy_size_harness"
    compile_res = subprocess.run(
        [
            "cc",
            "-I", str(REPO_ROOT),
            str(harness),
            str(archive),
            "-o", str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    assert compile_res.returncode == 0, compile_res.stderr

    run_res = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(REPO_ROOT),
    )
    assert run_res.returncode == 0, run_res.stderr
    assert run_res.stdout == "selected=1\noversize_null=1\n"


def test_pcc_python_relocate_copy_consumes_relocation_entry(tmp_path, pcc_runtime_archive):
    # The archive built from the current runtime source, not a leftover file.
    archive = pcc_runtime_archive
    # Structured artifact verdict: a missing prebuilt archive is an explicit
    # UNAVAILABLE prerequisite, never evidence about the archive's contents;
    # only the executed ``ar t`` probe below can claim runtime behavior
    # (AUD-P2-DEPENDENCY-RUNTIME-ARCHIVE-VERDICT).
    verdict = probe_artifact_dependency(archive, kind="runtime-archive")
    if not verdict.available:
        pytest.fail(verdict.skip_reason())

    harness = tmp_path / "relocate_copy_single_forward_harness.c"
    harness.write_text(
        """
        #include "pcc/runtime/include/py_runtime.h"
        #include <stdio.h>
        #include <stdlib.h>

        int main(void) {
            setenv("PCC_GC_BACKEND", "4", 1);
            /* Non-leaf zpage-resident tag: leaf scalars are excluded from the
             * relocation graph by the rework (see oversize test). */
            PyObject *old = pcc_gc_alloc(64, PY_TYPE_TUPLE, 0);
            if (old == NULL) return 2;
            /* Constructors publish a completed object; a fresh one is
             * never relocated (half-initialized fields). */
            pcc_gc_publish_initialized(old);
            pcc_gc_reset_relocation_set();
            pcc_gc_select_relocation_set(1);
            PyObject *moved = pcc_gc_relocate_copy(old, 64);
            printf("first_null=%d\\n", moved == NULL);
            printf("still_selected=%lld\\n", (long long)pcc_gc_relocation_set_contains(old));
            PyObject *moved_again = pcc_gc_relocate_copy(old, 64);
            printf("second_null=%d\\n", moved_again == NULL);
            if (moved_again != NULL) {
                pcc_gc_release(moved_again);
            }
            if (moved != NULL) {
                pcc_gc_release(moved);
            }
            pcc_gc_release(old);
            return 0;
        }
        """,
        encoding="utf-8",
    )
    exe = tmp_path / "relocate_copy_single_forward_harness"
    compile_res = subprocess.run(
        [
            "cc",
            "-I", str(REPO_ROOT),
            str(harness),
            str(archive),
            "-o", str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    assert compile_res.returncode == 0, compile_res.stderr

    run_res = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(REPO_ROOT),
    )
    assert run_res.returncode == 0, run_res.stderr
    assert run_res.stdout == (
        "first_null=0\n"
        "still_selected=0\n"
        "second_null=1\n"
    )


def test_pcc_python_relocating_step_copies_simple_object(tmp_path, pcc_runtime_archive):
    # The archive built from the current runtime source, not a leftover file.
    archive = pcc_runtime_archive
    # Structured artifact verdict: a missing prebuilt archive is an explicit
    # UNAVAILABLE prerequisite, never evidence about the archive's contents;
    # only the executed ``ar t`` probe below can claim runtime behavior
    # (AUD-P2-DEPENDENCY-RUNTIME-ARCHIVE-VERDICT).
    verdict = probe_artifact_dependency(archive, kind="runtime-archive")
    if not verdict.available:
        pytest.fail(verdict.skip_reason())

    harness = tmp_path / "relocating_step_harness.c"
    harness.write_text(
        """
        #include "pcc/runtime/include/py_runtime.h"
        #include <stdint.h>
        #include <stdio.h>
        #include <stdlib.h>

        /* GC4 counts no references: a raw stack word naming the object pins
         * it in place. Its live reference is a registered off-stack root and
         * the harness keeps the old address only masked. */
        #define PROBE_MASK ((uintptr_t)0x5a5a5a5a5a5a5a5aULL)

        int main(void) {
            setenv("PCC_GC_BACKEND", "4", 1);
            PyObject **cells = calloc(2, sizeof(PyObject *));
            if (cells == NULL) return 3;
            PyObject **root = &cells[0];
            /* An untraced slot the evacuation does not remap: loading it
             * must take the read barrier's forwarding. */
            PyObject **stale = &cells[1];
            pcc_gc_scheduler_root_register(root);
            uintptr_t masked;
            int64_t old_id;
            {
                /* Non-leaf zpage-resident tag: leaf scalars are excluded from
                 * the relocation graph by the rework, so they never relocate. */
                PyObject *old = pcc_gc_alloc(64, PY_TYPE_TUPLE, 0);
                if (old == NULL) return 2;
                /* Constructors publish a completed object; a fresh one is
                 * never relocated (half-initialized fields). */
                pcc_gc_publish_initialized(old);
                old_id = pcc_gc_object_id(old);
                pcc_gc_store_ptr(NULL, root, old);
                *stale = old;
                masked = (uintptr_t)old ^ PROBE_MASK;
                old = NULL;
            }
            pcc_gc_telemetry_reset();
            /* A step spends its budget on earlier phases first (remembered
             * roots, aging); step until the evacuation moves the object. */
            int steps = 0;
            while (steps < 64 && pcc_gc_telemetry(PCC_GC_COUNTER_RELOCATION_FORWARDS) == 0) {
                if (pcc_gc_step(2) <= 0) break;
                steps += 1;
            }
            PyObject *old = (PyObject *)(masked ^ PROBE_MASK);
            printf("relocated=%d\\n", steps > 0 && steps < 64);
            printf("forwards=%lld\\n", (long long)pcc_gc_telemetry(PCC_GC_COUNTER_RELOCATION_FORWARDS));
            printf("root_remapped=%d\\n", *root != old);
            PyObject *loaded = pcc_gc_load_ptr(NULL, stale);
            printf("same_old=%d\\n", loaded == old);
            printf("same_root=%d\\n", loaded == *root);
            printf("same_id=%d\\n", pcc_gc_object_id(loaded) == old_id);
            printf("barrier_forwards=%lld\\n", (long long)pcc_gc_telemetry(PCC_GC_COUNTER_RELOCATION_BARRIER_FORWARDS));
            *stale = NULL;
            pcc_gc_store_ptr(NULL, root, NULL);
            pcc_gc_scheduler_root_unregister(root);
            free(cells);
            return 0;
        }
        """,
        encoding="utf-8",
    )
    exe = tmp_path / "relocating_step_harness"
    compile_res = subprocess.run(
        [
            "cc",
            "-I", str(REPO_ROOT),
            str(harness),
            str(archive),
            "-o", str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    assert compile_res.returncode == 0, compile_res.stderr

    run_res = subprocess.run(
        [str(exe)],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(REPO_ROOT),
    )
    assert run_res.returncode == 0, run_res.stderr
    assert run_res.stdout == (
        "relocated=1\n"
        "forwards=1\n"
        "root_remapped=1\n"
        "same_old=0\n"
        "same_root=1\n"
        "same_id=1\n"
        "barrier_forwards=1\n"
    )


def test_pcc_python_traceback_helpers_ignore_ambient_exception_tls(tmp_path):
    from pcc.frontends.python.pipeline import compile_python

    src = REPO_ROOT / "pcc" / "runtime" / "py" / "py_exc_traceback.py"
    ll = tmp_path / "py_exc_traceback.ll"
    # Compile the runtime module the way the runtime build does.
    compile_python(
        str(src),
        str(ll),
        emit_llvm_only=True,
        python_library=True,
    )
    ir_text = ll.read_text(encoding="utf-8")
    match = re.search(
        r"define (?:external )?i64 @user_py_exc_traceback__is_exception"
        r"\([^)]*\)[^{]*\{(?P<body>.*?)\n\}",
        ir_text,
        re.S,
    )
    assert match is not None
    assert "py_err_occurred" not in match.group("body")


_COLLIDING_HASH_PROGRAM = """class Key:
    def __init__(self, hashed, number):
        self.hashed = hashed
        self.number = number

    def __hash__(self):
        return self.hashed

    def __eq__(self, other):
        return isinstance(other, Key) and self.number == other.number


# Negative hashes whose low bits collide and whose high bits differ: probing
# reaches every slot only through the unsigned perturbation of the full hash.
keys = [Key(-(1 << 62) + (i << 40) - 8, i) for i in range(300)]
keys += [Key(-2 - (i << 3), 1000 + i) for i in range(300)]
table = {}
members = set()
for key in keys:
    table[key] = key.number
    members.add(key)
print("dict", len(table), [k.number for k in keys if table.get(k) != k.number])
print("set", len(members), [k.number for k in keys if k not in members])
for index, key in enumerate(keys):
    if index % 3 == 0:
        del table[key]
        members.discard(key)
kept = [k.number for i, k in enumerate(keys) if i % 3 != 0]
print("dict-deleted", len(table), [k.number for i, k in enumerate(keys) if (k in table) != (i % 3 != 0)])
print("set-deleted", len(members), sorted(k.number for k in members) == kept)
for key in keys[::6]:
    table[key] = -key.number
    members.add(key)
print("dict-reinserted", len(table), [k.number for k in keys[::6] if table.get(k) != -k.number])
print("set-reinserted", len(members), all(k in members for k in keys[::6]))
"""


@pytest.fixture(scope="module")
def colliding_hash_outputs(tmp_path_factory):
    """(CPython output, pcc native output) of one colliding-hash program."""
    from pcc.frontends.python.pipeline import compile_python

    work = tmp_path_factory.mktemp("colliding_hashes")
    source = work / "colliding_hashes.py"
    source.write_text(_COLLIDING_HASH_PROGRAM, encoding="utf-8")
    reference = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=120, check=True,
    )
    binary = work / "colliding_hashes.out"
    compile_python(str(source), str(binary), backend="self",
                   ir_scaffold_mode="on")
    native = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=120,
        env=dict(os.environ, PATH="/nonexistent"),
    )
    assert (native.returncode, native.stderr) == (0, "")
    return reference.stdout.splitlines(), native.stdout.splitlines()


def test_pcc_python_dict_lookup_matches_unsigned_hash_perturb(colliding_hash_outputs):
    reference, native = colliding_hash_outputs
    lines = [line for line in reference if line.startswith("dict")]
    assert lines == [line for line in native if line.startswith("dict")]
    assert lines[0] == "dict 600 []"


def test_pcc_python_set_lookup_matches_unsigned_hash_perturb(colliding_hash_outputs):
    reference, native = colliding_hash_outputs
    lines = [line for line in reference if line.startswith("set")]
    assert lines == [line for line in native if line.startswith("set")]
    assert lines[0] == "set 600 []"


def test_runtime_mirror_probe_and_backend0_latch_source_parity():
    # The set and dict probes share one unsigned perturbation step: the two
    # mirrors must stay the same function. Probing behavior itself (every
    # colliding key found after inserts, deletes and reinserts, as CPython
    # does) is executed by the colliding-hash tests below.
    def perturb_helper(path: str) -> str:
        tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
        helper = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_perturb_shift5"
        )
        return ast.dump(helper)

    assert perturb_helper("pcc/runtime/py/py_set.py") == perturb_helper("pcc/runtime/py/py_dict.py")

    py_gc = (REPO_ROOT / "pcc/runtime/py/py_gc_backend.py").read_text(encoding="utf-8")
    gc_state = (
        REPO_ROOT / "pcc/runtime/py/freestanding_gc_state.py"
    ).read_text(encoding="utf-8")
    assert 'define_global_i32("pcc_gc_backend0_frame_roots_enabled", 0)' in gc_state
    assert 'store_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0, 1)' in py_gc
    assert 'load_i32(global_addr("pcc_gc_backend0_frame_roots_enabled"), 0)' in py_gc


def test_py_tuple_port_spike_runs_correctly(tmp_path):
    if not SPIKE_SRC.exists():
        pytest.fail(f"spike source missing: {SPIKE_SRC}")
    out = tmp_path / "py_tuple_spike"
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    compile_res = subprocess.run(
        [_pcc_binary(), str(SPIKE_SRC), "-o", str(out)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert compile_res.returncode == 0, (
        f"spike compile failed\n{compile_res.stderr}"
    )
    run_res = subprocess.run(
        [str(out)],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(REPO_ROOT),
    )
    assert run_res.returncode == 0, (
        f"spike run failed rc={run_res.returncode}\n"
        f"stdout: {run_res.stdout!r}\nstderr: {run_res.stderr!r}"
    )
    lines = run_res.stdout.strip().splitlines()
    assert lines == ["len 3", "10", "20", "30"], (
        f"unexpected spike output: {lines}"
    )


def test_py_tuple_port_spike_under_pcc_runtime_cc(tmp_path):
    """Same as above but with the pcc-emitted runtime archive."""
    if not SPIKE_SRC.exists():
        pytest.fail(f"spike source missing: {SPIKE_SRC}")
    out = tmp_path / "py_tuple_spike_pcc"
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PCC_RUNTIME_CC"] = "pcc"
    compile_res = subprocess.run(
        [_pcc_binary(), str(SPIKE_SRC), "-o", str(out)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert compile_res.returncode == 0, (
        f"spike compile failed (pcc runtime)\n{compile_res.stderr}"
    )
    run_res = subprocess.run(
        [str(out)],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(REPO_ROOT),
    )
    assert run_res.returncode == 0, (
        f"spike run failed (pcc runtime) rc={run_res.returncode}\n"
        f"stdout: {run_res.stdout!r}\nstderr: {run_res.stderr!r}"
    )
    lines = run_res.stdout.strip().splitlines()
    assert lines == ["len 3", "10", "20", "30"], (
        f"unexpected spike output (pcc runtime): {lines}"
    )
