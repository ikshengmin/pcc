"""SysV AMD64 register-save area and va_list lowering."""

from .self_backend_ir import TypeDesc


def start(func, ap):
    from .self_backend_x86_64_linux import _iter_arg_locations, _materialize_pointer_storage_address
    locations, stack, fp = _iter_arg_locations([arg.type for arg in func.args],
                                               gp_index=1 if func.hidden_sret_slot else 0)
    gp = 1 if func.hidden_sret_slot else 0
    for kind, payload in locations:
        if kind == "reg" and not str(payload).startswith("xmm"):
            gp += 1
        elif kind == "aggregate_regs":
            gp += len(payload)
        elif kind == "aggregate_abi_regs":
            gp += sum(1 for register, offset, size, kind in payload if kind == "integer")
    total = func.frame_size + func.platform_frame_extra
    lines = _materialize_pointer_storage_address(func, ap, "r11")
    lines.extend([f"  mov DWORD PTR [r11], {min(gp, 6) * 8}",
                  f"  mov DWORD PTR [r11 + 4], {48 + fp * 16}",
                  f"  lea r10, [rbp + {16 + stack}]", "  mov QWORD PTR [r11 + 8], r10",
                  f"  lea r10, [rbp - {total}]", "  mov QWORD PTR [r11 + 16], r10"])
    return lines


def argument(func, dest, ap, ty):
    from .self_backend_x86_64_linux import (
        _materialize_pointer_storage_address, _load_from_address, _store_reg_to_slot,
        _reg_name, _value_label,
    )
    from . import BackendUnavailable
    if ty.is_struct or ty.is_array:
        return aggregate_argument(func, dest, ap, ty)
    if not (ty.is_int or ty.is_ptr or ty.is_fp) or ty.bits > 64:
        raise BackendUnavailable("SysV va_arg does not yet support scalars wider than 64 bits")
    func.platform_intrinsic_labels += 1
    label = _value_label(func.name, dest, "va_" + str(func.platform_intrinsic_labels))
    offset = 4 if ty.is_fp else 0
    stride = 16 if ty.is_fp else 8
    limit = 176 if ty.is_fp else 48
    lines = _materialize_pointer_storage_address(func, ap, "r11")
    lines.extend([f"  mov eax, DWORD PTR [r11 + {offset}]", f"  cmp eax, {limit}",
                  f"  jae {label}_stack", "  mov r10, QWORD PTR [r11 + 16]", "  add r10, rax",
                  f"  add eax, {stride}", f"  mov DWORD PTR [r11 + {offset}], eax", f"  jmp {label}_load",
                  f"{label}_stack:", "  mov r10, QWORD PTR [r11 + 8]",
                  "  lea rax, [r10 + 8]", "  mov QWORD PTR [r11 + 8], rax", f"{label}_load:"])
    if dest in func.value_slots:
        register = _reg_name(ty, 0)
        lines.extend(_load_from_address("r10", register, ty))
        lines.extend(_store_reg_to_slot(register, func.value_slots[dest].offset, ty))
    return lines


def aggregate_argument(func, dest, ap, ty):
    from .self_backend_sysv_aggregates import classes
    from .self_backend_ir import _align_to
    from .self_backend_x86_64_linux import (
        _materialize_pointer_storage_address, _slot_addr, _value_label, _copy_address_to_address,
    )
    if dest not in func.value_slots:
        return []
    layout = classes(ty)
    gp = sum(1 for kind in layout if kind == "integer")
    fp = len(layout) - gp
    func.platform_intrinsic_labels += 1
    label = _value_label(func.name, dest, "vaagg_" + str(func.platform_intrinsic_labels))
    slot = func.value_slots[dest].offset
    lines = _materialize_pointer_storage_address(func, ap, "r11")
    if layout:
        lines.extend(["  mov edx, DWORD PTR [r11]", "  mov ecx, DWORD PTR [r11 + 4]"])
        if gp:
            lines.extend([f"  cmp edx, {48 - gp * 8}", f"  ja {label}_stack"])
        if fp:
            lines.extend([f"  cmp ecx, {176 - fp * 16}", f"  ja {label}_stack"])
        lines.append("  mov r8, QWORD PTR [r11 + 16]")
        for index, kind in enumerate(layout):
            lines.append("  lea r10, [r8 + rdx]" if kind == "integer" else "  lea r10, [r8 + rcx]")
            lines.append(f"  lea r9, {_slot_addr(slot - index * 8)}")
            lines.extend(_copy_address_to_address("r10", "r9", min(8, ty.slot_size - index * 8)))
            lines.append("  add edx, 8" if kind == "integer" else "  add ecx, 16")
        lines.extend(["  mov DWORD PTR [r11], edx", "  mov DWORD PTR [r11 + 4], ecx", f"  jmp {label}_end"])
    lines.extend([f"{label}_stack:", "  mov r10, QWORD PTR [r11 + 8]"])
    if ty.align > 8:
        lines.extend(["  add r10, 15", "  and r10, -16"])
    lines.append(f"  lea r9, {_slot_addr(slot)}")
    lines.extend(_copy_address_to_address("r10", "r9", ty.slot_size))
    lines.extend([f"  add r10, {_align_to(ty.slot_size, 8)}", "  mov QWORD PTR [r11 + 8], r10", f"{label}_end:"])
    return lines
