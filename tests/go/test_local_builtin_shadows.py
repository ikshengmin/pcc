"""Lexical values hide builtin functions; complete math/bits is still blocked.

https://go.dev/ref/spec#Declarations_and_scope
https://go.dev/ref/spec#Predeclared_identifiers
https://go.dev/ref/spec#Short_variable_declarations

All new native/reference obligations are authored, not execution evidence.
"""
import hashlib
import json
import os

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.sema import PREDECLARED_FUNCTIONS
from tests.go.owned_execution import (
    Execution, compile_owned, compile_reference, compile_reference_rejection, run, write_artifact,
)
from tests.go.test_execution import forbid_external_compiler, units


FUNCTION_NAMES = frozenset('append cap clear close complex copy delete imag len make max min new panic print println real recover'.split())


def source(body, declarations=''):
    return 'package main;' + (declarations + ';' if declarations else '') + 'func main(){' + body + '}'


@pytest.mark.parametrize('name', sorted(FUNCTION_NAMES))
@pytest.mark.parametrize('kind', ['var', 'const', 'parameter', 'result'])
def test_every_builtin_function_spelling_can_name_a_local_value(name, kind):
    assert FUNCTION_NAMES == PREDECLARED_FUNCTIONS
    if kind in ('var', 'const'):
        original = source(kind + ' ' + name + '=1;_=' + name)
    elif kind == 'parameter':
        original = source('_=f(1)', 'func f(' + name + ' int)int{return ' + name + '}')
    else:
        original = source('_=f()', 'func f()(' + name + ' int){' + name + '=1;return}')
    compile_go(original)


@pytest.mark.parametrize('body', [
    'len:=len("abc");_=len',
    'const len=len("abc");_=len',
    'var len=len("abc");_=len',
    'var(len=len("abc");cap=len+4);_=len;_=cap',
    'len,cap:=len("abc"),cap([2]int{});_=len;_=cap',
    '{len:=1;_=len};_=len("abc")',
    'for len:=range len("abc"){_=len};_=len("abc")',
])
def test_initializers_and_ranges_resolve_before_the_new_shadow_begins(body):
    compile_go(source(body))


INVALID = [
    (source('len:=1;_=len("abc")'), 'cannot call non-function len', 'cannot call'),
    (source('cap:=1;_=cap([2]int{})'), 'cannot call non-function cap', 'cannot call'),
    (source('panic:=1;panic(1)'), 'cannot call non-function panic', 'cannot call'),
    (source('print:=1;print(1)'), 'cannot call non-function print', 'cannot call'),
    (source('len:=1;var x len;_=x;_=len'), 'identifier is not a type', 'not a type'),
    (source('len:=1'), 'declared and not used: len', 'declared and not used'),
    (source('var len int;len=1'), 'declared and not used: len', 'declared and not used'),
    (source('len:=1;len:=2;_=len'), 'at least one new non-blank', 'no new variables'),
    (source('len:=1;_=len;var len int'), 'redeclaration', 'redeclared'),
    (source('const len=1;len=2;_=len'), 'cannot assign to constant', 'cannot assign'),
    (source('', 'func f()(len int){{len:=2;_=len;return}}'), 'shadowed at bare return', 'not in scope at return'),
    (source('', 'func f(len int,len int){}'), 'duplicate parameter', 'redeclared'),
    (source('', 'func f(len int)(len int){return}'), 'duplicate parameter or result', 'redeclared'),
]


