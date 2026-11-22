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


def test_bool_pointer_field_declaration_does_not_need_native_storage():
    source = "package main;type option struct{Feature *bool};func main(){}"
    ir = str(compile_go(source, "option.go"))
    assert "define void @__pcc_go_main" in ir and "alloca" not in ir


@pytest.mark.parametrize("rest", (
    "func main(){var value option;_=value}",
    "var Config option;func main(){}",
    "func main(){_=option{Feature:nil}}",
    "func read(value option){};func main(){}",
    "func get()option{return option{}};func main(){}",
))
def test_pointer_bearing_record_storage_remains_unadmitted(rest):
    source = "package main;type option struct{Feature *bool};" + rest
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


def test_selected_x86_option_builder_proves_catalog_capacity_and_static_fields():
    from pcc.frontends.go.packages import parse_package
    from pcc.frontends.go.slice_proof import bounded_static_option_builder

    recipe, _ = load_linux_normal_plan()
    manifest = json.loads(recipe)
    source = []
    for leaf in manifest["packages"]["internal/cpu"]["files"]["go"]:
        name = "src/internal/cpu/" + leaf
        original = (FIXTURE / name).read_bytes()
        assert manifest["files"][name] == [len(original), sha256(original)]
        source.append((name, original.decode()))
    proof = bounded_static_option_builder(parse_package(source, require_main=False),
                                          "doinit", "options", "getGOAMD64level")
    assert proof.record == "option" and proof.capacity == 23
    assert proof.initial_count == 8 and proof.thresholds == ((2, 5), (3, 5), (4, 5))
    assert tuple(proof.count_at(level) for level in range(1, 5)) == (23, 18, 13, 8)
    assert len(proof.feature_fields) == 23 and len(set(proof.feature_fields)) == 23
    assert {owner for owner, field in proof.feature_fields} == {"X86"}
    assert proof.unbound_exports == ("X86",)


@pytest.mark.parametrize("change", [
    ("&Flags.B", "&local"),
    ("&Flags.B", "&Flags.Number"),
    ('"b"', '"a"'),
    ("if level < 2", "for level < 2"),
    ("if level < 2", "if level < 2 && enabled()"),
    ("return }", "escape(config); return }"),
    ("return }", "_ = func() { escape(config) }; return }"),
])
def test_option_builder_rejects_unbounded_or_nonstatic_sources(change):
    from pcc.frontends.go.packages import parse_package
    from pcc.frontends.go.slice_proof import bounded_static_option_builder

    source = """package example
type Item struct { Label string; Flag *bool; Enabled bool }
var config []Item
var Flags struct { A bool; B bool; Number int }
func hardware() int32
func build() {
    config = []Item{{Label:"a", Flag:&Flags.A}}
    level := hardware()
    if level < 2 { config = append(config, Item{Label:"b", Flag:&Flags.B}) }
    return }
""".replace(*change)
    program = parse_package((("builder.go", source),), require_main=False)
    assert bounded_static_option_builder(program, "build", "config", "hardware") is None
