"""Execute the public memoryview C entry, not an unreachable runtime helper."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc3_public_memoryview_buffer_keeps_exporter_readable(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc3_memoryview_export_root_native.c"
    binary = tmp_path / "memoryview_export"
    compile_owned_c_with_runtime(source, binary, runtime_archive=explicit_owned_runtime)
    result = _record_execution(
        tmp_path, "gc3-public-buffer", [str(binary)], dict(os.environ, PCC_GC_BACKEND="3"),
    )
    assert (result["returncode"], result["stderr"]) == (0, ""), result
    # Report the extra-root diagnostic without pinning the current absence
    # as a compatibility contract.
    assert result["stdout"] in {
        "GC3_MEMORYVIEW_EXPORT_OK dedicated-root=0\n",
        "GC3_MEMORYVIEW_EXPORT_OK dedicated-root=1\n",
    }, result
