"""Host-only ABI-table contracts; no runtime provider or collector is invoked."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pcc.frontends.python.codegen.runtime_abi import (
    FREESTANDING_GC_CROSS_OBJECT_SIGNATURES,
    is_freestanding_gc_cross_object_runtime_import,
)


ROOT = Path(__file__).resolve().parents[2]


def test_cross_object_signature_chunks_preserve_ordered_runtime_mapping():
    source = ROOT / "pcc/frontends/python/codegen/runtime_abi.py"
    parts = [node for node in ast.parse(source.read_text()).body
             if isinstance(node, ast.FunctionDef)
             and node.name.startswith("_cross_object_signatures_part_")]
    assert parts
    expected = {}
    for part in parts:
        assert len(part.body) == 1 and isinstance(part.body[0], ast.Return)
        chunk = part.body[0].value
        assert isinstance(chunk, ast.Dict), part.name
        assert len(chunk.keys) <= 50, part.name
        assert all(isinstance(key, ast.Constant) and isinstance(key.value, str)
                   for key in chunk.keys), part.name
        expected.update(ast.literal_eval(chunk))
    # Dict order matters as well as the last value of any repeated symbol.
    assert list(FREESTANDING_GC_CROSS_OBJECT_SIGNATURES.items()) == list(expected.items())


@pytest.mark.parametrize("symbol,parameters,parameter_source", (
    ("pcc_gc_object_node_set_freeing", ("c_ptr", "c_int64"), "(c_ptr,c_int64)"),
    ("py_dealloc_float", ("c_ptr",), "(c_ptr,)"),
))
def test_rebalanced_chunk_boundary_keeps_exact_raw_abi(
    symbol, parameters, parameter_source,
):
    assert FREESTANDING_GC_CROSS_OBJECT_SIGNATURES[symbol] == (parameters, "c_void")
    assert is_freestanding_gc_cross_object_runtime_import(
        symbol, parameter_source, "c_void",
    )
    assert not is_freestanding_gc_cross_object_runtime_import(
        symbol, parameter_source, "c_ptr",
    )
    assert not is_freestanding_gc_cross_object_runtime_import(
        symbol, parameter_source.replace("c_ptr", "c_int64", 1), "c_void",
    )
    assert not is_freestanding_gc_cross_object_runtime_import(
        symbol, "()", "c_void",
    )
