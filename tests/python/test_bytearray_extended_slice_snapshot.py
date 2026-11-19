"""Ordinary non-unit bytearray slice stores consume a complete RHS snapshot."""

import pytest

from tests.python.owned_regression_support import (
    assert_owned_program,
    assert_reference_program,
    explicit_owned_runtime,
)
from tests.python.test_buffer_factory_ownership import BufferModel, RUNTIME, _functions


def _slice_model(failure=None):
    model = BufferModel("bytearray", relocate=False, failure=failure)
    addr = model.ns["global_addr"]
    model.ns["global_addr"] = lambda name: -2 if name == "pcc_bytearray_slice_borrowed_map" else addr(name)
    def quotient(numerator, divisor):
        assert numerator >= 0 and divisor > 0
        return numerator // divisor
    model.ns["unsigned_div_i64"] = quotient
    _functions(RUNTIME, {"py_bytearray_set_slice", "py_bytearray_pop"}, model.ns)
    return model


def test_extended_slice_source_snapshots_before_writing():
    rows = []
    for form in ("self", "view", "nested", "mutable", "frozen", "empty"):
        model = _slice_model()
        receiver = model.source
        initial = b"" if form == "empty" else b"ABCD"
        receiver[0].length = len(initial)
        receiver[0].data = bytearray(initial + b"\0")
        rhs = receiver
        if form in ("view", "nested"):
            for i in range(2 if form == "nested" else 1):
                view = model.new("view", 11)
                rhs[0].refs += 1
                view[0].base = rhs
                rhs = view
        elif form in ("mutable", "frozen"):
            rhs = model.new(form, 10 if form == "mutable" else 9)
            rhs[0].length = len(initial)
            rhs[0].data = bytearray(initial + b"\0")
        step = model.new("step", 1)
        step[0].value = -1
        ns = model.ns
        result = ns["py_bytearray_set_slice"](receiver, None, None, step, rhs)
        assert result == 0 and model.pending is None
        assert not model.leases and not model.pins and not model.raw
        assert model.frames == {model.external: 2}
        rows.append((form, model.content(receiver), initial[::-1]))
    assert all(actual == expected for _, actual, expected in rows), rows


@pytest.mark.parametrize("shrink,fail", ((False, False), (True, False), (False, True)))
def test_alias_reservation_rechecks_the_live_leaf_and_keeps_writes_atomic(shrink, fail):
    model = _slice_model("allocation" if fail else None)
    receiver = model.source
    receiver[0].data = bytearray(b"ABCD\0")
    receiver[0].length = 4
    step = model.new("step", 1)
    step[0].value = -1
    ns = model.ns
    depth, epochs, copies, writes, reservations = [], [], [], [], []
    during_reserve = []

    def lock():
        assert not depth
        epochs.append(len(epochs) + 1)
        depth.append(epochs[-1])

    def allocate(size, tag, flags):
        assert not depth
        reservations.append(size - 25)
        if shrink:
            during_reserve.append(True)
            assert ns["py_bytearray_pop"](model.load(model.external, 0), None) == 68
            during_reserve.pop()
        return model.allocate(size, tag, flags)

    def copy(dst, src, size):
        if src[0] is receiver[0]:
            assert len(depth) == 1 and dst[0] is not receiver[0]
            copies.append((depth[-1], size))
        model.memcpy(dst, src, size)

    def store(base, offset, byte):
        if isinstance(base, tuple) and base[0] is receiver[0] and not during_reserve:
            assert len(depth) == 1
            writes.append(depth[-1])
        model.store(base, offset, byte)

    def error(kind, message):
        assert not depth
        return model.error(kind, message)

    ns.update(pcc_py_gc_minor_graph_lock=lock, pcc_py_gc_minor_graph_unlock=depth.pop,
              pcc_gc_alloc=allocate, memcpy=copy, store_i8=store,
              py_int_from_i64=int, py_exc_new=error)
    result = ns["py_bytearray_set_slice"](receiver, None, None, step, receiver)
    assert reservations == [4] and not depth
    assert not model.leases and not model.raw and model.frames == {model.external: 2}
    if fail:
        assert result == -1 and model.pending[0].kind == 19
        assert model.content(receiver) == b"ABCD" and not copies and not writes
    else:
        expected = b"CBA" if shrink else b"DCBA"
        assert result == 0 and model.pending is None and model.content(receiver) == expected
        assert len(copies) == 1 and copies[0][1] == len(expected)
        assert writes == [copies[0][0]] * len(expected)


