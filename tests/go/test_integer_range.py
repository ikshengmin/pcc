"""Go integer range: typed bounds, capture/evaluation, iteration and CFG rules.

Specification: https://go.dev/ref/spec#For_range
https://go.dev/ref/spec#Assignments https://go.dev/ref/spec#Representability
https://go.dev/ref/spec#For_statements https://go.dev/ref/spec#Terminating_statements
The same complete program is executed by PCC and the explicit official oracle.
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


# Preserve both exact formerly rejected input byte strings. These are now
# positive execution obligations, not deletions or replacement success inputs.
GRADUATED = [source('', 'for range 3{}'), diagnostic_original(body='for range 3{}')]


@pytest.mark.parametrize('original', GRADUATED)
def test_former_integer_range_frontiers_are_retained_as_positive_sources(original, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(original)


@pytest.mark.parametrize('dtype', [
    'int', 'int8', 'int16', 'int32', 'int64', 'uint', 'uint8', 'uint16',
    'uint32', 'uint64', 'uintptr', 'byte', 'rune', 'Count',
])
@pytest.mark.parametrize('assignment', [False, True])
def test_integer_range_retains_bound_and_iteration_type(dtype, assignment, monkeypatch):
    declarations = 'type Count uint16;func accept(x ' + dtype + '){}'
    body = ('var i ' + dtype + ';for i=range ' + dtype + '(3){accept(i)}' if assignment else
            'for i:=range ' + dtype + '(3){accept(i)}')
    forbid_external_compiler(monkeypatch)
    compile_go(source(declarations, body))


@pytest.mark.parametrize('body', [
    'for range 0{}', 'for range -1{}', 'for _=range 3{}',
    'for range uint64(1<<63){break}',
    'var i uint64;for i=range 1<<63{break};_=i',
    'var i uint8;for i=range 255{};_=i',
    'var i Count;for i=range 3{};_=i',
    'n:=3;for n:=range n{_=n};_=n',
    'for i:=range 3{i:=i+1;_=i}',
    'for i:=range 3{_=i;for i:=range 2{_=i}}',
    'for range 3{switch{default:break};continue}',
])
def test_integer_range_scope_and_untyped_context(body, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(source('type Count uint16', body))


INVALID = [
    ('for i,j:=range 3{_=i;_=j}', '', 'at most one', 'only one iteration variable'),
    ('for i,_:=range 3{_=i}', '', 'at most one', 'only one iteration variable'),
    ('for _,_=range 3{}', '', 'at most one', 'only one iteration variable'),
    ('for _:=range 3{}', '', 'at least one new', 'no new variables'),
    ('for i:=range 3{}', '', 'declared and not used', 'declared and not used'),
    ('for i:=range 3{_=i};_=i', '', 'undefined name', 'undefined: i'),
    ('for i=range 3{}', '', 'undefined name', 'undefined: i'),
    ('const i=1;for i=range 3{}', '', 'constant or non-addressable', 'cannot assign'),
    ('var i bool;for i=range 3{};_=i', '', 'integer iteration variable', 'cannot use'),
    ('var i string;for i=range 3{};_=i', '', 'integer iteration variable', 'cannot use'),
    ('var i uint8;for i=range 256{};_=i', '', 'overflows', 'overflows'),
    ('var i uint8;for i=range -1{};_=i', '', 'overflows', 'overflows'),
    ('for range 1<<63{}', '', 'overflows', 'overflows'),
    ('for i:=range 1<<63{_=i}', '', 'overflows', 'overflows'),
    ('var i int64;for i=range int(3){};_=i', '', 'cannot use int as int64', 'cannot use'),
    ('var i uint8;for i=range uint16(3){};_=i', '', 'cannot use uint16 as uint8', 'cannot use'),
    ('var i A;for i=range B(3){};_=i', 'type A int;type B int', 'cannot use B as A', 'cannot use'),
    ('for (i):=range 3{_=i}', '', 'unparenthesized', 'cannot declare'),
    ('var a [1]int;for a[0]:=range 3{}', '', 'requires identifiers', 'cannot declare'),
    ('for range true{}', '', 'fixed array or integer', 'cannot range over'),
    ('for range f(){}', 'func f()(int,int){return 1,2}', 'fixed array or integer', 'multiple-value'),
    ('', 'func f()int{for range 1{return 7}}', 'missing return', 'missing return'),
    ('for range 3{fallthrough}', '', 'final non-empty', 'fallthrough'),
    ('for range 3{};continue', '', 'outside loop', 'continue'),
]


@pytest.mark.parametrize('body,declarations,message,reference_message', INVALID)
def test_invalid_integer_range_fails_before_ir(body, declarations, message, reference_message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid integer range reached IR'))
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(source(declarations, body))
    assert not isinstance(caught.value, UnsupportedGo)


@pytest.mark.parametrize('declaration', [
    'func f(p *int){for range 0{_=*p}}',
    'func f(p *int){for range -3{_=*p}}',
    'func f(p *int){for *p=range 0{}}',
    'func f(p *int){if p==nil{return};for range 3{_=*p}}',
    'func f(p *int){if p==nil{return};for range *p{_=*p}}',
    'func f(p *[3]int){for range len(*p){}}',
])
def test_integer_range_pointer_cfg_keeps_empty_and_guarded_edges(declaration, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(source(declaration))


@pytest.mark.parametrize('code,declaration', [
    ('pointer-nil', 'func f(p *int){for range 1{_=*p}}'),
    ('pointer-nil', 'func f(p *int,n int){for range n{_=*p}}'),
    ('pointer-nil', 'func f(p *int,n int){x:=1;for range n{p=&x};_=*p}'),
    ('pointer-nil', 'func f(p *int){if p==nil{return};for range 3{_=*p;p=nil}}'),
    ('pointer-nil', 'func f(p *int){if p==nil{return};for range 3{p=nil;continue};_=*p}'),
    ('pointer-nil', 'func f(p *int){for range *p{}}'),
    ('pointer-nil', 'func f(p *int,n int){for *p=range n{}}'),
    ('pointer-iteration-identity', 'func f(){for i:=range 3{p:=&i;_=p}}'),
])
def test_integer_range_does_not_invent_nil_or_iteration_identity_proofs(code, declaration, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unproved pointer reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(source(declaration))
    assert caught.value.code == 'go.unsupported.' + code


@pytest.mark.parametrize('dtype,width,predicate', [('int8', 8, 'slt'), ('uint8', 8, 'ult'),
                                                ('int64', 64, 'slt'), ('uint64', 64, 'ult')])
def test_integer_range_hidden_induction_uses_exact_width_and_signedness(dtype, width, predicate):
    text = str(compile_go(source('func f(n ' + dtype + '){for i:=range n{_=i}}')))
    assert 'icmp ' + predicate + ' i' + str(width) in text
    assert 'add i' + str(width) in text
    assert ' nsw ' not in text and ' nuw ' not in text


RAW = [
    ('DefaultBound', (), 10), ('UnsignedMaximum', (), 32385), ('SignedMaximum', (), 8001),
    ('Nonpositive', (), 0), ('ExistingUntyped', (), 254255), ('NamedInteger', (), 6),
    ('BoundSnapshot', (), 101), ('ShadowBound', (), 406), ('HiddenInduction', (), 6),
    ('BoundCallOnce', (), 103), ('BareBoundCallOnce', (), 103), ('EmptySkipsTarget', (), 100),
    ('EachTargetOnce', (), 302), ('OrderedEffects', (), 1221), ('ContinueSwitchBreak', (), 9),
    ('NestedRanges', (), 3), ('HugeUnsigned', (), 0xfedcba9876543213), ('HugeSigned', (), 3),
    ('ContextualWideConstant', (), 2), ('ShiftContext', (8,), 900), ('ShiftContext', (2,), 304),
    ('BodyScope', (), 33), ('ExistingPointerTarget', (), 2), ('EmptyNilTarget', (), 7),
    ('NilConstantLength', (), 3), ('GuardedCall', (), 6),
    ('SignedDynamic', (-9223372036854775808,), 0), ('SignedDynamic', (-1,), 0),
    ('SignedDynamic', (0,), 0), ('SignedDynamic', (5,), 10),
]


@pytest.mark.integration
@pytest.mark.parametrize('name,arguments,expected', RAW)
def test_original_integer_range_helpers_have_full_width_native_observations(name, arguments, expected, tmp_path, monkeypatch):
    original = units(('integer_range.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(original, observe_entry=(name, arguments))
    binary = tmp_path / 'integer-range-observer'
    write_artifact(artifact, binary)
    actual = run(binary)
    (tmp_path / 'observation.json').write_text(json.dumps({
        'owner': artifact.owner, 'function': name, 'arguments': arguments,
        'source_sha256': hashlib.sha256(original[0][1].encode()).hexdigest(),
        'executable_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'returncode': actual.returncode, 'stdout_hex': actual.stdout.hex(),
        'stderr_hex': actual.stderr.hex(), 'expected_hex': expected.to_bytes(8, 'little').hex(),
    }, indent=2) + '\n')
    assert actual == Execution(0, expected.to_bytes(8, 'little'), b'')


@pytest.mark.integration
def test_complete_integer_range_program_main_executes_owned(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units(('integer_range.go',)))
    binary = tmp_path / 'integer-range-main'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('originals', [
    pytest.param(None, id='complete-program'),
    *[pytest.param((('original.go', text),), id='graduated-' + str(i)) for i, text in enumerate(GRADUATED)],
])
def test_identical_integer_range_program_matches_official_raw_streams(originals, tmp_path, monkeypatch):
    originals = units(('integer_range.go',)) if originals is None else originals
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    binary = tmp_path / 'integer-range-main'
    write_artifact(artifact, binary)
    reference = compile_reference(originals, tmp_path / 'reference', compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    record_pair(tmp_path, hashlib.sha256(originals[0][1].encode()).hexdigest(), binary, reference)
    assert (reference.parent / originals[0][0]).read_bytes() == originals[0][1].encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('body,declarations,message,reference_message', INVALID)
def test_official_go_rejects_identical_invalid_integer_ranges(body, declarations, message, reference_message, tmp_path):
    original = source(declarations, body)
    with pytest.raises(GoDiagnostic, match=message):
        compile_go(original)
    receipt = compile_reference_rejection((('invalid.go', original),), tmp_path / 'reference',
                                          compiler=os.environ['PCC_GO_REFERENCE'],
                                          cache=os.environ['PCC_GO_REFERENCE_CACHE'], diagnostics=reference_message)
    assert receipt['classification'] == 'normal_source_rejection'
    assert (tmp_path / 'reference' / 'invalid.go').read_bytes() == original.encode()
    assert not (tmp_path / 'reference' / 'oracle').exists()
