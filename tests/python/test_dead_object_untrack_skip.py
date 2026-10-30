"""A dying object untracks only when the collector tracks it.

_py_decref_finish called py_gc_untrack for every death, and untrack takes
the object's tracked-shard lock before it reads the TRACKED flag. Of about
42 deaths per request in the native benchmark, 13 were never tracked, so
each paid a lock round trip on a shard line every carrier touches. The
count that reached zero is acquire-release, and nothing can track an object
nobody else references, so the finish reads the flag itself and leaves the
untracked ones alone. Instances still untrack in their own deallocator.
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
from pcc.runtime.py import py_abi_constants as abi


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_obj.py"


def finish(flags, tag=abi.PY_TYPE_LIST):
    events = []
    obj = {abi.PYOBJECTHEADER_FLAGS_OFFSET: flags}
    tree = ast.parse(RUNTIME.read_text())
    node = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "_py_decref_finish")
    namespace = {name: getattr(abi, name) for name in dir(abi) if name.isupper()}
    namespace.update({
        "GC_FREE_WATCH_BYTES": 24,
        "global_addr": lambda name: name,
        "load_i32": lambda base, offset: 0 if isinstance(base, str) else base[offset],
        "load_i64": lambda base, offset: base[offset],
        "load_ptr": lambda base, offset: base[offset],
        "stack_alloc": lambda size: {},
        "pcc_refcount_forget": lambda o: events.append("forget"),
        "py_weakref_invalidate": lambda o: events.append("weakrefs"),
        "pcc_gc_note_object_freeing": lambda o: events.append("note"),
        "py_gc_untrack": lambda o: events.append("untrack"),
        "pcc_dealloc_with_trash": lambda o, tag: events.append("dealloc"),
        "pcc_capi_is_cext_type_tag": lambda tag: 0,
        "py_gen_finalize_from_dealloc": lambda o: 0,
    })
    code = compile(ast.Module([node], type_ignores=[]), str(RUNTIME), "exec")
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            namespace[const.co_name] = types.FunctionType(const, namespace)
    prepared = {0: obj, 8: tag, 16: flags, 24: 0, 32: 0, 40: 1, 48: 0}
    namespace["_py_decref_finish"](prepared)
    return events


def test_a_tracked_death_untracks_before_its_deallocator():
    assert finish(abi.PY_FLAG_GC_TRACKED) == ["forget", "weakrefs", "note", "untrack", "dealloc"]


def test_an_untracked_death_skips_the_tracked_shard():
    assert finish(0) == ["forget", "weakrefs", "note", "dealloc"]


def test_the_flag_is_read_at_death_not_from_the_prepared_snapshot():
    # Another owner may have tracked the object (a dict gaining a container
    # value) after this owner's snapshot and before its own release.
    events = []
    obj = {abi.PYOBJECTHEADER_FLAGS_OFFSET: abi.PY_FLAG_GC_TRACKED}
    tree = ast.parse(RUNTIME.read_text())
    node = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "_py_decref_finish")
    namespace = {name: getattr(abi, name) for name in dir(abi) if name.isupper()}
    namespace.update({
        "GC_FREE_WATCH_BYTES": 24, "global_addr": lambda name: name,
        "load_i32": lambda base, offset: 0 if isinstance(base, str) else base[offset],
        "load_i64": lambda base, offset: base[offset], "load_ptr": lambda base, offset: base[offset],
        "stack_alloc": lambda size: {}, "pcc_refcount_forget": lambda o: None,
        "py_weakref_invalidate": lambda o: None, "pcc_gc_note_object_freeing": lambda o: None,
        "py_gc_untrack": lambda o: events.append("untrack"),
        "pcc_dealloc_with_trash": lambda o, tag: None, "pcc_capi_is_cext_type_tag": lambda tag: 0,
        "py_gen_finalize_from_dealloc": lambda o: 0,
    })
    code = compile(ast.Module([node], type_ignores=[]), str(RUNTIME), "exec")
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            namespace[const.co_name] = types.FunctionType(const, namespace)
    namespace["_py_decref_finish"]({0: obj, 8: abi.PY_TYPE_DICT, 16: 0, 24: 0, 32: 0, 40: 1, 48: 0})
    assert events == ["untrack"]


@pytest.mark.parametrize("tag", (abi.PY_TYPE_INSTANCE, abi.PY_TYPE_USER_CLASS_START))
def test_instances_leave_untracking_to_their_deallocator(tag):
    assert "untrack" not in finish(abi.PY_FLAG_GC_TRACKED, tag)


PROGRAM = r'''
import gc
import threading


class Node:
    def __init__(self, value, peer):
        self.value = value
        self.peer = peer

    def __del__(self):
        DEATHS.append(self.value)


DEATHS = []


def churn(seed, out):
    total = 0
    index = 0
    while index < 2000:
        flat = (index, seed, "x")
        nested = [flat, {"k": [index]}, (index, [seed])]
        table = {"n": index}
        table["box"] = [table]
        # Finalized by their last release on every collector; the cyclic
        # table above dies through the collector's tracked set.
        left = Node(index, Node(-index, None))
        total += len(nested) + flat[0] + len(table)
        index += 1
    out.append((seed, total))


def main():
    out = []
    threads = [threading.Thread(target=churn, args=(seed, out)) for seed in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    gc.collect()
    gc.collect()
    print(sorted(out), len(DEATHS), sum(DEATHS))


main()
'''


@pytest.mark.integration
def test_tracked_and_untracked_deaths_match_cpython_on_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "deaths.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=120)
    assert expected.returncode == 0, expected.stderr
    executable = tmp_path / "deaths"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected.stdout, ""), backend
