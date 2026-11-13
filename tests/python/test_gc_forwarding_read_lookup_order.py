"""Actual read-barrier bodies over guarded finite memory/index doubles.

No-chain compatibility and provenance ordering, not a historical one-hop
oracle. Chain semantics live in test_gc_relocation_read_chain.py. These are
host models, not native, concurrency, throughput or bootstrap evidence.
"""
import itertools
from pathlib import Path

from tests.python.runtime_body_model import load_functions

RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"
FORWARDING = RUNTIME / "freestanding_gc_forwarding_identity.py"
KNOWN = RUNTIME / "freestanding_gc_root_operations.py"


class State:
    def __init__(self, kind, flags, backend, count, retire_at_lock=False):
        # Every tracing collector is count-free; GC4 resolves a read only
        # in the stopped window between a move and its remap (remap owed,
        # 4e2feca72). GC3 moves nothing (03cbd1849).
        self.words = {('pcc_gc_backend_selected', 0): backend,
                      ('pcc_gc_relocation_barrier_forwards', 0): count,
                      ('pcc_gc_nonmoving_count_free', 0): int(backend != 0),
                      ('pcc_gc_backend4_remap_owed', 0): 1}
        self.index = {}
        self.forward = {}
        self.probes = []
        self.locks = []
        self.writes = []
        self.retire_at_lock = retire_at_lock
        self.obj = 0 if kind == 'null' else 35 if kind == 'tagged' else 0x1000
        if kind in ('known', 'removed', 'dead'):
            self.index[self.obj] = 0x2000
            self.words[(0x2000, 32)] = int(kind == 'removed')
            # Removed objects have no readable header in this model.
            if kind != 'removed':
                self.words[(self.obj, 12)] = flags | (524288 if kind == 'dead' else 0)
        self.ns = dict(i64=int, c_ptr=int, ptr_is_null=lambda p: int(p == 0),
                       is_tagged_int=lambda p: int(p & 1), null=lambda: 0,
                       global_addr=lambda n: n, load_ptr=self.load,
                       load_i32=self.load, load_i64=self.load, store_i32=self.store,
                       atomic_rmw_i32=self.atomic_and,
                       pcc_gc_object_index_find=self.find,
                       # Dictionary lookup models the stable index boundary;
                       # it is not a concurrent index-publication proof.
                       pcc_gc_object_index_find_stable=self.find,
                       pcc_gc_forwarding_index_find=self.find_forward,
                       pcc_py_gc_minor_graph_lock=self.lock,
                       pcc_py_gc_minor_graph_unlock=lambda: self.locks.append('unlock'))
        load_functions(KNOWN, {'pcc_gc_object_is_known_no_lock',
                               'pcc_gc_object_is_known_stable'}, self.ns)
        load_functions(FORWARDING, {'pcc_gc_note_relocation_read',
                                   '_note_relocation_read_unlocked'}, self.ns)

    def load(self, p, offset):
        assert (p, offset) in self.words, ('unsafe read', p, offset)
        return self.words[p, offset]

    def store(self, p, offset, value):
        assert (p, offset) in self.words, ('unsafe write', p, offset)
        self.words[p, offset] = value
        self.writes.append((p, offset, value))

    def atomic_and(self, operation, p, offset, value, order):
        # One indivisible current-word update; keep the guarded accesses and
        # write log shared with ordinary stores. No CPU ordering is modeled.
        assert operation == 'and' and order == 'relaxed'
        old = self.load(p, offset)
        self.store(p, offset, (old & value) & 0xffffffff)
        return old

    def find(self, obj):
        self.probes.append(('object', obj))
        return self.index.get(obj, 0)

    def find_forward(self, obj):
        self.probes.append(('forward', obj))
        return self.forward.get(obj, 0)

    def lock(self):
        self.locks.append('lock')
        if self.retire_at_lock:
            self.index.pop(self.obj, None)
            self.words.pop((self.obj, 12), None)


def test_no_edge_compatibility_and_guarded_headers():
    for kind, flags, backend, count, entry in itertools.product(
        ('null', 'tagged', 'known', 'removed', 'dead', 'unmapped'),
        (0, 2048, 8, 2056), range(5), (0, 7, 2147483646),
        ('_note_relocation_read_unlocked', 'pcc_gc_note_relocation_read'),
    ):
        state = State(kind, flags, backend, count)
        assert state.ns[entry](state.obj) == state.obj
        assert state.words['pcc_gc_relocation_barrier_forwards', 0] == count
        clears = (kind == 'known' and flags & 2048
                  and (entry == '_note_relocation_read_unlocked' or backend == 4))
        assert state.writes == ([(state.obj, 12, flags & ~2048)] if clears else [])
        if kind in ('null', 'tagged') or (entry == 'pcc_gc_note_relocation_read'
                                         and backend != 4):
            assert state.probes == [] and state.locks == []
        elif entry == '_note_relocation_read_unlocked':
            assert state.probes[0] == ('forward', state.obj)


def test_retirement_between_fast_check_and_graph_lock_does_not_read_header():
    for kind in ('known', 'removed', 'dead', 'unmapped'):
        state = State(kind, 2048, 4, 7, retire_at_lock=True)
        assert state.ns['pcc_gc_note_relocation_read'](state.obj) == state.obj
        assert state.writes == []
        assert state.locks == ['lock', 'unlock']


def test_known_unforwarded_read_keeps_fast_path():
    for backend in (4,):
        state = State('known', 8, backend, 7)
        assert state.ns['pcc_gc_note_relocation_read'](state.obj) == state.obj
        assert state.probes == [('object', state.obj)]
        assert state.locks == [] and state.writes == []
