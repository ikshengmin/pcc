"""Bounded host differential for the actual raw root-membership bodies.

The pointer primitives model 64-bit raw addresses, not C pointer subtraction.
The independent oracle enumerates addresses using Python integers modulo
2**64; it neither imports a historical checkout nor uses the optimized delta
formula. This proves finite control-flow cases only, not native execution.
"""
from __future__ import annotations

import ast
from collections import Counter
from functools import lru_cache
from pathlib import Path
import random

import pytest

from test_gc4_scalar_load_fastpath_model import ENTRY_POINTS, _LoadModel


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = "independent-enumerated-address-oracle"
AFTER = ROOT / "pcc/runtime/py/freestanding_gc_root_introspection.py"
REGISTRY = ROOT / "pcc/runtime/py/freestanding_gc_root_registry.py"
MASK = (1 << 64) - 1
SIGN = 1 << 63
SPAN = "pcc_gc_root_slot_in_span"
CALLER = "pcc_gc_slot_is_runtime_root"


def enumerated_membership(slot, base, count, counts):
    """Mathematical address-set oracle, independent of runtime intrinsics."""
    modulus = 2 ** 64
    slot %= modulus
    base %= modulus
    if slot == 0 or base == 0 or count <= 0:
        return 0
    for index in range(count):
        counts["slot_steps"] += 1
        counts["slot_comparisons"] += 1
        if (base + index * 8) % modulus == slot:
            return 1
    return 0


def signed64(value):
    value &= MASK
    return value - (1 << 64) if value & SIGN else value


@lru_cache(maxsize=None)
def code(path, names):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in selected} == set(names)
    for node in selected:
        node.decorator_list = []
    return compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec")


class SpanModel:
    def __init__(self, path):
        self.counts = Counter()
        self.ns = {
            "i64": int,
            "ptr_is_null": lambda p: int((p & MASK) == 0),
            "ptr_add": self.ptr_add,
            "ptr_diff": self.ptr_diff,
            "ptr_eq": self.ptr_eq,
            "logical_shift_right_i64": self.lshr,
        }
        if path == REFERENCE:
            self.ns[SPAN] = lambda slot, base, count: enumerated_membership(
                slot, base, count, self.counts
            )
        else:
            exec(code(path, (SPAN,)), self.ns)

    def ptr_add(self, base, offset):
        self.counts["slot_steps"] += 1
        return (base + signed64(offset)) & MASK

    def ptr_diff(self, left, right):
        self.counts["pointer_differences"] += 1
        return signed64((left & MASK) - (right & MASK))

    def ptr_eq(self, left, right):
        self.counts["slot_comparisons"] += 1
        return int((left & MASK) == (right & MASK))

    def lshr(self, value, shift):
        self.counts["logical_shifts"] += 1
        assert shift == 3
        return (value & MASK) >> shift

    def run(self, slot, base, count):
        assert -(1 << 63) <= count < (1 << 63)
        return self.ns[SPAN](slot & MASK, base & MASK, count)


def compare_case(slot, base, count):
    old, new = SpanModel(REFERENCE), SpanModel(AFTER)
    expected = old.run(slot, base, count)
    result = new.run(slot, base, count)
    assert result == expected, (hex(slot & MASK), hex(base & MASK), count)
    guarded = not (slot & MASK) or not (base & MASK) or count <= 0
    if guarded:
        assert not old.counts and not new.counts
    elif count <= 100000:
        assert new.counts["slot_steps"] == new.counts["slot_comparisons"] == 0
        assert new.counts["pointer_differences"] == 1
        assert new.counts["logical_shifts"] == int(((slot - base) & 7) == 0)
        assert old.counts["slot_steps"] == old.counts["slot_comparisons"]
    else:
        assert new.counts == old.counts
        assert new.counts["pointer_differences"] == 0
    return expected


@pytest.mark.parametrize("region", [0x1000, SIGN - 80, SIGN + 80, (1 << 64) - 80])
def test_all_relative_residues_and_small_boundaries(region):
    for residue in range(8):
        base = (region + residue) & MASK
        for count in [-1, 0, 1, 2, 3, 16, 17]:
            for offset in range(-10, max(1, count) * 8 + 10):
                compare_case(base + offset, base, count)


