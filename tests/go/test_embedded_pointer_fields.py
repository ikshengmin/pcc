"""Keep embedded pointer type syntax without admitting its storage or promotion."""
import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.syntax import GoParser, PointerSyntax, TypeInstantiationSyntax


def test_pointer_field_names_types_and_tags_survive_parsing():
    code = (
        "package main\n"
        "type Wrap struct { *File `original`; *external.Box[int]; n int }\n"
        "func main() {}\n"
    )
    syntax = GoParser().parse(code, "fields.go").types[0].type
    assert [tok.text for tok, _ in syntax.fields] == ["File", "Box", "n"]
    assert syntax.embedded == [True, True, False]
    assert syntax.tags[0].text == "`original`"
    first, second = [dtype for _, dtype in syntax.fields[:2]]
    assert isinstance(first, PointerSyntax) and first.element.token.text == "File"
    assert isinstance(second.element, TypeInstantiationSyntax)
    assert second.element.base.qualifier == "external"


def test_exact_os_style_embedded_pointer_rejects_before_storage_lowering():
    code = (
        "package main\n"
        "type File struct { n int }\n"
        "type noReadFrom struct{}\n"
        "type fileWithoutReadFrom struct { noReadFrom; *File }\n"
        "func main() {}\n"
    )
    with pytest.raises(UnsupportedGo, match="embedded pointer fields require") as caught:
        compile_go(code, "fields.go")
    assert caught.value.code == "go.unsupported.embedded-fields"


@pytest.mark.parametrize("source,message", (
    ("type T struct { **T }", "expected 'name'"),
    ("type T struct { *I }; type I interface { M() }", "embedded field cannot point"),
    ("type T struct { *P }; type P *int", "embedded field cannot point"),
))
def test_invalid_pointer_embedding_does_not_report_storage_unimplemented(source, message):
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(f"package main;{source};func main(){{}}", "fields.go")
    assert not isinstance(caught.value, UnsupportedGo)
