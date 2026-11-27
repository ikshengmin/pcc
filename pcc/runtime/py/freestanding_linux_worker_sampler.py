"""Owned, opt-in Linux x64 worker CPU PC sampler substrate.

This archive member has no automatic registration or startup action. A future
isolated native worker caller must hold the raw output buffer for the complete
start/stop interval on the same native thread and must not create threads.
The singleton pre/postflight is a refusal gate, not a proof against an
intervening thread lifetime. Status -100 means restoration
could not be established or the owner changed: the diagnostic process must
exit without publishing compilation output. Status -3 after stop invalidates
samples (foreign SIGPROF or a changed singleton), even when state restored.
Only kernel-generated samples matching the owned timer may write the buffer.
"""
from pcc import i64
from pcc.extern import c_abi_export, c_int64, extern
from pcc.unsafe import (global_addr, define_global_i64, define_global_ptr_null, global_load_ptr, global_store_ptr, function_addr, syscall6, stack_alloc, null, ptr_is_null, atomic_load_i64, atomic_store_i64, load_i8, load_i32, load_i64, store_i32, store_i64, store_ptr, memset, cstr)
__pcc_freestanding__ = True

live_count = extern('pcc_thread_live_count', (), c_int64)
# Signal handler is single-target and SIGPROF is masked during its invocation.
define_global_i64('pcc_worker_cpu_sampler_active', 0)
define_global_i64('pcc_worker_cpu_sampler_foreign', 0)
define_global_i64('pcc_worker_cpu_sampler_count', 0)
define_global_i64('pcc_worker_cpu_sampler_dropped_count', 0)
define_global_i64('pcc_worker_cpu_sampler_capacity', 0)
define_global_i64('pcc_worker_cpu_sampler_timer', -1)
define_global_i64('pcc_worker_cpu_sampler_owner_tid', 0)
define_global_i64('pcc_worker_cpu_sampler_saved_mask', 0)
define_global_i64('pcc_worker_cpu_sampler_saved_action0', 0)
define_global_i64('pcc_worker_cpu_sampler_saved_action1', 0)
define_global_i64('pcc_worker_cpu_sampler_saved_action2', 0)
define_global_i64('pcc_worker_cpu_sampler_saved_action3', 0)
define_global_ptr_null('pcc_worker_cpu_sampler_buffer')

@c_abi_export('pcc_worker_cpu_sampler_handler')
def handler(number:i64, info, context) -> None:
    if atomic_load_i64(global_addr('pcc_worker_cpu_sampler_active'), 0, 'acquire') != 1:
        # While the handler is installed but the owner transition is blocked,
        # another thread's foreign signal must invalidate the diagnostic.
        atomic_store_i64(global_addr('pcc_worker_cpu_sampler_foreign'),0,1,'release')
        return
    if number != 27 or ptr_is_null(info) or ptr_is_null(context):
        atomic_store_i64(global_addr('pcc_worker_cpu_sampler_foreign'),0,1,'release')
        return
    if load_i32(info, 8) != -2 or load_i64(info, 24) != 5260100 or load_i32(info,16) != load_i64(global_addr('pcc_worker_cpu_sampler_timer'),0):
        atomic_store_i64(global_addr('pcc_worker_cpu_sampler_foreign'),0,1,'release')
        return
    n:i64 = load_i64(global_addr('pcc_worker_cpu_sampler_count'), 0)
    if n < load_i64(global_addr('pcc_worker_cpu_sampler_capacity'), 0):
        store_i64(global_load_ptr('pcc_worker_cpu_sampler_buffer'), n << 3, load_i64(context, 168))
        store_i64(global_addr('pcc_worker_cpu_sampler_count'), 0, n+1)
    else:
        dropped:i64 = load_i64(global_addr('pcc_worker_cpu_sampler_dropped_count'), 0)
        if dropped < 9223372036854775807:
            store_i64(global_addr('pcc_worker_cpu_sampler_dropped_count'), 0, dropped+1)

