"""Go consumes shared compiler plans without opening managed native lowering.

Go specification: Types, String types, Type identity, Interface types, Values.
https://go.dev/ref/spec#Types
https://go.dev/ref/spec#String_types
https://go.dev/ref/spec#Type_identity
https://go.dev/ref/spec#Interface_types
The exact static-string program has authored owned-native and official-Go
obligations below. No managed runtime execution is claimed. All are CI-only.
"""
import os

import pytest

from pcc.frontends.go import GoDiagnostic, compile_go
from pcc.frontends.go.managed_objects import (
    managed_string_plan, static_string_plan, type_identity, value_plan,
)
from pcc.frontends.go.records import DefinedType, Field, StructType
from pcc.frontends.go.types import ArrayType, ERROR_TYPE, PointerType, SliceType
from pcc.support.managed_objects import (
    MANAGED_REQUIREMENTS, ManagedContractError, ManagedTypeIdentity,
    pointer_map, unmet_requirements,
)
from tests.go.owned_execution import (
    Execution, compile_owned, compile_reference, run, write_artifact,
)
from tests.go.test_execution import forbid_external_compiler


ORIGINAL = r'''package main
func Result() int64 {
    s := "\x00\xffé"
    var empty string
    if len(s) != 4 || s != "\000\377é" || empty != "" { return -1 }
    if "\xff" <= "\x7f" { return -2 }
    return 42
}
func main() { if Result() != 42 { panic(1) } }
'''


def units():
    return (('managed_static_strings.go', ORIGINAL),)


def test_shared_identity_preserves_defined_declarations_and_aliases():
    first = DefinedType('Counter', 'app', 'int64')
    alias = first
    second = DefinedType('Counter', 'app', 'int64')
    other = DefinedType('Counter', 'other', 'int64')
    assert type_identity(first) == type_identity(alias)
    assert type_identity(first) != type_identity(second) != type_identity(other)
    assert type_identity(first) != type_identity('int64')
    assert type_identity(PointerType(first)) == type_identity(PointerType(alias))
    assert type_identity(PointerType(first)) != type_identity(PointerType(second))
    assert type_identity(ArrayType(2, first)) != type_identity(ArrayType(3, first))
    assert type_identity(StructType((Field('value', first),))) != type_identity(StructType((Field('value', second),)))
    assert type_identity('string') != ManagedTypeIdentity('java', ('unresolved-bootstrap',), 'java/lang/String')
    main_error = DefinedType('error', '', 'int')
    assert type_identity(main_error).origin == ('package', '<main>')
    assert type_identity(ERROR_TYPE).origin == ('universe',)
    assert type_identity(main_error) != type_identity(ERROR_TYPE)


def test_resolved_scalar_payload_is_not_a_registered_box_or_native_abi():
    plan = value_plan(DefinedType('Count', 'app', 'int64'))
    assert plan.layout.identity == plan.identity
    resolved = pointer_map(plan.layout, pointer_size=8)
    assert resolved.fixed_size == 8 and resolved.owned == ()
    assert set(MANAGED_REQUIREMENTS) | {'object_abi', 'go-value-copy-semantics'} <= set(unmet_requirements(plan))
    assert plan.storage == 'unresolved'


def test_error_plan_separates_immutable_metadata_from_a_dynamic_value_edge():
    plan = value_plan(ERROR_TYPE)
    assert [field.path for field in plan.layout.scalars] == [('dynamic-type-and-method-table',)]
    assert [edge.path for edge in plan.layout.references] == [('dynamic-value',)]
    assert plan.layout.references[0].role == 'owned'
    assert plan.layout.fixed_size is None and plan.layout.references[0].offset is None
    assert {'go-interface-dynamic-type-and-method-table', 'go-interface-typed-nil-and-equality',
            'go-interface-box-copy', 'runtime_registration'} <= set(unmet_requirements(plan))
    with pytest.raises(ManagedContractError, match='unresolved'):
        pointer_map(plan.layout, pointer_size=8)


def test_pointer_and_aggregate_plans_do_not_claim_raw_pointer_traceability():
    pointer = value_plan(PointerType('int'))
    assert 'go-pointer-provenance-and-escape' in unmet_requirements(pointer)
    assert pointer.layout.references[0].target == type_identity('int')
    for dtype in (ArrayType(2, PointerType('int')), StructType((Field('value', 'string'),))):
        plan = value_plan(dtype)
        assert plan.layout is None
        assert 'go-aggregate-field-trace-and-copy-plan' in unmet_requirements(plan)


