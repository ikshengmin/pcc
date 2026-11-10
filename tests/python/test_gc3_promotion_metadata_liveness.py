"""Update-only slots heal forwarding but never provide a liveness edge.

The finite model loads production bodies through runtime_body_model.  It is
not native-stack or concurrent-collector evidence.
"""
from pathlib import Path
import os
import subprocess

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.test_gc3_norc_source_retirement import GC3


ADAPTERS = (
    "pcc_gc_generational_promote_slot",
    "pcc_gc_generational_promote_shallow_slot",
)


def snapshot(model, objects):
    return (
        tuple((obj, model.load(obj, 0), model.load(obj, 12)) for obj in objects),
        dict(model.index), dict(model.forwardings),
        model.load("pcc_gc_forwarding_population", 0),
        model.load("pcc_gc_backend3_promotion_head", 0),
        model.load("pcc_gc_backend3_promotion_tail", 0),
        tuple(model.events),
    )


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize("counted", (False, True))
@pytest.mark.parametrize("nursery", (False, True))
def test_update_only_white_young_slot_does_not_create_a_live_copy(adapter, counted, nursery):
    model = GC3(counted=counted)
    target, _, _ = model.value(nursery=nursery)
    slot = model.allocate(8)
    model.store(slot, 0, target)
    model.store("pcc_gc_mark_active", 0, 1)
    before = snapshot(model, (target,))
    with model.locked():
        model.ns[adapter](slot, 3, 0)
    assert model.load(slot, 0) == target
    assert snapshot(model, (target,)) == before


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize("counted", (False, True))
@pytest.mark.parametrize("hops", (1, 2))
def test_update_only_slot_heals_existing_chain_without_retaining_or_promoting(adapter, counted, hops):
    model = GC3(counted=counted)
    objects = [model.value()[0] for _ in range(hops + 1)]
    for source, target in zip(objects, objects[1:]):
        assert model.ns["pcc_gc_install_forwarding"](source, target) == 0
    slot = model.allocate(8)
    model.store(slot, 0, objects[0])
    model.store("pcc_gc_mark_active", 0, 1)
    before = snapshot(model, objects)
    with model.locked():
        model.ns[adapter](slot, 3, 0)
    assert model.load(slot, 0) == objects[-1]
    assert snapshot(model, objects) == before


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize("role", (1, 2))
def test_strong_owned_and_borrowed_slots_still_promote(adapter, role):
    model = GC3()
    source, _, _ = model.value()
    slot = model.allocate(8)
    model.store(slot, 0, source)
    model.store("pcc_gc_mark_active", 0, 1)
    with model.locked():
        model.ns[adapter](slot, role, 0)
    target = model.load(slot, 0)
    assert target != source and target in model.index
    assert model.load(target, 12) & 256
    assert model.load(target, 12) & 16
    assert not model.load(target, 12) & 128


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize("forwarded", (False, True))
def test_actual_weakref_visitor_keeps_target_update_only(adapter, forwarded):
    model = GC3()
    source, _, _ = model.value()
    target = model.copy(source) if forwarded else source
    owner = model.allocate(32)
    model.store(owner, 8, abi.PY_TYPE_WEAKREF)
    model.store(owner, 16, source)
    model.store(owner, 24, 0)
    model.store("pcc_gc_mark_active", 0, 1)
    before = snapshot(model, (source, target))
    with model.locked():
        assert model.ns["pcc_gc_visit_object_slots"](owner, model.ns[adapter], 0) == 1
    assert model.load(owner, 16) == target
    assert snapshot(model, (source, target)) == before


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize("heap_objects", (64, 256, 1024))
def test_update_only_unforwarded_query_does_not_walk_the_heap(adapter, heap_objects):
    model = GC3()
    objects = [model.value()[0] for _ in range(heap_objects)]
    slot = model.allocate(8)
    model.store(slot, 0, objects[-1])
    queries = []
    known = model.ns["pcc_gc_object_is_known_no_lock"]
    model.ns["pcc_gc_object_is_known_no_lock"] = lambda obj: queries.append(obj) or known(obj)
    model.ns["pcc_gc_forwarding_index_find"] = lambda obj: pytest.fail("unforwarded object")
    model.ns["pcc_gc_generational_oldify_copy"] = lambda obj: pytest.fail("metadata oldification")
    with model.locked():
        model.ns[adapter](slot, 3, 0)
    # n, 4n and 16n: one index query for one slot, independent of heap size.
    assert queries == [objects[-1]]
    assert model.load(slot, 0) == objects[-1]


@pytest.mark.parametrize("adapter", ("full", "shallow"))
@pytest.mark.parametrize("case", ("direct", "weak", "class", "chain", "late-alias", "strong1", "strong2"))
def test_native_metadata_does_not_supply_a_liveness_edge(tmp_path, pcc_runtime_archive, adapter, case):
    """Controlled mark-state injection with real objects and owned runtime."""
    source = Path(__file__).with_name("fixtures") / "gc3_promotion_metadata_liveness.c"
    executable = tmp_path / "metadata-liveness"
    compile_owned_c_with_runtime(source, executable, runtime_archive=pcc_runtime_archive)
    result = subprocess.run([str(executable), adapter, case], capture_output=True,
                            text=True, timeout=20,
                            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="3"))
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == ""
    assert f"METADATA adapter={adapter} case={case} good=1" in result.stdout
