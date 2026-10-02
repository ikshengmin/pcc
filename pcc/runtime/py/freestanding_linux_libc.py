"""Linux libc-shaped OS leaves implemented with owned kernel calls."""

from pcc import i64
from pcc.extern import c_abi_export, c_abi_typed_export, c_int32, c_ptr, c_void, extern
from pcc.unsafe import (
    syscall6, target_platform_machine, load_i8, load_i64, store_i64,
    ptr_add, ptr_is_null, stack_alloc, mkdir, null,
)

__pcc_freestanding__ = True

abort = extern("pcc_platform_abort", (), c_void)
pcc_errno_set = extern("pcc_errno_set", (c_int32,), c_void)


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