def test_static_bytes_are_not_managed_and_managed_spans_retain_the_base_owner():
    data = b'\x00\xff\xc3\xa9'
    span = static_string_plan(data)
    assert (span.backing, span.owner, span.offset, span.length) == ('static', None, 0, 4)
    assert static_string_plan(b'').length == 0
    with pytest.raises(ManagedContractError, match='immutable bytes'):
        static_string_plan(bytearray(data))
    owner = value_plan('string')
    managed = managed_string_plan(owner, offset=1, length=2)
    assert managed.owner is owner and managed.backing == 'managed'
    assert (managed.offset, managed.length) == (1, 2)
    assert {'rooted_lifetime', 'safepoint_reload', 'object_abi'} <= set(unmet_requirements(managed.owner))
    with pytest.raises(ManagedContractError, match='owner'):
        managed_string_plan(None, offset=0, length=1)


def test_byte_slice_shares_a_rooted_backing_plan_without_claiming_native_storage():
    from pcc.frontends.go.managed_objects import byte_backing_plan

    byte_slice = value_plan(SliceType('uint8'))
    named_byte_slice = value_plan(DefinedType('Buffer', 'app', SliceType('uint8')))
    owner = byte_backing_plan()
    assert byte_slice.identity != named_byte_slice.identity
    for plan in (byte_slice, named_byte_slice):
        assert [f.path for f in plan.layout.scalars] == [('offset',), ('length',), ('capacity',)]
        edge, = plan.layout.references
        assert (edge.path, edge.role, edge.target) == (('backing-owner',), 'owned', owner.identity)
        assert {'field_barriers', 'rooted_lifetime', 'safepoint_reload', 'object_abi'} <= set(unmet_requirements(plan))
        with pytest.raises(ManagedContractError, match='unresolved'):
            pointer_map(plan.layout, pointer_size=8)
    assert (owner.layout.element.fixed_size, owner.layout.element_stride) == (1, 1)
    assert owner.layout.element.references == ()
    assert managed_string_plan(owner, offset=1, length=2).owner is owner
    with pytest.raises(ManagedContractError):
        value_plan(SliceType(ERROR_TYPE))


