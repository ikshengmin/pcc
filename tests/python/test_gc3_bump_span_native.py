"""True bump allocation and whole-slab reuse without a production caller."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc3_bump_span_lifecycle_is_inert_outside_its_own_review_boundary(
    tmp_path, explicit_owned_runtime,
):
    source = Path(__file__).with_name("fixtures") / "gc3_bump_span_native.c"
    binary = tmp_path / "gc3_bump_span"
    repo = Path(__file__).resolve().parents[2]
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(repo / "pcc/runtime/src")),
    )
    for backend in range(5):
        result = _record_execution(tmp_path, f"bump-gc{backend}", [str(binary)],
                                   dict(os.environ, PCC_GC_BACKEND=str(backend)))
        wanted = ("GC3_BUMP_SPAN_OK committed=2048 new_keys=16 registered_cells=0 reused=1"
                  if backend == 3 else "GC3_BUMP_OTHER_BACKEND_INERT_OK")
        assert (result["returncode"], result["stdout"], result["stderr"]) == (
            0, wanted + "\n", "",
        ), result
