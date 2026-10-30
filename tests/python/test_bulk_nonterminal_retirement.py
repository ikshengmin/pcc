"""Batch only callback-free drops, retaining terminal order and mode guards."""
import ast
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

from pcc.runtime.py import py_abi_constants as abi
from pcc.frontends.python.pipeline import compile_python
from tests.owned_c_execution import compile_owned_c_with_runtime


SHARDS = "pcc_gc_mode_lease_shards"


class Model:
    def __init__(self, *, fast=1, log=0, active=0, switch=False, contend=False, shard=0):
        self.memory = {"pcc_gc_refcount_atomic": fast,
                       "pcc_diagnostics_runtime_log_fast_state": log,
                       "pcc_gc_foreign_lease_active": active}
        self.switch = switch
        self.contend = contend
        self.namespace = {"i64": int, "null": lambda: 0, "ptr_is_null": lambda p: p == 0,
                          "is_tagged_int": lambda p: p & 1, "global_addr": lambda n: n,
                          "ptr_to_int": lambda p: p,
                          "load_ptr": self.load, "load_i32": self.load,
                          "atomic_load_i64": lambda p, o, order: self.load(p, o),
                          "atomic_cas_i64": self.cas,
                          "MODE_LEASE_SHARD_COUNT": 32, "MODE_LEASE_SHARD_STRIDE": 128}
        self.namespace.update({n: getattr(abi, n) for n in dir(abi) if n.isupper()})
        path = Path(__file__).resolve().parents[2] / "pcc/runtime/py/freestanding_gc_root_operations.py"
        names = ("pcc_gc_release_nonterminal_tail", "_mode_lease_shard_index",
                 "_mode_lease_reserve_shard", "_mode_lease_release_shard")
        nodes = [n for n in ast.parse(path.read_text()).body
                 if isinstance(n, ast.FunctionDef) and n.name in names]
        assert len(nodes) == len(names)
        for node in nodes:
            node.decorator_list = []
        code = compile(ast.Module(nodes, type_ignores=[]), str(path), "exec")
        for const in code.co_consts:
            if isinstance(const, types.CodeType) and const.co_name in names:
                self.namespace[const.co_name] = types.FunctionType(const, self.namespace)
        # The owner array lives at 1000 (see run); preset its shard's count.
        self.shard = (SHARDS, self.namespace["_mode_lease_shard_index"](1000) * 128)
        self.memory[self.shard] = shard

    def key(self, pointer, offset):
        if isinstance(pointer, int):
            return pointer + offset
        return pointer if pointer != SHARDS else (pointer, offset)

    def load(self, pointer, offset):
        return self.memory.get(self.key(pointer, offset), 0)

    def cas(self, pointer, offset, expected, value, success, failure):
        key = self.key(pointer, offset)
        if self.contend and isinstance(pointer, int):
            self.memory[key] = 1
            self.contend = False
        old = self.memory.get(key, 0)
        if old == expected:
            self.memory[key] = value
            if self.switch and pointer == SHARDS and value > old:
                self.memory["pcc_gc_refcount_atomic"] = 0
        return old

    def shards_balanced(self, expected=0):
        return all(v == (expected if k == self.shard else 0)
                   for k, v in self.memory.items() if isinstance(k, tuple))

    def object(self, address, count, flags=0, tag=abi.PY_TYPE_LIST):
        self.memory.update({address: count, address + 8: tag, address + 12: flags})
        return address

    def run(self, values):
        for i, value in enumerate(values):
            self.memory[1000 + 8 * i] = value
        return self.namespace["pcc_gc_release_nonterminal_tail"](1000, len(values))


def test_batch_stops_before_terminal_and_preserves_reverse_order():
    m = Model()
    terminal = m.object(2000, 1)
    shared = m.object(3000, 3)
    immortal = m.object(4000, 1, abi.PY_FLAG_IMMORTAL, abi.PY_TYPE_STR)
    assert m.run([terminal, shared, immortal, 3, 0]) == 1
    assert m.memory[terminal] == 1 and m.memory[shared] == 2
    # The owner array's mode-lease shard guards the batch; the central
    # counted-lease word is left to counted leases.
    assert m.memory["pcc_gc_foreign_lease_active"] == 0
    assert m.shards_balanced()


@pytest.mark.parametrize("settings", ({"fast": 0}, {"log": 1}, {"active": -1},
                                      {"shard": (1 << 63) - 1}, {"switch": True}))
def test_unadmitted_modes_do_not_consume_references(settings):
    m = Model(**settings)
    shared = m.object(2000, 3)
    before = m.memory["pcc_gc_foreign_lease_active"]
    assert m.run([shared]) == 1 and m.memory[shared] == 3
    assert m.memory["pcc_gc_foreign_lease_active"] == before
    assert m.shards_balanced(settings.get("shard", 0))


