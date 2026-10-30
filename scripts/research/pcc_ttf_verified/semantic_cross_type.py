"""Four directed cross-type witnesses; not a fuzzing yield or native oracle.

Keep the original three mutation arms and the execution safety subset unchanged.
Different type tags alone do not decide Python equality: bool/int is a positive
control. The tuple/int witness is known in advance, so rediscovery is not new-bug
evidence. Only existing host lowering and owned-IR verification are invoked.
"""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import platform
import sys

from .pcc_adapter import PCCAdapter
from .safety import run_python, validate
from .templates import Template, Term
from .source_identity import git_blob, observe_source as check_source


@dataclass(frozen=True)
class EqualityCase:
    name: str
    lhs: Term
    rhs: Term
    expected: bool


CASES = (
    EqualityCase("tuple_int", Term("()", "tuple[]"), Term("0", "int"), False),
    EqualityCase("int_tuple", Term("0", "int"), Term("()", "tuple[]"), False),
    EqualityCase("list_tuple", Term("[]", "list[int]"), Term("()", "tuple[]"), False),
    EqualityCase("bool_int", Term("True", "bool"), Term("1", "int"), True),
)


def semantic_case(index):
    if type(index) is not int or not 0 <= index < len(CASES):
        raise ValueError("exactly four directed cases are available")
    case = CASES[index]
    assert case.lhs.typ != case.rhs.typ
    term = Term("(" + case.lhs.text + " == " + case.rhs.text + ")", "bool")
    text = "def probe(a: int, b: int):\n    return @@TERM@@\nresult = probe(2, 5)\n"
    template = Template(case.name, text, text.replace("@@TERM@@", term.text),
                        term, frozenset({("a", "int"), ("b", "int")}))
    template.check_grounded()
    return case, template.instantiate(term)


def run(source_root, manifest, output):
    source_root, output = Path(source_root).resolve(), Path(output)
    before = check_source(source_root, manifest)
    sys.path.insert(0, str(source_root))
    corpus = [semantic_case(index) for index in range(len(CASES))]
    # Validate and execute only the fixed bounded programs, before PCC imports.
    for case, source in corpus:
        validate(source)
        if run_python(source) is not case.expected:
            raise AssertionError("Python semantic control failed: " + case.name)
    adapter = PCCAdapter(source_root, manifest)
    check_source(source_root, manifest)
    rows = []
    for case, source in corpus:
        result, ir_text = adapter.lower(source)
        rows.append({"case": case.name, "lhs_type": case.lhs.typ,
                     "rhs_type": case.rhs.typ, "source": source,
                     "python_result": case.expected, "pcc": result,
                     "classification": ("host_lowering_and_ir_acceptance" if result["accepted"]
                                        else "host_lowering_or_ir_gap_not_native_miscompile")})
        if result["accepted"] and ir_text is None:
            raise AssertionError("accepted case has no owned IR")
    after = check_source(source_root, manifest)
    if before != after:
        raise AssertionError("source changed during the experiment")
    report = {"kind": "directed_cross_type_host_frontend_check", "source_identity": after,
              "python": sys.version, "platform": platform.platform(), "cases": rows,
              "not_measured": ["bug discovery rate", "fuzzing yield", "native behavior",
                               "collector behavior", "Stage1", "performance"],
              "interpretation": "Four preselected semantic witnesses, including a known gap; not a new fuzzing campaign or native differential test."}
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.source_manifest.read_text())
    print(json.dumps(run(args.source_root, manifest, args.output), indent=2))


if __name__ == "__main__":
    main()
