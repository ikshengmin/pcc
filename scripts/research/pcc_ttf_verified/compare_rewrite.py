"""Opt-in source-copy experiment. Nothing is patched into imported PCC."""

import ast
from collections import Counter
import difflib
import hashlib
import json
from pathlib import Path
import random
import statistics
import time

from .source_identity import observe_source


RELATIVE_SOURCE = "pcc/ir/optimization/integer_fold_contract.py"
BASE_SHA256 = "8591c99dbd7eb97ae73b6151ecc1bab7def1d995a64c0b983e2200ba52df302a"
TARGET = "fold_llvm_integer_compare"


def rewrite(source: str) -> str:
    if hashlib.sha256(source.encode()).hexdigest() != BASE_SHA256:
        raise ValueError("reference source does not match frozen 85b0 bytes")
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == TARGET)
    lines = source.splitlines(keepends=True)
    original = "".join(lines[function.lineno - 1:function.end_lineno])
    eager = "    lhs_s = signed_value(lhs_u, width)\n    rhs_s = signed_value(rhs_u, width)\n"
    signed_chain = '''    elif pred == "slt":
        result = lhs_s < rhs_s
    elif pred == "sle":
        result = lhs_s <= rhs_s
    elif pred == "sgt":
        result = lhs_s > rhs_s
    else:
        result = lhs_s >= rhs_s
'''
    lazy_chain = '''    else:
        lhs_s = signed_value(lhs_u, width)
        rhs_s = signed_value(rhs_u, width)
        if pred == "slt":
            result = lhs_s < rhs_s
        elif pred == "sle":
            result = lhs_s <= rhs_s
        elif pred == "sgt":
            result = lhs_s > rhs_s
        else:
            result = lhs_s >= rhs_s
'''
    if original.count(eager) != 1 or original.count(signed_chain) != 1:
        raise ValueError("expected rewrite anchors are not unique")
    replacement = original.replace(eager, "").replace(signed_chain, lazy_chain)
    result = "".join(lines[:function.lineno - 1]) + replacement + "".join(lines[function.end_lineno:])
    ast.parse(result)
    return result


def load_pair(source_root: Path):
    original = (source_root / RELATIVE_SOURCE).read_text()
    candidate = rewrite(original)
    namespaces = []
    for label, source in (("reference", original), ("candidate", candidate)):
        namespace = {"__name__": "pcc_ttf_verified_" + label}
        # Only the exactly hashed, local PCC module and the fixed narrow
        # transformation above can reach this execution step.
        exec(compile(source, label + "_integer_fold_contract.py", "exec"), namespace)
        namespaces.append(namespace)
    return original, candidate, namespaces


def differential_cases(predicates):
    for width in range(1, 5):
        for lhs in range(1 << width):
            for rhs in range(1 << width):
                for predicate in predicates:
                    yield predicate, width, lhs, rhs

    for width in (-2, 0, 1, 7, 8, 16, 32, 64, 128, 256):
        values = [-2**257, -129, -1, 0, 1, 127, 2**257]
        for lhs in values:
            for rhs in values:
                for predicate in (*predicates, "invalid"):
                    yield predicate, width, lhs, rhs


class EquivalenceMismatch(AssertionError):
    """The two functions returned different results on a corpus input."""


def check_equivalence(reference, candidate, cases):
    count = 0
    for case in cases:
        expected, actual = reference(*case), candidate(*case)
        if expected != actual:
            raise EquivalenceMismatch((case, expected, actual))
        count += 1
    if count == 0:
        raise ValueError("equivalence corpus cannot be empty")
    return count


def reject_wrong_candidate(candidate_source, reference, cases):
    anchor = "result = lhs_u == rhs_u"
    if candidate_source.count(anchor) != 1:
        raise ValueError("wrong-rewrite control needs exactly one equality branch")
    broken = candidate_source.replace(anchor, "result = lhs_u != rhs_u")
    namespace = {"__name__": "pcc_ttf_verified_deliberately_wrong"}
    exec(compile(broken, "wrong_integer_fold_contract.py", "exec"), namespace)
    try:
        check_equivalence(reference, namespace[TARGET], cases)
    except EquivalenceMismatch:
        return True
    raise AssertionError("same equivalence oracle accepted deliberately wrong candidate")


