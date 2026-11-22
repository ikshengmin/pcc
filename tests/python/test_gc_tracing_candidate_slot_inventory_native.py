"""Linux pcc0 witness for the stopped candidate-list and slot visitor contracts."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_native_stopped_candidates_and_owning_slot_roles(tmp_path, explicit_owned_runtime):
    repo = Path(__file__).resolve().parents[2]
    source = Path(__file__).with_name("fixtures") / "gc_tracing_candidate_slot_inventory.c"
    binary = tmp_path / "candidate_slot_inventory"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(repo / "pcc/runtime/src")),
    )
    for backend in (1, 2, 3, 4):
        result = _record_execution(
            tmp_path, f"gc{backend}", [str(binary)], dict(os.environ, PCC_GC_BACKEND=str(backend)),
        )
        assert (result["returncode"], result["stdout"], result["stderr"]) == (
            0, "GC_TRACING_CANDIDATE_SLOT_INVENTORY_OK\n", "",
        ), (backend, result)
