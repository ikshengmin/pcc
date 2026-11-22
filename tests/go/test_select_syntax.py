"""Communication clauses are preserved, but PCC does not schedule select."""
import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.syntax import GoParser


def test_select_keeps_communication_statements_and_bodies():
    unit = GoParser().parse(
        "package main\nfunc main() {\n"
        "select {\ncase <-c:\n case c <- true:\n case v, ok := <-c: _, _ = v, ok\n"
        "case x, ok = <-c: break\n default: panic(1)\n}\n}\n",
        "select.go",
    )
    select, = unit.functions[-1].body
    assert select.kind == "select"
    assert [clause.kind for clause in select.cases] == ["case", "case", "case", "case", "default"]
    assert [clause.init.kind for clause in select.cases[:-1]] == ["expression", "send", ":=", "="]
    assert select.cases[0].init.exprs[0].kind == "receive"
    assert [x.kind for x in select.cases[-1].body] == ["expression"]


@pytest.mark.parametrize("body", ("", "default: break L"))
def test_select_has_explicit_pre_ir_boundary(body):
    label = "L: " if body else ""
    with pytest.raises(UnsupportedGo, match="select scheduling") as caught:
        compile_go(f"package main\nfunc main() {{ {label}select {{ {body} }} }}\n", "select.go")
    assert caught.value.code == "go.unsupported.select"


@pytest.mark.parametrize("body,message", (
    ("case f():", "requires send or receive"),
    ("case c += <-c:", "requires send or receive"),
    ("case x, y, z := <-c:", "requires send or receive"),
    ("case x := <-c, 1:", "requires send or receive"),
    ("default: ; default:", "multiple defaults"),
    ("default: fallthrough", "cannot fallthrough in select"),
))
def test_invalid_select_clauses_are_source_errors(body, message):
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(f"package main\nfunc main() {{ select {{ {body} }} }}\n", "invalid.go")
    assert not isinstance(caught.value, UnsupportedGo)
