"""Real pcc-Python C-ABI buffer entry; no replacement allocator or startup."""
from pcc import i64
from pcc.extern import c_abi_typed_export
from pcc.unsafe import load_i64, store_i64

__pcc_freestanding__ = True


@c_abi_typed_export("raw_python_mutate", "i64", ("ptr", "i64"))
def raw_python_mutate(buffer, count: i64) -> i64:
    index: i64 = 0
    total: i64 = 0
    while index < count:
        value: i64 = load_i64(buffer, index * 8) + 17
        store_i64(buffer, index * 8, value)
        total = total + value
        index = index + 1
    return total
