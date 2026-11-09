"""Native borrowed leases with an independent owning root and traced alias.

Derived from test_foreign_address_leases.MOVEMENT_IR without editing it.
Both variants use the same threaded archive. False/True selects same-thread
or worker lock-handoff overlap, not nonthreaded/threaded runtime builds.
The phase-before-acquire handshake does not prove a failed CAS or queue entry.
Explicit move attempts and a checked STW handshake are not a full collection,
release-time pending-stop proof, or positive-refcount transfer qualification.
"""
from __future__ import annotations

import os
import subprocess

import pytest


BORROWED_MOVEMENT_IR = r'''

@lease_map = internal constant i32 1
@lease_borrowed_map = internal constant i32 -1
@lease_initial_pointer = internal global ptr null
@lease_initial_refcount = internal global i64 0
@lease_initial_central = internal global i64 0
@pcc_gc_foreign_lease_active = external global i64
@lease_text = internal constant [7 x i8] c"leased\00"
@lease_phase = internal global i64 0
@lease_worker_status = internal global i64 0
declare ptr @py_str_new(ptr, i64)
declare ptr @py_list_new(i64)
declare void @py_decref(ptr)
declare void @pcc_gc_store_root(ptr, ptr)
declare ptr @pcc_gc_load_ptr(ptr, ptr)
declare ptr @pcc_gc_load_borrowed_ptr(ptr, ptr)
declare ptr @pcc_gc_object_index_find(ptr)
declare i64 @pcc_gc_slot_is_runtime_root(ptr)
declare i64 @pcc_gc_root_map_is_borrowed(ptr)
declare void @pcc_platform_abort()
declare void @pcc_gc_frame_enter_lifo(ptr, ptr)
declare void @pcc_gc_frame_leave_lifo(ptr)
declare i64 @pcc_gc_backend()
declare i64 @pcc_gc_borrowed_lease_acquire(ptr)
declare i64 @pcc_gc_foreign_lease_release(ptr, i64)
declare i64 @pcc_gc_object_is_address_pinned(ptr)
declare void @pcc_gc_pin(ptr)
declare void @pcc_gc_unpin(ptr)
declare ptr @pcc_gc_generational_oldify_copy(ptr)
declare i64 @pcc_gc_backend4_relocation_set_add(ptr)
declare i64 @pcc_gc_object_known_size(ptr)
declare ptr @pcc_gc_relocate_copy(ptr, i64)
declare void @pcc_py_gc_minor_graph_lock()
declare void @pcc_py_gc_minor_graph_unlock()
declare i64 @pcc_threads_enabled()
declare i64 @pcc_thread_start(ptr, ptr, ptr)
declare i64 @pcc_thread_join(ptr, ptr)
declare void @pcc_thread_safepoint()
declare i64 @pcc_stop_the_world()
declare i64 @pcc_resume_world()

define ptr @lease_try_move(ptr %slot, i64 %backend) {
entry:
  %is3 = icmp eq i64 %backend, 3
  br i1 %is3, label %gc3, label %gc4
gc3:
  call void @pcc_py_gc_minor_graph_lock()
  %a = call ptr @pcc_gc_load_borrowed_ptr(ptr null, ptr %slot)
  %m3 = call ptr @pcc_gc_generational_oldify_copy(ptr %a)
  call void @pcc_py_gc_minor_graph_unlock()
  ret ptr %m3
gc4:
  call void @pcc_py_gc_minor_graph_lock()
  %b = call ptr @pcc_gc_load_borrowed_ptr(ptr null, ptr %slot)
  %admitted = call i64 @pcc_gc_backend4_relocation_set_add(ptr %b)
  %size = call i64 @pcc_gc_object_known_size(ptr %b)
  call void @pcc_py_gc_minor_graph_unlock()
  %m4 = call ptr @pcc_gc_relocate_copy(ptr %b, i64 %size)
  ret ptr %m4
}

define ptr @lease_thread(ptr %slot) {
entry:
  store atomic i64 1, ptr @lease_phase release, align 8
  br label %await_go
await_go:
  call void @pcc_thread_safepoint()
  %phase = load atomic i64, ptr @lease_phase acquire, align 8
  %go = icmp eq i64 %phase, 2
  br i1 %go, label %acquire, label %await_go
acquire:
  store atomic i64 3, ptr @lease_phase release, align 8
  %token = call i64 @pcc_gc_borrowed_lease_acquire(ptr %slot)
  store atomic i64 %token, ptr @lease_worker_status release, align 8
  store atomic i64 4, ptr @lease_phase release, align 8
  br label %await_release
await_release:
  call void @pcc_thread_safepoint()
  %phase2 = load atomic i64, ptr @lease_phase acquire, align 8
  %done = icmp eq i64 %phase2, 5
  br i1 %done, label %release, label %await_release
release:
  %status = call i64 @pcc_gc_foreign_lease_release(ptr %slot, i64 %token)
  store atomic i64 %status, ptr @lease_worker_status release, align 8
  ret ptr null
}

define i64 @test_borrowed_lease_movement(ptr %unused, i64 %threaded) {
entry:
  %slot = alloca ptr
  %owner = alloca ptr
  %handle = alloca ptr
  %thread_result = alloca ptr
  store ptr null, ptr %slot
  store ptr null, ptr %handle
  store ptr null, ptr %thread_result
  store ptr null, ptr %owner
  ; Register empty cells first: ownership and borrowed tracing are separate.
  call void @pcc_gc_frame_enter_lifo(ptr @lease_map, ptr %owner)
  call void @pcc_gc_frame_enter_lifo(ptr @lease_borrowed_map, ptr %slot)
  %backend = call i64 @pcc_gc_backend()
  %is3 = icmp eq i64 %backend, 3
  br i1 %is3, label %new_string, label %new_list
new_string:
  %text = call ptr @py_str_new(ptr @lease_text, i64 6)
  br label %root
new_list:
  %list = call ptr @py_list_new(i64 0)
  br label %root
root:
  %obj = phi ptr [%text, %new_string], [%list, %new_list]
  call void @pcc_gc_store_root(ptr %owner, ptr %obj)
  call void @py_decref(ptr %obj)
  ; Snapshot and first acquire share an outer graph transaction. The later
  ; same-thread/worker acquire still exercises ordinary graph entry.
  call void @pcc_py_gc_minor_graph_lock()
  %seed = call ptr @pcc_gc_load_ptr(ptr null, ptr %owner)
  store ptr %seed, ptr %slot
  %nonnull_seed = icmp ne ptr %seed, null
  br i1 %nonnull_seed, label %snapshot, label %bad_state
snapshot:
  store ptr %seed, ptr @lease_initial_pointer
  %initial_rc = load atomic i64, ptr %seed acquire, align 8
  store i64 %initial_rc, ptr @lease_initial_refcount
  %initial_central = load atomic i64, ptr @pcc_gc_foreign_lease_active acquire, align 8
  store i64 %initial_central, ptr @lease_initial_central
  %initial_ok = call i1 @lease_check_state(ptr %owner, ptr %slot, i64 0)
  br i1 %initial_ok, label %first_acquire, label %bad_state
first_acquire:
  %token = call i64 @pcc_gc_borrowed_lease_acquire(ptr %slot)
  call void @pcc_py_gc_minor_graph_unlock()
  %counted = icmp eq i64 %token, 1
  %first_state = call i1 @lease_check_state(ptr %owner, ptr %slot, i64 1)
  %first_ok = and i1 %counted, %first_state
  br i1 %first_ok, label %nested, label %bad_count
nested:
  %is_threaded = icmp ne i64 %threaded, 0
  br i1 %is_threaded, label %thread_start, label %same_thread
same_thread:
  %second = call i64 @pcc_gc_borrowed_lease_acquire(ptr %slot)
  %twice = icmp eq i64 %second, 1
  br i1 %twice, label %check_pin, label %bad_count
thread_start:
  %enabled = call i64 @pcc_threads_enabled()
  %can_thread = icmp ne i64 %enabled, 0
  br i1 %can_thread, label %start, label %bad_thread
start:
  %started = call i64 @pcc_thread_start(ptr %handle, ptr @lease_thread, ptr %slot)
  %start_ok = icmp eq i64 %started, 0
  br i1 %start_ok, label %await_started, label %bad_thread
await_started:
  call void @pcc_thread_safepoint()
  %p1 = load atomic i64, ptr @lease_phase acquire, align 8
  %worker_ready = icmp eq i64 %p1, 1
  br i1 %worker_ready, label %hold_lock, label %await_started
hold_lock:
  call void @pcc_py_gc_minor_graph_lock()
  store atomic i64 2, ptr @lease_phase release, align 8
  br label %await_contender
await_contender:
  %p2 = load atomic i64, ptr @lease_phase acquire, align 8
  %contending = icmp eq i64 %p2, 3
  br i1 %contending, label %drop_lock, label %await_contender
drop_lock:
  call void @pcc_py_gc_minor_graph_unlock()
  br label %await_acquired
await_acquired:
  call void @pcc_thread_safepoint()
  %p3 = load atomic i64, ptr @lease_phase acquire, align 8
  %acquired = icmp eq i64 %p3, 4
  br i1 %acquired, label %check_thread_token, label %await_acquired
check_thread_token:
  %worker_token = load atomic i64, ptr @lease_worker_status acquire, align 8
  %worker_counted = icmp eq i64 %worker_token, 1
  br i1 %worker_counted, label %check_pin, label %bad_thread
check_pin:
  %two_state = call i1 @lease_check_state(ptr %owner, ptr %slot, i64 2)
  br i1 %two_state, label %legacy_pin, label %bad_state
legacy_pin:
  %current = call ptr @pcc_gc_load_borrowed_ptr(ptr null, ptr %slot)
  call void @pcc_gc_pin(ptr %current)
  call void @pcc_gc_unpin(ptr %current)
  %attempt1 = call ptr @lease_try_move(ptr %slot, i64 %backend)
  %refused1 = icmp eq ptr %attempt1, null
  br i1 %refused1, label %release_first, label %bad_move
release_first:
  %released1 = call i64 @pcc_gc_foreign_lease_release(ptr %slot, i64 %token)
  %ok1 = icmp eq i64 %released1, 0
  %one_state = call i1 @lease_check_state(ptr %owner, ptr %slot, i64 1)
  %first_release_ok = and i1 %ok1, %one_state
  br i1 %first_release_ok, label %collect, label %bad_release
collect:
  ; Observe a completed stop handshake, not a collector cycle or a
  ; pending-stop request specifically serviced by lease release.
  %stopped = call i64 @pcc_stop_the_world()
  %stop_ok = icmp eq i64 %stopped, 0
  br i1 %stop_ok, label %resume, label %bad_stop
resume:
  %resumed = call i64 @pcc_resume_world()
  %resume_ok = icmp eq i64 %resumed, 0
  br i1 %resume_ok, label %second_move_attempt, label %bad_stop
second_move_attempt:
  %attempt2 = call ptr @lease_try_move(ptr %slot, i64 %backend)
  %refused2 = icmp eq ptr %attempt2, null
  br i1 %refused2, label %release_last, label %bad_move
release_last:
  br i1 %is_threaded, label %join, label %release_second
release_second:
  %released2 = call i64 @pcc_gc_foreign_lease_release(ptr %slot, i64 1)
  %ok2 = icmp eq i64 %released2, 0
  br i1 %ok2, label %move, label %bad_release
join:
  store atomic i64 5, ptr @lease_phase release, align 8
  %thread = load ptr, ptr %handle
  %joined = call i64 @pcc_thread_join(ptr %thread, ptr %thread_result)
  %worker_release = load atomic i64, ptr @lease_worker_status acquire, align 8
  %jok = icmp eq i64 %joined, 0
  %wok = icmp eq i64 %worker_release, 0
  %both = and i1 %jok, %wok
  br i1 %both, label %move, label %bad_thread
move:
  %zero_state = call i1 @lease_check_state(ptr %owner, ptr %slot, i64 0)
  br i1 %zero_state, label %unleased_move, label %bad_state
unleased_move:
  %before = call ptr @pcc_gc_load_borrowed_ptr(ptr null, ptr %slot)
  %moved = call ptr @lease_try_move(ptr %slot, i64 %backend)
  %nonnull = icmp ne ptr %moved, null
  %different = icmp ne ptr %moved, %before
  %changed = and i1 %nonnull, %different
  br i1 %changed, label %resolve, label %bad_after
resolve:
  %after = call ptr @pcc_gc_load_borrowed_ptr(ptr null, ptr %slot)
  %owned_after = call ptr @pcc_gc_load_ptr(ptr null, ptr %owner)
  %resolved = icmp eq ptr %after, %moved
  %owner_resolved = icmp eq ptr %owned_after, %moved
  %both_resolved = and i1 %resolved, %owner_resolved
  br i1 %both_resolved, label %finish, label %bad_after
finish:
  ; Borrowed cleanup cannot release ownership. The separate owning root
  ; stays live until every alias lease and worker has finished.
  store ptr null, ptr %slot
  call void @pcc_gc_frame_leave_lifo(ptr %slot)
  call void @pcc_gc_store_root(ptr %owner, ptr null)
  call void @pcc_gc_frame_leave_lifo(ptr %owner)
  ret i64 0
bad_state:
  call void @pcc_platform_abort()
  unreachable
bad_stop:
  call void @pcc_platform_abort()
  unreachable
bad_count:
  call void @pcc_platform_abort()
  unreachable
bad_move:
  call void @pcc_platform_abort()
  unreachable
bad_release:
  call void @pcc_platform_abort()
  unreachable
bad_after:
  call void @pcc_platform_abort()
  unreachable
bad_thread:
  call void @pcc_platform_abort()
  unreachable
}

; Phase snapshots occur while no helper thread changes a lease. The exact
; node and central increments are separate assertions. A live lease also
; keeps both pointer bits and the physical RC header unchanged. GC3/4 are
; count-free in production; this does not prove a positive RC transfer path.
define i1 @lease_check_state(ptr %owner, ptr %slot, i64 %expected) {
entry:
  call void @pcc_py_gc_minor_graph_lock()
  %owner_registered = call i64 @pcc_gc_slot_is_runtime_root(ptr %owner)
  %alias_registered = call i64 @pcc_gc_slot_is_runtime_root(ptr %slot)
  %owner_role = call i64 @pcc_gc_root_map_is_borrowed(ptr @lease_map)
  %alias_role = call i64 @pcc_gc_root_map_is_borrowed(ptr @lease_borrowed_map)
  %owner_root = icmp eq i64 %owner_registered, 1
  %alias_root = icmp eq i64 %alias_registered, 1
  %owning = icmp eq i64 %owner_role, 0
  %borrowed = icmp eq i64 %alias_role, 1
  %roots_ok = and i1 %owner_root, %alias_root
  %roles_ok = and i1 %owning, %borrowed
  %registered = and i1 %roots_ok, %roles_ok
  br i1 %registered, label %values, label %bad
values:
  %owned = call ptr @pcc_gc_load_ptr(ptr null, ptr %owner)
  %alias = call ptr @pcc_gc_load_borrowed_ptr(ptr null, ptr %slot)
  %nonnull = icmp ne ptr %owned, null
  %same = icmp eq ptr %alias, %owned
  %values_ok = and i1 %nonnull, %same
  br i1 %values_ok, label %lookup, label %bad
lookup:
  %node = call ptr @pcc_gc_object_index_find(ptr %owned)
  %known = icmp ne ptr %node, null
  br i1 %known, label %counts, label %bad
counts:
  %freeing_address = getelementptr i8, ptr %node, i64 32
  %freeing = load i64, ptr %freeing_address, align 8
  %live = icmp eq i64 %freeing, 0
  %count_address = getelementptr i8, ptr %node, i64 80
  %count = load i64, ptr %count_address, align 8
  %count_ok = icmp eq i64 %count, %expected
  %central = load atomic i64, ptr @pcc_gc_foreign_lease_active acquire, align 8
  %baseline = load i64, ptr @lease_initial_central
  %wanted = add i64 %baseline, %expected
  %central_ok = icmp eq i64 %central, %wanted
  %counts_ok = and i1 %count_ok, %central_ok
  %all_counts = and i1 %live, %counts_ok
  br i1 %all_counts, label %leased, label %bad
leased:
  %held = icmp sgt i64 %expected, 0
  br i1 %held, label %stable, label %good
stable:
  %initial = load ptr, ptr @lease_initial_pointer
  %stable_pointer = icmp eq ptr %owned, %initial
  %rc = load atomic i64, ptr %owned acquire, align 8
  %initial_rc = load i64, ptr @lease_initial_refcount
  %stable_rc = icmp eq i64 %rc, %initial_rc
  %stable_both = and i1 %stable_pointer, %stable_rc
  br i1 %stable_both, label %good, label %bad
good:
  call void @pcc_py_gc_minor_graph_unlock()
  ret i1 true
bad:
  call void @pcc_py_gc_minor_graph_unlock()
  ret i1 false
}
'''


