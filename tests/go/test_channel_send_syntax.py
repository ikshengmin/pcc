"""Recognize Go's channel SendStmt without implementing a channel ABI."""
import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.syntax import GoParser


def test_send_statement_preserves_channel_and_payload_expressions():
    parsed = GoParser().parse(
        "package main\nfunc main() { go func() { defer func() { b.signal <- true }() }() }\n",
        "send.go",
    )
    outer, = parsed.functions[-1].body
    deferred, = outer.exprs[0].callee.value.body
    send, = deferred.exprs[0].callee.value.body
    assert (send.kind, send.token.text) == ("send", "<-")
    channel, payload = send.exprs
    assert (channel.kind, channel.value, payload.value) == ("selector", "signal", "true")


@pytest.mark.parametrize("text,message", (
    ("x, y <- 1", "send requires one channel expression"),
    ("x <-", "unsupported expression token"),
    ("x <- 1, 2", "expected semicolon or newline"),
))
def test_malformed_send_is_not_an_expression_statement(text, message):
    with pytest.raises(GoDiagnostic, match=message):
        GoParser().parse(f"package main\nfunc main() {{ {text} }}\n", "invalid.go")


def test_send_to_scalar_is_a_source_error_before_ir():
    with pytest.raises(GoDiagnostic, match="cannot send to a non-channel value") as caught:
        compile_go("package main\nfunc main() { var x int; x <- 1 }\n", "invalid.go")
    assert not isinstance(caught.value, UnsupportedGo)


def test_receive_is_unary_and_preserves_standalone_and_comma_ok_forms():
    parsed = GoParser().parse(
        "package main\nfunc main() { <-b.signal; v, ok := <-b.signal; _ = !<-b.signal }\n",
        "receive.go",
    )
    receive_stmt, declaration, assignment = parsed.functions[-1].body
    assert receive_stmt.kind == "expression"
    assert [target.value for target in declaration.targets] == ["v", "ok"]
    negation, = assignment.exprs
    assert (negation.kind, negation.value) == ("unary", "!")
    for expr in (receive_stmt.exprs[0], declaration.exprs[0], negation.args[0]):
        assert (expr.kind, expr.token.text) == ("receive", "<-")
        assert (expr.args[0].kind, expr.args[0].value) == ("selector", "signal")


@pytest.mark.parametrize("statement", ("<-1", "_ = <-1", "v, ok := <-1; _, _ = v, ok"))
def test_receive_from_scalar_is_a_source_error_before_ir(statement):
    source = f"package main\nfunc main() {{ {statement} }}\n"
    with pytest.raises(GoDiagnostic, match="cannot receive from a non-channel value") as caught:
        compile_go(source, "invalid.go")
    assert not isinstance(caught.value, UnsupportedGo)
