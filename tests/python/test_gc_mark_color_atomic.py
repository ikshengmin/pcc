"""Live collector tracking and reachability; retains the shared header model."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.python.runtime_body_model import load_functions

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime/py"
TRACKING = RUNTIME / "freestanding_gc_tracking.py"
TRACKED = 2
FINALIZED = 4
PINNED = 64
# Bit patterns retained by the shared interleaving fixture while it migrates.
GRAY = 16
BLACK = 32
DYING = 524288


def _function(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == name)


def _load(path, name, namespace):
    node = _function(path, name)
    # An export decorator registers a symbol; it is not part of the body.
    node.decorator_list = []
    module = ast.Module(body=[node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


def _i32(value):
    value &= 0xFFFFFFFF
    return value if value < 0x80000000 else value - 0x100000000


class HeaderMemory:
    def __init__(self, initial, interference=()):
        self.obj = {8: 7, 12: _i32(initial)}
        self.interference = list(interference)
        self.states = [self.obj[12]]
        self.cas_calls = []
        self.loads = 0
        self.writes = []
        self.gray_worklist_events = []

    def update(self, operation):
        kind, mask = operation
        old = self.obj[12]
        self.obj[12] = _i32(old | mask if kind == "or" else old & mask)
        self.states.append(self.obj[12])

    def load(self, obj, offset, order):
        assert obj is self.obj and offset == 12 and order == "acquire"
        self.loads += 1
        return obj[offset]

    def cas(self, obj, offset, expected, desired, order, fail_order):
        assert obj is self.obj and offset == 12
        assert (order, fail_order) == ("acq_rel", "acquire")
        if self.interference:
            self.update(self.interference.pop(0))
        old = obj[offset]
        self.cas_calls.append((expected, desired, old))
        if old == _i32(expected):
            obj[offset] = _i32(desired)
            self.states.append(obj[offset])
            self.writes.append("cas")
        return old

    def rmw(self, operation, obj, offset, value, order):
        assert obj is self.obj and offset == 12 and order == "acq_rel"
        old = obj[offset]
        self.update((operation, value))
        self.writes.append(operation)
        return old

    def namespace(self):
        namespace = {
            "i64": int, "PYOBJECTHEADER_FLAGS_OFFSET": 12,
            "atomic_load_i32": self.load, "atomic_cas_i32": self.cas,
            "atomic_rmw_i32": self.rmw,
        }
        return namespace


def _layout_constants():
    tree = ast.parse(TRACKING.read_text(encoding="utf-8"))
    return {
        node.target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, int)
    }


def test_tracking_header_writes_are_exact_masked_atomic_operations():
    tree = ast.parse(TRACKING.read_text(encoding="utf-8"))
    constants = _layout_constants()
    assert constants["_TRACKED"] == TRACKED

    YOUNG = 131072  # pcc_gc_tracking_young_bit on the fused collector

    def mask_value(node):
        if isinstance(node, ast.Name):
            # ``young`` is the loaded pcc_gc_tracking_young_bit.
            return YOUNG if node.id == "young" else constants[node.id]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return mask_value(node.left) | mask_value(node.right)
        if isinstance(node, ast.Call):
            assert ast.unparse(node) == "load_i32(global_addr('pcc_gc_tracking_young_bit'), 0)"
            return YOUNG
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert):
            return ~mask_value(node.operand)
        return ast.literal_eval(node)

    writes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id == "atomic_rmw_i32":
            if ast.unparse(node.args[1]) == "obj":
                assert ast.literal_eval(node.args[2]) == 12
                assert ast.literal_eval(node.args[4]) == "acq_rel"
                writes.append((ast.literal_eval(node.args[0]), mask_value(node.args[3])))
            else:
                # Shared accounting moved to atomics; it is not a header
                # write and must target one of the named counter cells.
                assert ast.unparse(node.args[1]) in {
                    "global_addr('py_gc_tracked_count')",
                    "global_addr('pcc_gc_tracked_node_pool_count')",
                    "global_addr('pcc_gc_young_log_chunk_count')",
                    "count",  # young-log reservation counter, not object memory
                }
                assert ast.literal_eval(node.args[2]) == 0
                assert ast.literal_eval(node.args[0]) in ("add", "sub")
                assert ast.literal_eval(node.args[4]) == "acq_rel"
        elif node.func.id == "store_i32":
            assert ast.unparse(node.args[0]) != "obj"
    # Node-path untrack/track (two ors: tracked and already-indexed), plus
    # flag-mode entry and the first-freeze race recheck under shard ownership.
    # A first track also sets the young bit (flag mode and the node path);
    # every view teardown clears it, which promotes the survivors.
    assert sorted(writes) == (
        [("and", ~YOUNG)] + [("and", ~TRACKED)] * 3
        + [("or", TRACKED)] * 2 + [("or", TRACKED | YOUNG)] * 2
    )


@pytest.mark.parametrize("subset", [False, True])
def test_single_collector_marks_gc_nodes_not_object_headers(subset):
    """Exercise the real traversal, including a cycle and a non-owning slot."""
    objects = ("root", "child", "borrowed", "outside", "full-view")
    class Header(dict):
        __hash__ = object.__hash__
        __eq__ = object.__eq__

    flags = FINALIZED | TRACKED | (1 << 31)
    headers = {obj: Header({12: flags}) for obj in objects}
    object_names = {header: obj for obj, header in headers.items()}
    slots = {
        "root": [("child", 0), ("borrowed", 3), ("outside", 0), ("full-view", 0)],
        "child": [("root", 0)],
    }
    # The runtime's GC-node word encodes subset membership, full-view
    # ownership, and unrelated bookkeeping independently of reachability.
    reachable, candidate, full_view, keep = 1, 4, 64, 256
    original = (candidate | keep, candidate | keep, candidate, 0, candidate | full_view)
    nodes = {obj: {16: bits} for obj, bits in zip(objects, original, strict=True)}
    globals_ = {
        "pcc_gc_full_marking": {0: 0},
        "pcc_gc_backend0_subset_active": {0: int(subset)},
    }
    visited = []

    def visit(obj, visitor, ctx):
        name = object_names[obj]
        visited.append(name)
        for child, role in slots.get(name, ()):
            visitor(headers[child], role, ctx)

    def store(base, index, value):
        base[index] = value

    namespace = {
        "i64": int, "null": lambda: None, "ptr_is_null": lambda value: int(value is None),
        "ptr_eq": lambda left, right: int(left is right), "is_tagged_int": lambda obj: 0,
        "global_addr": globals_.__getitem__,
        "py_gc_index_find": lambda obj: nodes.get(object_names[obj]),
        "stack_alloc": lambda n: {},
        "load_i32": lambda base, index: base[index],
        "load_i64": lambda base, index: base[index],
        "load_ptr": lambda base, index: base[index],
        "store_i32": store, "store_i64": store, "store_ptr": store,
        "pcc_gc_load_ptr": lambda owner, slot: slot,
        "pcc_gc_visit_object_slots": visit,
    }
    load_functions(RUNTIME / "freestanding_gc_backend0_slots.py",
                   ("pcc_gc_backend0_mark_reachable", "pcc_gc_backend0_is_unreachable"),
                   namespace)
    mark = namespace["pcc_gc_backend0_mark_reachable"]
    is_unreachable = namespace["pcc_gc_backend0_is_unreachable"]
    mark(headers["root"])
    initial_visits = set(visited)
    assert initial_visits == ({"root", "child"} if subset else {"root", "child", "outside"})
    assert len(visited) == len(initial_visits)
    mark(headers["root"])
    assert len(visited) == len(initial_visits), "re-marking must not scan the cycle again"
    assert {obj for obj in objects if is_unreachable(headers[obj])} == {"borrowed"}
    assert {obj: node[16] for obj, node in nodes.items()} == {
        obj: bits | (reachable if obj in initial_visits else 0)
        for obj, bits in zip(objects, original, strict=True)
    }
    assert all(header[12] == flags for header in headers.values())
