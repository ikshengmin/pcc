"""Heap classes and their instance layouts retire without ordinary refcounts."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("tracing", [0, 1])
def test_permanent_raw_class_publishes_a_stable_traced_owner(tracing):
    from pcc.runtime.py import py_abi_constants as abi
    from test_gc4_conservative_root_seeding import body

    memory = {}
    globals_ = {"py_object_root_cache": 0, "pcc_gc_backend4_no_refcounts": tracing}
    allocations = []
    indexed = []

    def malloc(size):
        address = 4096 + len(allocations) * 4096
        allocations.append((address, size))
        return address

    def store(base, offset, value):
        memory[base + offset] = value

    def indexed_owner(owner, size):
        flags = memory[owner + abi.PYOBJECTHEADER_FLAGS_OFFSET]
        assert flags & abi.PY_FLAG_IMMORTAL and flags & abi.PY_FLAG_GC_PINNED
        mro = memory[owner + abi.PYCLASSOBJECT_MRO_OFFSET]
        assert memory[mro] == owner
        indexed.append((owner, size))

    namespace = dict(vars(abi))
    namespace.update(
        global_load_ptr=lambda name: globals_[name],
        global_store_ptr=lambda name, value: globals_.__setitem__(name, value),
        global_addr=lambda name: name,
        load_i32=lambda base, offset: globals_[base] if isinstance(base, str) else memory[base + offset],
        store_ptr=store, store_i32=store, store_i64=store,
        null=lambda: 0, ptr_is_null=lambda value: value == 0,
        malloc=malloc, memset=lambda *args: None,
        pcc_gc_pointer_register=lambda value: 0,
        pcc_gc_note_object_allocated_sized=indexed_owner,
    )
    constructor = body("py_substrate.py", "py_subs_object_root", namespace)
    root = constructor()
    assert constructor() == root
    assert len(allocations) == 2
    assert indexed == ([(root, abi.PYCLASSOBJECT_SIZE)] if tracing else [])


HARNESS = r'''
extern void *py_class_new(const char *, void **, int, const char **, int);
extern void *py_instance_new(void *);
extern void py_instance_set_field(void *, int, void *);
extern void py_decref(void *);
extern void *py_weakref_new(void *, void *);
extern void *py_weakref_call(void *);
extern void *py_None;
extern long pcc_gc_backend(void);
extern void pcc_gc_store_root(void **, void *);
extern void pcc_gc_scheduler_root_register(void **);
extern void pcc_gc_scheduler_root_unregister(void **);
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern void *pcc_gc_object_list_head(void);
extern void *pcc_gc_object_node_next(void *);
extern long pcc_gc_object_node_is_active(void *);
long count_fixture_classes(void);
static void *weak_roots[3];

long make_class_graph(void) {
    for (int i = 0; i < 3; ++i) pcc_gc_scheduler_root_register(&weak_roots[i]);
    const char *fields[1] = {"link"};
    void *base = py_class_new("PCCClassSweepBase", (void **)0, 0, fields, 1);
    if (!base) return 10;
    void *bases[1] = {base};
    void *child = py_class_new("PCCClassSweepChild", bases, 1, fields, 1);
    if (!child) return 11;
    void *instance = py_instance_new(child);
    if (!instance) return 12;
    py_instance_set_field(instance, 0, instance);
    void *objects[3] = {base, child, instance};
    for (int i = 0; i < 3; ++i) {
        void *ref = py_weakref_new(objects[i], (void *)0);
        if (!ref) return 14;
        pcc_gc_store_root(&weak_roots[i], ref);
        py_decref(ref);
    }
    /* GC0 uses allocator/flag tracking rather than the tracing object list.
       Other collectors must expose both classes before this observer can
       establish physical retirement. Weakrefs check the lifetime in all modes. */
    if (pcc_gc_backend() != 0 && count_fixture_classes() != 2) return 13;
    py_decref(instance);
    py_decref(child);
    py_decref(base);
    return 1;
}

static int is_fixture_class(const char *name) {
    const char *prefix = "PCCClassSweep";
    if (!name) return 0;
    for (int i = 0; prefix[i]; ++i) {
        if (name[i] != prefix[i]) return 0;
    }
    return 1;
}

long count_fixture_classes(void) {
    long remaining = 0;
    pcc_py_gc_minor_graph_lock();
    void *node = pcc_gc_object_list_head();
    while (node) {
        if (pcc_gc_object_node_is_active(node)) {
            void *obj = *(void **)node;
            if (*(int *)((char *)obj + @TAG_OFFSET@) == @CLASS_TAG@) {
                const char *name = *(const char **)((char *)obj + @NAME_OFFSET@);
                if (is_fixture_class(name)) remaining++;
            }
        }
        node = pcc_gc_object_node_next(node);
    }
    pcc_py_gc_minor_graph_unlock();
    return remaining;
}

long clear_dead_class_stack(void) {
    /* The factory's C locals are dead. Erase their old stack words so this
       physical-reclamation assertion does not depend on conservative false roots. */
    volatile long words[4096];
    for (int i = 0; i < 4096; ++i) words[i] = 0;
    return words[0];
}

long remaining_class_graph(void) {
    long alive = 0;
    pcc_py_gc_minor_graph_lock();
    for (int i = 0; i < 3; ++i) {
        void *value = py_weakref_call(weak_roots[i]);
        if (value && value != py_None) alive++;
        py_decref(value);
    }
    pcc_py_gc_minor_graph_unlock();
    alive += count_fixture_classes();
    for (int i = 0; i < 3; ++i) {
        pcc_gc_store_root(&weak_roots[i], (void *)0);
        pcc_gc_scheduler_root_unregister(&weak_roots[i]);
    }
    return alive;
}
'''

PROGRAM = '''import gc
from pcc.extern import c_int64, extern
make = extern("make_class_graph", (), c_int64)
remaining = extern("remaining_class_graph", (), c_int64)
clear_stack = extern("clear_dead_class_stack", (), c_int64)
def main():
    made = make()
    for index in range(8):
        clear_stack()
        gc.collect()
    print(made, remaining())
main()
'''

REFERENCE = '''import gc
import weakref
def make():
    class Base:
        pass
    class Child(Base):
        pass
    instance = Child()
    instance.link = instance
    return weakref.ref(Base), weakref.ref(Child), weakref.ref(instance)
def main():
    references = make()
    for index in range(8):
        gc.collect()
    print(1, sum(reference() is not None for reference in references))
main()
'''


@pytest.fixture(scope="module")
def class_graph_binary(tmp_path_factory, pcc_runtime_archive):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from pcc.runtime.py import py_abi_constants as abi
    from tests.python.test_owned_external_tls import _c_ir

    root = tmp_path_factory.mktemp("class_graph_lifetime")
    reference = subprocess.run([sys.executable, "-c", REFERENCE], capture_output=True,
                               text=True, timeout=20)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, "1 0\n", "")
    # The native observation checks physical class retirement as well as the
    # reference program's unreachable-class lifetime. The public constructors
    # provide the real field layout, copied names, base owners and C3 MRO.
    harness = (HARNESS.replace("@TAG_OFFSET@", str(abi.PYOBJECTHEADER_TYPE_TAG_OFFSET))
               .replace("@CLASS_TAG@", str(abi.PY_TYPE_CLASS))
               .replace("@NAME_OFFSET@", str(abi.PYCLASSOBJECT_NAME_OFFSET)))
    helper = root / "class_graph.o"
    target = host_target_triple()
    helper.write_bytes(emit_owned_object(_c_ir(harness, target), target))
    source = root / "class_graph.py"
    source.write_text(PROGRAM)
    binary = root / "class_graph"
    compile_python(str(source), str(binary), backend="self",
                   runtime_archive=str(pcc_runtime_archive), object_inputs=(str(helper),))
    return binary, reference.stdout


@pytest.mark.integration
@pytest.mark.xdist_group("class_graph_lifetime")
@pytest.mark.parametrize("backend,tracing", ((0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (1, 1), (2, 1), (4, 1)))
def test_class_graph_is_reclaimed_by_each_collector(class_graph_binary, backend, tracing):
    binary, expected = class_graph_binary
    result = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=30,
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend),
                 PCC_GC_TRACING_ONLY=str(tracing)),
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, expected, ""), (backend, tracing)


LATE_REFERENCE = '''import gc
events = []
created = []
def make():
    class Cycle:
        def __del__(self):
            events.append(1)
            if len(events) == 1:
                next = type(self)()
                next.link = next
                created.append(1)
    old = Cycle()
    old.link = old
def main():
    make()
    for index in range(8):
        gc.collect()
    print(0, len(created), len(events))
main()
'''

LATE_PROGRAM = '''import gc
from pcc.extern import c_int64, extern
make = extern("make_late_instance_graph", (), c_int64)
scrub = extern("clear_late_instance_stack", (), c_int64)
created = extern("late_instance_created", (), c_int64)
finalizers = extern("late_instance_finalizers", (), c_int64)
flags = extern("late_instance_allocation_flags", (), c_int64)
def main():
    status = make()
    for index in range(8):
        scrub()
        gc.collect()
    assert flags() & 1024 == 0
    print(status, created(), finalizers())
main()
'''


@pytest.mark.integration
@pytest.mark.xdist_group("class_graph_lifetime")
def test_finalizer_created_instance_keeps_its_class_and_runs_its_own_finalizer(
    tmp_path, pcc_runtime_archive,
):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from pcc.runtime.py import py_abi_constants as abi
    from tests.python.test_owned_external_tls import _c_ir

    reference = subprocess.run([sys.executable, "-c", LATE_REFERENCE], capture_output=True,
                               text=True, timeout=20)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, "0 1 2\n", "")
    fixture = Path(__file__).with_name("fixtures") / "gc_finalizer_new_instance.c"
    harness = (fixture.read_text().replace("@CLS_OFFSET@", str(abi.PYINSTANCEOBJECT_CLS_OFFSET))
               .replace("@FLAGS_OFFSET@", str(abi.PYOBJECTHEADER_FLAGS_OFFSET)))
    helper = tmp_path / "late_instance.o"
    target = host_target_triple()
    helper.write_bytes(emit_owned_object(_c_ir(harness, target), target))
    source = tmp_path / "late_instance.py"
    source.write_text(LATE_PROGRAM)
    binary = tmp_path / "late_instance"
    compile_python(str(source), str(binary), backend="self",
                   runtime_archive=str(pcc_runtime_archive), object_inputs=(str(helper),))
    for tracing in ("0", "1"):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="4",
                     PCC_GC4_TRACING_ONLY=tracing, PCC_GC_DEBT_THRESHOLD="1099511627776"),
        )
        assert (result.returncode, result.stdout, result.stderr) == (0, reference.stdout, ""), tracing


@pytest.mark.integration
@pytest.mark.xdist_group("class_graph_lifetime")
def test_permanent_object_class_keeps_its_mutable_namespace(tmp_path, pcc_runtime_archive):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from pcc.runtime.py import py_abi_constants as abi
    from tests.python.test_owned_external_tls import _c_ir

    fixture = Path(__file__).with_name("fixtures") / "gc_immortal_class_owner.c"
    harness = fixture.read_text().replace("@ATTRS@", str(abi.PYCLASSOBJECT_ATTRS_OFFSET))
    helper = tmp_path / "immortal_owner.o"
    target = host_target_triple()
    helper.write_bytes(emit_owned_object(_c_ir(harness, target), target))
    source = tmp_path / "immortal_owner.py"
    source.write_text('''import gc
from pcc.extern import c_int64, extern
make = extern("make_immortal_root_cache", (), c_int64)
scrub = extern("clear_immortal_cache_old_stack", (), c_int64)
live = extern("immortal_root_cache_is_live", (), c_int64)
def main():
    made = make()
    for index in range(4):
        scrub()
        gc.collect()
    print(made, live())
main()
''')
    binary = tmp_path / "immortal_owner"
    compile_python(str(source), str(binary), backend="self",
                   runtime_archive=str(pcc_runtime_archive), object_inputs=(str(helper),))
    for backend, tracing in ((0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (1, 1), (2, 1), (4, 1)):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend),
                     PCC_GC_TRACING_ONLY=str(tracing)),
        )
        assert (result.returncode, result.stdout, result.stderr) == (0, "1 1\n", ""), (backend, tracing)
