"""Run the opt-in GC2 review policy with real mark work and controlled pressure."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc2_candidate_opt_in_override_and_real_assist(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc2_pacer_pressure_native.c"
    binary = tmp_path / "gc2_pacer_pressure"
    compile_owned_c_with_runtime(source, binary, runtime_archive=explicit_owned_runtime)
    env = dict(os.environ, PCC_GC_BACKEND="2", PCC_GC2_PACER_PERCENT="100",
               PCC_GC_DEBT_THRESHOLD="0")
    for label, arg, changes, expected in (
        ("active", "on", {}, "GC2_PACER_PRESSURE_OK"),
        ("default", "off", {}, "GC2_PACER_DISABLED_OK"),
        ("override", "off", {"PCC_GC_DEBT_THRESHOLD": "131072"}, "GC2_PACER_DISABLED_OK"),
        *((f"gc{other}", "off", {"PCC_GC_BACKEND": str(other)}, "GC2_PACER_DISABLED_OK")
          for other in (0, 1, 3, 4)),
    ):
        process_env = dict(env, **changes)
        if label == "default":
            process_env.pop("PCC_GC2_PACER_PERCENT")
        result = _record_execution(tmp_path, label, [str(binary), arg], process_env)
        assert (result["returncode"], result["stdout"], result["stderr"]) == (
            0, expected + "\n", "",
        ), (label, result)
