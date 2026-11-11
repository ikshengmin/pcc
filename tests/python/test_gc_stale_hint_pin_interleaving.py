"""Deterministic production-body stale-hint / pin interleavings.

The shared loader executes real install/remove, read/root repair and pin/unpin
bodies. Memory/index/allocator providers remain finite host doubles. Each RMW
below reads the current word and updates it indivisibly, returning its old value;
32/64-bit masks model the intrinsic width. A hook schedules one whole pin/unpin
call after the repair's flags snapshot and immediately before its mutation.
This is a semantic witness, not CPU memory-ordering or concurrent native proof.
"""
from __future__ import annotations

import ast

import pytest

from tests.python.runtime_body_model import load_functions
from tests.python.test_gc3_norc_source_retirement import GC3, PY


ROOTS = PY / "freestanding_gc_root_operations.py"
READS = PY / "freestanding_gc_forwarding_identity.py"
FORWARDED, PINNED = 2048, 64


def _fixture(backend, shape):
    state = GC3(counted=True)
    state.store("pcc_gc_backend_selected", 0, backend)
    state.ns["pcc_gc_config_ensure"] = lambda: backend
    state.ns["pcc_gc_backend"] = lambda: backend
    load_functions(ROOTS, {"pcc_gc_pin", "pcc_gc_unpin",
                          "pcc_gc_object_is_known_no_lock",
                          "pcc_gc_object_is_known_stable",
                          "_resolve_root_slot_mode"}, state.ns)
    load_functions(READS, {"pcc_gc_note_relocation_read"}, state.ns)
    # A fixed synthetic frame gives one reproducible metric shard (offset 128).
    state.ns["frame_address"] = lambda: 1 << 19
    source, _, _ = state.value()
    target, _, _ = state.value()
    with state.locked():
        assert state.ns["pcc_gc_install_forwarding"](source, target) == 0
        if shape == "missing":
            state.ns["pcc_gc_forwarding_remove"](source)
        else:
            # Compatibility-only malformed initial edges, not installer output.
            state.store(state.forwardings[source], 8,
                        0 if shape == "null" else source)
    assert state.load(source, 12) & FORWARDED
    assert source in state.index and target in state.index
    slot = state.allocate(24)
    state.store(slot, 16, source)
    state.stopped = False  # These read/root entries do not require STW.
    state.events.clear()
    return state, source, target, slot


def _call(state, entry, source, slot):
    if entry == "read":
        return state.ns["pcc_gc_note_relocation_read"](source)
    with state.locked():
        return state.ns["_resolve_root_slot_mode"](
            slot, 16, int(entry == "borrowed-root"))


@pytest.mark.parametrize("backend", [4])
@pytest.mark.parametrize("entry,shape", [
    ("read", "missing"),
    ("owning-root", "missing"), ("borrowed-root", "missing"),
    ("owning-root", "null"), ("borrowed-root", "null"),
    ("owning-root", "self"), ("borrowed-root", "self"),
])
@pytest.mark.parametrize("operation", ["pin", "unpin"])
def test_stale_cleanup_preserves_completed_pin_operation(backend, entry, shape, operation):
    state, source, target, slot = _fixture(backend, shape)
    atomics, header_stores = [], []
    injected = []
    armed = False

    def rmw(width, opcode, address, offset, operand, order):
        nonlocal armed
        if (armed and width == 32 and address == source and offset == 12
                and opcode == "and" and operand == ~FORWARDED):
            inject()
        assert order == "relaxed"
        before = state.load(address, offset)
        after = {"or": lambda: before | operand,
                 "and": lambda: before & operand,
                 "add": lambda: before + operand,
                 "sub": lambda: before - operand}[opcode]()
        state.store(address, offset, after & ((1 << width) - 1))
        atomics.append((width, opcode, address, offset, operand, order))
        return before

    def store(address, offset, value):
        # Also inject on the old broken implementation: the stale value has
        # already been computed at this call boundary, so it loses the update.
        if address == source and offset == 12:
            if armed:
                inject()
            header_stores.append(value)
        state.store(address, offset, value)

    def inject():
        nonlocal armed
        armed = False
        depth = state.depth
        assert depth > 0  # The reader holds graph exclusion, not the pin caller.
        state.ns["pcc_gc_" + operation](source)
        assert state.depth == depth
        injected.append(operation)

    state.ns["atomic_rmw_i32"] = lambda *args: rmw(32, *args)
    state.ns["atomic_rmw_i64"] = lambda *args: rmw(64, *args)
    state.ns["store_i32"] = store
    if operation == "unpin":
        state.ns["pcc_gc_pin"](source)
    initial = state.load(source, 12)
    counts = [state.load(obj, 0) for obj in (source, target)]
    metric_before = state.load("pcc_gc_metric_shards", 128)
    atomics.clear()
    armed = True
    assert _call(state, entry, source, slot) == source
    expected = initial | PINNED if operation == "pin" else initial & ~PINNED
    assert state.load(source, 12) == expected & ~FORWARDED
    assert injected == [operation] and not armed
    assert header_stores == []
    assert atomics == [
        (32, "or" if operation == "pin" else "and", source, 12,
         PINNED if operation == "pin" else ~PINNED, "relaxed"),
        (64, "add" if operation == "pin" else "sub", "pcc_gc_metric_shards",
         128, 1, "relaxed"),
        (32, "and", source, 12, ~FORWARDED, "relaxed"),
    ]
    assert state.load("pcc_gc_metric_shards", 128) == metric_before + (
        1 if operation == "pin" else -1)
    assert state.load(slot, 16) == source
    assert [state.load(obj, 0) for obj in (source, target)] == counts
    assert not [event for event in state.events if event[0] in ("incref", "decref")]
    assert state.depth == 0 and not state.stopped


