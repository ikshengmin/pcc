"""Nominal types and struct values, not pointers/methods or a foreign Go ABI."""
import hashlib
import json
import os

import pytest

from pcc.frontends.go import GoDiagnostic, compile_go, compile_go_packages
from tests.go.owned_execution import Execution, compile_owned, compile_reference, run, write_artifact
from tests.go.test_execution import forbid_external_compiler, units


def source(declarations, body=""):
    return "package main;" + (declarations + ";" if declarations else "") + "func main(){" + body + "}"


INVALID = [
    ("type A int;type B int", "var a A;var b B;a=b;_=a", "cannot use"),
    ("type A int", "var a A;var b int;a=b;_=a", "cannot use"),
    ("type A string;type B string", "var a A;var b B;_=a==b", "mismatched string"),
    ("type A uint8", "var a A=256;_=a", "overflows A"),
    ("type A struct{X uint64};type B struct{X uint64}", "var a A;var b B;a=b;_=a", "cannot use"),
    ("type A struct{X uint64};type B struct{X int64}", "_=B(A{})", "invalid conversion"),
    ("type A struct{X uint64}", "_=A{Unknown:1}", "unknown or invalid"),
    ("type A struct{X,Y uint64}", "_=A{1}", "every field"),
    ("type A struct{X uint64}", "_=A{1,2}", "every field"),
    ("type A struct{X,Y uint64}", "_=A{X:1,2}", "mix keyed"),
    ("type A struct{X uint64}", "_=A{X:1,X:2}", "duplicate struct literal"),
    ("type A struct{X uint64}", "_=A{(X):1}", "unknown or invalid"),
    ("type A struct{X uint64}", "_=A{X:true}", "cannot use"),
    ("type A struct{X uint64}", "A{}.X=1", "non-addressable"),
    ("type A struct{X uint64};func f()A{return A{}}", "f().X++", "non-addressable"),
    ("type A struct{X uint64}", "var a A;_=a.Y", "unknown struct field"),
    ("type A struct{X uint64}", "var a A;_=a==a", "unsupported comparison"),
    ("type A struct{X uint64}", "const a=A{};_=a", "not a constant"),
    ("type A struct{X uint64}", "A:=1;_=A{}", "not a type"),
    ("type A int", "A:=1;_=A(2)", "cannot call non-function"),
    ("type A struct{X uint64};var a=A{X:f()};func f()uint64{return 1}", "", "shared-runtime initialization"),
    ("type A int;var A int", "", "duplicate package declaration"),
    ("type A int;func A(){}", "", "duplicate package declaration"),
    ("type A int;type A int", "", "duplicate package declaration"),
    ("type A = B;type B = A", "", "recursive type or alias cycle"),
    ("type A struct{A A}", "", "recursive type or alias cycle"),
    ("type A struct{B B};type B struct{A A}", "", "recursive type or alias cycle"),
    ("type A Missing", "", "undefined type"),
    ("type A struct{X int;X int}", "", "duplicate struct field"),
    ("type A struct{_ int}", "", "blank struct fields"),
    ("type A struct{int}", "", "embedded fields"),
    ('type A struct{X int `json:"x"`}', "", "struct tags"),
    ("", "type A int", "local type declarations"),
    ("type A struct{X int}", "var a [2]A;_=a", "aggregate elements"),
    ("type A [2]int", "var a A;var i int;_=a[i]", "bounds-panic"),
]


@pytest.mark.parametrize("declarations,body,message", INVALID)
def test_nominal_and_record_boundaries_are_source_diagnostics(declarations, body, message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, "lower", lambda *args: pytest.fail("invalid record reached IR"))
    with pytest.raises(GoDiagnostic, match=message):
        compile_go(source(declarations, body))


@pytest.mark.parametrize("declarations,body", [
    ("type(A uint64;B=A;C B);var b B=7", "var a A=b;_=C(a)"),
    ("type A = struct{X uint64}", "var a A;var b struct{X uint64};a=b;_=a"),
    ("type A = [2]uint64;type B A", "var a A;var b B;a=b;_=a"),
    ("type A bool", "var a A=true;if a{};_=!a"),
    ("type A string", 'const a A="x";_=a+"y";_=A(65)'),
    ("type A int8", "var a A=-1;_=int64(a);_=[2]A{1,a}"),
    ("type ()", ""),
    ("type A struct{};func f()(A,A){return A{},A{}}", "a,b:=f();_=a;_=b"),
])
def test_alias_and_underlying_assignability_uses_identity_not_physical_width(declarations, body, monkeypatch):
    forbid_external_compiler(monkeypatch)
    assert str(compile_go(source(declarations, body))).startswith("; ModuleID")


PROJECT = {
    "main": [("main.go", 'package main;import "lib";func main(){p:=records.Point{X:7};q:=records.Alias(p);if records.Read(q)!=7{for{}};p.X=9;if records.Read(q)!=7{for{}}}')],
    "lib": [("lib.go", 'package records;type Point struct{X uint64; hidden uint64};type Alias=Point;func Read(p Point)uint64{return p.X+p.hidden}')],
}


