"""Value arrays/records: typed equality and captured expression switches.

Normative contracts: https://go.dev/ref/spec#Comparison_operators
https://go.dev/ref/spec#Assignability https://go.dev/ref/spec#Expression_switches
https://go.dev/ref/spec#Order_of_evaluation
Authored CI obligations only; no recorded execution is implied by this module.
"""
import hashlib
import json
import os

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go, compile_go_packages
from tests.go.owned_execution import (
    Execution, compile_module, compile_owned, compile_reference,
    compile_reference_packages, compile_reference_rejection, run, write_artifact,
)
from tests.go.test_arrays import source as array_source
from tests.go.test_diagnostics import original as diagnostic_original
from tests.go.test_execution import forbid_external_compiler, units
from tests.go.test_nullable_pointers import record_pair
from tests.go.test_records import source


# All four exact original wrappers are preserved, not silently replaced with
# nearby examples. Their earlier diagnostic outcomes remain historical.
GRADUATED = [
    array_source('var a [2]int;_=a==a'),
    source('type A struct{X uint64}', 'var a A;_=a==a'),
    diagnostic_original(body='var a [1]int;_=a==a'),
    diagnostic_original(body='var a [1]int;switch a{case a:}'),
]


@pytest.mark.parametrize('original', GRADUATED)
def test_exact_aggregate_frontiers_graduate_without_rewriting(original, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(original)


@pytest.mark.parametrize('declarations,body', [
    ('', '_=[0]int{}==[0]int{}'),
    ('', '_=struct{}{}!=struct{}{}'),
    ('', '_=[3]bool{true}==[3]bool{true}'),
    ('type T int8;type A [2]T', '_=A{-128,127}==[2]T{-128,127}'),
    ('type A [2]int;type B=A', 'a:=A{1,2};var b B=a;_=a==b'),
    ('type A struct{X int}', 'a:=A{3};b:=struct{X int}{3};_=a==b;_=b==a'),
    ('type A [1]int;type B [1]int', 'a:=A{3};b:=B{3};_=a==A(b)'),
    ('type A struct{X string;V [2]int}', 'a:=A{};_=a==a'),
    ('type A struct{V struct{X bool}}', 'a:=A{};_=a==a'),
    ('func f(p *[2]int)bool{if p==nil{return false};return *p==[2]int{}}', '_=f(nil)'),
    ('', 'a:=[1]int{2};switch a{case a:;case a:}'),
    ('type A struct{}', 'switch a:=(A{});a{case A{}:;default:}'),
    ('', 'switch struct{}{}{case struct{}{}:;case struct{}{}:}'),
    ('', 'switch [0]int{}{}'),
])
def test_aggregate_comparison_typing_and_assignability(declarations, body, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(source(declarations, body))


def test_aggregate_ir_uses_component_values_and_short_circuit_edges(monkeypatch):
    forbid_external_compiler(monkeypatch)
    text = str(compile_go(source('type R struct{A [2]int;S string}',
                                 'a:=R{A:[2]int{1,2},S:"x"};b:=a;_=a==b;_=a!=b')))
    assert 'getelementptr [2 x i64]' in text
    assert 'load i64' in text
    assert 'aggregate.next' in text and 'aggregate.unequal' in text
    assert 'string.check' in text
    assert 'memcmp' not in text and 'noalias' not in text


def test_aggregate_switch_shares_one_captured_selector():
    from pcc.frontends.go.sema import Analyzer
    from pcc.frontends.go.syntax import GoParser
    program = GoParser().parse(source('', 'a:=[2]int{1,2};switch a{case a:;case [2]int{}:}'))
    Analyzer(program).analyze()
    main = next(function for function in program.functions if function.token.text == 'main')
    switch = next(statement for statement in main.body if statement.kind == 'switch')
    for clause in switch.cases:
        for comparison in clause.exprs:
            assert comparison.kind == 'aggregate_compare'
            assert comparison.args[0] is switch.exprs[0]
            assert comparison.type == 'untyped bool'


INVALID = [
    ('', 'a:=[1]int{};b:=[2]int{};_=a==b', 'mismatched aggregate', 'mismatched types'),
    ('', 'a:=[1]int{};b:=[1]int64{};_=a==b', 'mismatched aggregate', 'mismatched types'),
    ('type A [1]int;type B [1]int', 'var a A;var b B;_=a==b', 'mismatched aggregate', 'mismatched types'),
    ('type A struct{X int};type B struct{X int}', 'var a A;var b B;_=a==b', 'mismatched aggregate', 'mismatched types'),
    ('', 'a:=struct{X int}{};b:=struct{Y int}{};_=a==b', 'mismatched aggregate', 'mismatched types'),
    ('', 'var a [1]int;_=a==nil', 'mismatched aggregate', 'mismatched types'),
    ('', 'var a [0]int;_=nil==a', 'mismatched aggregate', 'mismatched types'),
    ('type A [1]int', 'var a A;_=a!=nil', 'mismatched aggregate', 'mismatched types'),
    ('', 'var a [1]int;_=a< a', 'only equality', 'operator < not defined'),
    ('type A struct{X int}', 'var a A;_=a+a', 'only equality', r'operator \+ not defined'),
    ('', 'var a [1]int;switch a{case [2]int{}:}', 'mismatched aggregate', 'mismatched types'),
    ('type A struct{X int};type B struct{X int}', 'var a A;switch a{case B{}:}', 'mismatched aggregate', 'mismatched types'),
    ('', 'var a [1]int;switch a{case 1:}', 'cannot use', 'cannot convert'),
    ('', 'const yes=[0]int{}==[0]int{};_=yes', 'not a constant', 'is not constant'),
    ('', 'switch a:=([1]int{});a{case a:};_=a', 'undefined name', 'undefined'),
    ('', 'var a [1]int;switch a{case a:fallthrough}', 'non-final switch clause', 'cannot fallthrough'),
]


@pytest.mark.parametrize('declarations,body,message,reference_message', INVALID)
def test_invalid_aggregate_comparisons_fail_before_ir(declarations, body, message, reference_message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid comparison reached IR'))
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(source(declarations, body))
    assert not isinstance(caught.value, UnsupportedGo)


UNSUPPORTED = [
    ('array-element-types', source('', 'var a [1]string;_=a==a')),
    ('array-element-types', source('', 'var a [1][1]int;_=a==a')),
    ('array-element-types', source('type R struct{X int}', 'var a [1]R;_=a==a')),
    ('pointer-storage', source('type R struct{P *int}', 'var a R;_=a==a')),
    ('blank-struct-fields', source('type R struct{_ int;X int}', 'var a R;_=a==a')),
    ('interface-types', source('type R struct{X any}', 'var a R;_=a==a')),
    ('pointer-nil', source('func f(p *[1]int)bool{return *p==[1]int{}}')),
    ('pointer-nil', source('func f(p *[0]int)bool{return *p==[0]int{}}')),
    ('pointer-nil', source('func f(p *[1]int){switch *p{case [1]int{}:}}')),
    ('array-bounds-panic', source('func f(a [2]int,i int)bool{return [1]int{a[i]}==[1]int{}}')),
]

# The exact original graduates unchanged in test_record_arrays with native
# and same-source official-reference obligations. Other gaps stay explicit.
GRADUATED_RECORD_ARRAY = ('array-element-types', source('type R struct{X int}', 'var a [1]R;_=a==a'))
ACTIVE_UNSUPPORTED = [case for case in UNSUPPORTED if case != GRADUATED_RECORD_ARRAY]


@pytest.mark.parametrize('code,original', ACTIVE_UNSUPPORTED)
def test_unimplemented_aggregate_storage_and_evaluation_stay_explicit(code, original, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported value reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.' + code


RAW = [
    ('Arrays', 15), ('NamedValues', 0xfedcba9876543217),
    ('PaddingAndCopies', 0x8877665544332211), ('NestedStrings', 63),
    ('EmptyStrings', 17), ('CapturedOperands', 1209), ('EmptyOperandCalls', 12),
    ('SwitchCapture', 12307), ('StructSwitch', 23), ('FallthroughSkipsLabels', 123),
    ('RepeatedCases', 29), ('EmptySwitch', 31), ('GuardedPointers', 37),
    ('LoopSwitch', 15), ('ShortCircuit', 41),
]


@pytest.mark.integration
@pytest.mark.parametrize('name,expected', RAW)
def test_aggregate_equality_full_width_native_observations(name, expected, tmp_path, monkeypatch):
    originals = units(('aggregate_equality.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals, observe_entry=(name, ()))
    binary = tmp_path / 'aggregate-observer'
    write_artifact(artifact, binary)
    actual = run(binary)
    (tmp_path / 'stdout').write_bytes(actual.stdout)
    (tmp_path / 'stderr').write_bytes(actual.stderr)
    (tmp_path / 'observation.json').write_text(json.dumps({
        'owner': artifact.owner, 'function': name,
        'input_sha256': hashlib.sha256(originals[0][1].encode()).hexdigest(),
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'returncode': actual.returncode, 'stdout_hex': actual.stdout.hex(),
        'stderr_hex': actual.stderr.hex(), 'expected_hex': expected.to_bytes(8, 'little').hex(),
    }, indent=2) + '\n')
    assert actual == Execution(0, expected.to_bytes(8, 'little'), b'')


@pytest.mark.integration
def test_complete_aggregate_equality_program_executes_owned(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units(('aggregate_equality.go',)))
    binary = tmp_path / 'aggregate-main'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('originals', [
    pytest.param(None, id='complete-program'),
    *[pytest.param((('original.go', text),), id='graduated-'+str(i)) for i, text in enumerate(GRADUATED)],
])
def test_identical_aggregate_programs_match_official_raw_streams(originals, tmp_path, monkeypatch):
    originals = units(('aggregate_equality.go',)) if originals is None else originals
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    binary = tmp_path / 'aggregate-main'
    write_artifact(artifact, binary)
    reference = compile_reference(originals, tmp_path / 'reference', compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    record_pair(tmp_path, hashlib.sha256(originals[0][1].encode()).hexdigest(), binary, reference)
    assert (reference.parent / originals[0][0]).read_bytes() == originals[0][1].encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('declarations,body,message,reference_message', INVALID)
def test_official_go_rejects_identical_invalid_aggregate_sources(declarations, body, message, reference_message, tmp_path):
    original = source(declarations, body)
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(original)
    assert not isinstance(caught.value, UnsupportedGo)
    receipt = compile_reference_rejection((('invalid.go', original),), tmp_path / 'reference',
                                          compiler=os.environ['PCC_GO_REFERENCE'],
                                          cache=os.environ['PCC_GO_REFERENCE_CACHE'], diagnostics=reference_message)
    assert receipt['classification'] == 'normal_source_rejection'
    assert (tmp_path / 'reference/invalid.go').read_bytes() == original.encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('code,original', ACTIVE_UNSUPPORTED)
def test_official_go_builds_unsupported_aggregate_originals_without_execution(code, original, tmp_path):
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.' + code
    reference = compile_reference((('original.go', original),), tmp_path / 'reference',
                                  compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert reference.is_file()
    assert (reference.parent / 'original.go').read_bytes() == original.encode()


# Comparison must include private fields without granting source selection of
# those fields to importers. These are authored supplied-source packages.
PROJECT = {
    'main': [('main.go', 'package main;import "lib";func main(){a:=records.New(3,4);b:=records.New(3,5);if a==b{for{}};b=records.New(3,4);if a!=b{for{}};switch a{case b:return};for{}}')],
    'lib': [('lib.go', 'package records;type R struct{X uint64;hidden uint64};func New(x,h uint64)R{return R{x,h}}')],
}


def test_imported_record_equality_keeps_private_field_identity(monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go_packages(PROJECT)
    invalid = dict(PROJECT)
    invalid['main'] = [('main.go', 'package main;import "lib";func main(){a:=records.New(3,4);_=a.hidden}')]
    with pytest.raises(GoDiagnostic, match='unexported struct field'):
        compile_go_packages(invalid)


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_imported_record_graph_matches_official_raw_streams(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_module(compile_go_packages(PROJECT))
    binary = tmp_path / 'aggregate-project'
    write_artifact(artifact, binary)
    reference = compile_reference_packages(PROJECT, tmp_path / 'reference', compiler=os.environ['PCC_GO_REFERENCE'],
                                           cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    identity = hashlib.sha256(json.dumps(PROJECT, sort_keys=True).encode()).hexdigest()
    record_pair(tmp_path, identity, binary, reference)
    for package, originals in PROJECT.items():
        for filename, text in originals:
            assert (reference.parent / 'gopath/src' / package / filename).read_bytes() == text.encode()
