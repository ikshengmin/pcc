"""Bounded mechanism tests only. No PCC compilation/native execution occurs."""

from __future__ import annotations

import importlib.util
from itertools import product
from pathlib import Path
import sys
import unittest


MODEL = Path(__file__).resolve().parents[2] / "scripts/research/flow_gc_protocol_model.py"
SPEC = importlib.util.spec_from_file_location("pcc_flow_gc_protocol_model", MODEL)
assert SPEC and SPEC.loader
model = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = model
SPEC.loader.exec_module(model)


class FlowCapabilityTests(unittest.TestCase):
    def test_repeated_non_destructive_use_returns_capability(self):
        context = model.FlowContext((11,))
        for _ in range(5):
            with context.focused("r0") as view:
                self.assertEqual(view.read(), 11)
        self.assertEqual(context.signature(), (("r0", "live"),))
        context.release("r0")
        context.check()

    def test_borrow_excludes_context_use_and_exactly_once_return(self):
        context = model.FlowContext((11,))
        view = context.focus("r0")
        with self.assertRaises(model.ProtocolError):
            context.focus("r0")
        with self.assertRaises(model.ProtocolError):
            context.release("r0")
        view.finish()
        with self.assertRaises(model.ProtocolError):
            view.finish()
        with self.assertRaises(model.ProtocolError):
            view.read()

    def test_exception_returns_capability(self):
        context = model.FlowContext((11,))
        with self.assertRaisesRegex(ValueError, "original"):
            with context.focused("r0"):
                raise ValueError("original")
        self.assertFalse(context.pending)
        context.release("r0")

    def test_aliases_need_distinct_credits_not_unique_object_claims(self):
        context = model.FlowContext((11, 11))
        with context.focused("r0") as left:
            with context.focused("r1") as right:
                self.assertEqual(left.read(), right.read())
                self.assertNotEqual(left.credit.number, right.credit.number)
        # Disjoint ROOT capabilities do not imply disjoint mutable OBJECTS.
        context.release("r0")
        self.assertEqual(context.slots["r1"].identity, 11)

    def test_duplicate_credit_is_rejected_even_for_same_object(self):
        context = model.FlowContext((11, 11))
        context.slots["r1"] = context.slots["r0"]
        with self.assertRaisesRegex(model.ProtocolError, "duplicated or lost"):
            context.check()

    def test_lost_credit_is_rejected(self):
        context = model.FlowContext((11,))
        context.slots["r0"] = None
        with self.assertRaisesRegex(model.ProtocolError, "duplicated or lost"):
            context.check()

    def test_a_over_a_does_not_guarantee_identity(self):
        context = model.FlowContext((11,))
        context.focus("r0").finish(12)
        context.check()
        self.assertEqual(context.slots["r0"].identity, 12)
        view = context.focus("r0")
        with self.assertRaisesRegex(model.ProtocolError, "identity-preservation"):
            view.finish(13, preserve_identity=True)
        view.finish(preserve_identity=True)

    def test_suspend_callback_and_escape_require_resolved_ephemeral_loans(self):
        context = model.FlowContext((11,))
        with context.focused("r0"):
            for effect in ("return", "suspend", "unknown_callback", "escape"):
                with self.subTest(effect=effect), self.assertRaises(model.ProtocolError):
                    context.effect(effect)
        for effect in ("return", "suspend", "unknown_callback", "escape"):
            context.effect(effect)

    def test_safepoint_needs_registered_root_in_addition_to_ownership(self):
        context = model.FlowContext((11,))
        with context.focused("r0"):
            context.effect("safepoint")
            context.registered.remove("r0")
            with self.assertRaisesRegex(model.ProtocolError, "registered"):
                context.effect("safepoint")

    def test_branch_contexts_must_agree(self):
        left = model.FlowContext((11,))
        right = model.FlowContext((11,))
        model.join_branches(left, right)
        right.release("r0")
        with self.assertRaisesRegex(model.ProtocolError, "same capability context"):
            model.join_branches(left, right)


