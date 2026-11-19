"""Managed bytearray scalar assignment validates and writes in one snapshot window."""

import pytest

from tests.python.owned_regression_support import (
    assert_owned_program,
    assert_reference_program,
    explicit_owned_runtime,
)
from tests.python.test_buffer_factory_ownership import BufferModel, RUNTIME, _functions


def test_scalar_bounds_and_byte_write_share_one_window():
    observations = []
    for index, byte, expected, error in (
        (0, 0, b"\x00BC", None), (-1, 255, b"AB\xff", None),
        (3, 255, b"ABC", 5), (-4, 0, b"ABC", 5),
        (1, -1, b"ABC", 2), (1, 256, b"ABC", 2),
    ):
        model = BufferModel("bytearray", relocate=False)
        source = model.source
        key, rhs = model.new("key", 1), model.new("rhs", 1)
        key[0].value, rhs[0].value = index, byte
        active, windows, visited, roots = [], [], [], []

        def lock():
            assert not active
            windows.append(len(windows) + 1)
            active.append(windows[-1])

        def resolve(slot, offset):
            assert active and model.frames[slot] == -1 and offset == 0
            assert model.load(slot, offset) is source
            roots.append(active[-1])
            return source

        def access(function, operation):
            def checked(base, offset, *args):
                if isinstance(base, tuple) and base[0] is source[0]:
                    absolute = (base[2] if len(base) == 3 else 0) + offset
                    if absolute >= 16:  # size or payload, not the immutable type tag
                        visited.append((operation, active[-1] if active else 0))
                return function(base, offset, *args)
            return checked

        def outside(function):
            def checked(*args):
                assert not active, "conversion or exception under graph lock"
                return function(*args)
            return checked

        ns = model.ns
        ns.update(
            pcc_py_gc_minor_graph_lock=lock, pcc_py_gc_minor_graph_unlock=active.pop,
            pcc_gc_resolve_borrowed_root_slot_unlocked=resolve,
            load_i64=access(model.load, "size"), store_i8=access(model.store, "write"),
            py_int_value_i64=outside(ns["py_int_value_i64"]),
            py_exc_new=outside(model.error), py_raise_owned=outside(model.raise_owned),
        )
        _functions(RUNTIME, {"py_bytearray_setitem"}, ns)
        status = ns["py_bytearray_setitem"](source, key, rhs)
        assert status == (0 if error is None else -1)
        assert (model.pending[0].kind if model.pending is not None else None) == error
        assert model.content(source) == expected
        assert not active and model.frames == {model.external: 2}
        assert [op for op, _ in visited] == (["size", "write"] if error is None else ["size"])
        observations.append((index, byte, roots, visited))
    assert all(len(roots) == 1 and all(window == roots[0] for _, window in visited)
               for index, byte, roots, visited in observations), observations


PROGRAM = r"""import gc

def main():
    data = bytearray(b'A\x00C')
    alias = data
    before = bytes(data)
    data[1] = 255
    gc.collect()
    alias[-1] = 0
    after = bytes(data)
    assert alias is data and after == b'A\xff\x00' and before == b'A\x00C'
    errors = 0
    for i in (-4, 3):
        try:
            data[i] = 1
        except IndexError:
            errors += 1
    for value in (-1, 256):
        try:
            data[1] = value
        except ValueError:
            errors += 1
    assert bytes(alias) == after
    assert data.pop(0) == 65
    alias[0] = 17
    assert bytes(data) == b'\x11\x00'
    data.clear()
    try:
        data[0] = 1
    except IndexError:
        errors += 1
    print(errors, len(alias), before[1], after[1], after[2])

main()
"""
EXPECTED = "5 0 0 255 0\n"


def test_scalar_store_public_reference(tmp_path):
    assert_reference_program(PROGRAM, EXPECTED, tmp_path)


@pytest.mark.integration
def test_scalar_store_public_native(python_program_compiler, request,
                                    explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, EXPECTED, tmp_path, python_program_compiler,
                         mode, explicit_owned_runtime, capfd)
