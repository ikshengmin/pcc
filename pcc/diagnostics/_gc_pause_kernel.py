"""Freestanding-only code appended to the diagnostic copy of py_obj.

The owner frame is stack allocated, never a heap allocation. One thread owns a
record from STW acquisition to its last instant before resume. A phase switch
bills just the elapsed interval to the *previous* phase. Returning restores
that phase, so nested root/sweep calls do not double-count time.
"""

RUNTIME = '''
define_thread_local_ptr_null("pcc_exp_pause_owner")
define_global_i64_array("pcc_exp_pause_result", RESULT_ZEROES)

@c_abi_export("pcc_exp_pause_begin")
def pcc_exp_pause_begin(ctx, start: int) -> None:
    memset(ctx, 0, 168)
    store_ptr(ctx, 152, global_load_ptr("pcc_exp_pause_owner"))
    store_i64(ctx, 8, start)
    global_store_ptr("pcc_exp_pause_owner", ctx)

@c_abi_export("pcc_exp_pause_switch")
def pcc_exp_pause_switch(phase: int) -> int:
    ctx = global_load_ptr("pcc_exp_pause_owner")
    if ptr_is_null(ctx) != 0:
        return -1
    current: int = load_i64(ctx, 0)
    now: int = pcc_runtime_monotonic_us()
    before: int = load_i64(ctx, 8)
    if now < before or phase < 0 or phase >= 14:
        store_i64(ctx, 160, 1)
        return current
    offset: int = 16 + current * 8
    store_i64(ctx, offset, load_i64(ctx, offset) + now - before)
    store_i64(ctx, 8, now)
    store_i64(ctx, 0, phase)
    return current

@c_abi_export("pcc_exp_pause_window")
def pcc_exp_pause_window(opened: int) -> None:
    ctx = global_load_ptr("pcc_exp_pause_owner")
    if ptr_is_null(ctx) != 0:
        return
    if opened != 0:
        prev: int = pcc_exp_pause_switch(12)
        store_i64(ctx, 144, prev)
    else:
        pcc_exp_pause_switch(load_i64(ctx, 144))

@c_abi_export("pcc_exp_pause_finish")
def pcc_exp_pause_finish(ctx, start: int, end: int) -> None:
    global_store_ptr("pcc_exp_pause_owner", load_ptr(ctx, 152))
    if start <= 0 or end < start:
        return
    out = global_addr("pcc_exp_pause_result")
    before: int = load_i64(ctx, 8)
    if end < before or load_i64(ctx, 160) != 0:
        store_i64(out, 24, 1)
        return
    current: int = load_i64(ctx, 0)
    offset: int = 16 + current * 8
    store_i64(ctx, offset, load_i64(ctx, offset) + end - before)
    delta: int = end - start
    if delta == 0:
        delta = 1
        store_i64(ctx, 16, load_i64(ctx, 16) + 1)
    newest_max: int = 0
    if delta > load_i64(out, 16):
        newest_max = 1
        store_i64(out, 16, delta)
    store_i64(out, 0, load_i64(out, 0) + 1)
    store_i64(out, 8, load_i64(out, 8) + delta)
    i: int = 0
    while i < 14:
        usec: int = load_i64(ctx, 16 + i * 8)
        store_i64(out, 32 + i * 8, load_i64(out, 32 + i * 8) + usec)
        if newest_max != 0:
            store_i64(out, 144 + i * 8, usec)
        i = i + 1

@c_abi_export("pcc_exp_pause_value")
def pcc_exp_pause_value(i: int) -> int:
    if i < 0 or i >= 32:
        return -1
    return load_i64(global_addr("pcc_exp_pause_result"), i * 8)
'''.replace("RESULT_ZEROES", ", ".join(["0"] * 32))
