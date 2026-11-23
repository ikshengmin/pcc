"""Native proof that young liveness, not a claimed count, permits slab reuse."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc3_bump_span_rewinds_only_after_an_empty_actual_mark(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc3_bump_closed_cut_native.c"
    binary = tmp_path / "gc3_bump_cut"
    repo = Path(__file__).resolve().parents[2]
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(repo / "pcc/runtime/src")),
    )
    result = _record_execution(tmp_path, "bump-cut", [str(binary)],
                               dict(os.environ, PCC_GC_BACKEND="3"))
    assert (result["returncode"], result["stdout"], result["stderr"]) == (
        0, "GC3_BUMP_CUT_OK survivors=2 deferred=1 rewound=3 reused=1 dirty=2 cleared=1\n", "",
    ), result
