"""StorePlan skips RC work while its unused stack words remain cleared."""

import ast
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[2] / 'pcc/runtime/py/py_obj.py'
FUNCTIONS = {
    'pcc_gc_store_root_plan_init', 'pcc_gc_store_ptr_plan_init',
    '_pcc_gc_store_plan_commit_locked', '_pcc_gc_store_plan_commit_sentinel_aware_locked',
    'pcc_gc_store_root_plan_commit_locked', 'pcc_gc_store_ptr_plan_commit_locked',
    'pcc_gc_store_ptr_plan_commit_sentinel_aware_locked', '_pcc_gc_store_plan_finish',
    'pcc_gc_store_root_plan_finish', 'pcc_gc_store_ptr_plan_finish',
}


class Store:
    PLAN = 0x1000
    SLOT = 0x2000
    OWNER = 0x3000
    VALUE = 0x4000
    COPY = 0x5000
    OLD = 0x6000
    SENTINEL = 0x7000

    def __init__(self, backend=4, count_free=True, logging=False, forwarded=False, marking=True):
        self.backend = backend
        self.count_free = count_free
        self.words = {self.SLOT: self.OLD}
        self.initialized = set()
        self.events = []
        self.old_reads = 0
        self.locked_heap_barriers = 0
        self.ready = False
        self.reenter_finish = None
        self.globals = {
            'pcc_gc_backend4_no_refcounts': int(count_free),
            'pcc_diagnostics_runtime_log_fast_state': int(logging),
            'pcc_gc_mark_active': int(marking),
        }
        self.ns = dict(
            i64=int, null=lambda: 0, ptr_is_null=lambda p: p == 0,
            is_tagged_int=lambda p: p & 1, ptr_eq=lambda a, b: a == b,
            ptr_add=lambda p, offset: p + offset,
            global_addr=lambda name: name,
            load_i32=self.load, load_i64=self.load, load_ptr=self.load,
            store_i32=self.store, store_i64=self.store, store_ptr=self.store,
            memset=self.zero,
            pcc_gc_note_store=lambda: self.events.append('note'),
            _gc_forwarding_population=lambda: int(forwarded),
            _gc_relocation_candidate=self.candidate,
            pcc_gc_note_relocation_read=self.resolve,
            pcc_gc_note_slot_write_barrier=self.barrier,
            # This fixture checks StorePlan sequencing/RC records, not the
            # barrier policy. The native heap-barrier test runs that policy.
            pcc_gc_backend4_note_heap_store_locked=self.locked_heap_barrier,
            _gc4_old_slot_is_releasable=self.old_known,
            _py_incref_prepare=lambda value, plan: self.prepare('retain', value, plan),
            _py_decref_prepare=lambda value, plan: self.prepare('release', value, plan),
            _py_refcount_prepared_reset=lambda plan, value: self.prepare('sentinel', value, plan),
            _py_incref_finish=lambda plan: self.finish_rc('retain', plan),
            _py_decref_finish=lambda plan: self.finish_rc('release', plan),
            pcc_diagnostics_runtime_log_event_code=self.log,
        )
        nodes = []
        for node in ast.parse(SOURCE.read_text()).body:
            if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                    and node.target.id.startswith('_STORE_PLAN_')):
                nodes.append(node)
            if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
                node.decorator_list = []
                nodes.append(node)
        assert {n.name for n in nodes if isinstance(n, ast.FunctionDef)} == FUNCTIONS
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), self.ns)

    def load(self, pointer, offset):
        if isinstance(pointer, str):
            return self.globals[pointer]
        address = pointer + offset
        if address == self.SLOT:
            self.old_reads += 1
            assert not self.count_free, 'count-free store inspected the previous reference'
        if self.PLAN <= address < self.PLAN + 128:
            assert address in self.initialized, ('uninitialized RC record read', offset)
            if self.count_free:
                assert not self.PLAN + 8 <= address < self.PLAN + 112, 'count-free API read an RC record'
        return self.words[address]

    def store(self, pointer, offset, value):
        address = pointer + offset
        if self.count_free and self.ready:
            assert not self.PLAN + 8 <= address < self.PLAN + 112, 'count-free commit rewrote an RC record'
        self.words[address] = value
        if self.PLAN <= address < self.PLAN + 128:
            self.initialized.add(address)
        if address == self.SLOT:
            self.events.append(('publish', value))

    def zero(self, pointer, value, size):
        assert (pointer, value, size) == (self.PLAN, 0, 128)
        for offset in range(0, size, 4):
            self.store(pointer, offset, 0)

    def candidate(self, value):
        assert value != self.SENTINEL, 'sentinel reached relocation lookup'
        self.events.append(('candidate', value))
        return value == self.VALUE

    def resolve(self, value):
        assert value == self.VALUE
        self.events.append(('resolve', value))
        return self.COPY

    def barrier(self, owner, slot, value):
        assert self.words[slot] == self.OLD
        self.events.append(('barrier', owner, value))

    def locked_heap_barrier(self, owner, slot, value):
        assert self.backend == 4 and owner == self.OWNER
        self.locked_heap_barriers += 1
        self.barrier(owner, slot, value)

    def old_known(self, value):
        assert not self.count_free, 'count-free store checked old provenance'
        assert value == self.OLD
        self.events.append('old-known')
        return 1

    def prepare(self, kind, value, plan):
        assert not self.count_free, 'count-free store prepared an RC record'
        self.events.append(('prepare', kind, value))
        self.store(plan, 0, value)
        self.store(plan, 48, 0)

    def finish_rc(self, kind, plan):
        assert not self.count_free, 'count-free store finished an RC record'
        self.events.append(('finish', kind, self.load(plan, 0)))

    def log(self, category, event, backend, ignored, owner):
        assert (category, event, backend) == (2, 3, self.backend)
        self.events.append(('log', owner))
        if self.reenter_finish is not None:
            self.reenter_finish()

    def init(self, owner=False):
        if owner:
            self.ns['pcc_gc_store_ptr_plan_init'](self.PLAN, self.OWNER, self.backend)
        else:
            self.ns['pcc_gc_store_root_plan_init'](self.PLAN, self.backend)
        self.ready = True

    def commit(self, kind, value, slot=None):
        slot = self.SLOT if slot is None else slot
        if kind == 'root':
            return self.ns['pcc_gc_store_root_plan_commit_locked'](self.PLAN, slot, value)
        if kind == 'field':
            return self.ns['pcc_gc_store_ptr_plan_commit_locked'](self.PLAN, self.OWNER, slot, value)
        return self.ns['pcc_gc_store_ptr_plan_commit_sentinel_aware_locked'](
            self.PLAN, self.OWNER, slot, value, self.SENTINEL,
        )

    def finish(self, kind):
        self.ns['pcc_gc_store_root_plan_finish' if kind == 'root'
                else 'pcc_gc_store_ptr_plan_finish'](self.PLAN)


