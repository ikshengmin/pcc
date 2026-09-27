"""Microsoft x64 ABI boundary for the shared x86 instruction selector.

References: LLVM X86CallingConv.td and MCWin64EH.cpp. The four argument
positions are shared by GPR/XMM classes; every call reserves home space.
"""

from .self_backend_ir import TypeDesc, _align_to

GPRS = ("rcx", "rdx", "r8", "r9")


def indirect(ty) -> bool:
    return (ty.is_struct or ty.is_array) and ty.slot_size not in (1, 2, 4, 8)


def prepare_frame(func) -> None:
    from .self_backend_kernel import get_indexed_function_kernel
    kernel = get_indexed_function_kernel(func)
    outgoing = max(32, func.frame_size)  # also bounds parallel phi scratch
    for call_id in range(len(kernel.call_scalars) // 8):
        header = kernel.call_header(call_id)
        span = kernel.call_span(call_id)
        hidden = 1 if indirect(kernel.type_desc(header.first)) else 0
        size = _align_to(max(32, (span.first + hidden) * 8), 16)
        for index in range(span.first):
            arg = kernel.call_arg(header.fourth + index)
            ty = kernel.type_desc(arg.first)
            if indirect(ty):
                size += _align_to(ty.slot_size, 16)
        outgoing = max(outgoing, size)
    func.platform_frame_extra = _align_to(outgoing, 16) + 80


def outgoing_size(func) -> int:
    return func.platform_frame_extra - 80


def frame_size(func) -> int:
    return func.frame_size + func.platform_frame_extra


def prologue(func):
    from .self_backend_x86_64_linux import (
        _asm_symbol, _store_reg_to_slot, _reg_alias, _copy_address_to_address,
        _slot_addr, _load_address_chunk_to_gp_reg,
    )
    symbol = _asm_symbol(func.name)
    size = frame_size(func)
    lines = ["", ".text", ".p2align 4, 0x90"]
    if func.is_global:
        lines.append(".globl " + symbol)
    lines.extend([f".type {symbol}, @function", f".seh_proc {symbol}", symbol + ":",
                  "  push rbp", "  .seh_pushreg rbp", "  mov rbp, rsp"])
    if size >= 4096:
        lines.extend([f"  mov eax, {size}", "  call __pcc_chkstk", "  sub rsp, rax"])
    else:
        lines.append(f"  sub rsp, {size}")
    lines.append(f"  .seh_stackalloc {size}")
    saved = outgoing_size(func)
    for reg, offset in (("rbx", 0), ("rsi", 8), ("rdi", 16)):
        lines.extend([f"  mov QWORD PTR [rsp + {saved + offset}], {reg}", f"  .seh_savereg {reg}, {saved + offset}"])
    for reg, offset in (("xmm10", 32), ("xmm11", 48)):
        lines.extend([f"  movdqu XMMWORD PTR [rsp + {saved + offset}], {reg}", f"  .seh_savexmm {reg}, {saved + offset}"])
    lines.append("  .seh_endprologue")
    if func.is_vararg:
        for index, reg in enumerate(GPRS):
            lines.append(f"  mov QWORD PTR [rbp + {16 + index * 8}], {reg}")
    start = 1 if func.hidden_sret_slot is not None else 0
    if start:
        slot = func.hidden_sret_slot
        lines.extend(_store_reg_to_slot("rcx", slot.offset, slot.type))
    for index, arg in enumerate(func.args, start):
        if arg.name not in func.value_slots:
            continue
        slot = func.value_slots[arg.name]
        aggregate = arg.type.is_array or arg.type.is_struct
        if indirect(arg.type):
            if index < 4:
                source = GPRS[index]
            else:
                lines.append(f"  mov r10, QWORD PTR [rbp + {16 + index * 8}]")
                source = "r10"
            lines.extend([f"  lea r11, {_slot_addr(slot.offset)}"])
            lines.extend(_copy_address_to_address(source, "r11", arg.type.slot_size))
        elif aggregate:
            if index < 4:
                reg = GPRS[index]
            else:
                lines.append(f"  lea r11, [rbp + {16 + index * 8}]")
                lines.extend(_load_address_chunk_to_gp_reg("r11", 0, arg.type.slot_size, "r10"))
                reg = "r10"
            scalar = TypeDesc("int", arg.type.slot_size * 8)
            lines.extend(_store_reg_to_slot(_reg_alias(reg, arg.type.slot_size), slot.offset, scalar))
        elif index < 4:
            reg = f"xmm{index}" if arg.type.is_fp else _reg_alias(GPRS[index], min(8, arg.type.slot_size))
            lines.extend(_store_reg_to_slot(reg, slot.offset, arg.type))
        else:
            from .self_backend_x86_64_linux import _load_from_address
            lines.append(f"  lea r11, [rbp + {16 + index * 8}]")
            reg = "xmm10" if arg.type.is_fp else _reg_alias("r10", min(8, arg.type.slot_size))
            lines.extend(_load_from_address("r11", reg, arg.type))
            lines.extend(_store_reg_to_slot(reg, slot.offset, arg.type))
    return lines


def epilogue(func):
    lines = []
    saved = outgoing_size(func)
    for reg, offset in (("xmm10", 32), ("xmm11", 48)):
        lines.append(f"  movdqu {reg}, XMMWORD PTR [rsp + {saved + offset}]")
    for reg, offset in (("rbx", 0), ("rsi", 8), ("rdi", 16)):
        lines.append(f"  mov {reg}, QWORD PTR [rsp + {saved + offset}]")
    lines.extend([f"  add rsp, {frame_size(func)}", "  pop rbp", "  ret"])
    return lines


def call(func, dest, ret_type, callee, is_indirect, args, is_vararg):
    from .self_backend_x86_64_linux import (
        _slot_addr, _materialize_value, _materialize_aggregate_value_address,
        _copy_address_to_address, _reg_alias, _store_reg_to_slot, _asm_symbol,
        _load_address_chunk_to_gp_reg,
    )
    from . import BackendUnavailable
    hidden = indirect(ret_type)
    if hidden and (dest is None or dest not in func.value_slots):
        raise BackendUnavailable("Win64 aggregate call needs result storage")
    first = 1 if hidden else 0
    argument_bytes = max(32, (len(args) + first) * 8)
    cursor = _align_to(argument_bytes, 16)
    copies = {}
    for index, (ty, value) in enumerate(args, first):
        if indirect(ty):
            copies[index] = cursor
            cursor += _align_to(ty.slot_size, 16)
    # Stage all arguments in memory before filling the four argument registers.
    # Aggregate copies and constant expressions may borrow rdx/rcx themselves.
    size = _align_to(cursor, 16)
    if size > outgoing_size(func):
        raise BackendUnavailable("Win64 outgoing arguments exceed the planned fixed frame")
    lines = []
    if hidden:
        lines.extend([f"  lea r10, {_slot_addr(func.value_slots[dest].offset)}", "  mov QWORD PTR [rsp], r10"])
    for index, (ty, value) in enumerate(args, first):
        if indirect(ty):
            lines.extend(_materialize_aggregate_value_address(func, value, ty, "r10"))
            lines.append(f"  lea r11, [rsp + {copies[index]}]")
            lines.extend(_copy_address_to_address("r10", "r11", ty.slot_size))
            lines.append(f"  lea r10, [rsp + {copies[index]}]")
        elif ty.is_struct or ty.is_array:
            lines.extend(_materialize_aggregate_value_address(func, value, ty, "r11"))
            lines.extend(_load_address_chunk_to_gp_reg("r11", 0, ty.slot_size, "r10"))
        elif ty.is_fp:
            lines.extend(_materialize_value(func, value, ty, "xmm10"))
            lines.append("  movq r10, xmm10" if ty.width == 64 else "  movd r10d, xmm10")
        else:
            lines.extend(_materialize_value(func, value, ty, _reg_alias("r10", min(8, ty.slot_size))))
        lines.append(f"  mov QWORD PTR [rsp + {index * 8}], r10")
    if hidden:
        lines.append("  mov rcx, QWORD PTR [rsp]")
    for index, (ty, value) in enumerate(args, first):
        if index >= 4:
            break
        if ty.is_fp:
            lines.append(f"  {'movsd' if ty.width == 64 else 'movss'} xmm{index}, {'QWORD' if ty.width == 64 else 'DWORD'} PTR [rsp + {index * 8}]")
        if not ty.is_fp or is_vararg:
            lines.append(f"  mov {GPRS[index]}, QWORD PTR [rsp + {index * 8}]")
    if is_indirect:
        lines.extend(_materialize_value(func, callee, TypeDesc("ptr"), "r11"))
        lines.append("  call r11")
    else:
        lines.append("  call " + _asm_symbol(callee))
    if dest is not None and dest in func.value_slots and not ret_type.is_void and not hidden:
        slot = func.value_slots[dest]
        if ret_type.is_struct or ret_type.is_array:
            lines.extend(_store_reg_to_slot(_reg_alias("rax", ret_type.slot_size), slot.offset,
                                            TypeDesc("int", ret_type.slot_size * 8)))
        else:
            reg = "xmm0" if ret_type.is_fp else _reg_alias("rax", min(8, ret_type.slot_size))
            lines.extend(_store_reg_to_slot(reg, slot.offset, ret_type))
    return lines


def vararg_start(func, ap):
    from .self_backend_x86_64_linux import _materialize_value
    first = len(func.args) + (1 if func.hidden_sret_slot is not None else 0)
    lines = _materialize_value(func, ap, TypeDesc("ptr"), "r10")
    lines.extend([f"  lea r11, [rbp + {16 + first * 8}]",
                  "  mov QWORD PTR [r10], r11"])
    return lines


def tls_address(symbol, reg, scratch_base):
    first = scratch_base + 24
    second = scratch_base + 64
    lines = [f"  mov QWORD PTR [rsp + {first}], r10", f"  mov QWORD PTR [rsp + {second}], r11",
             "  mov r10, QWORD PTR gs:88", "  mov r11d, DWORD PTR _tls_index[rip]",
             "  mov r10, QWORD PTR [r10 + r11*8]", f"  mov r11, QWORD PTR {symbol}@GOTTPOFF[rip]",
             "  add r10, r11"]
    if reg != "r10":
        lines.append(f"  mov {reg}, r10")
    if reg != "r10":
        lines.append(f"  mov r10, QWORD PTR [rsp + {first}]")
    if reg != "r11":
        lines.append(f"  mov r11, QWORD PTR [rsp + {second}]")
    return lines
