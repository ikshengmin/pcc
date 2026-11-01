"""Local static traits, not a generic bound solver or dynamic dispatch.

Rust Reference, retrieved 2026-11-01:
https://doc.rust-lang.org/reference/items/traits.html
https://doc.rust-lang.org/reference/items/implementations.html#trait-implementations
https://doc.rust-lang.org/reference/expressions/method-call-expr.html
https://doc.rust-lang.org/reference/paths.html#qualified-paths
https://doc.rust-lang.org/reference/expressions.html#evaluation-order-of-operands
Ledger: rust.traits.static-local-impls. Every reference input needs a real check.
"""
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from pcc.frontends.rust.semantics import Checker
from pcc.support.instantiation import InstanceState
from tests.rust.certification_support import actual_check_prerequisite, darwin_check_prerequisite

PROGRAMS = ('trait_static_values', 'trait_static_qualified', 'trait_static_generics',
            'trait_static_concrete_owners', 'trait_static_shadowing', 'trait_static_enum_namespace',
            'trait_static_receivers', 'trait_static_order', 'trait_static_local_drop_name')
VALUE_PROGRAMS, REFERENCE_PROGRAMS = PROGRAMS[:6], PROGRAMS[6:]


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def compile_source(text):
    return compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', VALUE_PROGRAMS)
@pytest.mark.parametrize('target,magic', [('x86_64-unknown-linux-gnu', b'\x7fELF'),
                                        ('arm64-apple-darwin', b'\xcf\xfa\xed\xfe')])
def test_static_value_trait_originals_have_real_target_objects(name, target, magic):
    result = compile_rust(source(name), experimental_rustc_checked=True, target_triple=target)
    assert result.emit_object().startswith(magic)


def test_trait_signatures_are_not_emitted_and_instances_share_the_existing_registry():
    text = ('trait A{fn id<U>(self,x:U)->U;} trait B{fn id<U>(self,x:U)->U;}'
            'struct R{x:u64} impl A for R{fn id<V>(self,x:V)->V{x}}'
            'impl B for R{fn id<W>(self,x:W)->W{x}}'
            'fn f()->u64{let a=<R as A>::id(R{x:1},40u64);'
            'let b=<R as A>::id::<u64>(R{x:1},1);'
            'let c=<R as B>::id(R{x:1},1u64);a+b+c}')
    parser = Parser(text)
    functions = parser.parse()
    assert len(parser.traits) == 2 and len(functions) == 3
    checker = Checker(functions, parser.records, parser.enums, parser.implementations, parser.traits)
    checker.check()
    entries = tuple(checker.function_generics.instances.entries.values())
    assert len(entries) == 2 and all(e.state is InstanceState.COMPLETE for e in entries)
    assert {e.value.declaration_name for e in entries} == {'<R as A>::id', '<R as B>::id'}
    assert len({e.value.identity for e in entries}) == 2
    assert all(f.body is not None for f in functions)


