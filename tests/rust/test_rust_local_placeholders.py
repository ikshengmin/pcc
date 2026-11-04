"""Local annotation holes share deferred generic call/result constraints.

Rust Reference, retrieved 2026-11-04:
https://doc.rust-lang.org/reference/types/inferred.html
https://doc.rust-lang.org/reference/statements.html#let-statements
https://doc.rust-lang.org/reference/expressions.html#evaluation-order-of-operands
Item signatures, constructor/owner holes and reference certification retain
their existing boundaries. This does not implement a new inference solver.
"""
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.syntax import Parser
from pcc.support.instantiation import InstanceState
from tests.rust.certification_support import actual_check_prerequisite


PROGRAMS = (
    'local_placeholder_values', 'local_placeholder_composites',
    'local_placeholder_replay', 'local_placeholder_order',
)


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


@pytest.mark.parametrize('text', [
    'fn f()->u64{let x:_=1u64;x}',
    'fn f()->u64{let x:_=4294967296;x}',
    'fn id<T>(x:T)->T{x}fn f()->u64{let x:_=id(4294967296);x}',
    'fn id<T>(x:T)->T{x}fn take(x:u64){}fn f(){let x:_=id(4294967296);take(x);}',
    'fn f(){let x:_=true;let y:_=42u64;}',
    'fn f()->(u64,bool){let x:(_,bool)=(4294967296,true);x}',
    'fn f()->[u64;2]{let x:[_;2]=[4294967296,1];x}',
    'fn f()->[u64;0]{let x:[_;0]=[];x}',
    'fn empty<T>()->[T;0]{[]}fn f()->[u64;0]{let x:[_;0]=empty();x}',
    'fn id<T>(x:T)->T{x}fn outer<U>(x:U)->U{let y:_=id::<_>(x);y}',
    'struct R<T>{x:T}fn make<T>(x:T)->R<T>{R::<T>{x:x}}'
    'fn f()->u64{let x:R<_>=make(4294967296);x.x}',
    'enum E<T>{None,Some(T)}fn empty<T>()->E<T>{E::<T>::None}'
    'fn f(){let mut x:E<_>=empty();x=E::<u64>::Some(4294967296);}',
    'fn f()->u64{let x:_=1u64;{let x:_=true;}x}',
])
def test_local_holes_receive_initializer_result_and_later_constraints(text):
    compile_rust(text, experimental_rustc_checked=True)


def test_local_holes_reuse_concrete_and_call_placeholder_instances():
    text = ('fn id<T>(x:T)->T{x}fn f(){'
            'let a:_=id(4294967296);let b:u64=id::<_>(a);'
            'let c:_=id::<u64>(b);let d:_=id(true);let e:_=id(3u32);}')
    parser = Parser(text)
    checker = Checker(parser.parse(), parser.records, parser.enums, parser.implementations)
    checker.check()
    entries = tuple(checker.function_generics.instances.entries.values())
    assert len(entries) == 3
    assert {entry.value.instance_arguments for entry in entries} == {('u64',), ('bool',), ('u32',)}
    assert all(entry.state is InstanceState.COMPLETE and entry.value is entry.result
               for entry in entries)


def test_unused_abstract_body_does_not_create_a_concrete_instance():
    text = 'fn id<T>(x:T)->T{x}fn outer<U>(x:U)->U{let value:_=id(x);value}'
    parser = Parser(text)
    checker = Checker(parser.parse(), parser.records, parser.enums, parser.implementations)
    checker.check()
    assert not checker.function_generics.instances.entries
    result = compile_rust(text, experimental_rustc_checked=True)
    assert result.symbols == {} and 'define' not in result.ir_text


# Complete originals are reused verbatim by the independently gated oracle.
INVALID = (
    ('unconstrained-array', 'fn f(){let x:[_;0]=[];}', 'cannot infer', 'E0282'),
    ('unconstrained-call', 'fn empty<T>()->[T;0]{[]}fn f(){let x:_=empty();}', 'cannot infer', 'E0282'),
    ('fixed-tuple-slot', 'fn f(){let x:(_,bool)=(1u64,2u64);}', 'type mismatch', 'E0308'),
    ('array-length', 'fn f(){let x:[_;2]=[1u64];}', 'type mismatch', 'E0308'),
    ('later-conflict', 'fn take(x:bool){}fn f(){let x:_=1u64;take(x);}', 'type mismatch', 'E0308'),
    ('out-of-range', 'fn f()->i32{let x:_=4294967296;x}', 'out of range', 'literal out of range'),
    ('noncopy-move', 'struct R{x:u64}fn id<T>(x:T)->T{x}fn f()->u64{let r:_=R{x:1};id(r);r.x}', 'moved', 'E0382'),
    ('unused-generic-move', 'fn id<T>(x:T)->T{x}fn bad<U>(x:U)->U{let y:_=id(x);x}', 'moved', 'E0382'),
    ('item-parameter', 'fn invalid(x:_){}', "unsupported Rust type '_'", 'E0121'),
    ('item-result', 'fn invalid()->_{1u64}', "unsupported Rust type '_'", 'E0121'),
    ('item-field', 'struct Invalid{x:_}', "unsupported Rust type '_'", 'E0121'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_ambiguous_invalid_and_moved_local_holes_fail_before_ir(name, text, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid local placeholder reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('text', [
    'fn f()->u64{let x=1u64;let p:_=&x;*p}',
    'fn f(p:&u64)->u64{let q:&_=p;*q}',
    'fn f()->u64{let mut x=1u64;let p:&mut _=&mut x;*p=2u64;*p}',
])
def test_local_holes_cannot_mint_reference_certification(text, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('uncertified reference reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', PROGRAMS)
@pytest.mark.parametrize('target,magic', [
    ('x86_64-unknown-linux-gnu', b'\x7fELF'),
    ('arm64-apple-darwin', b'\xcf\xfa\xed\xfe'),
])
def test_local_placeholder_originals_emit_owned_target_objects(name, target, magic):
    result = compile_rust(source(name), experimental_rustc_checked=True, target_triple=target)
    assert result.emit_object().startswith(magic)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
) else 'requires owned Linux x86-64 execution')
@pytest.mark.parametrize('name', PROGRAMS)
def test_local_placeholder_originals_execute_full_width_owned(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution
    test_original_match_fixture_full_width_owned_execution(name, tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc')
) else 'requires Linux native rustc/system-linker reference')
def test_local_placeholder_originals_match_native_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference, 'PROGRAMS', PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_genuine_rustc_rejects_same_invalid_local_placeholder_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
