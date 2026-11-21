"""Run the window's real source against a deterministic clock and stop visitor."""
import ast
from pathlib import Path
import unittest

SRC = Path(__file__).resolve().parents[2] / "pcc/runtime/py/freestanding_exp2_window.py"


class AttemptTime(unittest.TestCase):
    def setUp(self):
        self.state = {"pcc_exp2_window_state": 2}
        self.epoch = 7
        self.events = []
        times = self.times = []
        results = self.results = []

        def clock():
            self.events.append("clock")
            return times.pop(0)

        def visitor(epoch):
            self.events.append("visit")
            self.assertEqual(epoch, self.epoch)
            return results.pop(0)

        def add(op, name, off, delta, _order):
            self.assertEqual((op, off), ("add", 0))
            old = self.state.get(name, 0)
            self.state[name] = old + delta
            return old

        def cas(name, off, expected, new, *_):
            self.assertEqual(off, 0)
            old = self.state.get(name, 0)
            if old == expected:
                self.state[name] = new
            return old

        tree = ast.parse(SRC.read_text())
        tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        env = {
            "i64": int, "c_abi_export": lambda n: lambda f: f,
            "global_addr": lambda n: n,
            "atomic_load_i64": lambda name, off, order: self.state.get(name, 0),
            "atomic_rmw_i64": add, "atomic_cas_i64": cas,
            "atomic_store_i64": lambda name, off, v, order: self.state.__setitem__(name,v),
            "pcc_thread_world_stop_epoch_held": lambda: self.epoch,
            "pcc_platform_monotonic_ns": clock, "pcc_exp2_process_minor_held": visitor,
            # Deliberately unlike expected window totals: accidental delegation
            # to the success-only inner timer must not pass.
            "pcc_exp2_process_metric": lambda i: -999,
        }
        exec(compile(tree, str(SRC), "exec"), env)
        self.attempt = env["pcc_exp2_window_stop_minor"]
        self.metric = env["pcc_exp2_window_metric"]

    def test_all_outcomes_time_the_exact_visitor_once(self):
        self.results[:] = [4, -1, 0]
        self.times[:] = [11, 1011, 2000, 7001, 8000, 8001]
        self.assertEqual([self.attempt(7) for _ in range(3)], [4,-1,0])
        self.assertEqual(self.events, ["clock","visit","clock"] * 3)
        self.assertEqual([self.metric(k) for k in (33,34,3,4,46)], [3,1,6002,6,0])
        # Neither the outer attempt timer nor its accessor resets the refusal reason.
        self.state["pcc_exp2_window_first_bad_reason"] = -3
        self.assertEqual(self.metric(31),-3)
        self.assertEqual(self.metric(33),3)

    def test_non_attempts_do_not_read_the_clock_or_record_a_refusal(self):
        self.state["pcc_exp2_window_state"] = 0
        self.assertEqual(self.attempt(7),-1)
        self.state["pcc_exp2_window_state"] = 2
        self.assertEqual(self.attempt(6),-1)
        self.assertEqual(self.attempt(0),-1)
        self.assertEqual(self.events,[])
        self.assertEqual([self.metric(k) for k in (33,34,3,4,46)], [0,0,0,0,0])

    def test_invalid_clock_preserves_gc_result_and_disqualifies_partial_timings(self):
        self.results[:] = [1, -1, 0, 0]
        self.times[:] = [10,11, 0,31, 50,49, 60,60]
        self.assertEqual([self.attempt(7) for _ in range(4)], [1,-1,0,0])
        # This failure must not be mistaken for a short successful pause.
        self.assertEqual([self.metric(k) for k in (33,34,3,4,46)], [4,1,-1,-1,3])


if __name__ == "__main__":
    unittest.main()
