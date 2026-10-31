"""Fixed-array range uses Go snapshots, not slice backing or C pointer decay.

Specification contracts exercised by these unit and original-program tests:
https://go.dev/ref/spec#For_range (scope, snapshots, assignment, evaluation)
https://go.dev/ref/spec#Length_and_capacity (typed int and constant omission)
https://go.dev/ref/spec#Assignments (two-phase iteration target assignment)
https://go.dev/ref/spec#Terminating_statements (range is not an infinite for)
"""
import hashlib
import json
import os

import pytest

from pcc.frontends.go import GoDiagnostic, compile_go
from tests.go.owned_execution import Execution, compile_owned, compile_reference, run, write_artifact
from tests.go.test_execution import forbid_external_compiler, units
from tests.go.test_records import source


INVALID = [
    ('for i,j,k:=range [2]int{}{_=i;_=j;_=k}', '', 'at most two'),
    ('for _,_:=range [2]int{}{}', '', 'at least one new'),
    ('for i,i:=range [2]int{}{_=i}', '', 'duplicate declaration'),
    ('for (i):=range [2]int{}{_=i}', '', 'unparenthesized'),
    ('for a[0]:=range [2]int{}{}', 'var a [1]int', 'requires identifiers'),
    ('for i=range [2]int{}{}', '', 'undefined name'),
    ('var i int64;for i=range [2]int{}{};_=i', '', 'cannot use int as int64'),
    ('var v int;for _,v=range [2]uint64{}{};_=v', '', 'cannot use uint64 as int'),
    ('const i=0;for i=range [2]int{}{}', '', 'constant or non-addressable'),
    ('for i:=range [2]int{}{}', '', 'declared and not used'),
    ('for i:=range [2]int{}{_=i};_=i', '', 'undefined name'),
    ('for range 3{}', '', 'fixed array'),
    ('for range "abc"{}', '', 'fixed array'),
    ('for range f(){}', 'func f()(int,int){return 1,2}', 'fixed array'),
    ('for range [2]int{}{fallthrough}', '', 'final non-empty'),
    ('for i:=range [2]int{}{_=i};continue', '', 'outside loop'),
    ('for range [2]int{}{};break', '', 'outside loop'),
    ('var x uint8=len([2]int{});_=x', '', 'cannot use int as uint8'),
    ('const n=cap([2]int{});var x int64=n;_=x', '', 'cannot use int as int64'),
    ('_=cap("abc")', '', 'requires an admitted array'),
    ('_=cap()', '', 'requires one argument'),
    ('len([2]int{})', '', 'only function calls'),
    ('cap([2]int{})', '', 'only function calls'),
    ('const n=len(f());_=n', 'func f()[2]int{return [2]int{}}', 'not a constant'),
    ('', 'func f()int{for range [1]int{}{return 7}}', 'missing return'),
]


@pytest.mark.parametrize('body,declarations,message', INVALID)
def test_invalid_range_and_length_source_fails_before_ir(body, declarations, message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid range reached IR'))
    with pytest.raises(GoDiagnostic, match=message):
        compile_go(source(declarations, body))


@pytest.mark.parametrize('body', [
    'for range [0]int{}{}',
    'for _=range [2]int{}{}',
    'for _,_=range [2]int{}{}',
    'for i:=range [2]int{}{i:=i+1;_=i}',
    'for i:=range [2]int{}{_=i;for i:=range [2]int{}{_=i}}',
    'for range [2]int{}{switch {default:break};continue}',
    'var a [2]int;for (a[0])=range [2]int{}{};_=a',
    'const n=len([2]int{});var a [n]int;_=a',
    'var a [2]int;const n=cap(a);_=n',
    'for {for range [2]int{}{break};break}',
])
def test_fixed_array_range_grammar_scope_and_empty_values(body, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(source('', body))


RAW = [
    ('Sum',0xfedcba987654320f),('Snapshot',2399),('ValueCopy',32307),('CallOnce',123),
    ('IndexCallOnce',103),('BlankValueCallOnce',103),('BareCallOnce',103),('EmptyCallOnce',1),
    ('AssignmentOrder',10920),('AddressCallOnce',209),('DuplicateAssignment',209),
    ('HiddenInduction',3),('ShadowRange',303),('ContinueAndSwitch',134),('NestedBreak',3),
    ('EarlyBreak',5),('LastAssigned',1719),('NamedElement',27),('BooleanElement',13),
    ('LiteralCalls',202),('LengthCalls',233),('ConstantLength',55),('NestedBuiltinCall',102),
    ('MethodRange',123),('ReturnInside',0x8877665544332211),
]


@pytest.mark.integration
@pytest.mark.parametrize('name,expected', RAW)
def test_original_array_range_helpers_have_independent_full_width_observations(name, expected, tmp_path, monkeypatch):
    original=units(('array_range.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact=compile_owned(original,observe_entry=(name,()))
    binary=tmp_path/'range-native';write_artifact(artifact,binary);actual=run(binary)
    raw=expected.to_bytes(8,'little')
    (tmp_path/'raw-range-observation.json').write_text(json.dumps({
        'owner':artifact.owner,'function':name,'arguments':[],
        'expected_hex':raw.hex(),'stdout_hex':actual.stdout.hex(),
        'returncode':actual.returncode,'stderr_hex':actual.stderr.hex(),
        'source_sha256':hashlib.sha256(original[0][1].encode()).hexdigest(),
        'executable_sha256':hashlib.sha256(artifact.executable).hexdigest(),
    },indent=2)+'\n')
    assert actual==Execution(0,raw,b'')


@pytest.mark.integration
def test_original_array_range_program_main_executes(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied);artifact=compile_owned(units(('array_range.go',)))
    binary=tmp_path/'range-main';write_artifact(artifact,binary)
    assert run(binary)==Execution(0,b'',b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_official_go_receives_identical_original_array_range_program(tmp_path):
    original=units(('array_range.go',))
    reference=compile_reference(original,tmp_path/'reference',compiler=os.environ['PCC_GO_REFERENCE'],cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert run(reference)==Execution(0,b'',b'')
    assert (reference.parent/'array_range.go').read_bytes()==original[0][1].encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('index,reference_message', [
    (0,'at most two iteration variables'),(1,'no new variables'),(2,'i redeclared in this block'),(6,'cannot use'),
    (17,'cannot use'),(23,'not constant'),(24,'missing return'),
])
def test_official_go_rejects_identical_invalid_array_range_source(index, reference_message, tmp_path):
    body,declarations,message=INVALID[index];original=source(declarations,body)
    with pytest.raises(GoDiagnostic,match=message):compile_go(original)
    with pytest.raises(AssertionError,match=reference_message):
        compile_reference((('invalid.go',original),),tmp_path/'reference',compiler=os.environ['PCC_GO_REFERENCE'],cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert (tmp_path/'reference/invalid.go').read_bytes()==original.encode()
    assert not (tmp_path/'reference/oracle').exists()


def test_original_array_range_program_emits_real_arm64_darwin_object(monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    forbid_external_compiler(monkeypatch)
    module=compile_go(units(('array_range.go',))[0][1],target_triple='arm64-apple-darwin')
    assert emit_owned_object(str(module),'arm64-apple-darwin')[:4]==b'\xcf\xfa\xed\xfe'
