"""Linux AArch64 entry sharing the owned AArch64 instruction selector."""

from . import BackendUnavailable
from .self_backend_target_match import is_aarch64_linux_triple
from .self_backend_parse import parse_self_backend_target_triple


def emit_aarch64_linux_asm(ir_text: str) -> str:
    from .self_backend_aarch64_darwin import emit_aarch64_darwin_asm
    triple = parse_self_backend_target_triple(ir_text)
    if not is_aarch64_linux_triple(triple):
        raise BackendUnavailable("AArch64 Linux emitter received " + triple)
    return emit_aarch64_darwin_asm(ir_text, optimize=False)


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
        lines.append(f"  str x10, [x9, #{field}]")
    lines.extend([f"  mov w10, #{(gp - 8) * 8}", "  str w10, [x9, #24]",
                  f"  mov w10, #{(fp - 8) * 16}", "  str w10, [x9, #28]"])
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
    lines.extend([f"  ldr w10, [x9, #{offset}]", "  cmp w10, #0",
                  f"  b.ge {label}_stack", f"  ldr x11, [x9, #{top}]",
                  "  sxtw x10, w10", "  add x11, x11, x10",
                  f"  add w10, w10, #{stride}", f"  str w10, [x9, #{offset}]",
                  f"  b {label}_load", f"{label}_stack:", "  ldr x11, [x9]",
                                    ])
    if value_type.align >= 16:
        lines.extend(["  add x11, x11, #15", "  and x11, x11, #0xfffffffffffffff0"])
    lines.extend([f"  add x10, x11, #{max(8, value_type.slot_size)}", "  str x10, [x9]", f"{label}_load:"])
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
    lines.extend([f"  ldr w10, [x9, #{field}]", "  cmp w10, #0", f"  b.ge {label}_stack"])
    if not hfa and not indirect and ty.align >= 16:
        lines.extend(["  add w10, w10, #15", "  and w10, w10, #0xfffffff0"])
    lines.extend([f"  add w12, w10, #{stride}", f"  str w12, [x9, #{field}]",
                  "  cmp w12, #0", f"  b.gt {label}_stack", f"  ldr x11, [x9, #{top}]",
                  "  sxtw x10, w10", "  add x11, x11, x10"])
    if hfa:
        lines.extend(emit_value_slot_base_address(func, dest, "x13"))
        for index, (member, offset) in enumerate(hfa):
            register = "q10" if member.width == 128 else "s10" if member.width <= 32 else "d10"
            lines.extend([f"  ldr {register}, [x11, #{index * 16}]", f"  str {register}, [x13, #{offset}]"])
    else:
        if indirect:
            lines.append("  ldr x11, [x11]")
        lines.extend(copy_address_to_value_slot("x11", func, dest))
    lines.extend([f"  b {label}_end", f"{label}_stack:", "  ldr x11, [x9]"])
    if not indirect and ty.align >= 16:
        lines.extend(["  add x11, x11, #15", "  and x11, x11, #0xfffffffffffffff0"])
    lines.extend([f"  add x10, x11, #{8 if indirect else _align_to(ty.slot_size, 8)}", "  str x10, [x9]"])
    if indirect:
        lines.append("  ldr x11, [x11]")
    lines.extend(copy_address_to_value_slot("x11", func, dest))
    lines.append(label + "_end:")
    return lines
