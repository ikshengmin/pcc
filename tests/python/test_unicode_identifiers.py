"""Python Language Reference 2.3 / PEP 3131: XID and NFKC identifiers.

https://docs.python.org/3.15/reference/lexical_analysis.html#identifiers
https://peps.python.org/pep-3131/
"""
import ast
import os
from pathlib import Path
import random
import subprocess
import sys
import unicodedata

import pytest

from pcc.frontends.python.py_lex import Lexer, LexError, TK_NAME
from pcc.frontends.python.unicode_identifiers import (
    is_identifier_continue,
    is_identifier_start,
    normalize_identifier,
)


@pytest.mark.parametrize("name", (
    "glöbal", "樂", "变量", "café", "cafe\u0301", "K", "ﬀ", "ＡＢＣ",
    "각", "각", "a\u0315\u0300", "α\u0345\u0301", "𐐀name", "x١", "x\u200d",
))
def test_identifier_tokens_match_cpython_ast_normalization(name):
    source = name + " = 42\n"
    expected = ast.parse(source).body[0].targets[0].id
    token = Lexer(source).tokenize()[0]
    assert token.kind == TK_NAME
    assert token.text == expected
    assert normalize_identifier(name) == unicodedata.normalize("NFKC", name)


@pytest.mark.parametrize("source", ("😀 = 1\n", "\u0301x = 1\n", "x\u00a0 = 1\n"))
def test_non_identifier_characters_are_rejected(source):
    with pytest.raises(SyntaxError):
        ast.parse(source)
    with pytest.raises(LexError):
        Lexer(source).tokenize()


def test_normalization_does_not_turn_a_name_into_a_keyword():
    token = Lexer("ｉｆ = 42\n").tokenize()[0]
    assert token.kind == TK_NAME and token.text == "if" and token.raw_text == "ｉｆ"
    assert ast.parse("ｉｆ = 42\n").body[0].targets[0].id == "if"


def test_identifier_normalization_preserves_string_literal_contents():
    tokens = Lexer('K = "K"\n').tokenize()
    assert tokens[0].text == "K"
    assert tokens[2].text == '"K"'


@pytest.mark.parametrize("source", (
    "ｍａｔｃｈ value:\n    case 1: pass\n",
    "match value:\n    ｃａｓｅ 1: pass\n",
))
def test_normalization_does_not_create_soft_keywords(source):
    from pcc.frontends.python.py_parse import ParseError, parse

    with pytest.raises(SyntaxError):
        ast.parse(source)
    with pytest.raises(ParseError):
        parse(source)


def test_unicode_symbol_components_are_injective_and_keep_ascii_names():
    from pcc.frontends.python.symbol_names import identifier_symbol

    names = ("café", "caf$ue9$", "caf_ue9_", "参数", "變數", "ordinary_name")
    symbols = [identifier_symbol(name) for name in names]
    assert len(set(symbols)) == len(names)
    assert all(symbol.isascii() for symbol in symbols)
    assert identifier_symbol("ordinary_name") == "ordinary_name"


@pytest.mark.parametrize("target", (
    "arm64-apple-darwin", "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu", "x86_64-pc-windows-msvc",
))
def test_unicode_identifiers_emit_owned_objects(target):
    from pcc.backend.owned_object_emit import emit_owned_object
    from tests.python.test_shared_call_binding import _emit

    emitted = _emit(IDENTIFIER_PROGRAM)
    assert emitted.isascii()
    assert emit_owned_object(emitted, target)


