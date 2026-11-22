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


def test_static_option_catalog_metadata_does_not_open_native_globals_or_uses():
    from pcc.frontends.go.packages import SourceInstance, analyze_source_instances, parse_package
    from pcc.frontends.go.sema import Analyzer
    from pcc.frontends.go.slice_proof import StaticOptionCatalogRequest
    from pcc.frontends.go.types import SliceType

    source = """package main
type Item struct { Label string; Flag *bool; Enabled bool }
var catalog []Item
var Flags struct { A bool; B bool }
func hardware() int32 { return 1 }
func build() {
    catalog = []Item{{Label:"a", Flag:&Flags.A}}
    level := hardware()
    if level < 2 { catalog = append(catalog, Item{Label:"b", Flag:&Flags.B}) }
}
func main() {}
"""
    units = (("catalog.go", source),)
    request = StaticOptionCatalogRequest("catalog", "build", "hardware")
    with pytest.raises(UnsupportedGo, match="slice types require shared-runtime") as ordinary:
        compile_go(source, "catalog.go")
    assert ordinary.value.line == 3

    analyzer = Analyzer(parse_package(units, require_main=True), source_only=True,
                        static_option_catalogs=(request,))
    with pytest.raises(UnsupportedGo, match="proved global record slice needs owned initialization") as candidate:
        analyzer.analyze()
    assert candidate.value.line == 7
    symbol = analyzer.globals["catalog"]
    assert isinstance(symbol.type, SliceType) and symbol.type.element.name == "Item"
    assert symbol.static_option_catalog_proof.capacity == 2
    assert symbol.initializer is None and symbol.abi_symbol == ""

    instances = {"main": SourceInstance("main", units, {}, static_option_catalogs=(request,))}
    with pytest.raises(UnsupportedGo, match="proved global record slice needs owned initialization") as nested:
        analyze_source_instances(instances, main_instance="main", target_triple="x86_64-unknown-linux-gnu")
    assert nested.value.line == 7


def test_invalid_static_option_catalog_request_never_authorizes_declaration():
    from pcc.frontends.go.packages import SourceInstance, analyze_source_instances
    from pcc.frontends.go.slice_proof import StaticOptionCatalogRequest

    units = (("catalog.go", """package main
type Item struct { Label string; Flag *bool }
var catalog []Item
func main() {}
"""),)
    request = StaticOptionCatalogRequest("catalog", "unknownBuilder", "unknownLevel")
    with pytest.raises(UnsupportedGo, match="slice types require shared-runtime") as caught:
        analyze_source_instances({"main": SourceInstance("main", units, {}, static_option_catalogs=(request,))},
                                 main_instance="main", target_triple="x86_64-unknown-linux-gnu")
    assert caught.value.line == 3


CATALOG_RANGE_SOURCE = """package main
type Item struct { Label string; Flag *bool; Enabled bool }
var catalog []Item
var Flags struct { A bool }
func hardware() int32 { return 1 }
func use(key string) {
    for i := range catalog {
        if catalog[i].Label == key { catalog[i].Enabled = true }
    }
}
func build() {
    catalog = []Item{{Label:"a", Flag:&Flags.A}}
    level := hardware()
    if level < 2 { return }
}
func main() {}
"""


def test_static_catalog_index_range_checks_scalar_fields_before_storage_stop():
    from pcc.frontends.go.packages import parse_package
    from pcc.frontends.go.pointer_flow import validate_pointer_flow
    from pcc.frontends.go.sema import Analyzer
    from pcc.frontends.go.syntax import Program
    from pcc.frontends.go.slice_proof import StaticOptionCatalogRequest

    request = StaticOptionCatalogRequest("catalog", "build", "hardware")
    units = (("catalog_range.go", CATALOG_RANGE_SOURCE),)
    checker = Analyzer(parse_package(units, require_main=True), source_only=True,
                       static_option_catalogs=(request,))
    with pytest.raises(UnsupportedGo, match="proved global record slice needs owned initialization") as caught:
        checker.analyze()
    assert caught.value.line == 12  # the unadmitted builder write, not the index range
    loop = checker.functions["use"].body[0]
    assert loop.exprs[0].kind == "static_catalog_range"
    assert loop.init.symbols[0].type == "int"
    condition = loop.body[0]
    lhs = condition.exprs[0].args[0]
    assert lhs.type == "string" and lhs.args[0].kind == "static_catalog_index"
    write = condition.body[0].targets[0]
    assert write.type == "bool" and write.addressable and write.args[0].kind == "static_catalog_index"
    validate_pointer_flow(Program(checker.program.token, [checker.functions["use"]], []))


