from dataclasses import replace
import random
from pathlib import Path

import pytest

from scripts.research.pcc_ttf_verified.templates import MAX_DEPTH, TEMPLATES, Term, generate_term, mutant
from scripts.research.pcc_ttf_verified.safety import run_python, validate
from scripts.research.pcc_ttf_verified.run_fuzz import run


def test_templates_are_grounded():
    for template in TEMPLATES:
        template.check_grounded()
    with pytest.raises(ValueError, match="reproduce"):
        replace(TEMPLATES[0], seed="different text").check_grounded()


@pytest.mark.parametrize("mode", ["typed", "scope_only", "naive"])
def test_complete_initial_corpus_has_bounded_syntax(mode):
    for index in range(20):
        template, term, source = mutant(42, index, mode, 2)
        validate(source)
        if mode == "typed":
            assert term.typ == template.default.typ
            assert term.free <= template.live
        if mode == "scope_only":
            assert term.free <= template.live


def test_generator_depth_and_campaign_count_bounds(tmp_path):
    with pytest.raises(ValueError, match="depth"):
        generate_term(random.Random(0), "int", frozenset(), MAX_DEPTH + 1, "typed")
    with pytest.raises(ValueError, match="20 cases"):
        run(tmp_path, count=21)


@pytest.mark.parametrize("term", [
    "'a' * (16 * 16 * 16)",
    "16 * 16 * 16 * 16",
    "len('a' * (16 * 16 * 16))",
])
def test_expression_growth_rejected_without_execution(term):
    source = TEMPLATES[0].instantiate(Term(term, "int"))
    with pytest.raises(ValueError, match="growth bound"):
        validate(source)


@pytest.mark.parametrize("term", [
    "__import__('os')", "a.__class__", "probe(2, 5)",
    "[a for a in xs]", "(lambda: 1)()", "2 ** 8",
])
def test_non_subset_features_rejected_without_execution(term):
    source = TEMPLATES[0].instantiate(Term(term, "int"))
    with pytest.raises(ValueError):
        validate(source)


def test_argument_annotation_cannot_execute():
    source = TEMPLATES[0].seed.replace("a: int", "a: len('a')")
    with pytest.raises(ValueError, match="annotations"):
        validate(source)


def test_minimized_cross_type_case_is_ordinary_valid_python():
    path = Path(__file__).parents[2] / "scripts/research/pcc_ttf_verified/cases/tuple_int_comparison.py"
    assert run_python(path.read_text()) is False
