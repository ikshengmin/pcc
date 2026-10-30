#!/usr/bin/env python3
"""Compare existing normal Stage1 module counters; never compile or run PCC.

Inputs are raw worker-timing logs, not arbitrary text profiles. Revisions are
caller-supplied provenance labels, not verified by this offline analysis.
"""

import argparse
import json
from pathlib import Path


PREFIXES = {
    "worker": "pcc frontend worker done ",
    "kernel": "pcc direct indexed kernel cfg ",
    "capture": "pcc direct indexed capture ",
    "render": "pcc llvm text render ",
    "phase": "pcc indexed phase totals ",
    "structured": "pcc structured instructions ",
}
REQUIRED = {
    "worker": ("index", "infer_ms", "codegen_ms"),
    "kernel": ("blocks", "instructions", "functions", "call_cont_blocks",
               "call_err_cleanup_blocks", "err_frame_blocks", "inline_error_edges"),
    "capture": ("elapsed_ms", "supported_records", "fallback_records"),
    "render": ("elapsed_ms",),
    "phase": ("target", "route", "clock_ok", "codegen_complete",
              "frontend_generate_ns", "backend_call_inclusive_ns",
              "prepare_verify_ns", "stackmap_plan_ns", "function_emit_inclusive_ns",
              "function_setup_ns", "assembly_parse_ns"),
    "structured": ("unscaled", "move", "call", "fallback"),
}
MEASURES = {
    "ir_instructions": ("kernel", "instructions"),
    "blocks": ("kernel", "blocks"),
    "functions": ("kernel", "functions"),
    "call_cont_blocks": ("kernel", "call_cont_blocks"),
    "call_err_cleanup_blocks": ("kernel", "call_err_cleanup_blocks"),
    "err_frame_blocks": ("kernel", "err_frame_blocks"),
    "inline_error_edges": ("kernel", "inline_error_edges"),
    "machine_call_records": ("structured", "call"),
    "codegen_ms": ("worker", "codegen_ms"),
    "frontend_generate_ns": ("phase", "frontend_generate_ns"),
    "prepare_verify_ns": ("phase", "prepare_verify_ns"),
    "stackmap_plan_ns": ("phase", "stackmap_plan_ns"),
    "function_emit_inclusive_ns": ("phase", "function_emit_inclusive_ns"),
    "function_setup_ns": ("phase", "function_setup_ns"),
    "assembly_parse_ns": ("phase", "assembly_parse_ns"),
    "text_render_ms": ("render", "elapsed_ms"),
    "capture_fallback_records": ("capture", "fallback_records"),
}


def parse_log(lines):
    sections = {kind: {} for kind in PREFIXES}
    for number, line in enumerate(lines, 1):
        for kind, prefix in PREFIXES.items():
            if not line.startswith(prefix):
                continue
            fields = {}
            for token in line[len(prefix):].split():
                key, separator, value = token.partition("=")
                if not separator or not value or key in fields:
                    raise ValueError(f"line {number}: malformed or duplicate field")
                if key not in ("module", "target", "route"):
                    if not value.isascii() or not value.isdecimal():
                        raise ValueError(f"line {number}: {key} must be a nonnegative integer")
                    value = int(value)
                fields[key] = value
            module = fields.pop("module", None)
            if not module or any(key not in fields for key in REQUIRED[kind]):
                raise ValueError(f"line {number}: incomplete {kind} record")
            if module in sections[kind]:
                raise ValueError(f"line {number}: duplicate {kind} for {module}; split attempts first")
            sections[kind][module] = fields
            break
    modules = set(sections["worker"])
    if not modules:
        raise ValueError("no completed module records")
    for kind, records in sections.items():
        if set(records) != modules:
            raise ValueError(f"incomplete or mixed log: {kind} module set differs from completed workers")
    for module, phase in sections["phase"].items():
        if phase["clock_ok"] != 1 or phase["codegen_complete"] != 1:
            raise ValueError(f"{module}: failed clock or incomplete codegen")
    return {module: {kind: records[module] for kind, records in sections.items()}
            for module in sorted(modules)}


def _measures(module):
    return {name: module[kind][field] for name, (kind, field) in MEASURES.items()}


def _delta(before, after):
    return {name: {"before": before[name], "after": after[name],
                   "change_percent": (100 * (after[name] / before[name] - 1)
                                      if before[name] else None)}
            for name in MEASURES}


def compare(before, after, modules=None):
    common = set(before) & set(after)
    selected = set(modules) if modules is not None else common
    if not selected or not selected <= common:
        raise ValueError("requested modules must be present in both completed logs")
    rows = []
    before_total = dict.fromkeys(MEASURES, 0)
    after_total = dict.fromkeys(MEASURES, 0)
    for name in sorted(selected):
        old, new = before[name], after[name]
        for field in ("target", "route"):
            if old["phase"][field] != new["phase"][field]:
                raise ValueError(f"{name}: {field} changed; do not mix pipeline/platform timings")
        old_counts, new_counts = _measures(old), _measures(new)
        for key in MEASURES:
            before_total[key] += old_counts[key]
            after_total[key] += new_counts[key]
        rows.append({"module": name, "target": new["phase"]["target"],
                     "route": new["phase"]["route"], "metrics": _delta(old_counts, new_counts)})
    rows.sort(key=lambda row: (-row["metrics"]["codegen_ms"]["after"], row["module"]))
    return {
        "scope": "paired completed modules; cumulative worker durations, not CPU or wall time",
        "limits": ["cross-revision observation, not isolated causal A/B",
                   "machine call records are static, not dynamic runtime calls",
                   "function_setup_ns is included in function_emit_inclusive_ns; do not add them",
                   "IR/cleanup equivalence cannot be proved from these counters"],
        "paired_modules": len(selected),
        "before_only_modules": sorted(set(before) - set(after)),
        "after_only_modules": sorted(set(after) - set(before)),
        "totals": _delta(before_total, after_total), "modules": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--before-revision", required=True)
    parser.add_argument("--after-revision", required=True)
    parser.add_argument("--module", action="append", dest="modules")
    args = parser.parse_args()
    with args.before.open() as stream:
        before = parse_log(stream)
    with args.after.open() as stream:
        after = parse_log(stream)
    result = compare(before, after, args.modules)
    result["caller_supplied_revision_labels"] = {
        "before": args.before_revision, "after": args.after_revision,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
