"""Go direct generic functions, not concrete-only template validation.

https://go.dev/ref/spec#Type_parameter_declarations
https://go.dev/ref/spec#Instantiations https://go.dev/ref/spec#Type_inference
https://go.dev/ref/spec#Satisfying_a_type_constraint
https://go.dev/ref/spec#Declarations_and_scope
All native/reference programs below are authored CI obligations, not receipts.
"""
import hashlib
import json
import os

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go, compile_go_packages
from pcc.frontends.go.generics import ANY, GenericSession, TypeParameter
from pcc.support.instantiation import InstanceRegistry, InstanceState
from tests.go.owned_execution import (
    Execution, compile_module, compile_owned, compile_reference,
    compile_reference_packages, compile_reference_rejection, run, write_artifact,
)
from tests.go.test_execution import forbid_external_compiler, units
from tests.go.test_nullable_pointers import record_pair
from tests.go.test_records import source

IDENTITY = 'func Identity[T any](value T)T{return value}'
EQUAL = 'func Equal[T comparable](a,b T)bool{return a==b}'


@pytest.mark.parametrize('declarations,body', [
    (IDENTITY, '_=Identity(3);_=Identity[uint64](7)'),
    (IDENTITY, '_=Identity[uint64,](7)'),
    (IDENTITY+';type A=byte;type N uint8', '_=Identity[A](3);_=Identity[N](4)'),
    (IDENTITY, '_=Identity([2]int{1,2});_=Identity(struct{X int}{3})'),
    ('func Zero[T any]()T{var value T;return value}', '_=Zero[string]();_=Zero[[0]int]()'),
    ('func Pair[T,U any](a T,b U)(U,T){return b,a}', 'a,b:=Pair[int](3,"x");_=a;_=b'),
    (EQUAL, '_=Equal("x","x");_=Equal([2]int{},[2]int{})'),
    ('func F[T any](x T)T{{T:=3;_=T};return T(x)}', '_=F(7)'),
    ('var T int;func F[T any](x T)T{return T(x)}', '_=F(7)'),
    ('type T int;func F[T any](x T)T{return T(x)}', '_=F("x")'),
    ('func F[T any](T)T{var value T;return value}', '_=F(7)'),
    ('func F[T any](x T)(result T){result=x;return}', '_=F(7)'),
    (IDENTITY+';func F[T any](x T)T{return Identity[T](Identity(x))}', '_=F(7)'),
    ('func F[T any](x T,p *int)T{if p!=nil{*p+=1};return x}', '_=F(7,nil)'),
    ('func F[_ any](){}', 'F[int]()'),
    ('func F[_,_ any](){}', 'F[int,string]()'),
    ('func F[_ any,T any](x T)T{return x}', '_=F[int](uint64(7))'),
    ('func F[T any,_ comparable](x T)T{return x}', '_=F[uint64,string](7)'),
    ('func F[_ any](_ int){}', 'F[int](3)'),
    ('type C interface{};func F[T C](x T)T{return x}', '_=F(3)'),
    ('type C=any;func F[T C](x T)T{return x}', '_=F[uint64](7)'),
    ('type C any;func F[T C](x T)T{return x}', '_=F("x")'),
    ('type Base interface{};type C interface{Base};func F[T C](x T)T{return x}', '_=F([2]int{})'),
    ('type Base interface{};type C=Base;func F[T C](x T)T{return x}', '_=F(struct{X int}{3})'),
])
def test_generic_function_typing_and_value_inference(declarations, body, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(source(declarations, body))


def analyzed(text):
    from pcc.frontends.go.sema import Analyzer
    from pcc.frontends.go.syntax import GoParser
    program = GoParser().parse(text)
    analyzer = Analyzer(program)
    analyzer.analyze()
    return analyzer, program


def test_registry_reuses_aliases_but_preserves_nominal_and_recursive_identity():
    text = source(IDENTITY+';type A=byte;type N uint8;func Recur[T any](x T,n uint64)T{if n==0{return x};return Recur(x,n-1)}',
                  '_=Identity[int](3);_=Identity(4);_=Identity[A](5);_=Identity[uint8](6);_=Identity[N](7);_=Recur(8,2)')
    analyzer, program = analyzed(text)
    assert isinstance(analyzer.generics.instances, InstanceRegistry)
    declared = analyzer.functions['Identity']
    assert isinstance(declared.params[0][1], TypeParameter)
    analyzer.generics.materialize()
    analyzer.generics.validate_abstract([program])
    entries = list(analyzer.generics.instances.entries.values())
    assert len(entries) == 4  # Identity[int,uint8,N], Recur[int].
    assert all(entry.state is InstanceState.COMPLETE for entry in entries)
    assert len({entry.value.abi_symbol for entry in entries}) == 4
    size = len(program.functions)
    analyzer.generics.materialize()
    assert len(program.functions) == size
    assert len(analyzer.generics.instances) == 4


def test_unnamed_type_slots_preserve_arity_constraints_and_instance_identity():
    text = source('type A=uint8;type N uint8;func F[_,_ any,T any](_ int,x T)T{return x}',
                  '_=F[int,A](0,uint64(3));_=F[int,uint8](0,uint64(4));'
                  '_=F[string,uint8](0,uint64(5));_=F[int,N](0,uint64(6))')
    analyzer, program = analyzed(text)
    declared = analyzer.functions['F']
    parameters = analyzer.generics.parameters[id(declared)]
    assert [parameter.name for parameter in parameters] == ['_', '_', 'T']
    assert len(set(parameters)) == 3
    assert set(declared.type_bindings) == {'T'}
    analyzer.generics.materialize()
    analyzer.generics.validate_abstract([program])
    entries = list(analyzer.generics.instances.entries.values())
    assert len(entries) == 3
    assert len({entry.value.abi_symbol for entry in entries}) == 3
    assert all(set(entry.value.type_bindings) == {'T'} for entry in entries)
    assert all(entry.state is InstanceState.COMPLETE for entry in entries)


def test_complete_unnamed_type_parameter_program_builds_owned_ir(monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(units(('unnamed_type_parameters.go',))[0][1])


def test_named_empty_constraints_resolve_to_any_without_erasing_argument_identity():
    text = source('type Empty interface{};type Alias=Empty;type N uint8;type Byte=uint8;'
                  'func F[T Alias](x T)T{return x}',
                  '_=F[Byte](3);_=F[uint8](4);_=F[N](5)')
    analyzer, program = analyzed(text)
    declared = analyzer.functions['F']
    assert analyzer.generics.parameters[id(declared)][0].constraint is ANY
    analyzer.generics.materialize()
    analyzer.generics.validate_abstract([program])
    entries = list(analyzer.generics.instances.entries.values())
    assert len(entries) == 2
    assert len({entry.value.abi_symbol for entry in entries}) == 2
    assert all(entry.state is InstanceState.COMPLETE for entry in entries)


def test_complete_named_empty_constraint_program_builds_owned_ir(monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(units(('named_empty_constraints.go',))[0][1])


def test_generic_registry_failure_is_sticky_mechanism_only(monkeypatch):
    analyzer, _ = analyzed(source(IDENTITY, '_=Identity(3)'))
    failure = GoDiagnostic(analyzer.functions['Identity'].token, 'simulated instance failure')
    monkeypatch.setattr(analyzer, 'analyze_function', lambda function: (_ for _ in ()).throw(failure))
    for _ in range(2):
        with pytest.raises(GoDiagnostic) as caught:
            analyzer.generics.materialize()
        assert caught.value is failure
    assert next(iter(analyzer.generics.instances.entries.values())).state is InstanceState.FAILED


INVALID_UNUSED = [
    ('func Bad[T any](x T)T{return x+1}', 'mismatched type-parameter', r'cannot convert|mismatched types'),
    ('func Bad[T comparable](x T)T{return x+x}', 'not permitted', r'operator \+ not defined'),
    ('func Bad[T any](x T)bool{return x==x}', 'not permitted', 'incomparable types'),
    ('func Bad[T any]()T{return 0}', 'cannot use', 'cannot use 0'),
    ('func intValue(x int){};func Bad[T any](x T){intValue(x)}', 'cannot use', 'cannot use x'),
    ('func Bad[T,U any](x T)U{return x}', 'cannot use', 'cannot use x'),
    ('func Bad[T comparable](x T)bool{return x==nil}', 'mismatched type-parameter', 'mismatched types'),
    (EQUAL+';func Bad[T any](x T)bool{return Equal[T](x,x)}', 'does not satisfy comparable', 'does not satisfy comparable'),
    ('func Bad[T any](x T)T{_=missing;return x}', 'undefined name', 'undefined: missing'),
    ('func Bad[T any](x T)T{_=x}', 'missing return', 'missing return'),
    ('func Bad[T any,T any](){}', 'duplicate type parameter', 'redeclared'),
    ('func Bad[T any](T T){}', 'redeclares type parameter', 'redeclared'),
    ('func Bad[T any]()(T int){return}', 'redeclares type parameter', 'redeclared'),
    ('func Bad[T any](){var T int;_=T}', 'redeclaration', 'redeclared'),
    ('func Bad[T T](){}', 'cannot be used as a constraint', 'cannot use a type parameter as constraint'),
    ('func Bad[T any,U T](){}', 'cannot be used as a constraint', 'cannot use a type parameter as constraint'),
    ('func Bad[any any](){}', 'cannot be used as a constraint', 'cannot use a type parameter as constraint'),
    ('func Bad[T Missing](){}', 'undefined type constraint', 'undefined: Missing'),
    ('func Bad[_ any](x _){}', 'undefined type', 'cannot use _ as value or type'),
    ('func Bad[_ any]()_{for{}}', 'undefined type', 'cannot use _ as value or type'),
    ('func Bad[_ any](){_=_(3)}', 'undefined function _', 'cannot use _ as value or type'),
    ('func Bad[_ Missing](){}', 'undefined type constraint', 'undefined: Missing'),
    ('func Bad[_ any,T _](){}', 'undefined type constraint', 'cannot use _ as value or type'),
    ('func Bad[_ any,T any](x T)T{return x+1}', 'mismatched type-parameter', 'cannot convert|mismatched types'),
    ('func Need[_ any,_ comparable](){};func Bad[T any](){Need[int,T]()}',
     'does not satisfy comparable', 'does not satisfy comparable'),
    ('type C interface{};func Bad[T C](a,b T)bool{return a==b}', 'not permitted', 'incomparable types'),
    ('type C interface{};func Bad[T C](x T)T{return x+1}', 'mismatched type-parameter', 'cannot convert|mismatched types'),
    ('type C interface{};'+EQUAL+';func Bad[T C](x T)bool{return Equal[T](x,x)}',
     'does not satisfy comparable', 'does not satisfy comparable'),
    ('type C interface{};func Bad[C C](){}', 'cannot be used as a constraint', 'cannot use a type parameter as constraint'),
    ('const C=3;func Bad[T C](){}', 'constraint must denote a type', r'C (?:\(constant\) )?is not a type'),
]


@pytest.mark.parametrize('declarations,message,reference_message', INVALID_UNUSED)
def test_unused_generic_bodies_and_constraints_fail_before_materialization(declarations, message, reference_message, monkeypatch):
    monkeypatch.setattr(GenericSession, 'materialize', lambda *args: pytest.fail('invalid unused declaration reached instance work'))
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(source(declarations))
    assert not isinstance(caught.value, UnsupportedGo)


INVALID_CALLS = [
    (IDENTITY, '_=Identity(nil)', 'cannot infer', 'cannot infer'),
    ('func Zero[T any]()T{var x T;return x}', 'var x int=Zero();_=x', 'cannot infer', 'cannot infer'),
    # Observed normal Go 1.24.13 source rejection; keep the exact invalid
    # original and require its count-specific diagnostic, not a build failure.
    (IDENTITY, '_=Identity[int,string](3)', 'too many generic', 'got 2 type arguments but want 1'),
    ('func F(x int)int{return x}', '_=F[int](3)', 'nongeneric', 'cannot index'),
    (IDENTITY, 'x:=3;_=Identity[x](4)', 'must denote a type', 'is not a type'),
    ('func First[T any](a,b T)T{return a}', '_=First(256,uint8(1))', 'overflows uint8',
     r'cannot use 256 \(untyped int constant\) as uint8 value in argument to First \(overflows\)'),
    ('func First[T any](a,b T)T{return a};type A int;type B int', '_=First(A(1),B(2))', 'conflicting inferred', 'does not match inferred type'),
    ('func First[T any](a,b T)T{return a}', '_=First(1,"x")', 'cannot infer', 'mismatched types'),
    (IDENTITY+';func Empty(){}', '_=Identity(Empty())', 'void function call', 'no value'),
    # The exact a[0,] original is valid in official Go and now retained in
    # test_index_trailing_comma.py as a positive source/native/reference case.
    # Go 1.24.13 rejects a[0,0] during parsing, before index type checking.
    ('', 'a:=[1]int{1};_=a[0,0]', 'multiple indices',
     r'not a generic|more than one index|^syntax error: unexpected literal 0, expected \]$'),
    ('func F[_ any](){}', 'F()', 'cannot infer type argument', 'cannot infer'),
    ('func F[_,_ any](){}', 'F[int]()', 'cannot infer type argument', 'cannot infer'),
    ('func F[T any,_ any](x T){}', 'F[int](3)', 'cannot infer type argument', 'cannot infer'),
    ('func F[_ any](){}', 'F[int,string]()', 'too many generic type arguments', 'got 2 type arguments but want 1'),
]


# These are malformed TypeTerm spellings, not capability controls. The exact
# originals are also sent to the strict official rejection helper below.
MALFORMED_CONSTRAINTS = [
    'func F[T *](x T)T{return x}',
    'func F[T []](x T)T{return x}',
    'func F[T [2]int +](x T)T{return x}',
    'func F[T struct{X, Y}](x T)T{return x}',
    'func F[T map[int]](x T)T{return x}',
    'func F[T <-int](x T)T{return x}',
    'func F[T func(int,,)](x T)T{return x}',
    'func F[T *int |](x T)T{return x}',
    'func F[T ~](x T)T{return x}',
]


@pytest.mark.parametrize('declarations', MALFORMED_CONSTRAINTS)
def test_malformed_constraint_syntax_is_not_an_unsupported_capability(declarations):
    with pytest.raises(GoDiagnostic) as caught:
        compile_go(source(declarations))
    assert not isinstance(caught.value, UnsupportedGo)


@pytest.mark.parametrize('declarations,body,message,reference_message', INVALID_CALLS)
def test_invalid_generic_calls_fail_before_ir(declarations, body, message, reference_message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid call reached IR'))
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(source(declarations, body))
    assert not isinstance(caught.value, UnsupportedGo)


UNSUPPORTED = [
    ('generic-boundaries', source('func F[T ~int](x T)T{return x}')),
    ('generic-boundaries', source('func F[T int|int64](x T)T{return x}')),
    ('generic-boundaries', source('func F[T int](x T)T{return x}')),
    # Exact TypeTerm grammar is valid even without an interface wrapper.
    # These unused bodies are official build-only CI controls, never runtime
    # acceptance for pointers/slices/maps/channels/functions or general sets.
    ('generic-boundaries', 'package main;func F[T *int](x T)T{return x};func main(){}'),
    *[('generic-boundaries', source('func F[T '+term+'](x T)T{return x}')) for term in (
        '[2]int', '[]int', 'struct{X int}', 'struct{X [2]int}',
        'struct{X int `json:"x"`}', 'map[string]int', 'chan int', '<-chan int',
        'chan<- int', 'func(int)int', 'func(int,[2]int)', 'func(x,y int)int',
        'func(x [2]int,y (int))int', 'func(...int)',
        '(*int)', '*int | *[2]int', 'interface{~int}',
    )],
    ('generic-boundaries', source('type Box[T any] struct{Value T}')),
    ('generic-boundaries', source(IDENTITY, 'x:=3;_=Identity(&x)')),
    ('generic-boundaries', source('func F[T any](p *T){}')),
    # The identical former [2]T original is retained as a positive control in
    # test_generic_arrays.py; pointer and record frontiers stay negative.
    ('generic-boundaries', source('func F[T any](x struct{Value T}){}')),
    ('generic-boundaries', source('func F[T any](x T){p:=&x;_=p}')),
    ('generic-boundaries', source(IDENTITY, 'f:=Identity[int];_=f(3)')),
    ('generic-boundaries', source(IDENTITY, '_=Identity[any](3)')),
    ('predeclared-shadowing', source('type any int;func F[T any](x T)T{return x}')),
    ('predeclared-shadowing', source('type comparable int;func F[T comparable](x T)T{return x}')),
    ('predeclared-shadowing', source('func F[any comparable](){}')),
    ('predeclared-shadowing', source('func F[comparable any](){}')),
    ('pointer-nil', source('func F[T any](x T,p *int)T{_=*p;return x}')),
    ('pointer-escape', source('func F[T any](x T){var p *int;{y:=1;p=&y};_=p}')),
    ('limit-function-arity', source('func F['+','.join('T'+str(n) for n in range(257))+' any](){}')),
    ('generic-boundaries', source('func F[_ any](){}', 'F[*int]()')),
    ('generic-boundaries', source('func F[_ any](){}', 'F[any]()')),
    ('generic-boundaries', source('type C interface{M()};func F[T C](x T){}')),
    ('generic-boundaries', source('type C int;func F[T C](x T){}')),
    ('generic-boundaries', source('type C interface{~int};func F[T C](x T){}')),
    ('generic-boundaries', source('type C interface{};func F[T C](){}', 'F[C]()')),
]


def instance_limit_source():
    return source(IDENTITY, ';'.join('_=Identity(['+str(n)+']int{})' for n in range(129)))


@pytest.mark.parametrize('code,original', UNSUPPORTED)
def test_generic_frontiers_preserve_constraint_and_pointer_contracts(code, original, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported generic reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.' + code


def test_generic_instance_limit_is_explicit_before_ir(monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('instance limit reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(instance_limit_source())
    assert caught.value.code == 'go.unsupported.limit-generic-instances'


RAW = [
    ('Scalar',0xfedcba9876543210), ('Named',0x8877665544332211),
    ('ArrayCopy',0xfedcba9876543217), ('StructCopy',0x8877665544332211),
    ('TypedBeforeConstants',108), ('Comparisons',31), ('Zeros',19),
    ('MultipleAndPartial',2923), ('Recursion',68), ('Forwarding',84),
    ('EvaluationOrder',1207), ('StackArguments',47), ('AliasEffects',859),
    ('SwitchValues',61), ('NamesAndTrailingComma',67),
]


@pytest.mark.integration
@pytest.mark.parametrize('name,expected', RAW)
def test_generic_value_helpers_execute_full_width_native(name, expected, tmp_path, monkeypatch):
    originals = units(('generic_functions.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals, observe_entry=(name,()))
    binary = tmp_path/'generic-observer'
    write_artifact(artifact,binary)
    actual = run(binary)
    (tmp_path/'stdout').write_bytes(actual.stdout)
    (tmp_path/'stderr').write_bytes(actual.stderr)
    (tmp_path/'observation.json').write_text(json.dumps({
        'function':name,'owner':artifact.owner,'input_sha256':hashlib.sha256(originals[0][1].encode()).hexdigest(),
        'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'returncode':actual.returncode,
        'stdout_hex':actual.stdout.hex(),'stderr_hex':actual.stderr.hex(),'expected_hex':expected.to_bytes(8,'little').hex(),
    },indent=2)+'\n')
    assert actual == Execution(0,expected.to_bytes(8,'little'),b'')


@pytest.mark.integration
def test_complete_generic_value_program_executes_owned(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units(('generic_functions.go',)))
    binary = tmp_path/'generic-main'
    write_artifact(artifact,binary)
    assert run(binary) == Execution(0,b'',b'')


@pytest.mark.integration
@pytest.mark.parametrize('observe', [False, True])
def test_unnamed_type_parameter_program_executes_full_width_result(observe, tmp_path, monkeypatch):
    from tests.go.test_labels import observed
    originals = units(('unnamed_type_parameters.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals, observe_entry=('Result', ()) if observe else None)
    binary = tmp_path / 'unnamed-type-parameters'
    write_artifact(artifact, binary)
    expected = (0xfedcba9876543212).to_bytes(8, 'little') if observe else b''
    assert observed(binary, tmp_path / 'observation', owner=artifact.owner,
                    originals=originals) == Execution(0, expected, b'')


@pytest.mark.integration
@pytest.mark.parametrize('observe', [False, True])
def test_named_empty_constraint_program_executes_full_width_result(observe, tmp_path, monkeypatch):
    from tests.go.test_labels import observed
    originals = units(('named_empty_constraints.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals, observe_entry=('Result', ()) if observe else None)
    binary = tmp_path / 'named-empty-constraints'
    write_artifact(artifact, binary)
    expected = (0xfedcba9876543213).to_bytes(8, 'little') if observe else b''
    assert observed(binary, tmp_path / 'observation', owner=artifact.owner,
                    originals=originals) == Execution(0, expected, b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_generic_value_program_matches_official_raw_streams(tmp_path, monkeypatch):
    for filename in ('generic_functions.go', 'unnamed_type_parameters.go', 'named_empty_constraints.go'):
        directory = tmp_path / filename
        directory.mkdir()
        originals = units((filename,))
        with monkeypatch.context() as denied:
            forbid_external_compiler(denied)
            artifact = compile_owned(originals)
        binary = directory/'generic-main'
        write_artifact(artifact,binary)
        reference = compile_reference(originals,directory/'reference',compiler=os.environ['PCC_GO_REFERENCE'],cache=os.environ['PCC_GO_REFERENCE_CACHE'])
        record_pair(directory,hashlib.sha256(originals[0][1].encode()).hexdigest(),binary,reference)
        assert (reference.parent/originals[0][0]).read_bytes() == originals[0][1].encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original,message,reference_message', [
    *[(source(declarations),message,reference) for declarations,message,reference in INVALID_UNUSED],
    *[(source(declarations,body),message,reference) for declarations,body,message,reference in INVALID_CALLS],
    *[(source(declarations),'expected|unterminated','syntax error') for declarations in MALFORMED_CONSTRAINTS],
])
def test_official_go_rejects_identical_invalid_generic_sources(original, message, reference_message, tmp_path):
    with pytest.raises(GoDiagnostic,match=message) as caught:
        compile_go(original)
    assert not isinstance(caught.value,UnsupportedGo)
    receipt = compile_reference_rejection((('invalid.go',original),),tmp_path/'reference',compiler=os.environ['PCC_GO_REFERENCE'],
                                          cache=os.environ['PCC_GO_REFERENCE_CACHE'],diagnostics=reference_message)
    assert receipt['classification'] == 'normal_source_rejection'
    assert (tmp_path/'reference/invalid.go').read_bytes() == original.encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('code,original', [*UNSUPPORTED, ('limit-generic-instances',instance_limit_source())])
def test_official_go_builds_identical_generic_frontiers_without_execution(code, original, tmp_path):
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.'+code
    reference = compile_reference((('original.go',original),),tmp_path/'reference',compiler=os.environ['PCC_GO_REFERENCE'],cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert reference.is_file()
    assert (reference.parent/'original.go').read_bytes() == original.encode()


PROJECT = {
    'main': [('main.go', 'package main;import "one";import "two";type Local bool;func main(){a:=one.Forward(one.New());if uint64(a)!=13{for{}};if one.LocalValue(uint64(17))!=17{for{}};if one.Id(uint64(19))!=19||two.Id(uint64(19))!=0{for{}}}')],
    'one': [('one.go', 'package one;import "inner";type hidden uint64;type Local=uint64;func New()hidden{return 13};func Id[T any](x T)T{return x};func helper[T any](x T)T{return Id[T](x)};func Forward[T any](x T)T{_=inner.Marker;return helper(x)};func LocalValue[T any](x T)T{var marker Local=7;_=marker;return x}')],
    'two': [('two.go', 'package two;func Id[T any](x T)T{var zero T;return zero}')],
    'inner': [('inner.go', 'package inner;const Marker=7')],
}

EMPTY_CONSTRAINT_PROJECT = {
    'main': [('main.go', 'package main;import . "example/constraints";import "example/helper";'
        'func Forward[T Open](x T)T{return helper.Id(x)};'
        'func main(){if Forward(uint64(0xfedcba9876543213))!=0xfedcba9876543213{for{}}}')],
    'example/constraints': [('constraints.go', 'package constraints;type Open interface{};type Alias=Open')],
    'example/helper': [('helper.go', 'package helper;import . "example/constraints";func Id[T Alias](x T)T{return x}')],
}


def test_dot_imported_empty_constraints_keep_owner_and_import_usage(monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go_packages(EMPTY_CONSTRAINT_PROJECT)


@pytest.mark.parametrize('declaration,diagnostic', [
    ('const Open=3', GoDiagnostic),
    ('type Open interface{M()}', UnsupportedGo),
    ('type Open int', UnsupportedGo),
])
def test_dot_imported_constraints_do_not_admit_values_methods_or_exact_sets(declaration, diagnostic, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid constraint reached IR'))
    graph = {
        'main': [('main.go', 'package main;import . "dep";func F[T Open](){};func main(){}')],
        'dep': [('dep.go', 'package dep;'+declaration)],
    }
    with pytest.raises(diagnostic) as caught:
        compile_go_packages(graph)
    if diagnostic is UnsupportedGo:
        assert caught.value.code == 'go.unsupported.generic-boundaries'
    else:
        assert not isinstance(caught.value, UnsupportedGo)
        assert 'constraint must denote a type' in str(caught.value)


def test_imported_generic_instances_keep_declaration_scope_and_owner_identity(monkeypatch):
    forbid_external_compiler(monkeypatch)
    text = str(compile_go_packages(PROJECT))
    assert '_instance_' in text
    assert '_abstract_' not in text
    assert 'noalias' not in text


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_imported_generic_graph_matches_official_raw_streams(tmp_path, monkeypatch):
    for name, graph in (('original', PROJECT), ('empty-constraints', EMPTY_CONSTRAINT_PROJECT)):
        directory = tmp_path/name
        directory.mkdir()
        with monkeypatch.context() as denied:
            forbid_external_compiler(denied)
            artifact = compile_module(compile_go_packages(graph))
        binary = directory/'generic-project'
        write_artifact(artifact,binary)
        reference = compile_reference_packages(graph,directory/'reference',compiler=os.environ['PCC_GO_REFERENCE'],cache=os.environ['PCC_GO_REFERENCE_CACHE'])
        record_pair(directory,hashlib.sha256(json.dumps(graph,sort_keys=True).encode()).hexdigest(),binary,reference)
        for package,originals in graph.items():
            for filename,text in originals:
                assert (reference.parent/'gopath/src'/package/filename).read_bytes() == text.encode()
