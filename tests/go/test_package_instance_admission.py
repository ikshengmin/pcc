"""Exact build instances remain separate through owned semantic admission."""
import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo


MAIN = "_project.test"
ORDINARY = "example.org/value"
VARIANT = "example.org/value [_project.test]"


def units(name, source):
    return ((name + ".go", source),)


def test_each_import_uses_the_selected_instance_and_abi_owner():
    from pcc.frontends.go.packages import SourceInstance, analyze_source_instances

    graph = {
        MAIN: SourceInstance("main", units("main", 'package main;import "left";import "right";func main(){_=left.V()+right.V()}'),
                             {"left": "left", "right": "right"}),
        "left": SourceInstance("left", units("left", f'package left;import "{ORDINARY}";func V()int{{return value.Code()}}'),
                               {ORDINARY: ORDINARY}),
        "right": SourceInstance("right", units("right", f'package right;import "{ORDINARY}";func V()int{{return value.Code()}}'),
                                {ORDINARY: VARIANT}),
        ORDINARY: SourceInstance(ORDINARY, units("ordinary", "package value;func Code()int{return 13}"), {}),
        VARIANT: SourceInstance(ORDINARY, units("variant", "package value;func Code()int{return 29}"), {}),
    }
    result = analyze_source_instances(graph, main_instance=MAIN, target_triple="x86_64-unknown-linux-gnu")
    assert result[ORDINARY].program.functions[0].abi_symbol != result[VARIANT].program.functions[0].abi_symbol
    for consumer, dependency in (("left", ORDINARY), ("right", VARIANT)):
        returned = result[consumer].program.functions[0].body[0].exprs[0]
        assert returned.symbol is result[dependency].program.functions[0]
    graph["right"] = SourceInstance("right", graph["right"].units, {})
    with pytest.raises(GoDiagnostic, match="exact test import binding is missing"):
        analyze_source_instances(graph, main_instance=MAIN, target_triple="x86_64-unknown-linux-gnu")


def test_original_runtime_source_is_not_an_owned_test_provider():
    from pcc.frontends.go.packages import SourceInstance, analyze_source_instances

    runtime = "runtime [_project.test]"
    graph = {
        MAIN: SourceInstance("main", units("main", 'package main;import _ "runtime";func main(){}'), {"runtime": runtime}),
        runtime: SourceInstance("runtime", units("runtime", "package runtime;func Pretend(){}"), {}),
    }
    with pytest.raises(UnsupportedGo, match="runtime/intrinsic owner is unavailable") as caught:
        analyze_source_instances(graph, main_instance=MAIN, target_triple="x86_64-unknown-linux-gnu")
    assert caught.value.code == "go.unsupported.owned-runtime-surface"


def test_one_argument_linkname_records_source_owner_but_has_no_link_admission():
    from pcc.frontends.go import compile_go
    from pcc.frontends.go.packages import SourceInstance, analyze_source_instances

    source = 'package main\nimport _ "unsafe"\n//go:linkname Flag\nvar Flag int\nfunc main(){Flag=8}\n'
    graph = {
        MAIN: SourceInstance("main", units("link", source), {"unsafe": "unsafe"}),
        "unsafe": SourceInstance("unsafe", (), {}),
    }
    result = analyze_source_instances(graph, main_instance=MAIN, target_triple="x86_64-unknown-linux-gnu")
    assert result[MAIN].linkname_exports == {"Flag": "variable"}
    assert result[MAIN].globals["Flag"].linkname is None
    assert "unsafe" not in result
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(source)
    assert caught.value.code == "go.unsupported.linkname-directives"


def test_source_only_linkname_requires_file_local_unsafe_import_and_real_definition():
    from pcc.frontends.go.packages import SourceInstance, analyze_source_instances

    no_unsafe = (("a.go", "package main\n//go:linkname Flag\nvar Flag int\nfunc main(){}\n"),
                 ("b.go", 'package main\nimport _ "unsafe"\n'))
    with pytest.raises(GoDiagnostic, match='only allowed in Go files that import "unsafe"'):
        analyze_source_instances(
            {MAIN: SourceInstance("main", no_unsafe, {"unsafe": "unsafe"}), "unsafe": SourceInstance("unsafe", (), {})},
            main_instance=MAIN, target_triple="x86_64-unknown-linux-gnu",
        )
    missing = (("x.go", 'package main\nimport _ "unsafe"\n//go:linkname Flag\nfunc main(){}\n'),)
    with pytest.raises(GoDiagnostic, match="declared function or variable"):
        analyze_source_instances(
            {MAIN: SourceInstance("main", missing, {"unsafe": "unsafe"}), "unsafe": SourceInstance("unsafe", (), {})},
            main_instance=MAIN, target_triple="x86_64-unknown-linux-gnu",
        )