@pytest.mark.parametrize("base", [1, 0x1003, SIGN - 19, SIGN + 3, MASK - 19])
@pytest.mark.parametrize("count", [99999, 100000, 100001])
def test_admission_boundary_keeps_large_direct_call_loop(base, count):
    for offset in [-8, -1, 0, 1, (count - 1) * 8, count * 8 - 1, count * 8]:
        compare_case(base + offset, base, count)


def test_null_and_full_signed_count_guards():
    for count in [-(1 << 63), -100001, -100000, -1, 0, 1, 100000, 100001, (1 << 63) - 1]:
        compare_case(0, 0x1003, count)
        compare_case(0x1003, 0, count)
        compare_case(0, 0, count)
        if count <= 0:
            compare_case(0x1003, 0x1003, count)


def test_huge_direct_counts_keep_early_match_and_original_steps():
    # A huge miss would intentionally run the unchanged baseline for ages.
    for count in [100001, 1 << 60, 1 << 61, (1 << 63) - 1]:
        for offset in [0, 8, 16]:
            assert compare_case(0x1003 + offset, 0x1003, count) == 1


def test_signed_boundary_wrap_and_relative_alignment_counterexamples():
    cases = [
        (SIGN + 3, SIGN - 13, 3, 1),
        (11, MASK - 12, 4, 1),
        (0, MASK - 7, 2, 0),  # Keep null-slot guard even when span reaches null.
        (8, MASK - 7, 3, 1),
        (0x1013, 0x1003, 3, 1),  # Absolute address is deliberately unaligned.
        (0x1010, 0x1003, 3, 0),
        (0x1003 - 8, 0x1003, 100000, 0),
        (0x1003 + SIGN, 0x1003, 100000, 0),
    ]
    for slot, base, count, expected in cases:
        assert compare_case(slot, base, count) == expected


def test_deterministic_wide_address_differential():
    rng = random.Random(0x08661CBC)
    for _ in range(2000):
        base = rng.getrandbits(64)
        count = rng.randrange(1, 513)
        if rng.randrange(3):
            slot = (base + rng.randrange(-16, count * 8 + 16)) & MASK
        else:
            slot = rng.getrandbits(64)
        compare_case(slot, base, count)


def test_null_count_guard_and_large_count_loop_keep_original_structure():
    # A source-level contract in addition to the independent result oracle.
    # Direct counts outside normal map admission must keep the old algorithm.
    expected = ast.parse('''
def original_shape(slot, slots, count):
    if ptr_is_null(slot) != 0 or ptr_is_null(slots) != 0 or count <= 0:
        return 0
    i: i64 = 0
    while i < count:
        if ptr_eq(ptr_add(slots, i * 8), slot) != 0:
            return 1
        i = i + 1
    return 0
''').body[0]
    actual = next(n for n in ast.parse(AFTER.read_text()).body
                  if isinstance(n, ast.FunctionDef) and n.name == SPAN)
    assert ast.dump(expected.body[0]) == ast.dump(actual.body[0])
    assert [ast.dump(n) for n in expected.body[1:]] == [ast.dump(n) for n in actual.body[2:]]


@pytest.mark.parametrize("encoded", [-(1 << 31), -100001, -100000, -1, 0, 1, 100000, 100001, (1 << 31) - 1])
def test_actual_map_decoder_proves_production_count_bound(encoded):
    ns = {"i64": int, "ptr_is_null": lambda p: int(p == 0), "load_i32": lambda p, o: encoded}
    exec(code(REGISTRY, ("pcc_gc_root_slot_count_from_map",)), ns)
    expected = abs(encoded) if 1 <= abs(encoded) <= 100000 else 0
    assert ns["pcc_gc_root_slot_count_from_map"](1) == expected
    assert ns["pcc_gc_root_slot_count_from_map"](0) == 0


