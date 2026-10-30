"""Bounded paired host measurements; prints JSON to stdout, never production."""

import argparse
from dataclasses import asdict
import gc
import importlib.util
import json
from pathlib import Path
import platform
try:
    import resource
except ImportError:  # Correctness tests also import this module on Windows.
    resource = None
import statistics
import sys
import time
import tracemalloc

if __package__:
    from .closures import ActionKeyScope, Counts, count_baseline
    from .waitset import WaitsetHarness
else:
    from closures import ActionKeyScope, Counts, count_baseline
    from waitset import WaitsetHarness


def cap_address_space():
    # Reduce, never raise, inherited limits. One host process, no child builds.
    if resource is None or sys.platform != "linux":
        raise SystemExit("bounded measurements require Linux resource limits; host correctness tests remain portable")
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    finite = [512 * 1024 * 1024]
    if soft != resource.RLIM_INFINITY:
        finite.append(soft)
    if hard != resource.RLIM_INFINITY:
        finite.append(hard)
    resource.setrlimit(resource.RLIMIT_AS, (min(finite), hard))


def load_dag(source_root):
    path = source_root / "pcc" / "frontends" / "python" / "module_action_dag.py"
    spec = importlib.util.spec_from_file_location("pcc_research_action_dag", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def waitset_sample(source, case, pooled, iterations, trace=False):
    backend, live, ready_each = case
    harness = WaitsetHarness(source, pooled=pooled, backend=backend)
    owner = object()
    if not ready_each:
        for fd in range(live):
            harness.add(fd)
    gc.collect()
    if trace:
        tracemalloc.start()
    start = time.perf_counter_ns()
    cpu_start = time.process_time_ns()
    digest = 0
    for _ in range(iterations):
        if ready_each:
            for fd in range(live):
                generation = harness.add(fd)
                harness.kernel_events.append((fd, generation, 1))
        batch = harness.prepare(owner)
        harness.start(batch, owner)
        harness.block_returned(batch, owner)
        status, ready, timed = harness.finish(batch, owner)
        assert status == 0 and not timed
        digest += sum(fd + bits for fd, bits in ready)
    elapsed = (time.perf_counter_ns() - start) / 1e9
    cpu_elapsed = (time.process_time_ns() - cpu_start) / 1e9
    peak = None
    if trace:
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    memory = harness.memory
    counts = {key: getattr(memory, key) for key in (
        "event_allocations", "event_frees", "event_requested_bytes",
        "event_live_bytes", "event_peak_bytes")}
    assert harness.blocking_depth == 0
    harness.dispose()
    assert not memory.blocks and memory.event_live_bytes == 0
    return {"host_seconds": elapsed, "host_cpu_seconds": cpu_elapsed, "tracemalloc_peak_bytes": peak,
            "checksum": digest, **counts}


def action_sample(module, state, stages, pooled, rounds=1, trace=False, counts=None, names=None, eager=True):
    if names is None:
        names = [item.name for item in state.modules]
    gc.collect()
    if trace:
        tracemalloc.start()
    start = time.perf_counter_ns()
    cpu_start = time.process_time_ns()
    outputs = None
    for _ in range(rounds):
        scope = ActionKeyScope(module, state, counts, eager=eager) if pooled else None
        outputs = tuple((scope.key(name, stage) if pooled else module.action_key(state, name, stage))
                        for name in names for stage in stages)
    elapsed = (time.perf_counter_ns() - start) / 1e9
    cpu_elapsed = (time.process_time_ns() - cpu_start) / 1e9
    peak = None
    if trace:
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    return {"host_seconds": elapsed, "host_cpu_seconds": cpu_elapsed, "tracemalloc_peak_bytes": peak, "keys": outputs}


def pair_summary(samples):
    baseline = [pair["baseline"]["host_seconds"] for pair in samples]
    candidate = [pair["candidate"]["host_seconds"] for pair in samples]
    ratios = [b / c for b, c in zip(baseline, candidate)]
    cpu_baseline = [pair["baseline"]["host_cpu_seconds"] for pair in samples]
    cpu_candidate = [pair["candidate"]["host_cpu_seconds"] for pair in samples]
    cpu_ratios = [b / c for b, c in zip(cpu_baseline, cpu_candidate)]
    return {"baseline_median_seconds": statistics.median(baseline),
            "candidate_median_seconds": statistics.median(candidate),
            "paired_speedup_median": statistics.median(ratios),
            "paired_speedup_range": [min(ratios), max(ratios)],
            "baseline_median_cpu_seconds": statistics.median(cpu_baseline),
            "candidate_median_cpu_seconds": statistics.median(cpu_candidate),
            "paired_cpu_speedup_median": statistics.median(cpu_ratios),
            "paired_cpu_speedup_range": [min(cpu_ratios), max(cpu_ratios)]}


def benchmark(source_root, metadata, iterations, pairs, section="all", action_rounds=1):
    source = source_root / "pcc" / "runtime" / "py" / "freestanding_io_waitset.py"
    results = {"claim": "host algorithm/adapter measurements; no native throughput/p99/GC claim",
               "python": sys.version, "platform": platform.platform(), "waitset": {}, "actions": {}}
    cases = {"epoll_empty": (2, 0, False), "epoll_ready8": (2, 8, True),
             "epoll_steady256": (2, 256, False), "kqueue_steady256": (1, 256, False)}
    if section == "actions":
        cases = {}
    for name, case in cases.items():
        for pooled in (False, True):
            waitset_sample(source, case, pooled, 2)
        samples = []
        for pair in range(pairs):
            row = {}
            for pooled in ((False, True) if pair % 2 == 0 else (True, False)):
                row["candidate" if pooled else "baseline"] = waitset_sample(source, case, pooled, iterations)
            assert row["baseline"]["checksum"] == row["candidate"]["checksum"]
            samples.append(row)
        peaks = {"candidate" if pooled else "baseline": waitset_sample(source, case, pooled, iterations, trace=True)
                 for pooled in (False, True)}
        results["waitset"][name] = {"iterations": iterations, "summary": pair_summary(samples),
                                    "samples": samples, "separate_memory_observation": peaks}
    if section == "waitset":
        results["process_maxrss_kib_linux"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return results
    module = load_dag(source_root)
    state = module.load_graph_state(json.loads(metadata.read_text())["graph_state"])
    if state is None:
        raise ValueError("invalid action metadata")
    results["action_modules"] = len(state.modules)
    results["action_edges"] = sum(len(item.dependencies) for item in state.modules)
    leaf = min(state.modules, key=lambda item: (len(item.dependencies), item.name)).name
    action_cases = {"five_stage_rebuild_keys": (module.ACTION_STAGES, None),
                    "lookup_module_ir": (("module-ir",), None),
                    "single_leaf_lookup": (("module-ir",), [leaf])}
    for case_name, (stages, names) in action_cases.items():
        for eager in (True, False):
            name = case_name + ("_eager" if eager else "_lazy")
            samples = []
            for pooled in (False, True):
                action_sample(module, state, stages, pooled, names=names, eager=eager)
            for pair in range(pairs):
                row = {}
                for pooled in ((False, True) if pair % 2 == 0 else (True, False)):
                    row["candidate" if pooled else "baseline"] = action_sample(
                        module, state, stages, pooled, rounds=action_rounds,
                        names=names, eager=eager)
                assert row["baseline"]["keys"] == row["candidate"]["keys"]
                for result in row.values():
                    result["key_count"] = len(result.pop("keys"))
                samples.append(row)
            baseline_counts, candidate_counts = Counts(), Counts()
            with count_baseline(module, baseline_counts):
                expected = action_sample(module, state, stages, False, names=names)["keys"]
            actual = action_sample(module, state, stages, True, counts=candidate_counts,
                                   names=names, eager=eager)["keys"]
            assert expected == actual
            peaks = {}
            for pooled in (False, True):
                result = action_sample(module, state, stages, pooled, trace=True,
                                       names=names, eager=eager)
                result.pop("keys")
                peaks["candidate" if pooled else "baseline"] = result
            results["actions"][name] = {"summary": pair_summary(samples), "samples": samples,
                                       "rounds_per_timing": action_rounds,
                                       "baseline_counts": asdict(baseline_counts),
                                       "candidate_counts": asdict(candidate_counts),
                                       "separate_memory_observation": peaks}
    results["process_maxrss_kib_linux"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--pairs", type=int, default=5)
    parser.add_argument("--section", choices=("all", "waitset", "actions"), default="all")
    parser.add_argument("--action-rounds", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 1000 or not 1 <= args.pairs <= 9 or not 1 <= args.action_rounds <= 20:
        raise SystemExit("bounded experiment: iterations 1..1000, pairs 1..9, action rounds 1..20")
    cap_address_space()
    print(json.dumps(benchmark(args.source_root, args.metadata, args.iterations, args.pairs,
                               args.section, args.action_rounds), indent=2))
