"""Exact public scalar bodies over identical intrinsic state; not native evidence.

The baseline public body is frozen below. Its plan functions are the actual
production bodies loaded by Store, whose source identity is guarded separately.
"""
import ast
import itertools
from functools import lru_cache

from tests.python.test_gc4_count_free_store_plans import Store, SOURCE, FUNCTIONS

OLD_PUBLIC_SOURCE = '''
def pcc_gc_store_ptr(owner, slot, value) -> None:
    if ptr_is_null(slot):
        return
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = pcc_gc_backend()
    else:
        backend = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    if backend == 0:
        if load_i32(global_addr("pcc_diagnostics_runtime_log_fast_state"), 0) != 0:
            pcc_diagnostics_runtime_log_event_code(2, 3, backend, 0, owner)
        old = load_ptr(slot, 0)
        # This retaining store already owns the same reference. GC0 has no
        # tracing/relocation barrier to perform for an unchanged edge.
        if ptr_eq(old, value) != 0:
            return
        py_incref(value)
        store_ptr(slot, 0, value)
        py_decref(old)
        return
    plan = stack_alloc(128)
    pcc_gc_store_ptr_plan_init(plan, owner, backend)
    pcc_py_gc_minor_graph_lock()
    pcc_gc_store_ptr_plan_commit_locked(plan, owner, slot, value)
    pcc_py_gc_minor_graph_unlock()
    pcc_gc_store_ptr_plan_finish(plan)
'''


def public_body(source):
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.FunctionDef) and n.name == 'pcc_gc_store_ptr')
    node.decorator_list = []
    return node


@lru_cache(maxsize=1)
def plan_code():
    nodes = [n for n in ast.parse(SOURCE.read_text()).body
             if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS]
    assert {n.name for n in nodes} == FUNCTIONS
    for node in nodes:
        node.decorator_list = []
    return compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec')


@lru_cache(maxsize=2)
def public_code(source):
    return compile(ast.Module(body=[public_body(source)], type_ignores=[]),
                   '<public-store>', 'exec')


class PublicStore(Store):
    def __init__(self, *, source, backend, config, count_free, logging, forwarded, marking):
        count_free_requested = count_free
        count_free = count_free and backend != 0
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
        exec(plan_code(), self.ns)
        count_free = count_free_requested
        self.depth = 0
        self.allocations = 0
        self.globals.update(pcc_gc_config_initialized=config,
                            pcc_gc_backend_selected=backend,
                            pcc_gc_backend4_no_refcounts=int(count_free),
                            # Fixed with the collector, as pcc_gc_fix_backend
                            # sets it: count-free nonmoving GC1/GC2 only.
                            pcc_gc_nonmoving_count_free=int(
                                count_free and backend in (1, 2)))
        self.nonmoving_stores = []
        self.ns.update(stack_alloc=self.allocate,
                       _store_root_nonmoving_tracing=self.nonmoving_store,
                       pcc_gc_backend=self.initialize_backend,
                       pcc_py_gc_minor_graph_lock=self.lock,
                       pcc_py_gc_minor_graph_lock_for=self.lock_for,
                       pcc_py_gc_minor_graph_unlock=self.unlock,
                       py_incref=lambda v: self.events.append(('incref', v)),
                       py_decref=lambda v: self.events.append(('decref', v)))
        exec(public_code(source), self.ns)

    def initialize_backend(self):
        self.events.append('config-init')
        self.globals['pcc_gc_config_initialized'] = 1
        return self.backend

    def allocate(self, size):
        assert size == 128
        self.allocations += 1
        # Start with poison that resembles an address, not zeroed host storage.
        self.words.update({self.PLAN + i: self.VALUE for i in range(0, 128, 4)})
        return self.PLAN

    def lock_for(self, site):
        assert site == 3
        self.ns['pcc_py_gc_minor_graph_lock']()

    def lock(self):
        assert self.depth == 0
        self.depth += 1
        self.events.append('lock')

    def unlock(self):
        assert self.depth == 1
        self.depth -= 1
        self.events.append('unlock')

    def candidate(self, value):
        self.events.append(('candidate', value))
        return value == self.VALUE

    def nonmoving_store(self, slot, value, backend):
        # The unlocked GC1/GC2 store: its no-park scope, store note, slot
        # write and shade are covered by the native nonmoving-store tests.
        assert self.depth == 0
        self.nonmoving_stores.append((slot, value, backend))
        self.words[slot] = value

    def barrier(self, owner, slot, value):
        assert self.depth == 1
        super().barrier(owner, slot, value)

    def store(self, pointer, offset, value):
        if pointer + offset == self.SLOT and self.backend != 0:
            assert self.depth == 1
        super().store(pointer, offset, value)

    def snapshot(self):
        # All modeled observable mutable state; only private plan scratch and
        # its initialization bookkeeping differ by design.
        return (dict(self.globals), self.events, self.depth, self.old_reads,
                self.locked_heap_barriers, self.allocations, self.nonmoving_stores,
                {p: v for p, v in self.words.items()
                 if not self.PLAN <= p < self.PLAN + 128},
                self.ready, self.reenter_finish)


