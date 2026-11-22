"""The alternate and ordinary GC2 scans must not both pay one gray debt."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc2_contested_owner_retires_once(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc2_bridge_contested_credit_native.c"
    binary = tmp_path / "gc2_bridge_contested_credit"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(Path(__file__).resolve().parents[2] / "pcc/runtime/src")),
    )
    env = dict(os.environ, PCC_GC_BACKEND="2")
    for repeat in range(3):
        result = _record_execution(tmp_path, f"contested-{repeat}", [str(binary)], env)
        assert (result["returncode"], result["stderr"]) == (0, ""), result
        assert result["stdout"] == "GC2_CONTESTED_BRIDGE_OK ordinary=1 alternate=0 gray=1\n", result
