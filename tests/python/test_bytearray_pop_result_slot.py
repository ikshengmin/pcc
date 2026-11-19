"""A typed bytearray.pop used as another operand must publish its actual result."""

import re

import pytest

from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module
from tests.python.owned_regression_support import (
    assert_owned_program, assert_reference_program, explicit_owned_runtime,
)


def _compiled_function(source):
    module = infer_module(parse_and_lift(source, "pop_result.py", "pop_result"))
    compiler = L1CodeGen(module, ir_scaffold_mode="on")
    compiler._strict_no_libpython = True
    compiler._prefer_native_callable_values = True
    ir = str(compiler.generate(module))
    body = re.search(r"^define [^\n]*@user_pop_result_probe\([^\n]*\).*?^}", ir, re.M | re.S)
    assert body is not None
    return body.group(0)


@pytest.mark.parametrize("bound,count", (("changed.pop()", 1), ("changed.pop(index.pop())", 2)))
def test_typed_pop_publishes_each_nested_bound_before_cleanup(bound, count):
    body = _compiled_function(
        "def probe():\n"
        "    changed = bytearray(b'ABCD\\x03')\n"
        "    index = bytearray(b'\\x01')\n"
        "    del changed[1:" + bound + "]\n"
        "    return bytes(changed)\n"
    )
    calls = list(re.finditer(
        r"^  (%[^ ]+) = call [^\n]*@py_bytearray_pop\(ptr (%[^,]+), ptr (%[^)]+)\)\n",
        body, re.M,
    ))
    assert len(calls) == count
    results = []
    for call in calls:
        # No error check, lease cleanup or subsequent operand evaluation may
        # intervene before this producer hands its result to the offered root.
        first = body[call.end():].splitlines()[0].strip()
        store = re.fullmatch(r"store ptr " + re.escape(call[1]) + r", ptr (%[^ ,]+)", first)
        assert store is not None, first
        results.append(store[1])

    def input_slot(value):
        load = re.search(r"^  " + re.escape(value) + r" = load ptr, ptr (%[^ ,\n]+)", body, re.M)
        assert load is not None
        return load[1]

    delete = re.search(r"\bcall [^\n]*@py_obj_del_slice\(ptr [^,]+, ptr [^,]+, ptr (%[^,]+),", body)
    assert delete is not None and input_slot(delete[1]) == results[-1]
    assert "@py_err_occurred(" in body[calls[-1].end():delete.start()]
    if count == 2:
        assert input_slot(calls[-1][3]) == results[0]  # Inner pop supplied the outer index.
        receiver = input_slot(calls[-1][2])
        assert "@pcc_gc_root_copy_local(ptr " + receiver + "," in body[:calls[0].start()]
    assert not re.search(r"\bcall\b[^\n]*@py_cpy_", body)


def test_untyped_pop_does_not_assume_the_bytearray_producer():
    body = _compiled_function("def probe(other):\n    return other.pop()\n")
    assert "@py_bytearray_pop(" not in body


PROGRAM = r"""def main():
    changed = bytearray(b'ABCD\x03')
    del changed[1:changed.pop()]
    assert bytes(changed) == b'AD'
    target = bytearray(b'Q\x00R')
    values = bytearray(b'\xff\x00P')
    index = bytearray(b'\x01')
    del target[:values.pop(index.pop())]
    assert bytes(target) == b'Q\x00R' and bytes(values) == b'\xffP' and not index
    del target[:values.pop(0)]
    assert bytes(target) == b'' and bytes(values) == b'P'
    try:
        del changed[:index.pop()]
    except IndexError as err:
        assert str(err) == 'pop from empty bytearray'
    else:
        raise AssertionError('missing IndexError')
    assert bytes(changed) == b'AD'
    extremes = bytearray(b'\x00\xff')
    pair = (extremes.pop(), extremes.pop())
    assert pair == (255, 0) and len(extremes) == 0
    print(changed[0], changed[1], len(target), values[0], len(index), pair[0], pair[1])

main()
"""
EXPECTED = "65 68 0 80 0 255 0\n"


def test_pop_bound_reference(tmp_path):
    assert_reference_program(PROGRAM, EXPECTED, tmp_path)


@pytest.mark.integration
def test_pop_bound_native(python_program_compiler, request,
                          explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, EXPECTED, tmp_path, python_program_compiler,
                         mode, explicit_owned_runtime, capfd)