def test_public_scalar_store_exhaustive_differential():
    candidate = SOURCE.read_text()
    for backend, config, count_free, owner, slot, value, forwarded, marking, logging in itertools.product(
        range(5), (0, 1), (False, True), (0, Store.OWNER), (0, Store.SLOT),
        (0, 35, 2, 6, Store.VALUE, Store.OLD, Store.COPY),
        (False, True), (False, True), (False, True),
    ):
        options = dict(backend=backend, config=config, count_free=count_free,
                       logging=logging, forwarded=forwarded, marking=marking)
        before = PublicStore(source=OLD_PUBLIC_SOURCE, **options)
        after = PublicStore(source=candidate, **options)
        left = before.ns['pcc_gc_store_ptr'](owner, slot, value)
        right = after.ns['pcc_gc_store_ptr'](owner, slot, value)
        if slot and count_free and backend in (1, 2):
            # Count-free GC1/GC2 store through the unlocked nonmoving path:
            # no graph lock, no StorePlan scratch, the value written as is.
            assert left == right is None
            assert after.nonmoving_stores == [(slot, value, backend)]
            assert after.allocations == 0 and after.depth == 0
            assert 'lock' not in after.events
            assert after.events == (['config-init'] if not config else [])
            assert after.words[slot] == value
            continue
        assert left == right and before.snapshot() == after.snapshot(), (
            options, owner, slot, value, before.snapshot(), after.snapshot())
        if slot and backend == 4 and count_free:
            assert len(after.initialized) == 32
            assert before.words == after.words
            assert all(after.words[after.PLAN + i] == 0 for i in range(8, 112, 4))
            assert after.words[after.PLAN + 124] == 7
            assert after.old_reads == 0
            assert not any(isinstance(e, tuple) and e[0] in ('prepare', 'finish')
                           for e in after.events)


NONMOVING_BRANCH = '''
if (backend == 1 or backend == 2) and load_i32(global_addr("pcc_gc_nonmoving_count_free"), 0) != 0:
    _store_root_nonmoving_tracing(slot, value, backend)
    return
'''


def test_all_modes_retain_exact_generic_public_body():
    before = public_body(OLD_PUBLIC_SOURCE)
    after = public_body(SOURCE.read_text())
    # The scalar bypass removed the finish helper's compiled entry stop poll
    # and its committed-value root image. Restore the complete baseline path
    # for every collector that relocates. Count-free GC1/GC2 relocate
    # nothing, so no root image is owed; their one extra branch stores under
    # a no-park scope whose exit is the stop poll (pcc_thread_no_park_exit).
    branch = ast.parse(NONMOVING_BRANCH).body[0]
    extra = [i for i, stmt in enumerate(after.body)
             if ast.dump(stmt) == ast.dump(branch)]
    assert len(extra) == 1
    plan = next(i for i, stmt in enumerate(after.body)
                if isinstance(stmt, ast.Assign)
                and ast.unparse(stmt.targets[0]) == 'plan')
    # It follows GC0's counted path and precedes the StorePlan transaction.
    assert extra[0] == plan - 1
    del after.body[extra[0]]
    # Only the explicit store-site classification may differ from the
    # frozen semantic baseline, at this same transaction boundary.
    locks = [node for node in ast.walk(before) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)
             and node.func.id == 'pcc_py_gc_minor_graph_lock']
    assert len(locks) == 1 and not locks[0].args and not locks[0].keywords
    locks[0].func.id = 'pcc_py_gc_minor_graph_lock_for'
    locks[0].args = [ast.Constant(value=3)]
    assert ast.dump(before) == ast.dump(after)


def test_plan_dependencies_match_frozen_baseline():
    import hashlib
    # Hash exact frozen text, including decorators and internal comments.
    # AST serialization changes between Python versions; only source span
    # boundaries are taken from the parser, never its serialized node shape.
    expected = {
        'pcc_gc_store_root_plan_init': '33b3af8e1ae9df6e5396299b120b888acf64ebec8ed1c41aa6e4779d82f61c16',
        'pcc_gc_store_ptr_plan_init': '4fd9c5e92599156d5228d808f9e339243c6090f186f474c88f8169d03f15bbc4',
        '_pcc_gc_store_plan_commit_locked': 'dcd6f0eba1ba9d56e24415b24e4ff3023c85d272acc4f7b57537978ccc49ad44',
        'pcc_gc_store_ptr_plan_commit_locked': '3701c7755e7b2fedb58f5f8c97f3794f840c71b45787464a0bbe0ffef6427750',
        '_pcc_gc_store_plan_finish': 'cfbc5dccd47f8748479eb48c6051fc6aae53c5f4f4f605e9ebaef6addea78e04',
        'pcc_gc_store_ptr_plan_finish': 'a2d2d2e713fd19c5a0da9bf41542e32c9a615316a0958faf533869ac0c1a3891',
    }
    source = SOURCE.read_bytes().decode('utf-8')
    lines = source.splitlines(keepends=True)
    actual = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in expected:
            first_line = min([node.lineno] + [d.lineno for d in node.decorator_list])
            segment = ''.join(lines[first_line - 1:node.end_lineno])
            actual[node.name] = hashlib.sha256(segment.encode('utf-8')).hexdigest()
    assert actual == expected
