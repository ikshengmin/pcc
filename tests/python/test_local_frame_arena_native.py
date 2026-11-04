"""Execute the local frame arena through native exported calls and STW visits."""
import os
import subprocess


MODES = ((0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (1, 1), (2, 1), (4, 1))


PROGRAM = r'''from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import function_addr, global_addr, int_to_ptr, load_i64, load_ptr, memset, ptr_add, ptr_eq, stack_alloc, store_i32, store_i64, store_ptr

enter_guard = extern("pcc_thread_no_park_enter", (), c_void)
leave_guard = extern("pcc_thread_no_park_exit", (), c_void)
checked = extern("pcc_gc_note_frame_enter_local_checked", (c_ptr, c_ptr), c_int64)
enter = extern("pcc_gc_frame_enter_local", (c_ptr, c_ptr), c_void)
enter_lifo = extern("pcc_gc_frame_enter_lifo_local", (c_ptr, c_ptr), c_void)
leave = extern("pcc_gc_frame_leave_local", (c_ptr,), c_void)
leave_lifo = extern("pcc_gc_frame_leave_lifo_local", (c_ptr,), c_void)
contains = extern("pcc_thread_native_root_contains", (c_ptr,), c_int64)
visit = extern("pcc_thread_visit_native_root_frames", (c_ptr, c_ptr), c_int64)
track = extern("pcc_gc_should_track_frame_roots", (), c_int64)
stop = extern("pcc_stop_the_world", (), c_int64)
resume = extern("pcc_resume_world", (), c_int64)
fatal = extern("pcc_platform_abort", (), c_void)

@c_abi_export("arena_probe_visit")
def arena_probe_visit(frame: c_ptr, context: c_ptr) -> None:
    ranges = load_ptr(frame, 8)
    index: i64 = 0
    used: i64 = i64(load_i64(frame, 16))
    while index < used:
        entry = ptr_add(ranges, index * 16)
        slots = load_ptr(entry, 0)
        if ptr_eq(slots, load_ptr(context, 0)) != 0:
            store_ptr(slots, 0, int_to_ptr(33))
            count: i64 = i64(load_i64(context, 16))
            store_i64(context, 16, count + 1)
        elif ptr_eq(slots, load_ptr(context, 8)) != 0:
            store_ptr(slots, 0, int_to_ptr(35))
            count: i64 = i64(load_i64(context, 16))
            store_i64(context, 16, count + 1)
        index += 1

def probe() -> i64:
    owned_map = stack_alloc(4)
    borrowed_map = stack_alloc(4)
    slots = stack_alloc(16)
    second = ptr_add(slots, 8)
    store_i32(owned_map, 0, 1)
    store_i32(borrowed_map, 0, -1)
    store_ptr(slots, 0, int_to_ptr(17))
    store_ptr(second, 0, int_to_ptr(19))
    head = load_ptr(global_addr("pcc_gc_frame_head"), 0)
    revision: i64 = i64(load_i64(global_addr("pcc_gc_root_registry_revision"), 0))
    if track() == 0:
        enter_guard()
        if checked(owned_map, slots) != 1:
            fatal()
        leave_guard()
        if contains(slots) != 0:
            fatal()
        leave(slots)
        return 0
    enter_guard()
    if checked(owned_map, slots) != 1:
        fatal()
    if checked(owned_map, slots) != 0:
        fatal()
    enter_lifo(borrowed_map, slots)
    enter(owned_map, second)
    leave(slots)
    leave_guard()
    if contains(slots) != 1 or contains(second) != 1:
        fatal()
    if stop() != 0:
        fatal()
    context = stack_alloc(24)
    memset(context, 0, 24)
    store_ptr(context, 0, slots)
    store_ptr(context, 8, second)
    visit(function_addr("arena_probe_visit"), context)
    if load_i64(context, 16) != 2:
        fatal()
    if ptr_eq(load_ptr(slots, 0), int_to_ptr(33)) == 0:
        fatal()
    if ptr_eq(load_ptr(second, 0), int_to_ptr(35)) == 0:
        fatal()
    if ptr_eq(load_ptr(global_addr("pcc_gc_frame_head"), 0), head) == 0:
        fatal()
    after_revision: i64 = i64(load_i64(global_addr("pcc_gc_root_registry_revision"), 0))
    if after_revision != revision:
        fatal()
    if resume() != 0:
        fatal()
    leave_lifo(slots)
    leave(second)
    if contains(slots) != 0 or contains(second) != 0:
        fatal()
    return 0

def main():
    print(probe())
main()
'''


def test_native_private_frames_rewrite_and_retire_without_global_registration(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    source = tmp_path / 'arena_probe.py'
    source.write_text(PROGRAM)
    executable = tmp_path / 'arena_probe'
    python_program_compiler(str(source), str(executable), backend='self',
                            ir_scaffold_mode='on', runtime_archive=str(pcc_runtime_archive))
    for backend, tracing in MODES:
        result = subprocess.run(
            [str(executable)], capture_output=True, text=True, timeout=20,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_GC_TRACING_ONLY=str(tracing),
                     PCC_GC4_TRACING_ONLY='0'),
        )
        assert (result.returncode, result.stdout, result.stderr) == (0, '0\n', ''), (backend, tracing, result)


THREADED_PROGRAM = r'''from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_load_i64, atomic_store_i64, function_addr, global_addr, int_to_ptr,
    load_i64, load_ptr, memset, null, ptr_add, ptr_eq, ptr_is_null, stack_alloc,
    store_i32, store_i64, store_ptr,
)

enter_guard = extern("pcc_thread_no_park_enter", (), c_void)
leave_guard = extern("pcc_thread_no_park_exit", (), c_void)
checked = extern("pcc_gc_note_frame_enter_local_checked", (c_ptr, c_ptr), c_int64)
leave = extern("pcc_gc_frame_leave_local", (c_ptr,), c_void)
visit = extern("pcc_thread_visit_native_root_frames", (c_ptr, c_ptr), c_int64)
track = extern("pcc_gc_should_track_frame_roots", (), c_int64)
start = extern("pcc_thread_start", (c_ptr, c_ptr, c_ptr), c_int64)
join = extern("pcc_thread_join", (c_ptr, c_ptr), c_int64)
poll = extern("pcc_thread_safepoint", (), c_void)
stop = extern("pcc_stop_the_world", (), c_int64)
resume = extern("pcc_resume_world", (), c_int64)
fatal = extern("pcc_platform_abort", (), c_void)

# Per-worker raw record: ready, release, resumed, result, root, handle,
# replacement and reserved, each one 8-byte word. Four records precede the
# visitor's count word. Values are tagged immediates, never bogus object roots.
@c_abi_export("arena_thread_worker")
def arena_thread_worker(shared: c_ptr) -> c_ptr:
    root_map = stack_alloc(4)
    slot = stack_alloc(8)
    store_i32(root_map, 0, 1)
    store_ptr(slot, 0, int_to_ptr(17))
    enter_guard()
    if checked(root_map, slot) != 1:
        fatal()
    leave_guard()
    store_ptr(shared, 32, slot)
    atomic_store_i64(shared, 0, 1, "release")
    while atomic_load_i64(shared, 8, "acquire") == 0:
        poll()
    expected = load_ptr(shared, 48)
    if track() == 0:
        expected = int_to_ptr(17)
    if ptr_eq(load_ptr(slot, 0), expected) == 0:
        store_i64(shared, 24, 1)
    leave(slot)
    atomic_store_i64(shared, 16, 1, "release")
    return shared

@c_abi_export("arena_threads_visit")
def arena_threads_visit(frame: c_ptr, shared: c_ptr) -> None:
    ranges = load_ptr(frame, 8)
    index: i64 = 0
    used: i64 = i64(load_i64(frame, 16))
    while index < used:
        slots = load_ptr(ptr_add(ranges, index * 16), 0)
        worker: i64 = 0
        while worker < 4:
            record = ptr_add(shared, worker * 64)
            if ptr_eq(slots, load_ptr(record, 32)) != 0:
                store_ptr(slots, 0, load_ptr(record, 48))
                count: i64 = i64(load_i64(shared, 256))
                store_i64(shared, 256, count + 1)
            worker += 1
        index += 1

def probe() -> i64:
    shared = stack_alloc(264)
    memset(shared, 0, 264)
    worker: i64 = 0
    while worker < 4:
        record = ptr_add(shared, worker * 64)
        store_ptr(record, 48, int_to_ptr(33 + worker * 2))
        if start(ptr_add(record, 40), function_addr("arena_thread_worker"), record) != 0:
            fatal()
        worker += 1
    worker = 0
    while worker < 4:
        record = ptr_add(shared, worker * 64)
        while atomic_load_i64(record, 0, "acquire") == 0:
            poll()
        worker += 1
    if stop() != 0:
        fatal()
    head = load_ptr(global_addr("pcc_gc_frame_head"), 0)
    revision: i64 = i64(load_i64(global_addr("pcc_gc_root_registry_revision"), 0))
    visit(function_addr("arena_threads_visit"), shared)
    if track() != 0 and load_i64(shared, 256) != 4:
        fatal()
    if track() == 0 and load_i64(shared, 256) != 0:
        fatal()
    worker = 0
    while worker < 4:
        record = ptr_add(shared, worker * 64)
        atomic_store_i64(record, 8, 1, "release")
        if atomic_load_i64(record, 16, "acquire") != 0:
            fatal()
        worker += 1
    visit(function_addr("arena_threads_visit"), shared)
    if track() != 0 and load_i64(shared, 256) != 8:
        fatal()
    if track() == 0 and load_i64(shared, 256) != 0:
        fatal()
    if ptr_eq(load_ptr(global_addr("pcc_gc_frame_head"), 0), head) == 0:
        fatal()
    after_revision: i64 = i64(load_i64(global_addr("pcc_gc_root_registry_revision"), 0))
    if after_revision != revision:
        fatal()
    if resume() != 0:
        fatal()
    worker = 0
    result = stack_alloc(8)
    while worker < 4:
        record = ptr_add(shared, worker * 64)
        if join(load_ptr(record, 40), result) != 0:
            fatal()
        if ptr_eq(load_ptr(result, 0), record) == 0 or load_i64(record, 24) != 0:
            fatal()
        if atomic_load_i64(record, 16, "acquire") != 1:
            fatal()
        worker += 1
    # Retain the old slot addresses only for comparison. No visited range may
    # still name one after thread exit detached the empty local arena.
    store_i64(shared, 256, 0)
    if stop() != 0:
        fatal()
    visit(function_addr("arena_threads_visit"), shared)
    if load_i64(shared, 256) != 0:
        fatal()
    if resume() != 0:
        fatal()
    return 0

def main():
    print(probe())
main()
'''


def test_native_private_frames_rewrite_four_carriers_and_detach_on_exit(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    source = tmp_path / 'arena_threads.py'
    source.write_text(THREADED_PROGRAM)
    executable = tmp_path / 'arena_threads'
    python_program_compiler(str(source), str(executable), backend='self',
                            ir_scaffold_mode='on', runtime_archive=str(pcc_runtime_archive))
    for backend, tracing in MODES:
        result = subprocess.run(
            [str(executable)], capture_output=True, text=True, timeout=20,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_GC_TRACING_ONLY=str(tracing),
                     PCC_GC4_TRACING_ONLY='0'),
        )
        assert (result.returncode, result.stdout, result.stderr) == (0, '0\n', ''), (backend, tracing, result)
