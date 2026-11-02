"""Inherent method type parameters are distinct from their fixed impl prefix.

Rust Reference, retrieved 2026-11-01:
https://doc.rust-lang.org/reference/items/generics.html
https://doc.rust-lang.org/reference/items/associated-items.html#associated-functions-and-methods
https://doc.rust-lang.org/reference/expressions/method-call-expr.html
https://doc.rust-lang.org/reference/paths.html#paths-in-expressions
https://doc.rust-lang.org/reference/expressions.html#evaluation-order-of-operands
Ledger: rust.generics.inherent-method-inference. No trait solver or new cache.
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
from tests.rust.certification_support import actual_check_prerequisite, darwin_check_prerequisite

PROGRAMS = ('method_generic_owner_split', 'method_generic_result_context_u32',
            'method_generic_recursion', 'method_generic_nominal', 'method_generic_shadowing',
            'method_generic_order', 'method_generic_shared', 'method_generic_mutable')
VALUE_PROGRAMS = PROGRAMS[:5]
REFERENCE_PROGRAMS = PROGRAMS[5:]


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def compile_source(text):
    return compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', VALUE_PROGRAMS)
@pytest.mark.parametrize('target,magic', [('x86_64-unknown-linux-gnu', b'\x7fELF'),
                                        ('arm64-apple-darwin', b'\xcf\xfa\xed\xfe')])
def test_value_method_originals_have_real_target_objects(name, target, magic):
    result = compile_rust(source(name), experimental_rustc_checked=True, target_triple=target)
    assert result.emit_object().startswith(magic)


@pytest.mark.parametrize('text', [
    # Exact original previously in test_rust_methods.py's capability negatives.
    'struct R{x:i64} impl R{fn f<T>(self,x:T){}}',
    'struct R{x:u64} impl R{fn take<U>(self,x:U)->U{x}} fn f()->u64{R{x:1}.take(42)}',
    'struct R<T>{x:T} impl<T> R<T>{fn pair<U>(self,x:U)->(T,U){(self.x,x)}} fn f()->(u64,bool){R::<u64>{x:1}.pair(true)}',
    'struct R{x:u64} impl R{fn empty<U>()->[U;0]{[]}} fn f()->[u64;0]{R::empty()}',
    'struct R<T>{x:T} impl<T> R<T>{fn empty<U>()->[U;0]{[]}} fn f()->[u64;0]{R::<bool>::empty()}',
    'struct R{x:u64} impl R{fn head<U>(x:(U,))->U{x.0}} fn forever<V>()->V{forever()} fn f()->u64{R::head(forever())}',
    'struct R{x:u64} impl R{fn head<U>(x:(U,))->U{x.0}} fn forever<V>()->V{forever()} fn outer<V>()->V{R::head(forever())}',
    'struct R{x:u64} impl R{fn pass<U>(self,x:U)->U{x} fn outer<V>(self,x:V)->V{self.pass(x)}}',
    'struct R{x:u64} impl R{fn pass<R>(self,x:R)->R{x}} fn f()->bool{R{x:1}.pass(true)}',
    'struct R<T>{x:T} impl<T> R<T>{fn pass<R>(self,x:R)->R{x}} fn f()->bool{R::<u64>{x:1}.pass(true)}',
    'struct R{x:u64} impl R{fn id<U>(self,x:U)->U{x} fn pass<R>(self,x:R)->R{self.id(x)}}',
    'struct R<T>{x:T} impl<T> R<T>{fn id<U>(self,x:U)->U{x} fn pass<R>(self,x:R)->R{self.id(x)}}',
    'struct R{x:u64} impl R{fn id<U>(x:U)->U{x} fn pass<R>(self)->Self{Self::id(self)}} fn f()->u64{R{x:42}.pass::<bool>().x}',
    'struct R{x:u64} struct W<T>{x:T} impl<T> W<T>{fn id<U>(self,u:U)->Self{self} fn pass<R>(self,r:R)->Self{self.id::<R>(r)}} fn f()->u64{W::<R>{x:R{x:42}}.pass::<bool>(true).x.x}',
    'struct R{x:u64} impl R{fn unused<U>(self){}} fn f(){R{x:1}.unused::<bool>();}',
])
def test_method_arguments_and_result_contexts_share_fresh_variables(text):
    compile_source(text)


def test_impl_prefix_method_suffix_and_registry_identity_are_separate():
    text = ('struct Pair<A,B>{a:A,b:B} impl<X,Y> Pair<Y,X>{fn take<U>(self,x:U)->U{x}}'
            'struct Other<A,B>{a:A,b:B} impl<X,Y> Other<Y,X>{fn take<U>(self,x:U)->U{x}}'
            'fn f(){let a=Pair::<u64,bool>{a:1,b:true}.take(2u32);'
            'let b=Pair::<u64,bool>::take::<u32>(Pair::<u64,bool>{a:1,b:true},3);'
            'let c=Pair::<u64,bool>{a:1,b:true}.take(4u64);'
            'let d=Other::<u64,bool>{a:1,b:true}.take(5u32);}')
    parser = Parser(text)
    functions = parser.parse()
    assert [f.owner_parameter_count for f in functions if f.implementation] == [2, 2]
    assert [[p.text for p in f.type_parameters] for f in functions if f.implementation] == [['X', 'Y', 'U'], ['X', 'Y', 'U']]
    checker = Checker(functions, parser.records, parser.enums, parser.implementations)
    checker.check()
    entries = tuple(checker.function_generics.instances.entries.values())
    assert len(entries) == 3 and all(e.state is InstanceState.COMPLETE and e.value is e.result for e in entries)
    assert {(e.value.declaration_name, e.value.instance_arguments) for e in entries} == {
        ('Pair::take', ('bool', 'u64', 'u32')), ('Pair::take', ('bool', 'u64', 'u64')),
        ('Other::take', ('bool', 'u64', 'u32')),
    }
    assert len({e.value.identity for e in entries}) == 3


@pytest.mark.parametrize('text,message', [
    ('struct R<T>{x:T} impl<T> R<T>{fn bad<T>(self,x:T){}}', 'duplicate generic parameter'),
    ('struct R{x:u64} impl R{fn bad<U,U>(self,x:U){}}', 'duplicate generic parameter'),
    ('struct R{x:u64} impl R{fn unused<U>(self){}} fn f(){R{x:1}.unused();}', 'cannot infer generic parameter'),
    ('struct R{x:u64} impl R{fn empty<U>()->[U;0]{[]}} fn f(){let x=R::empty();}', 'cannot infer generic parameter'),
    ('struct R<T>{x:T} impl<T> R<T>{fn take<U>(self,x:U)->U{x}} fn f(){R::<u64>{x:1}.take::<u64,bool>(1);}', 'method argument count'),
    ('struct R{x:u64} impl R{fn take(self){}} fn f(){R{x:1}.take::<u64>();}', 'method argument count'),
    ('struct R{x:u64} impl R{fn take<U>(self,x:U)->U{x}} fn f(){R{x:1}.take::<bool>(1u64);}', 'type mismatch'),
    ('struct R<T>{x:T} impl<T> R<T>{fn take<U>(self,x:U)->U{x}} fn f(){R::<u64>::take::<bool>(R::<u32>{x:1},true);}', 'type mismatch'),
    ('struct R{x:u64} impl R{fn bad<U>(self,x:U)->(U,U){(x,x)}}', 'moved'),
    ('struct R{x:u64} impl R{fn bad<U>(self,x:U)->U{x+x}}', 'operator'),
    ('struct R{x:u64} impl R{fn bound<U:Copy>(self,x:U){}}', 'actual source/options-bound CheckedRustSource'),
    # Exact method-where control retained as a genuine-check positive in
    # test_rust_inherent_local_bounds.
    ('struct R{x:u64} impl R{fn bound<const N:usize>(self){}}', 'const generic'),
    ('struct R{x:u64} impl R{fn pick<U>(self,x:U)->U{x}} fn f(){let g=R::pick::<u64>;}', 'direct call'),
    ('struct R{x:u64} impl R{fn pick<U>(self,x:U)->U{x}} fn f(){R::pick::<u64>(1);}', 'expects 2 arguments'),
    ('enum E<T>{Some(T)} fn f(){E::<u64>::Some::<u64>(1);}', 'enum constructor segments'),
    ('fn f(){[1u64].len::<u64>();}', 'not a generic method'),
    ('struct R{x:u64} impl R{fn same<U>(a:U,b:U){}} fn forever<T>()->T{forever()} fn f(){let a=forever();R::same(a,(a,));}', 'infinite inferred'),
])
def test_invalid_ambiguous_or_out_of_scope_methods_fail_before_ir(text, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid method generic reached IR'))
    with pytest.raises(RustError, match=message):
        compile_source(text)


@pytest.mark.parametrize('name', REFERENCE_PROGRAMS)
def test_method_generics_never_turn_reference_flag_into_certification(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('uncertified method reference reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_source(source(name))


def test_reference_in_method_type_arguments_is_structurally_detected(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    text = ('struct R{x:u64} impl R{fn accept<U>(x:U){}}'
            'fn forever<T>()->T{forever()} fn f(){R::accept::<&u64>(forever());}')
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('reference type argument reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_source(text)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_unchanged_method_generic_originals_match_native_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference, 'PROGRAMS', PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_checker_rejects_original_generic_method_move_before_ir(tmp_path, monkeypatch):
    from pcc.frontends.rust import RustExternalCheckError, compile_rust_checked
    from pcc.frontends.rust.lowering import Lowerer
    from tests.rust.test_rust_certified_references import toolchain
    text = 'struct R{x:u64} impl R{fn take<U>(self,x:U)->U{x}} fn f(){let r=R{x:1};r.take(1u64);r.take(2u64);}'
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('rustc-rejected method move reached IR'))
    with pytest.raises(RustExternalCheckError, match='E0382'):
        compile_rust_checked(text, toolchain=toolchain(), evidence_directory=tmp_path / 'check')
    assert (tmp_path / 'check/original.rs').read_text() == text
    assert not (tmp_path / 'check/checked.rmeta').exists()


@pytest.mark.pcc_gate(unavailable=darwin_check_prerequisite())
@pytest.mark.parametrize('name', REFERENCE_PROGRAMS)
def test_reference_method_cross_object_requires_matching_target_check(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_targets import test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite
    test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite(name, monkeypatch, tmp_path=tmp_path)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_original_u16_result_context_is_valid_rust_but_owned_capability_gap(tmp_path, monkeypatch):
    # Preserve the failed original exactly. u16 is valid Rust, not an invalid
    # program; the distinct u32 original exercises the currently owned types.
    from pcc.frontends.rust.lowering import Lowerer
    from tests.rust.certification_support import certify_source
    text = source('method_generic_result_context')
    checked = certify_source(text, tmp_path / 'actual-rustc-check')
    assert checked.provenance.exit_code == 0
    assert (tmp_path / 'actual-rustc-check/original.rs').read_text() == text
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported u16 reached IR'))
    with pytest.raises(RustError, match='invalid or unsupported integer literal'):
        compile_rust(text, rustc_check=checked)
