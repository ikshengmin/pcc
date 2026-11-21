"""Freestanding-only code appended to the diagnostic copy of py_obj.

The owner frame is stack allocated, never a heap allocation. One thread owns a
record from STW acquisition to its last instant before resume. A phase switch
bills just the elapsed interval to the *previous* phase. Returning restores
that phase, so nested root/sweep calls do not double-count time.
"""
from pcc.diagnostics import _gc_pause_layout as layout

# Emit native constants from the same slot layout that the host validator uses.
# In the generated source all stack offsets, result offsets and sizes are named.
_NATIVE_CONSTANTS = {
    "_PAUSE_WORD_BYTES": layout.WORD_BYTES,
    "_PAUSE_PHASE_COUNT": layout.PHASE_COUNT,
    "_PAUSE_WORLD_RELEASED": layout.PHASES.index("world_released_until_reacquire"),
    "_PAUSE_CONTEXT_PHASE": layout.CURRENT_PHASE,
    "_PAUSE_CONTEXT_LAST_US": layout.LAST_US,
    "_PAUSE_CONTEXT_TOTALS": layout.PHASE_TOTALS,
    "_PAUSE_CONTEXT_SAVED_PHASE": layout.SAVED_WINDOW_PHASE,
    "_PAUSE_CONTEXT_PREVIOUS": layout.PREVIOUS_OWNER,
    "_PAUSE_CONTEXT_INVALID": layout.CONTEXT_INVALID,
    "_PAUSE_CONTEXT_BYTES": layout.CONTEXT_BYTES,
    "_PAUSE_RESULT_WINDOWS": layout.WINDOWS * layout.WORD_BYTES,
    "_PAUSE_RESULT_TOTAL_US": layout.TOTAL_US * layout.WORD_BYTES,
    "_PAUSE_RESULT_MAXIMUM_US": layout.MAXIMUM_US * layout.WORD_BYTES,
    "_PAUSE_RESULT_INVALID": layout.INVALID * layout.WORD_BYTES,
    "_PAUSE_RESULT_AGGREGATE": layout.AGGREGATE * layout.WORD_BYTES,
    "_PAUSE_RESULT_LONGEST": layout.LONGEST * layout.WORD_BYTES,
    "_PAUSE_RESULT_ZERO_US_WINDOWS": layout.ZERO_US_WINDOWS * layout.WORD_BYTES,
    "_PAUSE_RESULT_SLOTS": layout.SLOTS,
}

RUNTIME = "".join(f"{name}: int = {value}\n" for name, value in _NATIVE_CONSTANTS.items()) + '''
define_thread_local_ptr_null("pcc_exp_pause_owner")
define_global_i64_array("pcc_exp_pause_result", RESULT_ZEROES)

@c_abi_export("pcc_exp_pause_begin")
def pcc_exp_pause_begin(ctx, start: int) -> None:
    memset(ctx, 0, _PAUSE_CONTEXT_BYTES)
    store_ptr(ctx, _PAUSE_CONTEXT_PREVIOUS, global_load_ptr("pcc_exp_pause_owner"))
    store_i64(ctx, _PAUSE_CONTEXT_LAST_US, start)
    global_store_ptr("pcc_exp_pause_owner", ctx)

@c_abi_export("pcc_exp_pause_switch")
def pcc_exp_pause_switch(phase: int) -> int:
    ctx = global_load_ptr("pcc_exp_pause_owner")
    if ptr_is_null(ctx) != 0:
        return -1
    current: int = load_i64(ctx, _PAUSE_CONTEXT_PHASE)
    now: int = pcc_runtime_monotonic_us()
    before: int = load_i64(ctx, _PAUSE_CONTEXT_LAST_US)
    if now < before or phase < 0 or phase >= _PAUSE_PHASE_COUNT:
        store_i64(ctx, _PAUSE_CONTEXT_INVALID, 1)
        return current
    offset: int = _PAUSE_CONTEXT_TOTALS + current * _PAUSE_WORD_BYTES
    store_i64(ctx, offset, load_i64(ctx, offset) + now - before)
    store_i64(ctx, _PAUSE_CONTEXT_LAST_US, now)
    store_i64(ctx, _PAUSE_CONTEXT_PHASE, phase)
    return current

@c_abi_export("pcc_exp_pause_window")
def pcc_exp_pause_window(opened: int) -> None:
    ctx = global_load_ptr("pcc_exp_pause_owner")
    if ptr_is_null(ctx) != 0:
        return
    if opened != 0:
        prev: int = pcc_exp_pause_switch(_PAUSE_WORLD_RELEASED)
        store_i64(ctx, _PAUSE_CONTEXT_SAVED_PHASE, prev)
    else:
        pcc_exp_pause_switch(load_i64(ctx, _PAUSE_CONTEXT_SAVED_PHASE))

@c_abi_export("pcc_exp_pause_finish")
def pcc_exp_pause_finish(ctx, start: int, end: int) -> None:
    global_store_ptr("pcc_exp_pause_owner", load_ptr(ctx, _PAUSE_CONTEXT_PREVIOUS))
    if start <= 0 or end < start:
        return
    out = global_addr("pcc_exp_pause_result")
    before: int = load_i64(ctx, _PAUSE_CONTEXT_LAST_US)
    if end < before or load_i64(ctx, _PAUSE_CONTEXT_INVALID) != 0:
        store_i64(out, _PAUSE_RESULT_INVALID, 1)
        return
    current: int = load_i64(ctx, _PAUSE_CONTEXT_PHASE)
    offset: int = _PAUSE_CONTEXT_TOTALS + current * _PAUSE_WORD_BYTES
    store_i64(ctx, offset, load_i64(ctx, offset) + end - before)
    delta: int = end - start
    if delta == 0:
        zeroes: int = load_i64(out, _PAUSE_RESULT_ZERO_US_WINDOWS)
        store_i64(out, _PAUSE_RESULT_ZERO_US_WINDOWS, zeroes + 1)
    windows: int = load_i64(out, _PAUSE_RESULT_WINDOWS)
    newest_max: int = 0
    if windows == 0 or delta > load_i64(out, _PAUSE_RESULT_MAXIMUM_US):
        newest_max = 1
        store_i64(out, _PAUSE_RESULT_MAXIMUM_US, delta)
    store_i64(out, _PAUSE_RESULT_WINDOWS, windows + 1)
    store_i64(out, _PAUSE_RESULT_TOTAL_US, load_i64(out, _PAUSE_RESULT_TOTAL_US) + delta)
    i: int = 0
    while i < _PAUSE_PHASE_COUNT:
        phase_offset: int = i * _PAUSE_WORD_BYTES
        usec: int = load_i64(ctx, _PAUSE_CONTEXT_TOTALS + phase_offset)
        result_offset: int = _PAUSE_RESULT_AGGREGATE + phase_offset
        store_i64(out, result_offset, load_i64(out, result_offset) + usec)
        if newest_max != 0:
            store_i64(out, _PAUSE_RESULT_LONGEST + phase_offset, usec)
        i = i + 1

@c_abi_export("pcc_exp_pause_value")
def pcc_exp_pause_value(i: int) -> int:
    if i < 0 or i >= _PAUSE_RESULT_SLOTS:
        return -1
    return load_i64(global_addr("pcc_exp_pause_result"), i * _PAUSE_WORD_BYTES)
'''.replace("RESULT_ZEROES", ", ".join(["0"] * layout.SLOTS))
