# Compiled Python is the caller; interpreting extern declarations cannot run it.
from pcc.extern import extern, c_int32, c_int64

cpp_mix = extern('python_cpp_mix',
                 (c_int64, c_int64, c_int64, c_int64, c_int64, c_int64, c_int64, c_int64), c_int64)
java_mix = extern('pcc_java_496e7465726f70_6d6978_2849494949494949492949',
                  (c_int32, c_int32, c_int32, c_int32, c_int32, c_int32, c_int32, c_int32), c_int32)
go_fold = extern('__pcc_go_main_466f6c64',
                 (c_int64, c_int64, c_int64, c_int64, c_int64, c_int64, c_int64, c_int64), c_int64)
rust_mix32 = extern('__pcc_rust_python_mix32',
                    (c_int32, c_int32, c_int32, c_int32, c_int32, c_int32, c_int32, c_int32), c_int32)
rust_mix64 = extern('__pcc_rust_python_mix64',
                    (c_int64, c_int64, c_int64, c_int64, c_int64, c_int64, c_int64, c_int64), c_int64)


def main():
    print(cpp_mix(-7000000000000, 1, 2, 4, 8, 16, 0x100000020, 0x200000040))
    print(java_mix(-2000000000, 1, 2, 4, 8, 16, 32, 64))
    print(go_fold(-5000000000000, 1, 2, 4, 8, 16, 0x100000020, 0x200000040))
    print(rust_mix32(-1000000000, 1, 2, 4, 8, 16, 32, 64))
    print(rust_mix64(5000000000000, 1, 2, 4, 8, 16, 0x100000020, 0x200000040))
    # A returned Python integer crosses a second language boundary, rather
    # than four disconnected executables printing independent constants.
    value = java_mix(-2000000000, 1, 2, 4, 8, 16, 32, 64)
    print(go_fold(value, 1, 2, 4, 8, 16, 0x100000020, 0x200000040))


main()