class CollectorObligationTests(unittest.TestCase):
    def test_move_invalidates_raw_pointer_but_updates_registered_handle(self):
        heap = model.TraceHeap()
        heap.add(11)
        heap.root("r0", 11)
        raw = heap.roots["r0"]
        heap.move()
        with self.assertRaisesRegex(model.ProtocolError, "stale raw pointer"):
            heap.resolve(raw)
        self.assertEqual(heap.resolve(heap.roots["r0"]), 11)

    def test_owned_capability_without_gc_root_does_not_prevent_collection(self):
        context = model.FlowContext((11,))
        heap = model.TraceHeap()
        heap.add(11)
        with context.focused("r0"):
            self.assertEqual(heap.collect(), {11})
        context.check()  # Conservation passes even though GC visibility failed.

    def test_cycles_still_need_tracing_and_weak_edges_do_not_root(self):
        heap = model.TraceHeap()
        heap.add(11, 12)
        heap.add(12, 11)
        heap.root("r0", 11)
        self.assertEqual(heap.collect(), set())
        del heap.roots["r0"]
        self.assertEqual(heap.weak_get(11), 11)
        self.assertEqual(heap.collect(), {11, 12})
        self.assertIsNone(heap.weak_get(11))

    def test_resurrection_requires_retrace_and_one_shot_finalization(self):
        heap = model.TraceHeap()
        events = []

        def resurrect(current, identity):
            events.append(identity)
            current.root("resurrected", identity)

        heap.add(11, 12, finalizer=resurrect)
        heap.add(12)
        self.assertEqual(heap.collect(), set())
        self.assertEqual(heap.reachable(), {11, 12})
        del heap.roots["resurrected"]
        self.assertEqual(heap.collect(), {11, 12})
        self.assertEqual(events, [11])

    def test_old_to_young_store_needs_remembered_owner(self):
        self.assertEqual(model.minor_collection(remembered=False), {7})
        self.assertEqual(model.minor_collection(remembered=True), set())


class ConstructionTests(unittest.TestCase):
    def test_every_partial_prefix_and_alias_pattern_cleans_exactly_once(self):
        for count in range(5):
            for identities in product((11, 12), repeat=count):
                for prefix in range(count + 1):
                    with self.subTest(identities=identities, prefix=prefix):
                        state = model.Construction(identities)
                        for index in range(prefix):
                            state.transfer(index)
                            self.assertEqual(state.visible_identities(), set(identities))
                        callbacks = []

                        def callback(identity):
                            state.check()
                            callbacks.append(identity)
                            raise ValueError("finalizer must not overwrite original error")

                        self.assertEqual(
                            state.cleanup("operand failure", callback), "operand failure"
                        )
                        self.assertEqual(set(callbacks), set(identities))
                        self.assertEqual(len(callbacks), len(set(identities)))
                        self.assertEqual(state.released, set(range(count)))

    def test_finalizer_observes_valid_prefix_and_runtime_error_does_not_stop_cleanup(self):
        state = model.Construction((11, 12, 13))
        state.transfer(0)
        state.transfer(1)
        observed = []

        def callback(identity):
            state.check()
            observed.append((identity, state.length, state.visible_identities()))
            raise RuntimeError("an arbitrary finalizer error")

        self.assertEqual(state.cleanup("original error", callback), "original error")
        self.assertEqual(observed, [(12, 1, {11, 13}), (11, 0, {13}), (13, 0, set())])
        self.assertEqual(state.released, {0, 1, 2})
        self.assertEqual(state.length, 0)
        self.assertEqual(state.fields + state.roots, [None] * 6)
        self.assertEqual(len(state.callback_errors), 3)
        self.assertTrue(all(type(error) is RuntimeError for error in state.callback_errors))
        state.check()

    def test_callback_can_reenter_cleanup_without_double_release(self):
        state = model.Construction((11, 12, 13))
        state.transfer(0)
        state.transfer(1)
        observed = []

        def callback(identity):
            state.check()
            observed.append(identity)
            if identity == 12:
                self.assertEqual(
                    state.cleanup("nested error", callback), "nested error"
                )
                raise RuntimeError("outer finalizer error after nested cleanup")

        self.assertEqual(state.cleanup("original error", callback), "original error")
        self.assertEqual(observed, [12, 11, 13])
        self.assertEqual(state.finalized, [12, 11, 13])
        self.assertEqual(state.released, {0, 1, 2})
        self.assertEqual(len(state.callback_errors), 1)
        self.assertIs(type(state.callback_errors[0]), RuntimeError)
        self.assertEqual(state.length, 0)
        state.check()

    def test_publish_only_after_complete_initialization(self):
        state = model.Construction((11, 12))
        with self.assertRaises(model.ProtocolError):
            state.publish()
        state.transfer(0)
        with self.assertRaises(model.ProtocolError):
            state.publish()
        state.transfer(1)
        state.publish()
        with self.assertRaises(model.ProtocolError):
            state.publish()

    def test_same_root_cannot_be_consumed_twice(self):
        state = model.Construction((11, 11))
        state.transfer(0)
        with self.assertRaises(model.ProtocolError):
            state.transfer(0)

    def test_unlocked_clear_before_store_loses_owner(self):
        state = model.Construction((11,))
        state.roots[0] = None
        self.assertEqual(state.visible_identities(), set())
        with self.assertRaisesRegex(model.ProtocolError, "duplicated or lost"):
            state.check()

    def test_field_before_visible_length_conserves_owner_but_hides_it(self):
        state = model.Construction((11,))
        state.fields[0], state.roots[0] = state.roots[0], None
        self.assertEqual(state.visible_identities(), set())
        with self.assertRaisesRegex(model.ProtocolError, "invisible to tracing"):
            state.check()

    def test_finalizer_cannot_execute_under_commit_lock(self):
        state = model.Construction((11,))
        state.locked = True
        with self.assertRaisesRegex(model.ProtocolError, "callback inside graph lock"):
            state._release(0, lambda _identity: None)


