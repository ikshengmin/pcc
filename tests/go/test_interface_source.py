"""Basic interface declarations and canonical signatures, not runtime dispatch.

https://go.dev/ref/spec#Interface_types
https://go.dev/ref/spec#Type_identity
https://go.dev/ref/spec#Function_types
https://go.dev/ref/spec#Type_declarations
Authored ordinary, owned-native and same-input reference obligations. CI-only;
no source review constitutes execution acceptance.
"""
from copy import deepcopy
from pathlib import Path
import os

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go, compile_go_packages
from pcc.frontends.go.packages import parse_package
from pcc.frontends.go.sema import Analyzer
from pcc.frontends.go.records import underlying
from pcc.frontends.go.interface_methods import method_set
from tests.go.owned_execution import (
    Execution, compile_module, compile_owned, compile_reference, compile_reference_packages,
    compile_reference_rejection, run, write_artifact,
)
from tests.go.test_execution import forbid_external_compiler


FIXTURE = Path(__file__).parent / 'fixtures/interface_source/declarations.go'
RECOVERED_ORIGINAL = Path(__file__).parent / 'fixtures/source_interface_declarations.go'


def units(fixture=FIXTURE):
    return ((fixture.name, fixture.read_text()),)


def source(declarations, body=''):
    return 'package main;' + declarations + ';func main(){' + body + '}'


def analyze(original):
    analyzer = Analyzer(parse_package((('original.go', original),), require_main=True))
    analyzer.analyze()
    return analyzer


