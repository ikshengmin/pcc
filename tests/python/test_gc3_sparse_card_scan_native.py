"""Verify real GC3 stopped dirty-owner traversal, not nursery reclamation."""
import os
from pathlib import Path
import re

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc3_stopped_sparse_card_scan_visits_no_clean_old(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc3_sparse_card_scan_native.c"
    binary = tmp_path / "gc3_sparse_card_scan"
    repo = Path(__file__).resolve().parents[2]
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(repo / "pcc/runtime/src")),
    )
    for count in (64, 256, 1024):
        result = _record_execution(tmp_path, f"sparse-{count}", [str(binary), str(count)],
                                   dict(os.environ, PCC_GC_BACKEND="3"))
        assert (result["returncode"], result["stderr"]) == (0, ""), result
        assert re.fullmatch(
            rf"GC3_SPARSE_CARDS_OK clean={count} dirty=2 strong=2 repeats=128 us=\d+\n",
            result["stdout"],
        ), result
