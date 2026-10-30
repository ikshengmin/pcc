"""Fatal compiler-runtime leaves without managed-runtime dependencies."""

from pcc.extern import c_abi_export, c_void, extern
from pcc.unsafe import cstr, strlen, write

__pcc_freestanding__ = True

pcc_platform_abort = extern("pcc_platform_abort", (), c_void)


@c_abi_export("pcc_null_callee_trap")
def pcc_null_callee_trap() -> None:
    # Keep the existing fatal diagnostic and platform abort contract when the
    # self backend lowers a call through a constant null callee to this leaf.
    message = cstr("[NULL_CALL]\n")
    write(2, message, strlen(message))
    pcc_platform_abort()
