"""Blank package types retain checking without a binding; execution UNRUN.

https://go.dev/ref/spec#Declarations_and_scope
https://go.dev/ref/spec#Type_declarations
https://go.dev/ref/spec#Import_declarations
The complete official issue8079 is compile-only package p. An explicitly
authored constant/main companion makes a closed graph without editing it.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go, compile_go_packages
from pcc.frontends.go.packages import parse_package
from pcc.frontends.go.records import DefinedType, InterfaceType
from pcc.frontends.go.sema import Analyzer
from tests.go.owned_execution import (
    Execution, compile_module, compile_owned, compile_reference, compile_reference_packages,
    compile_reference_packages_rejection, compile_reference_rejection, write_artifact,
)
from tests.go.test_diagnostics import GRADUATED_BLANK_TYPE_CASE
from tests.go.test_execution import forbid_external_compiler
from tests.go.test_labels import observed
from tests.go.test_minmax import GRADUATED_BLANK_TYPE_ORIGINAL


ROOT = Path(__file__).parent
FIXTURE = ROOT / 'fixtures/blank_types.go'
UPSTREAM_ROOT = ROOT / 'upstream/go1.27.3'
UPSTREAM_PATH = 'test/fixedbugs/issue8079.go'
UPSTREAM = UPSTREAM_ROOT / UPSTREAM_PATH
UPSTREAM_SHA256 = '97e0601f3838cc934c1b5a9a04a18ea8e0e1a77ab8d57a75ae6123b466a5a1a7'
UPSTREAM_BLOB = '994999bf6fbfb5d13c8942d3794fe03310f5f9b5'
FIXTURE_SHA256 = 'f56b97960d4fbe7066c5b73a7443ff45afe17a75d181ff0e042743b072777116'


def units(path):
    return ((path.name, path.read_bytes().decode('utf-8')),)


def upstream_packages():
    return {
        'main': (('main.go', 'package main;import "p";func main(){if p.Probe!=42{for{}}}'),),
        'p': units(UPSTREAM) + (('probe.go', 'package p;const Probe=42'),),
    }


def flattened(packages):
    return tuple((path+'/'+name, text) for path, original in packages.items() for name, text in original)


VALID = (
    GRADUATED_BLANK_TYPE_CASE[1], GRADUATED_BLANK_TYPE_ORIGINAL,
    'package main;type _ int;type _ string;type _ = int;func main(){}',
    'package main;type (_ int;_ = int);func main(){}',
    'package main;type _ A;type A int;func main(){}',
    'package main;type T int;type _ = T;type A = T;func main(){var x A=7;var y T=x;if y!=7{for{}}}',
    'package main;type _ interface{M(int) string};func main(){}',
    'package main;type _ = error;func main(){}',
    'package main;type I interface{M() I};type _ I;func main(){}',
    'package main;const _=3;type _ int;func _(){};func main(){}',
)
INVALID = (
    ('package main;type _ Missing;func main(){}', 'undefined type Missing', 'undefined: Missing'),
    ('package main;type _ = Missing;func main(){}', 'undefined type Missing', 'undefined: Missing'),
    ('package main;type _ _;func main(){}', 'undefined type _', 'cannot use _ as value or type'),
    ('package main;type _ = _;func main(){}', 'undefined type _', 'cannot use _ as value or type'),
    ('package main;type _ int;type X _;func main(){}', 'undefined type _', 'cannot use _ as value or type'),
    ('package main;type _ int;func main(){_=_(1)}', 'undefined function _', 'cannot use _ as value or type'),
    ('package main;type _ [-1]int;func main(){}', 'array length must be nonnegative', 'invalid array length'),
    ('package main;type _ A;type A [1]A;func main(){}', 'invalid recursive type', 'invalid recursive type'),
    ('package main;type _ = A;type A = B;type B = A;func main(){}', 'invalid recursive type', 'invalid recursive type'),
    ('package main;type _ struct{X int;X int};func main(){}', 'duplicate struct field', 'X redeclared'),
    ('package main;type _ interface{M();M(int)};func main(){}', 'duplicate interface method', 'duplicate method M'),
    ('package main;type _ int;func F[T _](){};func main(){}', 'undefined type constraint _', 'cannot use _ as value or type'),
)
FRONTIERS = (
    ('package main;type _ []int;func main(){}', 'go.unsupported.slice-storage'),
    ('package main;type _ = []int;func main(){}', 'go.unsupported.slice-storage'),
    ('package main;type _ interface{~int};func main(){}', 'go.unsupported.generic-boundaries'),
    ('package main;type _[T any] struct{};func main(){}', 'go.unsupported.generic-boundaries'),
    ('package main;type _ struct{_ int};func main(){}', 'go.unsupported.blank-struct-fields'),
    ('package main;func main(){type _ int}', 'go.unsupported.local-type-declarations'),
    ('package main;type _ int;var _=7;func main(){}', 'go.unsupported.blank-package-variables'),
)
IMPORT_GRAPHS = tuple({
    'main': (('main.go', 'package main;'+imports+declaration+';func main(){}'),
             ('sibling.go', 'package main;type _ int')),
    'q': (('q.go', 'package q;type T uint64;type _ interface{}'),),
} for imports, declaration in (
    ('import "q";', 'type _ q.T'),
    ('import q "q";', 'type _ = q.T'),
    ('import . "q";', 'type _ T'),
))
INVALID_GRAPHS = (
    ({'main': (('main.go', 'package main;import "q";type _ int;func main(){}'),),
      'q': (('q.go', 'package q;type T int'),)}, 'imported and not used', 'imported and not used'),
    ({'main': (('main.go', 'package main;import "q";type _ q.hidden;func main(){}'),),
      'q': (('q.go', 'package q;type hidden int'),)}, 'unexported package member', r'name hidden not exported|not exported|undefined: q\.hidden'),
    ({'main': (('main.go', 'package main;import "q";type _ q.T;func main(){}'),
               ('sibling.go', 'package main;type _ q.T')),
      'q': (('q.go', 'package q;type T int'),)}, 'undefined imported package q', 'undefined: q'),
)


def test_complete_official_original_and_authored_fixture_keep_pinned_bytes():
    data = UPSTREAM.read_bytes()
    provenance = json.loads((ROOT / 'upstream/provenance.json').read_text())
    assert provenance['commit'] == '2aae1000ea1d4b28923b96db28acf4e3b99d6aff'
    assert hashlib.sha256(data).hexdigest() == UPSTREAM_SHA256 == provenance['files'][UPSTREAM_PATH]
    assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest() == UPSTREAM_BLOB
    assert provenance['git_blobs'][UPSTREAM_PATH] == UPSTREAM_BLOB
    assert hashlib.sha256((UPSTREAM_ROOT / 'LICENSE').read_bytes()).hexdigest() == (
        '911f8f5782931320f5b8d1160a76365b83aea6447ee6c04fa6d5591467db9dad')
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256
    assert units(UPSTREAM)[0][1].encode('utf-8') == data


def test_complete_official_blank_type_has_no_binding_but_keeps_checked_identity():
    owner = Analyzer(parse_package(units(UPSTREAM), require_main=False), require_main=False, package_path='p')
    owner.analyze()
    declaration, = owner.program.types
    assert declaration.token.text == '_' and '_' not in owner.types
    assert isinstance(declaration.resolved, DefinedType)
    assert declaration.resolved is declaration.identity
    assert declaration.resolved.underlying == InterfaceType(())


def test_repeated_blank_definitions_are_distinct_and_aliases_keep_canonical_targets():
    owner = Analyzer(parse_package(units(FIXTURE), require_main=True))
    owner.analyze()
    definitions = [item for item in owner.program.types if item.token.text == '_' and not item.alias]
    assert len(definitions) == 6
    assert len({id(item.identity) for item in definitions}) == len(definitions)
    assert all(item.resolved is item.identity for item in definitions)
    assert all(item.resolved.underlying is not None for item in definitions)
    aliases = [item for item in owner.program.types if item.token.text == '_' and item.alias]
    assert len(aliases) == 3
    assert aliases[0].resolved == 'uint64' and aliases[0].identity is None
    assert aliases[1].resolved is owner.types['Later'].resolved and aliases[1].identity is None
    assert aliases[2].identity is None and isinstance(aliases[2].resolved, InterfaceType)
    method, = aliases[2].resolved.methods
    assert method.name == 'Compare' and method.signature.parameters == ('uint64',)
    assert method.signature.result == 'bool'
    assert '_' not in owner.types and '_' not in owner.globals and '_' not in owner.functions


@pytest.mark.parametrize('original', VALID)
def test_complete_valid_and_graduated_originals_build_owned_ir(original, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(original, 'original.go')


@pytest.mark.parametrize('original,message,reference', INVALID)
def test_blank_type_rhs_errors_remain_invalid_before_ir(original, message, reference, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid blank type reached IR'))
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(original, 'original.go')
    assert type(caught.value) is GoDiagnostic


@pytest.mark.parametrize('original,code', FRONTIERS)
def test_blank_types_do_not_open_unimplemented_rhs_or_local_storage(original, code, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('type frontier reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original, 'original.go')
    assert caught.value.code == code


def test_imported_rhs_is_used_and_whole_official_package_is_unchanged(monkeypatch):
    forbid_external_compiler(monkeypatch)
    for packages in (*IMPORT_GRAPHS, upstream_packages()):
        compile_go_packages(packages)
    assert upstream_packages()['p'][0][1].encode('utf-8') == UPSTREAM.read_bytes()


@pytest.mark.parametrize('packages,message,reference', INVALID_GRAPHS)
def test_blank_declarations_do_not_bypass_import_scope_or_visibility(packages, message, reference, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid import reached IR'))
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go_packages(packages)
    assert type(caught.value) is GoDiagnostic


@pytest.mark.integration
def test_complete_fixture_has_independent_full_width_native_result(tmp_path, monkeypatch):
    original = units(FIXTURE)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(original, observe_entry=('Result', ()))
    binary = tmp_path / 'owned'
    write_artifact(artifact, binary)
    assert observed(binary, tmp_path / 'observation', owner=artifact.owner,
                    originals=original) == Execution(0, (0xfedcba9876543210).to_bytes(8, 'little'), b'')


@pytest.mark.integration
def test_complete_originals_and_package_graphs_execute_owned_main(tmp_path, monkeypatch):
    cases = [(units(FIXTURE), None)] + [((('original.go', text),), None) for text in VALID]
    cases += [(flattened(packages), packages) for packages in (*IMPORT_GRAPHS, upstream_packages())]
    for index, (original, packages) in enumerate(cases):
        with monkeypatch.context() as denied:
            forbid_external_compiler(denied)
            artifact = compile_module(compile_go_packages(packages)) if packages is not None else compile_owned(original)
        binary = tmp_path / ('owned-'+str(index))
        write_artifact(artifact, binary)
        assert observed(binary, tmp_path / ('observation-'+str(index)), owner=artifact.owner,
                        originals=original) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_complete_and_graduated_originals_match_official_processes(tmp_path, monkeypatch):
    originals = (units(FIXTURE),) + tuple((('original.go', text),) for text in VALID)
    for index, original in enumerate(originals):
        with monkeypatch.context() as denied:
            forbid_external_compiler(denied)
            artifact = compile_owned(original)
        binary = tmp_path / ('owned-'+str(index))
        write_artifact(artifact, binary)
        reference = compile_reference(original, tmp_path / ('reference-'+str(index)),
            compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
        for name, text in original:
            assert (reference.parent / name).read_bytes() == text.encode('utf-8')
        actual = observed(binary, tmp_path / ('pcc-observation-'+str(index)), owner=artifact.owner, originals=original)
        expected = observed(reference, tmp_path / ('go-observation-'+str(index)), owner='official-go', originals=original)
        assert actual == expected == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_import_graphs_and_official_package_keep_raw_process_evidence(tmp_path, monkeypatch):
    for index, packages in enumerate((*IMPORT_GRAPHS, upstream_packages())):
        original = flattened(packages)
        with monkeypatch.context() as denied:
            forbid_external_compiler(denied)
            artifact = compile_module(compile_go_packages(packages))
        binary = tmp_path / ('owned-'+str(index))
        write_artifact(artifact, binary)
        reference = compile_reference_packages(packages, tmp_path / ('reference-'+str(index)),
            compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
        for name, text in original:
            assert (reference.parent / 'gopath/src' / name).read_bytes() == text.encode('utf-8')
        actual = observed(binary, tmp_path / ('pcc-observation-'+str(index)), owner=artifact.owner, originals=original)
        expected = observed(reference, tmp_path / ('go-observation-'+str(index)), owner='official-go', originals=original)
        assert actual == expected == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original,message,reference', INVALID)
def test_identical_invalid_type_originals_have_attributed_official_rejections(original, message, reference, tmp_path):
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(original, 'original.go')
    assert type(caught.value) is GoDiagnostic
    (tmp_path / 'pcc-diagnostic.json').write_text(json.dumps({
        'source_sha256': hashlib.sha256(original.encode('utf-8')).hexdigest(),
        'diagnostic': str(caught.value), 'filename': caught.value.filename,
        'line': caught.value.line, 'column': caught.value.column,
    }, indent=2)+'\n')
    compile_reference_rejection((('original.go', original),), tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'], diagnostics=reference)


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('packages,message,reference', INVALID_GRAPHS)
def test_identical_invalid_import_graphs_have_attributed_official_rejections(packages, message, reference, tmp_path):
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go_packages(packages)
    assert type(caught.value) is GoDiagnostic
    (tmp_path / 'pcc-diagnostic.json').write_text(json.dumps({
        'sources': {path: hashlib.sha256(text.encode('utf-8')).hexdigest() for path, text in flattened(packages)},
        'diagnostic': str(caught.value), 'filename': caught.value.filename,
        'line': caught.value.line, 'column': caught.value.column,
    }, indent=2)+'\n')
    compile_reference_packages_rejection(packages, tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'], diagnostics=reference)


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original,code', FRONTIERS)
def test_valid_storage_frontiers_remain_reference_only(original, code, tmp_path):
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original, 'original.go')
    assert caught.value.code == code
    originals = (('original.go', original),)
    reference = compile_reference(originals, tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert (reference.parent / 'original.go').read_bytes() == original.encode('utf-8')
    assert observed(reference, tmp_path / 'go-observation', owner='official-go',
                    originals=originals) == Execution(0, b'', b'')
