"""Named native-record projections retain GEP and call evaluation semantics.

LLVM LangRef getelementptr and call semantics apply on both AArch64 targets.
The inventory test enforces the source binding contract; these tests exercise
its production consumers, including rejected GC short-call admission.
"""
from __future__ import annotations

import re

import pytest

from pcc.backend import self_backend_aarch64_darwin_calls as calls
from pcc.backend import self_backend_aarch64_darwin as backend
from pcc.backend import self_backend_aarch64_gc_calls as gc_calls
from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.self_backend_aarch64_darwin import (
    emit_aarch64_darwin_indexed_module,
    emit_aarch64_darwin_indexed_transport,
)
from pcc.backend.self_backend_aarch64_darwin_abi import aggregate_returned_indirect
from pcc.backend.self_backend_aarch64_darwin_addr import gep_constant_byte_offset_indexed
from pcc.backend.self_backend_kernel import (
    IndexedFunctionKernel,
    TYPE_KIND_FP,
    TYPE_KIND_INT,
    TYPE_KIND_PTR,
    get_indexed_function_kernel,
)
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.self_backend_prepare import prepare_module_for_target


TARGETS = ("arm64-apple-darwin", "aarch64-unknown-linux-gnu")
MARKER = "@__pcc_owned_gc_short_paths_v1 = internal constant i8 1\n"


def _record_queries(monkeypatch, kernel, names):
    events = []
    for name in names:
        original = getattr(IndexedFunctionKernel, name)

        def counted(self, index, _name=name, _original=original):
            if self is kernel:
                events.append((_name, index))
            return _original(self, index)

        monkeypatch.setattr(IndexedFunctionKernel, name, counted)
    return events


@pytest.mark.parametrize("indices,expected", (
    ("i64 2", 64),
    ("i64 -1, i64 3", -8),
    ("i64 0, i64 -1", -8),
    ("i64 2147483648", None),
    ("i64 %index", None),
))
def test_constant_gep_reads_each_required_span_once(monkeypatch, indices, expected):
    module = parse_self_backend_module(
        'target triple = "arm64-apple-darwin"\n'
        "define ptr @address(ptr %base, i64 %index) {\nentry:\n"
        "  %out = getelementptr [4 x i64], ptr %base, " + indices + "\n"
        "  ret ptr %out\n}\n"
    )
    kernel = get_indexed_function_kernel(module.functions[0])
    try:
        header = kernel.gep_header(0)
        base = kernel.type_header(header.first)
        events = _record_queries(monkeypatch, kernel, ("gep_index", "type_header", "type_span"))
        assert gep_constant_byte_offset_indexed(kernel, 0) == expected
        expected_events = [("gep_index", header.fourth)]
        if expected is not None:
            expected_events.append(("type_span", header.first))
            if "," in indices:
                expected_events.extend((
                    ("gep_index", header.fourth + 1),
                    ("type_header", header.first),
                    ("type_span", base.fourth),
                ))
        assert events == expected_events
    finally:
        kernel.close_native_tables()


