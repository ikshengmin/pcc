"""Exercise initial stopped machine roots and a separate registered root."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc3_initial_root_snapshot_does_not_refresh_over_collector_scratch(
    tmp_path, explicit_owned_runtime,
):
    source = Path(__file__).with_name("fixtures") / "gc3_initial_snapshot_roots_native.c"
    binary = tmp_path / "gc3_root_cut"
    repo = Path(__file__).resolve().parents[2]
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(repo / "pcc/runtime/src")),
    )
    for args, caller, registered in (((), 1, 1), (("caller",), 0, 1),
                                     (("registered",), 1, 0)):
        suffix = "-" + args[0] if args else ""
        result = _record_execution(tmp_path, "initial-root-cut" + suffix,
                                   [str(binary), *args], dict(os.environ, PCC_GC_BACKEND="3"))
        assert (result["returncode"], result["stdout"], result["stderr"]) == (
            0, f"GC3_REAL_ROOT_CUT_OK caller={caller} registered={registered} post_stop_scratch=0\n",
            "",
        ), result
