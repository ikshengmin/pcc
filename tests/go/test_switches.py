"""Ordered Go expression switches, not a C/Java switch rewrite."""
import hashlib
import json
import os

import pytest

from pcc.frontends.go import GoDiagnostic, compile_go
from tests.go.owned_execution import Execution, compile_owned, compile_reference, run, write_artifact
from tests.go.owned_execution import compile_reference_rejection
from tests.go.test_execution import forbid_external_compiler, units
from tests.go.test_records import source


# Stable historical keys keep the existing official rejection node identities.
# Cases 9/10 were valid aggregate selectors, not invalid Go; their exact source
# now has separately named positive obligations below. Earlier negative-test
# outcomes (including the stale assertion failure) remain historical evidence.
INVALID = {
    0: ('switch 1{default:;default:}', '', 'multiple default'),
    1: ('switch 1{case 1:;case 1:}', '', 'duplicate constant'),
    2: ('switch "a"{case "a":;case "\\x61":}', '', 'duplicate constant'),
    3: ('switch uint8(1){case 256:}', '', 'overflows uint8'),
    4: ('switch 1{case true:}', '', 'cannot use'),
    5: ('switch true{case 1:}', '', 'cannot use'),
    6: ('switch int(1){case int64(1):}', '', 'mismatched operand types'),
    7: ('switch A(1){case B(1):}', 'type A int;type B int', 'mismatched operand types'),
    8: ('switch A{}{}', 'type A struct{X int}', 'semicolon or newline'),
    11: ('switch f(){}', 'func f()(int,int){return 1,2}', 'switch requires a comparable type'),
    12: ('switch 1{case missing:}', '', 'undefined name'),
    13: ('switch 1{case 1:x:=7;_=x;case x:}', '', 'undefined name'),
    14: ('switch x:=1;x{case 1:};_=x', '', 'undefined name'),
    15: ('switch 1{case 1:continue}', '', 'outside loop'),
    16: ('fallthrough', '', 'final non-empty'),
    17: ('switch 1{case 1:fallthrough}', '', 'non-final switch clause'),
    18: ('switch 1{case 1:fallthrough;_=7;case 2:}', '', 'final non-empty'),
    19: ('switch 1{case 1:{fallthrough};case 2:}', '', 'final non-empty'),
    20: ('switch 1{case 1:if true{fallthrough};case 2:}', '', 'final non-empty'),
    21: ('switch 1{case 1:for{fallthrough};case 2:}', '', 'final non-empty'),
    22: ('switch 1{case 1:fallthrough;const();case 2:}', '', 'final non-empty'),
    23: ('switch 1{case 1:x:=1;x:=2;_=x}', '', 'at least one new'),
    24: ('switch 1{case 1:x:=1}', '', 'declared and not used'),
    25: ('switch 1{case 1:break Outer}', '', 'semicolon or newline'),
}

GRADUATED_AGGREGATE_SWITCHES = [
    source('type A struct{X int}', 'switch (A{}){}'),
    source('', 'switch [2]int{}{}'),
]


@pytest.mark.parametrize('body,declarations,message', INVALID.values())
def test_invalid_switch_source_fails_before_ir(body,declarations,message,monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *args:pytest.fail('invalid switch reached IR'))
    with pytest.raises(GoDiagnostic,match=message):compile_go(source(declarations,body))


