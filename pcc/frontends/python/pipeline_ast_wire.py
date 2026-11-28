"""Stable JSON wire codec for the typed-Python frontend AST."""

from __future__ import annotations

import json
import sys

from pcc.extern import c_int64, c_obj, c_rawptr, extern
from pcc.unsafe import load_i32, load_i64
from pcc.frontends.python import py_ast

from pcc.frontends.python.pipeline_modes import PyPipelineError
from pcc.frontends.python.py_ast_contract import (
    PY_AST_FIELD_NAME_OVERRIDES as _PY_AST_FIELD_NAME_OVERRIDES,
)


_PY_AST_WIRE_SCHEMA = "pcc.frontends.python.py_ast.v1"
_PY_AST_WIRE_SCHEMA_V2 = "pcc.frontends.python.py_ast.v2"
_PY_AST_WIRE_NODE_KEY = "__pcc_py_ast_v1__"
_PY_AST_WIRE_BYTES_KEY = "__pcc_bytes_v1__"

_owned_ast_tape_scan = extern("pcc_json_scan_owned_ast_v2", (c_obj,), c_obj)
_owned_ast_tape_token = extern(
    "pcc_json_owned_ast_v2_token_value", (c_obj, c_int64, c_int64, c_int64), c_obj
)
_owned_ast_tape_data = extern("py_bytes_data_ptr", (c_obj,), c_rawptr)
_owned_ast_tape_text_len = extern("py_str_byte_len", (c_obj,), c_int64)
_try_owned_ast_json = extern("pcc_json_try_loads_owned_ast", (c_obj,), c_obj)
_owned_ast_raw_reclaimable = extern("pcc_allocator_reclaimable_slab_bytes", (), c_int64)
_owned_ast_mapped = extern("pcc_allocator_mapped_bytes", (), c_int64)
_owned_ast_no_park = extern("pcc_thread_no_park_depth", (), c_int64)
_owned_ast_graph_depth = extern("pcc_py_gc_minor_graph_lock_depth", (), c_int64)
_owned_ast_pending = extern("py_err_occurred", (), c_int64)
_owned_ast_raw_trim = extern("pcc_allocator_trim", (), c_int64)


def _maybe_trim_owned_ast_raw_highwater() -> None:
    # A completed native parse may retire many temporary raw buffers without
    # the previous provider's collection cadence. Return only completely
    # free raw slabs at this coarse boundary, before AST allocations begin.
    # The absolute 8 MiB floor and >= 1/8 mapped high-water gate avoid scanning
    # small repetitive parses or a small idle fraction; no managed GC or
    # collector-pacing change. The existing trim owns the allocator lock.
    if _owned_ast_pending() != 0:
        return
    reclaimable = _owned_ast_raw_reclaimable()
    if reclaimable < 8388608:
        return
    mapped = _owned_ast_mapped()
    if mapped <= 0 or reclaimable < mapped // 8:
        return
    if _owned_ast_no_park() != 0 or _owned_ast_graph_depth() != 0:
        return
    _owned_ast_raw_trim()



def _closed_world_node_kind(node) -> str:
    try:
        return type(node).__name__
    except AttributeError:
        return ""


def _py_ast_field_value(obj, field_name, default=None):
    return getattr(obj, field_name, default)


def _py_ast_field_names(obj):
    """Return the stable field order for a known typed-Python AST node.

    Closed-world walkers also encounter primitive leaves.  Those deliberately
    have no child fields; unknown values therefore return an empty tuple rather
    than importing the host dataclass implementation.
    """

    return _PY_AST_FIELD_NAME_OVERRIDES.get(_closed_world_node_kind(obj), ())


_PY_AST_BASE_NAME_OVERRIDES = {
    "RawPointerType": ("Type",),
    "IntType": ("Type",),
    "FloatType": ("Type",),
    "ComplexType": ("Type",),
    "BoolType": ("Type",),
    "NoneType": ("Type",),
    "StrType": ("Type",),
    "BytesType": ("Type",),
    "ByteArrayType": ("Type",),
    "MemoryViewType": ("Type",),
    "ListType": ("Type",),
    "SetType": ("Type",),
    "ValueArrayType": ("Type",),
    "DictType": ("Type",),
    "TupleType": ("Type",),
    "FuncType": ("Type",),
    "ClassType": ("Type",),
    "ValueClassType": ("ClassType",),
    "DynType": ("Type",),
    "IntLit": ("Expr",),
    "FloatLit": ("Expr",),
    "ComplexLit": ("Expr",),
    "BoolLit": ("Expr",),
    "NoneLit": ("Expr",),
    "StrLit": ("Expr",),
    "BytesLit": ("Expr",),
    "Name": ("Expr",),
    "BinOp": ("Expr",),
    "UnaryOp": ("Expr",),
    "Compare": ("Expr",),
    "BoolExpr": ("Expr",),
    "Call": ("Expr",),
    "Attr": ("Expr",),
    "Subscript": ("Expr",),
    "Slice": ("Expr",),
    "ListExpr": ("Expr",),
    "DictExpr": ("Expr",),
    "TupleExpr": ("Expr",),
    "IfExpr": ("Expr",),
    "Lambda": ("Expr",),
    "Assign": ("Stmt",),
    "AugAssign": ("Stmt",),
    "ExprStmt": ("Stmt",),
    "If": ("Stmt",),
    "While": ("Stmt",),
    "For": ("Stmt",),
    "Return": ("Stmt",),
    "Pass": ("Stmt",),
    "Break": ("Stmt",),
    "Continue": ("Stmt",),
    "Raise": ("Stmt",),
    "Try": ("Stmt",),
    "With": ("Stmt",),
    "Import": ("Stmt",),
    "ImportFrom": ("Stmt",),
    "Global": ("Stmt",),
    "Nonlocal": ("Stmt",),
    "Delete": ("Stmt",),
    "FuncDef": ("Stmt",),
    "ClassDef": ("Stmt",),
}


