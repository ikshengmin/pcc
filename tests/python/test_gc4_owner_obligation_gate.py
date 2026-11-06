"""Host execution of the GC4 owner gate, known check and atomic shade bodies.

These deterministic intrinsic models prove policy and skipped reads. They do
not establish native execution, concurrent collector progress or throughput.
"""
from collections import Counter
from functools import lru_cache
from itertools import product
from pathlib import Path
import ast


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime/py"
SOURCE = RUNTIME / "freestanding_gc_barrier_dispatcher.py"
WHITE, GRAY, BLACK, YOUNG, OLD, REMEMBERED, RELOCATION_CANDIDATE, TERMINAL = (
    8, 16, 32, 128, 256, 512, 2048, 524288
)


@lru_cache(maxsize=None)
def extracted(path, names):
    nodes = [node for node in ast.parse(path.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    for node in nodes:
        node.decorator_list = []
    return compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec")


class BarrierState:
    """Graph lock is already held; every intrinsic checks that ownership."""

    def __init__(self, active=0, owner_flags=YOUNG, value_flags=WHITE | YOUNG,
                 owner_kind="live", value_kind="live", enqueue=1, alias=False,
                 source=SOURCE):
        self.flags = {}
        self.index = {}
        self.nodes = {}
        self.events = []
        self.reads = Counter()
        self.provenance = set()
        self.depth = 2
        self.gray = 9
        self.enqueue_result = enqueue
        self.entries = set()
        self.globals = {"pcc_gc_mark_active": active,
                        "pcc_gc_backend4_genzgc_store_barriers": 7}
        self.owner = self.add(100, owner_flags, owner_kind)
        self.value = self.owner if alias else self.add(200, value_flags, value_kind)
        namespace = {
            "i64": int, "PYOBJECTHEADER_FLAGS_OFFSET": 12,
            "ptr_is_null": lambda p: int(p == 0),
            "is_tagged_int": lambda p: int(p % 2 != 0),
            "global_addr": lambda name: name,
            "load_i32": self.load, "load_i64": self.load,
            "store_i32": self.store,
            "atomic_load_i32": lambda p, o, order: self.load(p, o),
            "atomic_cas_i32": self.cas,
            "pcc_gc_object_index_find": self.find,
            "pcc_gc_gray_count_increment_acq_rel": self.increment,
            "pcc_gc_gray_count_decrement_acq_rel": self.decrement,
            "pcc_gc_backend4_store_buffer_enqueue": self.enqueue,
        }
        exec(extracted(RUNTIME / "freestanding_gc_root_operations.py", (
            "pcc_gc_object_is_known_no_lock", "pcc_gc_shade_gray_atomic",
        )), namespace)
        self.known_body = namespace["pcc_gc_object_is_known_no_lock"]
        self.shade_body = namespace["pcc_gc_shade_gray_atomic"]
        namespace["pcc_gc_object_is_known_no_lock"] = self.known
        namespace["pcc_gc_shade_gray_atomic"] = self.shade
        exec(extracted(source, ("pcc_gc_backend4_note_heap_store_locked",)), namespace)
        self.barrier = namespace["pcc_gc_backend4_note_heap_store_locked"]

    def add(self, address, flags, kind):
        if kind == "null":
            return 0
        if kind == "tagged":
            return 1
        if kind == "sentinel":
            return 2
        if kind == "unknown":
            return address
        self.flags[address] = flags | (TERMINAL if kind == "terminal" else 0)
        if kind != "untracked":
            self.index[address] = address + 1000
            self.nodes[address + 1000] = int(kind == "dead")
        return address

    def known(self, pointer):
        self.reads[("known", pointer)] += 1
        return self.known_body(pointer)

    def find(self, pointer):
        assert self.depth == 2
        self.reads[("index", pointer)] += 1
        node = self.index.get(pointer, 0)
        if node and not self.nodes[node]:
            self.provenance.add(pointer)
        return node

    def load(self, address, offset):
        assert self.depth == 2
        if isinstance(address, str):
            assert offset == 0
            self.reads[("global", address)] += 1
            return self.globals[address]
        if offset == 32:
            self.reads[("node", address)] += 1
            return self.nodes[address]
        assert offset == 12 and address in self.provenance, (
            "header read before live indexed provenance", address, offset
        )
        self.reads[("header", address)] += 1
        return self.flags[address]

    def store(self, address, offset, value):
        assert self.depth == 2 and isinstance(address, str) and offset == 0
        self.globals[address] = value
        self.events.append(("counter", address, value))

    def cas(self, pointer, offset, expected, desired, success, failure):
        old = self.load(pointer, offset)
        if old == expected:
            self.flags[pointer] = desired
        return old

    def increment(self):
        assert self.depth == 2
        self.gray += 1

    def decrement(self):
        assert self.depth == 2
        self.gray -= 1

    def shade(self, pointer, allow_black):
        self.events.append(("shade", pointer, allow_black))
        self.shade_body(pointer, allow_black)

    def enqueue(self, owner, slot, value):
        assert self.depth == 2
        self.events.append(("enqueue", owner, slot, value))
        if self.enqueue_result == 0 or (owner, slot, value) in self.entries:
            return 0
        self.entries.add((owner, slot, value))
        self.flags[owner] |= REMEMBERED
        return 1

    def run(self):
        self.barrier(self.owner, 300, self.value)
        assert self.depth == 2

    def state(self):
        return (self.flags, self.globals, self.gray, self.entries,
                self.events, self.depth)


def flag_words(bits):
    return tuple(sum(bit for bit, selected in zip(bits, pattern) if selected)
                 for pattern in product((0, 1), repeat=len(bits)))


def test_all_color_generation_obligations_and_repeat_counts():
    flags = flag_words((WHITE, GRAY, BLACK, YOUNG, OLD))
    for active, owner, value, enqueue in product((0, 1, -1), flags, flags, (0, 1)):
        state = BarrierState(active, owner, value, enqueue=enqueue)
        state.run()
        expected_shade_call = bool(active and owner & BLACK and value & WHITE)
        expected_gray = expected_shade_call and not (value & (GRAY | BLACK))
        expected_enqueue = bool(owner & OLD and value & YOUNG)
        assert state.gray == 9 + expected_gray, (active, owner, value, enqueue)
        assert state.globals["pcc_gc_backend4_genzgc_store_barriers"] == (
            7 + bool(expected_enqueue and enqueue)
        )
        assert state.globals["pcc_gc_mark_active"] == active
        assert ("shade", 200, 0) in state.events if expected_shade_call else (
            not any(event[0] == "shade" for event in state.events)
        )
        assert bool(state.entries) == bool(expected_enqueue and enqueue)
        state.run()
        assert state.gray == 9 + expected_gray
        assert state.globals["pcc_gc_backend4_genzgc_store_barriers"] == (
            7 + bool(expected_enqueue and enqueue)
        )


def test_owner_without_obligation_never_probes_value():
    for active, owner in product((0, 1), flag_words((WHITE, GRAY, BLACK, YOUNG))):
        if active and owner & BLACK:
            continue
        for kind in ("live", "dead", "terminal", "untracked", "unknown",
                     "null", "tagged", "sentinel"):
            state = BarrierState(active, owner, WHITE | YOUNG, value_kind=kind)
            state.run()
            assert state.reads[("known", state.owner)] == 1
            assert state.reads[("known", state.value)] == 0
            assert state.reads[("index", state.value)] == 0
            assert state.reads[("header", state.value)] == 0
            assert not state.events and state.gray == 9


def test_unknown_dead_and_terminal_guards_keep_headers_safe():
    kinds = ("live", "dead", "terminal", "untracked", "unknown",
             "null", "tagged", "sentinel")
    for owner_kind, value_kind, active in product(kinds, kinds, (0, 1)):
        state = BarrierState(active, BLACK | OLD, WHITE | YOUNG,
                             owner_kind, value_kind)
        before = state.state()
        state.run()
        if owner_kind != "live" or value_kind != "live":
            assert not state.events and state.gray == 9
            assert state.globals["pcc_gc_backend4_genzgc_store_barriers"] == 7
        if owner_kind != "live":
            # For both null or both sentinels the same address denotes the
            # owner probe, so check only distinct pointers here.
            if state.owner != state.value:
                assert state.reads[("known", state.value)] == 0
        assert state.depth == before[-1]


def test_self_alias_and_non_policy_header_bits():
    flags = flag_words((WHITE, GRAY, BLACK, YOUNG, OLD, REMEMBERED, RELOCATION_CANDIDATE, TERMINAL))
    for active, flags_word in product((0, 1), flags):
        state = BarrierState(active, flags_word, alias=True)
        state.run()
        # A self-edge whose owner is black cannot newly shade itself gray,
        # including deliberately inconsistent white+black flag words.
        assert state.gray == 9
        expected = bool(flags_word & OLD and flags_word & YOUNG
                        and not flags_word & TERMINAL)
        assert bool(state.entries) == expected
        assert state.globals["pcc_gc_mark_active"] == active


def test_known_lookup_closure_is_read_only():
    sources = {
        RUNTIME / "freestanding_gc_root_operations.py": (
            "pcc_gc_object_is_known_no_lock",
        ),
        RUNTIME / "freestanding_gc_index_table.py": (
            "pcc_gc_object_index_find", "pcc_gc_index_py_find_published",
            "pcc_gc_index_py_find_slot", "pcc_gc_index_py_hash_ptr",
        ),
    }
    allowed = {"ptr_is_null", "is_tagged_int", "null", "load_i32", "load_i64",
               "load_ptr", "global_addr", "atomic_load_i64", "int_to_ptr",
               "ptr_eq", "ptr_diff", "logical_shift_right_i64", "wrapping_mul_i64"}
    allowed.update(name for names in sources.values() for name in names)
    for path, names in sources.items():
        functions = [node for node in ast.parse(path.read_text()).body
                     if isinstance(node, ast.FunctionDef) and node.name in names]
        assert {node.name for node in functions} == set(names)
        for node in functions:
            node.decorator_list = []
            for call in ast.walk(node):
                if isinstance(call, ast.Call):
                    assert isinstance(call.func, ast.Name) and call.func.id in allowed
