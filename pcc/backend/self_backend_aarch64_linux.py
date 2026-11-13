"""Linux AArch64 entry sharing the owned AArch64 instruction selector."""

from .self_backend_aarch64_darwin_mem import (
    emitted_memory_instruction_line,
    emitted_branch_line,
    emitted_move_register_line,
    emitted_scalar_register_line,
    emitted_scalar_immediate_line,
    emitted_addsub_immediate_line,
    emitted_compare_immediate_line,
)
from . import BackendUnavailable
from .self_backend_target_match import is_aarch64_linux_triple
from .self_backend_parse import parse_self_backend_target_triple


def emit_aarch64_linux_asm(ir_text: str, optimize: bool = False) -> str:
    from .self_backend_aarch64_darwin import emit_aarch64_darwin_asm
    triple = parse_self_backend_target_triple(ir_text)
    if not is_aarch64_linux_triple(triple):
        raise BackendUnavailable("AArch64 Linux emitter received " + triple)
    return emit_aarch64_darwin_asm(ir_text, optimize=optimize)


def emit_linux_vararg_start(func, ap_ptr, module_symbols):
    from .self_backend_aarch64_darwin_abi import assign_abi_arg_layout
    from .self_backend_aarch64_darwin_materialize import materialize_pointer
    from .self_backend_aarch64_darwin_regs import emit_add_offset
    from .self_backend_aarch64_darwin_regalloc import callee_saved_area_size
    arg_types = [arg.type for arg in func.args]
    _regs, _offsets, gp, fp, stack = assign_abi_arg_layout(arg_types, linux=True)
    # Linux's va_list is {stack, gr_top, vr_top, gr_offs, vr_offs}.
    top = -(func.frame_size + callee_saved_area_size(func))
    lines = materialize_pointer(func, ap_ptr, 9, module_symbols)
    for field, delta in ((0, stack), (8, top - 128), (16, top)):
        lines.extend(emit_add_offset("x10", "x29", delta))
        lines.append(emitted_memory_instruction_line('str', 'x10', 'x9', field, explicit_offset=True))
    lines.extend([emitted_scalar_immediate_line('mov', 'w10', '', (gp - 8) * 8), emitted_memory_instruction_line('str', 'w10', 'x9', 24, explicit_offset=True),
                  emitted_scalar_immediate_line('mov', 'w10', '', (fp - 8) * 16), emitted_memory_instruction_line('str', 'w10', 'x9', 28, explicit_offset=True)])
    return lines


def emit_linux_va_arg(func, dest, ap, value_type, module_symbols):
    from .self_backend_aarch64_darwin_materialize import materialize_pointer
    from .self_backend_aarch64_darwin_slots import load_value_from_address, store_value_regs_to_value_slot
    from .self_backend_aarch64_darwin_symbols import sanitize_label
    if value_type.is_struct or value_type.is_array:
        return emit_linux_aggregate_va_arg(func, dest, ap, value_type, module_symbols)
    if value_type.is_void:
        raise BackendUnavailable("AArch64 va_arg cannot return void")
    if value_type.bits > 64 and not (value_type.is_fp and value_type.width == 128):
        raise BackendUnavailable("AArch64 Linux va_arg does not yet support scalars wider than 64 bits")
    fp = value_type.is_fp
    offset = 28 if fp else 24
    top = 16 if fp else 8
    stride = 16 if fp else 8
    label = "L_va_" + sanitize_label(func.name + "_" + dest)
    lines = materialize_pointer(func, ap, 9, module_symbols)
    lines.extend([emitted_memory_instruction_line('ldr', 'w10', 'x9', offset, explicit_offset=True), emitted_compare_immediate_line('w10', 0),
                  emitted_branch_line('b.ge', label + '_stack'), emitted_memory_instruction_line('ldr', 'x11', 'x9', top, explicit_offset=True),
                  emitted_scalar_register_line('sxtw', 'x10', 'w10'), emitted_scalar_register_line('add', 'x11', 'x11', 'x10'),
                  emitted_addsub_immediate_line('add', 'w10', 'w10', stride), emitted_memory_instruction_line('str', 'w10', 'x9', offset, explicit_offset=True),
                  emitted_branch_line('b', label + '_load'), f"{label}_stack:", emitted_memory_instruction_line('ldr', 'x11', 'x9'),
                                    ])
    if value_type.align >= 16:
        lines.extend([emitted_addsub_immediate_line('add', 'x11', 'x11', 15), emitted_scalar_immediate_line('and', 'x11', 'x11', 18446744073709551600, radix=16)])
    lines.extend([emitted_addsub_immediate_line('add', 'x10', 'x11', max(8, value_type.slot_size)), emitted_memory_instruction_line('str', 'x10', 'x9'), f"{label}_load:"])
    lines.extend(load_value_from_address("x11", value_type, 10))
    lines.extend(store_value_regs_to_value_slot(func, dest, 10))
    return lines


