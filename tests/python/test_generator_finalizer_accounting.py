"""Count actual generator finalization, not no-op sweep dispatches.

Host models execute production bodies with controlled memory/runtime boundaries.
The integration program below must additionally execute against a real archive.
"""
import ast
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(os.environ.get('PCC_FINALIZER_MODEL_SOURCE_ROOT',
                           str(Path(__file__).resolve().parents[2])))
COUNTER = 'pcc_gc_user_finalizer_runs'


def _bodies(name, functions, namespace):
    path = ROOT / 'pcc/runtime/py' / name
    tree = ast.parse(path.read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name in functions]
    assert {node.name for node in nodes} == set(functions)
    for node in nodes:
        node.decorator_list = []
    tree = ast.Module(body=[ast.ImportFrom(module='__future__',
        names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])
    exec(compile(ast.fix_missing_locations(tree), str(path), 'exec'), namespace)


class Finalization:
    def __init__(self, *, done=0, state=1, flags=0, count_free=False):
        self.memory = {(1000, 0): 1, (1000, 8): 15, (1000, 12): flags,
                       (1000, 16): 2000, (1000, 32): state, (1000, 40): done,
                       (9000, 0): 1, (9000, 12): 0,
                       (COUNTER, 0): 0,
                       ('pcc_class_del_defined_count', 0): 1,
                       ('pcc_gc_backend_selected', 0): 4,
                       ('pcc_gc_backend4_no_refcounts', 0): int(count_free),
                       ('pcc_gc_tracing_finish_stop_epoch', 0): 7,
                       (3000, 0): 1000, (3000, 16): 0}
        self.events = []
        self.pending = 0
        self.count_free = count_free
        self.weak_hook = None
        self.close_hook = None
        self.close_count = 0
        self.rescued = []
        self.active = True
        self.next_slot = 10000
        constants = ast.parse((ROOT / 'pcc/runtime/py/py_abi_constants.py').read_text())
        self.ns = {}
        for node in constants.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name): self.ns[target.id] = node.value.value
        self.memory[1000, 8] = self.ns['PY_TYPE_GEN']
        self.ns.update(
            ptr_is_null=lambda value: value == 0,
            is_tagged_int=lambda value: value & 1,
            global_addr=lambda name: name,
            global_load_ptr=lambda name: 3000,
            load_i32=self.load, load_i64=self.load, load_ptr=self.load,
            store_i32=self.store, store_i64=self.store, store_ptr=self.store,
            atomic_rmw_i64=self.add, atomic_rmw_i32=self.bits,
            cstr=str, null=lambda: 0, stack_alloc=self.stack,
            py_incref=lambda value: self.ref(value, 1),
            py_decref=lambda value: self.ref(value, -1),
            pcc_gc_pin=lambda value: self.pin(value, True),
            pcc_gc_unpin=lambda value: self.pin(value, False),
            py_weakref_invalidate=self.weak,
            py_current_exception=lambda: self.pending,
            py_clear_exception=lambda: self.set_error(0),
            py_raise=self.set_error, py_tls_exc_set=self.set_error,
            py_gen_close=self.close,
            pcc_capi_is_cext_type_tag=lambda tag: 0,
            _load_instance_cls=lambda value: 2000,
            py_class_lookup=lambda cls, name: 7000,
            _finalizer_owner_enter=lambda slot: 0,
            _finalizer_owner_leave=lambda *args: None,
            _call_user_unary_method_void=lambda func, value: self.events.append(('instance', self.count())),
            pcc_diagnostics_runtime_log_event_code=lambda *args: None,
            pcc_thread_world_stop_epoch_held=lambda: 7,
            pcc_gc_object_node_is_active=lambda node: int(self.active),
            pcc_gc_object_is_address_pinned=lambda value: int(bool(self.load(value, 12) & 64)),
            pcc_gc_forwarding_index_find=lambda value: 0,
            pcc_gc_tracing_recheck_reachability_after_finalizers=self.recheck,
            py_weakref_invalidate_tracing_candidates=lambda: 0,
            pcc_gc_tracing_clear_unreachable=lambda value: self.events.append(('clear',)),
            pcc_gc_tracing_finalize_unreachable=self.free,
            abi_constant=lambda name: self.ns['PY_TYPE_CLASS'],
        )
        _bodies('py_gen.py', {'py_gen_finalize'}, self.ns)
        _bodies('py_dunder.py', {'py_user_del_dispatch'}, self.ns)
        _bodies('freestanding_gc_tracing_sweep_collector.py',
                {'pcc_gc_tracing_sweep_unreachable'}, self.ns)

    def load(self, base, offset):
        return self.memory.get((base, offset), 0)

    def store(self, base, offset, value):
        self.memory[base, offset] = value

    def add(self, op, base, offset, amount, order):
        assert op == 'add'
        previous = self.load(base, offset)
        self.store(base, offset, previous + amount)
        self.events.append(('count', self.count()))
        return previous

    def bits(self, op, base, offset, bits, order):
        previous = self.load(base, offset)
        self.store(base, offset, previous | bits if op == 'or' else previous & bits)
        return previous

    def stack(self, size):
        self.next_slot += size
        return self.next_slot

    def ref(self, value, delta):
        if value and not self.count_free:
            self.store(value, 0, self.load(value, 0) + delta)

    def pin(self, value, enabled):
        if value:
            flags = self.load(value, 12)
            self.store(value, 12, flags | 64 if enabled else flags & ~64)

    def count(self):
        return self.load(COUNTER, 0)

    def set_error(self, value):
        self.pending = value

    def weak(self, value):
        self.events.append(('weak', self.count()))
        if self.weak_hook:
            saved = self.pending
            self.weak_hook()
            self.pending = saved

    def close(self, value):
        self.events.append(('close', self.count()))
        self.close_count += 1
        if self.close_hook: self.close_hook()
        self.store(value, 40, 1)
        return 0

    def recheck(self):
        self.events.append(('recheck',))
        if self.rescued:
            self.store(1000, 12, self.load(1000, 12) & ~1024)

    def free(self, value):
        self.events.append(('free',))
        self.active = False


