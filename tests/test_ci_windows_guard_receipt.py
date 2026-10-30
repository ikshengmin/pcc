"""Host receipt contracts with a simulated guard; not Windows/native proof."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import ci_bootstrap_partition as partition
from scripts import ci_pytest_markers as gate
from scripts import platform_process_watchdog as watchdog


class WindowsGuardReceiptTests(unittest.TestCase):
    def failed_guard(self, error, active_seconds, cleanup_seconds):
        clock = SimpleNamespace(now=1000.0)
        environment = {"SENTINEL": "preserved"}
        original_save = gate.save
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            command = gate.marker_command(directory, "integration", "bootstrap")

            def save(path, value):
                original_save(path, value)
                clock.now += 0.125  # Outer owner also charges receipt writes.

            def fail(actual_command, **options):
                self.assertEqual(actual_command, command)
                self.assertEqual(options, {
                    "cwd": gate.ROOT,
                    "env": dict(environment, PCC_WORKER_TREE_BUDGET_BYTES="17179869184"),
                    "log_path": directory / "target.stdout",
                    "timeout": 1800,
                    "rss_limit": 17179869184,
                })
                running = json.loads((directory / "result.json").read_text())
                self.assertEqual(running["status"], "RUNNING")
                self.assertNotIn("seconds", running)
                try:
                    clock.now += active_seconds
                    raise error
                finally:
                    clock.now += cleanup_seconds

            owner_started = clock.now
            with patch.object(gate, "time", SimpleNamespace(monotonic=lambda: clock.now)), \
                    patch.object(gate, "save", save), \
                    patch.object(watchdog, "run", fail):
                with self.assertRaises(type(error)) as caught:
                    gate.run_guarded(directory, "integration", environment,
                                     "windows-x86_64", partition="bootstrap")
            self.assertIs(caught.exception, error)
            record = json.loads((directory / "result.json").read_text())
        self.assertEqual(record["status"], "FAIL")
        self.assertEqual(record["error"], type(error).__name__ + ": " + str(error))
        self.assertEqual(record["seconds"], active_seconds + cleanup_seconds)
        self.assertEqual(record["requested_timeout"], 1800)
        self.assertEqual(record["requested_rss_limit"], 17179869184)
        self.assertIs(record["outer_performance_lock"], False)
        self.assertNotIn("returncode", record)
        self.assertNotIn("return_contract", record)
        self.assertEqual(environment, {"SENTINEL": "preserved"})
        owner_elapsed = clock.now - owner_started
        self.assertEqual(owner_elapsed, record["seconds"] + 0.25)
        return record, owner_elapsed

    def remaining(self, guard, elapsed):
        identity = {
            "source": {"commit": "a" * 40, "source_sha256": "b" * 64, "clean_commit": True},
            "producer": {"run_id": 123, "run_attempt": 2, "sha": "a" * 40},
            "platform": "windows-x86_64",
            "manifest_sha256": "c" * 64,
        }
        owner = dict(identity, partition="bootstrap", marker="integration", status="FAIL",
                     timeout_s=1800, guard_elapsed_s=elapsed)
        return partition.remaining_seconds(owner, guard=guard, **identity)

    def test_timeout_records_cleanup_and_still_exhausts_original_budget(self):
        guard, elapsed = self.failed_guard(TimeoutError("stage deadline exceeded"), 1800, 0.25)
        self.assertGreater(guard["seconds"], guard["requested_timeout"])
        with self.assertRaisesRegex(ValueError, "BUDGET_EXHAUSTED: integration remainder UNRUN"):
            self.remaining(guard, elapsed)

    def test_other_guard_errors_preserve_elapsed_and_original_exception(self):
        for error in (OSError("launch failed"), RuntimeError("process-tree RSS cap exceeded"),
                      KeyboardInterrupt("interrupted")):
            with self.subTest(error=type(error).__name__):
                guard, elapsed = self.failed_guard(error, 2.0, 0.25)
                self.assertEqual(self.remaining(guard, elapsed), 1797)

    def test_missing_or_undercounted_guard_time_still_fails_closed(self):
        guard, elapsed = self.failed_guard(RuntimeError("guard failed"), 2.0, 0.25)
        missing = dict(guard)
        del missing["seconds"]
        with self.assertRaisesRegex(ValueError, "undercounts or lacks guard elapsed"):
            self.remaining(missing, elapsed)
        with self.assertRaisesRegex(ValueError, "undercounts or lacks guard elapsed"):
            self.remaining(guard, guard["seconds"] - 0.125)

    def test_success_keeps_watchdog_elapsed_and_return_contract(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            command = gate.marker_command(directory, "integration")
            result = {"command": command, "peak_tree_rss": 123, "seconds": 0.2}
            with patch.object(watchdog, "run", return_value=result) as run, \
                    patch.object(gate, "time", SimpleNamespace(monotonic=lambda: 1000.0)):
                gate.run_guarded(directory, "integration", {}, "windows-x86_64")
            run.assert_called_once()
            record = json.loads((directory / "result.json").read_text())
        self.assertEqual(record["seconds"], 0.2)
        self.assertEqual(record["status"], "COMPLETE")
        self.assertEqual(record["returncode"], 0)
        self.assertEqual(record["return_contract"],
                         "normal return after child exit 0 and Job cleanup/final wait")
        self.assertNotIn("error", record)


if __name__ == "__main__":
    unittest.main()
