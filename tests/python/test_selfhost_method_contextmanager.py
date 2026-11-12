"""Code in the self-host closure must not decorate methods with @contextmanager.

pcc lowers ``with f():`` statically when ``f`` is a module-level
``@contextmanager`` generator, but a decorated *method* is applied at class
definition by calling ``contextlib.contextmanager``, and pcc's
``pcc/stdlib/contextlib.py`` raises NotImplementedError there. A native pcc1
therefore failed the first time it initialized such a class: two methods of
CUnevaluatedTypeMixin made every Stage2 codegen worker fail as soon as one
scaffold check imported the C code generator.

The same two scopes are now explicit context classes in c_scope_context.py;
their behavior is checked here against the C generator's real scope context.
"""

from __future__ import annotations

import ast
import os
import sys
from collections import ChainMap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _closure():
    sys.path.insert(0, str(ROOT / "scripts"))
    from scripts.probe_stage1_closure import _tightened_closure

    return _tightened_closure(str(ROOT / "pcc" / "__main__.py"))


def test_no_method_in_the_self_host_closure_is_a_contextmanager():
    srcs, mods = _closure()
    assert "pcc.frontends.c.codegen.c_unevaluated_types" in mods
    found = []
    for src, mod in zip(srcs, mods):
        tree = ast.parse(Path(src).read_text(encoding="utf-8"))
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            for item in cls.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for dec in item.decorator_list:
                    if ast.unparse(dec) in ("contextmanager", "contextlib.contextmanager"):
                        found.append(f"{mod}:{item.lineno} {cls.name}.{item.name}")
    assert found == []


class _Host:
    """The state the two scopes read and restore on a C code generator."""

    def __init__(self):
        from pcc.frontends.c.codegen.c_scope_context import NewScopeContext

        self._new_scope = NewScopeContext
        self.builder = "builder"
        self.env = ChainMap({"__typedef_T": "outer"})
        self._decl_ast_types = ChainMap()
        self._typedef_ast_types = ChainMap({"T": "outer-alias"})
        self._scope_id_counter = 7
        self._current_scope_id = 7

    def new_scope(self):
        return self._new_scope(self)


def _mixin():
    from pcc.frontends.c.codegen.c_unevaluated_types import CUnevaluatedTypeMixin

    return CUnevaluatedTypeMixin


def test_unevaluated_scope_clears_the_builder_and_restores_everything():
    host = _Host()
    scope = _mixin()._unevaluated_declaration_scope
    with scope(host):
        assert host.builder is None
        assert host._unevaluated_binding_depth == 1
        assert host._scope_id_counter == 8
        host.env["inner"] = 1
        with scope(host):
            assert host._unevaluated_binding_depth == 2
        assert host._unevaluated_binding_depth == 1
        assert host.builder is None
    assert host.builder == "builder"
    assert host._unevaluated_binding_depth == 0
    assert host._scope_id_counter == 7
    assert host._current_scope_id == 7
    assert "inner" not in host.env


def test_unevaluated_scope_restores_state_when_the_body_raises():
    host = _Host()
    with pytest.raises(KeyError):
        with _mixin()._unevaluated_declaration_scope(host):
            host.env["inner"] = 1
            raise KeyError("x")
    assert host.builder == "builder"
    assert host._unevaluated_binding_depth == 0
    assert host._scope_id_counter == 7
    assert "inner" not in host.env


def test_typedef_definition_scope_resolves_the_declaring_scope_and_restores():
    host = _Host()
    host.env = host.env.new_child({"__typedef_T": "shadow"}).new_child({"x": 1})
    host._typedef_ast_types = host._typedef_ast_types.new_child({"T": "shadow-alias"}).new_child()
    env, aliases = host.env, host._typedef_ast_types
    with _mixin()._query_typedef_definition_scope(host, "T"):
        assert host.env["__typedef_T"] == "shadow"
        assert "x" not in host.env
        assert host._typedef_ast_types["T"] == "shadow-alias"
    assert host.env is env and host._typedef_ast_types is aliases
    with pytest.raises(ValueError):
        with _mixin()._query_typedef_definition_scope(host, "missing"):
            assert host.env["x"] == 1
            raise ValueError("y")
    assert host.env is env and host._typedef_ast_types is aliases
