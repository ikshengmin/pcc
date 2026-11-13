"""Generic and blanket trait impls are parsed; their instantiation is a gap.

https://doc.rust-lang.org/reference/items/implementations.html#trait-implementations
`impl<T: Copy> Tr for S<T>`, `impl<'a, I: ?Sized> Tr for &'a mut I where I: Tr`
are retained with their type/lifetime parameters, self type and where
predicates (as fallible-streaming-iterator's blanket impls are). Instantiating
them needs impl selection with coherence checking, which is not implemented:
that is reported at the `impl`, never silently skipped.
"""
import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from pcc.frontends.rust.types import SharedReference, TypeApplication


def test_generic_and_blanket_trait_impls_are_retained():
    parser = Parser("trait Tr{fn f(&self);} struct S<T>{x:T}\n"
                    "impl<T: Copy> Tr for S<T> { fn f(&self){} }\n"
                    "impl<'a, I: ?Sized> Tr for &'a mut I where I: Tr { fn f(&self){} }")
    parser.parse()
    generic, blanket = parser.implementations
    assert (generic.trait, generic.owner, [t.text for t in generic.type_parameters]) == (
        'Tr', TypeApplication('S', ('T',)), ['T'])
    assert (blanket.trait, blanket.owner, [t.text for t in blanket.lifetime_parameters]) == (
        'Tr', SharedReference('I', True), ["'a"])
    assert blanket.owner.lifetime == "'a" and blanket.where_predicates


@pytest.mark.parametrize('source', [
    "trait Tr{fn f(self)->u64;} struct S<T>{x:T} impl<T: Copy> Tr for S<T> { fn f(self)->u64{1} } pub fn g(){}",
    "trait Tr{fn f(self)->u64;} struct S<T>{x:T} impl<T> Tr for S<T> where T: Copy { fn f(self)->u64{1} } pub fn g(){}",
])
def test_their_instantiation_is_an_explicit_semantic_gap(source):
    with pytest.raises(RustError, match='blanket/generic trait impls require impl instantiation and coherence'):
        compile_rust(source, experimental_rustc_checked=True)


def test_concrete_trait_impls_still_compile():
    source = "trait Tr{fn f(self)->u64;} struct S{x:u64} impl Tr for S { fn f(self)->u64{self.x} } pub fn g()->u64{S{x:3}.f()}"
    assert 'define' in compile_rust(source, experimental_rustc_checked=True).ir_text
