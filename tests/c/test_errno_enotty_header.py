"""Owned preprocessing and AST contracts, not terminal/runtime qualification."""

import subprocess

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.parse.c_parser import CParser
from pcc.frontends.c.preprocessor import preprocess


# Every admitted C ABI profile, including the distinct GNU Windows profile.
TARGETS = (
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
    "arm64-apple-darwin",
    "x86_64-pc-windows-msvc",
    "x86_64-w64-windows-gnu",
)


@pytest.fixture(scope="module")
def parser():
    return CParser()


@pytest.fixture(autouse=True)
def no_external_preprocessor(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("errno header contract must use owned preprocessing only")

    monkeypatch.setattr(subprocess, "Popen", forbidden)


def _constant(node, expected):
    assert isinstance(node, c_ast.Constant)
    assert node.type == "int"
    assert int(node.value, 0) == expected


@pytest.mark.parametrize("target", TARGETS)
def test_enotty_has_exact_target_value_and_comparison_ast(parser, target):
    source = """
#include <errno.h>
#include <errno.h>
#if ENOTTY != 25
#error ENOTTY must have the target ABI value 25
#endif
int enotty_value = ENOTTY;
int terminal_error(int code) { return code == ENOTTY || code == EINVAL; }
"""
    tree = parser.parse(preprocess(source, target_triple=target), filename="enotty.c")
    value = next(node for node in tree.ext
                 if isinstance(node, c_ast.Decl) and node.name == "enotty_value")
    assert value.type.type.names == ["int"]
    _constant(value.init, 25)
    function = next(node for node in tree.ext
                    if isinstance(node, c_ast.FuncDef)
                    and node.decl.name == "terminal_error")
    result = function.body.block_items[0]
    assert isinstance(result, c_ast.Return)
    assert isinstance(result.expr, c_ast.BinaryOp) and result.expr.op == "||"
    for comparison, expected in ((result.expr.left, 25), (result.expr.right, 22)):
        assert isinstance(comparison, c_ast.BinaryOp) and comparison.op == "=="
        assert isinstance(comparison.left, c_ast.ID) and comparison.left.name == "code"
        _constant(comparison.right, expected)


@pytest.mark.parametrize("target", TARGETS)
def test_enotty_preserves_prior_definition_across_repeated_include(parser, target):
    source = """
#define ENOTTY 31337
#include <errno.h>
#include <errno.h>
#if ENOTTY != 31337
#error errno.h must not overwrite a prior ENOTTY definition
#endif
int preserved_enotty = ENOTTY;
"""
    tree = parser.parse(preprocess(source, target_triple=target), filename="enotty-prior.c")
    value = next(node for node in tree.ext
                 if isinstance(node, c_ast.Decl) and node.name == "preserved_enotty")
    assert value.type.type.names == ["int"]
    _constant(value.init, 31337)
