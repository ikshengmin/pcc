"""Go spec: Struct types, Type identity, Selectors (direct field only)."""
import os

import pytest

from pcc.frontends.go import UnsupportedGo, compile_go
from tests.go.owned_execution import (
    Execution, compile_owned, compile_reference, run, write_artifact,
)
from tests.go.test_execution import forbid_external_compiler

ORIGINAL = """package main
type Inner struct { Value int }
type Outer struct { Inner `typed`; Extra int }
func Result() int {
    a := Outer{Inner: Inner{Value: 17}, Extra: 25}
    b := a
    b.Inner.Value = 1
    if b.Inner.Value != 1 { return 0 }
    return a.Inner.Value + a.Extra
}
func main() { if Result() != 42 { panic("embedded field copy") } }
"""


def test_named_value_embedded_field_uses_existing_owned_record_storage():
    artifact = compile_owned((("original.go", ORIGINAL),), observe_result=True)
    assert "getelementptr" in artifact.ir


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
def test_named_value_embedded_field_matches_official_go(tmp_path, monkeypatch):
    units = (("original.go", ORIGINAL),)
    reference = compile_reference(units, tmp_path / "reference",
                                  compiler=os.environ["PCC_GO_REFERENCE"],
                                  cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units)
    owned = tmp_path / "owned"
    write_artifact(artifact, owned)
    assert run(owned) == run(reference) == Execution(0, b"", b"")


def test_promoted_selector_remains_an_explicit_boundary():
    source = "package main;type In struct{X int};type Out struct{In};func f(o Out)int{return o.X};func main(){}"
    with pytest.raises(UnsupportedGo) as error:
        compile_go(source)
    assert error.value.code == "go.unsupported.embedded-fields"


def test_embedded_and_explicitly_named_fields_are_not_identical_types():
    from pcc.frontends.go import GoDiagnostic

    original = (
        "package main;type Inner struct{X int};func main(){"
        "var a struct{Inner}; var b struct{Inner Inner}; a=b; _=a}"
    )
    with pytest.raises(GoDiagnostic) as error:
        compile_go(original)
    assert not isinstance(error.value, UnsupportedGo)
    assert "cannot use struct{Inner Inner} as struct{Inner}" in str(error.value)
