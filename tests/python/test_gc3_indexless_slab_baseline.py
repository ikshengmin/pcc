"""Distinguish allocator provenance from tracing-index visibility on GC3."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
@pytest.mark.parametrize(("arguments", "expected"), [
    ((), "GC3_INDEXLESS_SLAB_BASELINE_OK"),
    (("record",), "GC3_INDEXLESS_TO_INDEXED_BASELINE_OK"),
    (("prepare",), "GC3_RECORD_PREPARE_RETRY_OK"),
], ids=["indexless", "stable-old-record", "prepare-retry"])
def test_gc3_published_cell_can_be_managed_without_a_tracing_index(
    tmp_path, explicit_owned_runtime, arguments, expected,
):
    source = Path(__file__).with_name("fixtures") / "gc3_indexless_slab_baseline.c"
    binary = tmp_path / "gc3_slab_baseline"
    repo = Path(__file__).resolve().parents[2]
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(repo / "pcc/runtime/src")),
    )
    result = _record_execution(
        tmp_path, "slab-baseline", [str(binary), *arguments], dict(os.environ, PCC_GC_BACKEND="3"),
    )
    assert (result["returncode"], result["stdout"], result["stderr"]) == (
        0, expected + "\n", "",
    ), result