@pytest.mark.parametrize('entry', ('py_user_del_dispatch', 'py_gen_finalize'))
@pytest.mark.parametrize('shape', ('completed', 'unstarted', 'finalized'))
@pytest.mark.parametrize('count_free', (False, True))
def test_noop_generators_do_not_count_user_finalizers(entry, shape, count_free):
    model = Finalization(done=int(shape == 'completed'), state=0 if shape == 'unstarted' else 1,
                         flags=4 if shape == 'finalized' else 0, count_free=count_free)
    before = dict(model.memory)
    model.ns[entry](1000)
    assert model.count() == 0
    assert model.memory == before
    assert model.events == [] and model.close_count == 0


@pytest.mark.parametrize('entry', ('py_user_del_dispatch', 'py_gen_finalize'))
@pytest.mark.parametrize('scenario', ('normal', 'weak-reentry', 'close-reentry', 'close-error', 'resurrect'))
@pytest.mark.parametrize('count_free', (False, True))
def test_started_generator_counts_before_callbacks_and_finalizes_once(entry, scenario, count_free):
    model = Finalization(count_free=count_free)
    model.pending = 9000
    if scenario == 'weak-reentry':
        model.weak_hook = lambda: model.ns['py_user_del_dispatch'](1000)
    if scenario == 'close-reentry':
        model.close_hook = lambda: model.ns['py_gen_finalize'](1000)
    if scenario == 'close-error':
        model.close_hook = lambda: model.set_error(9100)
    if scenario == 'resurrect':
        model.close_hook = lambda: model.rescued.append(1000)
    model.ns[entry](1000)
    model.ns[entry](1000)
    assert model.count() == model.close_count == 1
    assert model.events[:3] == [('count', 1), ('weak', 1), ('close', 1)]
    assert model.pending == 9000
    assert model.load(1000, 0) == model.load(9000, 0) == 1
    assert model.load(1000, 12) & 4 and not model.load(1000, 12) & 64
    assert model.rescued == ([1000] if scenario == 'resurrect' else [])


@pytest.mark.parametrize('shape,expected', (('completed', 0), ('unstarted', 0), ('finalized', 0), ('suspended', 1)))
@pytest.mark.parametrize('count_free', (False, True))
def test_production_sweep_rechecks_only_for_real_generator_finalization(shape, expected, count_free):
    model = Finalization(done=int(shape == 'completed'), state=0 if shape == 'unstarted' else 1,
                         flags=1024 | 8 | (4 if shape == 'finalized' else 0), count_free=count_free)
    assert model.ns['pcc_gc_tracing_sweep_unreachable'](1) == 1
    assert model.count() == expected
    assert model.events.count(('recheck',)) == expected
    assert model.close_count == expected


def test_generator_resurrection_still_triggers_recheck_before_clear():
    model = Finalization(flags=1024 | 8, count_free=True)
    model.close_hook = lambda: model.rescued.append(1000)
    assert model.ns['pcc_gc_tracing_sweep_unreachable'](1) == 0
    assert model.count() == 1 and model.events.count(('recheck',)) == 1
    assert ('clear',) not in model.events and ('free',) not in model.events


def test_completed_generator_weak_callback_keeps_its_independent_recheck():
    model = Finalization(done=1, flags=1024 | 8, count_free=True)

    def invalidate_candidates():
        model.rescued.append(1000)
        return 1

    model.ns['py_weakref_invalidate_tracing_candidates'] = invalidate_candidates
    assert model.ns['pcc_gc_tracing_sweep_unreachable'](1) == 0
    assert model.count() == model.close_count == 0
    assert model.events.count(('recheck',)) == 1
    assert ('clear',) not in model.events and ('free',) not in model.events


