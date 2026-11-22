"""Preserve go-call syntax without admitting a scheduler or synchronous call."""
import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.syntax import GoParser


@pytest.mark.parametrize("declarations,call", (
    ("func f(n int) int { return n }", "f(7)"),
    ("type T int; func (T) f() {}", "T(0).f()"),
    ("", 'println("value")'),
))
def test_go_statement_keeps_call_but_is_not_executable(declarations, call):
    source = f"package main\n{declarations}\nfunc main() {{ go {call} }}\n"
    unit = GoParser().parse(source, "source.go")
    stmt, = unit.functions[-1].body
    assert (stmt.kind, stmt.token.line, stmt.exprs[0].kind) == ("go", 3, "call")
    with pytest.raises(UnsupportedGo, match="goroutine scheduling") as caught:
        compile_go(source, "source.go")
    assert caught.value.code == "go.unsupported.goroutines"


def test_go_statement_preserves_an_unmodified_function_literal_operand():
    source = "package main\nfunc main() { go func() { defer println(1) }() }\n"
    unit = GoParser().parse(source, "source.go")
    stmt, = unit.functions[-1].body
    assert stmt.kind == "go" and stmt.exprs[0].kind == "call"
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(source, "source.go")
    assert caught.value.code == "go.unsupported.function-values"


@pytest.mark.parametrize("call,message", (
    ("(f())", "expression in go must not be parenthesized"),
    ("f", "expression in go must be function call"),
    ("missing()", "undefined"),
    ("f(1)", "argument count"),
    ("int(1)", "go requires a function call"),
))
def test_invalid_go_calls_remain_source_errors(call, message):
    source = f"package main\nfunc f() {{}}\nfunc main() {{ go {call} }}\n"
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(source, "source.go")
    assert not isinstance(caught.value, UnsupportedGo)
