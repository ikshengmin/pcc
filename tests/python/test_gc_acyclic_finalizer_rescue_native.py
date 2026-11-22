"""Native, test-invoked #9 correctness arm: one finalizer and one refresh."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_native_acyclic_finalizer_recheck_protects_rescued_child(tmp_path, explicit_owned_runtime):
    repo = Path(__file__).resolve().parents[2]
    source = Path(__file__).with_name("fixtures") / "gc_acyclic_finalizer_rescue.c"
    binary = tmp_path / "acyclic_rescue"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(repo / "pcc/runtime/src")),
    )
    for backend in (1, 2, 3):
        for arm in ("rescue", "no-rescue"):
            result = _record_execution(
                tmp_path, f"gc{backend}-{arm}", [str(binary), arm],
                dict(os.environ, PCC_GC_BACKEND=str(backend)),
            )
            assert (result["returncode"], result["stdout"], result["stderr"]) == (
                0, "GC_ACYCLIC_FINALIZER_RESCUE_OK\n", "",
            ), (backend, arm, result)
