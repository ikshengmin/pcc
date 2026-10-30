"""Container retirement agrees with CPython and reloads after callbacks."""

import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python
from pcc.runtime.py import py_abi_constants as abi


@pytest.mark.parametrize("kind", ("list", "tuple"))
@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("count", (0, 1, 3))
def test_retirement_reverses_owners_and_reloads_remaining_children(kind, backend, count):
    owner, array = 1000, 2000
    memory = {owner + 16: count, owner + 24: count, owner + 32: array}
    base = array if kind == "list" else owner + abi.PYTUPLEOBJECT_ITEMS_OFFSET
    for index in range(count):
        memory[base + index * 8] = 100 + index
    retired, frees = [], []
    def release(value):
        retired.append(value)
        if len(retired) == 1:
            # A preceding finalizer can collect/relocate every remaining
            # child. The next iteration must load its current address.
            for index in range(count - 1):
                memory[base + index * 8] += 100
        if kind == "list" and backend == 4:
            assert memory[owner + 16] == 0 and memory[owner + 32] == 0
    ns = {name: getattr(abi, name) for name in dir(abi) if name.isupper()}
    ns.update(load_i64=lambda p, o: memory[p + o], load_ptr=lambda p, o: memory[p + o],
              store_i64=lambda p, o, v: memory.__setitem__(p + o, v),
              store_ptr=lambda p, o, v: memory.__setitem__(p + o, v),
              ptr_add=lambda p, o: p + o, ptr_is_null=lambda p: int(p == 0), null=lambda: 0,
              pcc_gc_backend=lambda: backend, pcc_gc_load_ptr=lambda o, p: memory[p],
              py_decref=release, free=lambda p: frees.append(("array", p)),
              pcc_gc_free_object_memory=lambda p: frees.append(("object", p)))
    source = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_obj_dealloc.py"
    body = [node for node in ast.parse(source.read_text()).body
            if isinstance(node, ast.FunctionDef) and node.name == "py_dealloc_" + kind]
    assert len(body) == 1
    body[0].decorator_list = []
    exec(compile(ast.Module(body=body, type_ignores=[]), str(source), "exec"), ns)
    ns["py_dealloc_" + kind](owner)
    expected = [] if count == 0 else [100 + count - 1] + [200 + i for i in reversed(range(count - 1))]
    assert retired == expected
    assert frees == ([("array", array)] if kind == "list" else []) + [("object", owner)]
    if kind == "list":
        assert memory[owner + 16] == memory[owner + 24] == memory[owner + 32] == 0


PROGRAM = '''import gc
import weakref
events = []
references = []
rescued = []
class Item:
    def __init__(self, name, revive=False):
        self.name = name
        self.revive = revive
        references.append(weakref.ref(self))
    def __del__(self):
        events.append(self.name)
        gc.collect()
        if self.revive:
            rescued.append(self)
def make(name):
    return Item(name)
def bad():
    raise ValueError("selected failure")
def target(*args):
    raise AssertionError("must not be called")
def failed(fn):
    try:
        fn(make("arg-a"), make("arg-b"), bad())
    except ValueError as error:
        assert str(error) == "selected failure"
def failed_star(fn):
    try:
        fn(make("star-a"), *[make("star-b")], bad())
    except ValueError as error:
        assert str(error) == "selected failure"
def failed_many(fn):
    try:
        fn(make("wide-0"), make("wide-1"), make("wide-2"), make("wide-3"),
           make("wide-4"), make("wide-5"), make("wide-6"), make("wide-7"),
           make("wide-8"), bad())
    except ValueError as error:
        assert str(error) == "selected failure"
def ordinary():
    first = [make("list-a"), make("list-b")]
    del first
    second = (make("tuple-a"), make("tuple-b"))
    del second
    nested = [(make("nested-a"), make("nested-b")), [make("nested-c"), make("nested-d")]]
    del nested
    failed(target)
    failed_star(target)
    failed_many(target)
    gc.collect()
    print(events)
    for ref in references:
        assert ref() is None
    references.clear()
def resurrection():
    values = [Item("resurrect-left"), Item("resurrect-right", True)]
    saved_id = id(values[1])
    right = references[-1]
    del values
    gc.collect()
    assert len(rescued) == 1 and id(rescued[0]) == saved_id
    assert right() is rescued[0]
    rescued.clear()
    gc.collect()
    assert right() is None
    print(events[-2:])
ordinary()
resurrection()
print("SEQUENCE_RETIREMENT_OK")
'''


def test_sequence_retirement_reference(tmp_path):
    source = tmp_path / "reference.py"
    source.write_text(PROGRAM)
    run = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=20)
    assert run.returncode == 0 and run.stderr == "", (run.stdout, run.stderr)
    assert "'arg-b', 'arg-a'" in run.stdout
    assert "'star-b', 'star-a'" in run.stdout
    assert "'tuple-b', 'tuple-a'" in run.stdout
    assert run.stdout.endswith("SEQUENCE_RETIREMENT_OK\n")


@pytest.mark.integration
def test_sequence_retirement_native_all_gc(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "sequence_retirement.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=20)
    assert reference.returncode == 0 and reference.stderr == ""
    binary = source.with_suffix("")
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                             env=dict(os.environ, PCC_GC_BACKEND=str(backend),
                                      PCC_GC_MINOR_ALLOC_MAX="4096", PATH="/nonexistent"))
        (tmp_path / ("gc" + str(backend) + ".stdout")).write_text(run.stdout)
        (tmp_path / ("gc" + str(backend) + ".stderr")).write_text(run.stderr)
        assert run.returncode == 0 and run.stderr == "" and run.stdout == reference.stdout, (
            backend, run.returncode, run.stdout, run.stderr, reference.stdout)