def _integer(model, value):
    if value is None:
        return None
    obj = model.new("scalar", 1)
    obj[0].value = value
    return obj


@pytest.mark.parametrize("fail", (False, True))
def test_extended_sequence_converts_once_before_live_bounds(fail):
    model = _slice_model()
    if fail:
        model.failure = "index"
    source, seq = model.source, model.new("sequence", 6)
    source[0].data, source[0].length = bytearray(b"ABCD\0"), 4
    for i, value in enumerate((88, 89)):
        item = model.new("element-" + str(i), 30)
        item[0].value = value
        seq[0].payload.append(item)
    calls, held = [], []

    def convert(slot, result):
        assert not held
        if not calls:
            assert model.ns["py_bytearray_pop"](model.load(model.external, 0), None) == 68
        calls.append(model.load(slot, 0)[0].value)
        return model.index(slot, result)

    model.ns.update(pcc_py_gc_minor_graph_lock=lambda: held.append(True),
                    pcc_py_gc_minor_graph_unlock=held.pop, py_obj_index_slots=convert,
                    py_int_from_i64=int)
    status = model.ns["py_bytearray_set_slice"](source, None, None, _integer(model, -2), seq)
    assert calls == [88, 89] and not held and not model.leases and not model.raw
    assert model.frames == {model.external: 2}
    if fail:
        assert status == -1 and model.content(source) == b"ABC"
        assert (model.pending[0].kind, model.pending[0].message) == (2, "index-callback-error")
    else:
        assert status == 0 and model.pending is None and model.content(source) == b"YBX"


def test_explicit_and_missing_signed_bounds_match_slice_order():
    for low, high, stride in (
        (None, None, -1), (None, -1, -1), (-99, 99, 2), (99, -99, -2),
        (-3, None, -2), (3, 1, 2), (2, 4, 2**63-1), (None, None, -(2**63)),
    ):
        m = _slice_model()
        expected = bytearray(b"abcd")
        m.source[0].data, m.source[0].length = expected + b"\0", len(expected)
        key = slice(low, high, stride)
        replacement = bytes(range(87, 87 + len(expected[key])))
        expected[key] = replacement
        rhs = m.new("bytes", 9)
        rhs[0].data, rhs[0].length = bytearray(replacement + b"\0"), len(replacement)
        assert m.ns["py_bytearray_set_slice"](
            m.source, _integer(m, low), _integer(m, high), _integer(m, stride), rhs
        ) == 0
        assert m.content(m.source) == expected and m.pending is None and not m.leases


PROGRAM = r"""import gc

class Byte:
    def __init__(self, target, calls, value):
        self.target = target
        self.calls = calls
        self.value = value
    def __index__(self):
        if not self.calls:
            self.target.pop()
        self.calls.append(self.value)
        return self.value

def main():
    a = bytearray(b'ABCD')
    alias = a
    a[::-1] = alias
    print(bytes(a))
    b = bytearray(b'ABCD')
    b[::-1] = memoryview(memoryview(b))
    print(bytes(b))
    c = bytearray(b'abcdef')
    c[5:0:-2] = bytearray(b'XYZ')
    print(bytes(c))
    d = bytearray(b'abcdef')
    d[::2] = (49, 50, 51)
    print(bytes(d))
    t = bytearray(b'ABCD')
    calls = []
    t[::-2] = [Byte(t, calls, 88), Byte(t, calls, 89)]
    print(bytes(t), calls)
    e = bytearray()
    e[::-1] = e
    gc.collect()
    print(len(e))
    try:
        c[::2] = b'X'
    except ValueError:
        print(bytes(c))

main()
"""
EXPECTED = "b'DCBA'\nb'DCBA'\nb'aZcYeX'\nb'1b2d3f'\nb'YBX' [88, 89]\n0\nb'aZcYeX'\n"


def test_extended_slice_public_reference(tmp_path):
    assert_reference_program(PROGRAM, EXPECTED, tmp_path)


@pytest.mark.integration
def test_extended_slice_public_native(python_program_compiler, request,
                                      explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, EXPECTED, tmp_path, python_program_compiler, mode,
                         explicit_owned_runtime, capfd)
