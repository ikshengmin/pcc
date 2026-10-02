"""Linux libc-shaped OS leaves implemented with owned kernel calls."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_void, extern
from pcc.unsafe import (
    syscall6, target_platform_machine, load_i8, ptr_add, stack_alloc, mkdir,
)

__pcc_freestanding__ = True

abort = extern("pcc_platform_abort", (), c_void)


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
