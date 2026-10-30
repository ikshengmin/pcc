"""Marking and generation barriers must compose while mutators keep running.

These execute the production barrier body with explicit colors and lock checks;
they do not establish native collector-worker progress by themselves.
"""
import ast
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[2] / 'pcc/runtime/py/freestanding_gc_barrier_dispatcher.py'
WHITE, GRAY, BLACK, YOUNG, OLD, REMEMBERED = 8, 16, 32, 128, 256, 512


class BarrierState:
    def __init__(self, backend, active=True, owner_flags=BLACK | OLD, value_flags=WHITE | OLD):
        self.backend = backend
        self.globals = {'pcc_gc_mark_active': int(active), 'pcc_gc_cms_wb_flushes': 0,
                        'pcc_gc_backend4_genzgc_store_barriers': 0}
        self.flags = {'owner': owner_flags, 'value': value_flags}
        self.gray_count = int(bool(value_flags & GRAY))
        self.depth = 0
        self.remembered = set()
        self.before_lock = None
        tree = ast.parse(SOURCE.read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == 'pcc_gc_note_slot_write_barrier')
        function.decorator_list = []
        namespace = {'i64': int, 'null': lambda: None, 'ptr_is_null': lambda p: p is None,
                     'is_tagged_int': lambda p: isinstance(p, int),
                     '_selected_backend': lambda: self.backend,
                     '_ptr_can_have_header': lambda p: p in self.flags,
                     'global_addr': lambda name: name, 'load_i32': self.load,
                     'store_i32': self.store,
                     'pcc_py_gc_minor_graph_lock': self.lock,
                     'pcc_py_gc_minor_graph_unlock': self.unlock,
                     'pcc_gc_object_is_known_no_lock': self.known,
                     'pcc_gc_gray_count_increment_acq_rel': self.increment,
                     'pcc_gc_set_mark_color': self.color,
                     'pcc_gc_backend3_remember_owner': self.remember,
                     'pcc_gc_backend4_store_buffer_enqueue': self.enqueue}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), namespace)
        self.barrier = namespace['pcc_gc_note_slot_write_barrier']

    def load(self, address, offset):
        return self.flags[address] if address in self.flags and offset == 12 else self.globals[address]

    def store(self, address, offset, value):
        self.globals[address] = value

    def lock(self):
        if self.before_lock:
            hook, self.before_lock = self.before_lock, None
            hook()
        self.depth += 1

    def unlock(self):
        assert self.depth > 0
        self.depth -= 1

    def known(self, pointer):
        assert self.depth > 0
        return pointer in self.flags

    def increment(self):
        assert self.depth > 0
        self.gray_count += 1

    def color(self, pointer, color):
        assert self.depth > 0
        self.flags[pointer] = self.flags[pointer] & ~(WHITE | GRAY | BLACK) | color

    def remember(self, owner, flags):
        assert self.depth > 0
        self.remembered.add(owner)
        self.flags[owner] |= REMEMBERED

    def enqueue(self, owner, slot, value):
        assert self.depth > 0
        entry = (owner, slot, value)
        if entry in self.remembered:
            return 0
        self.remembered.add(entry)
        self.flags[owner] |= REMEMBERED
        return 1


@pytest.mark.parametrize('backend', [1, 2, 3, 4])
@pytest.mark.parametrize('owner', [None, 'owner'])
def test_live_reference_to_white_object_is_counted_once(backend, owner):
    state = BarrierState(backend)
    state.barrier(owner, 'slot', 'value')
    assert state.flags['value'] & GRAY
    assert state.gray_count == 1
    state.barrier(owner, 'slot', 'value')
    assert state.gray_count == 1 and state.depth == 0


@pytest.mark.parametrize('backend', [3, 4])
@pytest.mark.parametrize('owner_generation,value_generation', [(OLD, OLD), (YOUNG, OLD), (YOUNG, YOUNG), (OLD, YOUNG)])
def test_major_marking_is_independent_of_generation_direction(backend, owner_generation, value_generation):
    state = BarrierState(backend, owner_flags=BLACK | owner_generation,
                         value_flags=WHITE | value_generation)
    state.barrier('owner', 'slot', 'value')
    assert state.flags['value'] & GRAY and state.gray_count == 1
    assert bool(state.remembered) == (owner_generation == OLD and value_generation == YOUNG)
    assert state.depth == 0


@pytest.mark.parametrize('backend', range(5))
@pytest.mark.parametrize('owner', [None, 'owner'])
def test_inactive_marking_does_not_fabricate_a_cycle(backend, owner):
    state = BarrierState(backend, active=False)
    state.barrier(owner, 'slot', 'value')
    assert state.flags['value'] & WHITE and state.gray_count == 0
    assert state.globals['pcc_gc_mark_active'] == 0 and state.depth == 0


@pytest.mark.parametrize('backend', [3, 4])
def test_inactive_old_to_young_store_keeps_generation_recording(backend):
    state = BarrierState(backend, active=False, value_flags=WHITE | YOUNG)
    state.barrier('owner', 'slot', 'value')
    assert state.remembered and state.flags['owner'] & REMEMBERED
    assert state.flags['value'] & WHITE and state.gray_count == 0


@pytest.mark.parametrize('backend', [3, 4])
def test_mark_verdict_uses_colors_after_lock_acquisition(backend):
    state = BarrierState(backend)
    state.before_lock = lambda: state.flags.update(value=GRAY | OLD)
    state.gray_count = 1
    state.barrier('owner', 'slot', 'value')
    assert state.flags['value'] & GRAY and state.gray_count == 1
    assert state.before_lock is None and state.depth == 0


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
