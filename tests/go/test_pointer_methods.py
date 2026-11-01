"""Direct pointer receiver methods, not interfaces, closures or heap allocation.

Go specification: https://go.dev/ref/spec#Method_declarations
https://go.dev/ref/spec#Method_sets https://go.dev/ref/spec#Calls
https://go.dev/ref/spec#Method_expressions https://go.dev/ref/spec#Address_operators
Every original below is retained unchanged for its declared obligation.
"""
import hashlib
import json
import os

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go, compile_go_packages
from tests.go.owned_execution import (
    Execution, compile_module, compile_owned, compile_reference,
    compile_reference_packages, compile_reference_rejection, run, write_artifact,
)
from tests.go.test_diagnostics import original as diagnostic_original
from tests.go.test_execution import forbid_external_compiler, units
from tests.go.test_nullable_pointers import record_pair
from tests.go.test_records import source


# All five byte-distinct originals that used the retired receiver capability
# boundary. None is replaced by a weaker source or relabeled as a rejection.
GRADUATED = [
    source('type T int;func(*T) M(){}'),
    source('type T int;func(t *T) M(){}'),
    source('type T int;type P = *T;func(p P)M(){}'),
    diagnostic_original('type T int;func(t *T)f(){}'),
    'package main;type T int;func(p *T)M(){if p!=nil{*p=3}};func main(){}',
]