class RegistryModel(SpanModel):
    def __init__(self, path, *, frames=(), continuations=(), scheduler=(), native=()):
        super().__init__(path)
        self.events = []
        self.memory = {}
        self.globals = {}
        self.depth = 0
        self.scheduler_depth = 0
        self.continuation_depth = 0
        self.native = set(native)
        self.address = 0x10000000
        self.add_chain("pcc_gc_frame_head", frames, 40)
        self.add_chain("pcc_gc_continuation_root_head", continuations, 24)
        self.add_chain("pcc_gc_scheduler_root_head", [(s, 0) for s in scheduler], None)
        self.ns.update(
            gc_backend_current=lambda: self.note("backend", 4),
            pcc_thread_native_root_contains=self.native_contains,
            pcc_py_gc_minor_graph_lock=self.lock,
            pcc_py_gc_minor_graph_unlock=self.unlock,
            pcc_gc_scheduler_root_list_acquire=self.scheduler_lock,
            pcc_gc_scheduler_root_list_release=self.scheduler_unlock,
            pcc_gc_continuation_root_list_acquire=self.continuation_lock,
            pcc_gc_continuation_root_list_release=self.continuation_unlock,
            global_load_ptr=self.global_read,
            load_ptr=self.read,
            load_i64=self.read,
        )
        # Both sides execute the current real caller; only its span predicate
        # differs, independent oracle versus actual runtime helper.
        exec(code(AFTER, (CALLER,)), self.ns)

    def add_chain(self, name, items, count_offset):
        head = 0
        for base, count in reversed(items):
            node = self.address
            self.address += 128
            if count_offset is None:
                self.memory[node, 0] = base & MASK
                self.memory[node, 8] = head
            else:
                self.memory[node, 8] = base & MASK
                self.memory[node, 16] = head
                self.memory[node, count_offset] = count
            head = node
        self.globals[name] = head

    def note(self, event, *args):
        self.events.append((event, *args))
        return args[-1] if args else None

    def native_contains(self, slot):
        self.events.append(("native", slot))
        assert self.depth == self.scheduler_depth == 0
        return int(slot in self.native)

    def lock(self):
        assert self.depth == self.scheduler_depth == 0
        self.depth = 1
        self.events.append(("lock",))

    def unlock(self):
        assert self.depth == 1 and self.scheduler_depth == self.continuation_depth == 0
        self.depth = 0
        self.events.append(("unlock",))

    def scheduler_lock(self):
        assert self.depth == 1 and self.scheduler_depth == 0
        self.scheduler_depth = 1
        self.events.append(("scheduler_lock",))

    def scheduler_unlock(self):
        assert self.depth == self.scheduler_depth == 1
        self.scheduler_depth = 0
        self.events.append(("scheduler_unlock",))

    def continuation_lock(self):
        # The continuation list's own word, taken inside the graph lock.
        assert self.depth == 1 and self.scheduler_depth == self.continuation_depth == 0
        self.continuation_depth = 1
        self.events.append(("continuation_lock",))

    def continuation_unlock(self):
        assert self.depth == self.continuation_depth == 1
        self.continuation_depth = 0
        self.events.append(("continuation_unlock",))

    def global_read(self, name):
        assert self.depth == 1
        if name == "pcc_gc_scheduler_root_head":
            assert self.scheduler_depth == 1
        if name == "pcc_gc_continuation_root_head":
            assert self.continuation_depth == 1
        value = self.globals[name]
        self.events.append(("global", name, value))
        return value

    def read(self, node, offset):
        assert self.depth == 1
        value = self.memory[node, offset]
        self.events.append(("metadata", node, offset, value))
        return value

    def contains(self, slot):
        value = self.ns[CALLER](slot & MASK)
        assert self.depth == self.scheduler_depth == 0
        return value


