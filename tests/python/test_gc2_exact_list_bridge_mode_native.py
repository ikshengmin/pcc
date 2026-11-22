"""Attest the review selector from source-matched startup environments."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc2_exact_list_bridge_selector_is_strict_and_backend_scoped(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc2_exact_list_bridge_mode_native.c"
    binary = tmp_path / "gc2_exact_list_bridge_mode"
    compile_owned_c_with_runtime(source, binary, runtime_archive=explicit_owned_runtime)
    cases = [(2, value) for value in (None, "", "0", "01", "1x", "2", "true", "1")]
    cases += [(backend, "1") for backend in (0, 1, 3, 4)]
    for i, (backend, value) in enumerate(cases):
        env = dict(os.environ, PCC_GC_BACKEND=str(backend))
        env.pop("PCC_GC2_EXACT_LIST_BRIDGE", None)
        if value is not None:
            env["PCC_GC2_EXACT_LIST_BRIDGE"] = value
        result = _record_execution(tmp_path, f"mode-{i}", [str(binary)], env)
        assert (result["returncode"], result["stderr"]) == (0, ""), result
        expected = 2 if backend == 2 and value == "1" else 0
        assert result["stdout"] == f"GC2_EXACT_LIST_MODE backend={backend} mode={expected}\n", result
