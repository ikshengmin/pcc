"""Bounded host-only paired benchmark; prints results, writes no artifacts.

Use the repository's approved host interpreter and external performance lock.
Time and tracemalloc are measured separately; no timing assertion is a test.
"""

import argparse
import gc
import json
import platform
from pathlib import Path
import statistics
import sys
import time
import tracemalloc

from pcc.frontends.python import type_infer
from pcc.frontends.python.py_lift import parse_and_lift

from .frontend_experiment import (
    REFERENCE_SCOPE,
    inventory,
    narrowing_fixture,
    scope_implementation,
    wide_fixture,
)
from .scope_record import RecordScope, Region


def paired_times(operations, samples, iterations):
    times = {name: [] for name in operations}
    for operation in operations.values():
        operation()
    for sample in range(samples):
        order = list(operations)
        if sample % 2:
            order.reverse()
        for name in order:
            gc.collect()
            start = time.perf_counter_ns()
            for _ in range(iterations):
                operations[name]()
            times[name].append((time.perf_counter_ns() - start) / iterations)
    medians = {name: statistics.median(values) for name, values in times.items()}
    return {
        "median_ns": medians,
        "min_ns": {name: min(values) for name, values in times.items()},
        "max_ns": {name: max(values) for name, values in times.items()},
        "record_over_reference": medians["record"] / medians["reference"],
    }


def peak_bytes(operation):
    gc.collect()
    tracemalloc.start()
    result = operation()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del result
    return {"retained_bytes": current, "peak_bytes": peak}


def construct(scope_type, count, width):
    outer = scope_type()
    outer.define("outer", 1)
    scopes = []
    for _ in range(count):
        scope = scope_type(outer)
        for field in range(width):
            scope.define("field" + str(field), field)
        # Read a local and an inherited field: representation cannot win just
        # by skipping actual lookups. Retain all records for this memory test.
        assert scope.lookup("outer") == 1
        if width:
            assert scope.lookup("field0") == 0
        scopes.append(scope)
    return scopes


def run(samples, iterations, records, *, sources=(), skip_micro=False):
    report = {
        "mode": "CPython host-only; actual PCC parse/infer; no native executable",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "samples": samples,
        "iterations": iterations,
        "record_count": records,
        "frontend": {},
        "representation": {},
    }
    fixtures = {
        "read_only_narrowing": narrowing_fixture(),
        "narrowing_with_local_writes": narrowing_fixture(writes=True),
        "wide_locals": wide_fixture(),
    }
    if sources:
        fixtures = {str(path): Path(path).read_text() for path in sources}
    for name, source in fixtures.items():
        parsed = parse_and_lift(source, name + ".py", name)

        def baseline():
            with scope_implementation(REFERENCE_SCOPE):
                return type_infer.infer_module(parsed)

        def record():
            with scope_implementation(RecordScope):
                return type_infer.infer_module(parsed)

        assert baseline() == record()
        _, baseline_counts = inventory(source, REFERENCE_SCOPE)
        _, record_counts = inventory(source, RecordScope)
        report["frontend"][name] = {
            "time": paired_times({"reference": baseline, "record": record}, samples, iterations),
            "allocations": {"reference": baseline_counts, "record": record_counts},
            "memory": {"reference": peak_bytes(baseline), "record": peak_bytes(record)},
        }
    if skip_micro:
        return report
    for width in (0, 1, 8):
        baseline = lambda: construct(REFERENCE_SCOPE, records, width)
        record = lambda: construct(RecordScope, records, width)
        report["representation"][str(width)] = {
            "time": paired_times({"reference": baseline, "record": record}, samples, 1),
            "memory": {"reference": peak_bytes(baseline), "record": peak_bytes(record)},
        }
    with Region() as region:
        borrow = region.new_scope()
        borrow.define("x", 7)
        raw = borrow._scope

        def raw_lookups():
            total = 0
            for _ in range(records * 10):
                total += raw.lookup("x")
            assert total == records * 70

        def checked_lookups():
            total = 0
            for _ in range(records * 10):
                total += borrow.lookup("x")
            assert total == records * 70

        report["dynamic_origin_guard"] = paired_times(
            {"reference": raw_lookups, "record": checked_lookups}, samples, 1,
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--records", type=int, default=4000)
    parser.add_argument("--source", action="append", default=[],
                        help="infer these actual source files instead of generated fixtures")
    parser.add_argument("--skip-micro", action="store_true",
                        help="measure only the frontend inputs")
    args = parser.parse_args()
    if not (1 <= args.samples <= 21 and 1 <= args.iterations <= 50 and 1 <= args.records <= 20000):
        parser.error("bounded experiment: samples 1..21, iterations 1..50, records 1..20000")
    print(json.dumps(run(args.samples, args.iterations, args.records,
                         sources=args.source, skip_micro=args.skip_micro), indent=2))


if __name__ == "__main__":
    main()