@pytest.mark.parametrize('fixture', (FIXTURE, RECOVERED_ORIGINAL))
def test_complete_declaration_original_has_owned_lowering(fixture, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(units(fixture)[0][1], fixture.name)


def test_named_identity_and_aliases_survive_deepcopy_and_recursive_signatures():
    analyzer = analyze(source('type N interface{Next()N};type A=N;type Other interface{Next()N}'))
    named = analyzer.types['N'].resolved
    assert analyzer.types['A'].resolved is named
    assert deepcopy(named) is named
    other = analyzer.types['Other'].resolved
    assert other is not named
    assert underlying(other) == underlying(named)


@pytest.mark.parametrize('declarations', [
    'type A B;type B interface{M(A)};type Alias=A',
    'type Alias=A;type B interface{M(A)};type A B',
    'type B interface{M(A)};type Alias=A;type A B',
])
def test_underlying_name_chains_do_not_copy_an_incomplete_placeholder(declarations):
    owner = analyze(source(declarations))
    a, b, alias = (owner.types[name].resolved for name in ('A', 'B', 'Alias'))
    assert a is alias and a is not b and a.underlying == b.underlying
    assert method_set(a).lookup('M').method.signature.parameters == (a,)


@pytest.mark.parametrize('declarations', [
    'type A = [1]B;type B A', 'type A = [0]B;type B A',
    'type A = struct{Value B};type B A', 'type B A;type A = struct{Value B}',
])
def test_alias_signature_anchors_do_not_legalize_recursive_value_containment(declarations):
    with pytest.raises(GoDiagnostic, match='invalid recursive type or alias cycle') as failure:
        compile_go(source(declarations))
    assert not isinstance(failure.value, UnsupportedGo)


@pytest.mark.parametrize('declarations', [
    'type I interface{M(interface{I})}', 'type I interface{M(*interface{I})}',
    'type I interface{M([]interface{I})}', 'type I interface{M(A)};type A=interface{I}',
    'type A=interface{I};type I interface{M(A)}',
])
def test_recursive_anonymous_signature_is_capability_not_invalid_source(declarations):
    with pytest.raises(UnsupportedGo, match='deferred method-set completion') as failure:
        compile_go(source(declarations))
    assert failure.value.code == 'go.unsupported.interface-types'


def test_outer_signature_does_not_hide_independent_embedding_cycle():
    with pytest.raises(GoDiagnostic, match='invalid recursive type or alias cycle') as failure:
        compile_go('package main;type Outer interface{M(interface{Bad})};'
                   'type Bad interface{Bad};func main(){}')
    assert not isinstance(failure.value, UnsupportedGo)


@pytest.mark.parametrize('declarations', [
    'type A interface{M(int)string};type B interface{A;M(int)string}',
    'type A interface{M()};type B interface{A};type C interface{A;B}',
    'type A interface{M(x,y int)(v,w string)}',
    'type A interface{M(_ int,_ int)(_ string,_ string)}',
    'type A interface{M(x [2]int)};type B interface{M([2]int)}',
    'type A interface{M(x ...int)};type B interface{M(...int)}',
    'type A interface{M()B};type B interface{M()A}',
    'type A=B;type B interface{M()A}',
    'type A interface{M(*A);N([]A)}',
    'type A interface{M()};type B A;type C interface{A;B}',
    'type A interface{};type B=any;func id[T any](v T)T{return v}',
])
def test_signature_only_declarations_do_not_require_runtime_storage(declarations):
    compile_go(source(declarations))


INVALID = (
    ('type A interface{M();M()}', 'duplicate method M'),
    ('type A interface{M(int)};type B interface{M(string)};type C interface{A;B}', 'duplicate method M'),
    ('type A interface{A}', 'invalid recursive type'),
    ('type A=B;type B=A', 'invalid recursive type'),
    ('type A interface{M(x int,x int)}', 'x redeclared'),
    ('type A interface{M()(x int,x int)}', 'x redeclared'),
    ('type A interface{M(x int)(x int)}', 'x redeclared'),
    ('type A interface{_()}', 'methods must have a unique non-blank name'),
)


@pytest.mark.parametrize('declarations,reference_message', INVALID)
def test_invalid_interface_originals_are_validity_errors_before_ir(declarations, reference_message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid source reached lowering'))
    with pytest.raises(GoDiagnostic) as failure:
        compile_go(source(declarations))
    assert not isinstance(failure.value, UnsupportedGo)


RUNTIME_BOUNDARIES = (
    source('type I interface{M()}', 'var i I;_=i'),
    source('type I interface{}', 'var i I;_=i'),
    source('', 'var i any;_=i'),
    source('type I interface{M()};func unused(i I){}'),
    source('type I interface{M()};func unused()I{return nil}'),
    source('type I interface{M()}', '_=I(nil)'),
    source('type I interface{M()}', 'var i [0]I;_=i'),
    source('type I interface{M()};type S struct{value I}'),
    source('type I interface{M()}', 'var p *I;_=p'),
    source('type I interface{M()};type T int;func(T)M(){}', 'var i I=T(0);_=i'),
    source('type I interface{M()};type T int;func(*T)M(){}', 'var p *T;var i I=p;_=i'),
)


@pytest.mark.parametrize('original', RUNTIME_BOUNDARIES)
def test_general_interface_storage_never_reaches_lowerer(original, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('runtime obligation erased'))
    with pytest.raises(UnsupportedGo):
        compile_go(original)


@pytest.mark.parametrize('signature', [
    'M(Box[int])', 'M(x Box[int])', 'M()Box[int]', 'M(*Box[int])',
    'M([]Box[int])', 'M(...Box[int])', 'M(Box[Box[int]])', 'M(Box[int,])',
])
def test_generic_signature_application_is_a_capability_boundary(signature):
    with pytest.raises(UnsupportedGo) as failure:
        compile_go(source('type I interface{' + signature + '};type Box[T any] struct{V T}'))
    assert failure.value.code == 'go.unsupported.generic-boundaries'


@pytest.mark.parametrize('signature', ['M(Box[])', 'M(Box[int string])', 'M(Box[*])'])
def test_malformed_type_arguments_remain_syntax_errors(signature):
    with pytest.raises(GoDiagnostic) as failure:
        compile_go(source('type I interface{' + signature + '}'))
    assert not isinstance(failure.value, UnsupportedGo)


PACKAGES = {
    'left': (('types.go', 'package same;type N interface{Next()N};type Hidden interface{hidden()}'),),
    'right': (('types.go', 'package same;type N interface{Next()N};type Hidden interface{hidden()}'),),
    'main': (('main.go', 'package main;import a "left";import b "right";type A=a.N;type B=b.N;type Both interface{a.Hidden;b.Hidden};func main(){}'),),
}


def test_import_path_not_package_spelling_owns_private_interface_names():
    compile_go_packages(PACKAGES)


GENERIC_ROOT = FIXTURE.parent / 'generic_packages'
GENERIC_PACKAGES = {
    'example.test/p': (('types.go', (GENERIC_ROOT / 'p/types.go').read_text()),),
    'main': (('main.go', (GENERIC_ROOT / 'main.go').read_text()),),
}


def test_qualified_generic_embedding_keeps_explicit_capability_boundary():
    with pytest.raises(UnsupportedGo) as failure:
        compile_go_packages(GENERIC_PACKAGES)
    assert failure.value.code == 'go.unsupported.generic-boundaries'


@pytest.mark.integration
@pytest.mark.parametrize('fixture', (FIXTURE, RECOVERED_ORIGINAL))
def test_complete_declaration_original_executes_owned_native(fixture, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units(fixture), observe_entry=('Result', ()))
    binary = tmp_path / 'declarations-owned'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, (42).to_bytes(8, 'little'), b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('fixture', (FIXTURE, RECOVERED_ORIGINAL))
def test_identical_declaration_original_has_real_reference_execution(fixture, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units(fixture))
    binary = tmp_path / 'declarations-main'
    write_artifact(artifact, binary)
    reference = compile_reference(units(fixture), tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert (reference.parent / fixture.name).read_bytes() == fixture.read_bytes()
    assert run(binary) == run(reference) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_original_package_declarations_have_reference_execution(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_module(compile_go_packages(PACKAGES))
    owned = tmp_path / 'packages-owned'
    write_artifact(artifact, owned)
    reference = compile_reference_packages(PACKAGES, tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    for package, inputs in PACKAGES.items():
        for filename, original in inputs:
            assert (reference.parent / 'gopath/src' / package / filename).read_bytes() == original.encode()
    assert run(owned) == run(reference) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_qualified_generic_embedding_has_reference_only_execution(tmp_path):
    reference = compile_reference_packages(GENERIC_PACKAGES, tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    for package, inputs in GENERIC_PACKAGES.items():
        for filename, original in inputs:
            assert (reference.parent / 'gopath/src' / package / filename).read_bytes() == original.encode()
    assert run(reference) == Execution(0, b'', b'')


VALID_UNIMPLEMENTED = tuple(FIXTURE.parent / 'valid_but_unimplemented' / name for name in (
    'recursive_anonymous_signature.go', 'generic_signature_instantiation.go'))


@pytest.mark.parametrize('fixture', VALID_UNIMPLEMENTED)
def test_preserved_valid_originals_keep_explicit_capability_boundary(fixture):
    with pytest.raises(UnsupportedGo):
        compile_go(fixture.read_text(), fixture.name)


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('fixture', VALID_UNIMPLEMENTED)
def test_preserved_valid_frontiers_have_reference_only_execution(fixture, tmp_path):
    reference = compile_reference(units(fixture), tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert (reference.parent / fixture.name).read_bytes() == fixture.read_bytes()
    assert run(reference) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('declarations,reference_message', INVALID)
def test_invalid_originals_have_attributed_official_rejections(declarations, reference_message, tmp_path):
    original = source(declarations)
    compile_reference_rejection((('original.go', original),), tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'],
        diagnostics=(reference_message,))
    assert (tmp_path / 'reference/original.go').read_bytes() == original.encode()
