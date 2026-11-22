"""Manual owned-native scaling probe for the reviewed issue 9 sweep.

From the repository root, provide the verified source-matched runtime as
PCC_RUNTIME_ARCHIVE and a fresh output directory. Use the guarded command in
the issue 9 investigation; this is not a pcc_perf acceptance benchmark.
"""
import itertools
import json
import os
from pathlib import Path
import random
import re
import statistics
import sys

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.owned_runtime_c_fixture import admitted_runtime
from tests.python.process_timeout import run_process_group_timeout

out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
repo = Path.cwd()
binary = out / "sweep_scale"
compile_owned_c_with_runtime(
    repo / "tests/fixtures/native/gc_acyclic_layers_scale.c", binary,
    runtime_archive=admitted_runtime(),
    cpp_args=("-I", str(repo / "pcc/runtime/src")),
)
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
