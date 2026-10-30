"""Semantic and deliberately invalid borrow tests (no compiler/toolchain build)."""

import gc
import threading
import weakref

import pytest

from experimental.record_scope_lifetime.scope_record import (
    LifetimeError,
    RecordScope,
    Region,
)


def test_empty_owner_is_truthy_and_does_not_hide_parent():
    outer = RecordScope()
    value = object()
    outer.define("x", value)
    inner = RecordScope(outer)
    assert bool(inner) and len(inner) == 0
    assert inner.lookup("x") is value
    assert inner.lookup_local("x") is None
    assert "x" not in inner.bindings


def test_shadowing_inline_spill_and_same_label_update():
    outer = RecordScope()
    first, second, third = object(), object(), object()
    outer.define("x", first)
    inner = RecordScope(outer)
    inner.define("x", second)
    assert inner._spill is None
    inner.update("x", third)
    assert inner._spill is None
    assert inner.lookup("x") is third
    assert outer.lookup("x") is first
    inner.define("y", second)
    assert inner._spill == {"x": third, "y": second}
    assert inner.lookup("x") is third and inner.lookup("y") is second
    assert inner._find_local("missing") == -1


def test_class_mark_does_not_survive_ordinary_assignment_or_shadowing():
    outer = RecordScope()
    outer.define("C", object())
    outer.class_objects.add("C")
    child = RecordScope(outer)
    assert child.is_class_object("C")
    child.define("C", object())
    assert not child.is_class_object("C")
    assert child._classes is None
    outer.define("C", object())
    assert not outer.is_class_object("C")


def test_live_capture_differs_from_snapshot_and_preserves_mutable_aliases():
    with Region() as region:
        outer = region.new_scope()
        payload = []
        outer.define("items", payload)
        outer.define("late", 1)
        child = region.new_scope(outer)
        child.define("local", 2)
        snapshot = child.snapshot()
        captured = child.capture()
        outer_capture = outer.capture()
        outer.define("late", 3)
        payload.append(7)
        assert snapshot["late"] == 1
        assert snapshot["items"] is payload
        assert captured.lookup("late") == 3
    with pytest.raises(LifetimeError, match="closed or stale"):
        child.lookup("local")
    outer_capture.define("late", 4)
    assert captured.lookup("late") == 4
    assert captured.lookup("items") == [7]
    with pytest.raises(TypeError):
        snapshot["late"] = 9
    captured.release()
    captured.release()
    outer_capture.release()
    with pytest.raises(LifetimeError, match="released"):
        captured.lookup("items")


def test_exception_exit_invalidates_borrows():
    with pytest.raises(ValueError, match="deliberate"):
        with Region() as region:
            borrow = region.new_scope()
            borrow.define("x", 1)
            raise ValueError("deliberate")
    with pytest.raises(LifetimeError, match="closed or stale"):
        borrow.define("x", 2)
    with pytest.raises(LifetimeError):
        borrow.capture()


def test_move_invalidates_only_old_handle_and_keeps_payload_identity():
    with Region() as region:
        borrow = region.new_scope()
        value = object()
        borrow.define("x", value)
        successor = borrow.move()
        assert successor.lookup("x") is value
        with pytest.raises(LifetimeError, match="moved"):
            borrow.lookup("x")
        with pytest.raises(LifetimeError, match="moved"):
            borrow.move()


def test_epoch_reuse_and_cross_origin_parent_rejected():
    with Region() as first, Region() as second:
        old = first.new_scope()
        with pytest.raises(LifetimeError, match="another origin"):
            second.new_scope(old)
        first.reset()
        current = first.new_scope()
        current.define("x", 1)
        with pytest.raises(LifetimeError, match="closed or stale"):
            old.lookup("x")
        assert current.lookup("x") == 1


def test_thread_crossing_rejected_for_borrow_and_capture():
    with Region() as region:
        borrow = region.new_scope()
        borrow.define("x", 1)
        capture = borrow.capture()
        rejected = []

        def other_thread():
            for operation in (lambda: borrow.lookup("x"),
                              lambda: capture.lookup("x"), region.close):
                try:
                    operation()
                except LifetimeError:
                    rejected.append(True)

        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert len(rejected) == 3
        assert borrow.lookup("x") == 1
        capture.release()


def test_owned_escape_retains_graph_until_last_alias_release():
    class Payload:
        pass

    with Region() as region:
        outer = region.new_scope()
        value = Payload()
        seen = weakref.ref(value)
        outer.define("x", value)
        child = region.new_scope(outer)
        captured = child.capture()
        alias = child.capture()
        del value, outer, child
    gc.collect()
    assert seen() is not None
    captured.release()
    gc.collect()
    assert seen() is not None
    alias.release()
    gc.collect()
    assert seen() is None


def test_unknown_label_and_none_sentinel_match_scope_contract():
    scope = RecordScope()
    assert scope.lookup("missing") is None
    assert scope.bindings.get("missing", 17) == 17
    scope.define("x", None)
    assert "x" in scope.bindings
    assert scope._find_local("x") == -1


def test_mapping_and_class_marks_are_separate_after_spill():
    scope = RecordScope()
    scope.define("x", 1)
    scope.define("C", 2)
    scope.class_objects.add("C")
    assert scope.is_class_object("C") and not scope.is_class_object("x")
    scope.update("C", 3)
    assert scope.lookup("C") == 3 and not scope.is_class_object("C")


def test_stale_origin_detector_rejects_guard_elision_mutant():
    class UncheckedRegion(Region):
        def _check(self, generation):
            pass

    def accepts_stale(region_type):
        with region_type() as region:
            old = region.new_scope()
            old.define("x", 1)
            region.reset()
            try:
                old.lookup("x")
            except LifetimeError:
                return False
            return True

    assert not accepts_stale(Region)
    assert accepts_stale(UncheckedRegion)


def test_none_sentinel_snapshot_follows_lookup_and_capture_is_not_a_borrow():
    with Region() as region:
        parent = region.new_scope()
        parent.define("x", 1)
        child = region.new_scope(parent)
        child.define("x", None)
        assert child.lookup("x") == child.snapshot()["x"] == 1
        captured = child.capture()
        with pytest.raises(LifetimeError, match="current borrow"):
            region.new_scope(captured)
        captured.release()
