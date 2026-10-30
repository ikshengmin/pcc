"""Small fixed-seed host PCC comparison; never a native/Stage1 benchmark."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
import time
import warnings

from .pcc_adapter import PCCAdapter
from .safety import run_python, validate
from .templates import MAX_DEPTH, mutant


MAX_CASES_PER_MODE = 20
MODES = ("typed", "scope_only", "naive")


def run(output: Path, count: int = 20, seed: int = 42, depth: int = 2,
        source_root=None, source_manifest=None):
    if not 1 <= count <= MAX_CASES_PER_MODE or not 0 <= depth <= MAX_DEPTH:
        raise ValueError("initial research bound: at most 20 cases/mode, depth 2")
    output.mkdir(parents=True, exist_ok=True)
    adapter = PCCAdapter(source_root, source_manifest)
    rows = []
    # Validate the complete generated corpus before executing any generated
    # source. No API accepts arbitrary source files or command strings.
    cases = []
    for index in range(count):
        for mode in MODES:
            started = time.perf_counter_ns()
            template, term, source = mutant(seed, index, mode, depth)
            generation_ns = time.perf_counter_ns() - started
            validate(source)
            cases.append((index, mode, template, term, source, generation_ns))
    with (output / "cases.jsonl").open("w") as stream:
        for index, mode, template, term, source, generation_ns in cases:
            row = {"index": index, "mode": mode, "template": template.name,
                   "generation_ns": generation_ns,
                   "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                   "term": term.text}
            source_path = output / "corpus" / mode / f"case_{index:03}.py"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(source)
            started = time.perf_counter_ns()
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", SyntaxWarning)
                    result = run_python(source)
                row.update(python_valid=True, python_result=repr(result),
                           python_result_type=type(result).__name__)
            except Exception as error:
                row.update(python_valid=False, python_error_type=type(error).__name__,
                           python_error=str(error))
            row["python_check_ns"] = time.perf_counter_ns() - started
            row["pcc"], text = adapter.lower(source)
            row["useful_valid_check"] = row["python_valid"] and row["pcc"]["accepted"]
            # Failed dynamic Python evaluation is not itself a compiler bug.
            row["compiler_candidate_failure"] = row["python_valid"] and not row["pcc"]["accepted"]
            rows.append(row)
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
            if row["compiler_candidate_failure"]:
                failures = output / "failures"
                failures.mkdir(exist_ok=True)
                (failures / f"{mode}_{index:03}.py").write_text(source)
                (failures / f"{mode}_{index:03}.json").write_text(json.dumps(row, indent=2))
    summaries = {}
    for mode in MODES:
        selected = [row for row in rows if row["mode"] == mode]
        useful = sum(row["useful_valid_check"] for row in selected)
        pcc_ns = sum(sum(row["pcc"]["timings_ns"].values()) for row in selected)
        summaries[mode] = {
            "cases": len(selected), "python_valid": sum(row["python_valid"] for row in selected),
            "pcc_accepted": sum(row["pcc"]["accepted"] for row in selected),
            "useful_valid_checks": useful,
            "candidate_failures": sum(row["compiler_candidate_failure"] for row in selected),
            "python_error_counts": dict(Counter(row.get("python_error_type") for row in selected if not row["python_valid"])),
            "generation_ns_total": sum(row["generation_ns"] for row in selected),
            "pcc_ns_total": pcc_ns,
            "pcc_ns_per_useful_valid_check": pcc_ns / useful if useful else None,
            "pcc_ns_median_per_case": statistics.median(sum(row["pcc"]["timings_ns"].values()) for row in selected),
            "stage_ns_total": {stage: sum(row["pcc"]["timings_ns"].get(stage, 0) for row in selected)
                               for stage in sorted({stage for row in selected for stage in row["pcc"]["timings_ns"]})},
        }
    identity = adapter.verify_source_identity()
    report = {"kind": "bounded_host_frontend_ir_check", "python": sys.version,
              "platform": platform.platform(), "seed": seed, "depth": depth,
              "source_commit": identity["revision"], "source_identity": identity,
              "summaries": summaries,
              "not_measured": ["native generated behavior", "native runtime", "Stage1", "semantic compiler equivalence"],
              "interpretation": "Python-valid means this fixed probe input raised no exception; PCC acceptance is lowering plus IR well-formedness, not runtime equivalence."}
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    arguments = parser.parse_args()
    manifest = (json.loads(arguments.source_manifest.read_text())
                if arguments.source_manifest else None)
    print(json.dumps(run(arguments.output, arguments.count, arguments.seed, arguments.depth,
                         arguments.source_root, manifest), indent=2))


if __name__ == "__main__":
    main()
