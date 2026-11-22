"""Separate native-owned declarations from executable empty Go bodies."""
import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.syntax import GoParser


def test_bodyless_function_retains_signature_and_is_not_an_empty_definition():
    code = (
        "package main\nfunc native(value int) (result int)\n"
        "func normal() {}\nfunc main() {}\n"
    )
    unit = GoParser().parse(code, "native.go")
    native, normal, _ = unit.functions
    assert native.declaration_only and native.body == []
    assert not normal.declaration_only and normal.body == []
    assert [(token.text, dtype) for token, dtype in native.params] == [("value", "int")]
    assert native.result == "int" and native.result_names[0].text == "result"
    with pytest.raises(UnsupportedGo, match="bodyless function requires an owned implementation") as caught:
        compile_go(code, "native.go")
    assert caught.value.code == "go.unsupported.external-function-owner"


@pytest.mark.parametrize("suffix", ("func native() 4\n", "func native() = 1\n"))
def test_bad_function_definitions_are_not_external_declarations(suffix):
    with pytest.raises(GoDiagnostic, match="expected '\\{'") as caught:
        GoParser().parse("package main\n" + suffix + "func main() {}\n", "bad.go")
    assert not isinstance(caught.value, UnsupportedGo)


def test_generic_bodyless_declaration_stays_invalid():
    with pytest.raises(GoDiagnostic, match="generic function declaration requires a body") as caught:
        GoParser().parse("package main\nfunc F[T any]()\nfunc main() {}\n", "bad.go")
    assert not isinstance(caught.value, UnsupportedGo)
