"""Force positive, over-width and non-list tokens into the GC2 bridge."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc2_bridge_exact_width_and_fallback_debit(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc2_bridge_fallback_native.c"
    binary = tmp_path / "gc2_bridge_fallback"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(Path(__file__).resolve().parents[2] / "pcc/runtime/src")),
    )
    env = dict(os.environ, PCC_GC_BACKEND="2")
    for repeat in range(3):
        for shape in range(3):
            for enabled in (0, 1):
                result = _record_execution(
                    tmp_path, f"fallback-{shape}-{enabled}-{repeat}",
                    [str(binary), str(shape), str(enabled)], env,
                )
                assert (result["returncode"], result["stderr"]) == (0, ""), result
                assert result["stdout"] == (
                    f"GC2_FORCED_BRIDGE_FALLBACK_OK shape={shape} enabled={enabled} "
                    f"visits={int(enabled and shape == 0)} "
                    f"fallbacks={int(enabled and shape == 1)}\n"
                ), result