def timed(function, cases, repeats=7):
    samples, checksum = [], 0
    for _ in range(repeats):
        started = time.perf_counter_ns()
        local = 0
        for case in cases:
            local += function(*case)[1]
        samples.append(time.perf_counter_ns() - started)
        checksum += local
    return {"median_ns": statistics.median(samples), "samples_ns": samples, "checksum": checksum}


def experiment(source_root: Path, output: Path, source_manifest=None):
    output.mkdir(parents=True, exist_ok=True)
    identity = observe_source(source_root, source_manifest)
    original, candidate, (reference_ns, candidate_ns) = load_pair(source_root)
    reference, optimized = reference_ns[TARGET], candidate_ns[TARGET]
    predicates = reference_ns["LLVM_INTEGER_COMPARE_PREDS"]
    count = check_equivalence(reference, optimized, differential_cases(predicates))
    negative_control = reject_wrong_candidate(
        candidate, reference, differential_cases(predicates),
    )
    demands = {}
    for label, namespace in (("reference", reference_ns), ("candidate", candidate_ns)):
        original_signed = namespace["signed_value"]
        counts = Counter()
        current = ""
        def counted_signed(value, width):
            counts[current] += 1
            return original_signed(value, width)
        namespace["signed_value"] = counted_signed
        try:
            for predicate in predicates:
                current = predicate
                namespace[TARGET](predicate, 64, -3, 5)
        finally:
            namespace["signed_value"] = original_signed
        demands[label] = {predicate: counts[predicate] for predicate in predicates}
    rng = random.Random(30188)
    timing = {}
    for family, choices in (("unsigned_and_equality", predicates[:6]),
                            ("signed", predicates[6:]), ("mixed", predicates)):
        cases = [(rng.choice(choices), rng.choice((8, 32, 64, 128)),
                  rng.randrange(-2**64, 2**64), rng.randrange(-2**64, 2**64))
                 for _ in range(2000)]
        # Alternate order per sample rather than timing all baseline samples
        # before all candidate samples. Raw paired samples remain in the report.
        samples = {"reference": [], "candidate": []}
        checksums = {"reference": [], "candidate": []}
        for repeat in range(7):
            order = (("reference", reference), ("candidate", optimized))
            if repeat % 2:
                order = tuple(reversed(order))
            for label, function in order:
                value = timed(function, cases, repeats=1)
                samples[label].append(value["median_ns"])
                checksums[label].append(value["checksum"])
        if checksums["reference"] != checksums["candidate"]:
            raise AssertionError("benchmark checksums differ")
        ref = statistics.median(samples["reference"])
        opt = statistics.median(samples["candidate"])
        timing[family] = {"calls_per_sample": len(cases), "samples_ns": samples,
                          "median_reference_ns": ref, "median_candidate_ns": opt,
                          "speedup": ref / opt, "checksums_equal": True}
    patch = "".join(difflib.unified_diff(original.splitlines(True), candidate.splitlines(True),
                                      fromfile="a/" + RELATIVE_SOURCE, tofile="b/" + RELATIVE_SOURCE))
    # This .txt is a review artifact, never applied automatically.
    (output / "candidate-source-diff.txt").write_text(patch)
    if observe_source(source_root, source_manifest) != identity:
        raise ValueError("PCC source changed during the experiment")
    report = {"source_sha256": BASE_SHA256,
              "candidate_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
              "source_commit": identity["revision"], "source_identity": identity,
              "reference_origin_commit": "85b0e08cd28e143d87607a5cd76e8f7c350cabd7",
              "target": TARGET, "differential_cases": count, "mismatches": 0,
              "negative_control_detected": negative_control, "signed_call_counts": demands,
              "host_function_timing": timing,
              "scope": "built-in integer inputs; host CPython function copies; no integration",
              "not_proven": ["Python-to-Lean translation", "all Python objects or resource exceptions", "PCC compiled behavior"],
              "not_measured": ["native", "Stage1", "whole-compiler speedup"]}
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.source_manifest.read_text()) if args.source_manifest else None
    print(json.dumps(experiment(args.source_root, args.output, manifest), indent=2))


if __name__ == "__main__":
    main()
