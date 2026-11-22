"""Native exact-owner traversal must not depend on the child's GC index."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc3_promoted_owner_walks_indexless_child_as_strong_slot(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc3_promoted_owner_slots_native.c"
    binary = tmp_path / "promoted_owner_slots"
    repo = Path(__file__).resolve().parents[2]
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(repo / "pcc/runtime/src")),
    )
    result = _record_execution(
        tmp_path, "promoted-owner", [str(binary)], dict(os.environ, PCC_GC_BACKEND="3"),
    )
    assert (result["returncode"], result["stdout"], result["stderr"]) == (
        0, "GC3_PROMOTED_STRONG_SLOT_OK\n", "",
    ), result
