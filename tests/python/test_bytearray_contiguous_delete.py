"""Ordinary contiguous deletion sees one live bytearray and one length."""

import pytest

from tests.python.owned_regression_support import (
    assert_owned_program, assert_reference_program, explicit_owned_runtime,
)
from tests.python.runtime_body_model import load_functions
from tests.python.test_buffer_factory_ownership import BufferModel, RUNTIME
from tests.python.test_bytearray_extended_slice_snapshot import _integer


@pytest.mark.parametrize("lo,hi,step", (
    (1, 3, None), (-3, None, 1), (None, -1, None), (-99, 99, 1), (5, 2, None),
))
def test_contiguous_delete_resolves_live_receiver_before_clipping(lo, hi, step):
    model = BufferModel("bytearray", relocate=True)
    ns = model.ns
    load_functions(RUNTIME, {"py_bytearray_del_slice"}, ns)
    owner = model.source[0]
    prefix, initial, live = b"PAD", b"original!", b"A\x00\xffBCD"
    ns["PYBYTEARRAYOBJECT_DATA_OFFSET"] += len(prefix)
    owner.data, owner.length = bytearray(prefix + initial + b"\0"), len(initial)
    active, locks, resolutions, lengths = [], [], [], []

    def lock():
        assert not active
        # A writer finished before our acquire. Moving the live object here
        # rewrites registered slots, not the caller's old borrowed argument.
        model.boundary()
        owner.data, owner.length = bytearray(prefix + live + b"\0"), len(live)
        locks.append(1)
        active.append(1)

    def resolve(slot, offset):
        assert active and model.frames[slot] == -1 and offset == 0
        resolutions.append(1)
        return model.load(slot, offset)

    def length(base, offset):
        if isinstance(base, tuple) and base[0] is owner and offset == 16:
            lengths.append(bool(active))
        return model.load(base, offset)

    def move(dst, src, n):
        assert active
        saved = bytes(model.load(src, i) for i in range(n))
        for i, value in enumerate(saved):
            model.store(dst, i, value)

    def in_scope(func, locked):
        def checked(*args):
            assert bool(active) == locked
            return func(*args)
        return checked

    ns.update(pcc_py_gc_minor_graph_lock=lock, pcc_py_gc_minor_graph_unlock=active.pop,
              pcc_gc_resolve_borrowed_root_slot_unlocked=resolve, load_i64=length,
              _bytes_data=in_scope(ns["_bytes_data"], True), memmove=move,
              store_i64=in_scope(model.store, True), store_i8=in_scope(model.store, True),
              py_int_value_i64=in_scope(ns["py_int_value_i64"], False),
              pcc_gc_alloc=in_scope(model.allocate, False), py_exc_new=in_scope(model.error, False))
    bounds = tuple(_integer(model, value) for value in (lo, hi, step))
    assert ns["py_bytearray_del_slice"](model.source, *bounds) == 0
    expected = bytearray(live)
    del expected[slice(lo, hi, step)]
    assert owner.length == len(expected)
    assert owner.data[:len(prefix) + len(expected) + 1] == prefix + expected + b"\0"
    assert locks == resolutions == [1] and lengths == [True] and not active
    assert model.frames == {model.external: 2} and not model.leases and model.pending is None


PROGRAM = r"""def main():
    data = bytearray(b'Q\x00A\xffBCD')
    alias = data
    snapshot = bytes(data)
    del data[2:4]
    assert bytes(alias) == b'Q\x00BCD'
    del data[-3:-2:1]
    del data[3:1]
    del data[:1]
    middle = bytes(alias)
    assert middle == b'\x00CD'
    del data[1:]
    assert bytes(alias) == b'\x00'
    del data[:]
    del data[:]
    changed = bytearray(b'ABCD\x03')
    del changed[1:changed.pop()]
    assert bytes(changed) == b'AD' and alias is data and bytes(alias) == b''
    print(snapshot[3], middle[0], middle[1], middle[-1], len(alias), changed[-1])

main()
"""
EXPECTED = "255 0 67 68 0 68\n"


def test_contiguous_delete_public_reference(tmp_path):
    assert_reference_program(PROGRAM, EXPECTED, tmp_path)


@pytest.mark.integration
def test_contiguous_delete_public_native(python_program_compiler, request,
                                         explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, EXPECTED, tmp_path, python_program_compiler,
                         mode, explicit_owned_runtime, capfd)
