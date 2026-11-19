"""Ordinary equal/shrinking bytearray slices; growth and export policy are separate."""

import pytest

from tests.python.owned_regression_support import (
    assert_owned_program, assert_reference_program, explicit_owned_runtime,
)
from tests.python.test_bytearray_extended_slice_snapshot import _integer, _slice_model


def _content(value, data):
    value[0].data, value[0].length = bytearray(data + b"\0"), len(data)
    return value


def _sequence_case(fail=False):
    model = _slice_model()
    if fail:
        model.failure = "index"
    target = _content(model.source, b"ABCD")
    seq = model.new("sequence", 6)
    for i, value in enumerate((88, 89) if fail else (88,)):
        el = model.new("element-" + str(i), 30)
        el[0].value = value
        seq[0].payload.append(el)
    calls, held = [], []
    def index(arg, out):
        assert not held
        if not calls:
            assert model.ns["py_bytearray_pop"](target, None) == 68
        calls.append(model.load(arg, 0)[0].value)
        return model.index(arg, out)
    model.ns.update(py_obj_index_slots=index, py_int_from_i64=int, memmove=model.memcpy,
                    pcc_py_gc_minor_graph_lock=lambda: held.append(True),
                    pcc_py_gc_minor_graph_unlock=held.pop)
    result = model.ns["py_bytearray_set_slice"](target, _integer(model, -2), None, None, seq)
    assert not held and not model.leases and not model.raw
    return result, model.content(target), calls, model.pending


def test_contiguous_bounds_follow_the_replacement_conversion():
    status, actual, calls, error = _sequence_case()
    assert (status, calls, error) == (0, [88], None)
    assert actual == b"AX"
    status, actual, calls, error = _sequence_case(fail=True)
    assert (status, actual, calls) == (-1, b"ABC", [88, 89])
    assert (error[0].kind, error[0].message) == (2, "index-callback-error")


def test_contiguous_payload_tail_and_length_share_the_snapshot_lock():
    model = _slice_model()
    target = _content(model.source, b"ABCDEF")
    rhs = _content(model.new("mutable", 10), b"XY")
    epoch = []
    active = []
    read_lengths, writes = [], []
    def lock():
        assert not active
        epoch.append(len(epoch) + 1)
        active.append(epoch[-1])
    def observed(access, action, base, offset, *args):
        if isinstance(base, tuple) and (base[0] is target[0] or base[0] is rhs[0]):
            addr = (base[2] if len(base) == 3 else 0) + offset
            if addr == 16 or addr >= 24:
                assert active, "buffer length or payload accessed outside snapshot lock"
                if action == "write":
                    writes.append((active[-1], addr))
                elif addr == 16:
                    read_lengths.append((active[-1], base[0]))
        return access(base, offset, *args)
    def move(dst, src, count):
        data = [observed(model.load, "read", src, i) for i in range(count)]
        for i, value in enumerate(data):
            observed(model.store, "write", dst, i, value)
    def outside(fn):
        def call(*args):
            assert not active, "allocation or error delivery in writer transaction"
            return fn(*args)
        return call
    model.ns.update(pcc_py_gc_minor_graph_lock=lock, pcc_py_gc_minor_graph_unlock=active.pop,
                    load_i64=lambda b, i: observed(model.load, "read", b, i),
                    store_i64=lambda b, i, v: observed(model.store, "write", b, i, v),
                    store_i8=lambda b, i, v: observed(model.store, "write", b, i, v),
                    memcpy=move, memmove=move, pcc_gc_alloc=outside(model.allocate),
                    py_exc_new=outside(model.error), py_raise_owned=outside(model.raise_owned))
    assert model.ns["py_bytearray_set_slice"](target, _integer(model, 1), _integer(model, 5),
                                             None, rhs) == 0
    assert model.content(target) == b"AXYF" and target[0].data[4] == 0
    assert set(id(value) for _, value in read_lengths) == {id(target[0]), id(rhs[0])}
    assert len({n for n, _ in read_lengths + writes}) == 1
    assert {offset for _, offset in writes} == {25, 26, 27, 16, 28}
    assert not active and not model.leases and not model.raw


