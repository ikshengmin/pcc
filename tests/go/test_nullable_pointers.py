"""Ordinary nullable Go pointers, not noalias, Rust borrowing, or nil panic support.

Normative: https://go.dev/ref/spec#Pointer_types
https://go.dev/ref/spec#Address_operators https://go.dev/ref/spec#Assignments
https://go.dev/ref/spec#Logical_operators https://go.dev/ref/spec#Expression_switches
https://go.dev/ref/spec#For_statements https://go.dev/ref/spec#Calls
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go, compile_go_packages
from tests.go.owned_execution import (
    Execution, compile_module, compile_owned, compile_reference,
    compile_reference_packages, compile_reference_rejection, run, write_artifact,
)
from tests.go.test_execution import forbid_external_compiler, units
from tests.go.test_records import source


# Exact former v5 capability originals graduate to real same-input positive
# controls. V5 source/receipts stay unchanged; these are new candidate results.
GRADUATED = [
    source('', 'var p *int;_=p'),
    source('', 'var p *int=nil;_=p'),
    source('', 'x:=1;p:=&x;p=nil;_=p'),
    source('func f(p *int){}'),
    'package main;\nfunc f(p *int){}\nfunc main(){}\n',
    'package main;\n\nfunc main(){var p *int;_=p}\n',
]


GUARDED = [
    'func f(p *int)int{if p==nil{return 0};return *p}',
    'func f(p *int)int{if p!=nil{return *p};return 0}',
    'func f(p *int)int{if !(p==nil){return *p};return 0}',
    'func f(p *int)int{if bool(p!=nil){return *p};return 0}',
    'type B bool;func f(p *int)int{if B(p!=nil){return *p};return 0}',
    'func f(p *int)int{if p==(*int)(nil){return 0};return *p}',
    'func f(p *int)int{var z *int;if p==z{return 0};return *p}',
    'func f(p *int)bool{return p!=nil&&*p==3}',
    'func f(p *int)bool{return p==nil||*p==3}',
    'func f(p *int)int{if p==nil{return 0}else{return *p}}',
    'func f(p *int)int{switch{case p==nil:return 0;default:return *p}}',
    'func f(p *int)int{for{if p==nil{return 0};break};return *p}',
    'func f(p *int){for;p!=nil;p=nil{*p=3;continue}}',
    'func f(p *int)int{if p==nil{return 0};{q:=p;*q=4};return *p}',
    'func f(p *[3]int)int{return len(*p)+cap(*p)}',
    'func f(p *[3]int)int{s:=0;for i:=range *p{s+=i};return s}',
    'func f(p *int)int{return len([1]int{*p})}',
    'func f(p *int)int{for range [0]int{}{_=*p};return 0}',
]


# Original review counterexamples are not rewritten into passing shapes.
# Each is valid Go, but this bounded frontend cannot provide its unguarded
# nil-proof, nil-panic or escaping-lifetime contract. Reference tests below typecheck
# these identical sources; they do not claim successful PCC execution.
UNPROVEN = [
    ('pointer-nil', 'package main; func f(p *int)int{return *p};func main(){x:=1;_=f(&x)}'),
    ('pointer-nil', 'package main; func f(p *int)int{if p!=nil{p=nil;return *p};return 0};func main(){}'),
    ('pointer-nil', 'package main; func f(p *int)int{if p!=nil{};return *p};func main(){}'),
    ('pointer-nil', 'package main;func known(p *int)bool{return p!=nil};func f(p *int)int{if known(p){return *p};return 0};func main(){}'),
    ('pointer-nil', 'package main; func f(p *int)bool{return *p==3&&p!=nil};func main(){}'),
    ('pointer-nil', 'package main; func f(p *int)int{if p!=nil{p:=(*int)(nil);return *p};return 0};func main(){}'),
    ('pointer-nil', 'package main; func f(p *int) int { switch { case p == nil: fallthrough; case p != nil: return *p }; return 0 };func main(){}'),
    ('pointer-nil', 'package main; func main(){ x:=1; var p *int; p,*p=&x,2; _=p }'),
    ('pointer-nil', 'package main; func main(){x:=1;var p *int;p,p=&x,nil;_=*p}'),
    ('pointer-nil', 'package main; func f(p,q *int)int{if p!=nil{p,q=q,p;return *p};return 0};func main(){}'),
    ('pointer-nil', 'package main; func f(p *int){for{_=*p;p=nil}};func main(){x:=1;f(&x)}'),
    ('pointer-nil', 'package main; func f(p *int){if p==nil{return};for i:=0;i<2;p=nil{_=*p;i++}};func main(){}'),
    ('pointer-nil', 'package main; func f(p *int){if p==nil{return};for i:=0;i<2;p=nil{i++;continue};_=*p};func main(){}'),
    ('pointer-nil', 'package main; func f(p *int)int{for p!=nil{break};return *p};func main(){}'),
    ('pointer-nil', 'package main; func f(p *int)int{switch{case p!=nil,p==nil:return *p};return 0};func main(){}'),
    ('pointer-nil', 'package main; func f(p *int){_=&*p};func main(){}'),
    ('pointer-nil', 'package main;type T int;func(t T)M(){};func f(p *T){(*p).M()};func main(){}'),
    ('pointer-nil', 'package main;func f(p *[0]int){for _,v:=range *p{_=v}};func main(){}'),
    ('pointer-escape', 'package main;func main(){var p *int;{x:=1;p=&x};if p!=nil{_=*p}}'),
    ('pointer-escape', 'package main;func f(p *int){{x:=1;p=&x};if p!=nil{_=*p}};func main(){}'),
    ('pointer-signatures', 'package main;func f(p *int)*int{return p};func main(){}'),
    ('pointer-storage', 'package main;var p *int;func f(q *int){p=q};func main(){}'),
    ('pointer-storage', 'package main;func f(p **int){};func main(){}'),
    # Exact former pointer-receiver original: test_pointer_methods.GRADUATED.
]


INVALID = [
    'package main;func f(p *int){};func main(){f(1)}',
    'package main;func f(p *int){};func main(){x:=int64(1);f(&x)}',
    'package main;func f(p *int,p *int){};func main(){}',
    'package main;func f(p *int){};func main(){f()}',
    'package main;func f(p *int){if p!=nil{*p=true}};func main(){}',
]


@pytest.mark.parametrize('original', GRADUATED)
def test_retained_nullable_frontier_originals_graduate_without_rewriting(original, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(original)


@pytest.mark.parametrize('declaration', GUARDED)
def test_source_guards_and_unevaluated_operands_admit_pointer_parameters(declaration, monkeypatch):
    forbid_external_compiler(monkeypatch)
    module = compile_go(source(declaration))
    text = str(module)
    assert 'noalias' not in text and 'nonnull' not in text


@pytest.mark.parametrize('code,original', UNPROVEN)
def test_unguarded_and_escaping_originals_fail_before_ir(code, original, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsafe pointer reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.' + code


@pytest.mark.parametrize('original', INVALID)
def test_pointer_parameter_validity_errors_remain_validity_errors(original, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid pointer reached IR'))
    with pytest.raises(GoDiagnostic) as caught:
        compile_go(original)
    assert not isinstance(caught.value, UnsupportedGo)


def test_nullable_zero_is_a_typed_ir_null_not_integer_zero():
    module = compile_go('package main;func main(){var p *int;_=p}')
    assert 'null' in str(module)


def test_nonretention_requires_source_callee_even_on_unreachable_edge():
    from pcc.frontends.go.pointer_flow import validate_pointer_flow
    from pcc.frontends.go.sema import Analyzer
    from pcc.frontends.go.syntax import GoParser
    program = GoParser().parse('package main;func f(p *int){};func main(){if false{f(nil)}}')
    Analyzer(program).analyze()
    program.functions = [f for f in program.functions if f.token.text == 'main']
    with pytest.raises(UnsupportedGo) as caught:
        validate_pointer_flow(program)
    assert caught.value.code == 'go.unsupported.pointer-signatures'


RAW = [
    ('NilCall', 17), ('LiveCall', 23), ('ZeroAndCopies', 7), ('Rebind', 33),
    ('Aliased', 0xfedcba9876543217), ('Distinct', 0xfedcba9876543210),
    ('ShortCircuits', 122), ('ParallelTargets', 2), ('DuplicateTargets', 1),
    ('SwapNullable', 3), ('ContinuePost', 5), ('BreakFlow', 217),
    ('SwitchGuard', 417), ('SwitchFallthrough', 10), ('NestedBreakTargets', 13),
    ('UnevaluatedNil', 63), ('ArrayParameter', 15), ('ExplicitAddress', 9),
    ('RecordParameter', 703), ('RecursiveCalls', 617), ('ScopedAlias', 13),
    ('ValueMethodGuard', 15), ('DelayedMethodGuard', 11), ('RebindParameter', 9908),
    ('LegacyNilStorage', 1), ('LegacyUnusedParameter', 1),
    ('StackParameters', 2203), ('StackAliasing', 2323),
    ('NamedPointerParameter', 705), ('ConvertedGuards', 604),
]


@pytest.mark.integration
@pytest.mark.parametrize('name,expected', RAW)
def test_nullable_pointer_full_width_native_observations(name, expected, tmp_path, monkeypatch):
    original = units(('nullable_pointers.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(original, observe_entry=(name, ()))
    binary = tmp_path / 'nullable-observer'
    write_artifact(artifact, binary)
    actual = run(binary)
    (tmp_path / 'observation.json').write_text(json.dumps({
        'input_sha256': hashlib.sha256(original[0][1].encode()).hexdigest(),
        'name': name, 'expected': expected, 'stdout_hex': actual.stdout.hex(),
        'stderr_hex': actual.stderr.hex(), 'returncode': actual.returncode,
        'object_sha256': hashlib.sha256(artifact.object).hexdigest(),
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
    }, indent=2) + '\n')
    assert actual == Execution(0, expected.to_bytes(8, 'little'), b'')


def record_pair(tmp_path, original_hash, native, reference):
    observed = []
    for owner, binary in [('pcc', native), ('reference', reference)]:
        actual = run(binary)
        observed.append(actual)
        (tmp_path / (owner + '.stdout')).write_bytes(actual.stdout)
        (tmp_path / (owner + '.stderr')).write_bytes(actual.stderr)
        (tmp_path / (owner + '.execution.json')).write_text(json.dumps({
            'input_sha256': original_hash, 'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'returncode': actual.returncode,
        }, indent=2) + '\n')
    assert observed[0] == observed[1] == Execution(0, b'', b'')


@pytest.mark.integration
def test_complete_nullable_pointer_main_executes_owned(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units(('nullable_pointers.go',)))
    binary = tmp_path / 'nullable-main'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_nullable_pointer_program_matches_official_raw_streams(tmp_path, monkeypatch):
    original = units(('nullable_pointers.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(original)
    binary = tmp_path / 'nullable-main'
    write_artifact(artifact, binary)
    reference = compile_reference(original, tmp_path / 'reference', compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    record_pair(tmp_path, hashlib.sha256(original[0][1].encode()).hexdigest(), binary, reference)
    assert (reference.parent / 'nullable_pointers.go').read_bytes() == original[0][1].encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original', GRADUATED)
def test_identical_graduated_frontiers_match_official_raw_streams(original, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned((('original.go', original),))
    binary = tmp_path / 'graduated'
    write_artifact(artifact, binary)
    reference = compile_reference((('original.go', original),), tmp_path / 'reference',
                                  compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    record_pair(tmp_path, hashlib.sha256(original.encode()).hexdigest(), binary, reference)
    assert (reference.parent / 'original.go').read_bytes() == original.encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('code,original', UNPROVEN)
def test_official_go_typechecks_identical_valid_unsupported_originals(code, original, tmp_path):
    # Native official executable is built but NOT run: some originals require
    # a nil panic. This is acceptance/typecheck evidence, not runtime parity.
    reference = compile_reference((('original.go', original),), tmp_path / 'reference',
                                  compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert (reference.parent / 'original.go').read_bytes() == original.encode()
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.' + code


PROJECT = {
    'main': [('main.go', 'package main;import "nullable";func main(){x:=4;if nullable.Read(nil)!=7||nullable.Bump(&x)!=9||x!=9{for{}}}')],
    'nullable': [('nullable.go', 'package nullable;func Read(p *int)int{if p==nil{return 7};return *p};func Bump(p *int)int{if p==nil{return 0};*p+=5;return Read(p)}')],
}


def test_imported_pointer_callees_are_analyzed_from_nullable_parameters(monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go_packages(PROJECT)


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_imported_pointer_project_matches_official_raw_streams(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_module(compile_go_packages(PROJECT))
    binary = tmp_path / 'nullable-project'
    write_artifact(artifact, binary)
    reference = compile_reference_packages(PROJECT, tmp_path / 'reference', compiler=os.environ['PCC_GO_REFERENCE'],
                                           cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    identity = hashlib.sha256(json.dumps(PROJECT, sort_keys=True).encode()).hexdigest()
    record_pair(tmp_path, identity, binary, reference)
    for package, files in PROJECT.items():
        for name, original in files:
            assert (reference.parent / 'gopath/src' / package / name).read_bytes() == original.encode()


# Pinned compiler's types2 assignment/call/declaration diagnostics. A build
# crash, resource error, missing output or arbitrary AssertionError is NOT a
# semantic rejection. Keep raw streams/returncode before these assertions.
INVALID_DIAGNOSTICS = [
    (b'cannot use 1', b'as *int value in argument to f'),
    (b'cannot use &x', b'*int64', b'as *int value in argument to f'),
    (b'p redeclared in this block',),
    (b'not enough arguments in call to f',),
    (b'cannot use true', b'as int value in assignment'),
]


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original,diagnostics', zip(INVALID, INVALID_DIAGNOSTICS))
def test_identical_invalid_pointer_parameter_sources_are_rejected_by_official_go(original, diagnostics, tmp_path):
    with pytest.raises(GoDiagnostic) as caught:
        compile_go(original)
    assert not isinstance(caught.value, UnsupportedGo)
    compile_reference_rejection((('original.go', original),), tmp_path / 'reference',
                                compiler=os.environ['PCC_GO_REFERENCE'],
                                cache=os.environ['PCC_GO_REFERENCE_CACHE'], diagnostics=diagnostics)
    assert (tmp_path / 'reference/original.go').read_bytes() == original.encode()
    assert not (tmp_path / 'reference/oracle').exists()
