"""Owned executable reference arm for #9; production dispatch is unchanged."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_native_acyclic_candidate_classifier_preserves_cycle_descendants(tmp_path, explicit_owned_runtime):
    repo = Path(__file__).resolve().parents[2]
    source = Path(__file__).with_name("fixtures") / "gc_acyclic_candidate_peel.c"
    binary = tmp_path / "acyclic_peel"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(repo / "pcc/runtime/src")),
    )
    for backend in range(5):
        result = _record_execution(
            tmp_path, f"gc{backend}", [str(binary)], dict(os.environ, PCC_GC_BACKEND=str(backend)),
        )
        assert (result["returncode"], result["stdout"], result["stderr"]) == (
            0, "GC_ACYCLIC_CANDIDATE_PEEL_OK\n", "",
        ), (backend, result)