@pytest.mark.parametrize('original', GRADUATED_AGGREGATE_SWITCHES)
def test_exact_aggregate_switch_frontiers_are_now_supported(original, monkeypatch):
    # https://go.dev/ref/spec#Expression_switches: arrays/structs are comparable
    # when their components are comparable. Keep the original empty bodies.
    forbid_external_compiler(monkeypatch)
    compile_go(original)


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original', GRADUATED_AGGREGATE_SWITCHES)
def test_exact_aggregate_switch_frontiers_match_native_and_official_streams(original, tmp_path, monkeypatch):
    from tests.go.test_nullable_pointers import record_pair
    originals = (('original.go', original),)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    binary = tmp_path/'aggregate-switch-original'
    write_artifact(artifact, binary)
    reference = compile_reference(originals, tmp_path/'reference', compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert (reference.parent/'original.go').read_bytes() == original.encode()
    record_pair(tmp_path, hashlib.sha256(original.encode()).hexdigest(), binary, reference)


@pytest.mark.parametrize('body',[
    'switch 1{case 1:return 7}',
    'switch 1{case 1:return 7;default:break}',
    'switch 1{case 1:if false{break};return 7;default:return 9}',
    'switch 1{case 1:fallthrough;default:}',
    'switch 1{default:return 7;const()}',
])
def test_switch_termination_requires_default_all_clauses_and_no_targeted_break(body):
    with pytest.raises(GoDiagnostic,match='missing return'):
        compile_go(source('func f()uint64{'+body+'}'))


@pytest.mark.parametrize('body',[
    'switch {}',  # Exact formerly unsupported source, not a rewritten variant.
    'switch 1{case 1:;case 2:}',
    'switch x:=1;{case x==1:}',
    'switch true{case true:;case false:}',
    'switch 1{case 1:fallthrough;;;case 2:}',
    'switch 1{case 1:const(A=iota);_=A;case 2:const(A=iota);_=A}',
])
def test_valid_switch_shapes_keep_distinct_empty_statement_and_clause_rules(body,monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(source('',body))


RAW=[('Ordered',2127),('Snapshot',91),('NoImplicitFallthrough',7),('FallthroughMiddle',36),
     ('FallthroughSkipsTest',7),('InitScope',4003),('ClauseScopes',7),('ContinueOuter',36),
     ('BreakTargets',321),('InfiniteLoopTermination',83),('AllReturn',0xfedcba9876543210),
     ('NamedSelector',0x8877665544332211),('Strings',0xfedcba9876543210),('BooleanSwitch',83),
     ('EmptySwitch',7),('DefaultAfterTests',2139),('NamedReturn',79),('NestedFallthrough',18)]


@pytest.mark.integration
@pytest.mark.parametrize('name,expected',RAW)
def test_original_switch_helpers_have_independent_full_width_observations(name,expected,tmp_path,monkeypatch):
    original=units(('switches_valid.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied);artifact=compile_owned(original,observe_entry=(name,()))
    binary=tmp_path/'switch-native';write_artifact(artifact,binary);actual=run(binary)
    raw=expected.to_bytes(8,'little')
    (tmp_path/'raw-switch-observation.json').write_text(json.dumps({'owner':artifact.owner,'function':name,'arguments':[],
      'expected_hex':raw.hex(),'stdout_hex':actual.stdout.hex(),'returncode':actual.returncode,'stderr_hex':actual.stderr.hex(),
      'source_sha256':hashlib.sha256(original[0][1].encode()).hexdigest(),
      'executable_sha256':hashlib.sha256(artifact.executable).hexdigest()},indent=2)+'\n')
    assert actual==Execution(0,raw,b'')


@pytest.mark.integration
def test_original_switch_program_main_executes(tmp_path,monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied);artifact=compile_owned(units(('switches_valid.go',)))
    binary=tmp_path/'switch-main';write_artifact(artifact,binary)
    assert run(binary)==Execution(0,b'',b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_official_go_receives_identical_original_switch_program(tmp_path):
    original=units(('switches_valid.go',))
    reference=compile_reference(original,tmp_path/'reference',compiler=os.environ['PCC_GO_REFERENCE'],cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert run(reference)==Execution(0,b'',b'')
    assert (reference.parent/'switches_valid.go').read_bytes()==original[0][1].encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('index,reference_message',[(0,'multiple defaults'),(1,'duplicate case'),(3,'overflows'),(17,'cannot fallthrough'),(18,'fallthrough statement out of place')])
def test_official_go_rejects_identical_invalid_switch_source(index,reference_message,tmp_path):
    body,declarations,message=INVALID[index];original=source(declarations,body)
    with pytest.raises(GoDiagnostic,match=message):compile_go(original)
    compile_reference_rejection((('invalid.go',original),),tmp_path/'reference',compiler=os.environ['PCC_GO_REFERENCE'],cache=os.environ['PCC_GO_REFERENCE_CACHE'], diagnostics=reference_message)
    assert (tmp_path/'reference/invalid.go').read_bytes()==original.encode()
    assert not (tmp_path/'reference/oracle').exists()


def test_original_switch_program_emits_real_arm64_darwin_object(monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    forbid_external_compiler(monkeypatch)
    module=compile_go(units(('switches_valid.go',))[0][1],target_triple='arm64-apple-darwin')
    assert emit_owned_object(str(module),'arm64-apple-darwin')[:4]==b'\xcf\xfa\xed\xfe'


def invalid_original_switch():
    original=units(('switches.go',))
    assert hashlib.sha256(original[0][1].encode()).hexdigest()=='5901ae51a75707684aad51a2e6f69b1c3c85ab397f09d32e3e238de8ccd929d8'
    return original


def test_retained_original_unreachable_case_still_requires_matching_type(monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *args:pytest.fail('invalid original switch reached IR'))
    with pytest.raises(GoDiagnostic,match='mismatched operand types int and uint64'):
        compile_go(invalid_original_switch()[0][1])


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_official_go_rejects_same_complete_retained_original_switch(tmp_path):
    original=invalid_original_switch()
    with pytest.raises(GoDiagnostic,match='mismatched operand types int and uint64'):
        compile_go(original[0][1])
    compile_reference_rejection(original,tmp_path/'reference',compiler=os.environ['PCC_GO_REFERENCE'],cache=os.environ['PCC_GO_REFERENCE_CACHE'], diagnostics='mismatched types uint64 and int')
    assert (tmp_path/'reference/switches.go').read_bytes()==original[0][1].encode()
    assert not (tmp_path/'reference/oracle').exists()
