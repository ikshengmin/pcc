"""Go UTF-8 string range over existing immutable descriptors; CI obligations.

Specification: https://go.dev/ref/spec#For_range (byte offsets, rune values,
malformed UTF-8 advances one byte, capture and iteration bindings),
https://go.dev/ref/spec#Assignments (target evaluation before stores),
https://go.dev/ref/spec#String_types (arbitrary bytes and embedded NUL),
https://go.dev/ref/spec#Terminating_statements (range is not an infinite for).
These tests are authored, not locally executed. Native/reference CI must run
the same original source without a host decoder or rewritten oracle program.
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
from tests.go.test_diagnostics import GRADUATED_STRING_RANGE_CASE
from tests.go.test_array_range import GRADUATED_STRING_RANGE
from tests.go.test_execution import forbid_external_compiler, units
from tests.go.test_nullable_pointers import record_pair
from tests.go.test_pointer_array_range import GRADUATED_STRING_RANGE as POINTER_STRING_RANGE
from tests.go.test_records import source


GRADUATED = [
    source(GRADUATED_STRING_RANGE[1], GRADUATED_STRING_RANGE[0]),
    GRADUATED_STRING_RANGE_CASE[1],
]
assert POINTER_STRING_RANGE[1] == GRADUATED[0]


@pytest.mark.parametrize('original', GRADUATED)
def test_former_string_range_frontiers_are_retained_as_positive_sources(original, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(original)


@pytest.mark.parametrize('body', [
    'for range ""{}', 'for range "abc"{}', 'for _=range "é"{}',
    'for _,_=range "é"{}', 'for i,_:=range "é"{acceptIndex(i)}',
    'for i,r:=range "é"{acceptIndex(i);acceptRune(r)}',
    'var i int;var r rune;for i,r=range "é"{};_=i;_=r',
    'for _,r:=range Text("é"){acceptRune(r)}',
    's:="é";for s:=range s{acceptIndex(s)};_=s',
    'for i,r:=range "é"{i:=i+1;r:=r+1;_=i;_=r}',
    'for range "é"{switch{default:break};continue}',
    'var p *int;for range ""{_=*p}',
])
def test_string_range_types_scopes_and_empty_flow(body, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(source('type Text string;func acceptIndex(i int){};func acceptRune(r rune){}', body))


INVALID = [
    ('for i,j,k:=range "abc"{_=i;_=j;_=k}', '', 'at most two', 'at most two iteration variables'),
    ('for _,_:=range "abc"{}', '', 'at least one new', 'no new variables'),
    ('for i,i:=range "abc"{_=i}', '', 'duplicate declaration', 'i redeclared in this block'),
    ('var i int64;for i=range "abc"{};_=i', '', 'cannot use int as int64', 'cannot use'),
    ('var r int;for _,r=range "abc"{};_=r', '', 'cannot use int32 as int', 'cannot use'),
    ('var r Rune;for _,r=range "abc"{};_=r', 'type Rune int32', 'cannot use int32 as Rune', 'cannot use'),
    ('for i:=range "abc"{}', '', 'declared and not used', 'declared and not used'),
    ('for i:=range "abc"{_=i};_=i', '', 'undefined name', 'undefined'),
    ('', 'func f()int{for range "abc"{return 1}}', 'missing return', 'missing return'),
]


@pytest.mark.parametrize('body,declarations,message,reference_message', INVALID)
def test_invalid_string_ranges_fail_before_ir(body, declarations, message, reference_message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid string range reached IR'))
    with pytest.raises(GoDiagnostic, match=message):
        compile_go(source(declarations, body))


@pytest.mark.parametrize('declarations,body,code', [
    ('func f(s string){var p *int;for range s{_=*p}}', '', 'pointer-nil'),
    ('', 'var p *int;for range "A"{_=*p}', 'pointer-nil'),
    ('func f(s string){var p *int;x:=1;for range s{p=&x};_=*p}', '', 'pointer-nil'),
    ('', 'for i:=range "A"{p:=&i;_=p}', 'pointer-iteration-identity'),
    ('', 'for _,r:=range "A"{p:=&r;_=p}', 'pointer-iteration-identity'),
    ('func f(a,b string){for range a+b{}}', '', 'dynamic-string-concatenation'),
    ('func f(x int){for range string(x){}}', '', 'dynamic-string-conversion'),
])
def test_string_range_does_not_admit_new_storage_or_pointer_proofs(declarations, body, code):
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(source(declarations, body))
    assert caught.value.code == 'go.unsupported.' + code


def test_complete_string_range_program_uses_owned_ir_only(monkeypatch):
    forbid_external_compiler(monkeypatch)
    module = compile_go(units(('string_range.go',))[0][1])
    text = str(module)
    assert 'string.rune.invalid' in text and 'string.range.post' in text
    assert 'declare ' not in text
    assert 'string.rune.available' in text


RAW = [
    ('ValidScalars', 12), ('InvalidLeads', 10), ('InvalidScalars', 14),
    ('Truncated', 123), ('InvalidThenValid', 7), ('LaterContinuations', 7), ('IndexOnly', 137),
    ('BlankRune', 137), ('Bare', 4), ('BothBlank', 4), ('NamedString', 300),
    ('RawString', 20246), ('CaptureOnce', 1300), ('IndexCaptureOnce', 104),
    ('BareCaptureOnce', 102), ('EmptyCapture', 1), ('ZeroString', 0),
    ('AssignmentOrder', 16622), ('TargetOnce', 2065), ('HiddenInduction', 4),
    ('ShadowSource', 302), ('ContinueSwitchBreak', 37), ('NestedBreak', 2),
    ('EarlyReturn', 1114111), ('EmptyTargets', 1719), ('EmptyPointerFlow', 7),
    ('GuardedPointerFlow', 7),
]


@pytest.mark.integration
@pytest.mark.parametrize('name,expected', RAW)
def test_original_string_range_helpers_have_full_width_native_observations(name, expected, tmp_path, monkeypatch):
    original = units(('string_range.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(original, observe_entry=(name, ()))
    binary = tmp_path / 'string-range-observer'
    write_artifact(artifact, binary)
    actual = run(binary)
    raw = expected.to_bytes(8, 'little')
    (tmp_path / 'observation.json').write_text(json.dumps({
        'owner': artifact.owner, 'function': name, 'arguments': [],
        'source_sha256': hashlib.sha256(original[0][1].encode()).hexdigest(),
        'executable_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'returncode': actual.returncode, 'stdout_hex': actual.stdout.hex(),
        'stderr_hex': actual.stderr.hex(), 'expected_hex': raw.hex(),
    }, indent=2) + '\n')
    assert actual == Execution(0, raw, b'')


@pytest.mark.integration
@pytest.mark.parametrize('originals', [
    pytest.param(None, id='complete-program'),
    *[pytest.param((('original.go', text),), id='graduated-' + str(i)) for i, text in enumerate(GRADUATED)],
])
def test_complete_and_graduated_string_range_programs_execute_owned(originals, tmp_path, monkeypatch):
    originals = units(('string_range.go',)) if originals is None else originals
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    binary = tmp_path / 'string-range-main'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('originals', [
    pytest.param(None, id='complete-program'),
    *[pytest.param((('original.go', text),), id='graduated-' + str(i)) for i, text in enumerate(GRADUATED)],
])
def test_identical_string_range_programs_match_official_raw_streams(originals, tmp_path, monkeypatch):
    originals = units(('string_range.go',)) if originals is None else originals
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    binary = tmp_path / 'string-range-main'
    write_artifact(artifact, binary)
    reference = compile_reference(originals, tmp_path / 'reference', compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    record_pair(tmp_path, hashlib.sha256(originals[0][1].encode()).hexdigest(), binary, reference)
    assert (reference.parent / originals[0][0]).read_bytes() == originals[0][1].encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('body,declarations,message,reference_message', INVALID)
def test_official_go_rejects_identical_invalid_string_ranges(body, declarations, message, reference_message, tmp_path):
    original = source(declarations, body)
    with pytest.raises(GoDiagnostic, match=message):
        compile_go(original)
    receipt = compile_reference_rejection((('invalid.go', original),), tmp_path / 'reference',
                                          compiler=os.environ['PCC_GO_REFERENCE'],
                                          cache=os.environ['PCC_GO_REFERENCE_CACHE'], diagnostics=reference_message)
    assert receipt['classification'] == 'normal_source_rejection'
    assert (tmp_path / 'reference' / 'invalid.go').read_bytes() == original.encode()
