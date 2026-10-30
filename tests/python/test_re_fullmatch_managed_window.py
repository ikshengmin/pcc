"""Actual managed-window adapter + actual raw core under a checked host model."""
import ast
from pathlib import Path
import re

import pytest
from tests.python.test_re_engine_fullmatch_core import CoreMemory

SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_re_match_runtime.py"


def run_window(pattern, text, start, endpos):
    core = CoreMemory()
    slots = {16: pattern, 24: text}
    result = {"error": None, "spans": None, "none": False}
    none = object()

    def construct(slots, tokens, caps, groups, flags):
        result["spans"] = [(core.read(caps, index * 16, 8, True), core.read(caps, index * 16 + 8, 8, True)) for index in range(groups + 1)]
        return 1

    def cstrlen(pointer):
        length = 0
        while core.read(pointer, length, 1):
            length += 1
        return length

    def write_slot(slots, offset, value):
        slots[offset] = value
        if offset == 0 and value is none:
            result["none"] = True

    ns = {
        "c_ptr": int,
        "load_ptr": lambda slots, offset: slots[offset],
        "store_ptr": write_slot,
        "load_i64": lambda pointer, offset: core.read(pointer, offset, 8, True),
        "store_i64": lambda pointer, offset, value: core.write(pointer, offset, value, 8),
        "load_i8": lambda pointer, offset: core.read(pointer, offset, 1, True),
        "py_str_check": lambda value: int(isinstance(value, str)),
        "py_str_utf8": core.raw,
        "py_str_byte_len": lambda value: len(value.encode()),
        "strlen": cstrlen,
        "cstr": lambda value: value,
        "stack_alloc": core.stack_alloc,
        "pcc_re_engine_fullmatch_flags": core.namespace["pcc_re_engine_fullmatch_flags"],
        "_re_construct": construct,
        "_re_adopt": lambda *args: 1,
        "global_load_ptr": lambda name: none,
        "_re_error": lambda slots, tokens, kind, message: result.update(error=(kind, message)),
    }
    nodes = []
    for node in ast.parse(SOURCE.read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id.startswith("_RE_") for target in node.targets):
            nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in {"_re_bytes_ascii", "_re_run_fullmatch"}:
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), ns)
    ns["_re_run_fullmatch"](slots, {}, 0, start, endpos)
    return result


@pytest.mark.parametrize("pattern,text,pos,endpos", [
    ("", "abc", 0, 0), ("", "abc", 3, 1), ("", "abc", 99, 99),
    ("", "abc", -99, -1), ("", "abc", 1, -99),
    ("(a|ab)", "xaby", 1, 3), ("a*?", "aaa", -99, 99),
    ("(a.*)", "a\0b", 0, 99), ("a.b", "xa\0by", 1, 4),
    ("^ab", "xaby", 1, 3), ("ab$", "xaby", 1, 3),
    ("a$", "a\n", 0, 99), ("(a)?b", "b", 0, 1),
])
def test_effective_windows_match_cpython_without_losing_original_offsets(pattern, text, pos, endpos):
    expected = re.compile(pattern).fullmatch(text, pos, endpos)
    actual = run_window(pattern, text, pos, endpos)
    assert actual["error"] is None
    if expected is None:
        assert actual["none"] and actual["spans"] is None
    else:
        assert actual["spans"] == [expected.span(group) for group in range(expected.re.groups + 1)]


@pytest.mark.parametrize("pattern,text,pos,endpos", [
    ("(.)", "é", 0, 1), ("(.)", "中", 0, 1), ("(.)", "😀", 0, 1),
    ("a", "aé", 0, 1), ("", "é", 0, 0), ("é", "a", 0, 1),
])
def test_multibyte_input_is_explicitly_unsupported_before_byte_bounds(pattern, text, pos, endpos):
    actual = run_window(pattern, text, pos, endpos)
    assert actual["error"][0] == 11
    assert "non-ASCII" in actual["error"][1]
    assert actual["spans"] is None and not actual["none"]


def test_embedded_nul_pattern_is_not_silently_truncated():
    actual = run_window("a\0b", "a", 0, 1)
    assert actual["error"][0] == 11 and "embedded-NUL" in actual["error"][1]
