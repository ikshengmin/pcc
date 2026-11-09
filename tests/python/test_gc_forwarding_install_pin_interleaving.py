"""Forwarding metadata publication preserves completed pin/unpin changes.

The shared loader runs production public/preallocated installers, plan
prepare/finish, pin/unpin, provenance and pin predicates. GC3 supplies finite
memory, allocator, graph-lock, exact-index and checked-RC-tail doubles. The
preallocated model covers already-reserved index capacity only. No collector
copy, CPU scheduling, native ABI or check-pin-to-publication exclusion is
proved by these deterministic interleavings.
"""
from __future__ import annotations

import ast
import re

import pytest

from tests.python.runtime_body_model import load_functions
from tests.python.test_gc3_norc_source_retirement import GC3, PY


SOURCE = PY / "freestanding_gc_forwarding_identity.py"
ROOTS = PY / "freestanding_gc_root_operations.py"
FORWARDED, TARGET, PINNED = 2048, 8192, 64


def _fixture(backend, installer):
    state = GC3(counted=True)
    state.store("pcc_gc_backend_selected", 0, backend)
    state.ns["pcc_gc_config_ensure"] = lambda: backend
    state.ns["pcc_gc_backend"] = lambda: backend
    state.ns["frame_address"] = lambda: 1 << 19
    load_functions(ROOTS, {"pcc_gc_pin", "pcc_gc_unpin",
                           "pcc_gc_object_is_known_no_lock",
                           "pcc_gc_object_is_address_pinned"}, state.ns)
    source, _, _ = state.value()
    target, _, _ = state.value()
    # Expose unrelated non-color metadata to accidental whole-word overwrites.
    for obj in (source, target):
        state.store(obj, 12, state.load(obj, 12) | 0x1000000)
    plan = 0
    if installer == "preallocated":
        tables = (state.forwardings, state.reverse, state.identities)

        def capacity(kind, extra):
            assert state.depth > 0 and kind in (0, 1, 2)
            assert extra > 0 and len(tables[kind]) + extra <= 4
            return 0  # A finite four-entry reservation already exists.

        def commit(kind, slots_io, cap, extra):
            assert state.depth > 0 and cap == 0
            assert state.load(slots_io, 0) == 0
            return capacity(kind, extra)

        def insert(kind, obj, node):
            assert state.depth > 0 and obj and not obj & 1 and node
            table = tables[kind]
            if obj in table:
                return 0
            if len(table) == 4:
                return -1
            table[obj] = node
            return 1

        def calloc(count, size):
            address = state.malloc(count * size)
            state.zero(address, 0, count * size)
            return address

        state.ns.update(calloc=calloc,
                        pcc_gc_forwarding_plan_index_capacity=capacity,
                        pcc_gc_forwarding_plan_index_commit=commit,
                        pcc_gc_forwarding_plan_index_insert=insert)
        plan = state.ns["pcc_gc_forwarding_install_plan_prepare"](source, target)
        assert plan and state.depth == 0
    # Public forwarding itself does not acquire a stopped-world epoch.
    state.stopped = False
    state.events.clear()
    return state, source, target, plan


def _install(state, installer, source, target, plan):
    if installer == "public":
        return state.ns["pcc_gc_install_forwarding"](source, target)
    with state.locked():
        return state.ns["pcc_gc_install_forwarding_preallocated_unlocked"](
            source, target, plan)


@pytest.mark.parametrize("backend", [3, 4])
@pytest.mark.parametrize("installer", ["public", "preallocated"])
@pytest.mark.parametrize("role", ["source", "target"])
@pytest.mark.parametrize("operation", ["pin", "unpin"])
def test_install_preserves_completed_pin_operation(backend, installer, role, operation):
    state, source, target, plan = _fixture(backend, installer)
    subject = source if role == "source" else target
    metadata_bit = FORWARDED if role == "source" else TARGET
    if operation == "unpin":
        state.ns["pcc_gc_pin"](subject)
    before = {obj: state.load(obj, 12) for obj in (source, target)}
    counts = {obj: state.load(obj, 0) for obj in (source, target)}
    atomics, stores, injected = [], [], []
    armed = True

    def inject():
        nonlocal armed
        armed = False
        depth = state.depth
        assert depth > 0
        state.ns["pcc_gc_" + operation](subject)
        assert state.depth == depth
        injected.append(operation)

    predicate = state.ns["pcc_gc_object_is_address_pinned"]

    def pinned(obj):
        # For source unpin, capture a pinned flags snapshot, then unpin
        # before the admission check. The check legitimately accepts it;
        # publication must not resurrect the obsolete snapshot's pin bit.
        if armed and obj == source and role == "source" and operation == "unpin":
            inject()
        return predicate(obj)

    def rmw(width, opcode, obj, offset, operand, order):
        if (armed and width == 32 and obj == subject and offset == 12
                and opcode == "or" and operand == metadata_bit):
            inject()
        assert order == "relaxed"
        old = state.load(obj, offset)
        value = {"or": lambda: old | operand, "and": lambda: old & operand,
                 "add": lambda: old + operand, "sub": lambda: old - operand}[opcode]()
        state.store(obj, offset, value & ((1 << width) - 1))
        atomics.append((width, opcode, obj, offset, operand))
        return old

    def store(obj, offset, value):
        # The legacy snapshot has already been evaluated when this hook
        # runs. It would overwrite a just-completed target/source pin.
        if obj in (source, target) and offset == 12:
            if armed and obj == subject:
                inject()
            stores.append((obj, value))
        state.store(obj, offset, value)

    state.ns.update(atomic_rmw_i32=lambda *args: rmw(32, *args),
                    atomic_rmw_i64=lambda *args: rmw(64, *args),
                    store_i32=store, pcc_gc_object_is_address_pinned=pinned)
    assert _install(state, installer, source, target, plan) == 0
    expected = dict(before)
    expected[subject] = (expected[subject] | PINNED if operation == "pin"
                         else expected[subject] & ~PINNED)
    expected[source] |= FORWARDED
    expected[target] |= TARGET
    assert {obj: state.load(obj, 12) for obj in (source, target)} == expected
    assert injected == [operation] and not armed and stores == []
    assert [call for call in atomics if call[0] == 32 and call[4] in (FORWARDED, TARGET)] == [
        (32, "or", source, 12, FORWARDED), (32, "or", target, 12, TARGET)]
    assert state.load(state.forwardings[source], 8) == target
    assert state.load(source, 0) == counts[source]
    # This fixture explicitly keeps counted mode for either backend.
    assert state.load(target, 0) == counts[target] + 1
    assert state.ns["pcc_gc_object_is_address_pinned"](subject) == int(operation == "pin")
    assert state.depth == 0 and not state.stopped
    if plan:
        state.ns["pcc_gc_forwarding_install_plan_finish"](plan)


