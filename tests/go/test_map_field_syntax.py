"""Go spec: Map types and struct type declarations, not map storage."""
import os

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.syntax import GoParser
from tests.go.owned_execution import Execution, compile_reference, run

ORIGINAL = """package main
type B struct { Extra map[string]float64 }
func main() {
    var b B
    if b.Extra != nil { panic("nonzero map") }
}
"""


def test_map_field_parses_and_preserves_key_value_and_location():
    parsed = GoParser().parse(ORIGINAL, "original.go")
    field = parsed.types[0].type.fields[0][1]
    assert (field.key, field.element) == ("string", "float64")
    assert (field.token.filename, field.token.line) == ("original.go", 2)


def test_map_storage_fails_closed_before_ir(monkeypatch):
    from pcc.frontends.go.lowering import Lowerer

    monkeypatch.setattr(Lowerer, "lower", lambda *args: pytest.fail("map reached IR"))
    with pytest.raises(UnsupportedGo) as error:
        compile_go(ORIGINAL, "original.go")
    assert error.value.code == "go.unsupported.map-storage"
    assert error.value.line == 2


def test_invalid_map_key_is_not_a_runtime_capability_error():
    with pytest.raises(GoDiagnostic, match="invalid map key") as error:
        compile_go("package main;type T struct{ Extra map[[]int]float64 };func main(){}")
    assert not isinstance(error.value, UnsupportedGo)


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
def test_official_go_accepts_the_original_map_field_program(tmp_path):
    binary = compile_reference((("original.go", ORIGINAL),), tmp_path / "reference",
                               compiler=os.environ["PCC_GO_REFERENCE"],
                               cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert run(binary) == Execution(0, b"", b"")
