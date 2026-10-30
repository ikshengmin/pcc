"""str repr() escapes exactly the code points CPython's isprintable() rejects.

The runtime carries those code points as a sorted [start, end) range table.
The table is checked against CPython for every code point, the runtime's own
lookup body runs on every range boundary, and a native program prints repr()
and ascii() of non-printable and printable text for comparison with CPython.
"""
from __future__ import annotations

import ast
import bisect
from pathlib import Path
import subprocess
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[2]
STUBS = ROOT / "pcc/runtime/py/py_obj_stubs.py"
_TREE = ast.parse(STUBS.read_text())


def _table() -> list[int]:
    for node in _TREE.body:
        call = getattr(node, "value", None)
        if (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "define_global_i32_array"
                and call.args and getattr(call.args[0], "value", None) == "pcc_unicode_nonprintable_ranges"):
            return [arg.value for arg in call.args[1:]]
    raise AssertionError("range table not found")


def _lookup():
    function = next(node for node in _TREE.body
                    if isinstance(node, ast.FunctionDef) and node.name == "_repr_codepoint_printable")
    module = ast.Module(body=[function], type_ignores=[])
    code = next(const for const in compile(module, str(STUBS), "exec").co_consts
                if isinstance(const, types.CodeType) and const.co_name == function.name)
    values = _table()
    return types.FunctionType(code, {
        "global_addr": lambda name: name,
        "load_i32": lambda table, offset: values[offset // 4],
    }), function


def test_range_table_matches_cpython_for_every_code_point():
    values = _table()
    starts, ends = values[0::2], values[1::2]
    assert len(starts) == len(ends) and starts == sorted(starts)
    assert all(start < end for start, end in zip(starts, ends))
    assert all(end < start for end, start in zip(ends, starts[1:]))
    for code in range(0x110000):
        index = bisect.bisect_right(starts, code) - 1
        rejected = index >= 0 and code < ends[index]
        assert rejected == (not chr(code).isprintable()), hex(code)


def test_runtime_lookup_matches_cpython_on_every_range_boundary():
    lookup, function = _lookup()
    values = _table()
    count = next(node.value.value for node in ast.walk(function)
                 if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "hi")
    assert count == len(values) // 2
    probes = {0, 0x10FFFF}
    for value in values:
        probes.update((value - 1, value, value + 1))
    for code in sorted(c for c in probes if 0 <= c <= 0x10FFFF):
        assert lookup(code) == int(chr(code).isprintable()), hex(code)


PROGRAM = '''
values = [
    "A\\udc80\\udcffZ", "\\ud800", "x\\x85y", "\\xa0", "\\xad", "\\u200b", "\\u2028",
    "\\ue000", "\\U000e0001", "\\U0010ffff", "\\u0378", "caf\\xe9", "\\u65e5\\u672c",
    "\\U0001f642", "q'\\"", "tab\\tnl\\n", "\\x7f\\x00",
]
for value in values:
    print(repr(value), ascii(value))
print(repr(UnicodeEncodeError("utf-8", "A\\udc80\\udcff\\xe9Z", 1, 3, "surrogates not allowed")))
'''


@pytest.mark.integration
def test_native_repr_and_ascii_match_cpython(tmp_path, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "repr_nonprintable.py"
    source.write_text(PROGRAM, encoding="utf-8")
    oracle = subprocess.run([sys.executable, str(source)], capture_output=True, timeout=30)
    assert oracle.returncode == 0, oracle.stderr
    binary = tmp_path / "repr_nonprintable"
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        run = subprocess.run([str(binary)], capture_output=True, timeout=30,
                             env={"PATH": "/nonexistent", "PCC_GC_BACKEND": str(backend)})
        assert (run.returncode, run.stderr) == (0, b""), backend
        assert run.stdout == oracle.stdout, (backend, run.stdout, oracle.stdout)
