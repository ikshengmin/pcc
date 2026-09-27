"""CRT-free Windows executable entry with UTF-8 argv and environment."""

from pcc import i64
from pcc.extern import c_abi_export, c_ptr, c_int, c_void, extern
from pcc.unsafe import malloc, free, null, ptr_is_null, load_i32, load_ptr, store_ptr, stack_alloc, global_addr, ptr_diff, call_void_ptr0

__pcc_freestanding__ = True

GetCommandLineW = extern("GetCommandLineW", (), c_ptr)
CommandLineToArgvW = extern("CommandLineToArgvW", (c_ptr, c_ptr), c_ptr)
LocalFree = extern("LocalFree", (c_ptr,), c_ptr)
ExitProcess = extern("ExitProcess", (c_int,), c_void)
utf8 = extern("pcc_win_utf8", (c_ptr,), c_ptr)
initial_environ = extern("pcc_win_initial_environ", (), c_ptr)
env_init = extern("pcc_platform_env_init", (c_ptr,), c_int)
main = extern("main", (c_int, c_ptr, c_ptr), c_int)


@c_abi_export("pcc_windows_start")
def pcc_windows_start() -> None:
    argc_slot = stack_alloc(8)
    wide_argv = CommandLineToArgvW(GetCommandLineW(), argc_slot)
    if ptr_is_null(wide_argv):
        ExitProcess(71)
        return
    argc: i64 = load_i32(argc_slot, 0)
    argv = malloc((argc + 1) * 8)
    if ptr_is_null(argv):
        LocalFree(wide_argv)
        ExitProcess(71)
        return
    index: i64 = 0
    while index < argc:
        value = utf8(load_ptr(wide_argv, index * 8))
        if ptr_is_null(value):
            ExitProcess(71)
            return
        store_ptr(argv, index * 8, value)
        index = index + 1
    store_ptr(argv, argc * 8, null())
    LocalFree(wide_argv)
    env = initial_environ()
    if ptr_is_null(env) or env_init(env) != 0:
        ExitProcess(71)
        return
    initializers = global_addr("__init_array_start")
    count: i64 = ptr_diff(global_addr("__init_array_end"), initializers) // 8
    index = 0
    while index < count:
        call_void_ptr0(load_ptr(initializers, index * 8))
        index = index + 1
    status: i64 = main(argc, argv, env)
    finalizers = global_addr("__fini_array_start")
    count = ptr_diff(global_addr("__fini_array_end"), finalizers) // 8
    while count > 0:
        count = count - 1
        call_void_ptr0(load_ptr(finalizers, count * 8))
    ExitProcess(status)
