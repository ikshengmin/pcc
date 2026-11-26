"""Owned host scalar verification is only a successful-proof shortcut."""
from dataclasses import replace

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import self_backend_verify as verify
from pcc.backend.self_backend_ir import TypeDesc
from pcc.backend.self_backend_kernel import IndexedFunctionKernel, get_indexed_function_kernel
from pcc.backend.self_backend_parse import parse_self_backend_module


SOURCE = '''target triple = "x86_64-unknown-linux-gnu"
define i64 @probe(i64 %x, i1 %cond) {
entry:
  %a = add i64 %x, 1
  br i1 %cond, label %left, label %right
left:
  %b = add i64 %a, 2
  ret i64 %b
right:
  ret i64 %a
}
'''


def _kernel():
    func = parse_self_backend_module(SOURCE).functions[0]
    kernel = get_indexed_function_kernel(func)
    verify._build_definitions(func, kernel)
    return func, kernel


def test_exact_host_planes_prove_only_successful_dominance():
    func, kernel = _kernel()
    planes = verify._host_definition_planes(kernel, kernel)
    assert planes is not None
    _, predecessors, successors = verify._build_cfg(func, kernel)
    dominators = verify._compute_dominators(predecessors, successors)
    x = kernel.value_id("x")
    a = kernel.value_id("a")
    b = kernel.value_id("b")
    assert all(value >= 0 for value in (x, a, b))
    assert verify._host_definition_dominates(planes, x, 0, 0, dominators)
    assert verify._host_definition_dominates(planes, a, 1, 0, dominators)
    assert not verify._host_definition_dominates(planes, a, 0, 0, dominators)
    assert not verify._host_definition_dominates(planes, b, 2, 0, dominators)
    assert not verify._host_definition_dominates(planes, -1, 0, 0, dominators)
    assert not verify._host_definition_dominates(planes, len(kernel.value_names), 0, 0, dominators)


def test_host_plane_guard_rejects_truncated_or_custom_storage(monkeypatch):
    _func, kernel = _kernel()
    assert verify._host_definition_planes(kernel, kernel) is not None
    assert verify._host_definition_planes(kernel, object()) is None
    original = kernel.value_scalars._values
    kernel.value_scalars._values = original[:-1]
    assert verify._host_definition_planes(kernel, kernel) is None

    class Observed(list):
        pass

    kernel.value_scalars._values = Observed(original)
    assert verify._host_definition_planes(kernel, kernel) is None
    kernel.value_scalars._values = original
    method = IndexedFunctionKernel.value_header
    monkeypatch.setattr(IndexedFunctionKernel, "value_header", lambda self, i: method(self, i))
    assert verify._host_definition_planes(kernel, kernel) is None


@pytest.mark.parametrize("source,expected", [
    (
        SOURCE.replace("ret i64 %a\n}", "ret i64 %b\n}"),
        "self IR verifier [ssa-dominance] in 'probe': definition of 'b' does not dominate terminator in 'right'",
    ),
    (
        SOURCE.replace("%a = add i64 %x, 1", "%a = add i64 %missing, 1"),
        "self IR verifier [ssa-dominance] in 'probe': 'entry'/binop uses undefined value 'missing'",
    ),
    (
        SOURCE.replace("i64 %x, i1 %cond", "i32 %x, i1 %cond"),
        "self IR verifier [operand-type] in 'probe': 'entry'/binop expects i64 for 'x', got i32",
    ),
])
def test_success_projection_keeps_canonical_failure_message(source, expected):
    with pytest.raises(BackendUnavailable) as error:
        verify.verify_parsed_module(parse_self_backend_module(source))
    assert str(error.value) == expected


def test_host_storage_type_shortcut_keeps_extended_storage_rejection():
    _func, kernel = _kernel()
    i64 = kernel.intern_type(TypeDesc("int", 64))
    fp80 = kernel.intern_type(TypeDesc("fp", 80))
    pointer = kernel.intern_type(TypeDesc("ptr", pointee=TypeDesc("fp", 80)))
    aggregate = kernel.intern_type(TypeDesc("array", count=2, elem=TypeDesc("fp", 80)))
    assert not verify._storage_only_float_type_id(kernel, i64)
    assert verify._storage_only_float_type_id(kernel, fp80)
    assert not verify._storage_only_float_type_id(kernel, pointer)
    assert verify._storage_only_float_type_id(kernel, aggregate)


def test_host_type_proof_only_elides_canonical_pointer_match():
    source = '''target triple = "x86_64-unknown-linux-gnu"
define ptr @probe(ptr %p) {
entry:
  ret ptr %p
}
'''
    func = parse_self_backend_module(source).functions[0]
    kernel = get_indexed_function_kernel(func)
    verify._build_definitions(func, kernel)
    value_id = kernel.value_id("p")
    typed_pointer = kernel.intern_type(TypeDesc("ptr", pointee=TypeDesc("int", 64)))
    integer = kernel.intern_type(TypeDesc("int", 64))
    assert verify._host_definition_type_matches(kernel, kernel, value_id, typed_pointer)
    assert not verify._host_definition_type_matches(kernel, kernel, value_id, integer)
    assert not verify._host_definition_type_matches(kernel, kernel, -1, typed_pointer)
