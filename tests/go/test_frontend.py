"""Owned Go scanner, parser, semantic and public-IR contracts."""

import hashlib
import json
from pathlib import Path

import pytest

from pcc.frontends.go import GoDiagnostic, GoParser, compile_go, compile_go_sources
from pcc.frontends.go.sema import Analyzer
from pcc.frontends.go.syntax import scan
from pcc.ir import ir


ROOT = Path(__file__).parent


def source(body, helpers=""):
    return "package main\n" + helpers + "\nfunc main() { " + body + " }\n"


@pytest.mark.parametrize("text,expected", [
    ("x // comment\ny", ["x", ";", "y", ";", ""]),
    ("x /* same */ y", ["x", "y", ";", ""]),
    ("x /* two\n lines */ y", ["x", ";", "y", ";", ""]),
    ("if x {\nreturn\n}", ["if", "x", "{", "return", ";", "}", ";", ""]),
    ("x +\ny", ["x", "+", "y", ";", ""]),
])
def test_go_semicolon_insertion(text, expected):
    assert [token.text for token in scan(text)] == expected


def test_go_parser_produces_source_ast_not_another_language():
    program = GoParser().parse(source("x := 1; _ = x"), "unit.go")
    assert program.functions[0].body[0].kind == ":="
    assert program.functions[0].body[0].names[0].text == "x"
    assert program.functions[0].body[0].exprs[0].kind == "integer"
    assert program.functions[0].token.filename == "unit.go"


def test_go_public_ir_has_typed_functions_and_control_flow():
    module = compile_go(source("_ = f(12)", "func f(x int) int { for x > 9 { x-- }; return x }"))
    assert isinstance(module, ir.Module)
    text = str(module)
    assert "define i64" in text and "alloca i64" in text and "br i1" in text
    assert "icmp sgt i64" in text
    assert "define void" in text


@pytest.mark.parametrize("literal,value", [
    ("0x_ff", 255), ("0b1010_0011", 163), ("0o701", 449),
    ("0_701", 449), ("0", 0), ("1_000", 1000),
])
def test_go_integer_radix_and_separator_semantics(literal, value):
    program = GoParser().parse(source("_ = " + literal))
    Analyzer(program).analyze()
    assert program.functions[0].body[0].exprs[0].constant == value


@pytest.mark.parametrize("literal", ["09", "0x", "0b2", "1__0", "0_x1", "42_", "1e4", "1.5"])
def test_invalid_or_unimplemented_literals_are_not_silently_reinterpreted(literal):
    with pytest.raises(GoDiagnostic, match="literal"):
        compile_go(source("_ = " + literal))


@pytest.mark.parametrize("body,helpers,message", [
    ("x := true; x = 1; _ = x", "", "cannot use untyped int as bool"),
    ("var x int; var y int64; x = y; _ = x", "", "cannot use int64 as int"),
    ("if 1 {}", "", "condition must be bool"),
    ("_ = true + false", "", "require integers"),
    ("_ = true < false", "", "requires integer"),
    ("_ = 1 && 2", "", "require bool"),
    ("_ = !1", "", "requires bool"),
    ("_ = ^true", "", "requires integer"),
    ("x := 9223372036854775808; _ = x", "", "overflows int"),
    ("_ = int64(9223372036854775808)", "", "overflows int64"),
    ("const x int = 9223372036854775807 + 1; _ = x", "", "overflows int"),
    ("const x = f(); _ = x", "func f() int { return 1 }", "not a constant"),
    ("const x = 1; x = 2", "", "cannot assign to constant"),
    ("var x int; x := 2; _ = x", "", "at least one new"),
    ("_, _ := 1, 2", "", "at least one new"),
    ("x, x := 1, 2; _ = x", "", "duplicate declaration"),
    ("x := 1", "", "declared and not used"),
    ("{x := 1; _ = x}; _ = x", "", "undefined name"),
    ("if x := 1; x > 0 {}; _ = x", "", "undefined name"),
    ("for i := 0; i < 2; i++ {}; _ = i", "", "undefined name"),
    ("for ; true; i := 1 {}", "", "post statement cannot"),
    ("break", "", "outside loop"), ("continue", "", "outside loop"),
    ("return 1", "", "void function cannot"),
    ("", "func f() int { if true { return 1 } }", "missing return"),
    ("_ = f(1)", "func f() int { return 1 }", "argument count"),
    ("_ = f()", "func f() {}", "void function call"),
    ("f := 1; f()", "func f() {}", "cannot call non-function"),
    ("1 + 2", "", "only function calls"),
    ("int(1)", "", "only function calls"),
    ("_ = bool(1)", "", "invalid conversion"),
    ("_ = int64()", "", "one argument"),
    ("_ = f(2, 0)", "func f(x, y int) int { return x/y }", "dynamic division"),
    ("_ = f(2, 0)", "func f(x, y int) int { return x<<y }", "dynamic signed shift"),
    ("_ = 3/0", "", "division by zero"),
    ("_ = 1 << -1", "", "negative shift"),
    ("panic(1)", "", "unsupported builtin panic"),
    ("go f()", "func f() {}", "unsupported statement"),
    ("defer f()", "func f() {}", "unsupported statement"),
    ("var x string", "", "unsupported type"),
    ("var x []int", "", "requires a type"),
    ("switch {}", "", "unsupported statement"),
    ("var true bool", "", "shadowing predeclared"),
])
def test_go_rejects_invalid_or_unimplemented_semantics(body, helpers, message):
    with pytest.raises(GoDiagnostic, match=message) as error:
        compile_go(source(body, helpers), "negative.go")
    assert error.value.filename == "negative.go"
    assert error.value.line >= 1 and error.value.column >= 1