@pytest.mark.parametrize('kind', ['root', 'field', 'sentinel'])
@pytest.mark.parametrize('value', [0, 35, Store.VALUE])
def test_count_free_publication_never_touches_rc_records_or_the_old_reference(kind, value):
    model = Store()
    model.init(owner=kind != 'root')
    assert len(model.initialized) == 32
    assert model.commit(kind, value) == 1
    model.finish(kind)
    assert model.words[model.SLOT] == value and model.old_reads == 0
    # The conservative scanner may read these words, so zeroing once remains
    # required even though the count-free transaction itself must not read or
    # populate the two unused RC records.
    assert all(model.words[model.PLAN + offset] == 0 for offset in range(8, 112, 4))
    barrier = [('barrier', 0 if kind == 'root' else model.OWNER, value)] if value == model.VALUE else []
    assert model.events == ['note', *barrier, ('publish', value)]
    before = list(model.events)
    assert model.commit(kind, value) == 0
    model.finish(kind)
    assert model.events == before


@pytest.mark.parametrize('kind', ['root', 'field', 'sentinel'])
def test_count_free_store_resolves_forwarding_before_its_insertion_barrier(kind):
    model = Store(forwarded=True)
    model.init(owner=kind != 'root')
    assert model.commit(kind, model.VALUE) == 1
    model.finish(kind)
    assert model.words[model.SLOT] == model.words[model.PLAN] == model.COPY
    assert model.events == ['note', ('candidate', model.VALUE), ('resolve', model.VALUE),
                            ('barrier', 0 if kind == 'root' else model.OWNER, model.COPY),
                            ('publish', model.COPY)]


def test_count_free_sentinel_never_reaches_relocation_or_rc_validation():
    model = Store(forwarded=True)
    model.init(owner=True)
    assert model.commit('sentinel', model.SENTINEL) == 1
    model.finish('sentinel')
    assert model.events == ['note', ('barrier', model.OWNER, model.SENTINEL),
                            ('publish', model.SENTINEL)]


