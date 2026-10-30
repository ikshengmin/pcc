"""Ordinary entry-root groups retain per-cell roles, exits and Python lifetimes."""
import os
import re
import subprocess
import sys

import pytest

from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.frontends.python.pipeline import compile_python
from tests.python.root_slot_contract import RootSlotContract
from tests.python.test_shared_call_binding import _emit, _function


@pytest.mark.parametrize("count", [1, 2, 15, 16, 17, 32, 33])
def test_borrowed_parameter_groups_cover_distinct_cells_and_all_exits(count):
    names = ["a" + str(index) for index in range(count)]
    text = _emit("def probe(" + ", ".join(names) + "):\n"
                 "    if a0 is None:\n        return None\n    return a0\n")
    body = _function(text)
    module = parse_self_backend_module(text)
    function = next(fn for fn in module.functions if fn.name == "user_binding_probe")
    roots = RootSlotContract(get_indexed_function_kernel(function).materialize_legacy_blocks(function),
                             module.globals_)
    groups = [name for name in roots.allocas if name.startswith("fn.roots.borrowed.")]
    assert len(groups) == (0 if count == 1 else (count + 15) // 16)
    addresses = [next(name for name in roots.geps if name.startswith(param + ".addr."))
                 for param in names] if count > 1 else [next(name for name in roots.allocas if name.startswith("a0.addr."))]
    cells = [roots.slot(address) for address in addresses]
    assert len(set(cells)) == count
    assert len(cells) == count
    if groups:
        assert {base for base, offset in cells} == set(groups)
        for base in groups:
            capacity = roots._pointer_cells(base)
            assert capacity == min(count, 16)
        assert re.search(r"@\.pcc.gc.frame.map.fn.borrowed.[^ ]+ = internal constant i32 -"
                         + str(min(count, 16)), text)
    assert roots.assert_frame_exits(groups)
    assert "@pcc_gc_load_borrowed_ptr(" in body


@pytest.mark.parametrize("count", [1, 8, 9, 17])
def test_late_owned_groups_balance_early_and_shared_exits(count):
    text = _emit("def probe(a, b):\n    if a is None:\n        return b\n"
                 + "".join("    value = [a, b]\n" for _ in range(count))
                 + "    return value\n")
    module = parse_self_backend_module(text)
    function = next(fn for fn in module.functions if fn.name == "user_binding_probe")
    roots = RootSlotContract(get_indexed_function_kernel(function).materialize_legacy_blocks(function),
                             module.globals_)
    groups = [name for name in roots.allocas if name.startswith("fn.roots.owned.")]
    assert groups
    for base in groups:
        assert roots.require_owning((base, 0)) == (base, 0, 8)
    all_groups = groups + [name for name in roots.allocas if name.startswith("fn.roots.borrowed.")]
    assert len(roots.assert_frame_exits(all_groups)) >= 2


PROGRAM = r'''import gc
import weakref

events = []
saved = []
class Box:
    def __init__(self, value):
        self.value = value
    def __del__(self):
        events.append(self.value)
        if self.value == 99:
            saved.append(self)

def many(a,b,c,d,e,f,g,h,i,j,k,l,m,n,o,p,q,mode):
    assert a is q and b is p
    assert not isinstance(a, str)
    assert not isinstance(b, list)
    gc.collect()
    assert q.value == 7 and p.value == 8
    if mode == 0:
        return a
    assert not isinstance(a, dict)
    assert not isinstance(b, tuple)
    a = Box(40)
    assert isinstance(a, type(q))
    b = None
    if mode == 1:
        raise ValueError("expected")
    # Persistent construction roots are independently cleared on completion.
    values = [Box(10), Box(11), {"nested": Box(12)}, (q, p)]
    gc.collect()
    assert values[3][0] is q
    return values

def closure(a, b):
    def read():
        return a
    a = b
    gc.collect()
    return read()

def resurrection(a, b):
    value = [Box(99)]
    value.clear()
    gc.collect()
    assert len(saved) == 1 and saved[0].value == 99
    return a is b

def main():
    first = Box(7)
    second = Box(8)
    ref = weakref.ref(first)
    result = many(first,second,None,None,None,None,None,None,None,None,None,None,None,None,None,second,first,0)
    assert result is first and events == []
    result = None
    try:
        many(first,second,None,None,None,None,None,None,None,None,None,None,None,None,None,second,first,1)
    except ValueError as error:
        assert str(error) == "expected"
    else:
        assert False
    gc.collect()
    assert events == [40] and ref() is first
    result = many(first,second,None,None,None,None,None,None,None,None,None,None,None,None,None,second,first,2)
    gc.collect()
    assert events == [40, 40]
    result = None
    gc.collect()
    assert sorted(events) == [10, 11, 12, 40, 40]
    assert closure(first, second) is second
    assert resurrection(first, first)
    saved.clear()
    first = None
    second = None
    gc.collect()
    assert ref() is None
    assert sorted(events) == [7, 8, 10, 11, 12, 40, 40, 99]
    print("FUNCTION_ROOT_GROUPS_OK")
main()
'''


@pytest.mark.parametrize("tables", ["0", "1"])
def test_function_root_groups_native_all_gc(tmp_path, pcc_runtime_archive, monkeypatch, tables):
    source = tmp_path / "function_groups.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=20)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, "FUNCTION_ROOT_GROUPS_OK\n", "")
    monkeypatch.setenv("PCC_PRECISE_STACKMAP_TABLES", tables)
    output = tmp_path / "function_groups"
    dump = tmp_path / "ir"
    dump.mkdir()
    monkeypatch.setenv("PCC_DEBUG_SELF_IR_DUMP_DIR", str(dump))
    compile_python(str(source), str(output), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    emitted = "\n".join(path.read_text() for path in dump.glob("*.ll"))
    grouped_roles = {role: "fn.roots." + role + "." in emitted
                     for role in ("borrowed", "owned")}
    assert all(grouped_roles.values()), grouped_roles
    for gc in range(5):
        env = dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc),
                   PCC_GC_MINOR_HEAP_SIZE="4096", PCC_GC_MINOR_ALLOC_MAX="128")
        result = subprocess.run([str(output)], capture_output=True, text=True, timeout=30, env=env)
        (tmp_path / (str(gc) + ".stdout")).write_text(result.stdout)
        (tmp_path / (str(gc) + ".stderr")).write_text(result.stderr)
        assert (result.returncode, result.stdout, result.stderr) == (0, reference.stdout, ""), gc
