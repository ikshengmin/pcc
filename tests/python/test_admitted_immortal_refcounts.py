"""Immortal early returns retain the existing tag/provenance/diagnostic gates."""
import os
from pathlib import Path
import subprocess

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.test_foreign_address_leases import _functions


SOURCE = Path(__file__).resolve().parents[2] / 'pcc/runtime/py/py_obj.py'


class RefcountModel:
    def __init__(self, tag=abi.PY_TYPE_STR, count=17, immortal=True,
                 backend=0, logging=False, probe=0, managed=True):
        self.count = count
        self.flags = abi.PY_FLAG_IMMORTAL if immortal else 0
        self.tag, self.backend, self.probe, self.managed = tag, backend, probe, managed
        self.events = []
        self.globals = {'pcc_gc_refcount_atomic': int(backend == 0 and probe == 0),
                        'pcc_gc_refcount_fast': 0,
                        'pcc_diagnostics_runtime_log_fast_state': int(logging)}
        self.ns = {n: getattr(abi, n) for n in dir(abi) if n.startswith('PY')}
        self.ns.update({
            'ptr_is_null': lambda p: p is None, 'is_tagged_int': lambda p: False,
            'global_addr': lambda p: p, 'load_i32': self.load,
            'load_i64': self.load, 'atomic_load_i64': self.load,
            'atomic_rmw_i64': self.increment,
            'i64': int,
            'atomic_cas_i64': self.compare_exchange,
            '_gc_backend_fast': lambda: self.event('backend', self.backend),
            '_refcount_provenance_probe_enabled': lambda: self.probe,
            '_ptr_can_have_header': lambda p: self.event('provenance', self.managed),
            '_note_unmanaged_refcount_op': lambda: self.event('unmanaged', None),
            'pcc_capi_is_cext_type_tag': lambda tag: self.event('cext', 1),
            'pcc_gc_object_is_known': lambda p: self.event('index', 0),
            'stack_alloc': lambda size: 'prepared',
            '_py_incref_prepare': lambda *a: self.event('prepare', None),
            '_py_incref_finish': lambda *a: self.event('finish', None),
            '_py_decref_prepare': lambda *a: self.event('prepare', None),
            '_py_decref_finish': lambda *a: self.event('finish', None),
        })
        _functions(SOURCE, {'py_incref', 'py_decref', '_py_incref_checked_tail', '_py_decref_checked_tail'}, self.ns)

    def event(self, name, result):
        self.events.append(name)
        return result

    def load(self, pointer, offset, *ordering):
        if pointer != 'object':
            return self.globals[pointer]
        assert self.managed, 'unmanaged header read before provenance rejection'
        if offset == abi.PYOBJECTHEADER_TYPE_TAG_OFFSET:
            return self.tag
        if offset == abi.PYOBJECTHEADER_FLAGS_OFFSET:
            return self.flags
        assert offset == abi.PYOBJECTHEADER_REFCOUNT_OFFSET
        return self.count

    def increment(self, op, pointer, offset, amount, *ordering):
        assert op == 'add' and pointer == 'object'
        self.events.append('atomic')
        self.count += amount
        return self.count - amount

    def compare_exchange(self, pointer, offset, expected, replacement, *ordering):
        assert pointer == 'object'
        old = self.count
        if old == expected:
            self.events.append('atomic')
            self.count = replacement
        return old


@pytest.mark.parametrize('operation', ['py_incref', 'py_decref'])
@pytest.mark.parametrize('count', [-1, 0, 17, 2**62])
@pytest.mark.parametrize('tag', [abi.PY_TYPE_NONE, abi.PY_TYPE_BOOL, abi.PY_TYPE_STR,
                                 abi.PY_TYPE_TUPLE, abi.PY_TYPE_USER_CLASS_START + 600])
def test_admitted_immortal_changes_no_count_and_enters_no_slow_path(operation, count, tag):
    model = RefcountModel(tag=tag, count=count)
    model.ns[operation]('object')
    assert model.count == count and model.flags == abi.PY_FLAG_IMMORTAL
    assert model.events == []


@pytest.mark.parametrize('operation', ['py_incref', 'py_decref'])
def test_mutable_nonterminal_counts_still_use_atomic_updates(operation):
    model = RefcountModel(count=3, immortal=False)
    model.ns[operation]('object')
    assert model.count == (4 if operation == 'py_incref' else 2)
    assert model.events == ['atomic']


@pytest.mark.parametrize('operation', ['py_incref', 'py_decref'])
def test_provenance_mode_rejects_before_any_unmanaged_header_load(operation):
    model = RefcountModel(probe=2, managed=False)
    model.ns[operation]('object')
    assert model.events == ['backend', 'provenance', 'unmanaged']
    assert model.count == 17


@pytest.mark.parametrize('operation', ['py_incref', 'py_decref'])
@pytest.mark.parametrize('backend', [1, 2, 3, 4])
def test_other_collectors_keep_the_checked_prepare_finish_path(operation, backend):
    model = RefcountModel(backend=backend)
    model.ns[operation]('object')
    assert model.events == ['backend', 'prepare', 'finish']


@pytest.mark.parametrize('operation', ['py_incref', 'py_decref'])
def test_logging_keeps_the_existing_slow_path(operation):
    model = RefcountModel(logging=True)
    model.ns[operation]('object')
    assert model.events == ['backend']