@pytest.mark.parametrize('original,message,reference_message', INVALID)
def test_bad_shadow_uses_remain_errors_before_ir(original, message, reference_message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid shadow reached IR'))
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(original)
    assert not isinstance(caught.value, UnsupportedGo)


@pytest.mark.parametrize('name', sorted(FUNCTION_NAMES))
def test_no_builtin_dispatch_survives_a_scalar_shadow(name):
    with pytest.raises(GoDiagnostic, match='cannot call non-function ' + name) as caught:
        compile_go(source(name + ':=1;_=' + name + '(1)'))
    assert not isinstance(caught.value, UnsupportedGo)


@pytest.mark.parametrize('original', [
    source('var int=1;_=int'), source('const true=1;_=true'),
    source('var iota=1;_=iota'), source('var nil=1;_=nil'),
    source('_=len', 'var len=1'), source('', 'func len(){}'),
    source('', 'type len int'), source('', 'func f[len any](){}'),
])
def test_other_predeclared_binding_categories_stay_explicit(original, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported shadow reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.predeclared-shadowing'


def test_original_math_bits_input_and_unrelated_runtime_frontier_are_preserved(tmp_path):
    from scripts import ci_go_math_bits_project as project
    lock, files = project.load_inputs()
    raw = files[project.PACKAGE_DIR + '/bits_test.go']
    assert hashlib.sha256(raw).hexdigest() == '4cfd1b20f33a9c6a892c08d25d2af0cb2767d8fe410531c9988801a1682d166b'
    assert raw.splitlines()[658] == b'\t\tlen := 8 - tab[i].nlz'
    assert raw.splitlines()[663] == b'\t\t\t\twant = len + k'
    report = project.initial_report()
    project.run_owned(report, files, tmp_path)
    row = report['owned']['package_frontend']
    assert (row['status'], row['code'], row['filename'], row['line'], row['column']) == (
        'UNSUPPORTED', 'go.unsupported.package-initialization', 'bits_errors.go', 9, 10)
    assert report['project_accepted'] is False
    assert all(report['owned'][name]['status'] == 'UNRUN' for name in (
        'dependency_closure', 'test_entry', 'test_build', 'tests_and_examples'))
    assert project.load_inputs()[1] == files
    # No slicing, fake testing/runtime/unsafe package, alternate bootstrap file
    # or a component result can satisfy the separate strict whole-project gate.


RAW = [
    ('OriginalShape', (3, 37), 42),
    ('Short', (), 3), ('Constant', (), 3), ('Variable', (), 4), ('Scope', (), 42),
    ('Parameters', (35, 7), 42), ('Named', (), 42), ('Range', (), 42),
    ('ArrayRange', (), 13), ('SwitchScope', (), 42), ('InitializerSideEffects', (), 13),
    ('ValueMethod', (), 42), ('Generic', (), 42), ('AllNames', (), 18),
    ('Wide', (), 0xfedcba9876543210),
]


@pytest.mark.integration
@pytest.mark.parametrize('name,args,expected', RAW)
def test_complete_original_helpers_return_full_width_native_results(name, args, expected, tmp_path, monkeypatch):
    original = units(('local_builtin_shadows.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(original, observe_entry=(name, args))
    binary = tmp_path / 'shadow-observer'
    write_artifact(artifact, binary)
    actual = run(binary)
    raw = (expected & ((1 << 64) - 1)).to_bytes(8, 'little')
    (tmp_path / 'observation.json').write_text(json.dumps({
        'owner': artifact.owner, 'function': name, 'arguments': args,
        'expected_hex': raw.hex(), 'actual_hex': actual.stdout.hex(),
        'returncode': actual.returncode, 'stderr_hex': actual.stderr.hex(),
        'source_sha256': hashlib.sha256(original[0][1].encode()).hexdigest(),
        'executable_sha256': hashlib.sha256(artifact.executable).hexdigest(),
    }, indent=2) + '\n')
    assert actual == Execution(0, raw, b'')


@pytest.mark.integration
def test_complete_original_main_checks_scope_restoration_and_evaluation_once(tmp_path, monkeypatch):
    original = units(('local_builtin_shadows.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(original)
    binary = tmp_path / 'shadow-main'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_complete_shadow_program_has_owned_and_official_native_self_checks(tmp_path, monkeypatch):
    original = units(('local_builtin_shadows.go',))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(original)
    binary = tmp_path / 'owned'
    write_artifact(artifact, binary)
    reference = compile_reference(original, tmp_path / 'reference', compiler=os.environ['PCC_GO_REFERENCE'],
                                  cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert run(binary) == run(reference) == Execution(0, b'', b'')
    assert (reference.parent / original[0][0]).read_bytes() == original[0][1].encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original,message,reference_message', INVALID)
def test_official_go_rejects_each_identical_invalid_shadow_use(original, message, reference_message, tmp_path):
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(original)
    assert not isinstance(caught.value, UnsupportedGo)
    receipt = compile_reference_rejection((('invalid.go', original),), tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'],
        diagnostics=reference_message)
    assert receipt['classification'] == 'normal_source_rejection'
    assert receipt['returncode'] == 1
    assert (tmp_path / 'reference/invalid.go').read_bytes() == original.encode()
