"""Preserve original compound generic constraints and fail before type claims."""
import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.syntax import GoParser


def test_bytealg_constraint_survives_parsing_without_claiming_slice_operations():
    source = (
        "package main\nfunc hashStr[T string | []byte](sep T) (uint32, uint32) {\n"
        " var h uint32; for i := 0; i < len(sep); i++ { h += uint32(sep[i]) }; return h, 1\n"
        "}\nfunc main() {}\n"
    )
    unit = GoParser().parse(source, "bytealg_shape.go")
    [(name, constraint)] = unit.functions[0].type_parameters
    assert name.text == "T"
    assert [(token.kind, token.text) for token in constraint.tokens] == [
        ("name", "string"), ("|", "|"), ("[", "["), ("]", "]"), ("name", "byte"),
    ]
    assert unit.functions[0].body
    with pytest.raises(UnsupportedGo, match="general type-set resolution") as caught:
        compile_go(source, "bytealg_shape.go")
    assert caught.value.code == "go.unsupported.generic-boundaries"


def test_compound_constraint_is_shared_by_a_group_without_changing_named_constraints():
    source = "package main\nfunc F[A, B ~int | ~int64, C comparable]() {}\nfunc main() {}\n"
    unit = GoParser().parse(source, "sets.go")
    a, b, c = unit.functions[0].type_parameters
    assert a[1] is b[1]
    assert [token.text for token in a[1].tokens] == ["~", "int", "|", "~", "int64"]
    assert c[1].text == "comparable"


def test_missing_term_is_still_a_syntax_error():
    with pytest.raises(GoDiagnostic) as caught:
        GoParser().parse("package main;func F[T string | ](){};func main(){}", "invalid.go")
    assert not isinstance(caught.value, UnsupportedGo)
