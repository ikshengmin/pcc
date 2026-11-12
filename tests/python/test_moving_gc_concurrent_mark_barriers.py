"""The store barrier composes with marking while mutators keep running.

These execute the production store barrier and its count-free shade with
explicit colors, phases, index membership and container-lock state; they
do not establish native collector-worker progress by themselves.
"""
import ast
from pathlib import Path

import pytest
from tests.python.runtime_body_model import install_extern_defaults


SOURCE = Path(__file__).resolve().parents[2] / 'pcc/runtime/py/freestanding_gc_barrier_dispatcher.py'
WHITE, GRAY, BLACK, YOUNG, OLD, REMEMBERED = 8, 16, 32, 128, 256, 512
TERMINAL = 524288


class BarrierState:
    def __init__(self, backend, active=True, value_flags=WHITE, container=False, indexed=True):
        self.backend = backend
        self.globals = {'pcc_gc_mark_active': int(active), 'pcc_gc_backend_selected': backend,
                        'pcc_gc_cms_wb_flushes': 0}
        self.flags = {'owner': BLACK | OLD, 'value': value_flags}
        self.container = container
        self.indexed = indexed
        self.shades = []
        self.no_park = 0
        names = {'pcc_gc_note_slot_write_barrier', 'pcc_gc_count_free_barrier_shade'}
        nodes = [n for n in ast.parse(SOURCE.read_text()).body
                 if isinstance(n, ast.FunctionDef) and n.name in names]
        assert {n.name for n in nodes} == names
        for node in nodes:
            node.decorator_list = []
        namespace = {'i64': int, 'ptr_is_null': lambda p: p is None,
                     'is_tagged_int': lambda p: isinstance(p, int),
                     '_selected_backend': lambda: self.backend,
                     'global_addr': lambda name: name,
                     'load_i32': self.load,
                     'atomic_load_i32': lambda address, offset, order: self.load(address, offset),
                     'atomic_rmw_i32': self.rmw,
                     'pcc_container_lock_held': lambda: int(self.container),
                     'pcc_gc_object_index_seen_stable': lambda p: int(self.indexed and p in self.flags),
                     'pcc_gc_shade_gray_atomic': self.shade,
                     'pcc_thread_no_park_enter': self.no_park_enter,
                     'pcc_thread_no_park_exit': self.no_park_exit}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
        # Cross-object entries this model leaves unbound (colour predicates of
        # non-cell objects read the header).
        install_extern_defaults(namespace)
        self.barrier = namespace['pcc_gc_note_slot_write_barrier']

    def load(self, address, offset):
        if address in self.flags:
            assert offset == 12
            return self.flags[address]
        return self.globals[address]

    def rmw(self, op, address, offset, delta, order):
        assert op == 'add' and offset == 0
        self.globals[address] += delta

    def shade(self, pointer, count):
        self.shades.append((pointer, count))
        self.flags[pointer] = self.flags[pointer] & ~(WHITE | GRAY | BLACK) | GRAY

    def no_park_enter(self):
        self.no_park += 1

    def no_park_exit(self):
        assert self.no_park > 0
        self.no_park -= 1


OWNERS = [None, 'owner']


@pytest.mark.parametrize('owner', OWNERS)
@pytest.mark.parametrize('active', [False, True])
def test_gc0_store_owes_no_barrier_work(owner, active):
    state = BarrierState(0, active=active)
    state.barrier(owner, 'slot', 'value')
    assert state.shades == [] and state.no_park == 0
    assert state.globals['pcc_gc_cms_wb_flushes'] == 0


@pytest.mark.parametrize('backend', [1, 2, 3, 4])
@pytest.mark.parametrize('owner', OWNERS)
@pytest.mark.parametrize('container', [False, True])
def test_inactive_marking_does_not_fabricate_a_cycle(backend, owner, container):
    state = BarrierState(backend, active=False, container=container)
    state.barrier(owner, 'slot', 'value')
    assert state.shades == [] and state.flags['value'] == WHITE
    assert state.globals['pcc_gc_mark_active'] == 0 and state.no_park == 0