@pytest.mark.parametrize("backend", [3, 4])
@pytest.mark.parametrize("installer", ["public", "preallocated"])
def test_pin_present_at_admission_still_rejects_without_publication(backend, installer):
    state, source, target, plan = _fixture(backend, installer)
    state.ns["pcc_gc_pin"](source)
    before = {obj: (state.load(obj, 0), state.load(obj, 12)) for obj in (source, target)}
    assert _install(state, installer, source, target, plan) == -2
    assert {obj: (state.load(obj, 0), state.load(obj, 12)) for obj in (source, target)} == before
    assert source not in state.forwardings and target not in state.reverse
    assert state.load("pcc_gc_relocation_forwards", 0) == 0
    assert state.load("pcc_gc_relocation_pin_rejects", 0) == 1
    assert state.depth == 0
    if plan:
        state.ns["pcc_gc_forwarding_install_plan_finish"](plan)


def test_retarget_preserves_pin_from_old_target_release_boundary():
    state, source, old_target, _ = _fixture(3, "public")
    assert _install(state, "public", source, old_target, 0) == 0
    target, _, _ = state.value()
    released = []

    def release(obj):
        assert obj == old_target and state.depth > 0
        state.counted_decref(obj)
        # This fixture's checked RC tail is the explicit reentrancy boundary;
        # the installer and pin operation remain real production bodies.
        state.ns["pcc_gc_pin"](source)
        released.append(obj)

    state.ns["_py_decref_checked_tail"] = release
    assert _install(state, "public", source, target, 0) == 0
    assert released == [old_target]
    assert state.load(state.forwardings[source], 8) == target
    assert state.load(source, 12) & (FORWARDED | PINNED) == FORWARDED | PINNED
    assert state.ns["pcc_gc_object_is_address_pinned"](source) == 1
    assert state.depth == 0 and not state.stopped


@pytest.mark.parametrize("name", ["pcc_gc_install_forwarding_unlocked",
                                  "pcc_gc_install_forwarding_preallocated_unlocked"])
def test_install_header_publication_uses_only_atomic_bit_or(name):
    body = next(node for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body
                if isinstance(node, ast.FunctionDef) and node.name == name)
    calls = [node for node in ast.walk(body) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)]
    updates = [node for node in calls if node.func.id == "atomic_rmw_i32"]
    assert len(updates) == 2
    assert {(node.args[1].id, ast.literal_eval(node.args[3])) for node in updates} == {
        ("from_obj", FORWARDED), ("to_obj", TARGET)}
    for node in updates:
        assert ast.literal_eval(node.args[0]) == "or"
        assert ast.literal_eval(node.args[2]) == 12
        assert ast.literal_eval(node.args[4]) == "relaxed"
    assert not [node for node in calls if node.func.id == "store_i32"
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in {"from_obj", "to_obj"}]


def test_install_publication_lowers_to_i32_atomic_or(tmp_path):
    from pcc.frontends.python import pipeline
    from pcc.frontends.python.pipeline_targets import host_target_triple

    output = tmp_path / "forwarding_install.ll"
    pipeline.compile_python(str(SOURCE), str(output), emit_llvm_only=True,
                            python_library=True, target_triple=host_target_triple())
    ir = output.read_text(encoding="utf-8")
    for name in ("pcc_gc_install_forwarding_unlocked",
                 "pcc_gc_install_forwarding_preallocated_unlocked"):
        body = re.search(r"define[^\n]*@" + name + r"\([^\n]*\)[^{]*\{(.*?)^}",
                         ir, re.MULTILINE | re.DOTALL)
        assert body is not None, name
        # AST checks own exact-mask validation. IR may use SSA truncations.
        updates = re.findall(r"atomicrmw or [^\n]*i32 (?:2048|8192|%[-a-zA-Z$._0-9]+) monotonic",
                             body.group(1))
        assert len(updates) == 2, name
