"""Finite host-only research checks; no compiler or emitted program runs."""
from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "frame_owner_experiment", ROOT / "scripts/research/compare_x86_frame_owner_search.py"
)
experiment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(experiment)


class OwnerSearchExperimentTests(unittest.TestCase):
    def test_all_legal_hints_on_small_nonuniform_block_maps(self):
        for size in range(1, 33):
            starts = [index * index + index for index in range(size)]
            for owner, start in enumerate(starts):
                stop = starts[owner + 1] if owner + 1 < size else start + 3
                for line in (start, stop - 1):
                    for previous in range(owner + 1):
                        self.assertEqual(experiment.production_owner(starts, line), owner)
                        self.assertEqual(experiment.lower_bound_owner(starts, line, previous), owner)
                        self.assertEqual(experiment.bounded_hint_owner(starts, line, previous), owner)

    def test_default_experiments_retain_dense_win_and_sparse_regressions(self):
        result = experiment.default_experiments()
        self.assertTrue(all(value["owners_equal"] for value in result.values()))
        dense = result["dense"]
        self.assertLess(dense["bounded_hint"]["reads"], dense["production_reads"] // 2)
        for name in ("four_block_gaps", "single_distant"):
            self.assertGreater(result[name]["bounded_hint"]["reads"], result[name]["production_reads"])
        self.assertGreater(result["lower_bound_counterexample"]["lower_bound"]["queries_with_more_reads"], 0)

    def test_repeated_queries_and_last_block_remain_valid(self):
        result = experiment.compare([10, 12, 90], [10, 10, 12, 89, 90, 200])
        self.assertTrue(result["owners_equal"])

    def test_invalid_shapes_ranges_and_query_order_are_rejected(self):
        cases = [
            {}, {"block_first": [0], "queries": [0], "extra": 1},
            {"block_first": [], "queries": [0]},
            {"block_first": [0], "queries": []},
            {"block_first": [0, 0], "queries": [0]},
            {"block_first": [2, 1], "queries": [2]},
            {"block_first": [2], "queries": [1]},
            {"block_first": [0], "queries": [2, 1]},
            {"block_first": [False], "queries": [0]},
            {"block_first": [0], "queries": [True]},
            {"block_first": [0], "queries": [1.0]},
            {"block_first": [-1], "queries": [0]},
            {"block_first": [0], "queries": [experiment.MAX_LINE + 1]},
            {"block_first": list(range(experiment.MAX_BLOCKS + 1)), "queries": [0]},
            {"block_first": [0], "queries": [0] * (experiment.MAX_QUERIES + 1)},
        ]
        for value in cases:
            with self.subTest(value=str(value)[:80]):
                with self.assertRaises(ValueError):
                    experiment.validate_input(value)

    def test_json_size_duplicate_key_and_malformed_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queries.json"
            for payload in (b" " * (experiment.MAX_BYTES + 1), b"{", b"\xff",
                            b'{"block_first":[0],"queries":[0],"queries":[1]}'):
                path.write_bytes(payload)
                with self.assertRaises(ValueError):
                    experiment.read_input(path)

    def test_supplied_cli_prints_scope_and_no_production_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queries.json"
            path.write_text('{"block_first":[0,2,5],"queries":[0,2,2,9]}')
            output = io.StringIO()
            with redirect_stdout(output):
                experiment.main(["--input", str(path)])
            result = json.loads(output.getvalue())
            self.assertFalse(result["production_modified"])
            self.assertIn("not native speed", result["scope"])
            self.assertEqual(result["experiments"]["supplied"]["queries"], 4)


if __name__ == "__main__":
    unittest.main()
