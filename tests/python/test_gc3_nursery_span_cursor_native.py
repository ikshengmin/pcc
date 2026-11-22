"""Execute the proposed span bitmap cursor without enabling a GC mode."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_owned_nursery_cursor_rejects_stale_epoch_and_freezes_range(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc3_nursery_span_cursor_native.c"
    binary = tmp_path / "span_cursor"
    compile_owned_c_with_runtime(source, binary, runtime_archive=explicit_owned_runtime)
    result = _record_execution(tmp_path, "span-cursor", [str(binary)], dict(os.environ))
    assert (result["returncode"], result["stdout"], result["stderr"]) == (
        0, "GC3_NURSERY_CURSOR_NATIVE_OK\n", "",
    ), result
