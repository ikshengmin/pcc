"""Six fixed, safe reductions of the observed tuple/int lowering diagnostic."""

import argparse
import json
from pathlib import Path

from .pcc_adapter import PCCAdapter
from .safety import run_python, validate
from .templates import mutant


def minimize(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    adapter = PCCAdapter()
    _, _, original = mutant(42, 15, "scope_only", 2)
    candidates = [original]
    candidates.append(original.replace("    xs = [a, b]\n", "")
                      .replace("    text = 'abc'\n", "")
                      .replace("    flag = a < b\n", ""))
    candidates.append(candidates[-1].replace("((pair == -2) and ('' and '')) + a", "pair == -2"))
    candidates.append(candidates[-1].replace("    pair = (a, b)\n", "").replace("pair == -2", "(a, b) == -2"))
    candidates.append(candidates[-1].replace("(a, b) == -2", "(0,) == 0"))
    candidates.append(candidates[-1].replace("(0,) == 0", "() == 0"))
    rows = []
    smallest = None
    for source in candidates:
        validate(source)
        result = run_python(source)
        checked, _ = adapter.lower(source)
        same = (not checked["accepted"] and checked["error_type"] == "NotImplementedError"
                and checked["error"] == "Layer 1 cannot coerce TupleType to int")
        rows.append({"source": source, "python_result": repr(result), "pcc": checked,
                     "preserves_observed_failure": same})
        if same and (smallest is None or len(source) < len(smallest)):
            smallest = source
    if smallest is None:
        raise AssertionError("observed failure did not reproduce")
    (output / "tuple_int_minimized.py").write_text(smallest)
    report = {"checks": len(rows), "rows": rows,
              "classification": "explicit PCC lowering capability gap, not native miscompilation",
              "minimality": "smallest preserving sample among these six fixed reductions; no global-minimality claim"}
    (output / "minimization.json").write_text(json.dumps(report, indent=2) + "\n")
    return {"checks": len(rows), "preserved": sum(row["preserves_observed_failure"] for row in rows),
            "smallest_source": smallest, "classification": report["classification"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(minimize(args.output), indent=2))


if __name__ == "__main__":
    main()
