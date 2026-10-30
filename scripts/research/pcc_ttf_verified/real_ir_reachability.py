"""Check the optional text simplifier on one real lowered Python seed."""

import argparse
import hashlib
import json
from pathlib import Path
import time

from .compare_rewrite import TARGET, load_pair
from .pcc_adapter import PCCAdapter
from .templates import TEMPLATES


def verify_pass_output(adapter, text):
    # Verification failure is an experiment failure, not a successful report.
    adapter.verify(adapter.parse_ir(text))


def inspect(source_root: Path, output: Path, source_manifest=None):
    output.mkdir(parents=True, exist_ok=True)
    _, _, namespaces = load_pair(source_root)
    adapter = PCCAdapter(source_root, source_manifest)
    source = TEMPLATES[0].seed
    checked, ir_text = adapter.lower(source)
    if not checked["accepted"]:
        raise AssertionError(checked)
    pass_path = source_root / "pcc/ir/optimization/instsimplify.py"
    pass_source = pass_path.read_text()
    outputs, measurements = {}, {}
    for label, namespace in zip(("reference", "candidate"), namespaces):
        isolated = {"__name__": "pcc.ir.optimization.research_" + label,
                    "__package__": "pcc.ir.optimization"}
        # This is the existing, frozen PCC pass in an isolated globals dict,
        # not fake IR, and no production module globals are monkeypatched.
        exec(compile(pass_source, str(pass_path), "exec"), isolated)
        calls = []
        def observed(*args):
            calls.append(args)
            return namespace[TARGET](*args)
        isolated[TARGET] = observed
        started = time.perf_counter_ns()
        result, changed = isolated["simplify_module_text"](ir_text)
        elapsed = time.perf_counter_ns() - started
        verify_pass_output(adapter, result)
        outputs[label] = result
        measurements[label] = {"fold_compare_calls": len(calls), "calls": calls,
                               "single_pass_ns": elapsed, "changed": changed,
                               "output_ir_verified": True, "verification_error": None}
    if outputs["reference"] != outputs["candidate"]:
        raise AssertionError("real-IR pass output differs")
    (output / "input.py").write_text(source)
    identity = adapter.verify_source_identity()
    report = {"source_identity": identity,
              "input_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
              "input_ir_sha256": checked["ir_sha256"],
              "pass_source_sha256": hashlib.sha256(pass_source.encode()).hexdigest(),
              "output_ir_sha256": hashlib.sha256(outputs["reference"].encode()).hexdigest(),
              "output_byte_equal": True, "measurements": measurements,
              "measurement_warning": "One pass sample is a reachability check, not a speedup estimate.",
              "pipeline_boundary": "Explicit optional text instsimplify; the separately observed normal Stage1 uses python_ir_passes=off. No Stage1 benefit is established."}
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.source_manifest.read_text()) if args.source_manifest else None
    print(json.dumps(inspect(args.source_root, args.output, manifest), indent=2))


if __name__ == "__main__":
    main()
