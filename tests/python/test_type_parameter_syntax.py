"""PEP 695/696 syntax, Python reference 8.10 and 7.14.

https://docs.python.org/3.15/reference/compound_stmts.html#type-params
https://docs.python.org/3.15/reference/simple_stmts.html#the-type-statement
The raw parser preserves annotation-scope inputs; lowering remains explicit.
"""
import ast
from dataclasses import fields
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pcc.frontends.python import py_parse as pp
from pcc.frontends.python.py_lift import LiftError, parse_and_lift


SOURCES = (
    "def f[T](value: T) -> T: return value\n",
    "async def f[T, *Ts, **P](value): return value\n",
    "@decorate\ndef f[T](value): return value\n",
    "class C[T](Base): pass\n",
    "class C[T: bound(), U: (int, str), *Ts, **P]: pass\n",
    "class C[T: pkg.Base = int, U = str,]: pass\n",
    "def f[T=int, *Ts=*tuple[int, ...], **P=[int, str]](): pass\n",
    "type A = int\n",
    "type A[T] = list[T]\n",
    "type A[T=int, *Ts=*tuple[int, ...], **P=...] = tuple[T]\n",
    "class C[*Ts, *Us]: pass\n",
    "class C[*Ts=tuple[int, ...]]: pass\n",
    "class C[**P=...]: pass\n",
)


def _shape(value):
    if value is None:
        return None
    if isinstance(value, ast.Name):
        return ("name", value.id)
    if isinstance(value, pp._Name):
        return ("constant", Ellipsis) if value.ident == "..." else ("name", value.ident)
    if isinstance(value, ast.Constant):
        return ("constant", value.value)
    if isinstance(value, ast.Attribute):
        return ("attr", _shape(value.value), value.attr)
    if isinstance(value, pp._Attr):
        return ("attr", _shape(value.obj), value.name)
    if isinstance(value, ast.Subscript):
        return ("subscript", _shape(value.value), _shape(value.slice))
    if isinstance(value, pp._Subscript):
        return ("subscript", _shape(value.obj), _shape(value.idx))
    if isinstance(value, (ast.Tuple, ast.List)):
        return ("tuple" if isinstance(value, ast.Tuple) else "list", tuple(_shape(x) for x in value.elts))
    if isinstance(value, (pp._Tuple, pp._List)):
        return ("tuple" if isinstance(value, pp._Tuple) else "list", tuple(_shape(x) for x in value.elems))
    if isinstance(value, ast.Call):
        assert not value.keywords
        return ("call", _shape(value.func), tuple(_shape(x) for x in value.args))
    if isinstance(value, pp._Call):
        return ("call", _shape(value.func), tuple(_shape(x) for x in value.args))
    if isinstance(value, (ast.Starred, pp._Starred)):
        return ("starred", _shape(value.value))
    raise AssertionError(type(value).__name__)


@pytest.mark.parametrize("source", SOURCES)
def test_type_parameter_syntax_preserves_cpython_ast_inputs(source):
    compile(source, "reference.py", "exec")
    expected = ast.parse(source).body[0]
    actual = pp.parse(source).body[0]
    assert actual.name == (expected.name.id if isinstance(expected, ast.TypeAlias) else expected.name)
    assert [param.name for param in actual.type_params] == [param.name for param in expected.type_params]
    for got, want in zip(actual.type_params, expected.type_params):
        assert got.kind == type(want).__name__
        assert _shape(got.bound) == _shape(getattr(want, "bound", None))
        assert _shape(got.default_value) == _shape(want.default_value)
    if isinstance(expected, ast.TypeAlias):
        assert _shape(actual.value) == _shape(expected.value)


@pytest.mark.parametrize("source", (
    "def f[T,T](): pass\n", "class C[**P,P]: pass\n", "class C[T,Ｔ]: pass\n",
    "type A[T,T] = int\n", "class C[*Ts:int]: pass\n", "class C[**P:int]: pass\n",
    "class C[T=int,U]: pass\n", "def f[T=int,*Ts](): pass\n",
    "type A[T=int, **P] = int\n", "type A[] = int\n", "def f[](): pass\n",
    "class C[T=*tuple[int,...]]: pass\n", "ｔｙｐｅ A = int\n",
))
def test_invalid_type_parameter_declarations_match_cpython_rejection(source):
    with pytest.raises(SyntaxError):
        compile(source, "reference.py", "exec")
    with pytest.raises(pp.ParseError):
        pp.parse(source)


def test_type_stays_a_soft_keyword_and_aliases_work_in_simple_suites():
    module = pp.parse("type = 3; type A = int\nif True: type B = str\n")
    assert isinstance(module.body[0], pp._Assign)
    assert isinstance(module.body[1], pp._TypeAlias)
    assert isinstance(module.body[2].body[0], pp._TypeAlias)


@pytest.mark.parametrize("source,code", (
    ("def f[T](value): return value\n", "PCC-PY-TYPE-PARAMS-UNIMPLEMENTED"),
    ("class C[T]: pass\n", "PCC-PY-TYPE-PARAMS-UNIMPLEMENTED"),
    ("type A = int\n", "PCC-PY-TYPE-ALIAS-UNIMPLEMENTED"),
))
def test_lowering_does_not_silently_erase_annotation_scopes(source, code):
    with pytest.raises(LiftError, match=code):
        parse_and_lift(source, "source.py", "source")


def test_ordinary_declarations_still_lift():
    module = parse_and_lift("def f(value): return value\nclass C: pass\n", "source.py", "source")
    assert len(module.body) == 2


@pytest.mark.parametrize("name", ("_FuncDef", "_ClassDef", "_TypeAlias", "_TypeParam", "Token"))
def test_standalone_native_field_exports_match_parser_records(name):
    from pcc.frontends.python import py_lex
    from pcc.frontends.python.codegen.layer1_support import _default_native_module_exports

    owner = py_lex if name == "Token" else pp
    expected = tuple(field.name for field in fields(getattr(owner, name)))
    exported = _default_native_module_exports("pcc.frontends.python.py_lift")
    assert exported[owner.__name__][name]["field_names"] == expected


PARSER_PROGRAM = '''from pcc.frontends.python.py_parse import parse
def main():
    module = parse("def f[T: int = str, *Ts = *tuple[int, ...], **P = ...](x): return x\\n")
    function = module.body[0]
    print(function.name, len(function.type_params))
    for parameter in function.type_params:
        print(parameter.name, parameter.kind, parameter.bound is None, parameter.default_value is None)
    alias = parse("type Alias[T] = list[T]\\n").body[0]
    print(alias.name, alias.type_params[0].name)
main()
'''


@pytest.mark.integration
def test_native_parser_preserves_type_parameter_records(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("PCC_PACKAGE_SITE", str(root))
    source = tmp_path / "parse_type_parameters.py"
    source.write_text(PARSER_PROGRAM, encoding="utf-8")
    reference = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=30,
        env=dict(os.environ, PYTHONPATH=str(root)),
    )
    assert reference.returncode == 0, reference.stderr
    output = tmp_path / "parse_type_parameters"
    python_program_compiler(
        str(source), str(output), backend="self", ir_scaffold_mode="on",
        runtime_archive=str(pcc_runtime_archive),
    )
    for collector in range(5):
        result = subprocess.run(
            [str(output)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(collector)),
        )
        assert (result.returncode, result.stdout, result.stderr) == (
            reference.returncode, reference.stdout, reference.stderr,
        ), collector