_PY_AST_FIELD_TYPE_OVERRIDES = {
    "SourceSpan": {
        "file": "str",
        "line": "int",
        "col": "int",
        "end_line": "int",
        "end_col": "int",
    },
    "Type": {"name": "str"},
    "RawPointerType": {"name": "str"},
    "IntType": {"name": "str", "width": "int", "signed": "bool"},
    "FloatType": {"name": "str", "width": "int"},
    "ComplexType": {"name": "str"},
    "BoolType": {"name": "str"},
    "NoneType": {"name": "str"},
    "StrType": {"name": "str"},
    "BytesType": {"name": "str"},
    "ByteArrayType": {"name": "str"},
    "MemoryViewType": {"name": "str"},
    "ListType": {"name": "str", "elem": "Type"},
    "SetType": {"name": "str", "elem": "Type"},
    "ValueArrayType": {"name": "str", "elem": "Type", "length": "int"},
    "DictType": {"name": "str", "key": "Type", "value": "Type"},
    "TupleType": {"name": "str", "elems": "tuple[Type, ...]"},
    "FuncType": {"name": "str", "params": "tuple[Type, ...]", "ret": "Type"},
    "ClassType": {
        "name": "str",
        "module": "str",
        "fields": "tuple[tuple[str, Type], ...]",
        "bases": "tuple[ClassType, ...]",
        "properties": "tuple[tuple[str, Type], ...]",
        "valueclass": "bool",
    },
    "ValueClassType": {
        "name": "str",
        "module": "str",
        "fields": "tuple[tuple[str, Type], ...]",
        "bases": "tuple[ClassType, ...]",
        "properties": "tuple[tuple[str, Type], ...]",
        "valueclass": "bool",
        "flattened": "bool",
        "nullable_fields": "bool",
    },
    "DynType": {"name": "str"},
    "Expr": {"span": "SourceSpan", "ty": "Type"},
    "IntLit": {"span": "SourceSpan", "ty": "Type", "value": "int"},
    "FloatLit": {"span": "SourceSpan", "ty": "Type", "value": "float"},
    "ComplexLit": {
        "span": "SourceSpan",
        "ty": "Type",
        "real": "float",
        "imag": "float",
    },
    "BoolLit": {"span": "SourceSpan", "ty": "Type", "value": "bool"},
    "NoneLit": {"span": "SourceSpan", "ty": "Type"},
    "StrLit": {"span": "SourceSpan", "ty": "Type", "value": "str"},
    "BytesLit": {"span": "SourceSpan", "ty": "Type", "value": "bytes"},
    "Name": {"span": "SourceSpan", "ty": "Type", "ident": "str"},
    "BinOp": {
        "span": "SourceSpan",
        "ty": "Type",
        "op": "str",
        "lhs": "Expr",
        "rhs": "Expr",
    },
    "UnaryOp": {
        "span": "SourceSpan",
        "ty": "Type",
        "op": "str",
        "operand": "Expr",
    },
    "Compare": {
        "span": "SourceSpan",
        "ty": "Type",
        "op": "str",
        "lhs": "Expr",
        "rhs": "Expr",
    },
    "BoolExpr": {
        "span": "SourceSpan",
        "ty": "Type",
        "op": "str",
        "left": "Expr",
        "right": "Expr",
    },
    "Call": {
        "span": "SourceSpan",
        "ty": "Type",
        "func": "Expr",
        "args": "tuple[Expr, ...]",
        "kwargs": "tuple[tuple[str, Expr], ...]",
        "operand_order": "tuple[tuple[str, int], ...]",
        "is_set_literal": "bool",
    },
    "Attr": {"span": "SourceSpan", "ty": "Type", "obj": "Expr", "name": "str"},
    "Subscript": {
        "span": "SourceSpan",
        "ty": "Type",
        "obj": "Expr",
        "idx": "Expr",
    },
    "Slice": {
        "span": "SourceSpan",
        "ty": "Type",
        "lo": "Expr",
        "hi": "Expr",
        "step": "Expr",
    },
    "ListExpr": {"span": "SourceSpan", "ty": "Type", "elems": "tuple[Expr, ...]"},
    "DictExpr": {
        "span": "SourceSpan",
        "ty": "Type",
        "pairs": "tuple[tuple[Expr, Expr], ...]",
    },
    "TupleExpr": {"span": "SourceSpan", "ty": "Type", "elems": "tuple[Expr, ...]"},
    "IfExpr": {
        "span": "SourceSpan",
        "ty": "Type",
        "cond": "Expr",
        "then_e": "Expr",
        "else_e": "Expr",
    },
    "Lambda": {
        "span": "SourceSpan",
        "ty": "Type",
        "params": "tuple[Arg, ...]",
        "body": "Expr",
    },
    "Stmt": {"span": "SourceSpan"},
    "Assign": {
        "span": "SourceSpan",
        "targets": "tuple[Expr, ...]",
        "value": "Expr",
        "annotation": "Type",
        "has_value": "bool",
    },
    "AugAssign": {
        "span": "SourceSpan",
        "target": "Expr",
        "op": "str",
        "value": "Expr",
    },
    "ExprStmt": {"span": "SourceSpan", "expr": "Expr"},
    "If": {
        "span": "SourceSpan",
        "cond": "Expr",
        "body": "tuple[Stmt, ...]",
        "else_body": "tuple[Stmt, ...]",
    },
    "While": {
        "span": "SourceSpan",
        "cond": "Expr",
        "body": "tuple[Stmt, ...]",
        "else_body": "tuple[Stmt, ...]",
    },
    "For": {
        "span": "SourceSpan",
        "target": "Expr",
        "iter": "Expr",
        "body": "tuple[Stmt, ...]",
        "else_body": "tuple[Stmt, ...]",
        "is_async": "bool",
    },
    "Return": {"span": "SourceSpan", "value": "Expr"},
    "Pass": {"span": "SourceSpan"},
    "Break": {"span": "SourceSpan"},
    "Continue": {"span": "SourceSpan"},
    "Raise": {"span": "SourceSpan", "exc": "Expr", "cause": "Expr"},
    "Try": {
        "span": "SourceSpan",
        "body": "tuple[Stmt, ...]",
        "handlers": "tuple[ExceptHandler, ...]",
        "else_body": "tuple[Stmt, ...]",
        "finally_body": "tuple[Stmt, ...]",
    },
    "ExceptHandler": {
        "exc_type": "Expr",
        "name": "str",
        "body": "tuple[Stmt, ...]",
        "span": "SourceSpan",
    },
    "With": {
        "span": "SourceSpan",
        "items": "tuple[tuple[Expr, Expr], ...]",
        "body": "tuple[Stmt, ...]",
        "is_async": "bool",
    },
    "Import": {"span": "SourceSpan", "names": "tuple[tuple[str, str], ...]"},
    "ImportFrom": {
        "span": "SourceSpan",
        "module": "str",
        "names": "tuple[tuple[str, str], ...]",
        "level": "int",
    },
    "Global": {"span": "SourceSpan", "names": "tuple[str, ...]"},
    "Nonlocal": {"span": "SourceSpan", "names": "tuple[str, ...]"},
    "Delete": {"span": "SourceSpan", "targets": "tuple[Expr, ...]"},
    "Arg": {
        "name": "str",
        "annotation": "Type",
        "default": "Expr",
        "kind": "str",
        "has_default": "bool",
    },
    "FuncDef": {
        "span": "SourceSpan",
        "name": "str",
        "args": "tuple[Arg, ...]",
        "return_ty": "Type",
        "body": "tuple[Stmt, ...]",
        "decorators": "tuple[Expr, ...]",
        "is_method": "bool",
        "is_async": "bool",
        "has_return_annotation": "bool",
        "manual_pointer_abi": "bool",
    },
    "ClassDef": {
        "span": "SourceSpan",
        "name": "str",
        "bases": "tuple[Expr, ...]",
        "keywords": "tuple[tuple[str, Expr], ...]",
        "body": "tuple[Stmt, ...]",
        "decorators": "tuple[Expr, ...]",
    },
    "Module": {"name": "str", "body": "tuple[Stmt, ...]", "docstring": "str"},
}