def call_source(target, shape, marked=True):
    callee = "ordinary" if shape == "ordinary" else "pcc_gc_probe"
    args = [("ptr", "null"), ("i64", "7"), ("double", "0.000000e+00")]
    if shape == "empty":
        args = []
    elif shape == "stack":
        args = [("i64", str(index)) for index in range(9)]
    elif shape == "load":
        callee = "pcc_gc_load_ptr"
        args = [("ptr", "null"), ("ptr", "null")]
    signature = ", ".join(kind for kind, _ in args)
    result_type = "ptr" if shape == "load" else "void"
    assignment = "%loaded = " if shape == "load" else ""
    invocation = result_type + " @" + callee
    parameters = ""
    if shape == "vararg":
        signature = "ptr, ..."
        invocation = "void (ptr, ...) @" + callee
    elif shape == "indirect":
        invocation = "void %callee"
        parameters = "ptr %callee"
    declaration = "declare " + result_type + " @" + callee + "(" + signature + ")\n"
    return (
        'target triple = "' + target + '"\n' + (MARKER if marked else "")
        + declaration + "define void @caller(" + parameters + ") {\nentry:\n"
        + "  " + assignment + "call " + invocation + "("
        + ", ".join(kind + " " + value for kind, value in args)
        + ")\n  ret void\n}\n"
    )


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("shape", ("mixed", "empty", "ordinary", "vararg", "stack", "indirect", "load"))
@pytest.mark.parametrize("marked", (False, True))
def test_gc_argument_kind_queries_are_ordered_and_admission_is_lazy(
    monkeypatch, target, shape, marked,
):
    prepared = prepare_module_for_target(
        call_source(target, shape, marked),
        aggregate_returned_indirect=aggregate_returned_indirect,
    )
    function = prepared.functions[0]
    kernel = get_indexed_function_kernel(function)
    try:
        header = kernel.call_header(0)
        span = kernel.call_span(0)
        arg_ids = list(range(header.fourth, header.fourth + span.first))
        type_ids = [kernel.call_arg(index).first for index in arg_ids]
        events = _record_queries(monkeypatch, kernel, ("call_arg", "type_kind_id"))
        observed = []
        original = gc_calls.gc_short_call_lines

        def observe(func, callee, call_id, ret_kind, arg_kinds, symbols):
            observed.append(arg_kinds)
            assert type(arg_kinds) is tuple
            expected = []
            for arg_id, type_id in zip(arg_ids, type_ids):
                expected.extend((("call_arg", arg_id), ("type_kind_id", type_id)))
            if expected:
                assert events[-len(expected):] == expected
            return original(func, callee, call_id, ret_kind, arg_kinds, symbols)

        monkeypatch.setattr(gc_calls, "gc_short_call_lines", observe)
        lines = calls.emit_call_instruction_indexed(function, kernel, 0, prepared.module_symbols)
        admitted = marked and shape in ("mixed", "empty", "load")
        expected_kinds = (TYPE_KIND_PTR, TYPE_KIND_INT, TYPE_KIND_FP) if shape == "mixed" else ()
        if shape == "load":
            expected_kinds = (TYPE_KIND_PTR, TYPE_KIND_PTR)
        assert observed == ([expected_kinds] if admitted else [])
        # Each register argument is read for ABI placement, and only admitted
        # calls read it again for the GC signature. Stack arguments also need
        # their existing materialization read, independent of GC admission.
        extra_stack = arg_ids[8:] if shape == "stack" else []
        if shape == "vararg" and target == TARGETS[0]:
            extra_stack = arg_ids[1:]
        expected_arg_ids = arg_ids + extra_stack + (arg_ids if admitted else [])
        assert [index for name, index in events if name == "call_arg"] == expected_arg_ids
        assert any("blr x12" in line if shape == "indirect" else "bl " in line for line in lines)
        assert ("  ldr x15, [x1]" in lines) == (admitted and shape == "load" and target == TARGETS[0])
    finally:
        kernel.close_native_tables()


def address_source(target):
    return '''target triple = "''' + target + '''"
define ptr @constant(ptr %base) {
entry:
  %out = getelementptr [4 x i64], ptr %base, i64 2, i64 3
  ret ptr %out
}
define ptr @dynamic(ptr %base, i64 %index) {
entry:
  %out = getelementptr i64, ptr %base, i64 %index
  ret ptr %out
}
'''


def test_dynamic_gep_reads_index_span_and_header_in_order(monkeypatch):
    original = backend._compute_emit_instruction
    observed = []

    def record(function, *args, **kwargs):
        if function.name != "dynamic":
            return original(function, *args, **kwargs)
        kernel = kwargs["indexed_kernel"]
        header = kernel.gep_header(0)
        index = kernel.gep_index(header.fourth)
        with monkeypatch.context() as patch:
            events = _record_queries(patch, kernel, ("gep_index", "type_span", "type_header"))
            result = original(function, *args, **kwargs)
        expected = [("gep_index", header.fourth), ("type_span", header.first), ("type_header", index.first)]
        assert sum(events[start:start + 3] == expected for start in range(len(events))) == 1
        observed.append(events)
        return result

    monkeypatch.setattr(backend, "_compute_emit_instruction", record)
    backend.emit_aarch64_darwin_asm(address_source(TARGETS[0]), optimize=False)
    assert len(observed) == 1


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("optimize", (False, True))
@pytest.mark.parametrize("shape", ("addresses", "gc", "gc-unmarked", "gc-vararg", "gc-stack"))
def test_bound_record_consumers_match_assembly_and_native_sections(target, optimize, shape):
    if shape == "addresses":
        source = address_source(target)
    else:
        source = call_source(target, {
            "gc": "mixed", "gc-unmarked": "mixed", "gc-vararg": "vararg", "gc-stack": "stack",
        }[shape], marked=shape != "gc-unmarked")
    assembly = emit_aarch64_darwin_indexed_module(parse_self_backend_module(source), optimize=optimize)
    transport = emit_aarch64_darwin_indexed_transport(
        parse_self_backend_module(source), optimize=optimize, structured_instructions=True,
    )
    if not optimize:
        assert transport.native_finalized
    # The existing dynamic GEP emits one textual shifted-register ADD;
    # binding its type-span record does not change that transport route.
    assert transport.fallback_instruction_count == (1 if shape == "addresses" else 0)
    assert transport.assemble_sections() == assemble_file(assembly)
    if shape == "addresses":
        assert re.search(r"^  add x\d+, x\d+, #88$", assembly, re.M), assembly
        assert re.search(r"^  add x\d+, x\d+, x\d+, lsl #3$", assembly, re.M), assembly
