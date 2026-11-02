"""Live pointer-to-array range, not array snapshots or slice backing allocation.

https://go.dev/ref/spec#For_range
https://go.dev/ref/spec#Length_and_capacity
https://go.dev/ref/spec#Assignments https://go.dev/ref/spec#Address_operators
The original input bytes below are retained for each positive/negative obligation.
"""
import hashlib
import json
import os

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from tests.go.owned_execution import (
    Execution, compile_owned, compile_reference, compile_reference_rejection,
    run, write_artifact,
)
from tests.go.test_diagnostics import original as diagnostic_original
from tests.go.test_execution import forbid_external_compiler, units
from tests.go.test_nullable_pointers import record_pair
from tests.go.test_records import source

GRADUATED = [
    source('', 'a:=[2]int{};p:=&a;for range p{}'),
    diagnostic_original('func f(p *[2]int){for range p{}}'),
]


@pytest.mark.parametrize('original', GRADUATED)
def test_original_pointer_array_frontiers_graduate_without_rewriting(original, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(original)


@pytest.mark.parametrize('body', [
    'for range p{}', 'for i:=range p{_=i}', 'for i,_:=range p{_=i}',
    'for _,_=range p{}', 'for range (*[2]int)(nil){}',
    'if false{for _,v:=range p{_=v}}',
])
def test_nil_index_only_and_unreachable_element_ranges_do_not_invent_indirection(body, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(source('func f(p *[2]int){'+body+'}'))


@pytest.mark.parametrize('declarations,body,evaluated,pointer', [
    ('', 'var p *[2]int;for range p{}', False, True),
    ('', 'var p *[2]int;for i,_:=range p{_=i}', False, True),
    ('', 'a:=[2]int{};for _,v:=range &a{_=v}', True, True),
    ('', 'var p *[0]int;for _,v:=range p{_=v}', True, True),
    ('func f()[0]int{return [0]int{}}', 'for range f(){}', True, False),
    ('func f()[2]int{return [2]int{}}', 'for i,_:=range f(){_=i}', True, False),
])
def test_typed_range_retains_constant_length_call_and_capture_contract(declarations, body, evaluated, pointer):
    from pcc.frontends.go.pointer_flow import validate_pointer_flow
    from pcc.frontends.go.records import underlying
    from pcc.frontends.go.sema import Analyzer
    from pcc.frontends.go.syntax import GoParser
    from pcc.frontends.go.types import PointerType
    program = GoParser().parse(source(declarations, body))
    Analyzer(program).analyze()
    validate_pointer_flow(program)
    main = next(f for f in program.functions if f.token.text == 'main')
    loop = next(s for s in main.body if s.kind == 'range')
    assert loop.evaluate_range is evaluated
    assert isinstance(underlying(loop.exprs[0].type), PointerType) is pointer
    if len(loop.targets) == 2 and loop.targets[-1].value != '_':
        assert any(t is loop.exprs[0] for t in main.temporaries)


INVALID = [
    ('var p *[2]int;for i,j,k:=range p{_=i;_=j;_=k}', 'at most two', 'at most two'),
    ('var p *[2]int;for _,_:=range p{}', 'at least one new', 'no new variables'),
    ('var p *[2]int;for i,i:=range p{_=i}', 'duplicate declaration', 'repeated|redeclared'),
    ('var p *[2]int;var i int64;for i=range p{}', 'cannot use int as int64', 'cannot use'),
    ('a:=[2]uint64{};var v int;for _,v=range &a{}', 'cannot use uint64 as int', 'cannot use'),
    ('var p *[2]int;for i=range p{}', 'undefined name', 'undefined'),
    ('var p *[2]int;const i=0;for i=range p{}', 'non-addressable', 'cannot assign'),
    ('var p *[2]int;for (i):=range p{_=i}', 'unparenthesized', r'cannot declare \(i\)'),
    ('var p *[2]int;for i:=range p{_=i};_=i', 'undefined name', 'undefined'),
]


@pytest.mark.parametrize('body,message,reference_message', INVALID)
def test_invalid_pointer_array_ranges_fail_before_ir(body, message, reference_message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid range reached IR'))
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(source('', body))
    assert not isinstance(caught.value, UnsupportedGo)


UNSUPPORTED = [
    ('pointer-nil', source('func f(p *[2]int){for _,v:=range p{_=v}}')),
    ('pointer-nil', source('', 'var p *[2]int;for _,v:=range p{_=v}')),
    ('pointer-nil', source('func f(p *[2]int){if p!=nil{p=nil;for _,v:=range p{_=v}}}')),
    ('pointer-nil', source('func f(p *[0]int){for _,v:=range *p{_=v}}')),
    ('pointer-iteration-identity', source('', 'a:=[2]int{};for _,v:=range &a{_=&v}')),
    ('pointer-escape', source('', 'a:=[2]int{};p:=&a;{b:=[2]int{};p=&b};for _,v:=range p{_=v}')),
    ('pointer-signatures', source('var a [2]int;func f()*[2]int{return &a}', 'for range f(){}')),
    ('pointer-signatures', source('var a [0]int;func f()*[0]int{return &a}', 'for _,v:=range f(){_=v}')),
    ('non-array-range', source('', 'for range "abc"{}')),
]


# Preserve this original verbatim; test_string_range owns its positive/native/
# same-input reference obligations. The remaining pointer frontiers stay live.
GRADUATED_STRING_RANGE = ('non-array-range', source('', 'for range "abc"{}'))
ACTIVE_UNSUPPORTED = [case for case in UNSUPPORTED if case != GRADUATED_STRING_RANGE]


@pytest.mark.parametrize('code,original', ACTIVE_UNSUPPORTED)
def test_valid_unsupported_pointer_array_frontiers_keep_exact_inputs(code, original, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported range reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.' + code


RAW = [
    ('WideSum', 0xfedcba987654320f), ('LiveMutation', 51), ('CapturedPointer', 67),
    ('CapturedDespiteNil', 109), ('NilIndices', 6), ('NilBare', 3),
    ('NilBlankValue', 6), ('NilBlankAssignment', 3), ('EmptyNilElements', 7),
    ('EmptyNilAssignment', 89), ('NamedArray', 14), ('AliasArray', 15),
    ('GuardedCalls', 105), ('AssignmentTargets', 9948), ('TargetsOnce', 413),
    ('Control', 31), ('SignedValues', 5), ('BoolValues', 2), ('ShadowPointer', 12),
]


@pytest.mark.integration
@pytest.mark.parametrize('name,expected', RAW)
def test_pointer_array_helpers_have_full_width_native_observations(name, expected, tmp_path, monkeypatch):
    original = units(('pointer_array_range.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(original, observe_entry=(name, ()))
    binary = tmp_path / 'pointer-array-observer'
    write_artifact(artifact, binary)
    actual = run(binary)
    (tmp_path / 'observation.json').write_text(json.dumps({
        'owner': artifact.owner, 'function': name,
        'source_sha256': hashlib.sha256(original[0][1].encode()).hexdigest(),
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'returncode': actual.returncode, 'stdout_hex': actual.stdout.hex(),
        'stderr_hex': actual.stderr.hex(), 'expected_hex': expected.to_bytes(8, 'little').hex(),
    }, indent=2) + '\n')
    assert actual == Execution(0, expected.to_bytes(8, 'little'), b'')


@pytest.mark.integration
def test_complete_pointer_array_program_main_executes_owned(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units(('pointer_array_range.go',)))
    binary = tmp_path / 'pointer-array-main'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('originals', [
    pytest.param(None, id='complete-program'),
    *[pytest.param((('original.go', text),), id='graduated-'+str(i)) for i, text in enumerate(GRADUATED)],
])
def test_identical_pointer_array_originals_match_official_raw_streams(originals, tmp_path, monkeypatch):
    originals = units(('pointer_array_range.go',)) if originals is None else originals
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    binary = tmp_path / 'pointer-array-main'
    write_artifact(artifact, binary)
    reference = compile_reference(originals, tmp_path / 'reference', compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    record_pair(tmp_path, hashlib.sha256(originals[0][1].encode()).hexdigest(), binary, reference)
    assert (reference.parent / originals[0][0]).read_bytes() == originals[0][1].encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('body,message,reference_message', INVALID)
def test_official_go_rejects_identical_invalid_pointer_array_ranges(body, message, reference_message, tmp_path):
    original = source('', body)
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
def test_official_go_builds_identical_unsupported_range_frontiers_without_execution(code, original, tmp_path):
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.' + code
    reference = compile_reference((('original.go', original),), tmp_path / 'reference',
                                  compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert reference.is_file()
    assert (reference.parent / 'original.go').read_bytes() == original.encode()