def _py_ast_field_type_override(class_name: str, field_name: str):
    pairs = ()
    if class_name == "SourceSpan":
        pairs = (
            ("file", "str"),
            ("line", "int"),
            ("col", "int"),
            ("end_line", "int"),
            ("end_col", "int"),
        )
    elif class_name == "Type":
        pairs = (("name", "str"),)
    elif class_name == "Expr":
        pairs = (("span", "SourceSpan"), ("ty", "Type"))
    elif class_name == "Stmt":
        pairs = (("span", "SourceSpan"),)
    elif class_name == "Name":
        pairs = (("span", "SourceSpan"), ("ty", "Type"), ("ident", "str"))
    elif class_name == "Arg":
        pairs = (
            ("name", "str"),
            ("annotation", "Type"),
            ("default", "Expr"),
            ("kind", "str"),
            ("has_default", "bool"),
        )
    elif class_name == "FuncDef":
        pairs = (
            ("span", "SourceSpan"),
            ("name", "str"),
            ("args", "tuple[Arg, ...]"),
            ("return_ty", "Type"),
            ("body", "tuple[Stmt, ...]"),
            ("decorators", "tuple[Expr, ...]"),
            ("is_method", "bool"),
            ("is_async", "bool"),
            ("has_return_annotation", "bool"),
            ("manual_pointer_abi", "bool"),
        )
    elif class_name == "ClassDef":
        pairs = (
            ("span", "SourceSpan"),
            ("name", "str"),
            ("bases", "tuple[Expr, ...]"),
            ("keywords", "tuple[tuple[str, Expr], ...]"),
            ("body", "tuple[Stmt, ...]"),
            ("decorators", "tuple[Expr, ...]"),
        )
    elif class_name == "Assign":
        pairs = (
            ("span", "SourceSpan"),
            ("targets", "tuple[Expr, ...]"),
            ("value", "Expr"),
            ("annotation", "Type"),
            ("has_value", "bool"),
        )
    elif class_name == "For":
        pairs = (
            ("span", "SourceSpan"),
            ("target", "Expr"),
            ("iter", "Expr"),
            ("body", "tuple[Stmt, ...]"),
            ("else_body", "tuple[Stmt, ...]"),
            ("is_async", "bool"),
        )
    elif class_name == "Return":
        pairs = (("span", "SourceSpan"), ("value", "Expr"))
    elif class_name == "ExprStmt":
        pairs = (("span", "SourceSpan"), ("expr", "Expr"))
    elif class_name == "If" or class_name == "While":
        pairs = (
            ("span", "SourceSpan"),
            ("cond", "Expr"),
            ("body", "tuple[Stmt, ...]"),
            ("else_body", "tuple[Stmt, ...]"),
        )
    elif class_name == "Call":
        pairs = (
            ("span", "SourceSpan"),
            ("ty", "Type"),
            ("func", "Expr"),
            ("args", "tuple[Expr, ...]"),
            ("kwargs", "tuple[tuple[str, Expr], ...]"),
            ("operand_order", "tuple[tuple[str, int], ...]"),
            ("is_set_literal", "bool"),
        )
    elif class_name == "Attr":
        pairs = (
            ("span", "SourceSpan"),
            ("ty", "Type"),
            ("obj", "Expr"),
            ("name", "str"),
        )
    elif class_name == "TupleExpr" or class_name == "ListExpr":
        pairs = (
            ("span", "SourceSpan"),
            ("ty", "Type"),
            ("elems", "tuple[Expr, ...]"),
        )
    if pairs:
        for override_field_name, field_type_text in pairs:
            if override_field_name == field_name:
                return field_type_text
        return None
    for override_class_name, field_map in _PY_AST_FIELD_TYPE_OVERRIDES.items():
        if override_class_name != class_name:
            continue
        for override_field_name, field_type_text in field_map.items():
            if override_field_name == field_name:
                return field_type_text
        return None
    return None


def _py_ast_bytes_to_wire(value):
    items = []
    i = 0
    while i < len(value):
        items.append(int(value[i]))
        i += 1
    return {_PY_AST_WIRE_BYTES_KEY: items}


def _py_ast_to_wire(value):
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return _py_ast_bytes_to_wire(value)
    kind = _closed_world_node_kind(value)
    if kind == "bytes":
        return _py_ast_bytes_to_wire(value)
    if isinstance(value, (tuple, list)):
        return [_py_ast_to_wire(item) for item in value]
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            out[str(key)] = _py_ast_to_wire(item)
        return out
    field_names = _PY_AST_FIELD_NAME_OVERRIDES.get(kind)
    if field_names is None:
        raise PyPipelineError("cannot serialize py_ast node kind " + kind)
    fields = {}
    for field_name in field_names:
        fields[field_name] = _py_ast_to_wire(
            _py_ast_field_value(value, field_name, None)
        )
    return {_PY_AST_WIRE_NODE_KEY: kind, "fields": fields}


def _py_ast_wire_bytes(items):
    if items is None:
        return b""
    raw = []
    for item in items:
        raw.append(int(item))
    return bytes(raw)


class _PyAstV2Fields:
    """Private positional field view for the shared AST constructor."""

    def __init__(self, row: list, positions: dict) -> None:
        self.row: list = row
        self.positions: dict = positions


def _py_ast_wire_field(fields, name: str, default=None):
    if not isinstance(fields, dict):
        if isinstance(fields, _PyAstV2Fields):
            positions = fields.positions
            if name in positions:
                return fields.row[positions[name]]
        return default
    if name not in fields:
        return default
    # One lookup, not two: this runs once per AST field for every node of
    # every module, and each lookup hashes the key and string-compares it.
    # Subscript rather than ``.get`` — ``dict.get`` mis-lowers under pcc1.
    return _py_ast_from_wire(fields[name])


def _py_ast_wire_tuple_field(fields, name: str):
    value = _py_ast_wire_field(fields, name, ())
    return () if value is None else value


def _py_ast_wire_bool_field(fields, name: str, default: bool):
    value = _py_ast_wire_field(fields, name, default)
    return default if value is None else value


def _py_ast_node_replace(node, changes):
    """Rebuild ``node`` with ``changes`` applied, without dataclass reflection.

    ``dataclasses.replace`` needs the host dataclass implementation plus ``**``
    expansion of a dynamically built mapping, and neither is available to the
    self-hosted compiler -- which is why nothing in the closed-world frontend
    uses ``dataclasses.fields``. The wire schema already names every field of
    every node in a stable order, and ``_py_ast_node_from_wire`` already builds
    a node from a field mapping, so an AST rewrite is a read of the old fields
    plus that constructor.
    """

    fields = {}
    for field_name in _py_ast_field_names(node):
        if field_name in changes:
            fields[field_name] = changes[field_name]
        else:
            fields[field_name] = _py_ast_field_value(node, field_name, None)
    return _py_ast_node_from_wire(_closed_world_node_kind(node), fields)


def _py_ast_from_wire(value):
    if isinstance(value, dict):
        if _PY_AST_WIRE_BYTES_KEY in value:
            return _py_ast_wire_bytes(value.get(_PY_AST_WIRE_BYTES_KEY))
        kind = value.get(_PY_AST_WIRE_NODE_KEY)
        if isinstance(kind, str) and kind:
            return _py_ast_node_from_wire(kind, value.get("fields", {}))
        out = {}
        for key, item in value.items():
            out[str(key)] = _py_ast_from_wire(item)
        return out
    if isinstance(value, list):
        return tuple(_py_ast_from_wire(item) for item in value)
    return value


