import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.research.pcc_ttf_verified import run_fuzz
from scripts.research.pcc_ttf_verified import real_ir_reachability
from scripts.research.pcc_ttf_verified.compare_rewrite import (
    TARGET,
    check_equivalence,
    differential_cases,
    load_pair,
    reject_wrong_candidate,
)
from scripts.research.pcc_ttf_verified.real_ir_reachability import verify_pass_output
from scripts.research.pcc_ttf_verified.source_identity import observe_source



@pytest.mark.parametrize("failure_stage", ["parse", "verify"])
def test_pass_output_fails_closed(failure_stage):
    def parse(text):
        if failure_stage == "parse":
            raise ValueError("rejected parse")
        return text

    def verify(parsed):
        raise ValueError("rejected verify")

    adapter = SimpleNamespace(parse_ir=parse, verify=verify)
    with pytest.raises(ValueError, match="rejected"):
        verify_pass_output(adapter, "deliberately malformed")


def test_reachability_inspection_does_not_report_success_after_verify_failure(tmp_path, monkeypatch):
    pass_path = tmp_path / "pcc/ir/optimization/instsimplify.py"
    pass_path.parent.mkdir(parents=True)
    pass_path.write_text("def simplify_module_text(text):\n    return 'bad IR', True\n")
    target = {TARGET: lambda *args: ("constant", 1)}
    monkeypatch.setattr(real_ir_reachability, "load_pair", lambda root: ("", "", [target, target]))
    def reject(text):
        raise ValueError("invalid emitted IR")
    adapter = SimpleNamespace(lower=lambda source: ({"accepted": True, "ir_sha256": "test"}, "input IR"),
                              parse_ir=lambda text: text, verify=reject)
    monkeypatch.setattr(real_ir_reachability, "PCCAdapter", lambda *args: adapter)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="invalid emitted IR"):
        real_ir_reachability.inspect(tmp_path, output)
    assert not (output / "summary.json").exists()


def test_same_oracle_rejects_actual_bad_candidate_source():
    source = '''def fold_llvm_integer_compare(pred, width, lhs_u, rhs_u):
    result = lhs_u == rhs_u
    return ("constant", 1 if result else 0)
'''
    namespace = {}
    exec(source, namespace)
    cases = [("eq", 8, 1, 1), ("eq", 8, 1, 0)]
    assert check_equivalence(namespace[TARGET], namespace[TARGET], cases) == 2
    assert reject_wrong_candidate(source, namespace[TARGET], cases) is True


def test_actual_pinned_rewrite_negative_control():
    import os
    root = os.environ.get("PCC_RESEARCH_SOURCE")
    if not root:
        pytest.skip("set PCC_RESEARCH_SOURCE to the frozen integer-fold source")
    _, candidate_source, (reference, candidate) = load_pair(Path(root))
    cases = [("eq", 8, 1, 1), ("ne", 8, 1, 0), ("slt", 8, -1, 1)]
    assert check_equivalence(reference[TARGET], candidate[TARGET], cases) == 3
    assert reject_wrong_candidate(candidate_source, reference[TARGET], cases) is True


def test_negative_control_does_not_accept_unrelated_assertion():
    source = '''def fold_llvm_integer_compare(pred, width, lhs_u, rhs_u):
    assert False, "unrelated failure"
    result = lhs_u == rhs_u
    return ("constant", 1 if result else 0)
'''
    with pytest.raises(AssertionError, match="unrelated failure"):
        reject_wrong_candidate(source, lambda *args: ("constant", 1), [("eq", 8, 1, 1)])


def test_equivalence_oracle_cannot_pass_without_cases():
    with pytest.raises(ValueError, match="empty"):
        check_equivalence(lambda: 1, lambda: 2, [])


def test_original_differential_domain_is_preserved():
    predicates = ("eq", "ne", "ult", "ule", "ugt", "uge", "slt", "sle", "sgt", "sge")
    cases = list(differential_cases(predicates))
    assert len(cases) == 8790
    assert ("invalid", -2, -(2**257), 2**257) in cases
