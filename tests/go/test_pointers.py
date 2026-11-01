"""Nonescaping pointer semantics, not a Go heap or nil-panic implementation.

Normative contracts: https://go.dev/ref/spec#Pointer_types
https://go.dev/ref/spec#Address_operators
https://go.dev/ref/spec#Assignments (evaluate all destinations before stores)
https://go.dev/ref/spec#Comparison_operators (identity, not value equality)
https://go.dev/ref/spec#Selectors https://go.dev/ref/spec#Index_expressions
https://go.dev/ref/spec#For_statements (distinct per-iteration variables)
"""
import hashlib
import json
import os
import subprocess
import re

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from tests.go.owned_execution import Execution, compile_owned, compile_reference, run, write_artifact
from tests.go.owned_execution import compile_reference_rejection
from tests.go.test_execution import forbid_external_compiler, units
from tests.go.test_records import source


INVALID = [
    ('_= &1', '', 'addressable'),
    ('const x=1;_=&x', '', 'addressable'),
    ('_=*(1)', '', 'requires a pointer'),
    ('var x int;var y int64;p:=&x;p=&y;_=p', '', 'cannot use'),
    ('var x int;p:=&x;_=p+1', '', 'only equality'),
    ('var x int;p:=&x;_=p<p', '', 'only equality'),
    ('var x int;p:=&x;p++', '', 'only equality'),
    ('var x int;p:=&x;*p=true', '', 'cannot use'),
    ('var x int;p:=&x;_=p==1', '', 'mismatched pointer'),
    ('var x int;var y int64;_=&x==&y', '', 'mismatched pointer'),
    ('_=nil', '', 'untyped nil'),
    ('var p int=nil;_=p', '', 'untyped nil'),
    ('var x int;p:=&x;const q=p;_=q', '', 'not a constant'),
    ('var x int;_=&(x+1)', '', 'addressable'),
    ('_=&"a"[0]', '', 'addressable'),
    ('', 'type P *int;func(p P)M(){}', 'receiver base cannot'),
    ('_=(*T).M(f())',
     'type T int;func(t T)M(n int)int{return int(t)+n};func f()(T,int){return 1,2}',
     r'cannot use T as \*T'),
    ('', 'type P = *P', 'alias cycle'),
    ('', 'type A = *B;type B = A', 'alias cycle'),
]


