"""Isolate two exact list scans with the current GC2 container protocol."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc2_disjoint_exact_lists_scan_while_graph_held(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc2_disjoint_list_slot_native.c"
    binary = tmp_path / "gc2_disjoint_list_slot"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(Path(__file__).resolve().parents[2] / "pcc/runtime/src")),
    )
    env = dict(os.environ, PCC_GC_BACKEND="2")
    for repeat in range(3):
        result = _record_execution(tmp_path, f"disjoint-list-{repeat}", [str(binary)], env)
        assert (result["returncode"], result["stderr"]) == (0, ""), result
        assert result["stdout"] == (
            "GC2_DISJOINT_LIST_SLOT_OK overlap=2 exact_strong_slots=2\n"
        ), result
