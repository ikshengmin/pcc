"""Target ABI regressions; execution coverage lives in the native port gate."""

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend.self_backend_aarch64_darwin_abi import (
    assign_abi_arg_layout,
    assign_abi_arg_regs,
    aggregate_returned_indirect,
    stack_arg_alignment,
    stack_arg_alignment_indexed,
    stack_arg_storage_size,
    stack_arg_storage_size_indexed,
)
from pcc.backend.self_backend_ir import TypeDesc
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.self_backend_prepare import prepare_parsed_function
from pcc.backend.self_backend_stackprep import assign_stack_slots
from pcc.backend.self_backend_module_symbols import prepare_module_symbols
from pcc.frontends.c.codegen.c_codegen import CCodeGenerator, postprocess_ir_text
from pcc.frontends.c.parse.c_parser import CParser
from pcc.ir import ir


@pytest.mark.parametrize("target", [
    "x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu", "x86_64-pc-windows-msvc",
])
def test_c_variadic_call_preserves_mixed_aggregate_abi_type(target):
    generator = CCodeGenerator()
    generator.module.triple = target
    generator.generate_code(CParser().parse("""
        struct pair { double real; long long integer; };
        extern long long take(int marker, ...);
        long long caller(void) {
            struct pair value = {1.5, 29};
            return take(0, value);
        }
    """))
    module = parse_self_backend_module(postprocess_ir_text(str(generator.module)))
    from pcc.backend.self_backend_kernel import get_indexed_function_kernel

    calls = []
    for function in module.functions:
        kernel = get_indexed_function_kernel(function)
        for call_id in range(len(kernel.call_scalars) // 8):
            if kernel.call_texts[kernel.call_header(call_id).second] == "take":
                calls.append(kernel.diagnostic_call_data(call_id))
    assert len(calls) == 1
    argument_type = calls[0][4][1][0]
    assert argument_type.is_struct
    assert [member.kind for member in argument_type.fields] == ["fp", "int"]
    from pcc.backend.self_backend_sysv_aggregates import classes
    assert classes(argument_type) == ("sse", "integer")


def test_linux_aarch64_named_aggregate_spill_exhausts_register_bank():
    integer = TypeDesc("int", width=64)
    double = TypeDesc("fp", width=64)
    pair = TypeDesc("struct", fields=(integer, integer))
    hfa = TypeDesc("struct", fields=(double, double))
    regs, offsets, gp, fp, stack = assign_abi_arg_layout([integer] * 7 + [pair], linux=True)
    assert regs[-1] == ()
    assert offsets[-1] == 16
    assert (gp, fp, stack) == (8, 0, 32)
    regs, offsets, gp, fp, stack = assign_abi_arg_layout([double] * 7 + [hfa], linux=True)
    assert regs[-1] == ()
    assert offsets[-1] == 16
    assert (gp, fp, stack) == (0, 8, 32)
    # Linux leaves x7 unused after the spill. Preserve Darwin's existing rule.
    assert assign_abi_arg_regs([integer] * 7 + [pair, integer], linux=True)[-1] == ()
    assert assign_abi_arg_regs([integer] * 7 + [pair, integer])[-1] == ("x7",)


def _aarch64_start(named_types):
    from pcc.backend.self_backend_aarch64_linux import emit_linux_vararg_start
    named = ", ".join(ty + " %a" + str(index) for index, ty in enumerate(named_types))
    text = ('target triple = "aarch64-unknown-linux-gnu"\n'
            + 'declare void @llvm.va_start(ptr)\n'
            + 'define void @probe(' + named + ', ...) {\nentry:\n'
            + '  %ap = alloca {ptr, ptr, ptr, i32, i32}\n'
            + '  call void @llvm.va_start(ptr %ap)\n  ret void\n}\n')
    module = parse_self_backend_module(text)
    function = module.functions[0]
    prepare_parsed_function(function)
    assign_stack_slots(function, aggregate_returned_indirect=aggregate_returned_indirect)
    symbols = prepare_module_symbols(text, list(module.globals_), list(module.functions))
    return emit_linux_vararg_start(function, "ap", symbols)


@pytest.mark.parametrize("named_types,gp_offset,fp_offset,stack", [
    (["i64"] * 7 + ["{i64, i64}"], 0, -128, 32),
    (["double"] * 7 + ["{double, double}"], -64, 0, 32),
    (["i64"] * 8 + ["{i64, i64, i64}"], 0, -128, 24),
    (["i64"] * 9 + ["{i64, i64, i64}"], 0, -128, 32),
])
def test_linux_aarch64_va_start_uses_allocation_cursors(named_types, gp_offset, fp_offset, stack):
    lines = _aarch64_start(named_types)
    assert "  add x10, x29, #" + str(stack) in lines
    assert lines[-4:] == [
        "  mov w10, #" + str(gp_offset), "  str w10, [x9, #24]",
        "  mov w10, #" + str(fp_offset), "  str w10, [x9, #28]",
    ]


def test_linux_va_arg_rejects_unimplemented_wide_scalar_without_truncating():
    from pcc.backend.self_backend_aarch64_linux import emit_linux_va_arg
    from pcc.backend.self_backend_sysv_varargs import argument
    wide = TypeDesc("int", width=128)
    with pytest.raises(BackendUnavailable, match="wider than 64 bits"):
        emit_linux_va_arg(None, "value", "ap", wide, None)
    with pytest.raises(BackendUnavailable, match="wider than 64 bits"):
        argument(None, "value", "ap", wide)


def test_sysv_va_list_parameter_uses_record_pointer_not_its_local_slot(monkeypatch):
    generator = CCodeGenerator()
    generator.module.triple = "x86_64-unknown-linux-gnu"
    record = ir.LiteralStructType([ir.IntType(32), ir.IntType(32),
                                  ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()])
    pointer = ir.Constant(record.as_pointer(), None)
    parameter_slot = ir.Constant(record.as_pointer().as_pointer(), None)
    monkeypatch.setattr(generator, "codegen", lambda expr: (pointer, parameter_slot))
    assert generator._builtin_va_list_storage(None) is pointer
    # Windows/Darwin va_list is a cursor: its local pointer variable is mutated.
    generator.module.triple = "x86_64-pc-windows-msvc"
    cursor = ir.Constant(ir.IntType(8).as_pointer(), None)
    cursor_slot = ir.Constant(ir.IntType(8).as_pointer().as_pointer(), None)
    monkeypatch.setattr(generator, "codegen", lambda expr: (cursor, cursor_slot))
    assert generator._builtin_va_list_storage(None) is cursor_slot


@pytest.mark.parametrize("linux", (False, True), ids=("darwin", "linux"))
def test_aarch64_fixed_stack_scalars_preserve_target_size_and_alignment(linux):
    # Apple ARM64: fixed arguments occupy natural slots; AAPCS64: minimum 8.
    # https://developer.apple.com/documentation/xcode/writing-arm64-code-for-apple-platforms
    integer, floating = TypeDesc("int", 64), TypeDesc("fp", 32)
    tail = [TypeDesc("int", 8), TypeDesc("int", 16), floating,
            TypeDesc("fp", 64), TypeDesc("int", 32), TypeDesc("ptr")]
    types = [integer] * 8 + [floating] * 8 + tail
    regs, offsets, gp, fp, stack = assign_abi_arg_layout(types, linux=linux)
    expected = [16, 24, 32, 40, 48, 56] if linux else [16, 18, 20, 24, 32, 40]
    assert all(regs[:16]) and regs[16:] == [()] * 6
    assert offsets == [None] * 16 + expected
    assert (gp, fp, stack) == (8, 8, 64 if linux else 48)


@pytest.mark.parametrize("linux", (False, True), ids=("darwin", "linux"))
def test_aarch64_fixed_hfa_stack_tail_uses_target_layout(linux):
    floating = TypeDesc("fp", 32)
    hfa = TypeDesc("struct", fields=(floating, floating, floating))
    _, offsets, _, fp, stack = assign_abi_arg_layout([floating] * 9 + [hfa, floating], linux=linux)
    assert offsets[8:] == ([16, 24, 40] if linux else [16, 20, 32])
    assert fp == 8 and stack == (48 if linux else 36)


@pytest.mark.parametrize("linux", (False, True), ids=("darwin", "linux"))
def test_aarch64_indexed_fixed_stack_helpers_match_type_descriptors(linux):
    from pcc.backend.self_backend_kernel import get_indexed_function_kernel

    types = ["i8", "i16", "i32", "float", "double", "ptr", "{i8, i8, i8}",
             "{float, float, float}", "{i64, i64, i64}", "[1 x i128]"]
    named = ", ".join(ty + " %a" + str(index) for index, ty in enumerate(types))
    module = parse_self_backend_module("define void @probe(" + named + ") {\nentry:\n  ret void\n}\n")
    function = module.functions[0]
    prepare_parsed_function(function)
    kernel = get_indexed_function_kernel(function)
    try:
        expected_sizes = [8, 8, 8, 8, 8, 8, 8, 16, 8, 16] if linux else [1, 2, 4, 4, 8, 8, 8, 12, 8, 16]
        expected_alignments = [8, 8, 8, 8, 8, 8, 8, 8, 8, 16] if linux else [1, 2, 4, 4, 8, 8, 8, 4, 8, 16]
        for argument, size, alignment in zip(function.args, expected_sizes, expected_alignments):
            type_id = kernel.value_type_id(kernel.value_id(argument.name))
            assert stack_arg_storage_size(argument.type, linux=linux) == size
            assert stack_arg_storage_size_indexed(kernel, type_id, linux=linux) == size
            assert stack_arg_alignment(argument.type, linux=linux) == alignment
            assert stack_arg_alignment_indexed(kernel, type_id, linux=linux) == alignment
    finally:
        kernel.close_native_tables()


@pytest.mark.parametrize("named_types,stack", [
    (["float"] * 9, 24), (["float"] * 10, 24), (["float"] * 11, 32),
    (["i8"] * 10, 24), (["i64"] * 8 + ["{i64, i64}"], 32),
    (["float"] * 8 + ["{float, float, float}"], 32),
])
def test_darwin_va_start_follows_complete_fixed_stack_tail(named_types, stack):
    from pcc.backend.self_backend_aarch64_darwin_calls import emit_vararg_start
    from pcc.backend.self_backend_kernel import get_indexed_function_kernel

    named = ", ".join(ty + " %a" + str(index) for index, ty in enumerate(named_types))
    text = ('target triple = "arm64-apple-darwin"\n'
            + 'define void @probe(' + named + ', ...) {\nentry:\n'
            + '  %ap = alloca ptr\n  ret void\n}\n')
    module = parse_self_backend_module(text)
    function = module.functions[0]
    prepare_parsed_function(function)
    assign_stack_slots(function, aggregate_returned_indirect=aggregate_returned_indirect)
    symbols = prepare_module_symbols(text, list(module.globals_), list(module.functions))
    try:
        assert "  add x10, x29, #" + str(stack) in emit_vararg_start(function, "ap", symbols)
    finally:
        get_indexed_function_kernel(function).close_native_tables()
