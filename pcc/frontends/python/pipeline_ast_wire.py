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


def _py_ast_node_from_wire(kind: str, fields):
    from pcc.frontends.python import py_ast as _pa

    if kind == "SourceSpan":
        return _pa.SourceSpan(
            _py_ast_wire_field(fields, "file", ""),
            _py_ast_wire_field(fields, "line", 0),
            _py_ast_wire_field(fields, "col", 0),
            _py_ast_wire_field(fields, "end_line", 0),
            _py_ast_wire_field(fields, "end_col", 0),
        )
    if kind == "Type":
        return _pa.Type(_py_ast_wire_field(fields, "name", ""))
    if kind == "RawPointerType":
        return _pa.RawPointerType(_py_ast_wire_field(fields, "name", "pcc.extern.c_rawptr"))
    if kind == "IntType":
        return _pa.IntType(
            _py_ast_wire_field(fields, "name", "int"),
            _py_ast_wire_field(fields, "width", 64),
            _py_ast_wire_field(fields, "signed", True),
        )
    if kind == "FloatType":
        return _pa.FloatType(
            _py_ast_wire_field(fields, "name", "float"),
            _py_ast_wire_field(fields, "width", 64),
        )
    if kind == "ComplexType":
        return _pa.ComplexType(_py_ast_wire_field(fields, "name", "complex"))
    if kind == "BoolType":
        return _pa.BoolType(_py_ast_wire_field(fields, "name", "bool"))
    if kind == "NoneType":
        return _pa.NoneType(_py_ast_wire_field(fields, "name", "None"))
    if kind == "StrType":
        return _pa.StrType(_py_ast_wire_field(fields, "name", "str"))
    if kind == "BytesType":
        return _pa.BytesType(_py_ast_wire_field(fields, "name", "bytes"))
    if kind == "ByteArrayType":
        return _pa.ByteArrayType(_py_ast_wire_field(fields, "name", "bytearray"))
    if kind == "MemoryViewType":
        return _pa.MemoryViewType(_py_ast_wire_field(fields, "name", "memoryview"))
    if kind == "ListType":
        return _pa.ListType(
            _py_ast_wire_field(fields, "name", "list"),
            _py_ast_wire_field(fields, "elem"),
        )
    if kind == "SetType":
        return _pa.SetType(
            _py_ast_wire_field(fields, "name", "set"),
            _py_ast_wire_field(fields, "elem"),
        )
    if kind == "ValueArrayType":
        return _pa.ValueArrayType(
            _py_ast_wire_field(fields, "name", "pcc.array"),
            _py_ast_wire_field(fields, "elem"),
            _py_ast_wire_field(fields, "length", 0),
        )
    if kind == "DictType":
        return _pa.DictType(
            _py_ast_wire_field(fields, "name", "dict"),
            _py_ast_wire_field(fields, "key"),
            _py_ast_wire_field(fields, "value"),
        )
    if kind == "TupleType":
        return _pa.TupleType(
            _py_ast_wire_field(fields, "name", "tuple"),
            _py_ast_wire_field(fields, "elems", ()),
        )
    if kind == "FuncType":
        return _pa.FuncType(
            _py_ast_wire_field(fields, "name", "func"),
            _py_ast_wire_field(fields, "params", ()),
            _py_ast_wire_field(fields, "ret"),
        )
    if kind == "ClassType":
        return _pa.ClassType(
            _py_ast_wire_field(fields, "name", ""),
            _py_ast_wire_field(fields, "module", ""),
            _py_ast_wire_tuple_field(fields, "fields"),
            _py_ast_wire_tuple_field(fields, "bases"),
            _py_ast_wire_tuple_field(fields, "properties"),
            _py_ast_wire_bool_field(fields, "valueclass", False),
        )
    if kind == "ValueClassType":
        return _pa.ValueClassType(
            _py_ast_wire_field(fields, "name", ""),
            _py_ast_wire_field(fields, "module", ""),
            _py_ast_wire_tuple_field(fields, "fields"),
            _py_ast_wire_tuple_field(fields, "bases"),
            _py_ast_wire_tuple_field(fields, "properties"),
            _py_ast_wire_bool_field(fields, "valueclass", True),
            _py_ast_wire_bool_field(fields, "flattened", True),
            _py_ast_wire_bool_field(fields, "nullable_fields", False),
        )
    if kind == "DynType":
        return _pa.DynType(_py_ast_wire_field(fields, "name", "dyn"))
    if kind == "IntLit":
        return _pa.IntLit(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "value", 0),
        )
    if kind == "FloatLit":
        return _pa.FloatLit(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "value", 0.0),
        )
    if kind == "ComplexLit":
        return _pa.ComplexLit(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "real", 0.0),
            _py_ast_wire_field(fields, "imag", 0.0),
        )
    if kind == "BoolLit":
        return _pa.BoolLit(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "value", False),
        )
    if kind == "NoneLit":
        return _pa.NoneLit(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
        )
    if kind == "StrLit":
        return _pa.StrLit(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "value", ""),
        )
    if kind == "BytesLit":
        return _pa.BytesLit(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "value", b""),
        )
    if kind == "Name":
        return _pa.Name(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "ident", ""),
        )
    if kind == "BinOp":
        return _pa.BinOp(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "op", ""),
            _py_ast_wire_field(fields, "lhs"),
            _py_ast_wire_field(fields, "rhs"),
        )
    if kind == "UnaryOp":
        return _pa.UnaryOp(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "op", ""),
            _py_ast_wire_field(fields, "operand"),
        )
    if kind == "Compare":
        return _pa.Compare(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "op", ""),
            _py_ast_wire_field(fields, "lhs"),
            _py_ast_wire_field(fields, "rhs"),
        )
    if kind == "BoolExpr":
        return _pa.BoolExpr(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "op", ""),
            _py_ast_wire_field(fields, "left"),
            _py_ast_wire_field(fields, "right"),
        )
    if kind == "Call":
        return _pa.Call(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "func"),
            _py_ast_wire_field(fields, "args", ()),
            _py_ast_wire_field(fields, "kwargs", ()),
            _py_ast_wire_field(fields, "operand_order", ()),
            _py_ast_wire_bool_field(fields, "is_set_literal", False),
        )
    if kind == "Attr":
        return _pa.Attr(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "obj"),
            _py_ast_wire_field(fields, "name", ""),
        )
    if kind == "Subscript":
        return _pa.Subscript(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "obj"),
            _py_ast_wire_field(fields, "idx"),
        )
    if kind == "Slice":
        return _pa.Slice(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "lo"),
            _py_ast_wire_field(fields, "hi"),
            _py_ast_wire_field(fields, "step"),
        )
    if kind == "ListExpr":
        return _pa.ListExpr(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "elems", ()),
        )
    if kind == "DictExpr":
        return _pa.DictExpr(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "pairs", ()),
        )
    if kind == "TupleExpr":
        return _pa.TupleExpr(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "elems", ()),
        )
    if kind == "IfExpr":
        return _pa.IfExpr(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "cond"),
            _py_ast_wire_field(fields, "then_e"),
            _py_ast_wire_field(fields, "else_e"),
        )
    if kind == "Lambda":
        return _pa.Lambda(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "ty"),
            _py_ast_wire_field(fields, "params", ()),
            _py_ast_wire_field(fields, "body"),
        )
    if kind == "Assign":
        return _pa.Assign(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "targets", ()),
            _py_ast_wire_field(fields, "value"),
            _py_ast_wire_field(fields, "annotation"),
            # Older wire nodes represented every Assign as a value store.
            # Do not guess declaration-only syntax from a NoneLit value.
            _py_ast_wire_field(fields, "has_value", True),
        )
    if kind == "AugAssign":
        return _pa.AugAssign(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "target"),
            _py_ast_wire_field(fields, "op", ""),
            _py_ast_wire_field(fields, "value"),
        )
    if kind == "ExprStmt":
        return _pa.ExprStmt(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "expr"),
        )
    if kind == "If":
        return _pa.If(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "cond"),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "else_body", ()),
        )
    if kind == "While":
        return _pa.While(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "cond"),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "else_body", ()),
        )
    if kind == "For":
        return _pa.For(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "target"),
            _py_ast_wire_field(fields, "iter"),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "else_body", ()),
            _py_ast_wire_field(fields, "is_async", False),
        )
    if kind == "Return":
        return _pa.Return(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "value"),
        )
    if kind == "Pass":
        return _pa.Pass(_py_ast_wire_field(fields, "span"))
    if kind == "Break":
        return _pa.Break(_py_ast_wire_field(fields, "span"))
    if kind == "Continue":
        return _pa.Continue(_py_ast_wire_field(fields, "span"))
    if kind == "Raise":
        return _pa.Raise(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "exc"),
            _py_ast_wire_field(fields, "cause"),
        )
    if kind == "Try":
        return _pa.Try(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "handlers", ()),
            _py_ast_wire_field(fields, "else_body", ()),
            _py_ast_wire_field(fields, "finally_body", ()),
        )
    if kind == "ExceptHandler":
        return _pa.ExceptHandler(
            _py_ast_wire_field(fields, "exc_type"),
            _py_ast_wire_field(fields, "name"),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "span"),
        )
    if kind == "With":
        return _pa.With(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "items", ()),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "is_async", False),
        )
    if kind == "Import":
        return _pa.Import(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "names", ()),
        )
    if kind == "ImportFrom":
        return _pa.ImportFrom(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "module", ""),
            _py_ast_wire_field(fields, "names", ()),
            _py_ast_wire_field(fields, "level", 0),
        )
    if kind == "Global":
        return _pa.Global(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "names", ()),
        )
    if kind == "Nonlocal":
        return _pa.Nonlocal(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "names", ()),
        )
    if kind == "Delete":
        return _pa.Delete(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "targets", ()),
        )
    if kind == "Arg":
        return _pa.Arg(
            _py_ast_wire_field(fields, "name", ""),
            _py_ast_wire_field(fields, "annotation"),
            _py_ast_wire_field(fields, "default"),
            _py_ast_wire_field(fields, "kind", "pos"),
            _py_ast_wire_field(fields, "has_default", False),
        )
    if kind == "FuncDef":
        return _pa.FuncDef(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "name", ""),
            _py_ast_wire_field(fields, "args", ()),
            _py_ast_wire_field(fields, "return_ty"),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "decorators", ()),
            _py_ast_wire_field(fields, "is_method", False),
            _py_ast_wire_field(fields, "is_async", False),
            _py_ast_wire_field(fields, "has_return_annotation", False),
            _py_ast_wire_field(fields, "manual_pointer_abi", False),
        )
    if kind == "ClassDef":
        return _pa.ClassDef(
            _py_ast_wire_field(fields, "span"),
            _py_ast_wire_field(fields, "name", ""),
            _py_ast_wire_field(fields, "bases", ()),
            _py_ast_wire_field(fields, "keywords", ()),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "decorators", ()),
        )
    if kind == "Module":
        return _pa.Module(
            _py_ast_wire_field(fields, "name", ""),
            _py_ast_wire_field(fields, "body", ()),
            _py_ast_wire_field(fields, "docstring"),
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


def _py_ast_v2_field_positions() -> dict:
    positions = {}
    for kind, field_names in _PY_AST_FIELD_NAME_OVERRIDES.items():
        row = {}
        for index, name in enumerate(field_names):
            row[name] = index + 1
        positions[kind] = row
    return positions


def _py_ast_from_wire_v2(value, files: list, positions: dict):
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
                decoded.append(_py_ast_from_wire_v2(row[index], files, positions))
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
                out[str(key)] = _py_ast_from_wire_v2(item, files, positions)
            return out
        raise PyPipelineError("unknown positional py_ast wire tag")
    if isinstance(value, list):
        return tuple(_py_ast_from_wire_v2(item, files, positions) for item in value)
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
    return _py_ast_from_wire_v2(payload["module"], files, _py_ast_v2_field_positions())


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


def _owned_ast_tape_materialize(text: str, tape: bytes, index: int, packed: int, length: int, files: list, positions: dict):
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
        if name == "IntLit" or name == "StrLit" or name == "IntType":
            child = _owned_ast_tape_record(tape,j)
            first = _owned_ast_tape_materialize(text,tape,j,child,length,files,positions)
            j = (child >> 32) if ((child >> 29) & 7) < 3 else j+1
            child = _owned_ast_tape_record(tape,j)
            second = _owned_ast_tape_materialize(text,tape,j,child,length,files,positions)
            j = (child >> 32) if ((child >> 29) & 7) < 3 else j+1
            child = _owned_ast_tape_record(tape,j)
            third = _owned_ast_tape_materialize(text,tape,j,child,length,files,positions)
            if name == "IntLit":
                return py_ast.IntLit(first,second,third)
            if name == "StrLit":
                return py_ast.StrLit(first,second,third)
            return py_ast.IntType(first,second,third)
        if name == "StrType" or name == "DynType":
            value = _owned_ast_tape_materialize(text,tape,j,_owned_ast_tape_record(tape,j),length,files,positions)
            if name == "StrType":
                return py_ast.StrType(value)
            return py_ast.DynType(value)
        fields = [name]
        while j < end:
            child = _owned_ast_tape_record(tape,j)
            fields.append(_owned_ast_tape_materialize(text,tape,j,child,length,files,positions))
            j = (child >> 32) if ((child >> 29) & 7) < 3 else j+1
        return _py_ast_node_from_wire(name,_PyAstV2Fields(fields,positions[name]))
    if kind != 0:
        raise PyPipelineError("invalid validated compact AST container")
    values = []
    while j < end:
        child = _owned_ast_tape_record(tape,j)
        values.append(_owned_ast_tape_materialize(text,tape,j,child,length,files,positions))
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