@pytest.mark.parametrize("text,message", [
    ("package x\nfunc main() {}", "requires package main"),
    ("package main\nimport \"fmt\"\nfunc main() {}", "import source unavailable"),
    ("package main\nvar x int\nfunc main() {}", "unsupported package declaration"),
    ("package main\nfunc main(x int) {}", "main must have no"),
    ("package main\nfunc main() int {return 0}", "main must have no"),
    ("package main\nfunc f() {}", "requires func main"),
    ("package main\nfunc init() {}\nfunc main() {}", "initialization support"),
    ("package main\nfunc main() {}\nfunc main() {}", "duplicate package"),
    ("package main\nconst a = b\nconst b = a\nfunc main() {}", "initialization cycle"),
    ("package main\nfunc f(x int,x int) {}\nfunc main() {}", "duplicate parameter"),
    ("package main\nfunc main() { if true {}\nelse {} }", "unsupported statement"),
    ("package main\nfunc main() {", "unterminated block"),
    ("package main\n/*", "unterminated comment"),
    ("package main\nfunc main() { _ = \"oops }", "unterminated literal"),
])
def test_go_package_and_lexical_diagnostics(text, message):
    with pytest.raises(GoDiagnostic, match=message):
        compile_go(text)


def test_go_multi_file_package_resolves_symbols_without_source_concatenation():
    units = [(name, (ROOT / "fixtures" / name).read_text()) for name in ("multi_main.go", "multi_helper.go")]
    assert "call i64" in str(compile_go_sources(units))
    with pytest.raises(ValueError, match="duplicate Go source"):
        compile_go_sources([units[0], units[0]])
    with pytest.raises(ValueError, match="no source files"):
        compile_go_sources([])


def test_go_target_int_width_is_explicit():
    with pytest.raises(GoDiagnostic, match="64-bit target"):
        compile_go(source(""), target_triple="i386-unknown-linux-gnu")


def test_go_unicode_symbols_have_collision_free_ascii_ir_names():
    text = str(compile_go(source("_ = α(4)", "func α(x int) int { return x + 1 }")))
    assert "__pcc_go_main_ceb1" in text


def test_go_original_upstream_source_and_license_are_pinned():
    manifest = json.loads((ROOT / "upstream/provenance.json").read_text())
    assert manifest["release"] == "go1.27.3"
    assert manifest["license"] == "BSD-3-Clause"
    for filename, expected in manifest["files"].items():
        assert hashlib.sha256((ROOT / "upstream/go1.27.3" / filename).read_bytes()).hexdigest() == expected
    assert "Redistribution and use in source and binary forms" in (ROOT / "upstream/go1.27.3/LICENSE").read_text()


@pytest.mark.parametrize("filename,message", [
    ("test/ken/for.go", "unsupported builtin panic"),
    ("src/math/bits/bits.go", "requires package main"),
])
def test_go_upstream_frontier_is_a_diagnostic_not_a_claimed_pass(filename, message):
    original = (ROOT / "upstream/go1.27.3" / filename).read_text()
    with pytest.raises(GoDiagnostic, match=message):
        compile_go(original, filename)


@pytest.mark.parametrize("body", ["(x) := 1; _ = x", "x, (y) := 1, 2; _ = x; _ = y"])
def test_parenthesized_lvalues_do_not_become_short_declaration_identifiers(body):
    with pytest.raises(GoDiagnostic, match="unparenthesized identifiers"):
        compile_go(source(body))


def test_parenthesized_ordinary_assignment_remains_valid():
    assert "store i64 2" in str(compile_go(source("x := 1; (x) = 2; _ = x")))