def test_null_slot_and_uncommitted_finish_do_not_consume_uninitialized_records():
    model = Store(logging=True)
    model.init()
    model.finish('root')
    assert model.commit('root', model.VALUE, slot=0) == 0
    model.finish('root')
    assert model.events == [] and model.words[model.PLAN + 124] == 0


def test_root_logging_stays_after_publication_and_finish_is_reentrant_safe():
    model = Store(logging=True)
    model.init()
    assert model.commit('root', model.VALUE) == 1
    model.reenter_finish = lambda: model.finish('root')
    model.finish('root')
    assert model.events == ['note', ('barrier', 0, model.VALUE),
                            ('publish', model.VALUE), ('log', 0)]


def test_field_logging_stays_at_initialization_and_does_not_repeat_at_finish():
    model = Store(logging=True)
    model.init(owner=True)
    assert model.commit('field', model.VALUE) == 1
    model.finish('field')
    assert model.events == [('log', model.OWNER), 'note', ('barrier', model.OWNER, model.VALUE),
                            ('publish', model.VALUE)]


@pytest.mark.parametrize('forwarded', [False, True])
def test_idle_root_store_skips_barrier_but_keeps_forwarding_resolution(forwarded):
    model = Store(marking=False, forwarded=forwarded)
    model.init()
    assert model.commit('root', model.VALUE) == 1
    model.finish('root')
    resolution = [('candidate', model.VALUE), ('resolve', model.VALUE)] if forwarded else []
    assert model.events == ['note', *resolution, ('publish', model.COPY if forwarded else model.VALUE)]


@pytest.mark.parametrize('kind', ['field', 'sentinel'])
def test_idle_heap_field_keeps_its_generation_barrier(kind):
    model = Store(marking=False)
    model.init(owner=True)
    assert model.commit(kind, model.VALUE) == 1
    model.finish(kind)
    assert model.events == ['note', ('barrier', model.OWNER, model.VALUE), ('publish', model.VALUE)]


@pytest.mark.parametrize('backend', range(5))
@pytest.mark.parametrize('kind', ['root', 'field', 'sentinel'])
def test_five_counted_collectors_keep_prepare_and_finish(backend, kind):
    model = Store(backend=backend, count_free=False)
    model.init(owner=kind != 'root')
    assert len(model.initialized) == 32
    assert model.commit(kind, model.VALUE) == 1
    model.finish(kind)
    assert [('prepare', 'retain', model.VALUE), ('prepare', 'release', model.OLD)] == [
        event for event in model.events if isinstance(event, tuple) and event[0] == 'prepare'
    ]
    assert [('finish', 'retain', model.VALUE), ('finish', 'release', model.OLD)] == [
        event for event in model.events if isinstance(event, tuple) and event[0] == 'finish'
    ]


@pytest.mark.integration
def test_store_plan_mode_checks_emit_scalar_ir_without_boxed_comparison(tmp_path):
    from pcc.frontends.python.owned_runtime_build import _compile_runtime_module
    from pcc.frontends.python.pipeline_targets import host_target_triple

    output = tmp_path / 'py_obj.ll'
    _compile_runtime_module('py_obj', str(SOURCE), str(output), host_target_triple())
    text = output.read_text()
    for name in (
        '_pcc_gc_store_plan_commit_locked',
        '_pcc_gc_store_plan_commit_sentinel_aware_locked',
        '_pcc_gc_store_plan_finish',
    ):
        definitions = [part.split('\n}', 1)[0] for part in text.split('\ndefine ')
                       if '@user_py_obj_' + name + '(' in part.split('\n', 1)[0]]
        assert len(definitions) == 1, name
        body = definitions[0]
        assert 'icmp ' in body
        for symbol in ('py_obj_eq_value', 'py_int_from_i64', 'pcc_gc_pin', 'pcc_gc_retain'):
            assert '@' + symbol + '(' not in body, (name, symbol)
        assert '.modvar.py_obj._STORE_PLAN_' not in body


@pytest.mark.parametrize('backend', range(5))
@pytest.mark.parametrize('count_free', [False, True])
@pytest.mark.parametrize('kind', ['root', 'field', 'sentinel'])
def test_locked_heap_barrier_dispatch_is_gc4_heap_only(backend, count_free, kind):
    # GC0 has no count-free StorePlan mode, regardless of the global flag.
    model = Store(backend=backend, count_free=count_free and backend != 0)
    model.init(owner=kind != 'root')
    assert model.commit(kind, model.VALUE) == 1
    assert model.locked_heap_barriers == int(backend == 4 and kind != 'root')
