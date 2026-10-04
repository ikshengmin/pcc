"""Linux libc-shaped OS leaves implemented with owned kernel calls."""

from pcc import i64
from pcc.extern import (
    c_abi_export,
    c_abi_typed_export,
    c_abi_variadic_export,
    c_int32,
    c_ptr,
    c_void,
    extern,
)
from pcc.unsafe import (
    syscall6, target_platform_machine, load_i8, load_i64, store_i64,
    ptr_add, ptr_is_null, stack_alloc, mkdir, null,
    close, load_i32, process_exit, va_arg_i32, va_arg_i64, va_arg_ptr,
    va_start, va_end,
    atomic_cas_i32, atomic_store_i32, call_void_ptr0,
    define_global_i32, define_global_i64, define_global_ptr_null,
    define_global_null_ptr_array, free, global_addr, load_ptr, malloc,
    ptr_diff, store_ptr,
)

__pcc_freestanding__ = True

abort = extern("pcc_platform_abort", (), c_void)
pcc_errno_set = extern("pcc_errno_set", (c_int32,), c_void)
c_fflush = extern("fflush", (c_ptr,), c_int32)

# atexit node: next@0, callback@8, dynamically_allocated@16; 24-byte LP64.
# C requires at least 32 registrations: provide that many without allocation.
define_global_null_ptr_array("pcc_c_atexit_reserve", 96)
define_global_i64("pcc_c_atexit_reserve_used", 0)
define_global_ptr_null("pcc_c_atexit_head")
define_global_i32("pcc_c_atexit_lock", 0)
define_global_i32("pcc_c_exit_started", 0)


@c_abi_export("mkdir")
def mkdir_c(path, mode: i64) -> i64:
    return mkdir(path, mode)


@c_abi_export("isatty")
def isatty(fd: i64) -> i64:
    buffer = stack_alloc(128)
    machine = target_platform_machine()
    number: i64 = 29 if load_i8(machine, 0) == 97 else 16
    if syscall6(number, fd, 21505, buffer, 0, 0, 0) == 0:
        return 1
    return 0


@c_abi_export("arc4random_buf")
def arc4random_buf(output, size: i64) -> None:
    machine = target_platform_machine()
    number: i64 = 278 if load_i8(machine, 0) == 97 else 318
    offset: i64 = 0
    while offset < size:
        result: i64 = syscall6(number, ptr_add(output, offset), size - offset, 0, 0, 0, 0)
        if result == -4:
            continue
        if result <= 0:
            abort()
            return
        offset = offset + result


@c_abi_typed_export("chmod", "i32", ("ptr", "i32"))
def chmod(path: c_ptr, mode: i64) -> i64:
    # fchmodat follows symlinks, matching chmod; both supported Linux ABIs
    # provide it even where the older chmod syscall does not exist.
    machine = target_platform_machine()
    number: i64 = 53 if load_i8(machine, 0) == 97 else 268
    result: i64 = syscall6(number, -100, path, mode, 0, 0, 0)
    if result < 0:
        pcc_errno_set(0 - result)
        return -1
    return 0


@c_abi_typed_export("utime", "i32", ("ptr", "ptr"))
def utime(path: c_ptr, times: c_ptr) -> i64:
    # LP64 struct utimbuf is two signed time_t seconds. utimensat uses two
    # (seconds, nanoseconds) pairs; NULL requests the kernel's current time.
    # The aarch64 ABI has no legacy utime syscall.
    converted = null()
    if not ptr_is_null(times):
        converted = stack_alloc(32)
        store_i64(converted, 0, load_i64(times, 0))
        store_i64(converted, 8, 0)
        store_i64(converted, 16, load_i64(times, 8))
        store_i64(converted, 24, 0)
    machine = target_platform_machine()
    number: i64 = 88 if load_i8(machine, 0) == 97 else 280
    result: i64 = syscall6(number, -100, path, converted, 0, 0, 0)
    if result < 0:
        pcc_errno_set(0 - result)
        return -1
    return 0


@c_abi_typed_export("_Exit", "void", ("i32",))
def immediate_exit(status: i64) -> None:
    # ISO C _Exit / POSIX _exit terminate the process, including other threads.
    # The intrinsic selects Linux exit_group (231 on x86-64, 94 on AArch64).
    # Do not run callbacks, flush FILE buffers, or substitute thread SYS_exit.
    while True:
        process_exit(status)


@c_abi_typed_export("_exit", "void", ("i32",))
def posix_immediate_exit(status: i64) -> None:
    immediate_exit(status)


@c_abi_typed_export("close", "i32", ("i32",))
def close_c(fd: i64) -> i64:
    # Linux releases the descriptor even when close reports EINTR. Retrying
    # could close an unrelated descriptor reused by another thread.
    result: i64 = close(fd)
    if result < 0:
        pcc_errno_set(0 - result)
        return -1
    return result