@pytest.mark.parametrize('body,declarations,message', INVALID)
def test_invalid_pointer_operations_fail_before_ir(body, declarations, message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid pointer reached IR'))
    with pytest.raises(GoDiagnostic, match=message):
        compile_go(source(declarations, body))


# Nullable storage and non-retaining parameter originals now live unchanged in
# test_nullable_pointers.GRADUATED, with native/official same-input controls.
# V5's old capability receipts remain historical; unguarded indirection does not
# graduate and retains its original pointer-nil code.
UNSUPPORTED = [
    ('pointer-nil', '_=*(*int)(nil)', ''),
    ('pointer-nil', '_=&*(*int)(nil)', ''),
    ('pointer-escape', 'x:=1;p:=&x;{y:=2;p=&y};_=p', ''),
    ('pointer-escape', 'x:=1;p:=&x;{q:=p;p=q};_=p', ''),
    ('pointer-escape', 'x:=1;p:=&x;for i:=0;i<2;i++{y:=i;p=&y};_=p', ''),
    ('pointer-escape', 'x:=1;p:=&x;if x>0{y:=2;p=&y};_=p', ''),
    ('pointer-storage', '', 'var p *int'),
    ('pointer-storage', '', 'var x int;var p=&x'),
    ('pointer-storage', '', 'type R struct{P *int}'),
    ('pointer-storage', '', 'var a [2]*int'),
    ('pointer-storage', 'x:=1;p:=&x;_=&p', ''),
    ('pointer-storage', '', 'type P **int'),
    ('pointer-storage', '', 'type N struct{Next *N}'),
    ('pointer-storage', '', 'type P *P'),
    ('pointer-storage', '', 'type A *B;type B A'),
    ('pointer-storage', '', 'type A = *B;type B A'),
    ('pointer-signatures', '', 'func f()*int{x:=1;return &x}'),
    ('pointer-signatures', '', 'func f()(int,*int){x:=1;return x,&x}'),
    ('pointer-iteration-identity', 'for i:=0;i<2;i++{p:=&i;_=p}', ''),
    ('pointer-iteration-identity', 'for _,v:=range [2]int{}{p:=&v;_=p}', ''),
    ('pointer-composite-address', 'p:=&[1]int{2};_=p', ''),
    ('pointer-composite-address', 'p:=&R{2};_=p', 'type R struct{X int}'),
    ('pointer-conversions', 'var x A;p:=&x;q:=(*int)(p);_=q', 'type A int'),
    ('pointer-switch', 'x:=1;p:=&x;switch p{case nil:}', ''),
    ('non-array-range', 'a:=[2]int{};p:=&a;for range p{}', ''),
    # Exact former pointer-receiver original: test_pointer_methods.GRADUATED.
    ('method-values', 'var t T;p:=&t;_=p.M', 'type T int;func(t T)M(){}'),
    ('method-values', '_=(*T).M', 'type T int;func(t T)M(){}'),
]


@pytest.mark.parametrize('suffix,body,declarations', UNSUPPORTED)
def test_pointer_capabilities_are_explicit_and_fail_closed(suffix, body, declarations, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported pointer reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(source(declarations, body))
    assert caught.value.code == 'go.unsupported.' + suffix


@pytest.mark.parametrize('body,declarations', [
    ('x:=1;p:=&x;q:=p;*q=3;_=*p', ''),
    ('x,y:=1,2;p:=&x;q:=&y;p,q=q,p;*p,*q=5,7', ''),
    ('var x uint8;p:=&x;*p=255;(*p)++;_=x', ''),
    ('var x R;p:=&x;p.X=7;_=(*p).X', 'type R struct{X int}'),
    ('var x [2]int;p:=&x;p[1]=7;_=len(p)+cap(p)', ''),
    ('var x int;var p *int=&x;_=p==nil', ''),
    ('var x int;var p P=&x;_=p==&x', 'type P *int'),
    ('var x int;p:=&x;_=(*int)(p);_=(*int)(nil)==nil', ''),
    ('var i int;for i=range [2]int{}{p:=&i;*p+=1}', ''),
    ('var x int;p:=&x;{q:=p;*q=2};_=*p', ''),
    ('_=len(P(nil))+cap(P(nil))', 'type P *[3]int'),
    ('_=len((*[3]int)(nil))+cap((*[3]int)(nil))', ''),
    ('_=(*struct{X int})(nil)==nil', ''),
    ('x:=[3]int{1,2,3};p:=&x;_=(*[3]int)(p)', ''),
])
def test_nonescaping_pointer_types_and_aliases_lower_without_external_owner(body, declarations, monkeypatch):
    forbid_external_compiler(monkeypatch)
    module = compile_go(source(declarations, body))
    assert 'noalias' not in str(module)


RAW = [
    ('WideAlias', (), 0xfedcba9876543217), ('Rebind', (), 509),
    ('Parallel', (), 1317), ('Equal', (), 63), ('RecordSnapshot', (), 3501),
    ('RecordStore', (), 810), ('ArrayStore', (), 2533), ('IndexOnce', (), 108),
    ('CompoundOnce', (), 1213), ('ShortRebind', (), 907), ('NestedScope', (), 15),
    ('IterationLocal', (), 14), ('OuterLoopBinding', (), 4), ('NamedPointer', (), 7),
    ('NamedValue', (), 0xfedcba9876543219), ('ValueMethod', (), 1164),
    ('StaticString', (), 7), ('BoolAlias', (), 19), ('GlobalAddress', (), 8),
    ('ParameterAddress', (4,), 9), ('ResultAddress', (), 23),
    ('DirectNilTypes', (), 7), ('ExplicitReceiverOrder', (), 9),
    ('CapturedReceiverOrder', (), 1),
]


@pytest.mark.integration
@pytest.mark.parametrize('name,arguments,expected', RAW)
def test_full_width_deterministic_pointer_program_observation(name, arguments, expected, tmp_path, monkeypatch):
    originals = units(('pointers_deterministic.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals, observe_entry=(name, arguments))
    binary = tmp_path / 'pointer-observer'
    write_artifact(artifact, binary)
    actual = run(binary)
    (tmp_path / 'pointer-observation.json').write_text(json.dumps({
        'name': name, 'input_sha256': hashlib.sha256(originals[0][1].encode()).hexdigest(),
        'object_sha256': hashlib.sha256(artifact.object).hexdigest(),
        'executable_sha256': hashlib.sha256(artifact.executable).hexdigest(),
        'stdout_hex': actual.stdout.hex(), 'stderr_hex': actual.stderr.hex(),
        'returncode': actual.returncode, 'expected': expected,
    }, indent=2) + '\n')
    assert actual == Execution(0, expected.to_bytes(8, 'little'), b'')


@pytest.mark.integration
def test_deterministic_pointer_program_main_executes(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units(('pointers_deterministic.go',)))
    binary = tmp_path / 'pointer-main'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_same_deterministic_pointer_program_matches_official_raw_process_streams(tmp_path, monkeypatch):
    originals = units(('pointers_deterministic.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    native = tmp_path / 'pointer-main'
    write_artifact(artifact, native)
    reference = compile_reference(originals, tmp_path / 'reference',
                                  compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    observations = []
    for owner, binary in [('pcc', native), ('reference', reference)]:
        actual = run(binary)
        observations.append(actual)
        (tmp_path / (owner + '.stdout')).write_bytes(actual.stdout)
        (tmp_path / (owner + '.stderr')).write_bytes(actual.stderr)
        (tmp_path / (owner + '.execution.json')).write_text(json.dumps({
            'input_sha256': hashlib.sha256(originals[0][1].encode()).hexdigest(),
            'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'returncode': actual.returncode,
        }, indent=2) + '\n')
    assert observations[0] == observations[1] == Execution(0, b'', b'')
    assert (reference.parent / 'pointers_deterministic.go').read_bytes() == originals[0][1].encode()


# Source-derived pinned types2 messages; only an actual normal rc1 observation
# can renew these rejection classifications. Earlier broad-assertion PASS nodes
# remain historical test outcomes, not proof of normal compiler rejection.
INVALID_REFERENCE_DIAGNOSTICS = [
    (b'cannot take address of 1', b'untyped int constant'),
    (b'cannot take address of x', b'untyped int constant'),
    (b'cannot indirect', b'untyped int constant'),
    (b'cannot use &y', b'*int64', b'as *int value in assignment'),
    (b'p + 1', b'mismatched types *int and untyped int'),
    (b'p < p', b'operator < not defined on pointer'),
    (b'p++', b'non-numeric type *int'),
    (b'cannot use true', b'as int value in assignment'),
    (b'p == 1', b'mismatched types *int and untyped int'),
    (b'&x == &y', b'mismatched types *int and *int64'),
    (b'use of untyped nil in assignment to _ identifier',),
    (b'cannot use nil as int value in variable declaration',),
    (b'p (', b'*int', b'is not constant'),
    (b'cannot take address of', b'x + 1'),
    (b'cannot take address of', b'"a"[0]'),
    (b'invalid receiver type P', b'pointer or interface type'),
    (b'cannot use f()', b'as *T value in argument to (*T).M'),
    (b'invalid recursive type', b'P refers to itself'),
    (b'invalid recursive type A', b'A refers to B', b'B refers to A'),
]
REVIEW_INVALID_DIAGNOSTICS = {
    'multi-result-value-receiver': INVALID_REFERENCE_DIAGNOSTICS[16],
    'recursive-pointer-alias': INVALID_REFERENCE_DIAGNOSTICS[17],
}


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('index', [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18])
def test_official_reference_rejects_identical_invalid_pointer_sources(index, tmp_path):
    body, declarations, message = INVALID[index]
    original = source(declarations, body)
    with pytest.raises(GoDiagnostic, match=message):
        compile_go(original)
    compile_reference_rejection((('invalid.go', original),), tmp_path / 'reference',
                      compiler=os.environ['PCC_GO_REFERENCE'],
                      cache=os.environ['PCC_GO_REFERENCE_CACHE'], diagnostics=INVALID_REFERENCE_DIAGNOSTICS[index])
    assert (tmp_path / 'reference' / 'invalid.go').read_bytes() == original.encode()
    assert not (tmp_path / 'reference' / 'oracle').exists()


# Preserve the reviewer's original counterexamples byte-for-byte. The larger
# fixture exercises the same mechanisms with independent full-width observers.
REVIEW_POSITIVES = [
    ('direct-array-pointer-type', 'package main; func main(){ _ = len((*[3]int)(nil)) }'),
    ('direct-struct-pointer-type', 'package main; func main(){ _ = (*struct{X int})(nil)==nil }'),
    ('delayed-pointer-method-expression', 'package main; type T int; var g T; func(t T) M(n int) int { return int(t) }; func change() int { g=9; return 0 }; func main(){ g=1; p:=&g; if (*T).M(p,change()) != 9 { for {} } }'),
]
REVIEW_INVALID = [
    ('multi-result-value-receiver', 'package main; type T int; func(t T) M(n int) int { return int(t)+n }; func f()(T,int){ return 1,2 }; func main(){ _=(*T).M(f()) }'),
    ('recursive-pointer-alias', 'package main; type P = *P; func main(){}'),
]


@pytest.mark.parametrize('name,original', REVIEW_INVALID)
def test_original_review_invalid_sources_never_reach_ir(name, original, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('review invalid source reached IR'))
    with pytest.raises(GoDiagnostic) as caught:
        compile_go(original, filename='original.go')
    assert not isinstance(caught.value, UnsupportedGo)


@pytest.mark.integration
@pytest.mark.parametrize('name,original', REVIEW_POSITIVES)
def test_original_review_programs_execute_owned(name, original, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned((('original.go', original),))
    binary = tmp_path / 'original-owned'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('name,original', REVIEW_POSITIVES)
def test_original_review_programs_match_official_raw_streams(name, original, tmp_path, monkeypatch):
    originals = (('original.go', original),)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    binary = tmp_path / 'original-owned'
    write_artifact(artifact, binary)
    reference = compile_reference(originals, tmp_path / 'reference',
                                  compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    observed = []
    for owner, path in [('pcc', binary), ('reference', reference)]:
        actual = run(path)
        observed.append(actual)
        (tmp_path / (owner + '.stdout')).write_bytes(actual.stdout)
        (tmp_path / (owner + '.stderr')).write_bytes(actual.stderr)
        (tmp_path / (owner + '.execution.json')).write_text(json.dumps({
            'input_sha256': hashlib.sha256(original.encode()).hexdigest(),
            'binary_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'returncode': actual.returncode,
        }, indent=2) + '\n')
    assert observed[0] == observed[1] == Execution(0, b'', b'')
    assert (reference.parent / 'original.go').read_bytes() == original.encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('name,original', REVIEW_INVALID)
def test_original_review_invalid_sources_match_official_rejection(name, original, tmp_path):
    with pytest.raises(GoDiagnostic) as caught:
        compile_go(original)
    assert not isinstance(caught.value, UnsupportedGo)
    compile_reference_rejection((('original.go', original),), tmp_path / 'reference',
                      compiler=os.environ['PCC_GO_REFERENCE'],
                      cache=os.environ['PCC_GO_REFERENCE_CACHE'], diagnostics=REVIEW_INVALID_DIAGNOSTICS[name])
    assert (tmp_path / 'reference' / 'original.go').read_bytes() == original.encode()
    assert not (tmp_path / 'reference' / 'oracle').exists()


REVIEW_NIL_RECEIVERS = [
    'package main; type T int; func(t T) M(n int) int { return int(t) }; func main(){ _=(*T).M(nil,0) }',
    'package main; type T int; func(t T) M(n int) int { return int(t) }; func main(){ _=(*T).M((*T)(nil),0) }',
]


@pytest.mark.parametrize('original', REVIEW_NIL_RECEIVERS)
def test_retained_nil_method_expression_requires_panic_contract(original, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('nil receiver reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.pointer-nil'


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original', REVIEW_NIL_RECEIVERS)
def test_official_reference_executes_retained_nil_method_panic_frontier(original, tmp_path):
    # This is an external-reference panic observation, not a PCC runtime pass.
    reference = compile_reference((('original.go', original),), tmp_path / 'reference',
                                  compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    actual = run(reference)
    (tmp_path / 'reference.stdout').write_bytes(actual.stdout)
    (tmp_path / 'reference.stderr').write_bytes(actual.stderr)
    (tmp_path / 'reference.execution.json').write_text(json.dumps({
        'input_sha256': hashlib.sha256(original.encode()).hexdigest(),
        'binary_sha256': hashlib.sha256(reference.read_bytes()).hexdigest(),
        'returncode': actual.returncode,
        'kind': 'external_go1.27.3_panic_frontier_not_pcc_runtime_support',
    }, indent=2) + '\n')
    # Observed pinned Go1.27.3 value-method wrapper panic, not a generic nil
    # dereference message. The former v4 assertion failure remains recorded.
    assert actual.returncode == 2 and actual.stdout == b''
    assert actual.stderr.splitlines()[0] == b'panic: value method main.T.M called using nil *T pointer'
    assert (reference.parent / 'original.go').read_bytes() == original.encode()
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.pointer-nil'


# The v3 authored original remains byte-identical. Its former acceptance node
# test_same_original_pointer_program_matches_official_raw_process_streams
# actually FAILED: official Go 1.27.3 timed out in main's overconstrained
# ImplicitReceiverOrder()==1 check (26 other selected nodes were unfinished).
# Go Order_of_evaluation leaves that plain load versus argument call unordered.
# The separate deterministic fixture/new node IDs above are new acceptance
# observations, never a relabeling of that old original or its failure.
# This node is DIAGNOSTIC ONLY: exclude its identity from passed-language
# baseline exports; it does not execute or qualify the retained original main.
DIAGNOSTIC_ONLY_TEST_IDS = (
    'tests/go/test_pointers.py::test_retained_original_pointer_order_diagnostic',
)
ORIGINAL_POINTER_SHA256 = '139227ec468ebf966943d0d60e047019570c7f629b835c45a0e7b2b0da4d270b'


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_retained_original_pointer_order_diagnostic(tmp_path, monkeypatch):
    original = units(('pointers.go',))
    observer = units(('pointers_observer_test.go',))[0][1]
    assert hashlib.sha256(original[0][1].encode()).hexdigest() == ORIGINAL_POINTER_SHA256
    original_rows = [(('ImplicitReceiverOrder' if name == 'CapturedReceiverOrder' else name), args, expected)
                     for name, args, expected in RAW]
    native, native_artifacts = {}, {}
    for name, args, expected in original_rows:
        with monkeypatch.context() as denied:
            forbid_external_compiler(denied)
            artifact = compile_owned(original, observe_entry=(name, args))
        binary = tmp_path / ('pcc-' + name)
        write_artifact(artifact, binary)
        actual = run(binary)
        assert actual.returncode == 0 and actual.stderr == b'' and len(actual.stdout) == 8
        native[name] = int.from_bytes(actual.stdout, 'little')
        native_artifacts[name] = {
            'object_sha256': hashlib.sha256(artifact.object).hexdigest(),
            'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'stdout_hex': actual.stdout.hex(), 'returncode': actual.returncode,
        }
        (tmp_path / (name + '.pcc.stdout')).write_bytes(actual.stdout)
    directory = tmp_path / 'reference'
    directory.mkdir()
    (directory / 'pointers.go').write_bytes(original[0][1].encode())
    (directory / 'observer_test.go').write_bytes(observer.encode())
    env = dict(os.environ, GOTOOLCHAIN='local', GOPROXY='off', GOSUMDB='off',
               GOWORK='off', GO111MODULE='off', CGO_ENABLED='0', GOMAXPROCS='2',
               GOCACHE=os.environ['PCC_GO_REFERENCE_CACHE'])
    binary = directory / 'observer'
    command = [os.environ['PCC_GO_REFERENCE'], 'test', '-c', '-p=2', '-o', str(binary),
               str(directory / 'pointers.go'), str(directory / 'observer_test.go')]
    build = subprocess.run(command, cwd=directory, env=env, capture_output=True, timeout=60)
    (directory / 'build.stdout').write_bytes(build.stdout)
    (directory / 'build.stderr').write_bytes(build.stderr)
    assert build.returncode == 0, (build.stdout, build.stderr)
    actual = subprocess.run([str(binary), '-test.run=^TestOriginalPointerObserver$', '-test.v'],
                            cwd=directory, env=env, capture_output=True, timeout=5)
    (directory / 'observer.stdout').write_bytes(actual.stdout)
    (directory / 'observer.stderr').write_bytes(actual.stderr)
    assert actual.returncode == 0 and actual.stderr == b''
    rows = re.findall(r'PCC_POINTER_OBSERVATION\t(\w+)\t([0-9a-f]{16})\t([0-9a-f]{16})', actual.stdout.decode())
    assert [name for name, _, _ in rows] == [name for name, _, _ in original_rows]
    assert [int(value, 16) for _, _, value in rows] == [expected for _, _, expected in original_rows]
    reference = {name: int(value, 16) for name, value, _ in rows}
    for name, _, expected in original_rows:
        if name == 'ImplicitReceiverOrder':
            assert native[name] in (1, 9) and reference[name] in (1, 9)
        else:
            assert native[name] == reference[name] == expected
    (tmp_path / 'diagnostic-observations.json').write_text(json.dumps({
        'kind': 'diagnostic_spec_allowed_observation',
        'baseline_eligible': False,
        'input_sha256': ORIGINAL_POINTER_SHA256,
        'observer_sha256': hashlib.sha256(observer.encode()).hexdigest(),
        'native': native, 'reference': reference, 'native_artifacts': native_artifacts,
        'reference_binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'reference_build_command': command,
        'preserved_original_main_result': 'historical FAIL: official Go1.27.3 timeout; not rerun or relabeled here',
    }, indent=2) + '\n')
    assert (directory / 'pointers.go').read_bytes() == original[0][1].encode()