@pytest.mark.parametrize('backend', [1, 3, 4])
@pytest.mark.parametrize('owner', OWNERS)
@pytest.mark.parametrize('container', [False, True])
def test_white_value_gets_marking_work_once(backend, owner, container):
    state = BarrierState(backend, container=container)
    state.barrier(owner, 'slot', 'value')
    assert state.shades == [('value', 0)] and state.flags['value'] == GRAY
    state.barrier(owner, 'slot', 'value')
    assert state.shades == [('value', 0)]
    assert state.no_park == 0


@pytest.mark.parametrize('owner', OWNERS)
@pytest.mark.parametrize('container', [False, True])
def test_gc2_shades_and_counts_unshaded_values(owner, container):
    state = BarrierState(2, container=container)
    state.barrier(owner, 'slot', 'value')
    assert state.shades == [('value', 1)]
    # The unlocked count-free path counts a shade of a value not yet gray;
    # a store under the owner's container lock shades without counting.
    expected = 0 if container and owner is not None else 1
    assert state.globals['pcc_gc_cms_wb_flushes'] == expected


@pytest.mark.parametrize('backend', [1, 2, 3, 4])
@pytest.mark.parametrize('owner', OWNERS)
@pytest.mark.parametrize('container', [False, True])
@pytest.mark.parametrize('case', ['terminal', 'staged', 'null', 'tagged'])
def test_values_that_owe_no_marking_work_are_skipped(backend, owner, container, case):
    state = BarrierState(backend, container=container,
                         value_flags=WHITE | TERMINAL if case == 'terminal' else WHITE,
                         indexed=case != 'staged')
    value = {'null': None, 'tagged': 7}.get(case, 'value')
    state.barrier(owner, 'slot', value)
    assert state.shades == [] and state.no_park == 0


@pytest.mark.parametrize('backend', [1, 2, 3, 4])
def test_tagged_owner_owes_nothing(backend):
    state = BarrierState(backend)
    state.barrier(7, 'slot', 'value')
    assert state.shades == []


@pytest.mark.parametrize('backend', [1, 3, 4])
@pytest.mark.parametrize('color', [GRAY, BLACK])
def test_already_shaded_value_is_left_alone(backend, color):
    state = BarrierState(backend, value_flags=color)
    state.barrier('owner', 'slot', 'value')
    assert state.shades == []