@pytest.mark.parametrize("replacement", [
    "i++; if catalog[i].Label == key {}",
    "if catalog[i+1].Label == key {}",
    "_ = catalog[i].Flag",
    "_ = &catalog[i].Enabled",
    "_ = catalog[i]",
    "touch(i)",
])
def test_static_catalog_index_range_rejects_aliases_and_changed_indices(replacement):
    from pcc.frontends.go.packages import parse_package
    from pcc.frontends.go.sema import Analyzer
    from pcc.frontends.go.slice_proof import StaticOptionCatalogRequest

    source = CATALOG_RANGE_SOURCE.replace(
        "if catalog[i].Label == key { catalog[i].Enabled = true }", replacement)
    analyzer = Analyzer(parse_package((("catalog_range.go", source),), require_main=True),
                        source_only=True, static_option_catalogs=(
                            StaticOptionCatalogRequest("catalog", "build", "hardware"),
                        ))
    with pytest.raises(UnsupportedGo, match="proved global record slice needs owned initialization") as caught:
        analyzer.analyze()
    assert caught.value.line == 7


CATALOG_COPY_SOURCE = CATALOG_RANGE_SOURCE.replace(
    """    for i := range catalog {
        if catalog[i].Label == key { catalog[i].Enabled = true }
    }""",
    """    for _, item := range catalog {
        if item.Enabled && !*item.Flag { print(item.Label) }
        *item.Flag = item.Enabled
    }""",
)


def test_static_catalog_copy_preserves_pointee_proof_without_materializing_record():
    from pcc.frontends.go.packages import parse_package
    from pcc.frontends.go.pointer_flow import validate_pointer_flow
    from pcc.frontends.go.sema import Analyzer
    from pcc.frontends.go.slice_proof import StaticOptionCatalogRequest
    from pcc.frontends.go.syntax import Program

    units = (("catalog_copy.go", CATALOG_COPY_SOURCE),)
    proof = StaticOptionCatalogRequest("catalog", "build", "hardware")
    checker = Analyzer(parse_package(units, require_main=True), source_only=True,
                       static_option_catalogs=(proof,))
    with pytest.raises(UnsupportedGo, match="proved global record slice needs owned initialization") as caught:
        checker.analyze()
    assert caught.value.line == 13
    consumer = checker.functions["use"]
    loop = consumer.body[0]
    assert loop.init.symbols[1].static_catalog_copy is checker.globals["catalog"]
    target = loop.body[1].targets[0]
    assert target.kind == "dereference"
    address = target.args[0]
    assert address.kind == "static_catalog_feature"
    assert address.catalog_pointees == (checker.globals["Flags"],)
    validate_pointer_flow(Program(checker.program.token, [consumer], []))


@pytest.mark.parametrize("bad", [
    "_ = item",
    "_ = item.Flag",
    "item.Flag = nil",
    "_ = &item.Enabled",
    "*item.Flag = true; item = Item{}",
    "touch(item)",
])
def test_static_catalog_copy_proof_never_allows_pointer_or_record_escape(bad):
    from pcc.frontends.go.packages import parse_package
    from pcc.frontends.go.sema import Analyzer
    from pcc.frontends.go.slice_proof import StaticOptionCatalogRequest

    text = CATALOG_COPY_SOURCE.replace("*item.Flag = item.Enabled", bad)
    checker = Analyzer(parse_package((("catalog_copy.go", text),), require_main=True), source_only=True,
                       static_option_catalogs=(StaticOptionCatalogRequest("catalog", "build", "hardware"),))
    with pytest.raises(UnsupportedGo, match="proved global record slice needs owned initialization") as caught:
        checker.analyze()
    assert caught.value.line == 7
