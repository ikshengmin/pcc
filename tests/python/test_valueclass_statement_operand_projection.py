"""Canonical operand projection keeps the legacy statement walk contract."""

import builtins
import dataclasses

import pytest

from pcc.frontends.python import py_ast as ast
from pcc.frontends.python.codegen import type_abi_lowering as owner


FIELDS = (
    "value", "expr", "lhs", "rhs", "left", "right", "operand", "cond",
    "then_e", "else_e", "obj", "idx", "lo", "hi", "step", "func", "args",
    "kwargs", "elems", "pairs", "targets", "target", "iter", "exc", "cause",
    "items", "default", "decorators",
)


class Walk:
    _statement_uses_managed_valueclass = (
        owner.TypeAbiLoweringMixin._statement_uses_managed_valueclass
    )

    def __init__(self):
        self.seen = []
        self.managed = set()

    def _valueclass_payload_expr_type(self, node):
        self.seen.append(node)
        return "managed" if id(node) in self.managed else None

    def _valueclass_payload_pointer_field_paths(self, ty):
        assert ty == "managed"
        return ((0,),)


# Every concrete source AST shape; derive expected field intersection from
# the legacy ordering rather than duplicating the implementation dispatch.
SHAPES = (
    ast.Expr, ast.IntLit, ast.FloatLit, ast.ComplexLit, ast.BoolLit,
    ast.NoneLit, ast.StrLit, ast.BytesLit, ast.Name, ast.BinOp, ast.UnaryOp,
    ast.Compare, ast.BoolExpr, ast.Call, ast.Attr, ast.Subscript, ast.Slice,
    ast.ListExpr, ast.DictExpr, ast.TupleExpr, ast.IfExpr, ast.Lambda,
    ast.Stmt, ast.Assign, ast.AugAssign, ast.ExprStmt, ast.If, ast.While,
    ast.For, ast.Return, ast.Pass, ast.Break, ast.Continue, ast.Raise,
    ast.Try, ast.ExceptHandler, ast.With, ast.Import, ast.ImportFrom,
    ast.Global, ast.Nonlocal, ast.Delete, ast.Arg, ast.FuncDef, ast.ClassDef,
    ast.Module,
)


def test_projection_shape_inventory_covers_the_owned_ast():
    discovered = {
        cls
        for cls in vars(ast).values()
        if isinstance(cls, type) and cls.__module__ == ast.__name__
        and issubclass(cls, (ast.Expr, ast.Stmt))
    }
    discovered.update((ast.Arg, ast.ExceptHandler, ast.Module))
    assert discovered == set(SHAPES)


@pytest.mark.parametrize("cls", SHAPES)
def test_canonical_shape_reads_only_declared_operands_in_legacy_order(cls, monkeypatch):
    declared = {f.name for f in dataclasses.fields(cls)}
    expect = [name for name in FIELDS if name in declared]
    marker = {name: object() for name in declared}
    node = cls(**marker)
    probes = []

    def probe(obj, name, default=None):
        if obj is node:
            probes.append(name)
        return builtins.getattr(obj, name, default)

    monkeypatch.setattr(owner, "getattr", probe, raising=False)
    walk = Walk()
    assert not walk._statement_uses_managed_valueclass(node)
    assert [id(x) for x in walk.seen] == [id(node)] + [
        id(marker[name]) for name in reversed(expect)
    ]
    assert all(name in expect for name in probes)
    assert probes == [name for name in expect if name in probes]


def test_managed_verdict_is_recomputed_and_stops_in_legacy_stack_order():
    a, b, c = object(), object(), object()
    node = ast.Call(None, None, a, (b,), (("k", c),))
    walk = Walk()
    assert not walk._statement_uses_managed_valueclass(node)
    assert walk.seen == [node, c, b, a]
    walk.seen.clear()
    walk.managed.add(id(b))
    assert walk._statement_uses_managed_valueclass(node)
    assert walk.seen == [node, c, b]
    walk.seen.clear()
    walk.managed.clear()
    assert not walk._statement_uses_managed_valueclass(node)
    assert walk.seen == [node, c, b, a]


def test_missing_declared_field_keeps_default_none():
    node = object.__new__(ast.BinOp)
    rhs = object()
    object.__setattr__(node, "rhs", rhs)
    walk = Walk()
    assert not walk._statement_uses_managed_valueclass(node)
    assert [id(x) for x in walk.seen] == [id(node), id(rhs)]


def test_custom_and_subclass_keep_all_probes_and_error_precedence():
    sentinel = object()
    reads = []

    class Custom(ast.Name):
        def __getattribute__(self, name):
            reads.append(name)
            if name == "value":
                return sentinel
            if name == "rhs":
                raise RuntimeError("custom rhs")
            raise AttributeError(name)

    walk = Walk()
    node = Custom(None, None, "x")
    with pytest.raises(RuntimeError, match="custom rhs"):
        walk._statement_uses_managed_valueclass(node)
    assert [name for name in reads if name in FIELDS] == list(FIELDS[:4])
    assert [id(x) for x in walk.seen] == [id(node)]

    class Foreign:
        def __getattr__(self, name):
            reads.append(name)
            if name == "value":
                return sentinel
            raise AttributeError(name)

    reads.clear()
    walk = Walk()
    obj = Foreign()
    assert not walk._statement_uses_managed_valueclass(obj)
    assert [name for name in reads if name in FIELDS] == list(FIELDS)
    assert walk.seen == [obj, sentinel]


def test_bytes_and_untyped_literal_payload_are_still_examined():
    data = b"x"
    node = ast.BytesLit(None, None, data)
    walk = Walk()
    walk.managed.add(id(data))
    assert walk._statement_uses_managed_valueclass(node)
    assert walk.seen == [node, data]
    opaque = object()
    raw = ast.IntLit(None, None, opaque)
    walk = Walk()
    walk.managed.add(id(opaque))
    assert walk._statement_uses_managed_valueclass(raw)
    assert walk.seen == [raw, opaque]
