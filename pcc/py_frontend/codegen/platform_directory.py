"""Directory intrinsic ABI selection, with no shell or host interpreter."""

from pcc.llvm_capi import ir


def emit(owner, intrinsic, expr):
    pointer = ir.IntType(8).as_pointer()
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    owner._unsafe_expect_arity(intrinsic, expr, 1)
    argument = owner._unsafe_ptr_arg(expr.args[0])
    result_type = pointer if intrinsic in ("directory_open", "directory_next") else i64
    platform = owner._target_sys_platform_text()
    if platform in ("linux", "win32"):
        prefix = "pcc_linux_" if platform == "linux" else "pcc_win_"
        function = owner._declare_external_function(prefix + intrinsic, result_type, [pointer])
        return owner.builder.call(function, [argument])
    if platform != "darwin":
        raise NotImplementedError("owned directory target: " + platform)
    if intrinsic == "directory_open":
        function = owner._declare_external_function("opendir", pointer, [pointer])
        return owner.builder.call(function, [argument])
    if intrinsic == "directory_close":
        function = owner._declare_external_function("closedir", i32, [pointer])
        return owner.builder.sext(owner.builder.call(function, [argument]), i64)
    error_function = owner._declare_external_function("__error", i32.as_pointer(), [])
    error_pointer = owner.builder.call(error_function, [])
    if intrinsic == "directory_error":
        return owner.builder.sub(ir.Constant(i64, 0), owner.builder.sext(owner.builder.load(error_pointer), i64))
    owner.builder.store(ir.Constant(i32, 0), error_pointer)
    function = owner._declare_external_function("readdir", pointer, [pointer])
    entry = owner.builder.call(function, [argument])
    empty = owner.builder.icmp_unsigned("==", entry, ir.Constant(pointer, None))
    name = owner.builder.gep(entry, [ir.Constant(i64, 21)])
    return owner.builder.select(empty, ir.Constant(pointer, None), name)
