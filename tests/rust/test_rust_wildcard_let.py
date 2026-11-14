"""Wildcard lets do not bind, copy or move places (Rust Reference patterns.wildcard).

https://doc.rust-lang.org/reference/patterns.html#wildcard-pattern
https://doc.rust-lang.org/reference/statements.html#let-statements
https://doc.rust-lang.org/reference/expressions.html#place-expressions-and-value-expressions
Authored native fixtures retain initializer effects and full-width observations.
"""
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from tests.rust.certification_support import actual_check_prerequisite

PROGRAMS = ('wildcard_let_effects', 'wildcard_let_place_values', 'wildcard_let_replay',
            'enum_omitted_values')
VALID = (
    'fn f(){let _=1;let _:u64=4294967296;let _:()=();}',
    'fn f(){let _:[u64;0]=[];let _:(_,bool)=(1,true);}',
    'struct R{x:u64}fn f(){let x=R{x:1};let _=x;let y=x;}',
    'struct R{x:u64}fn f(){let x=R{x:1};let y=x;let _=x;}',
    'struct R{x:u64}fn f(){let x=R{x:1};let y=x;let _=x.x;}',
    'struct R{x:u64}struct P{a:R,b:R}fn f(){let p=P{a:R{x:1},b:R{x:2}};let a=p.a;let _=p;let _=p.a;let b=p.b;}',
    'struct R{x:u64}fn f(){let a=[R{x:1}];let _=a[0];let b=a;}',
    'struct R{x:u64}fn f(){let x=R{x:1};let _=(x);let y=x;}',
    'fn f(){let x=();let _=x;}',
    'fn same<T>(x:T)->T{let _=x;x}',
    'fn f(){let _ignored=1u64;let _=_ignored;let _=_ignored;}',
    'enum E<T>{None,Some(T)}fn f(){let _:E<u64>=E::None;let _:E<_>=E::Some(4294967296u64);}',
    'struct R<T>{x:T}fn f(){let _:R<u64>=R{x:4294967296};}',
)


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


@pytest.mark.parametrize('text', VALID)
def test_wildcard_let_place_and_annotation_contract(text):
    compile_rust(text, experimental_rustc_checked=True)


def test_wildcard_has_distinct_syntax_and_no_binding_storage_or_place_load():
    text = 'struct R{x:u64}fn f(x:R){let _=x;}'
    parser = Parser(text)
    fn, = parser.parse()
    assert fn.body.children[0].kind == 'wildcard_let'
    assert fn.body.children[0].binding is None
    assert compile_rust(text, experimental_rustc_checked=True).ir_text == compile_rust(
        'struct R{x:u64}fn f(x:R){}', experimental_rustc_checked=True).ir_text


INVALID = (
    ('moved-index-base', 'struct R{x:u64}fn f(){let a=[R{x:1}];let b=a;let _=a[0];}', 'moved', 'E0382'),
    ('original-moved-array-program', source('wildcard_let_places'), 'moved', 'E0382'),
    ('wrong-annotation', 'fn f(){let _:bool=1u64;}', 'type mismatch', 'E0308'),
    ('array-length', 'fn f(){let _:[u64;2]=[1u64];}', 'type mismatch', 'E0308'),
    ('ambiguous-enum', 'enum E<T>{None,Some(T)}fn f(){let _=E::None;}', 'cannot infer', 'E0282'),
    ('unknown-name', 'fn f(){let _=missing;}', 'unknown', 'E0425'),
    ('overflow', 'fn f(){let _:i32=4294967296;}', 'out of range', 'literal out of range'),
    ('block-tail-moves', 'struct R{x:u64}fn f(){let x=R{x:1};let _={x};let y=x;}', 'moved', 'E0382'),
    ('call-argument-moves', 'struct R{x:u64}fn take(x:R){}fn f(){let x=R{x:1};let _=take(x);let y=x;}', 'moved', 'E0382'),
    ('underscore-name-moves', 'struct R{x:u64}fn f(){let x=R{x:1};let _named=x;let y=x;}', 'moved', 'E0382'),
    ('wildcard-mut', 'fn f(){let mut _=1;}', 'wildcard let patterns', 'mut'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_invalid_wildcard_originals_fail_before_lowering(name, text, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid wildcard reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('text,message', [
    ('fn f(){let _=loop{};}', 'actual source/options-bound CheckedRustSource'),
    ('fn f(){let [x]=[1];}', 'array destructuring'),
    # `fn f(){let (x,y)=(1,2);}` moved to test_rust_let_patterns (now supported).
    ('fn f(p:&u64){let _=p;}', 'actual source/options-bound CheckedRustSource'),
    ('fn f(){let _: &u64 = &1u64;}', 'actual source/options-bound CheckedRustSource'),
])
def test_wildcard_let_keeps_other_capability_boundaries(text, message):
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', PROGRAMS)
@pytest.mark.parametrize('target,magic', [
    ('x86_64-unknown-linux-gnu', b'\x7fELF'),
    ('arm64-apple-darwin', b'\xcf\xfa\xed\xfe'),
])
def test_wildcard_originals_emit_owned_objects(name, target, magic):
    assert compile_rust(source(name), experimental_rustc_checked=True,
                        target_triple=target).emit_object().startswith(magic)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
) else 'requires owned Linux x86-64 execution')
@pytest.mark.parametrize('name', PROGRAMS)
def test_wildcard_originals_execute_full_width_owned(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution
    test_original_match_fixture_full_width_owned_execution(name, tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc')
) else 'requires Linux native rustc/system-linker reference')
def test_wildcard_originals_match_actual_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference, 'PROGRAMS', PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_same_invalid_wildcard_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_accepts_wildcard_place_contracts(tmp_path):
    from tests.rust.certification_support import certify_source
    for index, text in enumerate(VALID):
        certify_source(text, tmp_path / str(index))


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_check_keeps_diverging_let_boundary(tmp_path):
    from tests.rust.certification_support import certify_source
    text = 'fn f(){let _=loop{};}'
    checked = certify_source(text, tmp_path / 'certifier')
    with pytest.raises(RustError, match='diverging let initializers'):
        checked.compile_owned()


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_reference_wildcard_still_uses_actual_certification(tmp_path):
    from tests.rust.certification_support import certify_source
    text = 'struct R{x:u64}fn f(p:&R){let _=*p;let _=p.x;}'
    checked = certify_source(text, tmp_path / 'certifier')
    result = checked.compile_owned()
    assert result.externally_checked and not result.borrow_checked_by_pcc