def test_unicode_cross_module_exports_match_definitions(tmp_path):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.pipeline_context import build_closed_world_context
    from pcc.frontends.python.type_infer import infer_module

    provider = tmp_path / "provider.py"
    consumer = tmp_path / "consumer.py"
    provider.write_text(IDENTIFIER_PROGRAM.split("def main():", 1)[0], encoding="utf-8")
    consumer.write_text(
        "from provider import café, Boîte\n"
        "def main():\n    print(café(1, glöbal=2), Boîte().méthode())\nmain()\n",
        encoding="utf-8",
    )
    modules, exports, derived = build_closed_world_context(
        [str(provider), str(consumer)], ["provider", "consumer"],
    )
    expected = next(method["symbol"] for method in exports["provider"]["Boîte"]["methods"]
                    if method["name"] == "méthode")
    for module in modules:
        typed = infer_module(module, external_exports=exports, derived_class_map=derived)
        codegen = L1CodeGen(typed, ir_scaffold_mode="on")
        codegen._native_module_exports = exports
        codegen._strict_no_libpython = True
        codegen._prefer_native_callable_values = True
        text = str(codegen.generate(typed))
        assert text.isascii()
        assert codegen.class_lowering.classes["Boîte"].methods["méthode"].name == expected
        assert emit_owned_object(text, "arm64-apple-darwin")


def test_identifier_properties_match_cpython_across_unicode():
    for code in range(0x110000):
        character = chr(code)
        assert is_identifier_start(code) == character.isidentifier(), hex(code)
        assert is_identifier_continue(code) == ("A" + character).isidentifier(), hex(code)


def test_normalization_matches_cpython_decompositions_and_combinations():
    for code in range(0x110000):
        character = chr(code)
        if unicodedata.decomposition(character) or 0xAC00 <= code <= 0xD7A3:
            assert normalize_identifier(character) == unicodedata.normalize("NFKC", character), hex(code)
    generator = random.Random(3131)
    alphabet = "aAeéαωKﬀＡ각" + "\u0300\u0301\u0308\u0315\u0323\u0345"
    for _ in range(1000):
        value = "".join(generator.choice(alphabet) for _ in range(12))
        assert normalize_identifier(value) == unicodedata.normalize("NFKC", value), repr(value)


def test_utf8_offsets_agree_for_ascii_and_unicode_source():
    source = "glöbal = 42\n樂 = glöbal\n"
    names = [token for token in Lexer(source).tokenize() if token.kind == TK_NAME]
    assert [(token.text, token.line, token.col) for token in names] == [
        ("glöbal", 1, 1), ("樂", 2, 1), ("glöbal", 2, 7),
    ]


IDENTIFIER_PROGRAM = '''def café(参数, *, glöbal):
    return 参数 + glöbal

class Boîte:
    def __init__(self):
        self.属性 = 9
    def méthode(self):
        return self.属性

def main():
    K = 3
    K += 1
    résumé = 11
    ｉｆ = 12
    print(café(7, glöbal=8), K, K, résumé, ｉｆ)
    print(Boîte().méthode())
    print(café.__name__, Boîte.__name__)
main()
'''

LEXER_PROGRAM = '''from pcc.frontends.python.py_lex import Lexer
def main():
    for token in Lexer("glöbal = 42\\nK = glöbal\\n").tokenize():
        print(token.kind, token.text, token.raw_text, token.line, token.col)
main()
'''


@pytest.mark.integration
@pytest.mark.parametrize("program", (IDENTIFIER_PROGRAM, LEXER_PROGRAM), ids=("names", "owned-lexer"))
def test_unicode_identifier_programs_match_cpython_on_five_collectors(
    tmp_path, monkeypatch, program, pcc_runtime_archive, python_program_compiler,
):
    # The reference sees this source tree through PYTHONPATH; give the native
    # resolver the same package source instead of relying on an installed pcc.
    monkeypatch.setenv("PCC_PACKAGE_SITE", str(Path(__file__).resolve().parents[2]))
    source = tmp_path / "unicode_program.py"
    source.write_text(program, encoding="utf-8")
    reference = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=30,
    )
    assert reference.returncode == 0, reference.stderr
    executable = tmp_path / "unicode_program"
    python_program_compiler(
        str(source), str(executable), backend="self", ir_scaffold_mode="on",
        runtime_archive=str(pcc_runtime_archive),
    )
    for collector in range(5):
        result = subprocess.run(
            [str(executable)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(collector)),
        )
        assert (result.returncode, result.stdout, result.stderr) == (
            reference.returncode, reference.stdout, reference.stderr,
        ), collector
