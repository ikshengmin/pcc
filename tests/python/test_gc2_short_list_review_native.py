"""Exercise the dormant GC2 exact-list kernel and its admission controls."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc2_review_list_entry_addresses_real_owned_slot_boundary(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc2_short_list_review_native.c"
    binary = tmp_path / "gc2_short_list_review"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(Path(__file__).resolve().parents[2] / "pcc/runtime/src")),
    )
    env = dict(os.environ, PCC_GC_BACKEND="2")
    for repeat in range(3):
        result = _record_execution(tmp_path, f"shade-list-{repeat}", [str(binary)], env)
        assert (result["returncode"], result["stderr"]) == (0, ""), result
        assert result["stdout"] == (
            "GC2_LIST_SHADE_OK scanned=1 late_barrier=1 queue=2 rejections=5\n"
        ), result
    for backend in (0, 1, 3, 4):
        result = _record_execution(
            tmp_path, f"backend-{backend}", [str(binary)],
            dict(env, PCC_GC_BACKEND=str(backend)),
        )
        assert (result["returncode"], result["stderr"]) == (0, ""), result
        assert result["stdout"] == f"GC2_LIST_SHADE_UNSUPPORTED backend={backend}\n", result
