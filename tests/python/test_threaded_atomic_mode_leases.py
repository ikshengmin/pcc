"""Exercise nonmoving leases against concurrent same-mode resets.

The collector is fixed once an object exists, so a GC0 address lease guards
nothing and is the no-op token: a same-mode reset may proceed while it is
held. With the refcount provenance probe the lease takes the checked path,
counts a mode reservation (token 2) and refuses resets while held.
"""

import os
import subprocess

import pytest

from pcc.backend.owned_object_emit import emit_owned_object
from pcc.frontends.python.pipeline import compile_python
from pcc.frontends.python.pipeline_targets import host_target_triple


STRESS_IR = r"""
@mode_lease_frame = internal constant i32 1
@mode_lease_start = internal global i64 0
@mode_lease_failures = internal global i64 0
@mode_lease_expect = internal global i64 2
declare i64 @pcc_gc_foreign_lease_count_quiescent()
declare ptr @py_list_new(i64)
declare void @pcc_gc_store_root(ptr, ptr)
declare void @pcc_gc_frame_enter_lifo(ptr, ptr)
declare void @pcc_gc_frame_leave_lifo(ptr)
declare i64 @pcc_gc_backend()
declare i64 @pcc_gc_set_backend(i64)
declare i64 @pcc_gc_foreign_lease_acquire(ptr)
declare i64 @pcc_gc_foreign_lease_release(ptr, i64)
declare i64 @pcc_thread_start(ptr, ptr, ptr)
declare i64 @pcc_thread_join(ptr, ptr)
declare void @pcc_thread_safepoint()
declare void @pcc_platform_abort()

define ptr @atomic_mode_lease_worker(ptr %slot) {
entry:
  br label %wait
wait:
  call void @pcc_thread_safepoint()
  %started = load atomic i64, ptr @mode_lease_start acquire, align 8
  %go = icmp eq i64 %started, 1
  br i1 %go, label %loop, label %wait
loop:
  %index = phi i64 [0, %wait], [%next, %advance], [%index, %retry]
  %token = call i64 @pcc_gc_foreign_lease_acquire(ptr %slot)
  %expect = load i64, ptr @mode_lease_expect
  %ok = icmp eq i64 %token, %expect
  br i1 %ok, label %held, label %refused
refused:
  ; A reset that already reserved -1 may reject a new acquisition.
  %transition = icmp eq i64 %token, -1
  br i1 %transition, label %retry, label %failure
retry:
  call void @pcc_thread_safepoint()
  br label %loop
held:
  %reset = call i64 @pcc_gc_set_backend(i64 0)
  %refused_reset = icmp eq i64 %reset, -1
  ; A no-op token blocks nothing: a concurrent reset may win either way.
  %untracked = icmp eq i64 %token, 0
  %reset_done = icmp eq i64 %reset, 0
  %free_reset = and i1 %untracked, %reset_done
  %guarded = or i1 %refused_reset, %free_reset
  %released = call i64 @pcc_gc_foreign_lease_release(ptr %slot, i64 %token)
  %release_ok = icmp eq i64 %released, 0
  %both = and i1 %guarded, %release_ok
  br i1 %both, label %advance, label %failure
failure:
  %old_errors = atomicrmw add ptr @mode_lease_failures, i64 1 acq_rel
  ret ptr null
advance:
  call void @pcc_thread_safepoint()
  %next = add i64 %index, 1
  %more = icmp slt i64 %next, 20000
  br i1 %more, label %loop, label %done
done:
  ret ptr null
}

define i64 @atomic_mode_lease_stress(i64 %expect) {
entry:
  store i64 %expect, ptr @mode_lease_expect
  %slot = alloca ptr
  %handle1 = alloca ptr
  %handle2 = alloca ptr
  %result = alloca ptr
  store ptr null, ptr %slot
  store ptr null, ptr %handle1
  store ptr null, ptr %handle2
  store ptr null, ptr %result
  %backend = call i64 @pcc_gc_backend()
  %is0 = icmp eq i64 %backend, 0
  br i1 %is0, label %prepare, label %wrong_backend
wrong_backend:
  ret i64 10
prepare:
  %before = call i64 @pcc_gc_foreign_lease_count_quiescent()
  call void @pcc_gc_frame_enter_lifo(ptr @mode_lease_frame, ptr %slot)
  %object = call ptr @py_list_new(i64 0)
  store ptr %object, ptr %slot
  %spawn1 = call i64 @pcc_thread_start(ptr %handle1, ptr @atomic_mode_lease_worker, ptr %slot)
  %spawn2 = call i64 @pcc_thread_start(ptr %handle2, ptr @atomic_mode_lease_worker, ptr %slot)
  store atomic i64 1, ptr @mode_lease_start release, align 8
  %ok1 = icmp eq i64 %spawn1, 0
  %ok2 = icmp eq i64 %spawn2, 0
  %spawns_ok = and i1 %ok1, %ok2
  br i1 %spawns_ok, label %reset_loop, label %spawn_failed
spawn_failed:
  %spawn_error = atomicrmw add ptr @mode_lease_failures, i64 100 acq_rel
  br label %join1
reset_loop:
  %attempt = phi i64 [0, %prepare], [%following, %reset_loop]
  %reset_result = call i64 @pcc_gc_set_backend(i64 0)
  call void @pcc_thread_safepoint()
  %following = add i64 %attempt, 1
  %again = icmp slt i64 %following, 2000
  br i1 %again, label %reset_loop, label %join1
join1:
  %h1 = load ptr, ptr %handle1
  %started1 = icmp ne ptr %h1, null
  br i1 %started1, label %do_join1, label %join2
do_join1:
  %j1 = call i64 @pcc_thread_join(ptr %h1, ptr %result)
  %joined1 = icmp eq i64 %j1, 0
  br i1 %joined1, label %join2, label %join_failed
join2:
  %h2 = load ptr, ptr %handle2
  %started2 = icmp ne ptr %h2, null
  br i1 %started2, label %do_join2, label %finish
do_join2:
  %j2 = call i64 @pcc_thread_join(ptr %h2, ptr %result)
  %joined2 = icmp eq i64 %j2, 0
  br i1 %joined2, label %finish, label %join_failed
join_failed:
  call void @pcc_platform_abort()
  unreachable
finish:
  %after = call i64 @pcc_gc_foreign_lease_count_quiescent()
  %balanced = icmp eq i64 %after, %before
  %errors = load atomic i64, ptr @mode_lease_failures acquire, align 8
  call void @pcc_gc_store_root(ptr %slot, ptr null)
  call void @pcc_gc_frame_leave_lifo(ptr %slot)
  %unbalanced = select i1 %balanced, i64 0, i64 1000
  %status = add i64 %errors, %unbalanced
  ret i64 %status
}
"""


