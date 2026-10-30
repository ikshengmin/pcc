"""A module's namespace is found without a dict lookup once it was found.

Every module global read and module attribute read found the module's
namespace dictionary by name in the shared module cache: a graph-locked dict
lookup with a retain and release, before the attribute lookup itself. A
namespace is registered once, never replaced or removed, and pinned, so the
threaded name-key cache entry of the module's C name publishes it (one CAS
from empty) and later lookups read it with one acquire load. Attribute values
are still read from the namespace each time, so rebinding stays visible.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

from pcc.frontends.python.pipeline import compile_python

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime/py/py_module_attrs_runtime.py"


class Words:
    def __init__(self):
        self.words = {}

    def load(self, base, offset, order=None):
        return self.words.get((base, offset), 0)

    def cas(self, base, offset, expected, value, success, failure):
        current = self.words.get((base, offset), 0)
        if current == expected:
            self.words[(base, offset)] = value
        return current


def _finder(threads, entries, namespaces):
    words = Words()
    calls = []
    tree = ast.parse(RUNTIME.read_text())
    consts = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.startswith("_NAME_CACHE_")):
            assert isinstance(node.value, ast.Constant)
            consts[node.targets[0].id] = node.value.value
    names = ("_module_find", "_threaded_name_entry")
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert sorted(node.name for node in nodes) == sorted(names)

    def dict_get(cache, key):
        calls.append(("dict_get", key))
        return namespaces.get(key, 0)

    handles = {}
    objects = {}

    def ptr_to_int(value):
        if value not in handles:
            handles[value] = (len(handles) + 1) * 8
            objects[handles[value]] = value
        return handles[value]

    namespace = dict(consts)
    namespace.update({
        "C_POINTER_SIZE": 8,
        "pcc_threads_enabled": lambda: threads,
        "_module_cache": lambda create: "cache",
        "_cached_name_key": lambda text: ("key", text),
        "_module_name_key": lambda text: calls.append(("fresh_key", text)) or ("fresh", text),
        "py_dict_get": dict_get,
        "py_decref": lambda obj: calls.append(("decref", obj)),
        "ptr_is_null": lambda pointer: pointer in (0, None),
        "global_addr": lambda name: name,
        "atomic_load_i64": words.load,
        "atomic_cas_i64": words.cas,
        "int_to_ptr": lambda value: objects[value],
        "ptr_to_int": ptr_to_int,
        "null": lambda: 0,
        "_threaded_name_key_match": lambda entry, text: ("key", text) if entries.get(text) == entry else 0,
    })
    # The table and every entry are handles; each text owns its own slot.
    words.words[("pcc_threaded_name_key_cache_bits", 0)] = ptr_to_int("table")
    for text, entry in entries.items():
        slot = ((ptr_to_int(text) >> 3) & (consts["_NAME_CACHE_CAPACITY"] - 1)) * 8
        words.words[("table", slot)] = ptr_to_int(entry)
    code = compile(ast.Module(nodes, type_ignores=[]), str(RUNTIME), "exec")
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            namespace[const.co_name] = types.FunctionType(const, namespace)
    return namespace, calls, words


def test_a_found_namespace_is_published_and_read_back_without_the_dict():
    ns, calls, words = _finder(1, {"helper": "entry"}, {("key", "helper"): "attrs"})
    assert ns["_module_find"]("helper") == "attrs"
    assert calls == [("dict_get", ("key", "helper")), ("decref", "attrs")]
    calls.clear()
    assert ns["_module_find"]("helper") == "attrs"
    assert calls == []


def test_a_missing_module_publishes_nothing():
    ns, calls, words = _finder(1, {"absent": "entry"}, {})
    assert ns["_module_find"]("absent") == 0
    assert ns["_module_find"]("absent") == 0
    assert [call[0] for call in calls] == ["dict_get", "dict_get"]


def test_without_an_entry_or_threads_the_dict_answers_each_time():
    ns, calls, words = _finder(1, {}, {("key", "helper"): "attrs"})
    assert ns["_module_find"]("helper") == "attrs"
    assert ns["_module_find"]("helper") == "attrs"
    assert [call[0] for call in calls] == ["dict_get", "decref", "dict_get", "decref"]
    ns, calls, words = _finder(0, {"helper": "entry"}, {("key", "helper"): "attrs"})
    assert ns["_module_find"]("helper") == "attrs"
    assert ns["_module_find"]("helper") == "attrs"
    assert [call[0] for call in calls] == ["dict_get", "decref", "dict_get", "decref"]


HELPER = r'''
mode = "a"
LIMIT = 50


def scale(value):
    return value * 3


def rebind(value):
    global mode
    mode = value
'''

MAIN = r'''
import pcc.virtual_thread as vt
import nscache_helper


def reader(seed):
    total = 0
    step = 0
    while step < 2000:
        total += nscache_helper.scale(seed) + nscache_helper.LIMIT
        if nscache_helper.mode not in ("a", "b"):
            total = -1000000
        if step % 100 == 0:
            vt.yield_now()
        step += 1
    return total


def main():
    threads = [vt.spawn(reader, seed) for seed in range(6)]
    vt.run(4, 100000000)
    print([vt.result(thread) for thread in threads])
    nscache_helper.rebind("b")
    print(nscache_helper.mode, nscache_helper.scale(7))


main()
'''


@pytest.mark.integration
def test_module_reads_on_four_carriers_and_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    (tmp_path / "nscache_helper.py").write_text(HELPER, encoding="utf-8")
    source = tmp_path / "nscache_main.py"
    source.write_text(MAIN, encoding="utf-8")
    executable = tmp_path / "nscache_main"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    expected = str([2000 * (seed * 3 + 50) for seed in range(6)]) + "\nb 21\n"
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                             cwd=str(tmp_path),
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected, ""), backend
