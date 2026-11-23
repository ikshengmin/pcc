"""LC_MAIN adapter for executables that selected the owned thread runtime."""
from pcc import i64
from pcc.extern import c_abi_typed_export, c_int, c_int64, c_ptr, extern
from pcc.unsafe import function_addr

__pcc_freestanding__ = True

main = extern("main", (c_int, c_ptr, c_ptr, c_ptr), c_int)
call_main = extern("pcc_process_call_main", (c_ptr, c_int64, c_ptr, c_ptr, c_ptr), c_int64)


@c_abi_typed_export("pcc_darwin_dispatch_main", "i32", ("i32", "ptr", "ptr", "ptr"))
def pcc_darwin_dispatch_main(argc: i64, argv: c_ptr, envp: c_ptr, apple: c_ptr) -> i64:
    return main(argc, argv, envp, apple)


@c_abi_typed_export("pcc_darwin_runtime_main", "i32", ("i32", "ptr", "ptr", "ptr"))
def pcc_darwin_runtime_main(argc: i64, argv: c_ptr, envp: c_ptr, apple: c_ptr) -> i64:
    result: i64 = call_main(function_addr("pcc_darwin_dispatch_main"), argc, argv, envp, apple)
    return result
