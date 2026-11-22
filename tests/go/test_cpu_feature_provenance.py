"""Keep the selected x86 option addresses distinct from general pointer storage."""
import json
import os

import pytest

from pcc.frontends.go import UnsupportedGo, compile_go
from pcc.frontends.go.branches import walk
from pcc.frontends.go.syntax import GoParser
from scripts.ci_go_math_bits_project import sha256
from scripts.go_std_closure import FIXTURE, load_linux_normal_plan
from tests.go.owned_execution import Execution, compile_owned, compile_reference, run, write_artifact
from tests.go.test_execution import forbid_external_compiler


DIRECT = """package main
var X86 struct { HasTest bool }
func set(p *bool, enabled bool) {
    if p != nil { *p = enabled }
}
func main() {
    address := &X86.HasTest
    set(address, true)
    if !X86.HasTest { panic("lost physical field") }
    *address = false
    if X86.HasTest { panic("lost alias") }
}
"""


def test_all_selected_x86_feature_initializers_take_static_record_field_addresses():
    recipe, _ = load_linux_normal_plan()
    manifest = json.loads(recipe)
    name = "src/internal/cpu/cpu_x86.go"
    original = (FIXTURE / name).read_bytes()
    assert manifest["files"][name] == [len(original), sha256(original)]
    unit = GoParser().parse(original.decode(), name, require_main=False)
    init = next(fn for fn in unit.functions if fn.token.text == "doinit")
    values, seen = [], set()

    def expression(expr):
        if id(expr) in seen:
            return
        seen.add(id(expr))
        if expr.kind == "composite":
            for key, value in zip(expr.keys, expr.args):
                if key is not None and key.value == "Feature":
                    values.append(value)
        for item in expr.args:
            expression(item)

    for statement in walk(init.body):
        for expr in statement.exprs:
            expression(expr)
    assert len(values) == 23
    selectors = []
    for value in values:
        assert (value.kind, value.value) == ("unary", "&")
        selector, = value.args
        assert selector.kind == "selector" and selector.args[0].value == "X86"
        selectors.append(selector.value)
    assert len(set(selectors)) == 23


def test_pointer_bearing_record_remains_unadmitted():
    source = "package main;type option struct{Feature *bool};func main(){}"
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(source, "option.go")
    assert caught.value.code == "go.unsupported.pointer-storage"


def test_static_field_proof_rejects_local_and_pointer_derived_places():
    from pcc.frontends.go.packages import SourceInstance, analyze_source_instances
    from pcc.frontends.go.pointer_flow import NONNULL, StaticLifetime, static_scalar_field_fact

    source = """package main
    type State struct{ Flag bool }
    var X86 struct{ Nested State }
    func work(p *State) {
        var local State
        _ = &X86.Nested.Flag
        _ = &local.Flag
        if p != nil { _ = &p.Flag }
    }
    func main() {}
    """
    result = analyze_source_instances({"main": SourceInstance("main", (("proof.go", source),), {})},
                                      main_instance="main", target_triple="x86_64-unknown-linux-gnu")
    work = result["main"].program.functions[0]
    addresses = [stmt.exprs[0] for stmt in walk(work.body)
                 if stmt.kind == "=" and stmt.exprs[0].kind == "address"]
    assert len(addresses) == 3
    static, local, derived = [static_scalar_field_fact(expr.args[0]) for expr in addresses]
    assert static.nulls == NONNULL
    root, = static.lifetimes
    assert isinstance(root, StaticLifetime) and root.symbol.token.text == "X86"
    assert local is derived is None


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
def test_direct_static_scalar_field_reference_matches_official_go(tmp_path, monkeypatch):
    units = (("direct.go", DIRECT),)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units)
    binary = tmp_path / "owned"
    write_artifact(artifact, binary)
    reference = compile_reference(units, tmp_path / "official", compiler=os.environ["PCC_GO_REFERENCE"],
                                  cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert run(binary) == run(reference) == Execution(0, b"", b"")
