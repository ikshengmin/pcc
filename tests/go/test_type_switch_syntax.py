"""Keep Go's type-switch guard and case types without dynamic dispatch."""
import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.syntax import GoParser


def test_type_switch_keeps_context_style_binding_and_case_types():
    source = (
        "package main\nfunc f(v any) string {\n"
        "switch n := 2; s := v.(type) {\n"
        "case string: return s\n case *int, nil: _ = n\n default: return \"\"\n}\n"
        "return \"\"\n}\nfunc main() {}\n"
    )
    parsed = GoParser().parse(source, "switch.go")
    switch = parsed.functions[0].body[0]
    assert switch.kind == "type_switch" and switch.init.names[0].text == "n"
    assert [name.text for name in switch.names] == ["s"]
    assert switch.exprs[0].kind == "name" and switch.exprs[0].value == "v"
    assert [[expr.kind for expr in clause.exprs] for clause in switch.cases] == [
        ["type"], ["type", "type"], [],
    ]
    assert switch.cases[0].exprs[0].value == "string"
    assert switch.cases[1].exprs[1].value.token.text == "nil"
    with pytest.raises(UnsupportedGo, match="general interface values") as caught:
        compile_go(source, "switch.go")
    assert caught.value.code == "go.unsupported.interface-types"


@pytest.mark.parametrize("body,diagnostic", (
    ("_ = v.(type)", "type switch guard"),
    ("switch f(v.(type)) {}", "type switch guard"),
    ("switch t = v.(type) {}", "type switch guard"),
    ("switch (v.(type)) {}", "type switch guard"),
    ("switch v.(type) { default: ; default: }", "multiple defaults"),
    ("switch v.(type) { case nil: fallthrough }", "cannot fallthrough in type switch"),
))
def test_invalid_type_switch_forms_remain_source_errors(body, diagnostic):
    source = f"package main\nfunc f(v any) {{ {body} }}\nfunc main() {{}}\n"
    with pytest.raises(GoDiagnostic, match=diagnostic) as caught:
        compile_go(source, "invalid.go")
    assert not isinstance(caught.value, UnsupportedGo)


def test_labeled_type_switch_has_a_break_target_before_rejecting_dispatch():
    src = "package main\nfunc main() { var v error; L: switch v.(type) { default: break L } }\n"
    with pytest.raises(UnsupportedGo, match="type switches"):
        compile_go(src, "switch.go")


def test_scalar_switch_operand_is_rejected_as_invalid_source():
    src = "package main\nfunc main() { switch v := 42; v.(type) {} }\n"
    with pytest.raises(GoDiagnostic, match="type switch operand is not an interface") as caught:
        compile_go(src, "switch.go")
    assert not isinstance(caught.value, UnsupportedGo)
