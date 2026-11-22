"""Execute the proposed span bitmap cursor without enabling a GC mode."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
@pytest.mark.parametrize(("arguments", "expected"), [
    ((), "GC3_NURSERY_CURSOR_NATIVE_OK"),
    (("stw",), "GC3_NURSERY_CURSOR_STW_OK"),
    (("unprotected",), "GC3_NURSERY_CURSOR_UNPROTECTED_CONTROL_OK"),
], ids=["cursor", "publish-stop", "unprotected-control"])
def test_owned_nursery_cursor_rejects_stale_epoch_and_freezes_range(
    tmp_path, explicit_owned_runtime, arguments, expected,
):
    source = Path(__file__).with_name("fixtures") / "gc3_nursery_span_cursor_native.c"
    binary = tmp_path / "span_cursor"
    compile_owned_c_with_runtime(source, binary, runtime_archive=explicit_owned_runtime)
    result = _record_execution(tmp_path, "span-cursor", [str(binary), *arguments], dict(os.environ, PCC_GC_BACKEND="3"))
    assert (result["returncode"], result["stdout"], result["stderr"]) == (
        0, expected + "\n", "",
    ), result