@pytest.mark.parametrize("entry", ["read", "owning-root", "borrowed-root"])
@pytest.mark.parametrize("kind", ["ordinary", "null", "tagged", "unknown", "gc0", "gc1", "gc2"])
def test_unrelated_early_returns_never_write_headers(entry, kind):
    state, source, _, slot = _fixture(4, "missing")
    state.store(source, 12, state.load(source, 12) & ~FORWARDED)
    value = source
    if kind in {"null", "tagged"}:
        value = 0 if kind == "null" else 7
    elif kind == "unknown":
        del state.index[source]
    elif kind.startswith("gc"):
        state.store("pcc_gc_backend_selected", 0, int(kind[-1]))
        state.store(source, 12, state.load(source, 12) | FORWARDED)
    state.store(slot, 16, value)

    def forbidden(*args):
        pytest.fail("early return wrote header or metadata")

    state.ns.update({name: forbidden for name in
                     ("store_i32", "atomic_rmw_i32", "store_ptr")})
    assert _call(state, entry, value, slot) == value
    assert state.load(slot, 16) == value


@pytest.mark.parametrize("path,name,count", [
    (ROOTS, "_resolve_root_slot_mode", 2),
    (READS, "_note_relocation_read_unlocked", 1),
])
def test_stale_cleanup_uses_atomic_bit_clear_not_snapshot_store(path, name, count):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    body = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == name)
    calls = [node for node in ast.walk(body) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)]
    clears = [node for node in calls if node.func.id == "atomic_rmw_i32"]
    assert len(clears) == count
    for call in clears:
        assert ast.literal_eval(call.args[0]) == "and"
        assert ast.literal_eval(call.args[2]) == 12
        assert isinstance(call.args[3], ast.UnaryOp)
        assert isinstance(call.args[3].op, ast.Invert)
        assert ast.literal_eval(call.args[3].operand) == FORWARDED
        assert ast.literal_eval(call.args[4]) == "relaxed"
    # The read helper may still update its global success counter, never a
    # source header. Root repair has no remaining whole-word i32 store.
    assert not [call for call in calls if call.func.id == "store_i32"
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id in {"value", "obj"}]


@pytest.mark.parametrize("path,symbol", [
    (ROOTS, "pcc_gc_resolve_root_slot_mode"),
    (READS, "pcc_gc_note_relocation_read_unlocked"),
])
def test_cleanup_lowers_to_i32_atomic_and(tmp_path, path, symbol):
    # CI-only lowering coverage. This inspects host compiler IR, not emitted
    # CPU execution, and complements the existing strict-object closure tests.
    import re

    from pcc.frontends.python import pipeline
    from pcc.frontends.python.pipeline_targets import host_target_triple

    output = tmp_path / (path.stem + ".ll")
    pipeline.compile_python(
        str(path), str(output), emit_llvm_only=True, python_library=True,
        target_triple=host_target_triple(),
    )
    ir = output.read_text(encoding="utf-8")
    body = re.search(r"define[^\n]*@" + symbol + r"\([^\n]*\)[^{]*\{(.*?)^}",
                     ir, re.MULTILINE | re.DOTALL)
    assert body is not None, symbol
    # Equivalent branches may be merged; the source guard checks both sites
    # and the exact mask. Lowering may leave that mask in an SSA trunc/xor
    # value instead of folding it to a signed or unsigned i32 literal.
    assert re.search(r"atomicrmw and [^\n]*i32 (?:-2049|4294965247|%[-a-zA-Z$._0-9]+) monotonic",
                     body.group(1))
