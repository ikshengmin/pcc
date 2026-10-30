import pytest

from scripts.research.pcc_ttf_verified.semantic_cross_type import CASES, semantic_case
from scripts.research.pcc_ttf_verified.safety import run_python, validate


@pytest.mark.parametrize("index", range(4))
def test_directed_cross_type_cases_keep_existing_safety_boundary(index):
    case, source = semantic_case(index)
    assert case.lhs.typ != case.rhs.typ
    validate(source)
    assert run_python(source) is case.expected


@pytest.mark.parametrize("index", [-1, 4, True, "0"])
def test_directed_corpus_bound_is_fixed(index):
    with pytest.raises(ValueError, match="four"):
        semantic_case(index)


def test_different_type_tags_do_not_mean_unequal():
    assert CASES[0].expected is False
    assert CASES[3].expected is True
    assert "() == 0" in semantic_case(0)[1]
