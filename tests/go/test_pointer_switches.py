"""Pointer expression switches: capture, ordered equality, and CFG nil proofs.

Normative contracts: https://go.dev/ref/spec#Expression_switches
https://go.dev/ref/spec#Comparison_operators
https://go.dev/ref/spec#Address_operators
https://go.dev/ref/spec#Order_of_evaluation
These are authored CI obligations, not recorded executions or Go completeness.
Distinct zero-size pointer identity is deliberately not an asserted oracle.
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


# Keep both exact historical wrapper forms, not merely equivalent rewrites.
# Their old unsupported receipts remain historical; these are new identities.
GRADUATED = [
    source('', 'x:=1;p:=&x;switch p{case nil:}'),
    diagnostic_original(body='x:=1;p:=&x;switch p{case nil:}'),
]


@pytest.mark.parametrize('original', GRADUATED)
def test_original_pointer_switch_frontiers_graduate_without_rewriting(original, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(original)


@pytest.mark.parametrize('declarations,body', [
    ('', 'var p *int;switch p{case nil:}'),
    ('', 'x:=3;p:=&x;switch p{}'),
    ('', 'x:=3;p:=&x;switch p{case &x:;case &x:}'),
    ('', 'switch (*int)(nil){case nil:;case (*int)(nil):}'),
    ('type P *int', 'x:=3;var p P=&x;switch p{case &x:;case nil:}'),
    ('func f(p *int)int{switch p{case nil:return 7;default:return *p}}', '_=f(nil)'),
    ('func f(p *int)int{switch p{default:return *p;case nil:return 7}}', '_=f(nil)'),
    ('func f(p,q *int)int{if q==nil{return 0};switch p{case q:return *p;default:return 1}}', ''),
    ('var x int;func f(p *int)int{switch &x{case p:return *p;default:return 1}}', ''),
    ('func f(p *int)int{switch p{case nil:return 0};return *p}', ''),
    ('func f(p *int)int{switch q:=p;q{case nil:return 0;default:return *q}}', ''),
    ('', 'x:=3;p:=&x;switch p{case nil:fallthrough;default:if p!=nil{_=*p}}'),
    ('', 'var a [0]int;switch &a{case &a:}'),
])
def test_pointer_switch_typing_and_source_guard_shapes(declarations, body, monkeypatch):
    forbid_external_compiler(monkeypatch)
    module = compile_go(source(declarations, body))
    assert 'noalias' not in str(module)


def test_typed_switch_reuses_one_selector_and_pointer_equality_nodes():
    from pcc.frontends.go.pointer_flow import validate_pointer_flow
    from pcc.frontends.go.records import underlying
    from pcc.frontends.go.sema import Analyzer
    from pcc.frontends.go.syntax import GoParser
    from pcc.frontends.go.types import PointerType
    program = GoParser().parse(source('', 'x:=3;p:=&x;switch p{case nil:;case &x:}'))
    Analyzer(program).analyze()
    validate_pointer_flow(program)
    main = next(function for function in program.functions if function.token.text == 'main')
    switch = next(statement for statement in main.body if statement.kind == 'switch')
    selector = switch.exprs[0]
    assert isinstance(underlying(selector.type), PointerType)
    for clause in switch.cases:
        for comparison in clause.exprs:
            assert comparison.kind == 'binary' and comparison.value == '=='
            assert comparison.args[0] is selector
            assert comparison.args[1].type == selector.type


INVALID = [
    ('', 'switch nil{}', 'untyped nil', 'use of untyped nil'),
    ('', 'var p *int;switch p{case 1:}', 'cannot use', 'cannot convert'),
    ('', 'var p *int;switch p{case true:}', 'cannot use', 'cannot convert'),
    ('', 'var p *int;switch p{case "x":}', 'cannot use', 'cannot convert'),
    ('', 'var p *int;var q *int64;switch p{case q:}', 'mismatched pointer', 'mismatched types'),
    ('type P *int;type Q *int', 'var p P;var q Q;switch p{case q:}', 'mismatched pointer', 'mismatched types'),
    ('type T int', 'var p *int;var q *T;switch p{case q:}', 'mismatched pointer', 'mismatched types'),
    ('', 'var p *int;switch p{default:;default:}', 'multiple default', 'multiple defaults'),
    ('', 'x:=3;switch p:=&x;p{case nil:};_=p', 'undefined name', 'undefined'),
    ('', 'var p *int;switch p{case nil:fallthrough}', 'non-final switch clause', 'cannot fallthrough'),
]


@pytest.mark.parametrize('declarations,body,message,reference_message', INVALID)
def test_invalid_pointer_switches_fail_before_ir(declarations, body, message, reference_message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid switch reached IR'))
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(source(declarations, body))
    assert not isinstance(caught.value, UnsupportedGo)


UNSUPPORTED = [
    ('pointer-nil', source('func f(p *int)int{switch p{case nil:fallthrough;default:return *p}}')),
    ('pointer-nil', source('func f(p *int)int{switch p{case nil:break;default:return *p};return *p}')),
    ('pointer-nil', source('func f(p,q *int)int{switch p{case q:return *p;default:return 0}}')),
    ('pointer-nil', source('func f(p *int)int{x:=3;switch p{case &x:return 1;default:return *p}}')),
    ('pointer-nil', source('func f(p *int)int{switch p{case nil:return 0;default:p=nil;return *p}}')),
    ('pointer-nil', source('func f(p *int){switch p{case &*p:}}')),
    ('pointer-nil', source('func f(p *int){switch &*p{case nil:}}')),
    ('pointer-nil', source('func f(p *int){for ;p!=nil;_=*p{switch p{default:p=nil;continue}}}')),
    ('pointer-nil', source('func f(p,q *int)int{switch p{case nil,q:return *p;default:return 0}}')),
    ('pointer-escape', source('', 'var p *int;switch p{case nil:x:=3;p=&x};_=p')),
    ('pointer-signatures', source('var x int;func f()*int{return &x}', 'switch f(){case nil:}')),
    ('pointer-storage', source('var p *int', 'switch p{case nil:}')),
]


@pytest.mark.parametrize('code,original', UNSUPPORTED)
def test_unproven_pointer_switch_edges_remain_explicit_frontiers(code, original, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported switch reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.' + code


RAW = [
    ('NilAndLive', 1709), ('Identity', 9), ('NamedPointer', 23),
    ('CaptureAndOrder', 12319), ('FallthroughSkipsCase', 21),
    ('InitAndShadow', 13), ('LoopControl', 9), ('TypedNil', 31),
    ('RepeatedAliasCases', 37), ('ParameterEquality', 1130),
    ('AddressSelectorGuard', 2913), ('RecordUpdate', 4800),
    ('WideAlias', 0xfedcba9876543217), ('EmptyAndNoMatch', 3),
]


@pytest.mark.integration
@pytest.mark.parametrize('name,expected', RAW)
def test_pointer_switch_full_width_native_observations(name, expected, tmp_path, monkeypatch):
    originals = units(('pointer_switches.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals, observe_entry=(name, ()))
    binary = tmp_path / 'pointer-switch-observer'
    write_artifact(artifact, binary)
    actual = run(binary)
    (tmp_path / 'observation.json').write_text(json.dumps({
        'owner': artifact.owner, 'function': name,
        'input_sha256': hashlib.sha256(originals[0][1].encode()).hexdigest(),
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'returncode': actual.returncode, 'stdout_hex': actual.stdout.hex(),
        'stderr_hex': actual.stderr.hex(), 'expected_hex': expected.to_bytes(8, 'little').hex(),
    }, indent=2) + '\n')
    assert actual == Execution(0, expected.to_bytes(8, 'little'), b'')


@pytest.mark.integration
def test_complete_pointer_switch_program_executes_owned(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units(('pointer_switches.go',)))
    binary = tmp_path / 'pointer-switch-main'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('originals', [
    pytest.param(None, id='complete-program'),
    *[pytest.param((('original.go', text),), id='graduated-'+str(i)) for i, text in enumerate(GRADUATED)],
])
def test_identical_pointer_switch_originals_match_official_raw_streams(originals, tmp_path, monkeypatch):
    originals = units(('pointer_switches.go',)) if originals is None else originals
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    binary = tmp_path / 'pointer-switch-main'
    write_artifact(artifact, binary)
    reference = compile_reference(originals, tmp_path / 'reference', compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    record_pair(tmp_path, hashlib.sha256(originals[0][1].encode()).hexdigest(), binary, reference)
    assert (reference.parent / originals[0][0]).read_bytes() == originals[0][1].encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('declarations,body,message,reference_message', INVALID)
def test_official_go_rejects_identical_invalid_pointer_switches(declarations, body, message, reference_message, tmp_path):
    original = source(declarations, body)
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
@pytest.mark.parametrize('code,original', UNSUPPORTED)
def test_official_go_builds_identical_unsupported_switch_frontiers_without_execution(code, original, tmp_path):
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.' + code
    reference = compile_reference((('original.go', original),), tmp_path / 'reference',
                                  compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert reference.is_file()
    assert (reference.parent / 'original.go').read_bytes() == original.encode()
