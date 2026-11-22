"""Execute the public memoryview C entry, not an unreachable runtime helper."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.fixture
def buffer_probe(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc3_memoryview_export_root_native.c"
    binary = tmp_path / "memoryview_export"
    repo = Path(__file__).resolve().parents[2]
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(repo / "pcc/runtime/src")),
    )
    return binary


def _run_probe(tmp_path, binary, *args):
    return _record_execution(
        tmp_path, "gc3-public-buffer", [str(binary), *args], dict(os.environ, PCC_GC_BACKEND="3"),
    )


@pytest.mark.integration
def test_gc3_public_memoryview_buffer_keeps_exporter_readable(tmp_path, buffer_probe):
    result = _run_probe(tmp_path, buffer_probe)
    assert (result["returncode"], result["stderr"]) == (0, ""), result
    # Report the extra-root diagnostic without pinning the current absence
    # as a compatibility contract.
    assert result["stdout"] in {
        "GC3_MEMORYVIEW_EXPORT_OK dedicated-root=0\n",
        "GC3_MEMORYVIEW_EXPORT_OK dedicated-root=1\n",
    }, result



@pytest.mark.integration
@pytest.mark.parametrize("release_first", ("direct", "view"))
def test_gc3_shared_explicit_exports_unpin_on_last_release(tmp_path, buffer_probe, release_first):
    result = _run_probe(tmp_path, buffer_probe, release_first)
    assert (result["returncode"], result["stdout"], result["stderr"]) == (
        0, "GC3_EXPLICIT_BUFFER_PIN_OK\n", "",
    ), result
