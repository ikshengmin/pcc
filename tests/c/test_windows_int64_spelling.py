"""Owned Windows integer spelling contracts, not a native zlib qualification."""
from pathlib import Path
import subprocess

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.c_abi_layout import (
    builtin_integer_is_unsigned, builtin_scalar_layout,
)
from pcc.frontends.c.parse.c_parse_driver import CParseDriver
from pcc.frontends.c.parse.c_parser import CParser
from pcc.frontends.c.parse.plyparser import ParseError
from pcc.frontends.c.preprocessor import preprocess
from pcc.frontends.c.ssa.builder import SSABuilder

ROOT = Path(__file__).resolve().parents[2]
WINDOWS = "x86_64-pc-windows-msvc"


@pytest.fixture(params=("owned-lr", "ply-compat"))
def parser(request):
    return CParseDriver() if request.param == "owned-lr" else CParser()


@pytest.fixture(autouse=True)
def no_external_tools(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("integer spelling controls must remain owned and host-only")
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def _parse(parser, source, **options):
    return parser.parse(preprocess(source, target_triple=WINDOWS, **options),
                        filename="windows_int64_contract.c")


@pytest.mark.parametrize("qualifier,expected_names,unsigned", (
    ("", ["long", "long"], False),
    ("signed ", ["signed", "long", "long"], False),
    ("unsigned ", ["unsigned", "long", "long"], True),
))
def test_windows_int64_signedness_and_real_type_layout(parser, qualifier, expected_names, unsigned):
    tree = _parse(parser, "typedef " + qualifier + "__int64 offset_t;\n" + """
struct offset_pair { char tag; offset_t value; };
offset_t values[3];
offset_t (*callback)(offset_t, offset_t *);
""")
    alias = next(x for x in tree.ext if isinstance(x, c_ast.Typedef))
    assert alias.type.type.names == expected_names
    assert builtin_integer_is_unsigned(expected_names, WINDOWS) is unsigned
    layout = builtin_scalar_layout(expected_names, WINDOWS)
    assert (layout.size, layout.alignment) == (8, 8)
    builder = SSABuilder(WINDOWS)
    builder.index_file_scope(tree)
    pair = next(x.type for x in tree.ext
                if isinstance(x, c_ast.Decl) and isinstance(x.type, c_ast.Struct))
    assert (builder._ast_type_size(pair), builder._ast_type_align(pair)) == (16, 8)
    values = next(x for x in tree.ext if isinstance(x, c_ast.Decl) and x.name == "values")
    assert builder._ast_type_size(values.type) == 24
    callback = next(x for x in tree.ext if isinstance(x, c_ast.Decl) and x.name == "callback")
    assert isinstance(callback.type, c_ast.PtrDecl)
    signature = callback.type.type
    assert isinstance(signature, c_ast.FuncDecl)
    assert signature.type.type.names == ["offset_t"]
    assert signature.args.params[0].type.type.names == ["offset_t"]
    assert isinstance(signature.args.params[1].type, c_ast.PtrDecl)
    assert signature.args.params[1].type.type.type.names == ["offset_t"]


def test_windows_keeps_long_llp64(parser):
    tree = _parse(parser, "long narrow; __int64 wide;")
    names = {x.name: x.type.type.names for x in tree.ext}
    assert names == {"narrow": ["long"], "wide": ["long", "long"]}
    assert builtin_scalar_layout(names["narrow"], WINDOWS).size == 4
    assert builtin_scalar_layout(names["wide"], WINDOWS).size == 8


def test_vendor_zconf_off64_is_really_64_bits(parser):
    source = """
#if !defined(_WIN32) || defined(__GNUC__)
#error This control must exercise zlib's actual non-GNU Windows branch
#endif
#include "zconf.h"
z_off64_t file_offset;
"""
    tree = _parse(parser, source, include_dirs=[str(ROOT / "projects/zlib-1.3.1")],
                  cpp_args=["-DHAVE_UNISTD_H", "-DHAVE_STDARG_H", "-U__ARM_FEATURE_CRC32"])
    declaration = next(x for x in tree.ext
                       if isinstance(x, c_ast.Decl) and x.name == "file_offset")
    assert declaration.type.type.names == ["long", "long"]
    layout = builtin_scalar_layout(declaration.type.type.names, WINDOWS)
    assert (layout.size, layout.alignment) == (8, 8)


def test_token_spelling_is_not_substring_or_string_rewrite():
    tree = _parse(CParseDriver(), 'const char *text = "__int64"; int prefix__int64suffix; __int64 actual;')
    declarations = {x.name: x for x in tree.ext}
    assert declarations["text"].init.value == '"__int64"'
    assert declarations["prefix__int64suffix"].type.type.names == ["int"]
    assert declarations["actual"].type.type.names == ["long", "long"]


@pytest.mark.parametrize("target", ("x86_64-unknown-linux-gnu", "arm64-apple-darwin"))
def test_other_targets_do_not_gain_microsoft_spelling(target):
    text = preprocess("#ifdef __int64\n#error Microsoft spelling leaked to another target\n#endif\n__int64 unresolved;\n",
                      target_triple=target)
    assert "__int64 unresolved;" in text


def test_unknown_reserved_type_still_rejected(parser):
    with pytest.raises(ParseError):
        _parse(parser, "__pcc_not_a_type unknown;")
