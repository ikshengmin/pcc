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
