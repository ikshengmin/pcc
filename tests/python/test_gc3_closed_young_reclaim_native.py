"""A stopped explicit GC3 cohort, not production allocation or root discovery."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc3_closed_young_graph_reclaims_only_after_a_complete_frontier(
    tmp_path, explicit_owned_runtime,
):
    source = Path(__file__).with_name("fixtures") / "gc3_closed_young_reclaim_native.c"
    binary = tmp_path / "gc3_closed_young"
    repo = Path(__file__).resolve().parents[2]
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(repo / "pcc/runtime/src")),
    )
    result = _record_execution(tmp_path, "closed-young", [str(binary)],
                               dict(os.environ, PCC_GC_BACKEND="3"))
    assert (result["returncode"], result["stdout"], result["stderr"]) == (
        0, "GC3_CLOSED_YOUNG_OK traced=4 reclaimed=1 vetoed=4\n", "",
    ), result
