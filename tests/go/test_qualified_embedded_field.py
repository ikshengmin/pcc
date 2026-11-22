"""Qualified value embeddings preserve the real imported nominal owner."""
import os

import pytest

from pcc.frontends.go import GoDiagnostic, compile_go_packages
from pcc.frontends.go.syntax import GoParser
from tests.go.owned_execution import (
    Execution, compile_module, compile_reference_packages, run, write_artifact,
)
from tests.go.test_execution import forbid_external_compiler


DEP = "package state\ntype Mutex struct { Field int }\n"
MAIN = """package main
import dep "example.org/state"
type Entry struct { dep.Mutex; Other int }
func main() {
    a := Entry{Mutex: dep.Mutex{Field: 13}, Other: 29}
    b := a
    b.Mutex.Field = 7
    if a.Mutex.Field + a.Other != 42 || b.Mutex.Field != 7 { panic("layout") }
}
"""
PACKAGES = {"main": (("main.go", MAIN),), "example.org/state": (("state.go", DEP),)}


def test_qualified_embedded_field_retains_local_field_name_and_real_qualifier():
    shape = GoParser().parse(MAIN, "main.go").types[0].type
    name, dtype = shape.fields[0]
    assert (name.text, dtype.qualifier) == ("Mutex", "dep")
    assert shape.embedded == [True, False]
    assert "getelementptr" in str(compile_go_packages(PACKAGES))


def test_qualified_unexported_embed_stays_invalid():
    packages = {
        "main": (("main.go", 'package main;import "state";type Entry struct{ state.internal };func main(){}'),),
        "state": (("state.go", "package state;type internal struct{ Field int }"),),
    }
    with pytest.raises(GoDiagnostic, match="cannot access unexported package member internal"):
        compile_go_packages(packages)


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
def test_qualified_embedded_value_copy_matches_official_go(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_module(compile_go_packages(PACKAGES))
    owned = tmp_path / "owned"
    write_artifact(artifact, owned)
    official = compile_reference_packages(PACKAGES, tmp_path / "reference",
                                          compiler=os.environ["PCC_GO_REFERENCE"],
                                          cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert run(owned) == run(official) == Execution(0, b"", b"")
