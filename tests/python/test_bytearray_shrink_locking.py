"""Managed in-place bytearray shrink operations share the mutable-copy lock."""
import os

import pytest

from tests.python.owned_regression_support import (
    _record_execution,
    assert_reference_program,
    explicit_owned_runtime,
)
from tests.python.test_buffer_factory_ownership import BufferModel, RUNTIME, _functions


def test_shrink_length_payload_and_terminator_form_one_transaction():
    # Execute the actual public runtime bodies, inspecting lock ownership
    # synchronously. No concurrent mutation or special collector is required.
    cases = []
    for op, index, initial, expected, status in (
        ("clear", None, b"ABC", b"", None),
        ("clear", None, b"", b"", None),
        ("pop", 1, b"ABC", b"AC", 66),
        ("pop", -2, b"ABC", b"AC", 66),
        ("pop", None, b"ABC", b"AB", 67),
        ("pop", 3, b"ABC", b"ABC", "pop index out of range"),
        ("pop", None, b"", b"", "pop from empty bytearray"),
    ):
        model = BufferModel("bytearray", relocate=False)
        source = model.source
        source[0].data = bytearray(initial + b"\0")
        source[0].length = len(initial)
        active, epochs, visits, reloads = [], [], [], []

        def lock():
            assert not active
            epochs.append(len(epochs) + 1)
            active.append(epochs[-1])

        def unlock():
            active.pop()

        def watch(function):
            def checked(base, off, *args):
                if isinstance(base, tuple) and base[0] is source[0]:
                    absolute = off + (base[2] if len(base) > 2 else 0)
                    if absolute >= 16:  # length, payload or trailing NUL
                        visits.append(active[-1] if active else 0)
                return function(base, off, *args)
            return checked

        def move(dst, src, count):
            saved = [watch(model.load)(src, i) for i in range(count)]
            for i, value in enumerate(saved):
                watch(model.store)(dst, i, value)

        def resolve(slot, offset):
            assert active and model.frames[slot] == -1 and offset == 0
            inscope = model.load(slot, offset)
            assert inscope is source
            reloads.append(active[-1])
            return inscope

        def outside(function):
            def checked(*args):
                assert not active, "allocation, error delivery or boxing under graph lock"
                return function(*args)
            return checked

        ns = model.ns
        ns.update(pcc_py_gc_minor_graph_lock=lock, pcc_py_gc_minor_graph_unlock=unlock,
                  load_i64=watch(model.load), load_i8=watch(model.load),
                  store_i64=watch(model.store), store_i8=watch(model.store),
                  pcc_gc_resolve_borrowed_root_slot_unlocked=resolve,
                  memmove=move, py_int_from_i64=outside(int),
                  py_exc_new=outside(model.error), py_raise_owned=outside(model.raise_owned))
        _functions(RUNTIME, {"py_bytearray_clear", "py_bytearray_pop"}, ns)
        arg = None
        if index is not None:
            arg = model.new("index", 1)
            arg[0].value = index
        if op == "clear":
            result = ns["py_bytearray_clear"](source)
        else:
            result = ns["py_bytearray_pop"](source, arg)
        if model.pending is not None:
            assert result is None and model.pending[0].kind == 5
            result = model.pending[0].message
        assert result == status
        assert model.content(source) == expected and source[0].data[len(expected)] == 0
        assert not active and model.frames == {model.external: 2}
        cases.append((op, index, initial, set(visits), reloads))
    assert all(len(reloads) == 1 and windows == set(reloads)
               for _, _, _, windows, reloads in cases), cases


def test_mutable_copy_reads_payload_while_holding_the_same_lock():
    # The public bytes() runtime body is the reader paired with clear/pop.
    model = BufferModel("bytearray", relocate=False)
    depth, reads = [], []
    previous = model.ns["memcpy"]

    def copy(dst, src, count):
        if src[0] is model.source[0]:
            assert depth == [1]
            reads.append(count)
        previous(dst, src, count)

    model.ns.update(memcpy=copy, pcc_py_gc_minor_graph_lock=lambda: depth.append(1),
                    pcc_py_gc_minor_graph_unlock=depth.pop)
    assert model.content(model.run(0)) == b"ABC"
    assert reads == [3] and not depth


PROGRAM = r"""import gc

def main():
    data = bytearray(b'A\x00\xffB')
    alias = data
    snapshot = bytes(data)
    gc.collect()
    assert data.pop(2) == 255 and bytes(alias) == b'A\x00B'
    assert data.pop(-2) == 0 and bytes(alias) == b'AB'
    assert data.pop() == 66 and bytes(alias) == b'A'
    for out_of_range in (1, -2):
        try:
            data.pop(out_of_range)
        except IndexError:
            pass
        else:
            raise AssertionError("missing pop index error")
    assert bytes(alias) == b'A'
    gc.collect()
    assert data.clear() is None and alias is data and bytes(alias) == b''
    assert data.clear() is None
    try:
        data.pop()
    except IndexError:
        pass
    else:
        raise AssertionError("missing empty pop error")
    assert snapshot == b'A\x00\xffB'
    print("BYTEARRAY_SHRINK_OK")

main()
"""
EXPECTED = "BYTEARRAY_SHRINK_OK\n"


def test_bytearray_shrink_public_reference(tmp_path):
    assert_reference_program(PROGRAM, EXPECTED, tmp_path)


@pytest.mark.integration
def test_bytearray_shrink_public_native(tmp_path, explicit_owned_runtime):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "shrink.py"
    source.write_text(PROGRAM)
    output = tmp_path / "shrink"
    compile_python(str(source), str(output), backend="self",
                   runtime_archive=str(explicit_owned_runtime))
    for mode in range(5):
        result = _record_execution(
            tmp_path, f"gc{mode}", [str(output)], dict(os.environ, PCC_GC_BACKEND=str(mode)),
        )
        assert (result["returncode"], result["stdout"], result["stderr"]) == (
            0, EXPECTED, "",
        ), (mode, result)