@pytest.mark.integration
@pytest.mark.parametrize("probe", ("0", "2"))
@pytest.mark.parametrize("owner_kind", ("slab", "published_literal"))
def test_threaded_mode_leases_preserve_backend_reset_barrier(tmp_path, threaded_pcc_runtime_archive, owner_kind, probe):
    target = host_target_triple()
    helper = tmp_path / "atomic_mode_leases.o"
    stress_ir = STRESS_IR
    if owner_kind == "published_literal":
        stress_ir = r'''@mode_lease_literal = internal global { i64, i32, i32, i64, i64, i64, [4 x i8] } { i64 1, i32 4, i32 1, i64 3, i64 3, i64 -1, [4 x i8] c"abc\00" }
declare i64 @pcc_gc_pointer_register(ptr)
declare i64 @pcc_gc_granule_is_object_start(ptr)
declare i64 @pcc_gc_managed_pointer_index_seen(ptr)
''' + stress_ir
        stress_ir = stress_ir.replace(
            "%object = call ptr @py_list_new(i64 0)",
            "%object = getelementptr i8, ptr @mode_lease_literal, i64 0\n"
            "  %registered = call i64 @pcc_gc_pointer_register(ptr %object)\n"
            "  %granule = call i64 @pcc_gc_granule_is_object_start(ptr %object)\n"
            "  %seen = call i64 @pcc_gc_managed_pointer_index_seen(ptr %object)\n"
            "  %not_slab = icmp eq i64 %granule, -1\n"
            "  %indexed = icmp eq i64 %seen, 1\n"
            "  %exact_owner = and i1 %not_slab, %indexed\n"
            "  br i1 %exact_owner, label %spawn, label %invalid_owner\n"
            "invalid_owner:\n  ret i64 20\nspawn:")
        stress_ir = stress_ir.replace("[0, %prepare]", "[0, %spawn]")
    helper.write_bytes(emit_owned_object('target triple = "' + target + '"\n' + stress_ir, target))
    source = tmp_path / "atomic_mode_leases.py"
    # The checked path counts a mode reservation (token 2); without the
    # probe a GC0 lease is the no-op token.
    expect = 0 if probe == "0" else 2
    source.write_text('''from pcc.extern import extern, c_int64
stress = extern("atomic_mode_lease_stress", (c_int64,), c_int64)
def main():
    print(stress(%d))
main()
''' % expect)
    binary = tmp_path / "atomic_mode_leases"
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   runtime_archive=str(threaded_pcc_runtime_archive), object_inputs=(str(helper),))
    for repetition in range(3):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=45,
            env=dict(os.environ, PCC_GC_BACKEND="0", PATH="", PCC_HOST_PYTHON="/nonexistent",
                     PCC_GC_REFCOUNT_PROVENANCE_PROBE=probe))
        (tmp_path / (str(repetition) + ".stdout")).write_text(result.stdout)
        (tmp_path / (str(repetition) + ".stderr")).write_text(result.stderr)
        assert result.returncode == 0 and result.stdout == "0\n" and result.stderr == "", (
            repetition, result.returncode, result.stdout, result.stderr)