def emit_linux_aggregate_va_arg(func, dest, ap, ty, module_symbols):
    from .self_backend_aarch64_darwin_abi import aggregate_hfa_members, aggregate_passed_indirect
    from .self_backend_aarch64_darwin_materialize import materialize_pointer
    from .self_backend_aarch64_darwin_slots import emit_value_slot_base_address, copy_address_to_value_slot
    from .self_backend_aarch64_darwin_symbols import sanitize_label
    from .self_backend_ir import _align_to
    hfa = aggregate_hfa_members(ty)
    indirect = aggregate_passed_indirect(ty)
    stride = len(hfa) * 16 if hfa else 8 if indirect else _align_to(ty.slot_size, 8)
    field = 28 if hfa else 24
    top = 16 if hfa else 8
    func.platform_intrinsic_labels += 1
    label = "L_vaagg_" + sanitize_label(func.name + "_" + dest) + "_" + str(func.platform_intrinsic_labels)
    lines = materialize_pointer(func, ap, 9, module_symbols)
    lines.extend([emitted_memory_instruction_line('ldr', 'w10', 'x9', field, explicit_offset=True), emitted_compare_immediate_line('w10', 0), emitted_branch_line('b.ge', label + '_stack')])
    if not hfa and not indirect and ty.align >= 16:
        lines.extend([emitted_addsub_immediate_line('add', 'w10', 'w10', 15), emitted_scalar_immediate_line('and', 'w10', 'w10', 4294967280, radix=16)])
    lines.extend([emitted_addsub_immediate_line('add', 'w12', 'w10', stride), emitted_memory_instruction_line('str', 'w12', 'x9', field, explicit_offset=True),
                  emitted_compare_immediate_line('w12', 0), emitted_branch_line('b.gt', label + '_stack'), emitted_memory_instruction_line('ldr', 'x11', 'x9', top, explicit_offset=True),
                  emitted_scalar_register_line('sxtw', 'x10', 'w10'), emitted_scalar_register_line('add', 'x11', 'x11', 'x10')])
    if hfa:
        lines.extend(emit_value_slot_base_address(func, dest, "x13"))
        for index, (member, offset) in enumerate(hfa):
            register = "q10" if member.width == 128 else "s10" if member.width <= 32 else "d10"
            lines.extend([emitted_memory_instruction_line('ldr', register, 'x11', index * 16, explicit_offset=True), emitted_memory_instruction_line('str', register, 'x13', offset, explicit_offset=True)])
    else:
        if indirect:
            lines.append(emitted_memory_instruction_line('ldr', 'x11', 'x11'))
        lines.extend(copy_address_to_value_slot("x11", func, dest))
    lines.extend([emitted_branch_line('b', label + '_end'), f"{label}_stack:", emitted_memory_instruction_line('ldr', 'x11', 'x9')])
    if not indirect and ty.align >= 16:
        lines.extend([emitted_addsub_immediate_line('add', 'x11', 'x11', 15), emitted_scalar_immediate_line('and', 'x11', 'x11', 18446744073709551600, radix=16)])
    lines.extend([emitted_addsub_immediate_line('add', 'x10', 'x11', 8 if indirect else _align_to(ty.slot_size, 8)), emitted_memory_instruction_line('str', 'x10', 'x9')])
    if indirect:
        lines.append(emitted_memory_instruction_line('ldr', 'x11', 'x11'))
    lines.extend(copy_address_to_value_slot("x11", func, dest))
    lines.append(label + "_end:")
    return lines
