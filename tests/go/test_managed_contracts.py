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
