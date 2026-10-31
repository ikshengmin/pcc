"""Value receiver methods use direct private Go calls, not interface dispatch."""
import hashlib
import json
import os

import pytest

from pcc.frontends.go import GoDiagnostic, compile_go, compile_go_packages
from tests.go.owned_execution import Execution, compile_owned, compile_reference, run, write_artifact
from tests.go.test_execution import forbid_external_compiler, units
from tests.go.test_records import source


INVALID = [
    ("func(int) M(){}", "", "defined type in the same package"),
    ("type T = uint64;func(T) M(){}", "", "defined type in the same package"),
    ("type T struct{M int};func(T) M(){}", "", "conflicts with struct field"),
    ("type T int;func(T) M(){};func(T) M(){}", "", "duplicate method"),
    ("type T int;type A=T;func(T) M(){};func(A) M(){}", "", "duplicate method"),
    ("type T int;func(t T) M(t int){}", "", "duplicate parameter"),
    ("type T int;func(t T) M()(t int){return}", "", "duplicate parameter or result"),
    ("type T int;func(a,b T) M(){}", "", "exactly one"),
    ("type T int;func(*T) M(){}", "", "pointer receivers"),
    ("type T int;func(t *T) M(){}", "", "pointer receivers"),
    ("type T int;func(T) _(){}", "", "blank method"),
    ("type T int;func(T) M(){};type U T", "var u U;u.M()", "no method"),
    ("type T int;func(T) M(){}", "M()", "undefined function"),
    ("type T int;func(T) M(x int){}", "T(1).M()", "argument count"),
    ("type T int;func(T) M(x int){}", "T.M(1)", "argument count"),
    ("type T int;func(T) M(){}", "T.M(int(1))", "cannot use"),
    ("type T int;func(T) M(){}", "f:=T(1).M;_=f", "stored method values"),
    ("type T int;func(T) M(){}", "f:=T.M;_=f", "first-class method expressions"),
]


@pytest.mark.parametrize("declarations,body,message", INVALID)
def test_method_sets_and_receivers_reject_invalid_or_unimplemented_source(declarations, body, message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer,"lower",lambda *args:pytest.fail("invalid method reached IR"))
    with pytest.raises(GoDiagnostic,match=message):compile_go(source(declarations,body))


def test_original_value_receiver_declaration_graduates_without_rewriting():
    # Exact program formerly diagnosed by the record-only capability boundary.
    compile_go(source("type A int;func(a A) F(){}"))


PROJECT={
 "main":[("main.go",'package main;import "lib";func main(){p:=records.Point{X:7};if p.Add(11)!=18||records.Point.Add(p,13)!=20{for{}};if p.X!=7{for{}}}')],
 "lib":[("lib.go",'package records;type Point struct{X uint64};func(p Point)Add(n uint64)uint64{p.X+=n;return p.X};func(p Point)hidden()uint64{return p.X}')],
}


@pytest.mark.parametrize("declaration,body,message", [
 ("func(records.Point) Bad(){}", "", "same package"),
 ("type A=records.Point;func(A) Bad(){}", "", "same package"),
 ("", "p:=records.Point{};_=p.hidden()", "unexported method"),
 ("", "_=records.Point.hidden(records.Point{})", "unexported method"),
])
def test_imported_methods_keep_base_type_package_ownership(declaration, body, message):
    project=dict(PROJECT)
    project['main']=[('main.go','package main;import "lib";'+(declaration+';' if declaration else '')+'func main(){'+body+'}')]
    with pytest.raises(GoDiagnostic,match=message):compile_go_packages(project)


RAW=[('ValueCopy',710),('Temporary',0xfedcba9876543210),('MethodExpression',0xfedcba9876543210),
 ('AliasMethod',77),('Unnamed',152),('MultiReturn',713),('MultiArgument',131),
 ('ReceiverSnapshot',0xfedcba9876543210),('LargeReceiver',0x8877665544332211),
 ('ScalarReceiver',0xfdb97530eca86420),('BooleanReceiver',83),('StringReceiver',3),
 ('Recursion',18),('Namespaces',22),('PredeclaredMethod',19),('Parenthesized',48)]


@pytest.mark.integration
@pytest.mark.parametrize('name,expected',RAW)
def test_original_value_methods_have_independent_full_width_observation(name,expected,tmp_path,monkeypatch):
    original=units(('methods.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied);artifact=compile_owned(original,observe_entry=(name,()))
    binary=tmp_path/'method-native';write_artifact(artifact,binary);actual=run(binary)
    raw=expected.to_bytes(8,'little')
    (tmp_path/'raw-method-observation.json').write_text(json.dumps({'owner':artifact.owner,'function':name,'arguments':[],
      'expected_hex':raw.hex(),'stdout_hex':actual.stdout.hex(),'returncode':actual.returncode,'stderr_hex':actual.stderr.hex(),
      'source_sha256':hashlib.sha256(original[0][1].encode()).hexdigest(),
      'executable_sha256':hashlib.sha256(artifact.executable).hexdigest()},indent=2)+'\n')
    assert actual==Execution(0,raw,b'')


@pytest.mark.integration
def test_original_method_program_main_executes(tmp_path,monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied);artifact=compile_owned(units(('methods.go',)))
    binary=tmp_path/'methods-main';write_artifact(artifact,binary)
    assert run(binary)==Execution(0,b'',b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_official_go_receives_identical_original_methods(tmp_path):
    original=units(('methods.go',))
    reference=compile_reference(original,tmp_path/'reference',compiler=os.environ['PCC_GO_REFERENCE'],cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert run(reference)==Execution(0,b'',b'')
    assert (reference.parent/'methods.go').read_bytes()==original[0][1].encode()


def test_original_methods_have_real_arm64_darwin_object(monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    forbid_external_compiler(monkeypatch)
    module=compile_go(units(('methods.go',))[0][1],target_triple='arm64-apple-darwin')
    assert emit_owned_object(str(module),'arm64-apple-darwin')[:4]==b'\xcf\xfa\xed\xfe'


@pytest.mark.integration
def test_original_imported_method_project_executes(tmp_path,monkeypatch):
    from tests.go.owned_execution import compile_module
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied);artifact=compile_module(compile_go_packages(PROJECT))
    binary=tmp_path/'method-project';write_artifact(artifact,binary)
    assert run(binary)==Execution(0,b'',b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_official_go_receives_identical_imported_method_project(tmp_path):
    from tests.go.owned_execution import compile_reference_packages
    reference=compile_reference_packages(PROJECT,tmp_path/'reference',compiler=os.environ['PCC_GO_REFERENCE'],cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert run(reference)==Execution(0,b'',b'')
    for package,items in PROJECT.items():
        for name,text in items:assert (reference.parent/'gopath/src'/package/name).read_bytes()==text.encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('index,reference_message', [
    (0,'cannot define new methods'), (2,'field and method with the same name'),
    (3,'already declared'), (5,'redeclared'), (11,'undefined'),
])
def test_official_go_rejects_identical_invalid_method_declarations(index,reference_message,tmp_path):
    declarations,body,message=INVALID[index]
    original=source(declarations,body)
    with pytest.raises(GoDiagnostic,match=message):compile_go(original)
    with pytest.raises(AssertionError,match=reference_message):
        compile_reference((('invalid.go',original),),tmp_path/'reference',compiler=os.environ['PCC_GO_REFERENCE'],cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert (tmp_path/'reference/invalid.go').read_bytes()==original.encode()
    assert not (tmp_path/'reference/oracle').exists()
