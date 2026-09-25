# Full-module regression for PY-P1-CONTEXTUAL-CLASS-METHOD-ARG-TAGGING.
#
# The original reproducer was a saved GUI module whose runtime left the core.
# This keeps the context that triggered the leak -- module state behind raw
# pointer helpers, typed module functions sharing the method's name, and a
# class method with unannotated parameters forwarding to a C extern -- with a
# libc extern whose effect shows the exact integers that crossed the edge.
# A boxed int leaking through (the historical 0x4000000000 pointer bits)
# fills the wrong bytes or faults.

from pcc.extern import c_int32, c_int64, c_ptr, c_rawptr, extern
from pcc.unsafe import (
    define_global_i64_array,
    global_addr,
    int_to_ptr,
    load_i32,
    load_i64,
    ptr_to_int,
    stack_alloc,
    store_i32,
    store_i64,
)

memset = extern("memset", (c_ptr, c_int32, c_int64), c_rawptr)

# buffer@0 count@8 last@16
define_global_i64_array("contextual_fill_state", 0, 0, 0)


def _g(off: int) -> int:
    return load_i64(global_addr("contextual_fill_state"), off)


def _setg(off: int, value: int) -> None:
    store_i64(global_addr("contextual_fill_state"), off, value)


def _gp(off: int):
    return int_to_ptr(_g(off))


def fill(byte_value: int, count: int, offset: int) -> None:
    memset(int_to_ptr(_g(0) + offset), byte_value, count)


def record(slot: int, value: int) -> None:
    buf = _gp(0)
    store_i32(buf, slot * 4, value & 255)
    _setg(8, _g(8) + 1)


def last() -> int:
    return _g(16)


class ContextualFillApp:
    # Keep the business values contextual so the method ABI is projected from
    # the call site and the extern edge sees the projected integers.
    def fill(
        self,
        byte_value,
        count,
        offset,
    ) -> int:
        memset(
            int_to_ptr(_g(0) + offset),
            byte_value,
            count,
        )
        _setg(16, count)
        return count

    def exercise(self) -> None:
        buf = stack_alloc(40)
        store_i64(buf, 0, 0)
        store_i64(buf, 8, 0)
        store_i64(buf, 16, 0)
        store_i64(buf, 24, 0)
        store_i64(buf, 32, 0)
        _setg(0, ptr_to_int(buf))
        rc = self.fill(7, 12, 8)
        print(rc)
        print(load_i64(buf, 0))
        print(load_i64(buf, 8))
        print(load_i64(buf, 16))
        print(load_i64(buf, 24))
        print(load_i32(buf, 32))
        print(last())


ContextualFillApp().exercise()