def _py_ast_node_from_wire(kind: str, fields, positional: bool = False):
    # Only fully decoded canonical v2 rows use the pinned numeric slots.
    # V1 and alternate layouts keep the named/default reader semantics.
    from pcc.frontends.python import py_ast as _pa

    if kind == "SourceSpan":
        return _pa.SourceSpan(
            (fields[1] if positional else _py_ast_wire_field(fields, "file", "")),
            (fields[2] if positional else _py_ast_wire_field(fields, "line", 0)),
            (fields[3] if positional else _py_ast_wire_field(fields, "col", 0)),
            (fields[4] if positional else _py_ast_wire_field(fields, "end_line", 0)),
            (fields[5] if positional else _py_ast_wire_field(fields, "end_col", 0)),
        )
    if kind == "Type":
        return _pa.Type((fields[1] if positional else _py_ast_wire_field(fields, "name", "")))
    if kind == "RawPointerType":
        return _pa.RawPointerType((fields[1] if positional else _py_ast_wire_field(fields, "name", "pcc.extern.c_rawptr")))
    if kind == "IntType":
        return _pa.IntType(
            (fields[1] if positional else _py_ast_wire_field(fields, "name", "int")),
            (fields[2] if positional else _py_ast_wire_field(fields, "width", 64)),
            (fields[3] if positional else _py_ast_wire_field(fields, "signed", True)),
        )
    if kind == "FloatType":
        return _pa.FloatType(
            (fields[1] if positional else _py_ast_wire_field(fields, "name", "float")),
            (fields[2] if positional else _py_ast_wire_field(fields, "width", 64)),
        )
    if kind == "ComplexType":
        return _pa.ComplexType((fields[1] if positional else _py_ast_wire_field(fields, "name", "complex")))
    if kind == "BoolType":
        return _pa.BoolType((fields[1] if positional else _py_ast_wire_field(fields, "name", "bool")))
    if kind == "NoneType":
        return _pa.NoneType((fields[1] if positional else _py_ast_wire_field(fields, "name", "None")))
    if kind == "StrType":
        return _pa.StrType((fields[1] if positional else _py_ast_wire_field(fields, "name", "str")))
    if kind == "BytesType":
        return _pa.BytesType((fields[1] if positional else _py_ast_wire_field(fields, "name", "bytes")))
    if kind == "ByteArrayType":
        return _pa.ByteArrayType((fields[1] if positional else _py_ast_wire_field(fields, "name", "bytearray")))
    if kind == "MemoryViewType":
        return _pa.MemoryViewType((fields[1] if positional else _py_ast_wire_field(fields, "name", "memoryview")))
    if kind == "ListType":
        return _pa.ListType(
            (fields[1] if positional else _py_ast_wire_field(fields, "name", "list")),
            (fields[2] if positional else _py_ast_wire_field(fields, "elem")),
        )
    if kind == "SetType":
        return _pa.SetType(
            (fields[1] if positional else _py_ast_wire_field(fields, "name", "set")),
            (fields[2] if positional else _py_ast_wire_field(fields, "elem")),
        )
    if kind == "ValueArrayType":
        return _pa.ValueArrayType(
            (fields[1] if positional else _py_ast_wire_field(fields, "name", "pcc.array")),
            (fields[2] if positional else _py_ast_wire_field(fields, "elem")),
            (fields[3] if positional else _py_ast_wire_field(fields, "length", 0)),
        )
    if kind == "DictType":
        return _pa.DictType(
            (fields[1] if positional else _py_ast_wire_field(fields, "name", "dict")),
            (fields[2] if positional else _py_ast_wire_field(fields, "key")),
            (fields[3] if positional else _py_ast_wire_field(fields, "value")),
        )
    if kind == "TupleType":
        return _pa.TupleType(
            (fields[1] if positional else _py_ast_wire_field(fields, "name", "tuple")),
            (fields[2] if positional else _py_ast_wire_field(fields, "elems", ())),
        )
    if kind == "FuncType":
        return _pa.FuncType(
            (fields[1] if positional else _py_ast_wire_field(fields, "name", "func")),
            (fields[2] if positional else _py_ast_wire_field(fields, "params", ())),
            (fields[3] if positional else _py_ast_wire_field(fields, "ret")),
        )
    if kind == "ClassType":
        return _pa.ClassType(
            (fields[1] if positional else _py_ast_wire_field(fields, "name", "")),
            (fields[2] if positional else _py_ast_wire_field(fields, "module", "")),
            ((() if fields[3] is None else fields[3]) if positional else _py_ast_wire_tuple_field(fields, "fields")),
            ((() if fields[4] is None else fields[4]) if positional else _py_ast_wire_tuple_field(fields, "bases")),
            ((() if fields[5] is None else fields[5]) if positional else _py_ast_wire_tuple_field(fields, "properties")),
            ((False if fields[6] is None else fields[6]) if positional else _py_ast_wire_bool_field(fields, "valueclass", False)),
        )
    if kind == "ValueClassType":
        return _pa.ValueClassType(
            (fields[1] if positional else _py_ast_wire_field(fields, "name", "")),
            (fields[2] if positional else _py_ast_wire_field(fields, "module", "")),
            ((() if fields[3] is None else fields[3]) if positional else _py_ast_wire_tuple_field(fields, "fields")),
            ((() if fields[4] is None else fields[4]) if positional else _py_ast_wire_tuple_field(fields, "bases")),
            ((() if fields[5] is None else fields[5]) if positional else _py_ast_wire_tuple_field(fields, "properties")),
            ((True if fields[6] is None else fields[6]) if positional else _py_ast_wire_bool_field(fields, "valueclass", True)),
            ((True if fields[7] is None else fields[7]) if positional else _py_ast_wire_bool_field(fields, "flattened", True)),
            ((False if fields[8] is None else fields[8]) if positional else _py_ast_wire_bool_field(fields, "nullable_fields", False)),
        )
    if kind == "DynType":
        return _pa.DynType((fields[1] if positional else _py_ast_wire_field(fields, "name", "dyn")))
    if kind == "IntLit":
        return _pa.IntLit(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "value", 0)),
        )
    if kind == "FloatLit":
        return _pa.FloatLit(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "value", 0.0)),
        )
    if kind == "ComplexLit":
        return _pa.ComplexLit(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "real", 0.0)),
            (fields[4] if positional else _py_ast_wire_field(fields, "imag", 0.0)),
        )
    if kind == "BoolLit":
        return _pa.BoolLit(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "value", False)),
        )
    if kind == "NoneLit":
        return _pa.NoneLit(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
        )
    if kind == "StrLit":
        return _pa.StrLit(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "value", "")),
        )
    if kind == "BytesLit":
        return _pa.BytesLit(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "value", b"")),
        )
    if kind == "Name":
        return _pa.Name(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "ident", "")),
        )
    if kind == "BinOp":
        return _pa.BinOp(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "op", "")),
            (fields[4] if positional else _py_ast_wire_field(fields, "lhs")),
            (fields[5] if positional else _py_ast_wire_field(fields, "rhs")),
        )
    if kind == "UnaryOp":
        return _pa.UnaryOp(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "op", "")),
            (fields[4] if positional else _py_ast_wire_field(fields, "operand")),
        )
    if kind == "Compare":
        return _pa.Compare(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "op", "")),
            (fields[4] if positional else _py_ast_wire_field(fields, "lhs")),
            (fields[5] if positional else _py_ast_wire_field(fields, "rhs")),
        )
    if kind == "BoolExpr":
        return _pa.BoolExpr(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "op", "")),
            (fields[4] if positional else _py_ast_wire_field(fields, "left")),
            (fields[5] if positional else _py_ast_wire_field(fields, "right")),
        )
    if kind == "Call":
        return _pa.Call(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "func")),
            (fields[4] if positional else _py_ast_wire_field(fields, "args", ())),
            (fields[5] if positional else _py_ast_wire_field(fields, "kwargs", ())),
            (fields[6] if positional else _py_ast_wire_field(fields, "operand_order", ())),
            ((False if fields[7] is None else fields[7]) if positional else _py_ast_wire_bool_field(fields, "is_set_literal", False)),
        )
    if kind == "Attr":
        return _pa.Attr(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "obj")),
            (fields[4] if positional else _py_ast_wire_field(fields, "name", "")),
        )
    if kind == "Subscript":
        return _pa.Subscript(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "obj")),
            (fields[4] if positional else _py_ast_wire_field(fields, "idx")),
        )
    if kind == "Slice":
        return _pa.Slice(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "lo")),
            (fields[4] if positional else _py_ast_wire_field(fields, "hi")),
            (fields[5] if positional else _py_ast_wire_field(fields, "step")),
        )
    if kind == "ListExpr":
        return _pa.ListExpr(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "elems", ())),
        )
    if kind == "DictExpr":
        return _pa.DictExpr(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "pairs", ())),
        )
    if kind == "TupleExpr":
        return _pa.TupleExpr(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "elems", ())),
        )
    if kind == "IfExpr":
        return _pa.IfExpr(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "cond")),
            (fields[4] if positional else _py_ast_wire_field(fields, "then_e")),
            (fields[5] if positional else _py_ast_wire_field(fields, "else_e")),
        )
    if kind == "Lambda":
        return _pa.Lambda(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "ty")),
            (fields[3] if positional else _py_ast_wire_field(fields, "params", ())),
            (fields[4] if positional else _py_ast_wire_field(fields, "body")),
        )
    if kind == "Assign":
        return _pa.Assign(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "targets", ())),
            (fields[3] if positional else _py_ast_wire_field(fields, "value")),
            (fields[4] if positional else _py_ast_wire_field(fields, "annotation")),
            # Older wire nodes represented every Assign as a value store.
            # Do not guess declaration-only syntax from a NoneLit value.
            (fields[5] if positional else _py_ast_wire_field(fields, "has_value", True)),
        )
    if kind == "AugAssign":
        return _pa.AugAssign(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "target")),
            (fields[3] if positional else _py_ast_wire_field(fields, "op", "")),
            (fields[4] if positional else _py_ast_wire_field(fields, "value")),
        )
    if kind == "ExprStmt":
        return _pa.ExprStmt(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "expr")),
        )
    if kind == "If":
        return _pa.If(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "cond")),
            (fields[3] if positional else _py_ast_wire_field(fields, "body", ())),
            (fields[4] if positional else _py_ast_wire_field(fields, "else_body", ())),
        )
    if kind == "While":
        return _pa.While(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "cond")),
            (fields[3] if positional else _py_ast_wire_field(fields, "body", ())),
            (fields[4] if positional else _py_ast_wire_field(fields, "else_body", ())),
        )
    if kind == "For":
        return _pa.For(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "target")),
            (fields[3] if positional else _py_ast_wire_field(fields, "iter")),
            (fields[4] if positional else _py_ast_wire_field(fields, "body", ())),
            (fields[5] if positional else _py_ast_wire_field(fields, "else_body", ())),
            (fields[6] if positional else _py_ast_wire_field(fields, "is_async", False)),
        )
    if kind == "Return":
        return _pa.Return(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "value")),
        )
    if kind == "Pass":
        return _pa.Pass((fields[1] if positional else _py_ast_wire_field(fields, "span")))
    if kind == "Break":
        return _pa.Break((fields[1] if positional else _py_ast_wire_field(fields, "span")))
    if kind == "Continue":
        return _pa.Continue((fields[1] if positional else _py_ast_wire_field(fields, "span")))
    if kind == "Raise":
        return _pa.Raise(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "exc")),
            (fields[3] if positional else _py_ast_wire_field(fields, "cause")),
        )
    if kind == "Try":
        return _pa.Try(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "body", ())),
            (fields[3] if positional else _py_ast_wire_field(fields, "handlers", ())),
            (fields[4] if positional else _py_ast_wire_field(fields, "else_body", ())),
            (fields[5] if positional else _py_ast_wire_field(fields, "finally_body", ())),
        )
    if kind == "ExceptHandler":
        return _pa.ExceptHandler(
            (fields[1] if positional else _py_ast_wire_field(fields, "exc_type")),
            (fields[2] if positional else _py_ast_wire_field(fields, "name")),
            (fields[3] if positional else _py_ast_wire_field(fields, "body", ())),
            (fields[4] if positional else _py_ast_wire_field(fields, "span")),
        )
    if kind == "With":
        return _pa.With(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "items", ())),
            (fields[3] if positional else _py_ast_wire_field(fields, "body", ())),
            (fields[4] if positional else _py_ast_wire_field(fields, "is_async", False)),
        )
    if kind == "Import":
        return _pa.Import(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "names", ())),
        )
    if kind == "ImportFrom":
        return _pa.ImportFrom(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "module", "")),
            (fields[3] if positional else _py_ast_wire_field(fields, "names", ())),
            (fields[4] if positional else _py_ast_wire_field(fields, "level", 0)),
        )
    if kind == "Global":
        return _pa.Global(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "names", ())),
        )
    if kind == "Nonlocal":
        return _pa.Nonlocal(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "names", ())),
        )
    if kind == "Delete":
        return _pa.Delete(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "targets", ())),
        )
    if kind == "Arg":
        return _pa.Arg(
            (fields[1] if positional else _py_ast_wire_field(fields, "name", "")),
            (fields[2] if positional else _py_ast_wire_field(fields, "annotation")),
            (fields[3] if positional else _py_ast_wire_field(fields, "default")),
            (fields[4] if positional else _py_ast_wire_field(fields, "kind", "pos")),
            (fields[5] if positional else _py_ast_wire_field(fields, "has_default", False)),
        )
    if kind == "FuncDef":
        return _pa.FuncDef(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "name", "")),
            (fields[3] if positional else _py_ast_wire_field(fields, "args", ())),
            (fields[4] if positional else _py_ast_wire_field(fields, "return_ty")),
            (fields[5] if positional else _py_ast_wire_field(fields, "body", ())),
            (fields[6] if positional else _py_ast_wire_field(fields, "decorators", ())),
            (fields[7] if positional else _py_ast_wire_field(fields, "is_method", False)),
            (fields[8] if positional else _py_ast_wire_field(fields, "is_async", False)),
            (fields[9] if positional else _py_ast_wire_field(fields, "has_return_annotation", False)),
            (fields[10] if positional else _py_ast_wire_field(fields, "manual_pointer_abi", False)),
        )
    if kind == "ClassDef":
        return _pa.ClassDef(
            (fields[1] if positional else _py_ast_wire_field(fields, "span")),
            (fields[2] if positional else _py_ast_wire_field(fields, "name", "")),
            (fields[3] if positional else _py_ast_wire_field(fields, "bases", ())),
            (fields[4] if positional else _py_ast_wire_field(fields, "keywords", ())),
            (fields[5] if positional else _py_ast_wire_field(fields, "body", ())),
            (fields[6] if positional else _py_ast_wire_field(fields, "decorators", ())),
        )
    if kind == "Module":
        return _pa.Module(
            (fields[1] if positional else _py_ast_wire_field(fields, "name", "")),
            (fields[2] if positional else _py_ast_wire_field(fields, "body", ())),
            (fields[3] if positional else _py_ast_wire_field(fields, "docstring")),
        )
    raise PyPipelineError("unknown py_ast wire node kind " + kind)


