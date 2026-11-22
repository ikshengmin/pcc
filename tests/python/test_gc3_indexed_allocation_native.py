"""GC3's current indexed allocation is the proposed major-cycle fallback."""

import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.fixture
def indexed_allocation_binary(tmp_path, explicit_owned_runtime):
    repo = Path(__file__).resolve().parents[2]
    source = Path(__file__).with_name("fixtures") / "gc3_indexed_allocation_native.c"
    binary = tmp_path / "indexed_alloc"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(repo / "pcc/runtime/src")),
    )
    return binary


def _execute_gc3(tmp_path, binary, *args):
    return _record_execution(
        tmp_path, "gc3", [str(binary), *args], dict(os.environ, PCC_GC_BACKEND="3"),
    )


@pytest.mark.integration
def test_gc3_indexed_allocation_and_list_stores_during_active_mark(tmp_path, indexed_allocation_binary):
    result = _execute_gc3(tmp_path, indexed_allocation_binary)
    assert (result["returncode"], result["stdout"], result["stderr"]) == (
        0, "GC3_INDEXED_ALLOC_OK\n", "",
    ), result



@pytest.mark.integration
def test_gc3_dict_and_ownerless_first_store_during_active_mark(tmp_path, indexed_allocation_binary):
    result = _execute_gc3(tmp_path, indexed_allocation_binary, "dict")
    assert (result["returncode"], result["stdout"], result["stderr"]) == (
        0, "GC3_DICT_FIRST_STORE_OK\n", "",
    ), result