@c_abi_export('pcc_worker_cpu_sampler_singleton')
def singleton() -> i64:
    if live_count() != 1:
        return 0
    if syscall6(39,0,0,0,0,0,0) != syscall6(186,0,0,0,0,0,0):
        return 0
    fd:i64 = syscall6(257,-100,cstr('/proc/self/task'),589824,0,0,0)
    if fd < 0:
        return -1
    buf = stack_alloc(4096)
    total:i64 = 0
    count:i64 = syscall6(217,fd,buf,4096,0,0,0)
    scans:i64 = 0
    while count > 0 and scans < 4:
        scans = scans + 1
        offset:i64 = 0
        while offset < count:
            length:i64 = load_i8(buf,offset+16) & 255
            length = length + ((load_i8(buf,offset+17)&255)<<8)
            if length < 20 or offset+length > count:
                syscall6(3,fd,0,0,0,0,0)
                return -1
            first:i64 = load_i8(buf,offset+19)
            if first >= 48 and first <= 57:
                total=total+1
                if total > 1:
                    if syscall6(3,fd,0,0,0,0,0) < 0:
                        return -1
                    return 0
            offset=offset+length
        count = syscall6(217,fd,buf,4096,0,0,0)
    closed:i64 = syscall6(3,fd,0,0,0,0,0)
    if count != 0 or closed < 0:
        return -1
    return 1 if total == 1 else 0

@c_abi_export('pcc_worker_cpu_sampler_restore')
def restore() -> i64:
    action=stack_alloc(32)
    store_i64(action,0,load_i64(global_addr('pcc_worker_cpu_sampler_saved_action0'),0))
    store_i64(action,8,load_i64(global_addr('pcc_worker_cpu_sampler_saved_action1'),0))
    store_i64(action,16,load_i64(global_addr('pcc_worker_cpu_sampler_saved_action2'),0))
    store_i64(action,24,load_i64(global_addr('pcc_worker_cpu_sampler_saved_action3'),0))
    if syscall6(13,27,action,null(),8,0,0) < 0:
        return -1
    mask=stack_alloc(8)
    store_i64(mask,0,load_i64(global_addr('pcc_worker_cpu_sampler_saved_mask'),0))
    if syscall6(14,2,mask,null(),8,0,0) < 0:
        return -1
    return 0

@c_abi_export('pcc_worker_cpu_sampler_start')
def start(buffer, capacity:i64, period_ns:i64) -> i64:
    if ptr_is_null(buffer) or capacity < 1 or capacity > 8192 or period_ns < 1000000 or period_ns > 100000000:
        return -1
    if atomic_load_i64(global_addr('pcc_worker_cpu_sampler_active'),0,'acquire') != 0:
        return -2
    if singleton() != 1:
        return -3
    # Query all conflicts before modifying signal state.
    action=stack_alloc(32)
    if syscall6(13,27,null(),action,8,0,0) < 0:
        return -4
    if load_i64(action,0) != 0:
        return -5
    timer=stack_alloc(32)
    if syscall6(36,2,timer,0,0,0,0) < 0:
        return -6
    if load_i64(timer,0) or load_i64(timer,8) or load_i64(timer,16) or load_i64(timer,24):
        return -7
    before=stack_alloc(8)
    if syscall6(14,2,null(),before,8,0,0) < 0:
        return -8
    if load_i64(before,0) & 67108864:
        return -9
    pending=stack_alloc(8)
    if syscall6(127,pending,8,0,0,0,0) < 0:
        return -10
    if load_i64(pending,0) & 67108864:
        return -11
    store_i64(global_addr('pcc_worker_cpu_sampler_saved_mask'),0,load_i64(before,0))
    store_i64(global_addr('pcc_worker_cpu_sampler_saved_action0'),0,load_i64(action,0))
    store_i64(global_addr('pcc_worker_cpu_sampler_saved_action1'),0,load_i64(action,8))
    store_i64(global_addr('pcc_worker_cpu_sampler_saved_action2'),0,load_i64(action,16))
    store_i64(global_addr('pcc_worker_cpu_sampler_saved_action3'),0,load_i64(action,24))
    bits=stack_alloc(8)
    store_i64(bits,0,67108864)
    if syscall6(14,0,bits,null(),8,0,0) < 0:
        return -12
    # Signals may become pending between the first query and blocking.
    # Never claim one already pending under the prior disposition.
    if syscall6(127,pending,8,0,0,0,0) < 0:
        if syscall6(14,2,before,null(),8,0,0) < 0:
            return -100
        return -10
    if load_i64(pending,0) & 67108864:
        if syscall6(14,2,before,null(),8,0,0) < 0:
            return -100
        return -11
    atomic_store_i64(global_addr('pcc_worker_cpu_sampler_foreign'),0,0,'release')
    new_action=stack_alloc(32)
    store_ptr(new_action,0,function_addr('pcc_worker_cpu_sampler_handler'))
    store_i64(new_action,8,4 | 268435456 | 67108864)
    store_ptr(new_action,16,global_addr('pcc_linux_rt_sigreturn'))
    store_i64(new_action,24,0)
    if syscall6(13,27,new_action,null(),8,0,0) < 0:
        if syscall6(14,2,before,null(),8,0,0) < 0:
            return -100
        return -13
    event=stack_alloc(64)
    memset(event,0,64)
    store_i64(event,0,5260100)
    store_i32(event,8,27)
    store_i32(event,12,4)
    owner_tid:i64 = syscall6(186,0,0,0,0,0,0)
    if owner_tid <= 0:
        if restore() != 0:
            return -100
        return -14
    store_i32(event,16,owner_tid)
    tid=stack_alloc(4)
    if syscall6(222,3,event,tid,0,0,0) < 0:
        if restore() != 0:
            return -100
        return -14
    id:i64 = load_i32(tid,0)
    store_i64(global_addr('pcc_worker_cpu_sampler_timer'),0,id)
    spec=stack_alloc(32)
    store_i64(spec,0,0)
    store_i64(spec,8,period_ns)
    store_i64(spec,16,0)
    store_i64(spec,24,period_ns)
    if syscall6(223,id,0,spec,null(),0,0) < 0:
        if syscall6(226,id,0,0,0,0,0) < 0 or restore() != 0:
            return -100
        store_i64(global_addr('pcc_worker_cpu_sampler_timer'),0,-1)
        return -15
    store_i64(global_addr('pcc_worker_cpu_sampler_count'),0,0)
    store_i64(global_addr('pcc_worker_cpu_sampler_dropped_count'),0,0)
    store_i64(global_addr('pcc_worker_cpu_sampler_capacity'),0,capacity)
    global_store_ptr('pcc_worker_cpu_sampler_buffer',buffer)
    store_i64(global_addr('pcc_worker_cpu_sampler_owner_tid'),0,owner_tid)
    atomic_store_i64(global_addr('pcc_worker_cpu_sampler_active'),0,1,'release')
    if syscall6(14,2,before,null(),8,0,0) < 0:
        return -100
    return 0

