"""Host-only protocol checks; these tests do not parse or execute TLA+."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/research"))
try:
    import flow_gc_tla_crosscheck as model
finally:
    sys.path.pop(0)


class TransferTransitionTests(unittest.TestCase):
    def test_smallest_no_barrier_witness_preserves_all_owners(self):
        events = ("output", "fields", "move:0", "root:0", "sweep")
        for barrier in (True, False):
            with self.subTest(barrier=barrier):
                state = model.State.initial((1,))
                for event in events:
                    state = model.step(state, event, barrier=barrier)
                    self.assertFalse(set(state.invariant_errors()) - {"NoLostLiveObjects"})
                self.assertEqual(state.missed, frozenset() if barrier else frozenset({1}))

    def test_aliases_remain_distinct_ownership_credits(self):
        state = model.State.initial((1, 1))
        state = model.step(state, "move:0", barrier=True)
        self.assertEqual(state.roots, (0, 2))
        self.assertEqual(state.fields, (1, 0))
        self.assertEqual(state.visible_tokens(), {1, 2})
        self.assertEqual(state.visible_identities(), {1})
        self.assertEqual(state.invariant_errors(), ())

    def test_field_scan_requires_output_discovery(self):
        with self.assertRaisesRegex(ValueError, "disabled event"):
            model.step(model.State.initial((1,)), "fields", barrier=True)

    def test_transfers_keep_program_order(self):
        with self.assertRaisesRegex(ValueError, "disabled event"):
            model.step(model.State.initial((1, 2)), "move:1", barrier=True)

    def test_each_root_is_scanned_once(self):
        state = model.step(model.State.initial((1,)), "root:0", barrier=True)
        with self.assertRaisesRegex(ValueError, "disabled event"):
            model.step(state, "root:0", barrier=True)

    def test_sweep_waits_for_all_scans_but_not_transfers(self):
        state = model.State.initial((1,))
        for event in ("output", "fields", "root:0", "sweep"):
            state = model.step(state, event, barrier=True)
        self.assertFalse(state.marking)
        self.assertEqual(state.length, 0)
        state = model.step(state, "move:0", barrier=True)
        state = model.step(state, "publish", barrier=True)
        self.assertEqual(state.invariant_errors(), ())
        self.assertEqual(tuple(model.enabled_events(state)), ())

    def test_publish_cannot_hide_incomplete_protocol(self):
        with self.assertRaisesRegex(ValueError, "disabled event"):
            model.step(model.State.initial((1,)), "publish", barrier=True)

    def test_lost_credit_and_invisible_field_are_detected(self):
        initial = model.State.initial((1,))
        lost = replace(initial, roots=(0,))
        self.assertIn("OwnershipConserved", lost.invariant_errors())
        invisible = replace(initial, roots=(0,), fields=(1,))
        self.assertNotIn("OwnershipConserved", invisible.invariant_errors())
        self.assertIn("PrefixVisible", invisible.invariant_errors())
        self.assertIn("AllOwnedVisible", invisible.invariant_errors())

    def test_exact_finite_schedule_comparison_and_state_exploration(self):
        result = model.experiment()
        self.assertEqual(result["schedule_comparison"], {
            "complete_schedules_per_variant": 41358,
            "safe_failing_schedules": 0,
            "barrier_removed_failing_schedules": 2550,
            "mirror_disagreements": 0,
        })
        self.assertEqual(result["positive"]["safety_violating_states"], 0)
        self.assertGreater(result["barrier_removed"]["safety_violating_states"], 0)
        self.assertEqual(result["barrier_removed"]["first_counterexample"], {
            "identities": [1],
            "events": ["output", "fields", "move:0", "root:0", "sweep"],
            "missed": [1],
        })

    def test_cfg_negative_control_changes_only_barrier_value(self):
        """Check the simple config text, not the TLA+ language or TLC behavior."""
        directory = ROOT / "specs/tla"
        def without_comments(path):
            return "\n".join(line for line in path.read_text().splitlines()
                             if not line.lstrip().startswith("\\*"))
        positive = without_comments(directory / "FlowGCTransfer.cfg")
        negative = without_comments(directory / "FlowGCTransferNoBarrier.cfg")
        self.assertEqual(positive.replace("UseBarrier = TRUE", "UseBarrier = FALSE"),
                         negative)


if __name__ == "__main__":
    unittest.main(verbosity=2)