def test_borrowed_movement_probe_emits_verified_owned_object():
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.owned_ir_validation import verify_ir_text

    target = host_target_triple()
    text = 'target triple = "' + target + '"\n' + BORROWED_MOVEMENT_IR
    verify_ir_text(text)
    assert emit_owned_object(text, target)


@pytest.mark.integration
@pytest.mark.parametrize("contending", (False, True))
def test_native_gc3_gc4_borrowed_counted_lease_overlap(
    tmp_path, threaded_pcc_runtime_archive, monkeypatch, contending,
):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple

    monkeypatch.setenv("PCC_WITH_THREADS", "1")
    target = host_target_triple()
    helper = tmp_path / "borrowed_lease_movement.o"
    helper.write_bytes(emit_owned_object(
        'target triple = "' + target + '"\n' + BORROWED_MOVEMENT_IR, target,
    ))
    source = tmp_path / "borrowed_lease_movement.py"
    output = tmp_path / "borrowed_lease_movement"
    source.write_text(
        "from pcc.extern import extern, c_ptr, c_int64\n"
        "from pcc.unsafe import null\n"
        'probe = extern("test_borrowed_lease_movement", (c_ptr, c_int64), c_int64)\n'
        "def check():\n"
        "    print(probe(null(), " + str(int(contending)) + "))\n"
        "check()\n"
    )
    compile_python(
        str(source), str(output), backend="self", ir_scaffold_mode="on",
        runtime_archive=str(threaded_pcc_runtime_archive), object_inputs=(str(helper),),
    )
    for backend in (3, 4):
        result = subprocess.run(
            [str(output)], capture_output=True, text=True, timeout=60,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend),
                     PCC_GC_MINOR_ALLOC_MAX="4096", PATH="",
                     PCC_HOST_PYTHON="/nonexistent/host-python"),
        )
        (tmp_path / ("gc" + str(backend) + ".stdout")).write_text(result.stdout)
        (tmp_path / ("gc" + str(backend) + ".stderr")).write_text(result.stderr)
        assert (result.returncode, result.stdout, result.stderr) == (0, "0\n", ""), (
            backend, contending, result.returncode, result.stdout, result.stderr,
        )
