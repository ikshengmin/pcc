"""Explicit shared-managed selection; real GC0 graph proof needs owned archive.

This does not qualify Go/Java semantics, other collectors, concurrency, Mac,
performance, or native pcc1. The C caller and pcc-Python adapter share the real
unchanged production allocator, root registry, slot walker and cycle collector.
"""
from pathlib import Path
import ast
import os
import platform
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
TARGET = "x86_64-unknown-linux-gnu"


def test_managed_selection_defaults_off_without_imports_or_output(tmp_path):
    from pcc.runtime.shared_managed_build import managed_runtime_objects

    before = set(sys.modules)
    missing = tmp_path / "must-not-exist"
    assert managed_runtime_objects("missing", str(missing), "invalid") == []
    assert managed_runtime_objects("missing", str(missing), "invalid", enabled=False, gc_backend=4) == []
    assert set(sys.modules) == before
    assert not missing.exists()


@pytest.mark.parametrize("target,collector", [(TARGET, n) for n in range(1, 5)] + [
    ("aarch64-unknown-linux-gnu", 0), ("arm64-apple-darwin", 0),
])
def test_managed_selection_rejects_unqualified_lanes_before_build(tmp_path, target, collector):
    from pcc.runtime.shared_managed_build import managed_runtime_objects

    out = tmp_path / "not-created"
    with pytest.raises(ValueError, match="Linux x86-64 GC0 only"):
        managed_runtime_objects("missing", str(out), target, enabled=True, gc_backend=collector)
    assert not out.exists()


def test_managed_module_is_not_in_default_runtime_inventory():
    from pcc.frontends.python.owned_runtime_build import runtime_modules

    assert "py_shared_managed" not in runtime_modules(str(ROOT / "pcc/runtime"), TARGET)
    # The only selected sources are in the additive, explicit helper. No
    # Python/default runtime import is created by the optional module itself.
    module = ROOT / "pcc/runtime/py/py_shared_managed.py"
    tree = ast.parse(module.read_text())
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert {"pcc_rt_managed_alloc", "pcc_rt_managed_collect", "pcc_rt_managed_ref_store"} <= names


def test_managed_optional_object_has_exact_abi_and_no_boxed_adapter_arithmetic(tmp_path, monkeypatch):
    from pcc.runtime.shared_managed_build import managed_runtime_objects

    def forbidden(*args, **kwargs):
        raise AssertionError("optional object compilation spawned an external owner")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    objects = managed_runtime_objects(str(ROOT / "pcc/runtime"), str(tmp_path), TARGET, enabled=True)
    assert objects == [str(tmp_path / "py_shared_managed.o")]
    assert Path(objects[0]).read_bytes().startswith(b"\x7fELF")
    ir = (tmp_path / "py_shared_managed.ll").read_text()
    bodies = dict(re.findall(r"^define[^\n]*@([^ (]+)\([^\n]*\)[^\n]*\{\n(.*?)^}", ir, re.M | re.S))
    for name, signature in {
        "pcc_rt_managed_alloc": ("ptr", "i64", "ptr"),
        "pcc_rt_managed_clone": ("ptr", "ptr"),
        "pcc_rt_managed_ref_store": ("ptr", "i64", "ptr"),
        "pcc_rt_managed_ref_load": ("ptr", "i64", "ptr"),
        "pcc_rt_managed_identity_equal": ("ptr", "ptr"),
    }.items():
        match = re.search(r"define(?:\s+external)?\s+i32\s+@" + name + r"\(([^)]*)\)", ir)
        assert match, name
        assert tuple(arg.strip().split()[0] for arg in match[1].split(",")) == signature
    for name, body in bodies.items():
        if name.startswith("pcc_rt_managed_"):
            assert "@py_int_" not in body, name
            assert "@py_obj_" not in body, name
            assert "@_pcc_py_module_top_" not in body, name
    assert "@pcc_gc_alloc(" in bodies["pcc_rt_managed_alloc"]
    assert "@py_gc_track(" in bodies["pcc_rt_managed_alloc"]
    assert "@pcc_gc_store_ptr(" in bodies["pcc_rt_managed_ref_store"]
    assert "@py_gc_collect(" in bodies["pcc_rt_managed_collect"]


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=lambda: bool(os.environ.get("PCC_RUNTIME_ARCHIVE")) and sys.platform == "linux" and platform.machine() == "x86_64")
def test_shared_managed_real_gc0_graph_and_rejection(tmp_path, monkeypatch):
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from pcc.runtime.shared_managed_build import managed_runtime_objects
    from tests.owned_runtime_c_fixture import admitted_runtime

    archive = admitted_runtime()
    fixture = Path(__file__).with_name("fixtures") / "managed_graph.c"
    binary = tmp_path / "managed_graph"
    with monkeypatch.context() as patch:
        def forbidden(*args, **kwargs):
            raise AssertionError("managed compile/link attempted external ownership")
        patch.setattr(subprocess, "Popen", forbidden)
        objects = managed_runtime_objects(str(ROOT / "pcc/runtime"), str(tmp_path / "optional"), TARGET, enabled=True)
        evaluator = CEvaluator(backend="self", target_triple=TARGET)
        units = evaluator.compile_translation_units(
            [TranslationUnit(fixture.name, str(fixture), fixture.read_text())],
            use_system_cpp=False, use_compile_cache=False,
            include_dirs=[str(ROOT / "pcc/runtime/include"), str(ROOT / "utils/fake_libc_include")],
        )
        evaluator.emit_executable(units, str(binary), link_args=[*objects, str(archive)])
    def limit_native_address_space():
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (64 * 1024 * 1024, 64 * 1024 * 1024))

    for collector in range(5):
        # GC1-4 are *rejection tests*, never collection qualification.
        result = subprocess.run([str(binary)], env={**os.environ, "PCC_GC_BACKEND": str(collector)},
                                capture_output=True, timeout=10, preexec_fn=limit_native_address_space)
        (tmp_path / f"gc{collector}.stdout").write_bytes(result.stdout)
        (tmp_path / f"gc{collector}.stderr").write_bytes(result.stderr)
        assert (result.returncode, result.stdout, result.stderr) == (0, b"", b""), collector
