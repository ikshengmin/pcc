"""Explicit struct-literal holes reuse existing whole-body constraints.

Rust Reference:
https://doc.rust-lang.org/reference/types/inferred.html
https://doc.rust-lang.org/reference/expressions/struct-expr.html
https://doc.rust-lang.org/reference/expressions.html#evaluation-order-of-operands
Only explicit type slots on named-field struct literals are added. Omitted
arguments, enum/qualified-owner holes and borrow certification are unchanged.
"""
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.call_inference import pending_integer
from pcc.frontends.rust.generics import GenericDeclarations
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.syntax import Parser
from pcc.frontends.rust.types import TypeApplication
from tests.rust.certification_support import actual_check_prerequisite


PROGRAMS = ('struct_placeholder_values', 'struct_placeholder_composites',
            'struct_placeholder_replay', 'struct_placeholder_order')


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


@pytest.mark.parametrize('text', [
    'struct S<T>{x:T}fn f(){S::<_>{x:1};}',
    'struct R<T>{x:T}fn f()->u64{R::<_>{x:1u64}.x}',
    'struct R<T>{x:T}fn f()->R<u64>{R::<_>{x:4294967296}}',
    'struct R<T>{x:T}fn take(x:R<u64>){}fn f(){let x=R::<_>{x:4294967296};take(x);}',
    'struct R<T>{x:T}fn f()->u64{let x:R<_>=R::<_>{x:4294967296};x.x}',
    'struct Pair<T,U>{a:T,b:U}fn f()->(u64,bool){let p=Pair::<_,bool>{b:true,a:4294967296};(p.a,p.b)}',
    'struct R<T>{x:T}fn f()->(u64,bool){R::<(_,_)>{x:(4294967296,true)}.x}',
    'struct R<T>{x:T}fn f()->[u64;2]{R::<[_;2]>{x:[4294967296,1]}.x}',
    'struct R<T>{x:T}fn f()->[u64;0]{R::<[_;0]>{x:[]}.x}',
    'struct R<T>{x:T}fn empty<T>()->[T;0]{[]}fn f()->R<[u64;0]>{R::<_>{x:empty()}}',
    'struct R<T>{x:T}fn outer<U>(x:U)->R<U>{R::<_>{x:x}}',
    'struct R<T>{x:T}fn id<T>(x:T)->T{x}fn f()->u64{id(R::<_>{x:4294967296}).x}',
    'struct R<T>{x:T}fn f()->R<u64>{let mut x=R::<_>{x:1};x=R::<u64>{x:4294967296};x}',
])
def test_explicit_struct_holes_share_fields_results_and_later_constraints(text):
    compile_rust(text, experimental_rustc_checked=True)


def test_data_registry_only_sees_resolved_arguments_and_reuses_nominal_identity(monkeypatch):
    original = GenericDeclarations.instantiate

    def concrete_only(self, ty, token):
        assert not pending_integer(ty), 'unresolved constructor reached data registry'
        return original(self, ty, token)

    monkeypatch.setattr(GenericDeclarations, 'instantiate', concrete_only)
    text = ('struct R<T>{x:T}fn take(x:R<u64>){}fn f(){'
            'let x=R::<_>{x:4294967296};take(x);let y=R::<u64>{x:1};take(y);}')
    parser = Parser(text)
    checker = Checker(parser.parse(), parser.records, parser.enums, parser.implementations)
    checker.check()
    entries = tuple(checker.generics.instances.entries.values())
    assert len(entries) == 1 and entries[0].value == TypeApplication('R', ('u64',))


INVALID = (
    ('unconstrained', 'struct R<T>{x:T}fn f(){R::<[_;0]>{x:[]};}', 'cannot infer', 'E0282'),
    ('fixed-slot', 'struct Pair<T,U>{a:T,b:U}fn f(){Pair::<_,bool>{a:1u64,b:2u64};}', 'type mismatch', 'E0308'),
    ('repeated-slot', 'struct Pair<T>{a:T,b:T}fn f(){Pair::<_>{a:1u64,b:true};}', 'type mismatch', 'E0308'),
    ('nominal-slot', 'struct Pair<T,U>{a:T,b:U}struct R<T>{x:T}fn f(){R::<Pair<_,bool>>{x:Pair::<u64,u64>{a:1,b:2}};}', 'type mismatch', 'E0308'),
    ('array-length', 'struct R<T>{x:T}fn f(){R::<[_;2]>{x:[1u64]};}', 'type mismatch', 'E0308'),
    ('arity', 'struct R<T>{x:T}fn f(){R::<_,_>{x:1u64};}', 'argument count', 'E0107'),
    ('missing-field', 'struct Pair<T,U>{a:T,b:U}fn f(){Pair::<_,bool>{a:1u64};}', 'missing struct field', 'E0063'),
    ('duplicate-field', 'struct R<T>{x:T}fn f(){R::<_>{x:1u64,x:2u64};}', 'duplicate field', 'E0062'),
    ('unknown-field', 'struct R<T>{x:T}fn f(){R::<_>{y:1u64};}', 'unknown struct field', 'E0560'),
    ('overflow', 'struct R<T>{x:T}fn f()->R<i32>{R::<_>{x:4294967296}}', 'out of range', 'literal out of range'),
    ('noncopy-move', 'struct R<T>{x:T}fn take<T>(x:R<T>){}fn f()->u64{let r=R::<_>{x:1u64};take(r);r.x}', 'moved', 'E0382'),
    ('unused-abstract-move', 'struct R<T>{x:T}fn bad<U>(x:U)->U{let r=R::<_>{x:x};x}', 'moved', 'E0382'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_invalid_or_ambiguous_constructors_fail_before_lowering(name, text, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid constructor reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('text,message', [
    ('struct R<T>{x:T}fn f(){R{x:1u64};}', 'requires explicit type arguments'),
    ('enum E<T>{Some(T)}fn f(){E::<_>::Some(1u64);}', "unsupported Rust type '_'"),
    ('struct R<T>{x:T}impl<T>R<T>{fn take(self)->T{self.x}}fn f()->u64{R::<_>::take(R::<u64>{x:1})}', "unsupported Rust type '_'"),
])
def test_constructor_holes_do_not_change_omitted_enum_or_owner_boundaries(text, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported path reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


def test_constructor_holes_do_not_authorize_reference_storage(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('uncertified reference reached IR'))
    text = 'struct R<T>{x:T}fn f(p:&u64){R::<_>{x:p};}'
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', PROGRAMS)
@pytest.mark.parametrize('target,magic', [
    ('x86_64-unknown-linux-gnu', b'\x7fELF'),
    ('arm64-apple-darwin', b'\xcf\xfa\xed\xfe'),
])
def test_constructor_hole_originals_emit_owned_target_objects(name, target, magic):
    result = compile_rust(source(name), experimental_rustc_checked=True, target_triple=target)
    assert result.emit_object().startswith(magic)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
) else 'requires owned Linux x86-64 execution')
@pytest.mark.parametrize('name', PROGRAMS)
def test_constructor_hole_originals_execute_full_width_owned(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution
    test_original_match_fixture_full_width_owned_execution(name, tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc')
) else 'requires Linux native rustc/system-linker reference')
def test_constructor_hole_originals_match_actual_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference, 'PROGRAMS', PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_same_invalid_constructor_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