@pytest.mark.parametrize("body,message", [
    ("_=records.Point{hidden:1}", "unexported struct field"),
    ("_=records.Point{1,2}", "unexported struct field"),
    ("p:=records.Point{};_=p.hidden", "unexported struct field"),
    ("var p records.hidden;_=p", "unexported package member"),
])
def test_imported_type_fields_respect_package_identity(body, message):
    project = dict(PROJECT)
    project["main"] = [("main.go", 'package main;import "lib";func main(){'+body+'}')]
    with pytest.raises(GoDiagnostic, match=message):
        compile_go_packages(project)


RAW = [
    ("MixedFields", 0x0123456789abcdef), ("LargeArgument", 0x8877665544332211),
    ("Scalar", 0xfedcba9876543210), ("Narrow", 127), ("Boolean", 71), ("String", 2),
    ("CopyLocal", 0xfedcba9876543210), ("CopyArgument", 799), ("TemporaryField", 0xfedcba9876543210),
    ("LargeResult", 0x8877665544332211), ("NestedCopy", 0xfedcba9876543210),
    ("ArrayField", 0x8877665544332211), ("NamedArray", 0xfedcba9876543210),
    ("Explicit", 0xfedcba9876543210), ("Anonymous", 0xfedcba9876543210),
    ("Zero", 0), ("TextCopy", 0x8877665544332211), ("Order", 21), ("Parallel", 21),
    ("Compound", 202), ("Snapshot", 0xfedcba9876543210), ("EmptyValue", 73), ("Multi", 18),
    ("UnnamedParameters", 79), ("Shadow", 0xfedcba9876543210),
]


@pytest.mark.integration
@pytest.mark.parametrize("name,expected", RAW)
def test_original_record_helpers_have_independent_full_width_observations(name, expected, tmp_path, monkeypatch):
    original = units(("records.go",))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(original, observe_entry=(name, ()))
    binary = tmp_path / "records-native"
    write_artifact(artifact, binary)
    actual = run(binary)
    raw = expected.to_bytes(8, "little")
    (tmp_path / "raw-record-observation.json").write_text(json.dumps({
        "owner": artifact.owner, "function": name, "arguments": [], "expected_hex": raw.hex(),
        "stdout_hex": actual.stdout.hex(), "returncode": actual.returncode, "stderr_hex": actual.stderr.hex(),
        "source_sha256": hashlib.sha256(original[0][1].encode()).hexdigest(),
        "executable_sha256": hashlib.sha256(artifact.executable).hexdigest(),
    }, indent=2)+"\n")
    assert actual == Execution(0, raw, b"")


@pytest.mark.integration
def test_original_record_program_main_executes(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units(("records.go",)))
    binary = tmp_path / "records-main"
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, b"", b"")


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
def test_official_go_receives_the_identical_original_record_program(tmp_path):
    original = units(("records.go",))
    reference = compile_reference(original, tmp_path / "reference", compiler=os.environ["PCC_GO_REFERENCE"], cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert run(reference) == Execution(0, b"", b"")
    assert (reference.parent / "records.go").read_bytes() == original[0][1].encode()


def test_original_record_program_emits_real_arm64_darwin_object(monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    forbid_external_compiler(monkeypatch)
    module = compile_go(units(("records.go",))[0][1], target_triple="arm64-apple-darwin")
    assert emit_owned_object(str(module), "arm64-apple-darwin")[:4] == b"\xcf\xfa\xed\xfe"


@pytest.mark.integration
def test_original_imported_record_project_runs_owned(tmp_path, monkeypatch):
    from tests.go.owned_execution import compile_module
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_module(compile_go_packages(PROJECT))
    binary = tmp_path / "project"
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, b"", b"")


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
def test_official_go_receives_identical_imported_record_project(tmp_path):
    from tests.go.owned_execution import compile_reference_packages
    reference = compile_reference_packages(PROJECT, tmp_path / "reference", compiler=os.environ["PCC_GO_REFERENCE"], cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert run(reference) == Execution(0, b"", b"")
    for package, items in PROJECT.items():
        for filename, text in items:
            assert (reference.parent / "gopath/src" / package / filename).read_bytes() == text.encode()


def test_empty_type_group_still_precedes_later_import_as_a_declaration():
    with pytest.raises(GoDiagnostic, match="imports must precede"):
        compile_go('package main;type ();import "lib";func main(){}')


def test_record_expansion_limit_is_explicit_before_ir():
    with pytest.raises(GoDiagnostic, match="4096 scalar slots"):
        compile_go(source("type R struct{A,B [4096]uint64}"))


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
@pytest.mark.parametrize("index,reference_message", [(0,"cannot use"),(2,"mismatched types"),(4,"cannot use"),(8,"too many values")])
def test_official_go_rejects_the_same_nominal_type_or_literal_error(index, reference_message, tmp_path):
    declarations, body, message = INVALID[index]
    original = source(declarations, body)
    with pytest.raises(GoDiagnostic, match=message):
        compile_go(original)
    with pytest.raises(AssertionError, match=reference_message):
        compile_reference((("invalid.go",original),), tmp_path/"reference", compiler=os.environ["PCC_GO_REFERENCE"], cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert (tmp_path/"reference/invalid.go").read_bytes() == original.encode()
    assert not (tmp_path/"reference/oracle").exists()


def test_import_alias_conflicts_with_sibling_file_type_declaration():
    from tests.go.test_packages import conflicting_sibling_package
    with pytest.raises(GoDiagnostic, match="import name conflicts"):
        compile_go_packages(conflicting_sibling_package("type arithmetic int"))
