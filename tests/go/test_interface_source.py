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
import hashlib
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
NAMED_ANCHORS = FIXTURE.parent / 'named_signature_anchors.go'


def units(fixture=FIXTURE):
    return ((fixture.name, fixture.read_text()),)


def source(declarations, body=''):
    prefix = 'package main;' + (declarations + ';' if declarations else '')
    return prefix + 'func main(){' + body + '}'


def test_source_helper_preserves_complete_empty_declaration_runtime_original():
    original = source('', 'var i any;_=i')
    assert original == 'package main;func main(){var i any;_=i}'
    assert hashlib.sha256(original.encode()).hexdigest() == '2bd427cb037ffd7d4342ef88d09d6939bf4c4f08db74edc3b3edb2c2669f23e5'
    # Preserve the exact former malformed helper output as an ordinary syntax
    # error, not evidence that the intended interface-runtime path was reached.
    malformed = 'package main;;func main(){var i any;_=i}'
    with pytest.raises(GoDiagnostic, match='empty package declarations') as failure:
        compile_go(malformed)
    assert not isinstance(failure.value, UnsupportedGo)


def analyze(original):
    analyzer = Analyzer(parse_package((('original.go', original),), require_main=True))
    analyzer.analyze()
    return analyzer


@pytest.mark.parametrize('fixture', (FIXTURE, RECOVERED_ORIGINAL, NAMED_ANCHORS))
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


def test_named_signature_anchors_stop_anonymous_expansion_without_erasing_identity():
    owner = analyze(NAMED_ANCHORS.read_text())
    direct = owner.types['Direct'].resolved
    assert owner.types['Alias'].resolved is direct
    signature = method_set(direct).lookup('Next').method.signature
    assert signature.parameters == (direct,) and signature.result is direct
    wrapped = method_set(owner.types['Wrapped'].resolved).lookup('Use').method.signature
    assert all(parameter.element is direct for parameter in wrapped.parameters)
    assert owner.types['ForwardAlias'].resolved is owner.types['Forward'].resolved
    anonymous = method_set(owner.types['WithAnonymous'].resolved).lookup('Use').method.signature.parameters[0]
    assert method_set(anonymous).lookup('Next').method.signature.result is owner.types['Left'].resolved


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


ANONYMOUS_CYCLES = (
    'type I interface{M(interface{I})}', 'type I interface{M(*interface{I})}',
    'type I interface{M([]interface{I})}', 'type I interface{M(A)};type A=interface{I}',
    'type A=interface{I};type I interface{M(A)}',
    'type I interface{M([0]interface{I})}',
    'type I interface{M(struct{F interface{I}})}',
    'type I interface{M(...interface{I})}',
    'type I interface{M() interface{I}}',
    'type I interface{M() *interface{I}}',
    'type I interface{M() (int,interface{I})}',
    'type A interface{M(interface{B})};type B interface{N(interface{A})}',
    'type I interface{M(interface{J})};type J interface{I}',
)


@pytest.mark.parametrize('declarations', ANONYMOUS_CYCLES)
def test_recursive_anonymous_signatures_are_invalid_before_ir(declarations, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('anonymous cycle reached IR'))
    with pytest.raises(GoDiagnostic, match='invalid recursive type: anonymous interface') as failure:
        compile_go(source(declarations))
    assert not isinstance(failure.value, UnsupportedGo)


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


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_corrected_empty_declaration_runtime_original_is_reference_only(tmp_path):
    original = source('', 'var i any;_=i')
    with pytest.raises(UnsupportedGo) as failure:
        compile_go(original)
    assert failure.value.code == 'go.unsupported.interface-types'
    reference = compile_reference((('original.go', original),), tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert reference.is_file()
    assert (reference.parent / 'original.go').read_bytes() == original.encode()


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
@pytest.mark.parametrize('fixture,expected', (
    (FIXTURE, 42), (RECOVERED_ORIGINAL, 42), (NAMED_ANCHORS, 0xfedcba9876543210),
))
def test_complete_declaration_original_executes_owned_native(fixture, expected, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units(fixture), observe_entry=('Result', ()))
    binary = tmp_path / 'declarations-owned'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, expected.to_bytes(8, 'little'), b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('fixture', (FIXTURE, RECOVERED_ORIGINAL, NAMED_ANCHORS))
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


# Preserve the exact formerly misclassified original at its historical path.
# Actual Go 1.27.3 compilation rejects its anonymous interface cycle, as do
# official issue56103/bug398 compiler tests; it is not a valid runtime frontier.
ANONYMOUS_CYCLE_ORIGINAL = FIXTURE.parent / 'valid_but_unimplemented/recursive_anonymous_signature.go'
VALID_UNIMPLEMENTED = (FIXTURE.parent / 'valid_but_unimplemented/generic_signature_instantiation.go',)


def test_original_recursive_anonymous_fixture_remains_unchanged_and_invalid(monkeypatch):
    original = ANONYMOUS_CYCLE_ORIGINAL.read_bytes()
    assert original == b'package main\n\ntype Recursive interface {\n\tVisit(interface { Recursive })\n}\n\nfunc main() {}\n'
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid original reached IR'))
    with pytest.raises(GoDiagnostic, match='invalid recursive type: anonymous interface') as failure:
        compile_go(original.decode(), ANONYMOUS_CYCLE_ORIGINAL.name)
    assert not isinstance(failure.value, UnsupportedGo)


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_anonymous_cycle_originals_have_official_source_rejections(tmp_path):
    originals = [('original-' + str(index) + '.go', source(declarations))
                 for index, declarations in enumerate(ANONYMOUS_CYCLES)]
    originals.append((ANONYMOUS_CYCLE_ORIGINAL.name, ANONYMOUS_CYCLE_ORIGINAL.read_text()))
    for index, (filename, original) in enumerate(originals):
        with pytest.raises(GoDiagnostic, match='invalid recursive type: anonymous interface') as failure:
            compile_go(original, filename)
        assert not isinstance(failure.value, UnsupportedGo)
        directory = tmp_path / str(index)
        receipt = compile_reference_rejection(((filename, original),), directory,
            compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'],
            diagnostics='invalid recursive type')
        assert receipt['classification'] == 'normal_source_rejection'
        assert (directory / filename).read_bytes() == original.encode()


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