NATIVE_SOURCE = r'''
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#define SOURCE_COLOR @SOURCE_COLOR@
extern int32_t pcc_gc_mark_active;
extern int64_t pcc_gc_gray_count_load_acquire(void);
extern void pcc_gc_gray_count_store_release(int64_t);
extern void pcc_gc_set_mark_color(PyObject *, int64_t);
extern PyObject *pcc_gc_generational_oldify_copy(PyObject *);

static int fail(int code, const char *phase) {
    fprintf(stderr, "code=%d phase=%s gray=%lld\n", code, phase,
            (long long)pcc_gc_gray_count_load_acquire());
    return code;
}

int main(void) {
    int backend = pcc_gc_backend();
    if (backend != 3 && backend != 4) return 2;
    if (pcc_threads_enabled() != 1) return 3;
    py_gc_disable();
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    pcc_gc_gray_count_store_release(0);
    PyObject *born = py_list_new(0);
    if (born == 0) return 4;
    if ((py_header_flags_load(py_header(born)) & 56) != 32)
        return fail(10, "allocation-not-black");
    py_decref(born);
    __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);

    for (int root_case = 0; root_case < 2; root_case++) {
        PyObject *owner = py_list_new(0);
        PyObject *value = py_list_new(0);
        if (owner == 0 || value == 0) return 5;
        pcc_gc_pin(owner);
        pcc_gc_pin(value);
        uint32_t of = py_header_flags_load(py_header(owner));
        uint32_t vf = py_header_flags_load(py_header(value));
        __atomic_store_n(&py_header(owner)->flags, (of & ~(56u | 384u)) | 32u | 256u, __ATOMIC_RELEASE);
        __atomic_store_n(&py_header(value)->flags, (vf & ~(56u | 384u)) | 8u | 256u, __ATOMIC_RELEASE);
        __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
        pcc_gc_gray_count_store_release(0);
        PyObject **slot = ((PyListObject *)owner)->items;
        slot[0] = 0;
        pcc_gc_note_slot_write_barrier(root_case ? 0 : owner, slot, value);
        if ((py_header_flags_load(py_header(value)) & 16) == 0)
            return fail(20, "barrier-did-not-gray");
        if (pcc_gc_gray_count_load_acquire() != 1)
            return fail(21, "barrier-work-count");
        pcc_gc_note_slot_write_barrier(root_case ? 0 : owner, slot, value);
        if (pcc_gc_gray_count_load_acquire() != 1)
            return fail(22, "duplicate-gray-work");
        pcc_gc_unpin(value);
        py_decref(value);
        if (pcc_gc_gray_count_load_acquire() != 0)
            return fail(23, "terminal-gray-work-leaked");
        __atomic_store_n(&pcc_gc_mark_active, 0, __ATOMIC_RELEASE);
        pcc_gc_unpin(owner);
        py_decref(owner);
    }

    /* GC3 copies leaf payloads; compound young objects promote in place. */
    PyObject *source = backend == 3 ? py_bytes_new("gray", 4) : py_list_new(0);
    if (source == 0) return 6;
    void *handle = pcc_gc_scheduler_root_register_handle(&source);
    if (handle == 0) return 7;
    pcc_gc_unpin(source);
    pcc_gc_set_mark_color(source, SOURCE_COLOR);
    pcc_gc_gray_count_store_release(SOURCE_COLOR == 16 ? 1 : 0);
    __atomic_store_n(&pcc_gc_mark_active, 1, __ATOMIC_RELEASE);
    PyObject *copy = 0;
    if (backend == 3) {
        pcc_py_gc_minor_graph_lock();
        copy = pcc_gc_generational_oldify_copy(source);
        pcc_py_gc_minor_graph_unlock();
    } else {
        pcc_gc_reset_relocation_set();
        if (pcc_gc_select_relocation_set(1) != 1)
            return fail(30, "relocation-selection");
        copy = pcc_gc_relocate_copy(source, sizeof(PyListObject));
    }
    if (copy == 0) return fail(31, "copy-rejected");
    if ((py_header_flags_load(py_header(copy)) & 16) == 0)
        return fail(32, "replacement-not-gray");
    /* GC3 retires the old node; GC4 keeps a sweep-visible source shell. */
    if (pcc_gc_gray_count_load_acquire() != (backend == 3 ? 1 : 1 + (SOURCE_COLOR == 16)))
        return fail(33, "moving-gray-work-ownership");
    printf("MOVING_MARK_INVARIANTS_OK\n");
    /* Deliberate single-transition probe: no full-cycle or leak claim. */
    return 0;
}
'''


@pytest.mark.integration
@pytest.mark.parametrize("source_color", [WHITE, GRAY, BLACK], ids=("white", "gray", "black"))
def test_moving_mark_invariants_with_owned_native_c(tmp_path, pcc_runtime_archive, source_color):
    import os
    import subprocess

    from tests.owned_runtime_c_fixture import link_c_harness

    source = tmp_path / 'moving_mark.c'
    source.write_text(NATIVE_SOURCE.replace("@SOURCE_COLOR@", str(source_color)))
    binary = tmp_path / 'moving_mark'
    runtime = Path(__file__).resolve().parents[2] / 'pcc/runtime'
    link_c_harness(source, binary, extra_include_dirs=(str(runtime / 'src'),))
    for backend in (3, 4):
        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                             env=dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_GC_MINOR_ALLOC_MAX='4096', PATH='/nonexistent'))
        (tmp_path / f'gc{backend}.stdout').write_text(ran.stdout)
        (tmp_path / f'gc{backend}.stderr').write_text(ran.stderr)
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, 'MOVING_MARK_INVARIANTS_OK\n', ''), backend
