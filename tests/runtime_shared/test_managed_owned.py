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
    _run_managed_fixture(tmp_path, monkeypatch, "managed_graph.c")


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=lambda: bool(os.environ.get("PCC_RUNTIME_ARCHIVE")) and sys.platform == "linux" and platform.machine() == "x86_64")
def test_shared_scalar_slice_owner_survives_producer_and_collection(tmp_path, monkeypatch):
    _run_managed_fixture(tmp_path, monkeypatch, "scalar_slice_owner.c")


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=lambda: bool(os.environ.get("PCC_RUNTIME_ARCHIVE")) and sys.platform == "linux" and platform.machine() == "x86_64")
def test_go_scalar_slice_descriptor_calls_the_real_opt_in_allocator(tmp_path, monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.go.scalar_slice_runtime import ScalarSliceRuntime
    from pcc.ir.compat import ir

    module = ir.Module()
    module.triple = TARGET
    pointer = ir.IntType(8).as_pointer()
    i64 = ir.IntType(64)
    fn = ir.Function(module, ir.FunctionType(ir.IntType(32), [i64, pointer.as_pointer()]),
                     "scalar_slice_alloc_words")
    builder = ir.IRBuilder(fn.append_basic_block("entry"))
    runtime = ScalarSliceRuntime(module, gc_backend=0)
    builder.ret(runtime.allocate(builder, "uintptr", *fn.args))
    adapter = tmp_path / "go_scalar_slice.o"
    adapter.write_bytes(emit_owned_object(str(module), TARGET))
    _run_managed_fixture(tmp_path, monkeypatch, "scalar_slice_owner.c",
                         extra_object=adapter, cpp_args=["-DPCC_TEST_GO_SCALAR_ADAPTER"])


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=lambda: (
    bool(os.environ.get("PCC_RUNTIME_ARCHIVE")) and bool(os.environ.get("PCC_GO_REFERENCE"))
    and bool(os.environ.get("PCC_GO_REFERENCE_CACHE"))
    and sys.platform == "linux" and platform.machine() == "x86_64"
))
@pytest.mark.xdist_group(name="go-reference")
def test_go_scalar_first_append_returns_a_live_owner_and_matches_go(tmp_path, monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.go.scalar_slice_runtime import compile_scalar_first_append_entry
    from tests.go.owned_execution import Execution, compile_reference, run

    fixture_dir = ROOT / "tests/go/fixtures"
    producer = fixture_dir / "managed_slice_first_producer.go"
    original = (producer.name, producer.read_text())
    module, symbol = compile_scalar_first_append_entry((original,), function_name="first",
                                                      target_triple=TARGET, gc_backend=0)
    adapter = tmp_path / "go_first_append.o"
    adapter.write_bytes(emit_owned_object(str(module), TARGET))
    expected = b"".join(x.to_bytes(8, "little") for x in (0, 1 << 63, (1 << 64) - 1))
    observed = _run_managed_fixture(tmp_path, monkeypatch, "scalar_slice_first_append.c",
                                    extra_object=adapter, expected_gc0_stdout=expected,
                                    cpp_args=["-Dscalar_slice_first_append=" + symbol])
    source = fixture_dir / "managed_slice_first_append.go"
    reference = compile_reference((original, (source.name, source.read_text())),
                                  tmp_path / "reference", compiler=os.environ["PCC_GO_REFERENCE"],
                                  cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    for original_source in (source, producer):
        assert (reference.parent / original_source.name).read_bytes() == original_source.read_bytes()
    assert run(reference) == Execution(0, observed, b"")


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=lambda: (
    bool(os.environ.get("PCC_RUNTIME_ARCHIVE")) and bool(os.environ.get("PCC_GO_REFERENCE"))
    and bool(os.environ.get("PCC_GO_REFERENCE_CACHE"))
    and sys.platform == "linux" and platform.machine() == "x86_64"
))
@pytest.mark.xdist_group(name="go-reference")
def test_go_scalar_full_growth_preserves_both_owners_and_matches_go(tmp_path, monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.go.scalar_slice_runtime import ScalarSliceRuntime
    from pcc.ir.compat import ir
    from tests.go.owned_execution import Execution, compile_reference, run

    module = ir.Module()
    module.triple = TARGET
    runtime = ScalarSliceRuntime(module, gc_backend=0)
    pointer = ir.IntType(8).as_pointer()
    i64 = ir.IntType(64)
    seed = ir.Function(module, ir.FunctionType(ir.IntType(32), [i64, pointer.as_pointer()]),
                       "scalar_slice_seed")
    builder = ir.IRBuilder(seed.append_basic_block("entry"))
    builder.ret(runtime.allocate(builder, "uintptr", *seed.args))
    append = runtime.full_append_function("uintptr")
    grow = ir.Function(module, append.function_type, "scalar_slice_grow")
    builder = ir.IRBuilder(grow.append_basic_block("entry"))
    builder.ret(builder.call(append, list(grow.args)))
    adapter = tmp_path / "go_full_append.o"
    adapter.write_bytes(emit_owned_object(str(module), TARGET))
    expected = b"".join(x.to_bytes(8, "little") for x in (3, 11, 7, 5, (1 << 64) - 1))
    observed = _run_managed_fixture(tmp_path, monkeypatch, "scalar_slice_grow.c",
                                    extra_object=adapter, expected_gc0_stdout=expected)
    original = ROOT / "tests/go/fixtures/managed_slice_growth.go"
    reference = compile_reference(((original.name, original.read_text()),), tmp_path / "reference",
                                  compiler=os.environ["PCC_GO_REFERENCE"],
                                  cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert (reference.parent / original.name).read_bytes() == original.read_bytes()
    assert run(reference) == Execution(0, observed, b"")


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=lambda: (
    bool(os.environ.get("PCC_RUNTIME_ARCHIVE")) and bool(os.environ.get("PCC_GO_REFERENCE"))
    and bool(os.environ.get("PCC_GO_REFERENCE_CACHE"))
    and sys.platform == "linux" and platform.machine() == "x86_64"
))
@pytest.mark.xdist_group(name="go-reference")
def test_go_scalar_within_capacity_aliases_share_writes_and_match_go(tmp_path, monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.go.scalar_slice_runtime import ScalarSliceRuntime
    from pcc.ir.compat import ir
    from tests.go.owned_execution import Execution, compile_reference, run

    module = ir.Module()
    module.triple = TARGET
    runtime = ScalarSliceRuntime(module, gc_backend=0)
    pointer = ir.IntType(8).as_pointer()
    i64 = ir.IntType(64)
    seed = ir.Function(module, ir.FunctionType(ir.IntType(32), [i64, pointer.as_pointer()]),
                       "scalar_slice_seed")
    builder = ir.IRBuilder(seed.append_basic_block("entry"))
    builder.ret(runtime.allocate(builder, "uintptr", *seed.args))
    append = runtime.in_capacity_append_function("uintptr")
    write = ir.Function(module, append.function_type, "scalar_slice_append_within")
    builder = ir.IRBuilder(write.append_basic_block("entry"))
    builder.ret(builder.call(append, list(write.args)))
    adapter = tmp_path / "go_append_within.o"
    adapter.write_bytes(emit_owned_object(str(module), TARGET))
    expected = b"".join(x.to_bytes(8, "little") for x in (3, 5, (1 << 64) - 1, 0, 11))
    observed = _run_managed_fixture(tmp_path, monkeypatch, "scalar_slice_append_within.c",
                                    extra_object=adapter, expected_gc0_stdout=expected)
    original = ROOT / "tests/go/fixtures/managed_slice_within.go"
    reference = compile_reference(((original.name, original.read_text()),), tmp_path / "reference",
                                  compiler=os.environ["PCC_GO_REFERENCE"],
                                  cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert (reference.parent / original.name).read_bytes() == original.read_bytes()
    assert run(reference) == Execution(0, observed, b"")


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=lambda: (
    bool(os.environ.get("PCC_RUNTIME_ARCHIVE")) and bool(os.environ.get("PCC_GO_REFERENCE"))
    and bool(os.environ.get("PCC_GO_REFERENCE_CACHE"))
    and sys.platform == "linux" and platform.machine() == "x86_64"
))
@pytest.mark.xdist_group(name="go-reference")
def test_go_scalar_loop_state_transitions_are_failure_atomic_and_match_go(tmp_path, monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.go.scalar_slice_runtime import ScalarSliceRuntime
    from pcc.ir.compat import ir
    from tests.go.owned_execution import Execution, compile_reference, run

    module = ir.Module()
    module.triple = TARGET
    runtime = ScalarSliceRuntime(module, gc_backend=0)
    pointer = ir.IntType(8).as_pointer()
    i64 = ir.IntType(64)
    seed = ir.Function(module, ir.FunctionType(ir.IntType(32), [i64, pointer.as_pointer()]),
                       "scalar_slice_seed")
    builder = ir.IRBuilder(seed.append_basic_block("entry"))
    builder.ret(runtime.allocate(builder, "uintptr", *seed.args))
    append = runtime.append_owned_state_function("uintptr")
    write = ir.Function(module, append.function_type, "scalar_slice_append_state")
    builder = ir.IRBuilder(write.append_basic_block("entry"))
    builder.ret(builder.call(append, list(write.args)))
    adapter = tmp_path / "go_append_state.o"
    adapter.write_bytes(emit_owned_object(str(module), TARGET))
    expected = b"".join(x.to_bytes(8, "little") for x in ((1 << 63) | i for i in range(9)))
    observed = _run_managed_fixture(tmp_path, monkeypatch, "scalar_slice_append_state.c",
                                    extra_object=adapter, expected_gc0_stdout=expected)
    original = ROOT / "tests/go/fixtures/managed_slice_append_state.go"
    reference = compile_reference(((original.name, original.read_text()),), tmp_path / "reference",
                                  compiler=os.environ["PCC_GO_REFERENCE"],
                                  cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert (reference.parent / original.name).read_bytes() == original.read_bytes()
    assert run(reference) == Execution(0, observed, b"")


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=lambda: (
    bool(os.environ.get("PCC_RUNTIME_ARCHIVE")) and bool(os.environ.get("PCC_GO_REFERENCE"))
    and bool(os.environ.get("PCC_GO_REFERENCE_CACHE"))
    and sys.platform == "linux" and platform.machine() == "x86_64"
))
@pytest.mark.xdist_group(name="go-reference")
def test_identical_go_slice_building_loop_executes_with_owned_result(tmp_path, monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.go.scalar_slice_runtime import compile_scalar_append_loop_entry
    from tests.go.owned_execution import Execution, compile_reference, run

    fixtures = ROOT / "tests/go/fixtures"
    producer = fixtures / "managed_slice_loop_producer.go"
    observer = fixtures / "managed_slice_loop_observer.go"
    source = (producer.name, producer.read_text())
    module, symbol = compile_scalar_append_loop_entry((source,), function_name="build",
                                                     target_triple=TARGET, gc_backend=0)
    adapter = tmp_path / "go_slice_loop.o"
    adapter.write_bytes(emit_owned_object(str(module), TARGET))
    expected = b"".join(word.to_bytes(8, "little") for n in (0, 1, 10) for word in (n, *range(n)))
    observed = _run_managed_fixture(tmp_path, monkeypatch, "scalar_slice_source_loop.c",
                                    extra_object=adapter, expected_gc0_stdout=expected,
                                    cpp_args=["-Dscalar_slice_source_loop=" + symbol])
    originals = (source, (observer.name, observer.read_text()))
    reference = compile_reference(originals, tmp_path / "reference",
                                  compiler=os.environ["PCC_GO_REFERENCE"],
                                  cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    for original in (producer, observer):
        assert (reference.parent / original.name).read_bytes() == original.read_bytes()
    assert run(reference) == Execution(0, observed, b"")


def _run_managed_fixture(tmp_path, monkeypatch, fixture_name, *, extra_object=None, cpp_args=(),
                         expected_gc0_stdout=b""):
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from pcc.runtime.shared_managed_build import managed_runtime_objects
    from tests.owned_runtime_c_fixture import admitted_runtime

    archive = admitted_runtime()
    fixture = Path(__file__).with_name("fixtures") / fixture_name
    binary = tmp_path / fixture.stem
    with monkeypatch.context() as patch:
        def forbidden(*args, **kwargs):
            raise AssertionError("managed compile/link attempted external ownership")
        patch.setattr(subprocess, "Popen", forbidden)
        objects = managed_runtime_objects(str(ROOT / "pcc/runtime"), str(tmp_path / "optional"), TARGET, enabled=True)
        evaluator = CEvaluator(backend="self", target_triple=TARGET)
        units = evaluator.compile_translation_units(
            [TranslationUnit(fixture.name, str(fixture), fixture.read_text())],
            use_system_cpp=False, use_compile_cache=False, cpp_args=list(cpp_args),
            include_dirs=[str(ROOT / "pcc/runtime/include"), str(ROOT / "utils/fake_libc_include")],
        )
        if extra_object is not None:
            objects.append(str(extra_object))
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
        expected_stdout = expected_gc0_stdout if collector == 0 else b""
        assert (result.returncode, result.stdout, result.stderr) == (0, expected_stdout, b""), collector
    return (tmp_path / "gc0.stdout").read_bytes()
