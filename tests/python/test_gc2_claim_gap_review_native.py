"""Exercise the dormant GC2 mark claim before scan and before retirement."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc2_claim_survives_parkable_gaps(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc2_claim_gap_review_native.c"
    binary = tmp_path / "gc2_claim_gap_review"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(Path(__file__).resolve().parents[2] / "pcc/runtime/src")),
    )
    env = dict(os.environ, PCC_GC_BACKEND="2")
    for repeat in range(3):
        for abort in (0, 1):
            result = _record_execution(
                tmp_path, f"claim-gap-{abort}-{repeat}", [str(binary), str(abort)], env,
            )
            assert (result["returncode"], result["stderr"]) == (0, ""), result
            assert result["stdout"] == (
                f"GC2_CLAIM_GAP_OK abort={abort} cuts=2 remaining={abort + 1}\n"
            ), result
