"""Execute actual original/candidate AST bodies over guarded finite memory.

Host semantic differential only: index primitives and locks are modeled.
No native collector, concurrency, throughput or bootstrap acceptance claim.
"""
import ast
import itertools
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime/py"
FORWARDING = RUNTIME / "freestanding_gc_forwarding_identity.py"
KNOWN = RUNTIME / "freestanding_gc_root_operations.py"
# Historical order retained solely as a semantic differential oracle.
BASELINE = """
def _note_relocation_read_unlocked(obj: c_ptr) -> c_ptr:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return obj
    if pcc_gc_object_is_known_no_lock(obj) == 0:
        unknown_node = pcc_gc_forwarding_find(obj)
        if ptr_is_null(unknown_node) == 0:
            unknown_target = load_ptr(unknown_node, 8)
            if ptr_is_null(unknown_target) == 0:
                count: i64 = load_i32(
                    global_addr("pcc_gc_relocation_barrier_forwards"), 0
                )
                store_i32(
                    global_addr("pcc_gc_relocation_barrier_forwards"),
                    0,
                    count + 1,
                )
                return unknown_target
        return obj
    flags: i64 = load_i32(obj, 12)
    node = pcc_gc_forwarding_find(obj)
    if ptr_is_null(node) == 0:
        target = load_ptr(node, 8)
        if ptr_is_null(target) == 0:
            count2: i64 = load_i32(
                global_addr("pcc_gc_relocation_barrier_forwards"), 0
            )
            store_i32(
                global_addr("pcc_gc_relocation_barrier_forwards"), 0, count2 + 1
            )
            return target
    if (flags & 2048) != 0:
        store_i32(obj, 12, flags & ~2048)
    return obj
"""
NAMES = {'pcc_gc_forwarding_find', '_note_relocation_read_unlocked',
         'pcc_gc_note_relocation_read', '_graph_lock', '_graph_unlock'}

@lru_cache(maxsize=4)
def functions(path, names, baseline=False):
    nodes = [n for n in ast.parse(path.read_text()).body
             if isinstance(n, ast.FunctionDef) and n.name in names]
    if baseline:
        old = ast.parse(BASELINE).body[0]
        nodes = [old if n.name == old.name else n for n in nodes]
    assert {n.name for n in nodes} == names
    for n in nodes:
        n.decorator_list = []
    return compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec')

class State:
    def __init__(self, source, kind, flags, target, backend, count, retire_at_lock=False):
        self.words = {('pcc_gc_backend_selected', 0): backend,
                      ('pcc_gc_relocation_barrier_forwards', 0): count}
        self.index = {}
        self.forward = {}
        self.probes = 0
        self.forward_probes = 0
        self.locks = []
        self.writes = []
        self.retire_at_lock = retire_at_lock
        self.obj = 0 if kind == 'null' else 35 if kind == 'tagged' else 0x1000
        if kind in ('known', 'removed', 'dead'):
            self.index[self.obj] = 0x2000
            self.words[(0x2000, 32)] = int(kind == 'removed')
            self.words[(self.obj, 12)] = flags | (524288 if kind == 'dead' else 0)
        if target != 'absent':
            self.forward[self.obj] = 0x3000
            self.words[(0x3000, 8)] = {'null': 0, 'self': self.obj,
                                        'live': 0x4000, 'chain': 0x4000}[target]
            if target == 'chain':
                self.forward[0x4000] = 0x5000
                self.words[(0x5000, 8)] = 0x6000
        self.ns = dict(i64=int, c_ptr=int, ptr_is_null=lambda p: int(p == 0),
                       is_tagged_int=lambda p: int(p & 1), null=lambda: 0,
                       global_addr=lambda n: n, load_ptr=self.load,
                       load_i32=self.load, load_i64=self.load, store_i32=self.store,
                       pcc_gc_object_index_find=self.find,
                       pcc_gc_forwarding_index_find=self.find_forward,
                       pcc_py_gc_minor_graph_lock=self.lock,
                       pcc_py_gc_minor_graph_unlock=lambda: self.locks.append('unlock'))
        exec(functions(KNOWN, frozenset({'pcc_gc_object_is_known_no_lock'})), self.ns)
        exec(functions(FORWARDING, frozenset(NAMES), baseline=source), self.ns)

    def load(self, p, offset):
        assert (p, offset) in self.words, ('unsafe read', p, offset)
        return self.words[p, offset]

    def store(self, p, offset, value):
        assert (p, offset) in self.words, ('unsafe write', p, offset)
        self.words[p, offset] = value
        self.writes.append((p, offset, value))

    def find(self, obj):
        self.probes += 1
        return self.index.get(obj, 0)

    def find_forward(self, obj):
        self.forward_probes += 1
        return self.forward.get(obj, 0)

    def lock(self):
        self.locks.append('lock')
        if self.retire_at_lock:
            self.index.pop(self.obj, None)
            self.words.pop((self.obj, 12), None)

    def run(self, entry):
        result = self.ns[entry](self.obj)
        return result, self.words, self.writes, self.locks

def test_forwarding_first_matches_checked_historical_order():
    original, candidate = True, False
    cases = 0
    saved = 0
    kinds = ('null', 'tagged', 'known', 'removed', 'dead', 'unmapped')
    targets = ('absent', 'null', 'live', 'self', 'chain')
    for args in itertools.product(kinds, (0, 2048, 8, 2056), targets,
                                  range(5), (0, 7, 2147483646)):
        for entry in ('_note_relocation_read_unlocked', 'pcc_gc_note_relocation_read'):
            x, y = State(original, *args), State(candidate, *args)
            assert x.run(entry) == y.run(entry), (args, entry)
            assert x.forward_probes == y.forward_probes, (args, entry)
            assert y.probes <= x.probes, (args, entry)
            saved += x.probes - y.probes
            cases += 1
    for kind in ('known', 'removed', 'dead', 'unmapped'):
        for target in targets:
            args = kind, 2048, target, 4, 7, True
            x, y = State(original, *args), State(candidate, *args)
            assert x.run('pcc_gc_note_relocation_read') == y.run('pcc_gc_note_relocation_read')
            cases += 1
    for kind in ('known', 'unmapped'):
        args = kind, 2048, 'chain', 4, 7
        x, y = State(original, *args), State(candidate, *args)
        rx, ry = x.run('_note_relocation_read_unlocked'), y.run('_note_relocation_read_unlocked')
        assert rx == ry and ry[0] == 0x4000
        assert y.words['pcc_gc_relocation_barrier_forwards', 0] == 8
        assert (x.probes, y.probes) == (1, 0)
    assert cases == 3620
    assert saved == 972


if __name__ == '__main__':
    test_forwarding_first_matches_checked_historical_order()
    print('3620 actual-source host differential cases passed')
