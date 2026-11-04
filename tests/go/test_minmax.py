"""Ordered min/max and fully checked blank functions; execution is UNRUN.

https://go.dev/ref/spec#Min_and_max
https://go.dev/ref/spec#Declarations_and_scope
https://go.dev/ref/spec#Length_and_capacity
https://go.dev/ref/spec#For_range
Whole upstream inputs keep their original package declarations and dependencies.
An upstream blank function is typechecked, never made callable by source edits.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go, compile_go_packages
from pcc.frontends.go.packages import parse_package
from pcc.frontends.go.sema import Analyzer
from tests.go.owned_execution import (
    Execution, compile_module, compile_owned, compile_reference,
    compile_reference_packages, compile_reference_rejection, write_artifact,
)
from tests.go.test_diagnostics import GRADUATED_BLANK_FUNCTION_CASE
from tests.go.test_execution import forbid_external_compiler
from tests.go.test_labels import observed


ROOT = Path(__file__).parent
FIXTURE = ROOT / 'fixtures/minmax.go'
RANGE_FIXTURE = ROOT / 'fixtures/minmax_range.go'
UPSTREAM = ROOT / 'upstream/go1.27.3'
SHIFT_ORIGINAL = 'src/internal/types/testdata/fixedbugs/issue61486.go'
FMT_ORIGINAL = 'test/fixedbugs/issue60582.go'
ORIGINALS = {
    SHIFT_ORIGINAL: ('b12a800f0d9e84003e265fb158a432c08646d8e9',
                     'a4397b17537cbf3d95d6d969a23b09db90992e6f1faf95fab754010adf45f242'),
    FMT_ORIGINAL: ('f698918098fa59bc20c1f50f36c8ae3e050a65ee',
                   '5560002195cb3d895aec27b0dd1f559ae2019af00bbdf1863d40f8ebdc181ce7'),
}
ENTRIES = (
    ('UnsignedMin', (0xffffffffffffffff, 0x8000000000000000), 0x8000000000000000, 8),
    ('UnsignedMax', (0xffffffffffffffff, 0x8000000000000000), 0xffffffffffffffff, 8),
    ('SignedMin', (-(1 << 63), (1 << 63)-1), -(1 << 63), 8),
    ('SignedMax', (-(1 << 63), (1 << 63)-1), (1 << 63)-1, 8),
    ('NarrowMin', (-128, 127), -128, 1), ('Named', (), 2, 2),
    ('RuneKind', (), 131, 4), ('OneShift', (64,), 0, 8),
    ('RuneShift', (33,), 0, 4), ('TypedShift', (63,), 1 << 63, 8),
    ('ExactConstant', (), (1 << 48)+7, 8), ('Ordered', (), 3121, 8),
    ('MultiResult', (), 1, 8), ('StringOrder', (), 3122, 8),
    ('StringMaxBytes', (), 128, 8), ('StringTie', (), 12, 8),
    ('StringResults', (), 1, 8), ('MinLenCall', (), 311, 8),
    ('BlankBodies', (), 42, 8),
)
RANGE_ENTRIES = (
    ('ArrayNoIndex', (), 31422, 8), ('ArrayOneIndex', (), 25713, 8),
    ('ArrayBlankValue', (), 641, 8), ('IntegerBound', (), 233, 8),
    ('VariableMin', (3, 1), 1, 8), ('VariableMax', (3, 1), 1, 8),
    ('ConstantMin', (), 1, 8), ('ConstantMax', (), 1, 8),
)


def units(path):
    return ((path.name, path.read_bytes().decode('utf-8')),)


def source(body='', declarations=''):
    return 'package main;' + (declarations+';' if declarations else '') + 'func main(){'+body+'}'


def upstream_packages():
    # The whole blank-function original remains in its original package p.
    # This authored exported constant lets the main refer to that package;
    # it does not call or replace the unnameable upstream function.
    return {
        'main': (('main.go', 'package main;import "p";func main(){if p.Probe!=7{for{}}}'),),
        'p': units(UPSTREAM / SHIFT_ORIGINAL) + (('probe.go', 'package p;const Probe=7'),),
    }


def flattened(packages):
    return tuple((path+'/'+name, text) for path, original in packages.items() for name, text in original)


GRADUATED_BLANK_TYPE_ORIGINAL = source(declarations='type _ int')
VALID = (
    GRADUATED_BLANK_FUNCTION_CASE[1],
    source(declarations='func _(){};func _(){}'),
    source(declarations='func _[T any](x T)T{return x};func _[T comparable](x T)T{return x}'),
    source('min:=3;max:=4;if min+max!=7{for{}}'),
    source('const n=len([1]int{min(1,2)});if n!=1{for{}}'),
    source('var x int8=min(1<<100,7);if x!=7{for{}}'),
    source('var x int32=max(1,2,\'A\');if x!=65{for{}}'),
    source('var x uint8=min(255,0);if x!=0{for{}}'),
    GRADUATED_BLANK_TYPE_ORIGINAL,
)
INVALID = (
    (source('_=min()'), 'at least one', 'not enough arguments'),
    (source('_=max(true)'), 'ordered', 'cannot be ordered'),
    (source('_=min(nil)'), 'ordered', 'cannot be ordered|untyped nil'),
    (source('_=min("x",1)'), 'mismatched', 'mismatched types'),
    (source('_=max(int(1),int64(2))'), 'mismatched', 'mismatched types'),
    (source('_=min(A(1),B(2))', 'type A int;type B int'), 'mismatched', 'mismatched types'),
    (source('_=min(1<<100,0,int8(1))'), 'overflows', 'overflows'),
    (source('_=max(-1,uint8(0))'), 'overflows', 'overflows'),
    (source('x:=min(\'A\');var y int=x;_=y'), 'cannot use', 'cannot use'),
    (source(declarations='func f(s uint)uint64{return min(1<<s)}'), 'cannot use int as uint64', 'cannot use'),
    (source('var x int;const n=len([1]int{min(x)});_=n'), 'constant', 'not constant'),
    (source('var x int;const n=cap([1]int{max(x)});_=n'), 'constant', 'not constant'),
    (source('min(1,2)'), 'expression statements', 'not used'),
    (source('min:=3;_=min(1)'), 'non-function', 'cannot call'),
    (source('_=min[uint64](1,2)'), 'generic function', 'cannot index|cannot instantiate'),
    (source(declarations='func f[T any](x T)T{return min(x)}'), 'ordered', 'cannot be ordered'),
    (source('_=min([1]int{1})'), 'ordered', 'cannot be ordered'),
    (source('_=min(f())', 'func f(){}'), 'ordered', 'used as value'),
    (source('_=min(1,f())', 'func f()(int,int){return 2,3}'), 'multiple-value', 'multiple-value'),
    (source('_()','func _(){}'), 'undefined function _', 'cannot use _'),
    (source(declarations='func _()int{}'), 'missing return', 'missing return'),
    (source(declarations='func _(){x:=1}'), 'not used', 'not used'),
    (source(declarations='func _(){goto L;var x int;L:_=x}'), 'jumps over', 'jumps over'),
    (source(declarations='func _[T any](x T)T{return x+1}'), 'cannot use|arithmetic|mismatched', 'cannot convert|invalid operation'),
)
FRONTIERS = (
    (source(declarations='func f(x float64)float64{return min(x,1)}'), 'go.unsupported.floating-types'),
    (source(declarations='func f[T ~int](x T)T{return min(x,1)}'), 'go.unsupported.generic-boundaries'),
    (source(declarations='func _(){var p *int;_=*p}'), 'go.unsupported.pointer-nil'),
    (source(declarations='func f(p *int)int{return len([1]int{min(*p)})}'), 'go.unsupported.pointer-nil'),
    (source(declarations='func f()int{return 1};var x=min(f(),2)'), 'go.unsupported.package-initialization'),
    (source(declarations='type T int;func (t T)_(){}'), 'go.unsupported.blank-method-declarations'),
)


@pytest.mark.parametrize('path,identity', ORIGINALS.items())
def test_complete_upstream_originals_keep_pinned_bytes_and_license(path, identity):
    data = (UPSTREAM / path).read_bytes()
    provenance = json.loads((ROOT / 'upstream/provenance.json').read_text())
    assert provenance['commit'] == '2aae1000ea1d4b28923b96db28acf4e3b99d6aff'
    assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest() == identity[0]
    assert hashlib.sha256(data).hexdigest() == identity[1]
    assert provenance['files'][path] == identity[1]
    assert provenance['git_blobs'][path] == identity[0]
    assert hashlib.sha256((UPSTREAM / 'LICENSE').read_bytes()).hexdigest() == (
        '911f8f5782931320f5b8d1160a76365b83aea6447ee6c04fa6d5591467db9dad')
    assert units(UPSTREAM / path)[0][1].encode('utf-8') == data


def test_complete_official_shift_original_checks_blank_body_and_defaults_min():
    program = parse_package(units(UPSTREAM / SHIFT_ORIGINAL), require_main=False)
    owner = Analyzer(program, require_main=False, package_path='p')
    owner.analyze()
    assert '_' not in owner.functions
    blank, = program.functions
    assert blank.token.text == '_'
    expr, = blank.body[0].exprs
    assert expr.kind == 'minmax' and expr.type == 'int'
    assert expr.args[0].type == 'int' and expr.args[0].args[0].type == 'int'


def test_constant_kinds_do_not_overflow_an_invented_addition_or_default_early():
    text = source(declarations="const r=min('A',66);const i=max(1,2);"
        "const u=max(uint64(0xffffffffffffffff),uint64(1));"
        "const big=max(1<<100,7);const s=min(\"\\xff\",\"\\x80\");var v=min('A',66)")
    owner = Analyzer(parse_package((('original.go', text),), require_main=True))
    owner.analyze()
    assert (owner.globals['r'].type, owner.globals['r'].constant) == ('untyped rune', 65)
    assert (owner.globals['i'].type, owner.globals['i'].constant) == ('untyped int', 2)
    assert (owner.globals['u'].type, owner.globals['u'].constant) == ('uint64', (1 << 64)-1)
    assert (owner.globals['big'].type, owner.globals['big'].constant) == ('untyped int', 1 << 100)
    assert (owner.globals['s'].type, owner.globals['s'].constant) == ('untyped string', b'\x80')
    assert owner.globals['v'].type == 'int32'


def test_complete_range_original_keeps_pinned_bytes_and_owned_ir(monkeypatch):
    data = RANGE_FIXTURE.read_bytes()
    assert hashlib.sha256(data).hexdigest() == 'e1223cd77891ac12e200b9f33ad6396c4b915c16a4ff48e84452a9d03327ee5b'
    assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest() == '024ca90bdea1aa7001067ec32132758b7c26ebfd'
    original = units(RANGE_FIXTURE)
    assert original[0][1].encode('utf-8') == data
    forbid_external_compiler(monkeypatch)
    compile_go(original[0][1], original[0][0])


def test_nonconstant_minmax_counts_for_range_even_without_user_calls():
    owner = Analyzer(parse_package(units(RANGE_FIXTURE), require_main=True))
    owner.analyze()
    expected = {'VariableMin': True, 'VariableMax': True,
                'ConstantMin': False, 'ConstantMax': False}
    actual = {}
    for function in owner.program.functions:
        if function.token.text in expected:
            statement, = [stmt for stmt in function.body if stmt.kind == 'range']
            actual[function.token.text] = statement.evaluate_range
    assert actual == expected


@pytest.mark.parametrize('original', VALID)
def test_complete_valid_and_former_boundary_originals_build_owned_ir(original, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(original, 'original.go')


def test_distinct_blank_declarations_keep_private_checked_ir_owners(monkeypatch):
    forbid_external_compiler(monkeypatch)
    module = compile_go(units(FIXTURE)[0][1], FIXTURE.name)
    blanks = [item for name, item in module.globals.items() if '_blank_' in name]
    assert len(blanks) == 2  # Unnameable generic declarations have no requested instances.
    assert len({item.name for item in blanks}) == 2
    assert all(item.linkage == 'internal' for item in blanks)
    assert all(block.is_terminated for item in blanks for block in item.blocks)
    assert not any('_instance_' in name for name in module.globals)


def test_complete_upstream_package_graph_has_owned_ir_without_rewriting_original(monkeypatch):
    forbid_external_compiler(monkeypatch)
    packages = upstream_packages()
    module = compile_go_packages(packages)
    blank, = [item for name, item in module.globals.items() if '_blank_' in name]
    assert blank.linkage == 'internal' and all(block.is_terminated for block in blank.blocks)
    assert packages['p'][0][1].encode('utf-8') == (UPSTREAM / SHIFT_ORIGINAL).read_bytes()


@pytest.mark.parametrize('original,message,reference', INVALID)
def test_invalid_originals_fail_before_ir_without_capability_reclassification(original, message, reference, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('invalid original reached IR'))
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(original, 'original.go')
    assert type(caught.value) is GoDiagnostic


@pytest.mark.parametrize('original,code', FRONTIERS)
def test_unimplemented_runtime_and_type_domains_remain_explicit(original, code):
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original, 'original.go')
    assert caught.value.code == code


def test_complete_fmt_original_retains_missing_original_dependency():
    original = units(UPSTREAM / FMT_ORIGINAL)
    with pytest.raises(GoDiagnostic, match='import source unavailable: fmt'):
        compile_go(original[0][1], original[0][0])


@pytest.mark.integration
@pytest.mark.parametrize('entry,arguments,expected,byte_width', ENTRIES)
def test_independent_native_results_keep_all_declared_bits(entry, arguments, expected, byte_width, tmp_path, monkeypatch):
    original = units(FIXTURE)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(original, observe_entry=(entry, arguments))
    binary = tmp_path / 'owned'
    write_artifact(artifact, binary)
    raw = (expected & ((1 << (8*byte_width))-1)).to_bytes(byte_width, 'little')
    (tmp_path / 'observer.json').write_text(json.dumps({
        'entry': entry, 'arguments': arguments, 'bytes': byte_width, 'expected_hex': raw.hex(),
    }, indent=2)+'\n')
    assert observed(binary, tmp_path / 'observation', owner=artifact.owner,
                    originals=original) == Execution(0, raw, b'')


@pytest.mark.integration
@pytest.mark.parametrize('entry,arguments,expected,byte_width', RANGE_ENTRIES)
def test_complete_range_original_preserves_side_effects_once(entry, arguments, expected, byte_width, tmp_path, monkeypatch):
    original = units(RANGE_FIXTURE)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(original, observe_entry=(entry, arguments))
    binary = tmp_path / 'owned'
    write_artifact(artifact, binary)
    raw = expected.to_bytes(byte_width, 'little')
    (tmp_path / 'observer.json').write_text(json.dumps({
        'entry': entry, 'arguments': arguments, 'bytes': byte_width, 'expected_hex': raw.hex(),
        'source_sha256': hashlib.sha256(RANGE_FIXTURE.read_bytes()).hexdigest(),
    }, indent=2)+'\n')
    assert observed(binary, tmp_path / 'observation', owner=artifact.owner,
                    originals=original) == Execution(0, raw, b'')


@pytest.mark.integration
def test_whole_fixture_and_original_package_companion_execute_owned_main(tmp_path, monkeypatch):
    cases = ((units(FIXTURE), False), (units(RANGE_FIXTURE), False),
             (flattened(upstream_packages()), True))
    for index, (original, graph) in enumerate(cases):
        with monkeypatch.context() as denied:
            forbid_external_compiler(denied)
            artifact = compile_module(compile_go_packages(upstream_packages())) if graph else compile_owned(original)
        binary = tmp_path / ('owned-'+str(index))
        write_artifact(artifact, binary)
        assert observed(binary, tmp_path / ('observation-'+str(index)), owner=artifact.owner,
                        originals=original) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_complete_and_graduated_originals_match_official_raw_execution(tmp_path, monkeypatch):
    originals = (units(FIXTURE), units(RANGE_FIXTURE)) + tuple((('original.go', text),) for text in VALID)
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
def test_identical_upstream_package_graph_has_official_source_and_raw_process_evidence(tmp_path, monkeypatch):
    packages = upstream_packages()
    original = flattened(packages)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_module(compile_go_packages(packages))
    binary = tmp_path / 'owned'
    write_artifact(artifact, binary)
    reference = compile_reference_packages(packages, tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    for name, text in original:
        assert (reference.parent / 'gopath/src' / name).read_bytes() == text.encode('utf-8')
    actual = observed(binary, tmp_path / 'pcc-observation', owner=artifact.owner, originals=original)
    expected = observed(reference, tmp_path / 'go-observation', owner='official-go', originals=original)
    assert actual == expected == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original,message,reference', INVALID)
def test_identical_invalid_originals_have_attributed_official_rejections(original, message, reference, tmp_path):
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
def test_complete_fmt_original_has_reference_only_execution_without_pcc_stub(tmp_path):
    original = units(UPSTREAM / FMT_ORIGINAL)
    with pytest.raises(GoDiagnostic, match='import source unavailable: fmt'):
        compile_go(original[0][1], original[0][0])
    reference = compile_reference(original, tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert (reference.parent / original[0][0]).read_bytes() == original[0][1].encode('utf-8')
    assert observed(reference, tmp_path / 'go-observation', owner='official-go',
                    originals=original) == Execution(0, b'5\n7\n', b'')
