"""Distinguish allocator provenance from tracing-index visibility on GC3."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc3_published_cell_can_be_managed_without_a_tracing_index(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc3_indexless_slab_baseline.c"
    binary = tmp_path / "gc3_slab_baseline"
    compile_owned_c_with_runtime(source, binary, runtime_archive=explicit_owned_runtime)
    result = _record_execution(
        tmp_path, "slab-baseline", [str(binary)], dict(os.environ, PCC_GC_BACKEND="3"),
    )
    assert (result["returncode"], result["stdout"], result["stderr"]) == (
        0, "GC3_INDEXLESS_SLAB_BASELINE_OK\n", "",
    ), result
