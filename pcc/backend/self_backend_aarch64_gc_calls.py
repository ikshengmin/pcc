"""Lower owned GC ABI short paths after precise-root planning.

The IR calls remain intact for stack-map construction. Only explicitly marked
PCC runtime clients qualify; ordinary C symbols and local definitions retain
their calls. Slow paths use the original ABI and keep a real return-PC anchor.
"""
from __future__ import annotations

from .self_backend_aarch64_darwin_mem import (
    emitted_direct_call_line,
    emitted_global_address_lines,
)
from .self_backend_aarch64_darwin_symbols import asm_symbol_prevalidated
from .self_backend_kernel import TYPE_KIND_PTR, TYPE_KIND_VOID
from .self_backend_target_match import is_aarch64_darwin_triple


GC_ABI_MARKER = "__pcc_owned_gc_short_paths_v1"
GC_ABI_EXCLUSION_PREFIX = GC_ABI_MARKER + ".exclude."
_LOADS = ("pcc_gc_load_ptr", "pcc_gc_load_borrowed_ptr")
_FRAME_ENTERS = (
    "pcc_gc_frame_enter", "pcc_gc_frame_enter_lifo",
    "pcc_gc_frame_enter_mapped", "pcc_gc_frame_enter_lifo_mapped",
)
_FRAME_LEAVES = (
    "pcc_gc_frame_leave", "pcc_gc_frame_leave_lifo",
    "pcc_gc_frame_leave_mapped", "pcc_gc_frame_leave_lifo_mapped",
)
_BARRIERS = ("pcc_gc_note_slot_write_barrier", "pcc_gc_note_write_barrier")
GC_SHORT_CALL_HELPERS = _LOADS + _FRAME_ENTERS + _FRAME_LEAVES + _BARRIERS


def _flag(lines, name, module_symbols, *, acquire=False):
    symbol = asm_symbol_prevalidated(name, module_symbols)
    lines.extend(emitted_global_address_lines("x16", symbol, True))
    lines.append("  ldar w17, [x16]" if acquire else "  ldr w17, [x16]")


def gc_short_call_lines(func, callee, call_id, ret_kind, arg_kinds, module_symbols):
    """Return a guarded ABI expansion, or None when its proof is absent."""
    # Shards retain original-module definitions as compile-time exclusions.
    # A definition in a sibling object is still not an owned runtime import.
    excluded = GC_ABI_EXCLUSION_PREFIX + callee
    if (GC_ABI_MARKER not in module_symbols.internal_symbols
            or GC_ABI_MARKER not in module_symbols.defined_symbols
            or callee in module_symbols.defined_symbols
            or (excluded in module_symbols.internal_symbols
                and excluded in module_symbols.defined_symbols)
            or not is_aarch64_darwin_triple(module_symbols.target_triple)):
        return None
    load = callee in _LOADS
    frame = callee in _FRAME_ENTERS or callee in _FRAME_LEAVES
    barrier = callee in _BARRIERS
    if not load and not frame and not barrier:
        return None
    count = 2
    if callee in _FRAME_LEAVES:
        count = 1
    elif callee == "pcc_gc_note_slot_write_barrier":
        count = 3
    if (ret_kind != (TYPE_KIND_PTR if load else TYPE_KIND_VOID)
            or len(arg_kinds) != count
            or any(kind != TYPE_KIND_PTR for kind in arg_kinds)):
        return None
    prefix = "L" + asm_symbol_prevalidated(func.name, module_symbols)
    prefix += ".gc.short." + str(call_id)
    slow, done, empty = prefix + ".slow", prefix + ".done", prefix + ".empty"
    lines = []
    if load or frame:
        # These managed runtime entries poll before their bodies. A pending
        # stop must enter the original function, even on its otherwise empty path.
        _flag(lines, "pcc_thread_stop_requested", module_symbols, acquire=True)
        lines.append("  cbnz w17, " + slow)
    if load:
        lines.append("  cbz x1, " + empty)
        # Preserve the runtime's slot-before-barrier-state read order. x15 is
        # ABI scratch and no call occurs while this raw value is needed.
        lines.append("  ldr x15, [x1]")
        _flag(lines, "pcc_gc_read_barrier_enabled", module_symbols)
        lines.append("  cbnz w17, " + slow)
        lines.extend(["  mov x0, x15", "  b " + done, empty + ":", "  mov x0, #0", "  b " + done])
    else:
        if barrier:
            value = "x2" if count == 3 else "x1"
            lines.extend(["  cbz " + value + ", " + done,
                          "  and x17, " + value + ", #1",
                          "  cbnz x17, " + done])
        _flag(lines, "pcc_gc_config_initialized", module_symbols)
        lines.append("  cbz w17, " + slow)
        _flag(lines, "pcc_gc_backend_selected", module_symbols)
        lines.append("  cbnz w17, " + slow)
        if frame:
            _flag(lines, "pcc_gc_backend0_frame_roots_enabled", module_symbols)
            lines.append("  cbnz w17, " + slow)
        lines.append("  b " + done)
    lines.append(slow + ":")
    lines.append(emitted_direct_call_line(asm_symbol_prevalidated(callee, module_symbols)))
    lines.append(done + ":")
    return lines