REGISTRIES = [
    {},
    {"frames": [(0x1003, 3)]},
    {"continuations": [(0x1003, 3)]},
    {"frames": [(0x1003, 3), (0x100B, 4)], "continuations": [(0x1003, 8)]},
    {"frames": [(0x1003, 0), (0x1003, -1)], "continuations": [(0x1003, 3)]},
    {"frames": [(SIGN - 13, 4)], "continuations": [(MASK - 12, 4)]},
    {"frames": [(0, 3), (0x1000, 2)], "scheduler": [0x1003, 0x1013]},
    {"frames": [(0x1003, 3)], "native": [0x1003], "scheduler": [0x1003]},
    {"frames": [(0x1003, 3)], "native": [0x1003], "scheduler": [0x3003]},
]


@pytest.mark.parametrize("layout", REGISTRIES)
def test_actual_registry_caller_preserves_overlap_order_locks_and_shortcuts(layout):
    slots = [0, 3, 11, 0x1000, 0x1003, 0x100B, 0x1010, 0x1013, 0x101B, 0x103B, 0x3003,
             SIGN - 13, SIGN + 3, MASK - 12]
    for slot in slots:
        old, new = RegistryModel(REFERENCE, **layout), RegistryModel(AFTER, **layout)
        assert new.contains(slot) == old.contains(slot)
        assert new.events == old.events
        assert new.memory == old.memory and new.globals == old.globals


@pytest.mark.parametrize("entry", ENTRY_POINTS)
@pytest.mark.parametrize("kind", ["frame", "continuation", "overlap", "native", "scheduler", "miss", "misaligned"])
def test_actual_gc4_load_entries_use_real_registry_with_identical_effects(entry, kind):
    slot = _LoadModel.SLOT
    layouts = {
        "frame": {"frames": [(slot - 8, 3)]},
        "continuation": {"continuations": [(slot - 8, 3)]},
        "overlap": {"frames": [(slot - 8, 3), (slot, 1)], "continuations": [(slot, 2)]},
        "native": {"native": [slot]},
        "scheduler": {"scheduler": [slot]},
        "miss": {"frames": [(slot + 8, 3)]},
        "misaligned": {"frames": [(slot - 7, 3)]},
    }
    for population, pending, value in [(0, 1, _LoadModel.OBJECT), (0, 0, _LoadModel.OBJECT),
                                       (1, 1, _LoadModel.OBJECT), (0, 1, 0), (0, 1, 3)]:
        states = []
        for path in [REFERENCE, AFTER]:
            registry = RegistryModel(path, **layouts[kind])
            load = _LoadModel(value=value, population=population, pending=pending,
                              candidate=True, forwarded=bool(population))
            def membership(slot, load=load, registry=registry):
                load.note("root_membership", slot)
                return registry.contains(slot)
            load.namespace["pcc_gc_slot_is_runtime_root"] = membership
            result = load.run(entry)
            expected_membership = int(population == 0 and pending and value == load.OBJECT)
            assert load.counts["root_membership"] == expected_membership
            assert bool(registry.events) == bool(expected_membership)
            states.append((result, load.cells, load.events, load.counts, registry.events,
                           registry.depth, registry.scheduler_depth))
        assert states[0] == states[1]


def test_registered_large_span_has_constant_primitive_work_without_timing_claim():
    old = RegistryModel(REFERENCE, frames=[(0x1003, 100000)])
    new = RegistryModel(AFTER, frames=[(0x1003, 100000)])
    slot = 0x1003 + 99999 * 8
    assert old.contains(slot) == new.contains(slot) == 1
    assert old.counts["slot_steps"] == 100000
    assert old.counts["slot_comparisons"] == 100000
    assert new.counts["slot_steps"] == new.counts["slot_comparisons"] == 0
    assert new.counts["pointer_differences"] == new.counts["logical_shifts"] == 1
    assert old.events == new.events


def test_runtime_caller_preserves_100001_fallback_steps_and_scheduler_miss():
    for slot in [0x1003 + 100000 * 8, 0x1003 + 100001 * 8]:
        old = RegistryModel(REFERENCE, continuations=[(0x1003, 100001)])
        new = RegistryModel(AFTER, continuations=[(0x1003, 100001)])
        assert old.contains(slot) == new.contains(slot)
        assert old.events == new.events
        assert old.counts == new.counts
        assert new.counts["slot_steps"] == 100001
        assert new.counts["pointer_differences"] == 0
