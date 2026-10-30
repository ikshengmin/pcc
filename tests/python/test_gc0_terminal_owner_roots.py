"""A parked terminal decref still owns fields until its deallocator runs."""

import os
from pathlib import Path
import subprocess

import pcc
import pytest

from tests.python.test_foreign_address_leases import _functions


@pytest.mark.parametrize("reverse", (False, True))
def test_zero_count_pending_owner_keeps_transitive_fields_alive(reverse):
    objects = (100, 200, 300, 400)
    nodes = {obj: obj + 1000 for obj in objects}
    edges = {100: (200, 200), 200: (300,), 300: (), 400: (400,)}
    counts = {100: 0, 200: 2, 300: 1, 400: 1}
    ordered = list(reversed(objects)) if reverse else list(objects)
    memory = {(obj, 0): counts[obj] for obj in objects}
    for index, obj in enumerate(ordered):
        memory[nodes[obj], 0] = obj
        memory[nodes[obj], 32] = nodes[ordered[index + 1]] if index + 1 < len(ordered) else 0

    def load(base, offset):
        return memory.get((base, offset), 0)

    def store(base, offset, value):
        memory[base, offset] = value

    def subtract(obj):
        for child in edges[obj]:
            node = nodes[child]
            store(node, 8, load(node, 8) - 1)

    def mark(obj):
        node = nodes[obj]
        if load(node, 16):
            return
        store(node, 16, 1)
        for child in edges[obj]:
            mark(child)

    namespace = dict(global_load_ptr=lambda name: nodes[ordered[0]], ptr_is_null=lambda value: value == 0,
                     load_ptr=load, load_i64=load, store_i64=store, store_i32=store,
                     pcc_gc_backend0_visit_subtract=subtract, pcc_gc_backend0_mark_reachable=mark,
                     _mark_runtime_roots=lambda: None)
    source = Path(pcc.__file__).resolve().parent / "runtime/py/freestanding_gc_backend0_collector.py"
    _functions(source, {"_recompute_reachability"}, namespace)
    namespace["_recompute_reachability"]()
    assert load(nodes[200], 16) == 1, "the pending owner still owns both child references"
    assert load(nodes[300], 16) == 1, "the pending owner's descendants must remain alive"
    assert load(nodes[400], 16) == 0, "an unrelated unreachable cycle must remain collectable"


DRIVER = '''
typedef long long i64;
extern void *py_list_new(i64);
extern void py_list_append(void *, void *);
extern void py_decref(void *);
extern i64 py_gc_collect(void);
extern i64 pcc_gc_granule_is_object_start(void *);
extern i64 py_list_len(void *);
extern void *py_gc_index_find(void *);
extern void py_gc_untrack(void *);
i64 terminal_owner_probe(void) {
    void *parent = py_list_new(1);
    void *child = py_list_new(1);
    if (!parent || !child) return 10;
    py_list_append(child, child);
    py_list_append(parent, child);
    py_decref(child);
    if (!py_gc_index_find(parent) || !py_gc_index_find(child)) return 11;
    /* This is the real parked-decref boundary: rc reached zero, but untrack
       and field disposal have not run. The raw C local is deliberately not
       a compiler-registered root and does not contribute a reference. */
    __atomic_store_n((i64 *)parent, 0, 3);
    i64 collected = py_gc_collect();
    if (collected != 0) return 20;
    if (pcc_gc_granule_is_object_start(child) != 1) return 21;
    if (py_list_len(child) != 1) return 22;
    /* Resume the last decrement. The now-unowned child cycle must die. */
    __atomic_store_n((i64 *)parent, 1, 3);
    py_decref(parent);
    if (py_gc_collect() != 1) return 23;
    if (pcc_gc_granule_is_object_start(child) == 1) return 24;
    return 0;
}
'''


def compile_terminal_owner_probe(tmp_path, archive):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.python.test_owned_external_tls import _c_ir

    target = host_target_triple()
    source = tmp_path / "terminal_owner.c"
    source.write_text(DRIVER)
    ir = _c_ir(DRIVER, target)
    (tmp_path / "terminal_owner.ll").write_text(ir)
    obj = tmp_path / "terminal_owner.o"
    obj.write_bytes(emit_owned_object(ir, target))
    wrapper = tmp_path / "driver.py"
    wrapper.write_text('from pcc.extern import extern, c_int64\n'
                       'probe = extern("terminal_owner_probe", (), c_int64)\n'
                       'def main():\n    print(probe())\nmain()\n')
    binary = tmp_path / "driver"
    compile_python(str(wrapper), str(binary), backend="self", libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(archive), object_inputs=(str(obj),))
    return binary


@pytest.mark.integration
def test_pending_zero_count_owner_native_owned_c(tmp_path, threaded_pcc_runtime_archive):
    binary = compile_terminal_owner_probe(tmp_path, threaded_pcc_runtime_archive)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=25,
                            env=dict(os.environ, PCC_GC_BACKEND="0", PATH="/nonexistent"))
    (tmp_path / "native.stdout").write_text(result.stdout)
    (tmp_path / "native.stderr").write_text(result.stderr)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert result.stdout == "0\n"
