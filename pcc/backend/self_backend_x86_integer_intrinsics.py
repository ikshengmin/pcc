"""Integer intrinsic expansion using the baseline x86-64 instruction set."""

from . import BackendUnavailable


def emit(func, dest, ret_type, callee, args):
    prefixes = ("llvm.abs.", "llvm.bswap.", "llvm.fshl.", "llvm.fshr.",
                "llvm.uadd.sat.", "llvm.usub.sat.", "llvm.ucmp.", "llvm.scmp.",
                "llvm.vector.reduce.add.", "llvm.vector.reduce.mul.",
                "llvm.vector.reduce.or.", "llvm.vector.reduce.umax.")
    if not callee.startswith(prefixes):
        return None
    expected = 3 if callee.startswith(("llvm.fshl.", "llvm.fshr.")) else 1 if callee.startswith(("llvm.bswap.", "llvm.vector.reduce.")) else 2
    if len(args) != expected:
        raise BackendUnavailable("integer intrinsic operand count mismatch: " + callee)
    if dest is None or dest not in func.value_slots:
        return []
    from .self_backend_x86_64_linux import (
        _materialize_value, _materialize_vector_lane_to_reg, _reg_name,
        _reg_alias, _store_reg_to_slot, _value_label,
    )
    if callee.startswith("llvm.vector.reduce."):
        ty, value = args[0]
        if not ty.is_array or ty.elem is None or not ty.elem.is_int or ty.count <= 0:
            raise BackendUnavailable("integer reduction requires a nonempty integer vector")
        element = ty.elem
        if element.width not in (1, 8, 16, 32, 64):
            raise BackendUnavailable("unsupported integer reduction width")
        accumulator = "rax" if element.width == 64 else "eax"
        scratch = "r10" if element.width == 64 else "r10d"
        lines = _materialize_vector_lane_to_reg(func, value, ty, 0, _reg_name(element, 0))
        if element.width < 32:
            lines.append(f"  movzx eax, {_reg_name(element, 0)}")
        for index in range(1, ty.count):
            lines.extend(_materialize_vector_lane_to_reg(func, value, ty, index, _reg_name(element, 10)))
            if element.width < 32:
                lines.append(f"  movzx r10d, {_reg_name(element, 10)}")
            if ".umax." in callee:
                lines.extend([f"  cmp {accumulator}, {scratch}", f"  cmovb {accumulator}, {scratch}"])
            else:
                operation = "add" if ".add." in callee else "imul" if ".mul." in callee else "or"
                lines.append(f"  {operation} {accumulator}, {scratch}")
        if element.width == 1:
            lines.append("  and eax, 1")
        lines.extend(_store_reg_to_slot(_reg_name(ret_type, 0), func.value_slots[dest].offset, ret_type))
        return lines
    ty, value = args[0]
    if not ty.is_int or ty.width not in (8, 16, 32, 64):
        raise BackendUnavailable("unsupported integer intrinsic width")
    accumulator = _reg_name(ty, 0)
    scratch = _reg_name(ty, 10)
    lines = _materialize_value(func, value, ty, accumulator)
    func.platform_intrinsic_labels += 1
    label = _value_label(func.name, dest, "intrinsic_" + str(func.platform_intrinsic_labels))
    if callee.startswith("llvm.abs."):
        if ty.width < 32:
            lines.append(f"  movsx eax, {accumulator}")
        register = "rax" if ty.width == 64 else "eax"
        temporary = "r10" if ty.width == 64 else "r10d"
        lines.extend([f"  mov {temporary}, {register}", f"  neg {register}",
                      f"  test {temporary}, {temporary}", f"  cmovge {register}, {temporary}"])
    elif callee.startswith("llvm.bswap."):
        if ty.width not in (16, 32, 64):
            raise BackendUnavailable("bswap requires 16/32/64 bits")
        lines.extend([f"  mov {scratch}, {accumulator}", "  xor eax, eax"])
        for index in range(ty.width // 8):
            lines.extend(["  shl rax, 8", "  mov r11, r10", "  and r11d, 255",
                          "  or rax, r11", "  shr r10, 8"])
    elif callee.startswith(("llvm.fshl.", "llvm.fshr.")):
        if len(args) != 3:
            raise BackendUnavailable("funnel shift requires three operands")
        lines.extend(_materialize_value(func, args[1][1], args[1][0], scratch))
        count_type, count_value = args[2]
        lines.extend(_materialize_value(func, count_value, count_type, _reg_alias("rcx", min(8, count_type.slot_size))))
        lines.extend([f"  and ecx, {ty.width - 1}", f"  je {label}_zero", "  mov r11d, ecx"])
        if callee.startswith("llvm.fshl."):
            lines.extend([f"  shl {accumulator}, cl", f"  mov ecx, {ty.width}",
                          "  sub ecx, r11d", f"  shr {scratch}, cl"])
        else:
            lines.extend([f"  shr {scratch}, cl", f"  mov ecx, {ty.width}",
                          "  sub ecx, r11d", f"  shl {accumulator}, cl"])
        lines.extend([f"  or {accumulator}, {scratch}", f"  jmp {label}_end", f"{label}_zero:"])
        if callee.startswith("llvm.fshr."):
            lines.append(f"  mov {accumulator}, {scratch}")
        lines.append(label + "_end:")
    elif callee.startswith(("llvm.uadd.sat.", "llvm.usub.sat.")):
        lines.extend(_materialize_value(func, args[1][1], args[1][0], scratch))
        subtract = callee.startswith("llvm.usub.")
        lines.extend([f"  {'sub' if subtract else 'add'} {accumulator}, {scratch}",
                      f"  jae {label}_end", f"  mov {accumulator}, {0 if subtract else -1}", f"{label}_end:"])
    else:
        lines.extend(_materialize_value(func, args[1][1], args[1][0], scratch))
        signed = callee.startswith("llvm.scmp.")
        lines.extend([f"  cmp {accumulator}, {scratch}", f"  set{'g' if signed else 'a'} al",
                      f"  set{'l' if signed else 'b'} r11b", "  movzx eax, al", "  movzx r11d, r11b",
                      "  sub eax, r11d"])
        if ret_type.width == 64:
            lines.append("  movsxd rax, eax")
    lines.extend(_store_reg_to_slot(_reg_name(ret_type, 0), func.value_slots[dest].offset, ret_type))
    return lines