class ConcurrentMarkTests(unittest.TestCase):
    def test_minimal_barrier_counterexample_conserves_ownership(self):
        schedule = ("output", "fields", "move:0", "root:0", "sweep")
        self.assertEqual(model.mark_schedule((11,), schedule, barrier=False), {11})
        self.assertEqual(model.mark_schedule((11,), schedule, barrier=True), set())

    def test_all_bounded_mark_schedules_and_barrier_removed_control(self):
        result = model.finite_mark_experiment()
        self.assertEqual(result["checked_schedules"], 41358)
        self.assertEqual(result["safe_failures"], 0)
        self.assertGreater(result["barrier_removed_failures"], 0)
        self.assertIsNotNone(result["first_counterexample"])


class ReentrantStorePlanTests(unittest.TestCase):
    def test_callback_invalidates_snapshot_even_with_live_owner(self):
        entry = model.MutableEntry("original key", 11)
        plan = model.StorePlan(entry)
        entry.reentrant_replace("replacement key", 12)
        entry.locked = True
        self.assertFalse(plan.commit(13))
        entry.locked = False
        releases = []
        plan.finish(releases.append)
        self.assertEqual((entry.key, entry.value), ("replacement key", 12))
        self.assertEqual(releases, [])
        # Negative control: a raw stale-location store corrupts the new entry.
        entry.value = 13
        self.assertNotEqual((entry.key, entry.value), ("replacement key", 12))

    def test_commit_once_then_finish_outside_lock_exactly_once(self):
        entry = model.MutableEntry("key", 11)
        plan = model.StorePlan(entry)
        with self.assertRaises(model.ProtocolError):
            plan.commit(12)
        entry.locked = True
        self.assertTrue(plan.commit(12))
        with self.assertRaises(model.ProtocolError):
            plan.commit(13)
        with self.assertRaises(model.ProtocolError):
            plan.finish(lambda _value: None)
        entry.locked = False
        releases = []
        plan.finish(releases.append)
        self.assertEqual(releases, [11])
        with self.assertRaises(model.ProtocolError):
            plan.finish(releases.append)

    def test_raising_finalizer_cannot_reuse_consumed_plan(self):
        entry = model.MutableEntry("key", 11)
        plan = model.StorePlan(entry)
        entry.locked = True
        self.assertTrue(plan.commit(12))
        entry.locked = False

        def finalizer(_value):
            with self.assertRaises(model.ProtocolError):
                plan.finish(lambda _old: None)
            raise ValueError("finalizer")

        with self.assertRaisesRegex(ValueError, "finalizer"):
            plan.finish(finalizer)
        self.assertEqual(plan.state, "finished")


if __name__ == "__main__":
    unittest.main(verbosity=2)