@pytest.mark.parametrize('original', GRADUATED)
def test_original_pointer_receiver_frontiers_graduate_without_rewriting(original, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(original)


@pytest.mark.parametrize('receiver', ['*T', 'A', '*V'])
@pytest.mark.parametrize('body', [
    'var t T;t.M();(&t).M();(*T).M(&t)',
    'var p *T;p.M();(*T).M(nil);p=nil;p.M()',
])
def test_pointer_receiver_aliases_and_direct_calls(receiver, body, monkeypatch):
    forbid_external_compiler(monkeypatch)
    module = compile_go(source('type T uint64;type V=T;type A=*T;func(t '+receiver+')M(){}', body))
    assert 'noalias' not in str(module) and 'nonnull' not in str(module)


def test_typed_call_shapes_keep_real_pointer_receivers_and_value_wrappers_distinct():
    from pcc.frontends.go.pointer_flow import validate_pointer_flow
    from pcc.frontends.go.sema import Analyzer
    from pcc.frontends.go.syntax import GoParser
    from pcc.frontends.go.types import PointerType
    program = GoParser().parse(source('type T uint64;func(t *T)P(){};func(t T)V(){}',
                                     'var t T;var p *T;t.P();p.P();(*T).P(p);(*T).V(&t);t.V()'))
    Analyzer(program).analyze()
    validate_pointer_flow(program)
    main = next(f for f in program.functions if f.token.text == 'main')
    calls = [s.exprs[0] for s in main.body if s.kind == 'expression']
    assert len(calls) == 5
    assert calls[0].receiver.kind == 'address' and isinstance(calls[0].receiver.type, PointerType)
    assert calls[1].receiver.kind == 'name' and isinstance(calls[1].receiver.type, PointerType)
    assert calls[2].receiver is None and isinstance(calls[2].args[0].type, PointerType)
    assert [c.indirect_receiver for c in calls] == [False, False, False, True, False]
    assert not isinstance(calls[4].receiver.type, PointerType)


def test_pointer_receiver_call_closure_is_checked_even_on_unreachable_edges():
    from pcc.frontends.go.pointer_flow import validate_pointer_flow
    from pcc.frontends.go.sema import Analyzer
    from pcc.frontends.go.syntax import GoParser
    program = GoParser().parse(source('type T int;func(t *T)M(){}', 'if false{var t T;t.M()}'))
    Analyzer(program).analyze()
    program.functions = [f for f in program.functions if f.token.text == 'main']
    with pytest.raises(UnsupportedGo) as caught:
        validate_pointer_flow(program)
    assert caught.value.code == 'go.unsupported.pointer-signatures'


INVALID = [
    ('type T int;func(t *T)M(){}', 'var t T;T.M(&t)', 'invalid method expression', 'invalid method expression'),
    ('type T int;func(t *T)M(){}', 'T(1).M()', 'non-addressable', 'cannot call pointer method'),
    ('type T struct{X int};func(t *T)M(){}', 'T{}.M()', 'non-addressable', 'cannot call pointer method'),
    ('type T int;func(t *T)M(){};func f()T{return 1}', 'f().M()', 'non-addressable', 'cannot call pointer method'),
    ('type T int;func(t *T)M(){}', 'const t T=1;t.M()', 'non-addressable', 'cannot call pointer method'),
    ('type T int;func(t T)M(){};func(t *T)M(){}', '', 'duplicate method', 'already declared'),
    ('type T struct{M int};func(t *T)M(){}', '', 'conflicts with struct field', 'field and method'),
    ('func(t *int)M(){}', '', 'defined type in the same package', 'cannot define new methods'),
    ('type T int;type P *T;func(t P)M(){}', '', 'receiver base cannot', 'invalid receiver type'),
    ('type T int;func(t **T)M(){}', '', 'receiver base cannot', 'invalid receiver type'),
    ('type T int;type P *T;func(t *P)M(){}', '', 'receiver base cannot', 'invalid receiver type'),
    ('type T int;func(t *T)M(){}', 'var t T;(*T).M(t)', 'cannot use T', 'cannot use t'),
    ('type T int;type U T;func(t *T)M(){}', 'var u U;u.M()', 'no method', 'undefined'),
    ('type T int;func(t *T)M(){}', '_=T.M', 'invalid method expression', 'invalid method expression'),
    ('type T int;func(t *T)M(){};func f()[1]T{return [1]T{1}}', 'f()[0].M()', 'non-addressable', 'cannot call pointer method'),
]


@pytest.mark.parametrize('declarations,body,message,reference_message', INVALID)
def test_pointer_method_validity_errors_are_not_capability_claims(declarations, body, message, reference_message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid pointer method reached IR'))
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(source(declarations, body))
    assert not isinstance(caught.value, UnsupportedGo)


UNSUPPORTED = [
    ('pointer-nil', source('type T int;func(t *T)M(){*t=1}')),
    ('pointer-nil', source('type T struct{X int};func(t *T)M(){t.X=1}')),
    ('pointer-nil', source('type T int;func(t *T)M(){if t!=nil{t=nil;*t=1}}')),
    ('pointer-nil', source('type T int;func(t *T)M(){for t!=nil{break};*t=1}')),
    ('pointer-nil', source('type T int;func(t *T)M(){}', 'var p *T;(*p).M()')),
    ('pointer-escape', source('type T int;func(t *T)M(){}', 'var p *T;{var t T;p=&t};p.M()')),
    ('pointer-iteration-identity', source('type T int;func(t *T)M(){}', 'for i:=range T(2){i.M()}')),
    ('method-values', source('type T int;func(t *T)M(){}', 'var t T;_=t.M')),
    ('method-values', source('type T int;func(t *T)M(){}', '_=(*T).M')),
    ('pointer-signatures', source('type T int;func(t *T)M()*T{return t}')),
]


@pytest.mark.parametrize('code,original', UNSUPPORTED)
def test_receiver_lifetime_nil_and_function_value_frontiers_remain_explicit(code, original, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported receiver reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.' + code


PROJECT = {
    'main': [('main.go', 'package main;import "counter";func main(){c:=counter.Counter{N:5};if c.Add(7)!=12||c.N!=12{for{}};var p *counter.Counter;if p.Add(3)!=7||(*counter.Counter).Add(&c,2)!=14{for{}}}')],
    'counter': [('counter.go', 'package counter;type Counter struct{N uint64};func(c *Counter)Add(n uint64)uint64{if c==nil{return 7};c.N+=n;return c.N};func(c *Counter)hidden(){}')],
}


def test_imported_pointer_method_original_project_is_owned(monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go_packages(PROJECT)


@pytest.mark.parametrize('declarations,body,message', [
    ('func(c *counter.Counter)Bad(){}', '', 'same package'),
    ('type Alias=counter.Counter;func(c *Alias)Bad(){}', '', 'same package'),
    ('', 'var c counter.Counter;c.hidden()', 'unexported method'),
    ('', 'var c counter.Counter;(*counter.Counter).hidden(&c)', 'unexported method'),
])
def test_imported_receiver_base_ownership_and_method_visibility(declarations, body, message):
    project = dict(PROJECT)
    project['main'] = [('main.go', 'package main;import "counter";'+(declarations+';' if declarations else '')+'func main(){'+body+'}')]
    with pytest.raises(GoDiagnostic, match=message):
        compile_go_packages(project)


# The first ordinary attempt generated these two malformed inputs and failed
# its intended visibility assertions. Keep that failure history unchanged;
# these separately named controls establish only the originals' syntax errors.
MALFORMED_VISIBILITY_ORIGINALS = [
    'package main;import "counter";;func main(){var c counter.Counter;c.hidden()}',
    'package main;import "counter";;func main(){var c counter.Counter;(*counter.Counter).hidden(&c)}',
]


@pytest.mark.parametrize('original', MALFORMED_VISIBILITY_ORIGINALS)
def test_retained_malformed_visibility_originals_are_syntax_errors(original, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('malformed source reached IR'))
    project = dict(PROJECT)
    project['main'] = [('main.go', original)]
    with pytest.raises(GoDiagnostic, match='empty package declarations') as caught:
        compile_go_packages(project)
    assert not isinstance(caught.value, UnsupportedGo)


RAW = [
    ('ImplicitStruct', 12), ('ExplicitPointer', 0xfedcba9876543213), ('NilReceiver', 7),
    ('NilExpression', 7), ('AliasReceiver', 12), ('ReceiverIdentity', 12),
    ('SharedReceiver', 909), ('DistinctReceivers', 201), ('ReceiverOnce', 1208),
    ('NilStillArguments', 207), ('ArgumentMutation', 9), ('ValueWrapper', 9),
    ('ValueSnapshot', 109), ('FieldReceiver', 11), ('ArrayReceiver', 12),
    ('RecursiveReceiver', 6), ('MultipleResults', 878), ('BooleanReceiver', 10),
    ('StringReceiver', 5), ('ArrayPointerReceiver', 21), ('NilForward', 7),
    ('UnnamedNil', 23), ('GlobalReceiver', 7),
]


@pytest.mark.integration
@pytest.mark.parametrize('name,expected', RAW)
def test_original_pointer_method_helpers_have_full_width_native_observations(name, expected, tmp_path, monkeypatch):
    original = units(('pointer_methods.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(original, observe_entry=(name, ()))
    binary = tmp_path / 'pointer-method-observer'
    write_artifact(artifact, binary)
    actual = run(binary)
    (tmp_path / 'observation.json').write_text(json.dumps({
        'owner': artifact.owner, 'function': name, 'source_sha256': hashlib.sha256(original[0][1].encode()).hexdigest(),
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'returncode': actual.returncode,
        'stdout_hex': actual.stdout.hex(), 'stderr_hex': actual.stderr.hex(),
        'expected_hex': expected.to_bytes(8, 'little').hex(),
    }, indent=2) + '\n')
    assert actual == Execution(0, expected.to_bytes(8, 'little'), b'')


@pytest.mark.integration
def test_complete_pointer_method_main_executes_owned(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units(('pointer_methods.go',)))
    binary = tmp_path / 'pointer-method-main'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('originals', [
    pytest.param(None, id='complete-program'),
    *[pytest.param((('original.go', text),), id='graduated-'+str(i)) for i, text in enumerate(GRADUATED)],
])
def test_identical_pointer_method_originals_match_official_raw_streams(originals, tmp_path, monkeypatch):
    originals = units(('pointer_methods.go',)) if originals is None else originals
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    binary = tmp_path / 'pointer-method-main'
    write_artifact(artifact, binary)
    reference = compile_reference(originals, tmp_path / 'reference', compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    record_pair(tmp_path, hashlib.sha256(originals[0][1].encode()).hexdigest(), binary, reference)
    assert (reference.parent / originals[0][0]).read_bytes() == originals[0][1].encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_imported_pointer_method_project_matches_official_raw_streams(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_module(compile_go_packages(PROJECT))
    binary = tmp_path / 'pointer-method-project'
    write_artifact(artifact, binary)
    reference = compile_reference_packages(PROJECT, tmp_path / 'reference', compiler=os.environ['PCC_GO_REFERENCE'],
                                           cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    record_pair(tmp_path, hashlib.sha256(json.dumps(PROJECT, sort_keys=True).encode()).hexdigest(), binary, reference)
    for package, files in PROJECT.items():
        for name, original in files:
            assert (reference.parent / 'gopath/src' / package / name).read_bytes() == original.encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('declarations,body,message,reference_message', INVALID)
def test_official_go_rejects_identical_invalid_pointer_methods(declarations, body, message, reference_message, tmp_path):
    original = source(declarations, body)
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(original)
    assert not isinstance(caught.value, UnsupportedGo)
    receipt = compile_reference_rejection((('invalid.go', original),), tmp_path / 'reference',
                                          compiler=os.environ['PCC_GO_REFERENCE'],
                                          cache=os.environ['PCC_GO_REFERENCE_CACHE'], diagnostics=reference_message)
    assert receipt['classification'] == 'normal_source_rejection'
    assert (tmp_path / 'reference/invalid.go').read_bytes() == original.encode()
    assert not (tmp_path / 'reference/oracle').exists()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('code,original', UNSUPPORTED)
def test_official_go_builds_identical_unsupported_receiver_frontiers_without_execution(code, original, tmp_path):
    # Valid Go is not an invalid-program rejection. Some originals need a
    # panic or escaping storage; the external executable is deliberately NOT run.
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.' + code
    reference = compile_reference((('original.go', original),), tmp_path / 'reference',
                                  compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert reference.is_file()
    assert (reference.parent / 'original.go').read_bytes() == original.encode()