def _py_ast_to_wire_v2(value, files: list, file_ids: dict):
    """Compact positional sidecar wire; never elide an AST field."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"b": _py_ast_bytes_to_wire(value)[_PY_AST_WIRE_BYTES_KEY]}
    kind = _closed_world_node_kind(value)
    if kind == "bytes":
        return {"b": _py_ast_bytes_to_wire(value)[_PY_AST_WIRE_BYTES_KEY]}
    if isinstance(value, (tuple, list)):
        return [_py_ast_to_wire_v2(item, files, file_ids) for item in value]
    if isinstance(value, dict):
        # Explicitly tag opaque maps so even keys named n/s/b/d cannot
        # collide with the wire's record tags.
        out = {}
        for key, item in value.items():
            out[str(key)] = _py_ast_to_wire_v2(item, files, file_ids)
        return {"d": out}
    field_names = _PY_AST_FIELD_NAME_OVERRIDES.get(kind)
    if field_names is None:
        raise PyPipelineError("cannot serialize py_ast node kind " + kind)
    if kind == "SourceSpan":
        file = _py_ast_field_value(value, "file", None)
        if not isinstance(file, str):
            raise PyPipelineError("invalid positional py_ast source file")
        if file not in file_ids:
            file_ids[file] = len(files)
            files.append(file)
        row = [file_ids[file]]
        for field_name in ("line", "col", "end_line", "end_col"):
            position = _py_ast_field_value(value, field_name, None)
            if not isinstance(position, int) or isinstance(position, bool):
                raise PyPipelineError("invalid positional py_ast source position")
            row.append(position)
        return {"s": row}
    row = [kind]
    for field_name in field_names:
        row.append(_py_ast_to_wire_v2(
            _py_ast_field_value(value, field_name, None), files, file_ids,
        ))
    return {"n": row}


# Exact version-pinned row layout, encoded as one static string literal.
# Each name is length-prefixed and each row carries its field count. Unlike a
# module-scope dictionary copy, this adds no dynamic startup traversal or
# container initialization. A schema change must use the named/default reader
# until the wire version and this positional constructor contract agree.
_PY_AST_V2_CANONICAL_LAYOUT = (
    "10:SourceSpan5:4:file4:line3:col8:end_line7:end_col"
    "4:Type1:4:name"
    "14:RawPointerType1:4:name"
    "7:IntType3:4:name5:width6:signed"
    "9:FloatType2:4:name5:width"
    "11:ComplexType1:4:name"
    "8:BoolType1:4:name"
    "8:NoneType1:4:name"
    "7:StrType1:4:name"
    "9:BytesType1:4:name"
    "13:ByteArrayType1:4:name"
    "14:MemoryViewType1:4:name"
    "8:ListType2:4:name4:elem"
    "7:SetType2:4:name4:elem"
    "14:ValueArrayType3:4:name4:elem6:length"
    "8:DictType3:4:name3:key5:value"
    "9:TupleType2:4:name5:elems"
    "8:FuncType3:4:name6:params3:ret"
    "9:ClassType6:4:name6:module6:fields5:bases10:properties10:valueclass"
    "14:ValueClassType8:4:name6:module6:fields5:bases10:properties10:valueclass9:flattened15:nullable_fields"
    "7:DynType1:4:name"
    "4:Expr2:4:span2:ty"
    "6:IntLit3:4:span2:ty5:value"
    "8:FloatLit3:4:span2:ty5:value"
    "10:ComplexLit4:4:span2:ty4:real4:imag"
    "7:BoolLit3:4:span2:ty5:value"
    "7:NoneLit2:4:span2:ty"
    "6:StrLit3:4:span2:ty5:value"
    "8:BytesLit3:4:span2:ty5:value"
    "4:Name3:4:span2:ty5:ident"
    "5:BinOp5:4:span2:ty2:op3:lhs3:rhs"
    "7:UnaryOp4:4:span2:ty2:op7:operand"
    "7:Compare5:4:span2:ty2:op3:lhs3:rhs"
    "8:BoolExpr5:4:span2:ty2:op4:left5:right"
    "4:Call7:4:span2:ty4:func4:args6:kwargs13:operand_order14:is_set_literal"
    "4:Attr4:4:span2:ty3:obj4:name"
    "9:Subscript4:4:span2:ty3:obj3:idx"
    "5:Slice5:4:span2:ty2:lo2:hi4:step"
    "8:ListExpr3:4:span2:ty5:elems"
    "8:DictExpr3:4:span2:ty5:pairs"
    "9:TupleExpr3:4:span2:ty5:elems"
    "6:IfExpr5:4:span2:ty4:cond6:then_e6:else_e"
    "6:Lambda4:4:span2:ty6:params4:body"
    "4:Stmt1:4:span"
    "6:Assign5:4:span7:targets5:value10:annotation9:has_value"
    "9:AugAssign4:4:span6:target2:op5:value"
    "8:ExprStmt2:4:span4:expr"
    "2:If4:4:span4:cond4:body9:else_body"
    "5:While4:4:span4:cond4:body9:else_body"
    "3:For6:4:span6:target4:iter4:body9:else_body8:is_async"
    "6:Return2:4:span5:value"
    "4:Pass1:4:span"
    "5:Break1:4:span"
    "8:Continue1:4:span"
    "5:Raise3:4:span3:exc5:cause"
    "3:Try5:4:span4:body8:handlers9:else_body12:finally_body"
    "13:ExceptHandler4:8:exc_type4:name4:body4:span"
    "4:With4:4:span5:items4:body8:is_async"
    "6:Import2:4:span5:names"
    "10:ImportFrom4:4:span6:module5:names5:level"
    "6:Global2:4:span5:names"
    "8:Nonlocal2:4:span5:names"
    "6:Delete2:4:span7:targets"
    "3:Arg5:4:name10:annotation7:default4:kind11:has_default"
    "7:FuncDef10:4:span4:name4:args9:return_ty4:body10:decorators9:is_method8:is_async21:has_return_annotation18:manual_pointer_abi"
    "8:ClassDef6:4:span4:name5:bases8:keywords4:body10:decorators"
    "6:Module3:4:name4:body9:docstring"
)


def _py_ast_v2_layout_is_canonical() -> bool:
    schema = _PY_AST_FIELD_NAME_OVERRIDES
    if type(schema) is not dict or len(schema) != 67:
        return False
    parts = []
    for kind, fields in schema.items():
        if type(kind) is not str or type(fields) is not tuple:
            return False
        parts.append(str(len(kind)) + ":" + kind + str(len(fields)) + ":")
        for field in fields:
            if type(field) is not str:
                return False
            parts.append(str(len(field)) + ":" + field)
    return "".join(parts) == _PY_AST_V2_CANONICAL_LAYOUT


def _py_ast_v2_field_positions() -> dict:
    positions = {}
    for kind, field_names in _PY_AST_FIELD_NAME_OVERRIDES.items():
        row = {}
        for index, name in enumerate(field_names):
            row[name] = index + 1
        positions[kind] = row
    return positions


def _py_ast_from_wire_v2(value, files: list, positions: dict, positional: bool = False):
    if isinstance(value, dict):
        if len(value) != 1:
            raise PyPipelineError("invalid positional py_ast wire tag")
        if "n" in value:
            row = value["n"]
            if not isinstance(row, list) or not row or not isinstance(row[0], str):
                raise PyPipelineError("invalid positional py_ast wire node")
            kind = row[0]
            if kind not in positions:
                raise PyPipelineError("unknown py_ast wire node kind " + kind)
            if kind == "SourceSpan":
                raise PyPipelineError("invalid positional py_ast source span tag")
            field_positions = positions[kind]
            if len(row) != len(field_positions) + 1:
                raise PyPipelineError("invalid positional py_ast wire field count")
            # Validate every encoded field, including any field that an older
            # constructor normalizes or ignores. This is not a partial decode.
            decoded = [kind]
            for index in range(1, len(row)):
                decoded.append(_py_ast_from_wire_v2(row[index], files, positions, positional))
            if positional:
                return _py_ast_node_from_wire(kind, decoded, True)
            return _py_ast_node_from_wire(kind, _PyAstV2Fields(decoded, field_positions))
        if "s" in value:
            from pcc.frontends.python import py_ast as _pa

            row = value["s"]
            if not isinstance(row, list) or len(row) != 5:
                raise PyPipelineError("invalid positional py_ast source span")
            file_id = row[0]
            if (
                not isinstance(file_id, int) or isinstance(file_id, bool)
                or file_id < 0 or file_id >= len(files)
            ):
                raise PyPipelineError("invalid positional py_ast source file id")
            for index in range(1, 5):
                position = row[index]
                if not isinstance(position, int) or isinstance(position, bool):
                    raise PyPipelineError("invalid positional py_ast source position")
            return _pa.SourceSpan(files[file_id], row[1], row[2], row[3], row[4])
        if "b" in value:
            row = value["b"]
            if not isinstance(row, list):
                raise PyPipelineError("invalid positional py_ast bytes")
            for item in row:
                if (not isinstance(item, int) or isinstance(item, bool)
                        or item < 0 or item > 255):
                    raise PyPipelineError("invalid positional py_ast byte")
            return _py_ast_wire_bytes(row)
        if "d" in value:
            mapping = value["d"]
            if not isinstance(mapping, dict):
                raise PyPipelineError("invalid positional py_ast opaque map")
            out = {}
            for key, item in mapping.items():
                out[str(key)] = _py_ast_from_wire_v2(item, files, positions, positional)
            return out
        raise PyPipelineError("unknown positional py_ast wire tag")
    if isinstance(value, list):
        return tuple(_py_ast_from_wire_v2(item, files, positions, positional) for item in value)
    return value


def _py_ast_v2_payload(ast_mod) -> dict:
    files: list = []
    file_ids: dict = {}
    module = _py_ast_to_wire_v2(ast_mod, files, file_ids)
    return {"schema": _PY_AST_WIRE_SCHEMA_V2, "files": files, "module": module}


def _decode_py_ast_v2_payload(payload):
    if len(payload) != 3 or "files" not in payload or "module" not in payload:
        raise PyPipelineError("invalid positional py_ast wire payload")
    files = payload["files"]
    if not isinstance(files, list):
        raise PyPipelineError("invalid positional py_ast source file table")
    seen = {}
    for file in files:
        if not isinstance(file, str) or file in seen:
            raise PyPipelineError("invalid positional py_ast source file table")
        seen[file] = True
    return _py_ast_from_wire_v2(
        payload["module"], files, _py_ast_v2_field_positions(),
        _py_ast_v2_layout_is_canonical(),
    )


def _write_py_ast_wire_v2(path: str, ast_mod) -> None:
    payload = _py_ast_v2_payload(ast_mod)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload))


def _write_py_ast_wire(path: str, ast_mod) -> None:
    payload = {"schema": _PY_AST_WIRE_SCHEMA, "module": _py_ast_to_wire(ast_mod)}
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload))


def _read_py_ast_wire(path: str):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return _decode_py_ast_wire(text, path)


def _owned_ast_tape_u32(tape: bytes, offset: int) -> int:
    # Borrow pointer only for this single direct load; owner remains rooted,
    # no borrowed raw pointer spans a call or allocation.
    p = _owned_ast_tape_data(tape)
    return load_i32(p, offset) & 4294967295


def _owned_ast_tape_record(tape: bytes, index: int) -> int:
    # Immutable bytes payload starts at an 8-byte-aligned offset; the record
    # address is 32+8*index. Borrow is consumed by this single load before
    # any other call/safepoint. Valid caps keep packed words below 2**56.
    p = _owned_ast_tape_data(tape)
    return load_i64(p, 32 + index*8)


def _owned_ast_tape_primitive(text: str, packed: int, kind: int, length: int):
    start = packed & 536870911
    end = packed >> 32
    if packed < 0 or ((packed >> 29) & 7) != kind or start >= end or end > length:
        return None
    return _owned_ast_tape_token(text, start, end, kind)


def _owned_ast_tape_walk(text: str, tape: bytes, i: int, limit: int, length: int,
         file_count: int, depth: int) -> int:
    if i < 0 or i >= limit or depth >= 64:
        return -1
    packed = _owned_ast_tape_record(tape,i)
    kind = (packed >> 29) & 7
    start = packed & 536870911
    end = packed >> 32
    if packed < 0 or start >= length:
        return -1
    if kind >= 3:
        if kind > 5 or start >= end or end > length:
            return -1
        return i+1
    if end <= i or end > limit:
        return -1
    j = i+1
    if kind == 2:
        if end - j != 5:
            return -1
        file_id = _owned_ast_tape_primitive(text,_owned_ast_tape_record(tape,j),4,length)
        if not isinstance(file_id,int) or isinstance(file_id,bool):
            return -1
        if file_id < 0 or file_id >= file_count:
            return -1
        j += 1
        while j < end:
            v = _owned_ast_tape_record(tape,j)
            lo = v & 536870911
            hi = v >> 32
            if v < 0 or ((v >> 29) & 7) != 4 or lo >= hi or hi > length:
                return -1
            j += 1
        return end
    expected = -1
    if kind == 1:
        if j >= end:
            return -1
        name = _owned_ast_tape_primitive(text,_owned_ast_tape_record(tape,j),3,length)
        if not isinstance(name,str) or name == "SourceSpan":
            return -1
        if name not in _PY_AST_FIELD_NAME_OVERRIDES:
            return -1
        expected = len(_PY_AST_FIELD_NAME_OVERRIDES[name])
        j += 1
    fields = 0
    while j < end:
        next_index = _owned_ast_tape_walk(text,tape,j,end,length,file_count,depth+1)
        if next_index <= j or next_index > end:
            return -1
        j = next_index
        fields += 1
    if j != end or (expected >= 0 and fields != expected):
        return -1
    return end


def _owned_ast_tape_validated_files(text: str, tape):
    if type(tape) is not bytes or len(tape) < 32:
        return None
    length = _owned_ast_tape_text_len(text)
    if length < 0 or length > 16777216:
        return None
    if _owned_ast_tape_u32(tape,0)!=844383056 or _owned_ast_tape_u32(tape,4)!=1 or _owned_ast_tape_u32(tape,8)!=length:
        return None
    count = _owned_ast_tape_u32(tape,12)
    file_root = _owned_ast_tape_u32(tape,16)
    module_root = _owned_ast_tape_u32(tape,20)
    if count <= 1 or count > 4194300 or len(tape) != 32+8*count:
        return None
    if file_root != 0 or module_root <= 0 or module_root >= count:
        return None
    if _owned_ast_tape_u32(tape,24)!=0 or _owned_ast_tape_u32(tape,28)!=0:
        return None
    a = _owned_ast_tape_record(tape,file_root)
    if a < 0 or ((a >> 29) & 7) != 0 or (a & 536870911)>=length or (a >> 32)!=module_root:
        return None
    files = []
    seen = set()
    for j in range(1,module_root):
        filename = _owned_ast_tape_primitive(text,_owned_ast_tape_record(tape,j),3,length)
        if not isinstance(filename,str) or filename in seen:
            return None
        seen.add(filename)
        files.append(filename)
    if _owned_ast_tape_walk(text,tape,module_root,count,length,len(files),1)!=count:
        return None
    return files


def _owned_ast_tape_checked_leaf(text: str, packed: int, kind: int, length: int):
    value = _owned_ast_tape_primitive(text,packed,kind,length)
    if kind == 3:
        if not isinstance(value,str):
            raise PyPipelineError("invalid validated compact AST string")
    elif kind == 4:
        if not isinstance(value,int) or isinstance(value,bool):
            raise PyPipelineError("invalid validated compact AST integer")
    elif kind == 5:
        if value is not None and not isinstance(value,bool):
            raise PyPipelineError("invalid validated compact AST literal")
    else:
        raise PyPipelineError("invalid validated compact AST _owned_ast_tape_primitive")
    return value


def _owned_ast_tape_materialize(text: str, tape: bytes, index: int, packed: int, length: int, files: list, positions: dict, positional: bool = False):
    kind = (packed >> 29) & 7
    if kind >= 3:
        return _owned_ast_tape_checked_leaf(text,packed,kind,length)
    end = packed >> 32
    j = index+1
    if kind == 2:
        file_id = _owned_ast_tape_checked_leaf(text,_owned_ast_tape_record(tape,j),4,length)
        line = _owned_ast_tape_checked_leaf(text,_owned_ast_tape_record(tape,j+1),4,length)
        col = _owned_ast_tape_checked_leaf(text,_owned_ast_tape_record(tape,j+2),4,length)
        end_line = _owned_ast_tape_checked_leaf(text,_owned_ast_tape_record(tape,j+3),4,length)
        end_col = _owned_ast_tape_checked_leaf(text,_owned_ast_tape_record(tape,j+4),4,length)
        return py_ast.SourceSpan(files[file_id],line,col,end_line,end_col)
    if kind == 1:
        name = _owned_ast_tape_checked_leaf(text,_owned_ast_tape_record(tape,j),3,length)
        j += 1
        # Exact canonical contract and arity were checked by the full
        # preflight. Retain original child order and constructor arguments.
        if positional and (name == "IntLit" or name == "StrLit" or name == "IntType"):
            child = _owned_ast_tape_record(tape,j)
            first = _owned_ast_tape_materialize(text,tape,j,child,length,files,positions,positional)
            j = (child >> 32) if ((child >> 29) & 7) < 3 else j+1
            child = _owned_ast_tape_record(tape,j)
            second = _owned_ast_tape_materialize(text,tape,j,child,length,files,positions,positional)
            j = (child >> 32) if ((child >> 29) & 7) < 3 else j+1
            child = _owned_ast_tape_record(tape,j)
            third = _owned_ast_tape_materialize(text,tape,j,child,length,files,positions,positional)
            if name == "IntLit":
                return py_ast.IntLit(first,second,third)
            if name == "StrLit":
                return py_ast.StrLit(first,second,third)
            return py_ast.IntType(first,second,third)
        if positional and (name == "StrType" or name == "DynType"):
            value = _owned_ast_tape_materialize(text,tape,j,_owned_ast_tape_record(tape,j),length,files,positions,positional)
            if name == "StrType":
                return py_ast.StrType(value)
            return py_ast.DynType(value)
        fields = [name]
        while j < end:
            child = _owned_ast_tape_record(tape,j)
            fields.append(_owned_ast_tape_materialize(text,tape,j,child,length,files,positions,positional))
            j = (child >> 32) if ((child >> 29) & 7) < 3 else j+1
        if positional:
            return _py_ast_node_from_wire(name, fields, True)
        return _py_ast_node_from_wire(name,_PyAstV2Fields(fields,positions[name]))
    if kind != 0:
        raise PyPipelineError("invalid validated compact AST container")
    values = []
    while j < end:
        child = _owned_ast_tape_record(tape,j)
        values.append(_owned_ast_tape_materialize(text,tape,j,child,length,files,positions,positional))
        j = (child >> 32) if ((child >> 29) & 7) < 3 else j+1
    return tuple(values)


def _decode_py_ast_wire(text: str, path: str = ""):
    """Decode an AST snapshot without reopening an identity-checked file."""

    if sys.implementation.name == "pcc" and type(text) is str:
        tape = _owned_ast_tape_scan(text)
        if tape is not None:
            files = _owned_ast_tape_validated_files(text, tape)
            if files is not None:
                # No fallback below this boundary. Original constructors own
                # all faults, normalization and child order. The source,
                # immutable tape and file table remain rooted.
                positions = _py_ast_v2_field_positions()
                root = _owned_ast_tape_u32(tape, 20)
                return _owned_ast_tape_materialize(
                    text, tape, root, _owned_ast_tape_record(tape, root),
                    _owned_ast_tape_text_len(text), files, positions,
                    _py_ast_v2_layout_is_canonical(),
                )
            # Release unsupported tape before allocating the old raw graph.
            tape = None
    return _decode_py_ast_wire_legacy(text, path)


def _decode_py_ast_wire_legacy(text: str, path: str = ""):
    """Original owned JSON reader and public-provider fallback."""

    payload = None
    if sys.implementation.name == "pcc" and type(text) is str:
        payload = _try_owned_ast_json(text)
    private = payload is not None
    if not private:
        payload = json.loads(text)
    schema = payload.get("schema") if isinstance(payload, dict) else None
    if schema == _PY_AST_WIRE_SCHEMA_V2:
        if private:
            _maybe_trim_owned_ast_raw_highwater()
        return _decode_py_ast_v2_payload(payload)
    if schema != _PY_AST_WIRE_SCHEMA:
        raise PyPipelineError(
            "invalid frontend py_ast wire file " + path + " schema=" + str(schema)
        )
    if private:
        _maybe_trim_owned_ast_raw_highwater()
    return _py_ast_from_wire(payload.get("module"))