@c_abi_export('pcc_worker_cpu_sampler_stop')
def stop() -> i64:
    if atomic_load_i64(global_addr('pcc_worker_cpu_sampler_active'),0,'acquire') != 1:
        return -1
    if syscall6(186,0,0,0,0,0,0) != load_i64(global_addr('pcc_worker_cpu_sampler_owner_tid'),0):
        return -100
    bits=stack_alloc(8)
    store_i64(bits,0,67108864)
    if syscall6(14,0,bits,null(),8,0,0) < 0:
        return -100
    atomic_store_i64(global_addr('pcc_worker_cpu_sampler_active'),0,0,'release')
    global_store_ptr('pcc_worker_cpu_sampler_buffer',null())
    id:i64 = load_i64(global_addr('pcc_worker_cpu_sampler_timer'),0)
    spec=stack_alloc(32)
    memset(spec,0,32)
    if syscall6(223,id,0,spec,null(),0,0) < 0:
        return -100
    if syscall6(226,id,0,0,0,0,0) < 0:
        return -100
    # Pending targeted signal may already have been discarded by timer_delete.
    # Bounded drain, refuse unexpected ownership rather than continue.
    info=stack_alloc(128)
    timeout=stack_alloc(16)
    memset(timeout,0,16)
    signal:i64 = syscall6(128,bits,info,timeout,8,0,0)
    if signal != -11:
        if signal != 27 or load_i32(info,8) != -2 or load_i64(info,24) != 5260100 or load_i32(info,16) != id:
            return -100
        if syscall6(128,bits,info,timeout,8,0,0) != -11:
            return -100
    if restore() != 0:
        return -100
    store_i64(global_addr('pcc_worker_cpu_sampler_owner_tid'),0,0)
    store_i64(global_addr('pcc_worker_cpu_sampler_timer'),0,-1)
    if singleton() != 1 or atomic_load_i64(global_addr('pcc_worker_cpu_sampler_foreign'),0,'acquire') != 0:
        return -3
    return load_i64(global_addr('pcc_worker_cpu_sampler_count'),0)

@c_abi_export('pcc_worker_cpu_sampler_dropped')
def dropped() -> i64:
    return load_i64(global_addr('pcc_worker_cpu_sampler_dropped_count'),0)
