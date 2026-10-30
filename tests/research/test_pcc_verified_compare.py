import os
from pathlib import Path

import pytest

from scripts.research.pcc_ttf_verified.compare_rewrite import TARGET, load_pair, rewrite


def test_rewrite_refuses_unfrozen_source():
    with pytest.raises(ValueError, match="frozen 85b0"):
        rewrite("def fold_llvm_integer_compare(): return 0\n")


def test_pinned_source_copy_and_boundary_results():
    root = os.environ.get("PCC_RESEARCH_SOURCE")
    if not root:
        pytest.skip("set PCC_RESEARCH_SOURCE to the frozen 85b0 source snapshot")
    before, after, (reference, candidate) = load_pair(Path(root))
    assert before != after
    for case in (("eq", 8, 1, 1), ("ne", 64, -1, 0),
                 ("ult", 8, -1, 1), ("slt", 8, -1, 1),
                 ("sge", 128, 2**127, -1), ("eq", 0, 1, 1),
                 ("invalid", 64, 1, 1)):
        assert reference[TARGET](*case) == candidate[TARGET](*case)
    assert candidate[TARGET]("eq", 8, 1, 1) == ("constant", 1)
