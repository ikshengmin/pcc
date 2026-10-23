"""Exercise counted nonmoving leases against concurrent same-mode resets."""

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
@pcc_gc_foreign_lease_active = external global i64
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
  %ok = icmp eq i64 %token, 2
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
  %guarded = icmp eq i64 %reset, -1
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

define i64 @atomic_mode_lease_stress(ptr %unused) {
entry:
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
  %before = load atomic i64, ptr @pcc_gc_foreign_lease_active acquire, align 8
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
  %after = load atomic i64, ptr @pcc_gc_foreign_lease_active acquire, align 8
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
def test_threaded_mode_leases_preserve_backend_reset_barrier(tmp_path, threaded_pcc_runtime_archive):
    target = host_target_triple()
    helper = tmp_path / "atomic_mode_leases.o"
    helper.write_bytes(emit_owned_object('target triple = "' + target + '"\n' + STRESS_IR, target))
    source = tmp_path / "atomic_mode_leases.py"
    source.write_text('''from pcc.extern import extern, c_ptr, c_int64
from pcc.unsafe import null
probe = extern("atomic_mode_lease_stress", (c_ptr,), c_int64)
def main():
    print(probe(null()))
main()
''')
    binary = tmp_path / "atomic_mode_leases"
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   runtime_archive=str(threaded_pcc_runtime_archive), object_inputs=(str(helper),))
    for repetition in range(3):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=45,
            env=dict(os.environ, PCC_GC_BACKEND="0", PATH="", PCC_HOST_PYTHON="/nonexistent"))
        (tmp_path / (str(repetition) + ".stdout")).write_text(result.stdout)
        (tmp_path / (str(repetition) + ".stderr")).write_text(result.stderr)
        assert result.returncode == 0 and result.stdout == "0\n" and result.stderr == "", (
            repetition, result.returncode, result.stdout, result.stderr)