@pytest.mark.parametrize('operation', ['py_incref', 'py_decref'])
def test_extension_tags_still_require_their_type_validation(operation):
    model = RefcountModel(tag=abi.PY_TYPE_CEXT_TAG_BASE + 1)
    model.ns[operation]('object')
    assert model.events == ['backend', 'cext']


PROBE = r'''
#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
extern int32_t pcc_gc_refcount_atomic;
static int finalized;
static PyObject *finish(PyObject *captures, PyObject *args) { (void)captures; (void)args; finalized++; return py_None; }
int main(void) {
    int backend = pcc_gc_backend();
    py_gc_disable();
    PyObject *values[6] = {py_None, py_True, py_False, py_Ellipsis, py_NotImplemented, py_str_new("constant", 8)};
    if (values[5] == 0) return 2;
    __atomic_fetch_or(&py_header(values[5])->flags, PY_FLAG_IMMORTAL, __ATOMIC_RELEASE);
    for (int i = 0; i < 6; i++) {
        int64_t before = pcc_refcount_load(&py_header(values[i])->refcount);
        for (int n = 0; n < 100; n++) { py_incref(values[i]); py_decref(values[i]); }
        if (pcc_refcount_load(&py_header(values[i])->refcount) != before) return 3;
    }
    __atomic_fetch_and(&py_header(values[5])->flags, ~PY_FLAG_IMMORTAL, __ATOMIC_RELEASE);
    py_decref(values[5]);
    PyObject *mutable = py_list_new(0);
    if (mutable == 0) return 4;
    int64_t before = pcc_refcount_load(&py_header(mutable)->refcount);
    py_incref(mutable); py_incref(mutable);
    if (pcc_refcount_load(&py_header(mutable)->refcount) != before + 2) return 5;
    py_decref(mutable);
    if (pcc_refcount_load(&py_header(mutable)->refcount) != before + 1) return 6;
    py_decref(mutable); py_decref(mutable);
    PyClassObject *cls = py_class_new("TerminalControl", 0, 0, 0, 0);
    if (cls == 0) return 10;
    PyObject *captures = py_tuple_new(0);
    PyObject *method = py_func_new((void *)(uintptr_t)finish, captures);
    if (captures == 0 || method == 0) return 11;
    py_class_add_method(cls, "__del__", method);
    /* py_class_add_method borrows func: keep this owner until class death. */
    py_decref(captures);
    PyObject *instance = py_instance_new(cls);
    PyObject *weak = py_weakref_new(instance, 0);
    if (instance == 0 || weak == 0) return 7;
    py_decref(instance);
    PyObject *seen = py_weakref_call(weak);
    if (seen != py_None || finalized != 1) return 8;
    py_decref(seen); py_decref(weak); py_decref((PyObject *)cls); py_decref(method);
    if (py_err_occurred() != 0) return 9;
    printf("IMMORTAL_REFS_OK gc=%d inline=%d\n", backend, pcc_gc_refcount_atomic);
    return 0;
}
'''


@pytest.mark.integration
def test_native_immortals_mutable_counts_and_terminal_lifetimes(tmp_path, pcc_runtime_archive):
    from tests.owned_runtime_c_fixture import link_c_harness

    source = tmp_path / 'immortal_refs.c'; source.write_text(PROBE)
    binary = tmp_path / 'immortal_refs'
    runtime = Path(__file__).resolve().parents[2] / 'pcc/runtime'
    link_c_harness(source, binary, extra_include_dirs=(str(runtime / 'src'),))
    for backend in range(5):
        for probe in (0, 1, 2):
            env = dict(os.environ, PCC_GC_BACKEND=str(backend),
                       PCC_GC_REFCOUNT_PROVENANCE_PROBE=str(probe), PATH='/nonexistent')
            env.pop('PCC_LOG', None); env.pop('PCC_LOG_FILE', None)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15, env=env)
            (tmp_path / f'gc{backend}-probe{probe}.stdout').write_text(run.stdout)
            (tmp_path / f'gc{backend}-probe{probe}.stderr').write_text(run.stderr)
            expected = f'IMMORTAL_REFS_OK gc={backend} inline={int(backend == 0 and probe == 0)}\n'
            assert (run.returncode, run.stdout, run.stderr) == (0, expected, ''), (backend, probe)


@pytest.mark.parametrize('observed', [2, 4])
def test_nonterminal_drop_retries_a_competing_count_change(observed):
    model = RefcountModel(count=3, immortal=False)
    attempts = []
    def compare(pointer, offset, expected, replacement, *ordering):
        attempts.append(expected)
        if len(attempts) == 1:
            model.count = observed
            return observed
        return model.compare_exchange(pointer, offset, expected, replacement, *ordering)
    model.ns['atomic_cas_i64'] = compare
    model.ns['py_decref']('object')
    assert attempts == [3, observed]
    assert model.count == observed - 1 and model.events == ['atomic']


def test_competing_drop_to_one_keeps_terminal_ownership_in_the_checked_tail():
    model = RefcountModel(count=3, immortal=False)
    def competing_drop(*args):
        model.count = 1
        return 1
    model.ns['atomic_cas_i64'] = competing_drop
    model.ns['_py_decref_checked_tail'] = lambda value: model.event('checked-terminal', None)
    model.ns['py_decref']('object')
    assert model.count == 1 and model.events == ['checked-terminal']
