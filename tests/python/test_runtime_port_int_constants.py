"""Private integer constants of runtime-port modules fold into their uses.

A runtime port (``__pcc_runtime_port__ = True``) has module state, so a
module-scope ``_NAME = 5`` or ``_NAME: i64 = 5`` is a module variable and
every use loaded it from memory: 250 loads in the thread kernel's lock and
native-frame paths, from words that shared a cache line with a container
lock stripe. A port that declares ``__pcc_fold_private_int_constants__``
has such a constant substituted into its uses and keeps the variable
itself. These tests pin which names qualify, that the declaration is
required, and that nothing else about a module changes.

Host pcc0 lift and owned-IR evidence only; no native-pcc1 or throughput claim.
"""
from __future__ import annotations

import re

import pytest

from pcc.frontends.python import py_ast as pa
from pcc.frontends.python.freestanding_constants import (
    collect_runtime_port_int_constants,
)
from pcc.frontends.python.py_lift import parse_and_lift


PLAIN_PORT = "__pcc_runtime_port__ = True\n"
PORT = PLAIN_PORT + "__pcc_fold_private_int_constants__ = True\n"


def _lift(source):
    return parse_and_lift(source, "<port>", "port")


def _function(module, name):
    return next(
        node for node in module.body
        if isinstance(node, pa.FuncDef) and node.name == name
    )


def _names(node):
    """Every Name identifier read or written under ``node``."""
    found = []

    def walk(value):
        if isinstance(value, tuple):
            for item in value:
                walk(item)
            return
        if isinstance(value, pa.Name):
            found.append(value.ident)
        if not hasattr(value, "__dataclass_fields__"):
            return
        for field in value.__dataclass_fields__:
            if field in ("span", "ty", "annotation", "return_ty"):
                continue
            walk(getattr(value, field))

    walk(node)
    return found


def _int_literals(node):
    found = []

    def walk(value):
        if isinstance(value, tuple):
            for item in value:
                walk(item)
            return
        if isinstance(value, pa.UnaryOp) and isinstance(value.operand, pa.IntLit):
            if value.op == "-":
                found.append(-value.operand.value)
                return
        if isinstance(value, pa.IntLit):
            found.append(value.value)
        if not hasattr(value, "__dataclass_fields__"):
            return
        for field in value.__dataclass_fields__:
            if field in ("span", "ty", "annotation", "return_ty"):
                continue
            walk(getattr(value, field))

    walk(node)
    return found


def _assigned(module):
    return [
        statement.targets[0].ident
        for statement in module.body
        if isinstance(statement, pa.Assign)
        and isinstance(statement.targets[0], pa.Name)
    ]


@pytest.mark.parametrize(
    "definition, value",
    [
        ("_OFFSET = 48", 48),
        ("_OFFSET: int = 48", 48),
        ("_OFFSET: i64 = 48", 48),
        ("_OFFSET = -8", -8),
        ("_OFFSET: i64 = 4503599627370496", 4503599627370496),
    ],
    ids=["plain", "annotated-int", "annotated-i64", "negative", "wide"],
)
def test_a_private_integer_constant_folds_into_every_function_that_reads_it(
    definition, value
):
    module = _lift(
        PORT + "from pcc import i64\n" + definition + "\n"
        "def first(base):\n    return base + _OFFSET\n"
        "def second(base):\n    return _OFFSET - base\n"
    )
    for name in ("first", "second"):
        function = _function(module, name)
        assert "_OFFSET" not in _names(function)
        assert _int_literals(function) == [value]
    # The module variable stays: another object may still read it.
    assert "_OFFSET" in _assigned(module)


def test_the_folded_literal_is_spelled_as_the_parser_spells_one():
    module = _lift(PORT + "_LOW = -1\n_HIGH = 7\ndef f():\n    return _LOW + _HIGH\n")
    expression = _function(module, "f").body[0].value
    assert isinstance(expression.lhs, pa.UnaryOp) and expression.lhs.op == "-"
    assert isinstance(expression.lhs.operand, pa.IntLit)
    assert isinstance(expression.lhs.operand.ty, pa.IntType)
    assert isinstance(expression.rhs, pa.IntLit)
    assert isinstance(expression.rhs.ty, pa.IntType)


@pytest.mark.parametrize(
    "source",
    [
        "LIMIT = 5\ndef f():\n    return LIMIT\n",
        "_LIMIT = 5\n_LIMIT = 6\ndef f():\n    return _LIMIT\n",
        "_LIMIT = 5\ndef bump():\n    global _LIMIT\n    _LIMIT = 6\n"
        "def f():\n    return _LIMIT\n",
        "_LIMIT = 5\n_LIMIT += 1\ndef f():\n    return _LIMIT\n",
        "_LIMIT = 5\nfor _LIMIT in (1, 2):\n    pass\ndef f():\n    return _LIMIT\n",
        "_LIMIT = 5\ndel _LIMIT\ndef f():\n    return _LIMIT\n",
        "_LIMIT = 5.0\ndef f():\n    return _LIMIT\n",
        "_LIMIT = 'five'\ndef f():\n    return _LIMIT\n",
        "_LIMIT = True\ndef f():\n    return _LIMIT\n",
        "_LIMIT: float = 5\ndef f():\n    return _LIMIT\n",
        "_BASE = 4\n_LIMIT = _BASE + 1\ndef f():\n    return _LIMIT\n",
        "_A = _LIMIT = 5\ndef f():\n    return _LIMIT\n",
    ],
    ids=[
        "public", "assigned-twice", "global-rebind", "augmented", "loop-target",
        "deleted", "float-literal", "str-literal", "bool-literal",
        "float-annotation", "computed", "chained-targets",
    ],
)
def test_a_name_that_is_not_one_private_integer_literal_stays_a_variable(source):
    module = _lift(PORT + source)
    name = "_LIMIT" if "_LIMIT" in source else "LIMIT"
    assert name in _names(_function(module, "f"))
    assert name not in collect_runtime_port_int_constants(module)