def test_instance_finalizer_accounting_is_unchanged():
    model = Finalization()
    model.store(1000, 8, model.ns['PY_TYPE_INSTANCE'])
    model.ns['py_user_del_dispatch'](1000)
    model.ns['py_user_del_dispatch'](1000)
    assert model.count() == 1
    assert model.events.count(('instance', 1)) == 1


NATIVE_PROGRAM = '''import gc
import weakref
from pcc.extern import c_int64, c_obj, c_void, extern
count = extern("generator_finalizer_runs", (), c_int64)
dispatch = extern("generator_dispatch", (c_obj,), c_void)
finalize = extern("generator_finalize_direct", (c_obj,), c_void)
preserve = extern("generator_finalize_preserves_pending", (c_obj,), c_int64)
events = []
rescued = []
def values(label):
    try:
        yield 1
    finally:
        events.append(label)
def failing():
    try:
        yield 1
    finally:
        events.append("error")
        raise ValueError("cleanup error")
def callback(ref):
    events.append("weak")
def cyclic(box):
    try:
        yield 1
    finally:
        events.append("resurrect")
        rescued.append(box[0])
        gc.collect()
def make_cycle():
    box = []
    gen = cyclic(box)
    box.append(gen)
    next(gen)
    return weakref.ref(gen, callback)
class Finalizable:
    def __del__(self):
        events.append("instance")
def main():
    gc.disable()
    unstarted = values("unstarted")
    before = count()
    dispatch(unstarted)
    finalize(unstarted)
    assert count() == before
    assert events == []
    completed = values("completed")
    assert next(completed) == 1
    try:
        next(completed)
    except StopIteration:
        pass
    else:
        raise AssertionError("generator did not complete")
    before = count()
    dispatch(completed)
    finalize(completed)
    assert count() == before
    assert events == ["completed"]
    events.clear()
    suspended = values("suspended")
    next(suspended)
    ref = weakref.ref(suspended, callback)
    before = count()
    dispatch(suspended)
    assert count() - before == 1
    assert events == ["weak", "suspended"]
    assert ref() is None
    dispatch(suspended)
    finalize(suspended)
    assert count() - before == 1
    events.clear()
    bad = failing()
    next(bad)
    before = count()
    assert preserve(bad) == 1
    assert count() - before == 1
    assert events == ["error"]
    events.clear()
    before = count()
    resurrected_ref = make_cycle()
    for index in range(4):
        gc.collect()
    assert count() - before == 1
    assert resurrected_ref() is None
    assert events == ["weak", "resurrect"]
    assert len(rescued) == 1
    try:
        next(rescued[0])
    except StopIteration:
        pass
    else:
        raise AssertionError("resurrected generator was not closed")
    rescued.clear()
    for index in range(4):
        gc.collect()
    assert count() - before == 1
    instance = Finalizable()
    before = count()
    dispatch(instance)
    dispatch(instance)
    assert count() - before == 1
    assert events == ["weak", "resurrect", "instance"]
    print("GENERATOR_FINALIZER_ACCOUNTING_OK")
main()
'''


@pytest.fixture(scope='module')
def finalizer_accounting_binary(tmp_path_factory, pcc_runtime_archive):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.python.test_owned_external_tls import _c_ir
    root = tmp_path_factory.mktemp('generator_finalizer_accounting')
    fixture = Path(__file__).with_name('fixtures') / 'gc_generator_finalizer_accounting.c'
    helper = root / 'counter.o'
    target = host_target_triple()
    helper.write_bytes(emit_owned_object(_c_ir(fixture.read_text(), target), target))
    source = root / 'accounting.py'
    source.write_text(NATIVE_PROGRAM)
    binary = root / 'accounting'
    compile_python(str(source), str(binary), backend='self',
                   runtime_archive=str(pcc_runtime_archive), object_inputs=(str(helper),))
    return binary


@pytest.mark.integration
@pytest.mark.xdist_group('generator_finalizer_accounting')
@pytest.mark.parametrize('backend,tracing', ((0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (4, 1)))
def test_generator_finalizer_accounting_native(finalizer_accounting_binary, backend, tracing):
    result = subprocess.run(
        [str(finalizer_accounting_binary)], capture_output=True, text=True, timeout=30,
        env=dict(os.environ, PATH='/nonexistent', PCC_GC_BACKEND=str(backend),
                 PCC_GC4_TRACING_ONLY='0', PCC_GC_TRACING_ONLY=str(tracing)),
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        0, 'GENERATOR_FINALIZER_ACCOUNTING_OK\n', '',
    ), (backend, tracing, result)
