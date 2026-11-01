"""bytes/bytearray split and rsplit with CPython's arguments; slice repr.

Python reference: "Bytes and Bytearray Operations" (``bytes.split(sep=None,
maxsplit=-1)``, ``bytes.rsplit``) and "Slice objects" in the standard type
hierarchy. ``b"a b".split()`` used to raise AttributeError (only one or two
positional arguments were lowered), ``rsplit`` was missing, and
``repr(slice(1, 2))`` printed the default ``<slice object at 0x...>``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python


_PROGRAM = r'''
data = b"a b  c\n"
print(data.split())
print(data.split(b" "))
print(b"a,b,,c".split(b",", 2))
print(b"  x  y ".split(None, 1))
print(b"  x  y ".split(None, 0))
print(b"".split(), b"".split(b","))
print(data.rsplit(None, 1))
print(b"  x  y ".rsplit(None, 1))
print(b"a,b,c".rsplit(sep=b",", maxsplit=1))
print(b"a,b,c".rsplit(b","))
print(bytearray(b"k=v=w").split(maxsplit=1, sep=b"="))
print(type(bytearray(b"p q").split()[0]).__name__)
print(b"a\x0bb\x0cc\rd\te".split())
print(b"aXXbXXc".split(b"XX", -5))
try:
    b"abc".split(b"")
except ValueError as error:
    print("ValueError", error)
try:
    b"abc".split("b")
except TypeError as error:
    print("TypeError", error)
print(repr(slice(1, 2)))
print(slice(None, 5, 2))
print(repr(slice("a", [1], None)))
print(str(slice(3)))
print([slice(1, 2, 3)])
'''


def test_every_split_shape_lowers_to_the_full_runtime_entry(tmp_path):
    source = tmp_path / "split.py"
    source.write_text(_PROGRAM, encoding="utf-8")
    output = tmp_path / "split.ll"
    compile_python(str(source), str(output), emit_llvm_only=True)
    calls = re.findall(r"@py_bytes_split_full\(ptr [^,]+, ptr [^,]+, ptr [^,]+, i64 ([01])\)",
                       output.read_text(encoding="utf-8"))
    # 13 split and 4 rsplit calls (the two error cases included).
    assert sorted(calls) == ["0"] * 13 + ["1"] * 4


_SPLIT_HELPERS = (
    "_bytes_is_space", "_bytes_split_piece", "_bytes_find_at", "_bytes_rfind_before",
    "_bytes_split_whitespace", "_bytes_rsplit_whitespace", "_bytes_split_sep",
    "_bytes_rsplit_sep", "py_bytes_split_full",
)


class _Value:
    def __init__(self, value):
        self.value, self.refs = value, 1


def _split_body(allocated, errors):
    """The actual py_bytes_split_full body over Python stand-ins for memory."""
    from tests.python.test_split_abs_result_ownership import runtime_body

    none = _Value(None)

    def new(value):
        result = _Value(value)
        allocated.append(result)
        return result

    def append(sequence, item):
        item.refs += 1
        sequence.value.append(item)

    def decref(obj):
        obj.refs -= 1
        assert obj.refs >= 0

    def reverse(sequence):
        sequence.value.reverse()

    namespace = {
        "_bytes_data": lambda obj: (obj.value, 0) if isinstance(obj.value, (bytes, bytearray)) else None,
        "py_bytes_len": lambda obj: len(obj.value),
        "ptr_is_null": lambda obj: obj is None, "null": lambda: None,
        "ptr_eq": lambda left, right: left is right,
        "global_load_ptr": lambda name: none,
        "load_i8": lambda ptr, offset: ptr[0][ptr[1] + offset],
        "ptr_add": lambda ptr, offset: (ptr[0], ptr[1] + offset),
        "py_list_new": lambda capacity: new([]), "py_list_append": append,
        "py_list_reverse": reverse, "py_decref": decref,
        "_bytes_new_same_family": lambda src, ptr, size: new(
            type(src.value)(ptr[0][ptr[1]:ptr[1] + size])),
        "py_obj_index_i64": lambda value: value.value, "py_err_occurred": lambda: 0,
        "cstr": lambda text: text, "py_exc_new": lambda tag, message: (tag, message),
        "py_raise_owned": errors.append,
        "_bytes_like_required": lambda obj: errors.append(("TypeError", type(obj.value).__name__)),
    }
    for name in _SPLIT_HELPERS:
        function = runtime_body("py_obj_stubs.py", name, namespace)
    return function, none


@pytest.mark.parametrize("family", [bytes, bytearray])
@pytest.mark.parametrize("from_right", [0, 1])
@pytest.mark.parametrize("data, separator, limit", [
    (b"a b  c\n", None, None), (b"  x  y ", None, 1), (b"  x  y ", None, 0),
    (b"", None, None), (b"\t\x0b\x0c\r\n ", None, None), (b"a\x0bb\x0cc\rd", None, 2),
    (b"a,,b,", b",", None), (b"a,b,c", b",", 0), (b"a,b,c", b",", 1), (b"a,b,c", b",", -3),
    (b"", b",", None), (b"aXXbXXXc", b"XX", None), (b"aXXbXXXc", b"XX", 1),
])
def test_actual_split_body_matches_cpython(family, from_right, data, separator, limit):
    allocated, errors = [], []
    function, none = _split_body(allocated, errors)
    receiver = _Value(family(data))
    sep = none if separator is None else _Value(separator)
    maxsplit = None if limit is None else _Value(limit)
    result = function(receiver, sep, maxsplit, from_right)
    method = family(data).rsplit if from_right else family(data).split
    expected = method(separator) if limit is None else method(separator, limit)
    assert errors == []
    assert [part.value for part in result.value] == expected
    assert all(type(part.value) is family for part in result.value)
    assert receiver.refs == 1 and result.refs == 1 and all(part.refs == 1 for part in result.value)


def test_actual_split_body_rejects_empty_and_non_bytes_separators():
    allocated, errors = [], []
    function, _none = _split_body(allocated, errors)
    assert function(_Value(b"abc"), _Value(b""), None, 0) is None
    assert function(_Value(b"abc"), _Value("b"), None, 1) is None
    assert errors == [(2, "empty separator"), ("TypeError", "str")]
    assert allocated == []


@pytest.mark.parametrize("backend", range(5), ids=lambda backend: f"gc{backend}")
def test_split_and_slice_repr_match_cpython(tmp_path, backend):
    source = tmp_path / "split.py"
    source.write_text(_PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=60)
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / "split"
    compile_python(str(source), str(binary), backend="self")
    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120,
                         env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
    assert (ran.returncode, ran.stderr) == (0, "")
    assert ran.stdout == expected.stdout
