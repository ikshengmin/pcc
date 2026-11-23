"""Remove what only collectors 1-4 could reach, until nothing changes.

One round: ``sweep_one_collector.py facts`` derives the words no remaining
function writes and the functions whose body became empty;
``fold_one_collector.py --write`` folds every runtime module under those
facts and the fixed ones (the selected collector is 0); and
``sweep_one_collector.py apply`` deletes the functions nothing reaches any
more. ``forced.json`` holds facts read from folded bodies by hand.
Afterwards ``cleanup_declarations.py apply`` removes the ABI entries, header
prototypes, state words and Makefile names of what is gone, and
``prune_tests.py report`` lists the tests that name it. Intermediate files
go to build/one-collector/. Compile every changed module and run the
collector's tests after a run: the tools prove folds, not behaviour.
"""
import hashlib, subprocess, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
(REPO / "build" / "one-collector").mkdir(parents=True, exist_ok=True)
RUNTIME = REPO / "pcc/runtime/py"

def digest():
    h = hashlib.sha256()
    for p in sorted(RUNTIME.glob("*.py")):
        h.update(p.read_bytes())
    return h.hexdigest()

def run(*args):
    out = subprocess.run([sys.executable, *args], capture_output=True, text=True, cwd=REPO)
    if out.returncode != 0:
        print(out.stdout[-2000:]); print(out.stderr[-3000:])
        raise SystemExit("failed: " + " ".join(args))
    return out.stdout

before = None
for round_number in range(1, 13):
    now = digest()
    if now == before:
        print("fixed point after", round_number - 1, "rounds")
        break
    before = now
    facts = run("scripts/one_collector/sweep_one_collector.py", "facts").strip().splitlines()[-1]
    files = [str(p) for p in sorted(RUNTIME.glob("*.py"))]
    folded = run("scripts/one_collector/fold_one_collector.py", "--write", *files).strip().splitlines()[-1]
    swept = run("scripts/one_collector/sweep_one_collector.py", "apply").strip().splitlines()[-1]
    lines = sum(p.read_text().count("\n") for p in RUNTIME.glob("*.py"))
    print(f"round {round_number}: {facts} | {folded} | {swept} | runtime lines {lines}")