def test_contiguous_growth_still_rejects_without_writing():
    # This is a native representation limitation, deliberately not asserted
    # as a CPython match. It is a status-only error after unlocking.
    for form in ("self", "mutable", "frozen"):
        model = _slice_model()
        v = _content(model.source, b"ABCD")
        rhs = v if form == "self" else _content(model.new(form, 9 if form == "frozen" else 10), b"xy")
        held = []
        def error(kind, message):
            assert not held
            return model.error(kind, message)
        model.ns.update(pcc_py_gc_minor_graph_lock=lambda: held.append(1),
                        pcc_py_gc_minor_graph_unlock=held.pop, py_exc_new=error)
        assert model.ns["py_bytearray_set_slice"](v, _integer(model, 2), _integer(model, 3),
                                                  _integer(model, 1), rhs) == -1
        assert model.pending[0].kind == 11 and model.content(v) == b"ABCD"
        assert not held and not model.leases and not model.raw


def test_contiguous_bounds_and_legal_aliases_match_cpython():
    for lo, hi, form, data in (
        (None, None, "self", b"ABCD"),
        (-(2**63), 2**63-1, "nested self-view", b"ABCD"),
        (1, -1, "distinct view", b"Z"),
        (-100, 100, "distinct mutable", b""),
        (3, 1, "frozen", b""),
        (0, 2, "frozen", b"\x00\xff"),
    ):
        expected = bytearray(b"ABCD")
        model = _slice_model()
        target = _content(model.source, expected)
        rhs = target if form in ("self", "nested self-view") else _content(
            model.new("rhs", 9 if form == "frozen" else 10), data)
        oracle_rhs = expected if form in ("self", "nested self-view") else bytearray(data)
        depth = {"nested self-view": 2, "distinct view": 1}.get(form, 0)
        for _ in range(depth):
            wrapped = model.new("view", 11)
            rhs[0].refs += 1
            wrapped[0].base = rhs
            rhs = wrapped
            oracle_rhs = memoryview(oracle_rhs)
        expected[slice(lo, hi, 1)] = oracle_rhs
        model.ns["memmove"] = model.memcpy  # Shrinks move the tail toward zero.
        before = model.allocations
        assert model.ns["py_bytearray_set_slice"](target, _integer(model, lo), _integer(model, hi),
                                                  _integer(model, 1), rhs) == 0
        assert model.content(target) == expected and target[0].data[len(expected)] == 0
        assert model.allocations == before and not model.raw and not model.leases


PROGRAM = """import gc

class Byte:
    def __init__(self, target, calls):
        self.target = target
        self.calls = calls

    def __index__(self):
        self.calls.append(self.target.pop())
        return 88

class Bad:
    def __init__(self, reason):
        self.reason = reason

    def __index__(self):
        raise self.reason

def main():
    a = bytearray(b'ABCDEF')
    alias = a
    a[1:5] = bytearray(b'XY')
    print(bytes(alias))
    rhs = bytearray(b'WZ')
    a[1:3:1] = memoryview(rhs)
    print(bytes(a), bytes(rhs))
    a[3:1] = ()
    a[-2:] = (48, 49)
    print(bytes(alias))

    b = bytearray(b'ABCD')
    view = memoryview(memoryview(b))
    b[:] = b
    b[:] = view
    b[1:2] = b'Z'
    print(bytes(view), bytes(b))
    # The only active view of b sees equal-size stores. No export of c or e.
    c = bytearray(b'ABCD')
    calls = []
    c[-2:] = [Byte(c, calls)]
    print(bytes(c), calls)
    error = ValueError('not a byte')
    try:
        c[:2] = [89, Bad(error)]
    except ValueError as caught:
        assert caught is error
    else:
        raise AssertionError('element exception lost')
    assert bytes(c) == b'AX'
    e = bytearray(b'ABCD')
    e[1:3] = b''
    print(bytes(e))
    e[:] = b''
    gc.collect()
    print(len(e), bytes(alias))
main()
"""
EXPECTED = ("b'AXYF'\nb'AWZF' b'WZ'\nb'AW01'\nb'AZCD' b'AZCD'\n"
            "b'AX' [68]\nb'AD'\n0 b'AW01'\n")


def test_contiguous_slice_public_reference(tmp_path):
    assert_reference_program(PROGRAM, EXPECTED, tmp_path)


@pytest.mark.integration
def test_contiguous_slice_public_native(python_program_compiler, request, explicit_owned_runtime,
                                       tmp_path, capfd):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, EXPECTED, tmp_path, python_program_compiler, mode,
                         explicit_owned_runtime, capfd)
