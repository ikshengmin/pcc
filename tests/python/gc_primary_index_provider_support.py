"""Canonical primary-index provider support, independent of GC copy execution.

Only the three address/selector functions, their constants and the real shard
array are selected. No collector, tracking mutation, lock, or reentry body is
copied into this support module.
"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
from pathlib import Path


FUNCTIONS = (
    "_tracked_shard_address",
    "_tracked_shard_index",
    "_tracked_primary_index_cells",
)
CONSTANTS = ("_SHARD_COUNT", "_SHARD_STRIDE", "_SHARD_PRIMARY_INDEX")
STORAGE = "pcc_gc_tracked_shards"
UNSAFE_IMPORTS = (
    "define_global_i64_array", "global_addr", "logical_shift_right_i64",
    "null", "ptr_add", "ptr_diff",
)


def _segment(source: str, node: ast.AST) -> str:
    start = min([node.lineno] + [n.lineno for n in getattr(node, "decorator_list", ())])
    return "".join(source.splitlines(keepends=True)[start - 1:node.end_lineno]).rstrip()


def canonical_provider_source(runtime_dir: Path) -> tuple[str, dict]:
    """Return source-derived provider bytes; fail closed on dependency drift."""
    tracking_path = runtime_dir / "py/freestanding_gc_tracking.py"
    state_path = runtime_dir / "py/freestanding_gc_state.py"
    tracking, state = tracking_path.read_text(), state_path.read_text()
    tree, state_tree = ast.parse(tracking), ast.parse(state)
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    constants = {n.target.id: n for n in tree.body
                 if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    imported_unsafe = {
        alias.name for module in (tree, state_tree) for node in module.body
        if isinstance(node, ast.ImportFrom) and node.module == "pcc.unsafe"
        for alias in node.names
    }
    if not set(UNSAFE_IMPORTS) <= imported_unsafe:
        raise ValueError("canonical primary-index provider imports changed")
    allowed = set(FUNCTIONS + CONSTANTS + UNSAFE_IMPORTS + ("i64", "c_ptr", "c_abi_export"))
    for name in FUNCTIONS:
        function = functions[name]
        local = {n.arg for n in function.args.args}
        local.update(n.id for n in ast.walk(function)
                     if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
        unknown = {n.id for n in ast.walk(function)
                   if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)} - allowed - local
        if unknown:
            raise ValueError("canonical primary-index provider dependencies changed: " + repr(sorted(unknown)))
    storage = [n for n in state_tree.body
               if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
               and isinstance(n.value.func, ast.Name)
               and n.value.func.id == "define_global_i64_array"
               and n.value.args and isinstance(n.value.args[0], ast.Constant)
               and n.value.args[0].value == STORAGE]
    if len(storage) != 1:
        raise ValueError("canonical primary-index shard storage is not unique")
    values = [ast.literal_eval(n) for n in storage[0].value.args[1:]]
    count = ast.literal_eval(constants["_SHARD_COUNT"].value)
    stride = ast.literal_eval(constants["_SHARD_STRIDE"].value)
    offset = ast.literal_eval(constants["_SHARD_PRIMARY_INDEX"].value)
    if (not all(type(n) is int and n == 0 for n in values)
            or len(values) * 8 < (count + 1) * stride
            or not 0 <= offset <= stride - 32):
        raise ValueError("canonical primary-index storage/layout contract changed")
    parts = [
        '"""Source-selected canonical primary-index address provider only."""',
        "from pcc import i64",
        "from pcc.extern import c_abi_export, c_ptr",
        "from pcc.unsafe import (" + ", ".join(UNSAFE_IMPORTS) + ")",
        "__pcc_freestanding__ = True",
        *(_segment(tracking, constants[n]) for n in CONSTANTS),
        _segment(state, storage[0]),
        *(_segment(tracking, functions[n]) for n in FUNCTIONS),
    ]
    source = "\n\n".join(parts) + "\n"
    return source, {
        "schema": "pcc.primary-index-fixture-provider.v1",
        "tracking_sha256": hashlib.sha256(tracking.encode()).hexdigest(),
        "state_sha256": hashlib.sha256(state.encode()).hexdigest(),
        "provider_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "functions": list(FUNCTIONS), "constants": list(CONSTANTS),
        "storage": STORAGE, "storage_bytes": len(values) * 8,
        "shard_count": count, "shard_stride": stride, "primary_offset": offset,
        "scope": "address-selection-and-storage-only; no GC-copy/collector execution",
    }


def emit_provider_object(runtime_dir: Path, build: Path, triple: str) -> Path:
    """Compile only the canonical provider; caller owns the existing budget."""
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.owned_runtime_build import _compile_runtime_module, runtime_ir_passes
    from pcc.ir.optimization.driver import optimize_ir

    source, receipt = canonical_provider_source(runtime_dir)
    # _compile_runtime_module derives its ABI root from source.parent.parent.
    # Keep a tiny source-only runtime view, with real canonical headers, instead
    # of changing that helper or mutating the repository's runtime directory.
    provider_root = build / "primary-index-provider-runtime"
    provider_py = provider_root / "py"
    provider_py.mkdir(parents=True)
    shutil.copytree(runtime_dir / "include", provider_root / "include")
    shutil.copyfile(runtime_dir / "Makefile", provider_root / "Makefile")
    provider_source = provider_py / "freestanding_gc_tracking.py"
    provider_source.write_text(source)
    ir_path = build / "primary-index-provider.ll"
    _compile_runtime_module(provider_source.stem, str(provider_source), str(ir_path), triple)
    passes = runtime_ir_passes(str(runtime_dir))
    ir = optimize_ir(ir_path.read_text(), passes)
    ir_path.write_text(ir)
    output = build / "primary-index-provider.o"
    encoded = emit_owned_object(ir, triple)
    output.write_bytes(encoded)
    receipt.update(runtime_passes=passes, target=triple,
                   ir_sha256=hashlib.sha256(ir.encode()).hexdigest(),
                   object_sha256=hashlib.sha256(encoded).hexdigest())
    (build / "primary-index-provider.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return output
