"""Manual owned-native scaling probe for the reviewed issue 9 sweep.

From the repository root, provide the verified source-matched runtime as
PCC_RUNTIME_ARCHIVE and a fresh output directory. Use the guarded command in
the issue 9 investigation; this is not a pcc_perf acceptance benchmark.
"""
import argparse
import itertools
import json
import os
from pathlib import Path
import random
import re
import statistics

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.owned_runtime_c_fixture import admitted_runtime
from tests.python.process_timeout import run_process_group_timeout

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("out", type=Path)
parser.add_argument("--compile-only", action="store_true",
                    help="link the probe with the owned runtime for the current source tree")
pairing = parser.add_mutually_exclusive_group()
pairing.add_argument("--weak-peer-base-binary", type=Path,
                     help="same probe linked before the parked-peer snapshot")
pairing.add_argument("--target-base-binary", type=Path,
                     help="same probe linked before the target-local directory")
args = parser.parse_args()
out = args.out.resolve()
out.mkdir(parents=True, exist_ok=True)
repo = Path.cwd()
binary = out / "sweep_scale"
compile_owned_c_with_runtime(
    repo / "tests/fixtures/native/gc_acyclic_layers_scale.c", binary,
    runtime_archive=admitted_runtime(),
    cpp_args=("-I", str(repo / "pcc/runtime/src")),
)
if args.compile_only:
    raise SystemExit(0)
base_binary = args.target_base_binary or args.weak_peer_base_binary
target_mode = args.target_base_binary is not None
if base_binary is not None:
    base = base_binary.resolve(strict=True)
    sizes = (4, 16, 64) if target_mode else (16, 64)
    cases = list(itertools.product((1, 3), sizes, (0, 4096, 16384)))
    records = {case: [] for case in cases}
    rng = random.Random(9070924)
    controls = []
    for trial in range(5):
        if target_mode:
            control_pair = []
            control_slots = (0, len(cases))
        else:
            control_slots = ()
        rng.shuffle(cases)
        for i in range(len(cases) + 1):
            if i in control_slots:
                # The exact identical parent arm brackets each trial.
                # It exposes host drift without changing collector or input.
                probe = run_process_group_timeout(
                    [str(base), "64", "0", "0", "1"], timeout=12,
                    env=dict(os.environ, PCC_GC_BACKEND="1",
                             PCC_GC_DEBT_THRESHOLD="1099511627776"),
                )
                prefix = "weak=0 scans=1 n=64 live=0 del=64 reclaimed=64 full=0 windows=64 "
                us = re.fullmatch(r"us=(\d+)\n", probe.stdout.removeprefix(prefix))
                assert probe.returncode == 0 and not probe.stderr and us is not None, probe
                control_pair.append(int(us.group(1)))
            if i == len(cases):
                break
            gc, n, weak = cases[i]
            arms = [("base", base), ("candidate", binary)]
            if (trial + i) % 2:
                arms.reverse()
            times = {}
            for arm, executable in arms:
                if target_mode:
                    scans = 1 if arm == "base" else 0
                else:
                    scans = 0 if weak == 0 else n if arm == "base" else 1
                expected = f"weak={weak} scans={scans} n={n} live=0 del={n} reclaimed={n} full=0 windows={n} "
                command = [str(executable), str(n), "0", str(weak)]
                if target_mode:
                    command.append("1")  # one rooted weakref for each dying candidate
                run = run_process_group_timeout(
                    command, timeout=12,
                    env=dict(os.environ, PCC_GC_BACKEND=str(gc),
                             PCC_GC_DEBT_THRESHOLD="1099511627776"),
                )
                us = re.fullmatch(r"us=(\d+)\n", run.stdout.removeprefix(expected))
                assert run.returncode == 0 and not run.stderr and us is not None, (gc, arm, run)
                times[arm] = int(us.group(1))
            records[gc, n, weak].append(times)
        if target_mode:
            controls.append(control_pair)
    if target_mode:
        print(json.dumps({
            "control": "identical parent GC1, n64, no unrelated weakrefs",
            "pairs_us": controls,
            "median_ratio": statistics.median(b / a for a, b in controls),
        }), flush=True)
    for (gc, n, weak), pairs in sorted(records.items()):
        print(json.dumps({
            "gc": gc, "n": n, "weak": weak, "pairs_us": pairs,
            "wins": sum(p["candidate"] < p["base"] for p in pairs),
            "median_ratio": statistics.median(p["candidate"] / p["base"] for p in pairs),
        }), flush=True)
    raise SystemExit(0)

grid = list(itertools.product((1, 3), (4, 16, 64), (0, 4096, 16384)))
cells = {cell: [] for cell in grid}
rng = random.Random(9070922)
for trial in range(5):
    rng.shuffle(grid)
    for backend, count, live in grid:
        run = run_process_group_timeout([str(binary), str(count), str(live)],
            timeout=8, env=dict(os.environ, PCC_GC_BACKEND=str(backend),
                                PCC_GC_DEBT_THRESHOLD="1099511627776"))
        print(f"g={backend} trial={trial} rc={run.returncode} {run.stdout.strip()} {run.stderr.strip()}",
              flush=True)
        expected = f"n={count} live={live} del={count} reclaimed={count} full=0"
        match = re.fullmatch(r"windows=(\d+) us=(\d+)\n", run.stdout[len(expected)+1:])
        assert run.returncode == 0 and run.stdout.startswith(expected) and match is not None, run
        cells[backend, count, live].append(tuple(map(int, match.groups())))
for key, runs in sorted(cells.items()):
    gc, n, live = key
    print(json.dumps({"gc": gc, "n": n, "live": live, "windows": [t[0] for t in runs],
                      "us": [t[1] for t in runs], "median_us": statistics.median(t[1] for t in runs)}))
