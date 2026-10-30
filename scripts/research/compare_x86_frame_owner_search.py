"""Compare research-only x86 frame-owner lookup prototypes with current code.

Run: uv run python scripts/research/compare_x86_frame_owner_search.py
Or pass --input FILE containing exactly {"block_first": [...], "queries": [...]}.
Queries must be nondecreasing: source ends and selected frame accesses have
that property, but branch targets do not. This tool does not capture queries.

The bounded hint saves list reads on dense inputs but costs extra reads on
some sparse inputs; even a previous-owner binary lower bound can cost one
extra comparison. Neither prototype is enabled in the compiler. List reads
are not CPU time. Actual query density, owner phase share and native benefit
remain unknown; synthetic wins do not justify a production optimization.
No compilation, subprocess, native execution, timing or profiling occurs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from pcc.backend.x86_64_frame_registers import _block_of as production_owner

MAX_BYTES = 1_048_576
MAX_BLOCKS = 16_384
MAX_QUERIES = 65_536
MAX_LINE = 2_147_483_647


def lower_bound_owner(starts, line, previous):
    low = previous
    high = len(starts) - 1
    while low < high:
        middle = (low + high + 1) // 2
        if starts[middle] <= line:
            low = middle
        else:
            high = middle - 1
    return low


def bounded_hint_owner(starts, line, previous):
    low = previous
    high = len(starts) - 1
    probes = 0
    while low < high and probes < 4:
        if starts[low + 1] > line:
            return low
        low += 1
        probes += 1
    return lower_bound_owner(starts, line, low)


class CountedStarts(list):
    def __init__(self, values):
        super().__init__(values)
        self.reads = 0

    def __getitem__(self, index):
        self.reads += 1
        return super().__getitem__(index)


def validate_input(value):
    if type(value) is not dict or set(value) != {"block_first", "queries"}:
        raise ValueError("input must contain exactly block_first and queries")
    starts, queries = value["block_first"], value["queries"]
    for name, values, limit in (("block_first", starts, MAX_BLOCKS),
                                ("queries", queries, MAX_QUERIES)):
        if type(values) is not list or not 1 <= len(values) <= limit:
            raise ValueError(f"{name} must be a nonempty list of at most {limit} entries")
        if any(type(item) is not int or not 0 <= item <= MAX_LINE for item in values):
            raise ValueError(f"{name} must contain integers in 0..{MAX_LINE}, not bools")
    if any(left >= right for left, right in zip(starts, starts[1:])):
        raise ValueError("block_first must be strictly increasing")
    if queries[0] < starts[0] or any(left > right for left, right in zip(queries, queries[1:])):
        raise ValueError("queries must be nondecreasing and at least the first block start")
    return starts, queries


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def read_input(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError(f"input exceeds {MAX_BYTES} bytes")
    return validate_input(json.loads(raw, object_pairs_hook=_unique_object))


def compare(starts, queries):
    starts, queries = validate_input({"block_first": starts, "queries": queries})
    arrays = [CountedStarts(starts) for _ in range(3)]
    owners = [0, 0]
    regressions = [0, 0]
    extra = [0, 0]
    for line in queries:
        before = [values.reads for values in arrays]
        expected = production_owner(arrays[0], line)
        owners[0] = lower_bound_owner(arrays[1], line, owners[0])
        owners[1] = bounded_hint_owner(arrays[2], line, owners[1])
        if owners != [expected, expected]:
            raise AssertionError(f"owner mismatch at line {line}: {owners}, expected {expected}")
        baseline_reads = arrays[0].reads - before[0]
        for index in range(2):
            delta = arrays[index + 1].reads - before[index + 1] - baseline_reads
            regressions[index] += delta > 0
            extra[index] = max(extra[index], delta)
    return {
        "blocks": len(starts), "queries": len(queries), "owners_equal": True,
        "production_reads": arrays[0].reads,
        "lower_bound": {"reads": arrays[1].reads, "queries_with_more_reads": regressions[0],
                        "max_extra_reads_per_query": extra[0]},
        "bounded_hint": {"reads": arrays[2].reads, "queries_with_more_reads": regressions[1],
                         "max_extra_reads_per_query": extra[1]},
    }


def default_experiments():
    starts = list(range(0, 4096, 8))
    patterns = {
        "dense": list(range(4096)),
        "one_per_block": list(range(0, 4096, 8)),
        "two_block_gaps": list(range(0, 4096, 16)),
        "four_block_gaps": list(range(0, 4096, 32)),
        "single_distant": [4095],
        "sparse_three": [0, 4080, 4095],
    }
    results = {name: compare(starts, queries) for name, queries in patterns.items()}
    results["lower_bound_counterexample"] = compare(list(range(11)), [1, 5])
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="bounded JSON of one ordered query stream")
    args = parser.parse_args(argv)
    try:
        results = ({"supplied": compare(*read_input(args.input))} if args.input
                   else default_experiments())
    except (OSError, ValueError, RecursionError) as error:
        parser.error(str(error))
    print(json.dumps({
        "schema": "pcc.frame-owner-search-experiment.v1",
        "scope": "host list-read counts and owner equality only; not native speed",
        "production_modified": False,
        "remaining_evidence": ["actual query density", "frame-owner phase share", "native benefit"],
        "experiments": results,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
