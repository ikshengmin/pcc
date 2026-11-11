from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pcc.frontends.python.codegen import runtime_abi
from pcc.frontends.python.codegen.runtime_abi import (
    FREESTANDING_GC_CROSS_OBJECT_SIGNATURES,
    RUNTIME_SIGNATURES,
)


SOURCE = (
    Path(__file__).resolve().parents[2]
    / "pcc" / "frontends" / "python"
    / "codegen"
    / "runtime_abi.py"
)


def _chunk_literals(prefix: str) -> list[ast.Dict]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    literals: list[ast.Dict] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith(prefix):
            continue
        returns = [stmt for stmt in node.body if isinstance(stmt, ast.Return)]
        assert len(returns) == 1
        value = returns[0].value
        assert isinstance(value, ast.Dict)
        literals.append(value)
    return literals


def _chunk_keys(prefix: str) -> tuple[list[str], list[int]]:
    keys: list[str] = []
    sizes: list[int] = []
    for value in _chunk_literals(prefix):
        sizes.append(len(value.keys))
        for key in value.keys:
            assert isinstance(key, ast.Constant) and isinstance(key.value, str)
            keys.append(key.value)
    return keys, sizes


def _assemble_chunks(prefix: str) -> dict:
    parts = []
    for name, value in vars(runtime_abi).items():
        if name.startswith(prefix):
            parts.append((int(name.rsplit("_", 1)[1]), value))
    assembled = {}
    for _index, part in sorted(parts):
        assembled.update(part())
    return assembled


def test_runtime_signature_literals_are_bounded_function_chunks() -> None:
    keys, sizes = _chunk_keys("_runtime_signatures_part_")

    assert sizes
    assert max(sizes) <= 50
    assert len(keys) == len(set(keys))
    assert _assemble_chunks("_runtime_signatures_part_") == RUNTIME_SIGNATURES
    assert keys == list(RUNTIME_SIGNATURES)


def test_cross_object_signature_literals_are_bounded_function_chunks() -> None:
    keys, sizes = _chunk_keys("_cross_object_signatures_part_")

    assert sizes
    assert max(sizes) <= 50
    assembled = _assemble_chunks("_cross_object_signatures_part_")
    assert assembled == FREESTANDING_GC_CROSS_OBJECT_SIGNATURES
    assert list(assembled) == list(FREESTANDING_GC_CROSS_OBJECT_SIGNATURES)


def test_checked_frame_signature_starts_a_new_raw_chunk() -> None:
    symbol = "pcc_gc_note_frame_enter_checked"
    expected = (("c_ptr", "c_ptr"), "c_int64")
    keys, _sizes = _chunk_keys("_cross_object_signatures_part_")

    assert keys.count(symbol) == 1
    assert symbol not in runtime_abi._cross_object_signatures_part_0()
    assert runtime_abi._cross_object_signatures_part_12()[symbol] == expected
    assert FREESTANDING_GC_CROSS_OBJECT_SIGNATURES[symbol] == expected
    assert symbol not in RUNTIME_SIGNATURES


@pytest.mark.parametrize(
    ("prefix", "signatures"),
    [
        ("_runtime_signatures_part_", RUNTIME_SIGNATURES),
        ("_cross_object_signatures_part_", FREESTANDING_GC_CROSS_OBJECT_SIGNATURES),
    ],
    ids=["runtime", "cross_object"],
)
def test_signature_chunks_preserve_flat_literal_values_and_order(
    prefix: str, signatures: dict
) -> None:
    # A single literal supplies the reference semantics independently of the
    # chunk calls and update sequence, including last-write-wins values and
    # first-insertion positions for duplicate names in the cross-object table.
    literals = _chunk_literals(prefix)
    flattened = ast.Dict(
        keys=[key for literal in literals for key in literal.keys],
        values=[value for literal in literals for value in literal.values],
    )
    expression = ast.fix_missing_locations(ast.Expression(body=flattened))
    expected = eval(compile(expression, str(SOURCE), "eval"), vars(runtime_abi))

    assert list(signatures.items()) == list(expected.items())


def test_cross_object_chunks_are_applied_once_in_declaration_order() -> None:
    # Specification: docs/project-intent.md, ordinary Python dictionary semantics.
    # Duplicate declarations currently agree, so mapping equality alone cannot
    # detect reversed updates. Check the real assembly order, then call the
    # imported production helpers to verify last-write-wins and insertion order.
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    table = "FREESTANDING_GC_CROSS_OBJECT_SIGNATURES"
    declared = [
        node.name for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name.startswith("_cross_object_signatures_part_")
    ]
    applied = []
    constructors = 0
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name) and node.target.id == table
        ):
            constructors += 1
            applied.append(ast.unparse(node.value))
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            function = call.func
            if (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name) and function.value.id == table
            ):
                assert function.attr == "update" and len(call.args) == 1 and not call.keywords
                applied.append(ast.unparse(call.args[0]))

    assert constructors == 1
    assert len(declared) == len(set(declared))
    assert applied == [name + "()" for name in declared]
    pairs = [item for name in declared for item in getattr(runtime_abi, name)().items()]
    expected = dict(pairs)
    assert len(pairs) > len(expected)  # Exercise existing duplicate declarations.
    assert list(FREESTANDING_GC_CROSS_OBJECT_SIGNATURES.items()) == list(expected.items())


# Discover every bounded helper family, so a newly added family cannot escape
# the same native-literal contract by missing a hand-maintained parameter list.
_CHUNK_FAMILIES = tuple(sorted({
    name.rsplit("_", 1)[0] + "_"
    for name, value in vars(runtime_abi).items()
    if name.startswith("_") and "_part_" in name
    and name.rsplit("_", 1)[1].isdigit() and callable(value)
}))


@pytest.mark.parametrize("prefix", _CHUNK_FAMILIES)
def test_every_abi_chunk_family_obeys_native_literal_bound(prefix: str) -> None:
    assert {"_runtime_signatures_part_", "_cross_object_signatures_part_"} <= set(_CHUNK_FAMILIES)
    _keys, sizes = _chunk_keys(prefix)
    assert sizes
    assert max(sizes) <= 50