def test_a_computed_constant_still_reads_the_literals_it_is_built_from():
    module = _lift(PORT + "_BASE = 4\n_LIMIT = _BASE + 1\ndef f():\n    return _BASE\n")
    assert collect_runtime_port_int_constants(module) == {"_BASE": 4}
    limit = next(
        statement for statement in module.body
        if isinstance(statement, pa.Assign) and statement.targets[0].ident == "_LIMIT"
    )
    assert "_BASE" not in _names(limit.value) and _int_literals(limit.value) == [4, 1]
    assert _int_literals(_function(module, "f")) == [4]


def test_a_function_that_binds_the_name_keeps_its_own_variable():
    module = _lift(
        PORT + "_SIZE = 16\n"
        "def parameter(_SIZE):\n    return _SIZE\n"
        "def local():\n    _SIZE = 3\n    return _SIZE\n"
        "def reader():\n    return _SIZE\n"
    )
    assert _names(_function(module, "parameter")).count("_SIZE") == 1
    assert _names(_function(module, "local")).count("_SIZE") == 2
    assert "_SIZE" not in _names(_function(module, "reader"))


def test_a_class_that_binds_the_name_is_left_alone():
    module = _lift(
        PORT + "_SIZE = 16\n"
        "class Holder:\n    _SIZE = 2\n    def get(self):\n        return _SIZE\n"
        "def reader():\n    return _SIZE\n"
    )
    holder = next(node for node in module.body if isinstance(node, pa.ClassDef))
    assert "_SIZE" in _names(holder)
    assert "_SIZE" not in _names(_function(module, "reader"))


def test_a_runtime_port_that_does_not_opt_in_keeps_its_variables():
    module = _lift(PLAIN_PORT + "_LIMIT = 5\ndef f():\n    return _LIMIT\n")
    assert "_LIMIT" in _names(_function(module, "f"))
    assert collect_runtime_port_int_constants(module) == {}


def test_the_declaration_alone_does_not_make_an_ordinary_module_fold():
    module = parse_and_lift(
        "__pcc_fold_private_int_constants__ = True\n_LIMIT = 5\n"
        "def f():\n    return _LIMIT\n",
        "<ordinary>", "ordinary",
    )
    assert "_LIMIT" in _names(_function(module, "f"))


def test_an_ordinary_module_keeps_assignable_globals():
    source = "_LIMIT = 5\ndef f():\n    return _LIMIT\n"
    module = parse_and_lift(source, "<ordinary>", "ordinary")
    assert "_LIMIT" in _names(_function(module, "f"))
    assert collect_runtime_port_int_constants(module) == {}


def test_a_freestanding_module_still_drops_the_folded_definition():
    module = parse_and_lift(
        "__pcc_freestanding__ = True\n_LIMIT = 5\nLIMIT = 6\n"
        "def f():\n    return _LIMIT + LIMIT\n",
        "<kernel>", "kernel",
    )
    # No module state: both names fold and neither statement remains.
    assert _assigned(module) == ["__pcc_freestanding__"]
    assert _int_literals(_function(module, "f")) == [5, 6]


def test_runtime_port_ir_reads_no_folded_constant_from_memory(tmp_path, monkeypatch):
    from pcc.frontends.python.pipeline import compile_python

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "port_constants.py"
    output = source.with_suffix(".ll")
    source.write_text(
        "from pcc.extern import c_abi_export\n"
        "from pcc.unsafe import load_i64\n"
        "__pcc_runtime_port__ = True\n"
        "__pcc_fold_private_int_constants__ = True\n"
        "_FIELD: int = 24\n"
        "_COUNTER: int = 0\n"
        "@c_abi_export(\"port_field\")\n"
        "def port_field(record) -> int:\n"
        "    return load_i64(record, _FIELD)\n"
        "@c_abi_export(\"port_bump\")\n"
        "def port_bump() -> int:\n"
        "    global _COUNTER\n"
        "    _COUNTER = _COUNTER + 1\n"
        "    return _COUNTER\n"
    )
    compile_python(
        str(source), str(output), emit_llvm_only=True, backend="self",
        ir_scaffold_mode="on", target_triple="x86_64-unknown-linux-gnu",
    )
    text = output.read_text()
    field = re.search(r"(?ms)^define [^\n]*@port_field\(.*?^\}", text).group(0)
    bump = re.search(r"(?ms)^define [^\n]*@port_bump\(.*?^\}", text).group(0)
    # The constant is an immediate in its reader; the variable is still
    # defined, and a rebound name still loads and stores its variable.
    assert "_FIELD" not in field and re.search(r"\b24\b", field)
    assert re.search(r"(?m)^@[^\n]*_FIELD[^\n]* = ", text)
    assert "_COUNTER" in bump and "store" in bump