def test_escaping_uintptr_slice_keeps_scalar_payload_separate_from_owner_root():
    from pcc.frontends.go.managed_objects import scalar_slice_backing_plan
    from pcc.support.managed_objects import allocation_extent

    view = value_plan(SliceType("uintptr"))
    backing = scalar_slice_backing_plan("uintptr")
    assert view.identity != type_identity(SliceType("uint8"))
    owner, = view.layout.references
    assert owner.role == "owned" and owner.target == backing.identity
    assert [slot.kind for slot in view.layout.scalars] == [
        "element-offset", "element-length", "element-capacity",
    ]
    assert allocation_extent(backing.layout, 3) == 24
    payload = pointer_map(backing.layout, pointer_size=8)
    assert payload.owned == () and payload.element.owned == () and payload.element_stride == 8
    assert "object_abi" in unmet_requirements(backing) and "rooted_lifetime" in unmet_requirements(view)
    assert "go-owned-slice-result-transfer" in unmet_requirements(view)
    with pytest.raises(ManagedContractError, match="overflows"):
        allocation_extent(backing.layout, (1 << 63) // 8)
    with pytest.raises(GoDiagnostic, match="slice types require shared-runtime") as caught:
        compile_go("package main;func sizes()[]uintptr{return nil};func main(){}")
    assert caught.value.managed_plan == view


def test_actual_static_string_lowering_consumes_common_span_and_retains_immutable_abi(monkeypatch):
    from pcc.frontends.go import lowering
    seen = []
    original = lowering.static_string_plan
    def record(data):
        plan = original(data)
        seen.append((data, plan))
        return plan
    monkeypatch.setattr(lowering, 'static_string_plan', record)
    module = compile_go(ORIGINAL)
    assert seen and any(data == b'\x00\xff\xc3\xa9' for data, _ in seen)
    assert all(plan.backing == 'static' and plan.owner is None for _, plan in seen)
    globals_ = [value for name, value in module.globals.items() if name.startswith('__pcc_go_string_')]
    assert globals_ and all(value.linkage == 'private' and value.global_constant for value in globals_)
    assert 'declare ' not in str(module)


def test_shared_plan_does_not_open_dynamic_string_or_interface_native_paths(monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported managed value reached IR'))
    for source, message in (
        ('package main;func main(){s:="a";_=s+"b"}', 'allocation'),
        ('package main;type E int;func(e E)Error()string{return "e"};func main(){var e error=E(1);_=e}', 'runtime'),
    ):
        with pytest.raises(GoDiagnostic, match=message):
            compile_go(source)


@pytest.mark.integration
def test_exact_static_backing_original_executes_owned_native(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units(), observe_entry=('Result', ()))
    binary = tmp_path / 'static-strings-owned'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, (42).to_bytes(8, 'little'), b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_static_backing_original_matches_official_go(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units())
    owned = tmp_path / 'static-strings-main'
    write_artifact(artifact, owned)
    reference = compile_reference(units(), tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert (reference.parent / 'managed_static_strings.go').read_bytes() == ORIGINAL.encode()
    assert run(owned) == run(reference) == Execution(0, b'', b'')


def test_opt_in_scalar_slice_emits_pointer_free_descriptor_and_alloc_call():
    from pcc.frontends.go.scalar_slice_runtime import ScalarSliceRuntime
    from pcc.ir.compat import ir

    module = ir.Module()
    module.triple = "x86_64-unknown-linux-gnu"
    pointer = ir.IntType(8).as_pointer()
    i64 = ir.IntType(64)
    probe = ir.Function(module, ir.FunctionType(ir.IntType(32), [i64, pointer.as_pointer()]), "probe")
    builder = ir.IRBuilder(probe.append_basic_block("entry"))
    runtime = ScalarSliceRuntime(module, gc_backend=0)
    words = runtime.descriptor("uintptr")
    assert words is runtime.descriptor("uintptr")
    assert runtime.descriptor("uint8") is not words
    assert words.global_constant and words.linkage == "private"
    assert tuple(item.value for item in words.initializer.value) == (1, 0, 8, 8, 0, 0)
    builder.ret(runtime.allocate(builder, "uintptr", *probe.args))
    assert "call i32 (ptr, i64, ptr) @pcc_rt_managed_alloc(ptr @__pcc_go_scalar_backing_uintptr" in str(module)
    assert "declare i32 @pcc_rt_managed_alloc(ptr, i64, ptr)" in str(module)
    assert 'pcc_gc_alloc' not in str(module)
    untouched = tuple(module.globals)
    with pytest.raises(ManagedContractError, match="qualified scalar"):
        runtime.descriptor("int64")
    assert tuple(module.globals) == untouched

    invalid = ir.Module()
    invalid.triple = module.triple
    runtime = ScalarSliceRuntime(invalid, gc_backend=0)
    with pytest.raises(ManagedContractError, match="requires its module"):
        runtime.allocate(builder, "uintptr", *probe.args)
    assert not invalid.globals
    conflicting = ir.Function(invalid, ir.FunctionType(i64, []), "pcc_rt_managed_alloc")
    bad_probe = ir.Function(invalid, probe.function_type, "probe")
    bad_builder = ir.IRBuilder(bad_probe.append_basic_block("entry"))
    with pytest.raises(ManagedContractError, match="incompatible"):
        runtime.allocate(bad_builder, "uintptr", *bad_probe.args)
    assert set(invalid.globals) == {conflicting.name, bad_probe.name}


@pytest.mark.parametrize("triple,backend", [
    ("aarch64-unknown-linux-gnu", 0), ("arm64-apple-darwin", 0),
    ("x86_64-unknown-linux-gnu", 3), ("x86_64-unknown-linux-gnu", False),
])
def test_opt_in_scalar_slice_descriptor_rejects_unqualified_lane_without_ir(triple, backend):
    from pcc.frontends.go.scalar_slice_runtime import ScalarSliceRuntime
    from pcc.ir.compat import ir

    module = ir.Module()
    module.triple = triple
    with pytest.raises(ManagedContractError, match="Linux x86-64 GC0"):
        ScalarSliceRuntime(module, gc_backend=backend)
    assert not module.globals


def test_opt_in_first_scalar_append_publishes_only_after_checked_initialization():
    from pcc.frontends.go.scalar_slice_runtime import ScalarSliceRuntime
    from pcc.ir.compat import ir

    module = ir.Module()
    module.triple = "x86_64-unknown-linux-gnu"
    runtime = ScalarSliceRuntime(module, gc_backend=0)
    fn = runtime.first_append_function("uintptr")
    assert fn is runtime.first_append_function("uintptr")
    assert fn.linkage == "internal"
    text = str(module)
    assert "alloca ptr" in text and "br i1" in text
    allocate = text.split("scalar_append_allocate:", 1)[1].split("scalar_append_invalid:", 1)[0]
    assert allocate.index("call i32 (ptr, i64, ptr) @pcc_rt_managed_alloc") < allocate.index("br i1")
    ready = text.split("scalar_append_ready:", 1)[1].split("scalar_append_done:", 1)[0]
    assert ready.index("@pcc_rt_managed_data") < ready.index("store i64") < ready.index("store ptr")
    with pytest.raises(ManagedContractError, match="qualified scalar"):
        runtime.first_append_function("int")
    with pytest.raises(GoDiagnostic, match="slice types require shared-runtime"):
        compile_go("package main;func first(x uintptr)[]uintptr{var s []uintptr;return append(s,x)};func main(){}")


def test_opt_in_closed_first_append_lowers_the_actual_source_producer():
    from pcc.frontends.go.scalar_slice_runtime import compile_scalar_first_append_entry
    from pcc.frontends.go.lowering import function_symbol

    source = ("package main; func Collect(value uintptr) []uintptr { "
              "var result []uintptr; return append(result, value) }")
    module, symbol = compile_scalar_first_append_entry(
        (("producer.go", source),), function_name="Collect",
        target_triple="x86_64-unknown-linux-gnu", gc_backend=0,
    )
    assert symbol == function_symbol("Collect")
    assert "define i32 @" + symbol in str(module)
    assert "@__pcc_go_scalar_append_first_uintptr" in str(module)
    with pytest.raises(GoDiagnostic, match="slice types require shared-runtime"):
        compile_go(source + "; func main(){}")


@pytest.mark.parametrize("producer", [
    "func F(v uintptr) []uintptr { var x []uintptr; x = append(x, v); return append(x, v) }",
    "func F(v uintptr) []uintptr { var x = [1]uintptr{v}; return append(x, v) }",
    "func F(append uintptr) []uintptr { var x []uintptr; return append(x, append) }",
    "func F(v uintptr) []uintptr { var append []uintptr; return append(append, v) }",
    "func F(v uint8) []uintptr { var x []uintptr; return append(x, v) }",
    "func F(v uintptr) []uintptr { var x []uintptr; return append(x, v...) }",
    "func F(v uintptr) (x []uintptr) { var y []uintptr; return append(y, v) }",
    "func F(v uintptr) []uintptr { var x []uintptr = nil; return append(x, v) }",
    "func F(v uintptr) []uintptr { var x []uintptr; return append(x, v) }; var x uintptr",
    "func F(v uintptr) []uintptr { var x []uintptr; return append(x, v) }; func extra() {}",
])
def test_opt_in_closed_first_append_rejects_unproved_source_before_emitting(producer):
    from pcc.frontends.go.scalar_slice_runtime import compile_scalar_first_append_entry

    with pytest.raises(GoDiagnostic):
        compile_scalar_first_append_entry(
            (("producer.go", "package main;" + producer),), function_name="F",
            target_triple="x86_64-unknown-linux-gnu", gc_backend=0,
        )


def test_opt_in_full_scalar_growth_copies_after_allocation_before_publishing():
    from pcc.frontends.go.scalar_slice_runtime import ScalarSliceRuntime
    from pcc.ir.compat import ir

    module = ir.Module()
    module.triple = "x86_64-unknown-linux-gnu"
    runtime = ScalarSliceRuntime(module, gc_backend=0)
    grow = runtime.full_append_function("uintptr")
    assert grow is runtime.full_append_function("uintptr")
    assert grow.linkage == "internal"
    text = str(module)
    before_alloc, rest = text.split("scalar_grow_allocate:", 1)
    assert "@pcc_rt_managed_descriptor" in before_alloc
    assert "@pcc_rt_managed_element_count" in before_alloc
    ready, done = rest.split("scalar_grow_ready:", 1)[1].split("scalar_grow_done:", 1)
    assert (ready.index("@pcc_rt_managed_data") < ready.index("@memcpy") <
            ready.index("store i64") < ready.index("store ptr"))
    assert "ret i32" in done
    with pytest.raises(ManagedContractError, match="qualified scalar"):
        runtime.full_append_function("int")


def test_opt_in_in_capacity_append_checks_length_and_does_not_allocate():
    from pcc.frontends.go.scalar_slice_runtime import ScalarSliceRuntime
    from pcc.ir.compat import ir

    module = ir.Module()
    module.triple = "x86_64-unknown-linux-gnu"
    runtime = ScalarSliceRuntime(module, gc_backend=0)
    append = runtime.in_capacity_append_function("uintptr")
    assert append is runtime.in_capacity_append_function("uintptr")
    before, write = str(module).split("scalar_in_capacity_write:", 1)
    assert "icmp ult i64" in before and "@pcc_rt_managed_element_count" in before
    assert "@pcc_rt_managed_data" in write and "store i64" in write
    assert "@pcc_rt_managed_alloc" not in str(module)
    assert "@memcpy" not in str(module)
    with pytest.raises(ManagedContractError, match="qualified scalar"):
        runtime.in_capacity_append_function("int")


def test_opt_in_append_owned_state_preserves_slots_until_success():
    from pcc.frontends.go.scalar_slice_runtime import ScalarSliceRuntime
    from pcc.ir.compat import ir

    module = ir.Module()
    module.triple = "x86_64-unknown-linux-gnu"
    runtime = ScalarSliceRuntime(module, gc_backend=0)
    append = runtime.append_owned_state_function("uintptr")
    assert append is runtime.append_owned_state_function("uintptr")
    text = str(module).split("define internal i32 @__pcc_go_scalar_append_owned_uintptr", 1)[1]
    assert "@__pcc_go_scalar_append_first_uintptr" in text
    assert "@__pcc_go_scalar_append_in_capacity_uintptr" in text
    assert "@__pcc_go_scalar_append_full_uintptr" in text
    publish = text.split("scalar_state_publish:", 1)[1].split("scalar_state_invalid:", 1)[0]
    assert publish.index("store ptr") < publish.index("store i64") < publish.index("@pcc_rt_managed_drop")
    with pytest.raises(ManagedContractError, match="qualified scalar"):
        runtime.append_owned_state_function("bool")


def test_opt_in_scalar_source_loop_returns_nil_or_an_owned_result():
    from pcc.frontends.go.scalar_slice_runtime import compile_scalar_append_loop_entry
    source = ("package main; func Build(n uint32) []uintptr { "
              "var dst []uintptr; for i := uint32(0); i < n; i++ { "
              "dst = append(dst, uintptr(i)) }; return dst }")
    module, symbol = compile_scalar_append_loop_entry(
        (("loop.go", source),), function_name="Build",
        target_triple="x86_64-unknown-linux-gnu", gc_backend=0,
    )
    text = str(module).split("define i32 @" + symbol, 1)[1]
    assert "phi i32" in text and "icmp ult i32" in text
    assert "@__pcc_go_scalar_append_owned_uintptr" in text
    assert "zext i32" in text
    fail = text.split("scalar_loop_failed:", 1)[1].split("scalar_loop_publish:", 1)[0]
    assert "@pcc_rt_managed_drop" in fail and "store" not in fail
    with pytest.raises(GoDiagnostic, match="slice types require shared-runtime"):
        compile_go(source + "; func main(){}")


@pytest.mark.parametrize("change", [
    ("i < n", "i <= n"),
    ("i++", "i--"),
    ("uintptr(i)", "uintptr(n)"),
    ("var dst []uintptr", "var dst []uintptr = nil"),
    ("return dst", "return nil"),
    ("n uint32", "uint32 uint32"),
    ("i := uint32(0)", "i := uint32(1)"),
])
def test_opt_in_scalar_source_loop_rejects_other_ownership_and_bounds(change):
    from pcc.frontends.go.scalar_slice_runtime import compile_scalar_append_loop_entry
    source = ("package main; func Build(n uint32) []uintptr { "
              "var dst []uintptr; for i := uint32(0); i < n; i++ { "
              "dst = append(dst, uintptr(i)) }; return dst }").replace(*change)
    with pytest.raises(GoDiagnostic):
        compile_scalar_append_loop_entry(
            (("loop.go", source),), function_name="Build",
            target_triple="x86_64-unknown-linux-gnu", gc_backend=0,
        )


def test_pinned_cpu_cache_sizes_producers_have_exclusive_source_owners():
    import hashlib
    import json
    from pathlib import Path
    from pcc.frontends.go.packages import parse_package
    from pcc.frontends.go.slice_proof import scalar_slice_owner_group

    root = Path(__file__).with_name("upstream") / "go1.27.3"
    selection = json.loads((root / "pcc-linux-amd64-math-bits-normal-plan.json").read_text())
    entries = selection["packages"]["internal/cpu"]["files"]["go"]
    path = root / "src/internal/cpu/datacache_x86.go"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "bbd50f64070d9497f062de27710a17d1aead867054d7dd9143bf063cdcd4fbea"
    units = []
    for name in entries:
        source = (path.parent / name).read_bytes()
        assert [len(source), hashlib.sha256(source).hexdigest()] == selection["files"]["src/internal/cpu/" + name]
        units.append((name, source.decode()))
    units = tuple(units)
    proof = scalar_slice_owner_group(parse_package(units, require_main=False))
    assert set(proof) == {"DataCacheSizes", "getDataCacheSizesIntel", "getDataCacheSizesAMD"}
    hub = proof["DataCacheSizes"]
    assert hub.owner is None and hub.nil_returns == 2
    assert hub.forwarded_returns == ("getDataCacheSizesIntel", "getDataCacheSizesAMD")
    intel, amd = proof["getDataCacheSizesIntel"], proof["getDataCacheSizesAMD"]
    assert (intel.owner, intel.appends, intel.length_reads, intel.owned_returns, intel.nil_returns) == (
        "caches", 1, 1, 1, 1)
    assert (amd.owner, amd.appends, amd.owned_returns, amd.nil_returns) == ("caches", 3, 3, 1)


@pytest.mark.parametrize("extra", [
    "a := s; _ = a",
    "sink(s)",
    "_ = &s",
    "for s := uint32(0); s < 1; s++ {}",
    "var len uint32; _ = len",
    "append(s, x)",
    "s = append(s, s...)",
    "s = nil",
    "goto tail; tail:",
    "defer sink(s)",
])
def test_scalar_slice_owner_proof_rejects_hidden_aliases_and_writes(extra):
    from pcc.frontends.go.packages import parse_package
    from pcc.frontends.go.slice_proof import scalar_slice_owner_group

    program = ("package example; func F(x uintptr) []uintptr { "
               "var s []uintptr; " + extra + "; return s }")
    assert scalar_slice_owner_group(parse_package((("probe.go", program),), require_main=False)) is None


def test_pinned_scalar_slice_owner_proof_rejects_forward_cycles_and_alias_imports():
    from pcc.frontends.go.packages import parse_package
    from pcc.frontends.go.slice_proof import scalar_slice_owner_group

    for source in (
        "package sample; func A() []uintptr { return B() }; func B() []uintptr { return A() }",
        'package sample; import len "unsafe"; func F() []uintptr { return nil }',
    ):
        assert scalar_slice_owner_group(
            parse_package((("owners.go", source),), require_main=False)
        ) is None


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
def test_unchanged_x86_cpu_cache_result_survives_official_go_collection(tmp_path):
    from pathlib import Path
    from tests.go.owned_execution import compile_reference_packages

    root = Path(__file__).with_name("upstream") / "go1.27.3/src/internal/cpu"
    originals = tuple((name, (root / name).read_text()) for name in ("datacache_x86.go", "cpu_x86.s"))
    active = '''package main
import ("cpu_probe"; "os"; "runtime")
func main() {
    first := cpu.DataCacheSizes()
    runtime.GC()
    second := cpu.DataCacheSizes()
    if len(first) != len(second) || (first == nil) != (second == nil) {
        panic("inconsistent cache shape")
    }
    for i := range first {
        if first[i] != second[i] { panic("inconsistent cache size") }
    }
    os.Stdout.Write([]byte("cpu-source-ok"))
}
'''
    packages = {
        "main": (("main.go", active),),
        "cpu_probe": (*originals, ("native.go", "package cpu\n"
                                  "func cpuid(eaxArg, ecxArg uint32) (eax, ebx, ecx, edx uint32)\n")),
    }
    reference = compile_reference_packages(packages, tmp_path / "reference",
                                           compiler=os.environ["PCC_GO_REFERENCE"],
                                           cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    selected = reference.parent / "gopath/src/cpu_probe"
    for name, _ in originals:
        assert (selected / name).read_bytes() == (root / name).read_bytes()
    assert run(reference) == Execution(0, b"cpu-source-ok", b"")
