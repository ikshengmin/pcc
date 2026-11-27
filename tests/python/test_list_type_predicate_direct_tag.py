"""List, tuple and dict operations read their operand's type tag without the
provenance probe.

``_ptr_is_list`` asked ``pcc_gc_pointer_is_managed`` (a granule lookup)
before reading the tag of every list operation's operand: 45 of a
list.append's 412 instructions and of a list read's 284 in the handler
(docs/status.md, item 3). The operand is a value the caller holds, so it is
NULL, a tagged integer or an object whose header is readable; once the
debug cache is known off (0), the tag is read directly, as
``_gc0_owner_fast_path``'s owners and ``pcc_gc_store_root`` do.
PCC_DEBUG_RUNTIME (cache 1) and the unknown cache (-1) keep the probe.
py_tuple and py_dict decide their header question the same way (dict.items()
asked it twice per new pair). The program runs list, tuple and dict
operation families natively.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import types

import pytest

from pcc.runtime.py import py_abi_constants as abi


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_list.py"


def _predicates(debug_cache, managed):
    """``_ptr_is_list``, ``_list_is_sane`` and ``_list_type_ok`` over word
    memory: objects are dicts keyed by offset, a tagged integer is an odd
    int, NULL is None."""
    probes = []
    bad = []

    def load(base, offset):
        return base[offset]

    def is_managed(value):
        probes.append(value)
        return managed

    namespace = {name: getattr(abi, name) for name in dir(abi) if name.isupper()}
    namespace.update({
        "load_i32": lambda base, offset: debug_cache if base == "cache" else load(base, offset),
        "load_i64": load, "load_ptr": load,
        "global_addr": lambda name: "cache" if name == "pcc_debug_runtime_enabled_cache" else name,
        "ptr_is_null": lambda value: int(value is None),
        "is_tagged_int": lambda value: int(isinstance(value, int) and value & 1 == 1),
        "pcc_gc_pointer_is_managed": is_managed,
        "_debug_bad_container": lambda value, code: bad.append(code),
    })
    tree = ast.parse(RUNTIME.read_text())
    wanted = ("_ptr_can_have_header", "_ptr_is_list", "_list_is_sane", "_list_type_ok")
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    assert len(nodes) == len(wanted)
    code = compile(ast.Module(nodes, type_ignores=[]), str(RUNTIME), "exec")
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            namespace[const.co_name] = types.FunctionType(const, namespace)
    return namespace, probes, bad


def _object(tag, length=2, capacity=4, items="items"):
    return {abi.PYOBJECTHEADER_TYPE_TAG_OFFSET: tag,
            abi.PYLISTOBJECT_LENGTH_OFFSET: length,
            abi.PYLISTOBJECT_CAPACITY_OFFSET: capacity,
            abi.PYLISTOBJECT_ITEMS_OFFSET: items}


def test_a_known_off_debug_cache_reads_the_tag_without_the_probe():
    namespace, probes, bad = _predicates(debug_cache=0, managed=0)
    assert namespace["_ptr_is_list"](_object(abi.PY_TYPE_LIST)) is True
    assert namespace["_ptr_is_list"](_object(abi.PY_TYPE_DICT)) is False
    assert namespace["_ptr_is_list"](None) is False
    assert namespace["_ptr_is_list"](17) is False
    assert namespace["_list_is_sane"](_object(abi.PY_TYPE_LIST), -101) is True
    assert namespace["_list_type_ok"](_object(abi.PY_TYPE_LIST), -103) is True
    assert probes == [] and bad == []


@pytest.mark.parametrize("debug_cache", (-1, 1))
def test_an_unknown_or_on_debug_cache_keeps_the_probe(debug_cache):
    namespace, probes, bad = _predicates(debug_cache=debug_cache, managed=0)
    listed = _object(abi.PY_TYPE_LIST)
    # The probe refuses a pointer it does not know, whatever its tag says.
    assert namespace["_ptr_is_list"](listed) is False
    assert namespace["_list_is_sane"](listed, -101) is False
    assert probes == [listed, listed] and bad == [-101]
    namespace, probes, bad = _predicates(debug_cache=debug_cache, managed=1)
    assert namespace["_list_is_sane"](listed, -101) is True
    assert probes == [listed] and bad == []


def _module_predicate(module, predicate, debug_cache, managed):
    probes = []
    path = Path(__file__).resolve().parents[2] / "pcc/runtime/py" / module
    namespace = {name: getattr(abi, name) for name in dir(abi) if name.isupper()}
    namespace.update({
        "load_i32": lambda base, offset: debug_cache if base == "cache" else base[offset],
        "global_addr": lambda name: "cache" if name == "pcc_debug_runtime_enabled_cache" else name,
        "ptr_is_null": lambda value: int(value is None),
        "is_tagged_int": lambda value: int(isinstance(value, int) and value & 1 == 1),
        "pcc_gc_pointer_is_managed": lambda value: probes.append(value) or managed,
    })
    tree = ast.parse(path.read_text())
    wanted = ("_ptr_can_have_header", predicate)
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    assert len(nodes) == 2
    code = compile(ast.Module(nodes, type_ignores=[]), str(path), "exec")
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            namespace[const.co_name] = types.FunctionType(const, namespace)
    return namespace[predicate], probes


@pytest.mark.parametrize("module,predicate,tag", (
    ("py_list.py", "_ptr_is_list", abi.PY_TYPE_LIST),
    ("py_tuple.py", "_ptr_is_tuple", abi.PY_TYPE_TUPLE),
    ("py_dict.py", "_ptr_is_dict", abi.PY_TYPE_DICT),
))
def test_tuple_and_dict_predicates_share_the_gate(module, predicate, tag):
    other = abi.PY_TYPE_STR
    is_kind, probes = _module_predicate(module, predicate, debug_cache=0, managed=0)
    assert is_kind({abi.PYOBJECTHEADER_TYPE_TAG_OFFSET: tag}) is True
    assert is_kind({abi.PYOBJECTHEADER_TYPE_TAG_OFFSET: other}) is False
    assert is_kind(None) is False and is_kind(17) is False
    assert probes == []
    for cache in (-1, 1):
        is_kind, probes = _module_predicate(module, predicate, debug_cache=cache, managed=0)
        value = {abi.PYOBJECTHEADER_TYPE_TAG_OFFSET: tag}
        assert is_kind(value) is False and probes == [value]


@pytest.mark.parametrize("change", ("tag", "tagged", "length", "capacity", "items"))
def test_a_receiver_that_is_not_a_sane_list_is_still_reported(change):
    namespace, probes, bad = _predicates(debug_cache=0, managed=1)
    value = {
        "tag": _object(abi.PY_TYPE_DICT),
        "tagged": 17,
        "length": _object(abi.PY_TYPE_LIST, length=-1),
        "capacity": _object(abi.PY_TYPE_LIST, length=5, capacity=4),
        "items": _object(abi.PY_TYPE_LIST, items=None),
    }[change]
    assert namespace["_list_is_sane"](value, -101) is False
    assert bad == [-101] and probes == []
    # NULL is refused without a report, as before.
    assert namespace["_list_is_sane"](None, -101) is False
    assert bad == [-101]


PROGRAM = r'''
class Box:
    def __init__(self, v):
        self.v = v


def main():
    parts: list[str] = []
    for i in range(40):
        parts.append(str(i))
    boxes = []
    for i in range(30):
        boxes.append(Box(i))
    mixed = [None, 3, "s", 2.5, Box(7), (1, 2), [4]]
    total = 0
    for b in boxes:
        total += b.v
    pairs = sorted({"b": 2, "a": 1, "c": 3}.items())
    copy = parts[:]
    copy.extend(["x", "y"])
    copy.insert(0, "first")
    last = copy.pop()
    copy.reverse()
    copy.remove("7")
    print(len(parts), parts[0], parts[-1], "".join(parts[:5]))
    print(total, len(mixed), mixed[4].v, mixed[5], mixed[6], pairs)
    print(len(copy), copy[0], copy[-1], last, copy.index("3"), "3" in copy, copy.count("1"))
    print(parts == [str(i) for i in range(40)], sum(len(p) for p in parts), max(parts))
    table = {}
    for i in range(20):
        table["k" + str(i)] = (i, str(i))
    del table["k3"]
    merged = tuple(parts[:3]) + (None, 4)
    first, *rest = merged
    keys = [k for k in table if table[k][0] % 5 == 0]
    print(len(table), table.get("k4"), table.get("k3", "gone"), "k7" in table, keys)
    print(merged, first, rest, len(merged), merged.index(None), 4 in merged, merged[1:3])


main()
'''

EXPECTED = (
    "40 0 39 01234\n"
    "435 7 7 (1, 2) [4] [('a', 1), ('b', 2), ('c', 3)]\n"
    "41 x first y 36 True 1\n"
    "True 70 9\n"
    "19 (4, '4') gone True ['k0', 'k5', 'k10', 'k15']\n"
    "('0', '1', '2', None, 4) 0 ['1', '2', None, 4] 5 3 True ('1', '2')\n"
)


# Native execution: the owned runtime archive with this py_list.
@pytest.mark.integration
def test_list_operations_run_natively(tmp_path, threaded_pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python
    source = tmp_path / "list_ops.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "list_ops"
    compile_python(str(source), str(executable), backend="self", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                         env=dict(os.environ, PATH="/nonexistent"))
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, EXPECTED, "")