def test_batch_limit_and_cas_loss_leave_a_replayable_prefix():
    m = Model(active=4)
    assert m.run([0] * 130) == 66
    assert m.memory["pcc_gc_foreign_lease_active"] == 4
    assert m.shards_balanced()
    m = Model(contend=True)
    item = m.object(2000, 2)
    assert m.run([item, 3]) == 1 and m.memory[item] == 1
    assert m.memory["pcc_gc_foreign_lease_active"] == 0
    assert m.shards_balanced()


PROBE = r'''
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
extern int32_t pcc_gc_refcount_atomic;
extern int64_t pcc_gc_foreign_lease_count_quiescent(void);
int main(int argc, char **argv) {
    if (argc != 2 || pcc_gc_set_backend(atoi(argv[1]))) return 1;
    PyObject *roots[3] = {NULL, NULL, NULL};
    int32_t map = 3;
    pcc_gc_frame_enter(&map, roots);
    roots[0] = py_list_new(0);
    PyObject *words[130];
    for (int i = 0; i < 130; i++) words[i] = (i & 1) ? py_None : py_int_from_i64(i);
    int64_t active = pcc_gc_foreign_lease_count_quiescent();
    int64_t next = pcc_gc_release_nonterminal_tail(words, 130);
    if (pcc_gc_refcount_atomic) {
        if (next != 66 || pcc_gc_release_nonterminal_tail(words, 66) != 2 ||
            pcc_gc_release_nonterminal_tail(words, 2) != 0) return 2;
    } else if (next != 130) return 3;
    if (pcc_gc_foreign_lease_count_quiescent() != active) return 4;
    py_incref(roots[0]); roots[1] = roots[0];
    py_incref(roots[0]); roots[2] = roots[0];
    next = pcc_gc_release_nonterminal_tail(&roots[1], 2);
    if (pcc_gc_refcount_atomic) {
        if (next != 0) return 5;
        roots[1] = NULL; roots[2] = NULL;
    } else {
        if (next != 2) return 6;
        pcc_gc_store_root(&roots[2], NULL);
        pcc_gc_store_root(&roots[1], NULL);
    }
    if (pcc_gc_release_nonterminal_tail(roots, 1) != 1) return 7;
    pcc_gc_store_root(&roots[0], NULL);
    pcc_gc_frame_leave(roots);
    puts("BULK_RETIREMENT_OK");
    return 0;
}
'''


def test_owned_c_batch_admission_and_balance_five_gc(tmp_path, pcc_runtime_archive):
    source = tmp_path / "batch.c"
    source.write_text(PROBE)
    output = tmp_path / "batch"
    compile_owned_c_with_runtime(source, output, runtime_archive=pcc_runtime_archive)
    for gc in range(5):
        for probe in ("0", "2"):
            result = subprocess.run([str(output), str(gc)], capture_output=True, text=True,
                                    timeout=20, env=dict(os.environ, PATH="/nonexistent",
                                                        PCC_GC_REFCOUNT_PROVENANCE_PROBE=probe))
            assert (result.returncode, result.stdout, result.stderr) == (0, "BULK_RETIREMENT_OK\n", ""), (gc, probe)


PROGRAM = '''import gc
import weakref

events = []
saved = []
class Item:
    def __init__(self, name):
        self.name = name
    def __del__(self):
        events.append(self.name)
        if self.name == "middle":
            saved.append(self)
        gc.collect()

def main():
    held = Item("held")
    marker = weakref.ref(held)
    values = [Item("first"), held, None, 17, "static", Item("middle")]
    for index in range(130):
        values.append(None)
    values.append(Item("last"))
    values = None
    gc.collect()
    assert events == ["last", "middle", "first"]
    assert marker() is held and saved[0].name == "middle"
    pair = (Item("tuple first"), held, None, "static", Item("tuple last"))
    pair = None
    gc.collect()
    assert events[-2:] == ["tuple last", "tuple first"]
    held = None
    saved.clear()
    gc.collect()
    assert marker() is None
    assert events == ["last", "middle", "first", "tuple last", "tuple first", "held"]
    print("ORDER_AND_RESURRECTION_OK")
main()
'''


def test_native_terminal_order_reentry_and_resurrection_five_gc(tmp_path, pcc_runtime_archive):
    source = tmp_path / "lifetime.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=15)
    assert reference.returncode == 0, reference.stderr
    output = tmp_path / "lifetime"
    compile_python(str(source), str(output), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for gc in range(5):
        result = subprocess.run([str(output)], capture_output=True, text=True, timeout=30,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc)))
        assert (result.returncode, result.stdout, result.stderr) == (0, reference.stdout, ""), gc