@c_abi_typed_export("fcntl", "i32", ("i32", "i32"))
@c_abi_variadic_export("fcntl")
def fcntl_c(fd: i64, command: i64) -> i64:
    # Linux LP64: fixed arguments are int, not machine-word C longs. Consume
    # unnamed arguments according to the command; two-argument getters must
    # not fetch an absent argument. Kernel errors are not retry requests.
    machine = target_platform_machine()
    number: i64 = 25 if load_i8(machine, 0) == 97 else 72
    result: i64 = 0
    if command == 9:  # F_GETOWN can legitimately return a negative process group.
        owner = stack_alloc(8)
        result = syscall6(number, fd, 16, owner, 0, 0, 0)  # F_GETOWN_EX
        if result >= 0:
            result = load_i32(owner, 4)
            if load_i32(owner, 0) == 2:  # F_OWNER_PGRP
                return 0 - result
            return result
    elif (
        command == 1 or command == 3 or command == 11
        or command == 1025 or command == 1028
        or command == 1032 or command == 1034
    ):
        result = syscall6(number, fd, command, 0, 0, 0, 0)
    elif (
        command == 0 or command == 2 or command == 4
        or command == 8 or command == 10 or command == 1024
        or command == 1027 or command == 1030 or command == 1031
        or command == 1033
    ):
        cursor = va_start()
        argument: i64 = va_arg_i32(cursor)
        va_end(cursor)
        result = syscall6(number, fd, command, argument, 0, 0, 0)
    elif command == 1026:  # F_NOTIFY uses an unsigned long event mask.
        cursor = va_start()
        mask: i64 = va_arg_i64(cursor)
        va_end(cursor)
        result = syscall6(number, fd, command, mask, 0, 0, 0)
    elif (
        command == 5 or command == 6 or command == 7
        or command == 15 or command == 16 or command == 17
        or command == 36 or command == 37 or command == 38
        or command == 1029 or command == 1035 or command == 1036
        or command == 1037 or command == 1038
    ):
        cursor = va_start()
        argument_ptr = va_arg_ptr(cursor)
        va_end(cursor)
        result = syscall6(number, fd, command, argument_ptr, 0, 0, 0)
    else:
        # Unknown commands have no known variadic argument contract. Do not
        # read arbitrary register/stack data to guess one.
        pcc_errno_set(22)
        return -1
    if result < 0:
        pcc_errno_set(0 - result)
        return -1
    return result


@c_abi_export("pcc_c_atexit_acquire")
def _atexit_acquire() -> None:
    lock = global_addr("pcc_c_atexit_lock")
    while atomic_cas_i32(lock, 0, 0, 1, "acquire", "relaxed") != 0:
        pass


@c_abi_export("pcc_c_atexit_release")
def _atexit_release() -> None:
    atomic_store_i32(global_addr("pcc_c_atexit_lock"), 0, 0, "release")


@c_abi_typed_export("atexit", "i32", ("ptr",))
def atexit_c(callback: c_ptr) -> i64:
    # Callbacks are void(void) native function pointers. Registration is
    # serialized, and callbacks execute outside this lock so they may register
    # another callback, which becomes the next callback to run.
    _atexit_acquire()
    used: i64 = load_i64(global_addr("pcc_c_atexit_reserve_used"), 0)
    dynamic: i64 = 0
    if used < 32:
        node = ptr_add(global_addr("pcc_c_atexit_reserve"), used * 24)
        store_i64(global_addr("pcc_c_atexit_reserve_used"), 0, used + 1)
    else:
        node = malloc(24)
        if ptr_is_null(node):
            _atexit_release()
            return -1
        dynamic = 1
    store_ptr(node, 0, load_ptr(global_addr("pcc_c_atexit_head"), 0))
    store_ptr(node, 8, callback)
    store_i64(node, 16, dynamic)
    store_ptr(global_addr("pcc_c_atexit_head"), 0, node)
    _atexit_release()
    return 0


@c_abi_export("pcc_c_run_exit_callbacks")
def _run_exit_callbacks() -> None:
    while True:
        _atexit_acquire()
        node = load_ptr(global_addr("pcc_c_atexit_head"), 0)
        if ptr_is_null(node):
            _atexit_release()
            break
        store_ptr(global_addr("pcc_c_atexit_head"), 0, load_ptr(node, 0))
        callback = load_ptr(node, 8)
        dynamic: i64 = load_i64(node, 16)
        _atexit_release()
        if dynamic != 0:
            free(node)
        call_void_ptr0(callback)


@c_abi_typed_export("exit", "void", ("i32",))
def exit_c(status: i64) -> None:
    # A second/recursive exit is outside this C lifecycle contract; ensure it
    # cannot run the same callback twice or deadlock in a callback-held lock.
    if atomic_cas_i32(global_addr("pcc_c_exit_started"), 0, 0, 1,
                      "acq_rel", "acquire") != 0:
        immediate_exit(status)
    _run_exit_callbacks()
    # The owned ELF linker supplies these bounds, including for empty arrays.
    # Both direct exit and return from C main reach this same finalizer path.
    finalizers = global_addr("__fini_array_start")
    remaining: i64 = ptr_diff(global_addr("__fini_array_end"), finalizers) // 8
    while remaining > 0:
        remaining = remaining - 1
        call_void_ptr0(load_ptr(finalizers, remaining * 8))
    # A finalizer may register an atexit callback, just like an exit callback.
    _run_exit_callbacks()
    c_fflush(null())
    # The kernel closes descriptors; no wait for children or stdio flush is
    # part of _Exit itself. An output error does not change the requested code.
    immediate_exit(status)
