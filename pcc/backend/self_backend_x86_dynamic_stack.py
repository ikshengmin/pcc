"""Checked Linux SysV variable-length stack storage, independent of Win64."""

from . import BackendUnavailable
from .self_backend_call_flags import CALL_FLAG_FRAME_PROTOCOL, CALL_FLAG_INDIRECT
from .self_backend_dynamic_alloca import X86_DYNAMIC_ARRAY


def has_checked_dynamic_stack(func, kernel):
    if X86_DYNAMIC_ARRAY not in kernel.call_texts:
        return False
    uses = False
    registered = False
    call_id = 0
    while call_id < len(kernel.call_scalars) // 8:
        header = kernel.call_header(call_id)
        if header.third & CALL_FLAG_FRAME_PROTOCOL:
            registered = True
        if not (header.third & CALL_FLAG_INDIRECT) and kernel.call_texts[header.second] == X86_DYNAMIC_ARRAY:
            uses = True
        call_id += 1
    if uses and (func.type_context is None or not func.type_context.x86_dynamic_layout_supported):
        raise BackendUnavailable("x86_64 dynamic alloca requires the supported SysV data layout")
    if uses and registered:
        # Fixed-location stack maps do not yet describe an array with a
        # dynamic base. In particular, a call result is not proof of heap
        # ownership if it is our stack allocation intrinsic.
        raise BackendUnavailable(
            f"x86_64 dynamic alloca with managed frame registration is not supported in {func.name!r}"
        )
    return uses


def emit_checked_dynamic_array(func, dest, ret_type, args):
    from .self_backend_x86_64_linux import _materialize_value, _store_reg_to_slot, _value_label

    if (not func.x86_dynamic_stack or not ret_type.is_ptr or len(args) != 3
            or not args[2][1].isdigit()):
        raise BackendUnavailable(f"x86_64 dynamic alloca expects ptr (i64 count, i64 stride, i64 align) in {func.name!r}")
    for arg_type, _value in args:
        if not arg_type.is_int or arg_type.width != 64:
            raise BackendUnavailable(f"x86_64 dynamic alloca requires i64 operands in {func.name!r}")
    align = int(args[2][1])
    if align < 16 or align > 4294967296 or align & (align - 1):
        raise BackendUnavailable(f"x86_64 dynamic alloca requires power-of-two stack alignment in {func.name!r}")
    func.platform_intrinsic_labels += 1
    tag = _value_label(func.name, dest or "unused", "stack_" + str(func.platform_intrinsic_labels))
    lines = _materialize_value(func, args[0][1], args[0][0], "rax")
    lines.extend(_materialize_value(func, args[1][1], args[1][0], "r10"))
    # Compute the aligned destination before touching RSP. Multiplication
    # or pointer underflow cannot wrap back into the currently live frame.
    lines.extend(["  mul r10", "  test rdx, rdx", f"  jne {tag}_overflow",
                  "  mov r10, rsp", "  sub r10, rax", f"  jb {tag}_overflow"])
    if align <= 2147483648:
        lines.append(f"  and r10, {-align}")
    else:
        # x86-64 and r64,imm32 sign-extends. Use the full-width mask.
        lines.extend([f"  mov r11, {-align}", "  and r10, r11"])
    # Touch every base page en route, including an over-alignment gap.
    # A zero-size, already aligned allocation must leave the old frame
    # untouched. Calls and phi edge scratch grow below the returned base.
    lines.extend(["  cmp rsp, r10", f"  je {tag}_done", f"{tag}_probe:",
                  "  mov r11, rsp", "  sub r11, r10", "  cmp r11, 4096",
                  f"  jbe {tag}_last", "  sub rsp, 4096", "  mov BYTE PTR [rsp], 0",
                  f"  jmp {tag}_probe", f"{tag}_overflow:", "  ud2",
                  f"{tag}_last:", "  mov rsp, r10", "  mov BYTE PTR [rsp], 0",
                  f"{tag}_done:"])
    if dest is not None and dest in func.value_slots:
        lines.extend(_store_reg_to_slot("r10", func.value_slots[dest].offset, ret_type))
    return lines
