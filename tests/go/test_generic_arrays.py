"""Fixed-array generic signatures: authored CI obligations, execution UNRUN.

https://go.dev/ref/spec#Array_types
https://go.dev/ref/spec#Type_inference https://go.dev/ref/spec#Type_unification
https://go.dev/ref/spec#Instantiations https://go.dev/ref/spec#Assignability
https://go.dev/ref/spec#Comparison_operators https://go.dev/ref/spec#Order_of_evaluation
"""
import hashlib
import json
import os

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go, compile_go_packages
from pcc.frontends.go.generics import GenericSession
from tests.go.owned_execution import (
    Execution, compile_module, compile_owned, compile_reference,
    compile_reference_packages, compile_reference_rejection, run, write_artifact,
)
from tests.go.test_execution import forbid_external_compiler, units
from tests.go.test_generic_functions import analyzed
from tests.go.test_nullable_pointers import record_pair
from tests.go.test_records import source


# Keep the exact formerly valid-but-unsupported original, not a nearby rewrite.
GRADUATED = source('func F[T any](x [2]T){}')
FIRST = 'func First[T any](x [2]T)T{return x[0]}'
PAIR = 'func Pair[T any](a,b [2]T)T{return a[0]}'


def test_array_signatures_and_abstract_bodies_compile_without_external_owner(monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(GRADUATED)
    compile_go(units(('generic_arrays.go',))[0][1])


def test_array_inference_registry_preserves_element_not_outer_nominal_identity():
    text = source(FIRST+';type Alias=uint8;type N uint8;type A [2]N;type B [2]N',
                  '_=First([2]Alias{});_=First[uint8]([2]uint8{});'
                  '_=First(A{});_=First(B{});_=First([2]N{})')
    analyzer, program = analyzed(text)
    analyzer.generics.materialize()
    analyzer.generics.validate_abstract([program])
    entries = list(analyzer.generics.instances.entries.values())
    assert len(entries) == 2  # uint8/alias versus N; A and B infer the same N.
    assert len({entry.value.abi_symbol for entry in entries}) == 2
    assert {entry.value.params[0][1].count for entry in entries} == {2}


INVALID_UNUSED = [
    ('func Bad[T any](x [2]T)bool{return x==x}', 'not permitted', 'incomparable|cannot be compared'),
    ('func Bad[T any](x [0]T)bool{return x==x}', 'not permitted', 'incomparable|cannot be compared'),
    ('func Bad[T any](x [2]T){switch x{}}', 'comparable', 'not comparable|cannot switch'),
    ('func Bad[T any](x [2]T)T{return x[0]+x[1]}', 'not permitted', 'operator \\+ not defined'),
    ('func Bad[T any]() [2]T{return [2]T{0,0}}', 'cannot use', 'cannot use 0'),
    ('func Bad[T any](x [2]T)T{return x[2]}', 'out of bounds', 'out of bounds'),
    ('func Bad[T,U any](x [2]T)U{return x[0]}', 'cannot use', 'cannot use'),
    (FIRST+';func Bad[T any](x T)T{return First(x)}', 'incompatible array', 'does not match'),
    ('func Equal[T comparable](x [2]T)bool{return x==x};'
     'func Bad[T any](x [2]T)bool{return Equal(x)}', 'does not satisfy comparable', 'does not satisfy comparable'),
]
INVALID_CALLS = [
    (FIRST, '_=First([3]int{})', 'incompatible array', 'does not match'),
    (FIRST, '_=First[int]([3]int{})', 'incompatible array', 'cannot use'),
    (FIRST, '_=First(3)', 'cannot infer', 'does not match|cannot infer'),
    (PAIR, '_=Pair([2]int{},[2]uint64{})', 'conflicting inferred', 'does not match inferred'),
    (PAIR+';type N int', '_=Pair([2]N{},[2]int{})', 'conflicting inferred', 'does not match inferred'),
    (FIRST+';type N int', '_=First[int]([2]N{})', 'cannot use', 'cannot use'),
    ('func F[T any](x T,a [2]T)T{return x}', '_=F(256,[2]uint8{})', 'overflows uint8',
     r'cannot use 256 \(untyped int constant\) as uint8 value in argument to F \(overflows\)'),
    ('func F[T any](a [2]T,x T)T{return x}', '_=F([2]uint8{},256)', 'overflows uint8',
     r'cannot use 256 \(untyped int constant\) as uint8 value in argument to F \(overflows\)'),
    ('func Zero[T any]() [2]T{var z [2]T;return z}', 'var x [2]int=Zero();_=x', 'cannot infer', 'cannot infer'),
]


@pytest.mark.parametrize('declarations,message,reference_message', INVALID_UNUSED)
def test_invalid_unused_array_bodies_fail_before_instances(declarations, message, reference_message, monkeypatch):
    monkeypatch.setattr(GenericSession, 'materialize', lambda *args: pytest.fail('invalid declaration reached instances'))
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(source(declarations))
    assert not isinstance(caught.value, UnsupportedGo)


def test_favorable_concrete_array_call_cannot_legalize_invalid_generic_body(monkeypatch):
    monkeypatch.setattr(GenericSession, 'materialize', lambda *args: pytest.fail('invalid declaration reached instances'))
    with pytest.raises(GoDiagnostic, match='not permitted') as caught:
        compile_go(source('func Bad[T any](x [2]T)bool{return x==x}', '_=Bad([2]int{})'))
    assert not isinstance(caught.value, UnsupportedGo)


@pytest.mark.parametrize('declarations,body,message,reference_message', INVALID_CALLS)
def test_array_inference_errors_fail_before_ir(declarations, body, message, reference_message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid call reached IR'))
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(source(declarations, body))
    assert not isinstance(caught.value, UnsupportedGo)


UNSUPPORTED = [
    ('array-element-types', source('func Zero[T any]() [2]T{var x [2]T;return x}', '_=Zero[string]()')),
    ('array-element-types', source('func Zero[T any]() [0]T{var x [0]T;return x}', '_=Zero[struct{X int}]()')),
    ('array-element-types', source('func F[T any](x [2][2]T){}')),
    ('generic-boundaries', source('func F[T any](x *[2]T){}')),
    ('generic-boundaries', source('func F[T any](x [2]T){p:=&x;_=p}')),
    ('generic-boundaries', source('func F[T any](x [2]T){p:=&x[0];_=p}')),
    ('generic-boundaries', source('func F[T any](x struct{Values [2]T}){}')),
]

# Keep the old complete source as a positive/native/reference obligation in
# test_record_arrays, rather than reporting an implementation as unsupported.
GRADUATED_RECORD_ARRAY = ('array-element-types', source(
    'func Zero[T any]() [0]T{var x [0]T;return x}', '_=Zero[struct{X int}]()'))
ACTIVE_UNSUPPORTED = [case for case in UNSUPPORTED if case != GRADUATED_RECORD_ARRAY]


@pytest.mark.parametrize('code,original', ACTIVE_UNSUPPORTED)
def test_array_generics_do_not_open_other_storage_contracts(code, original, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported layout reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.' + code


RAW = [
    ('NamedInference', 0xfedcba9876543213), ('CopyAndResult', 1319),
    ('LocalAndLiteral', 2329), ('ConstraintChecks', 43), ('AbstractForwarding', 4747),
    ('RecursiveAndMultiple', 6141), ('PartialInference', 6771), ('ConstantsAfterArrays', 7983),
    ('EvaluationOnce', 120711), ('ZeroLengthAndSize', 4), ('StackArrayResult', 0x8877665544332211),
]


@pytest.mark.integration
@pytest.mark.parametrize('name,expected', RAW)
def test_generic_arrays_execute_full_width_native(name, expected, tmp_path, monkeypatch):
    originals = units(('generic_arrays.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals, observe_entry=(name, ()))
    binary = tmp_path/'array-observer'
    write_artifact(artifact, binary)
    actual = run(binary)
    (tmp_path/'observation.json').write_text(json.dumps({
        'function': name, 'owner': artifact.owner,
        'input_sha256': hashlib.sha256(originals[0][1].encode()).hexdigest(),
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'returncode': actual.returncode, 'stdout_hex': actual.stdout.hex(), 'stderr_hex': actual.stderr.hex(),
    }, indent=2)+'\n')
    assert actual == Execution(0, expected.to_bytes(8, 'little'), b'')


@pytest.mark.integration
@pytest.mark.parametrize('originals', [
    (('graduated.go', GRADUATED),), tuple(units(('generic_arrays.go',))),
])
def test_complete_generic_arrays_execute_owned(originals, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    binary = tmp_path/'array-main'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('originals', [
    (('graduated.go', GRADUATED),), tuple(units(('generic_arrays.go',))),
])
def test_identical_array_generic_programs_match_official(originals, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    binary = tmp_path/'array-main'
    write_artifact(artifact, binary)
    reference = compile_reference(originals, tmp_path/'reference', compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    record_pair(tmp_path, hashlib.sha256(originals[0][1].encode()).hexdigest(), binary, reference)
    for filename, text in originals:
        assert (reference.parent/filename).read_bytes() == text.encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original,message,reference_message', [
    *[(source(declarations), message, reference) for declarations, message, reference in INVALID_UNUSED],
    *[(source(declarations, body), message, reference) for declarations, body, message, reference in INVALID_CALLS],
    (source('func Bad[T any](x [2]T)bool{return x==x}', '_=Bad([2]int{})'),
     'not permitted', 'incomparable|cannot be compared'),
])
def test_official_go_rejects_identical_invalid_array_generic_sources(original, message, reference_message, tmp_path):
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(original)
    assert not isinstance(caught.value, UnsupportedGo)
    receipt = compile_reference_rejection((('invalid.go', original),), tmp_path/'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'],
        diagnostics=reference_message)
    assert receipt['classification'] == 'normal_source_rejection'
    assert (tmp_path/'reference/invalid.go').read_bytes() == original.encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('code,original', ACTIVE_UNSUPPORTED)
def test_official_go_builds_identical_array_storage_frontiers(code, original, tmp_path):
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.' + code
    reference = compile_reference((('original.go', original),), tmp_path/'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert reference.is_file()
    assert (reference.parent/'original.go').read_bytes() == original.encode()


PROJECT = {
    'main': [('main.go', 'package main;import "values";func main(){x:=values.Forward(values.New());if uint64(x)!=97{for{}}}')],
    'values': [('values.go', 'package values;type hidden uint64;type Values [2]hidden;func New()Values{return Values{97,101}};func first[T any](x [2]T)T{return x[0]};func Forward[T any](x [2]T)T{return first(x)}')],
}


def test_imported_array_inference_keeps_unexported_element_identity(monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go_packages(PROJECT)


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_imported_array_generics_match_official(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_module(compile_go_packages(PROJECT))
    binary = tmp_path/'array-project'
    write_artifact(artifact, binary)
    reference = compile_reference_packages(PROJECT, tmp_path/'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    record_pair(tmp_path, hashlib.sha256(json.dumps(PROJECT, sort_keys=True).encode()).hexdigest(), binary, reference)
    for package, originals in PROJECT.items():
        for filename, text in originals:
            assert (reference.parent/'gopath/src'/package/filename).read_bytes() == text.encode()