# Complete originals: both owned before-IR rejection and actual rustc rejection
# below consume these exact strings. Capability gaps have a separate table.
INVALID = (
    ('missing', 'trait T{fn f(self);} struct R{x:u64} impl T for R{}', 'missing required', 'E0046'),
    ('extra', 'trait T{} struct R{x:u64} impl T for R{fn f(self){}}', 'not a member', 'E0407'),
    ('duplicate-impl', 'trait T{} struct R{x:u64} impl T for R{} impl T for R{}', 'conflicting', 'E0119'),
    ('duplicate-item', 'trait T{fn f(self);fn f(self);}', 'duplicate trait', 'E0428'),
    ('duplicate-method', 'trait T{fn f(self);} struct R{x:u64} impl T for R{fn f(self){} fn f(self){}}', 'duplicate trait', 'E0201'),
    ('result', 'trait T{fn f(self)->u64;} struct R{x:u64} impl T for R{fn f(self)->bool{true}}', 'signature mismatch', 'E0053'),
    ('parameter', 'trait T{fn f(self,x:u64);} struct R{x:u64} impl T for R{fn f(self,x:bool){}}', 'signature mismatch', 'E0053'),
    ('receiver', 'trait T{fn f(self);} struct R{x:u64} impl T for R{fn f(x:Self){}}', 'receiver', 'E0186'),
    ('arity', 'trait T{fn f<U>(self,x:U);} struct R{x:u64} impl T for R{fn f(self,x:u64){}}', 'generic parameter count', 'E0049'),
    ('generic-position', 'trait T{fn f<U,V>(self,x:U,y:V)->U;} struct R{x:u64} impl T for R{fn f<A,B>(self,x:B,y:A)->A{y}}', 'signature mismatch', 'E0053'),
    ('ambiguous', 'trait A{fn f(self);} trait B{fn f(self);} struct R{x:u64} impl A for R{fn f(self){}} impl B for R{fn f(self){}} fn g(){R{x:1}.f();}', 'ambiguous', 'E0034'),
    ('qualified-missing', 'trait T{fn f(self);} struct R{x:u64} fn g(){<R as T>::f(R{x:1});}', 'no concrete implementation', 'E0277'),
    ('move', 'trait T{fn f(self);} struct R{x:u64} impl T for R{fn f(self){}} fn g(){let r=R{x:1};r.f();r.f();}', 'moved', 'E0382'),
    ('abstract-move', 'trait T{fn f<U>(self,x:U)->(U,U);} struct R{x:u64} impl T for R{fn f<V>(self,x:V)->(V,V){(x,x)}}', 'moved', 'E0382'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_invalid_trait_originals_reject_before_ir(name, text, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid trait reached IR'))
    with pytest.raises(RustError, match=message):
        compile_source(text)


@pytest.mark.parametrize('text,message', [
    ('trait T<U>{}', 'generic traits'),
    ('trait T{fn f()->[Self;1];}', 'Sized/bound'),
    ('struct R<X>{x:X} trait T{fn f()->R<Self>;}', 'Sized/bound'),
    ('trait T:Copy{}', 'supertraits'),
    ('trait T{fn f(self){}}', 'default trait'),
    ('trait T{type Item;}', 'associated types'),
    ('trait T{const N:u64;}', 'associated types'),
    ('unsafe trait T{}', "expected 'fn'"),
    ('trait T{unsafe fn f(self);}', 'safe required'),
    ('trait T{fn f<U:Copy>(self,x:U);}', 'bounds'),
    ('trait T{} struct R<U>{x:U} impl<U> T for R<U>{}', 'blanket/generic'),
    ('trait T{} impl T for u64{}', 'local nominal'),
    ('trait T{} fn f<U:T>(x:U){}', 'bounds'),
    ('trait T{} fn f(x:&dyn T){}', 'reference-bearing|unsupported|expected'),
])
def test_trait_capability_frontiers_are_not_silent_fallbacks(text, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported trait reached IR'))
    with pytest.raises(RustError, match=message):
        compile_source(text)


@pytest.mark.parametrize('name', REFERENCE_PROGRAMS)
def test_trait_references_require_actual_check_not_boolean(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unchecked trait reference reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_source(source(name))


def test_unused_trait_reference_signature_alone_requires_actual_check(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unchecked signature reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_source('trait T{fn f(&self);} fn answer()->u64{42}')


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64','AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_unchanged_static_trait_originals_match_actual_native_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference, 'PROGRAMS', PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch)


REFERENCE_INVALID = (
    ('receiver-mutability', 'trait T{fn f(&self);} struct R{x:u64} impl T for R{fn f(&mut self){}}', 'E0053'),
    ('borrow-after-move', 'trait T{fn f(self);} struct R{x:u64} impl T for R{fn f(self){}} fn g(){let r=R{x:1};r.f();let p=&r;}', 'E0382'),
    ('no-mutability-fallback', 'trait A{fn f(&mut self);} struct R{x:u64} impl A for R{fn f(&mut self){}} fn g(){let r=R{x:1};r.f();}', 'E0596'),
    ('receiver-ambiguity', 'trait A{fn f(&self);} trait B{fn f(&self);} struct R{x:u64} impl A for R{fn f(&self){}} impl B for R{fn f(&self){}} fn g(){let r=R{x:1};r.f();}', 'E0034'),
)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_invalid_trait_originals_before_owned_ir(tmp_path, monkeypatch):
    from pcc.frontends.rust import RustExternalCheckError, compile_rust_checked
    from pcc.frontends.rust.lowering import Lowerer
    from tests.rust.test_rust_certified_references import toolchain
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('rustc-invalid trait reached IR'))
    originals = [(name, text, code) for name, text, _, code in INVALID] + list(REFERENCE_INVALID)
    for name, text, code in originals:
        directory = tmp_path / name
        with pytest.raises(RustExternalCheckError, match=code):
            compile_rust_checked(text, toolchain=toolchain(), evidence_directory=directory)
        assert (directory / 'original.rs').read_text() == text
        assert not (directory / 'checked.rmeta').exists()


@pytest.mark.parametrize('name', REFERENCE_PROGRAMS)
@pytest.mark.pcc_gate(unavailable=darwin_check_prerequisite())
def test_reference_trait_cross_object_requires_actual_target_check(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_targets import test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite
    test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite(name, monkeypatch, tmp_path=tmp_path)


def test_concrete_array_arguments_are_canonical_impl_identities():
    text = ('trait T{fn n(self)->u64;} struct R<X>{x:X}'
            'impl T for R<[u64;1+1]>{fn n(self)->u64{2}}'
            'impl T for R<[u64;3]>{fn n(self)->u64{3}}'
            'fn f()->u64{R::<[u64;2]>{x:[0;2]}.n()+R::<[u64;3]>{x:[0;3]}.n()}')
    result = compile_source(text)
    assert '<R<[u64; 2]> as T>::n' in result.symbols
    assert '<R<[u64; 3]> as T>::n' in result.symbols


def test_constant_spelling_cannot_evade_impl_coherence(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    text = ('trait T{} struct R<X>{x:X}'
            'impl T for R<[u64;1+1]>{} impl T for R<[u64;2]>{}')
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('conflicting impl reached IR'))
    with pytest.raises(RustError, match='conflicting'):
        compile_source(text)


def test_enum_constructor_wins_over_same_named_trait_associated_item():
    # Exact reviewer original; explicit qualification separately selects T::V.
    text = ('enum E{V(u64),Other(u64)} trait T{fn V(x:u64)->Self;}'
            'impl T for E{fn V(x:u64)->Self{E::Other(x+1)}}'
            'fn f()->u64{match E::V(41){E::V(x)=>x,E::Other(x)=>x}}')
    result = compile_source(text)
    assert 'f' in result.symbols and '<E as T>::V' in result.symbols
    parser = Parser(text)
    functions = parser.parse()
    checker = Checker(functions, parser.records, parser.enums, parser.implementations, parser.traits)
    checker.check()
    scrutinee = checker.names['f'].body.children[-1].children[0]
    assert (scrutinee.kind, scrutinee.value, scrutinee.ty) == ('enum_construct', 'V', 'E')


def test_trait_lifetime_capability_model_does_not_bypass_public_admission(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    from tests.rust.analysis_only import analyze_source
    text = "trait T{fn f<'a>(&'a self);}"
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unqualified lifetime reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_source(text)
    with pytest.raises(RustError, match='lifetime parameters'):
        analyze_source(text, moves=False)  # Facts-only model, no execution authorization.
