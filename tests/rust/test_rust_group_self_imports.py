"""Bind brace-terminal self to the parent's type namespace, never its value.

See Rust Reference items.use.self and rustc_resolve build_reduced_graph (1.91.1
and 1.95.0). No use declaration manufactures std, core, generated files or ABI.
"""
import shutil

import pytest

from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function, run_rustc_str_function

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.crate_context import parse_crate_sources
from pcc.frontends.rust.module_resolution import ModuleNamespaces
from pcc.frontends.rust.syntax import Parser


def scopes(src, edition=2024):
    return ModuleNamespaces(parse_crate_sources('lib.rs', {'lib.rs':src},
                            crate_name='group_self', edition=edition))


def test_keeps_parent_identity_namespace_self_token_alias_and_whole_group_span():
    source=('mod outer {pub mod middle {pub mod inner {pub fn x(){}} pub fn y(){} } }\n'
            '/// a source import\npub use crate::outer::middle::{self as alias, inner::{self, x}, y};')
    resolved=scopes(source)
    items=resolved.crate.root.body.uses
    assert [('::'.join(t.text for t in u.path),u.binding.text,
             u.group_self.text if u.group_self else None) for u in items]==[
        ('crate::outer::middle','alias','self'),
        ('crate::outer::middle::inner','inner','self'),
        ('crate::outer::middle::inner::x','x',None),('crate::outer::middle::y','y',None)]
    for u in items:
        assert u.public and u.documentation[0].text==' a source import'
        assert source[slice(*u.source_span)].endswith('inner::{self, x}, y};')
    for u in (items[0],items[1]):
        assert u.group_self.source_path=='lib.rs' and u.group_self.line==3
        assert resolved.resolve(u.binding.text,'value',resolved.root,u.token,_optional_final=True) is None
    assert resolved.resolve('alias::inner::x','value',resolved.root,items[0].token).identity.name=='x'
    assert resolved.resolve('inner::x','value',resolved.root,items[0].token).identity.module.components==('outer','middle','inner')


def test_self_binds_only_type_without_even_checking_inaccessible_value_with_the_same_spelling():
    src=('mod producer { pub mod both {pub fn inside()->u64{40}} fn both()->u64{777} } '
         'use producer::both::{self as imported}; '
         'fn imported()->u64{2} pub fn answer()->u64{imported::inside()+imported()}')
    result=compile_rust(src)
    assert result.emit_object()[:4]==b'\x7fELF'
    # Ordinary imports still inspect both namespaces, including accessibility.
    with pytest.raises(RustError,match='private'):
        compile_rust(src.replace('producer::both::{self as imported}','producer::both as imported'))


def test_no_value_is_exported_even_if_the_parent_has_a_public_function_of_the_same_name():
    text=('mod boths{pub mod both{} pub fn both(){}} mod consumer {'
          'pub use crate::boths::both::{self};} '
          'pub fn test(){consumer::both();}')
    r=scopes(text)
    token=r.crate.root.body.functions[0].token
    assert r.resolve('consumer::both','type',r.root,token).identity.name=='both'
    assert r.resolve('consumer::both','value',r.root,token,_optional_final=True) is None
    with pytest.raises(RustError):
        compile_rust(text)


@pytest.mark.parametrize('src,reason',[
 ('mod item {} use item::{self};','duplicate imported type-namespace'),
 ('mod p{mod hidden{}} use p::hidden::{self as got};','private'),
 ('mod p{mod hidden{} pub use self::hidden::{self as leaked};}','cannot be re-exported'),
 ('mod p {pub fn f(){}} use p::f::{self};','unknown use target'),
 ('mod p {pub struct S{}} use p::S::{self as S};','parent kind'),
 ('use {self as alias};','supported named parent'),
 ('mod p{} use p::{self as one, self as two};','multiple direct self'),
 ('mod p{} use p::{self as _};','anonymous'),
 ('use std::process::{self, Command};','source dependency is unavailable'),
 ('mod p{} use p::{self::bad};','nonleading self'),
])
def test_illegal_or_unimplemented_parent_spelling_privacy_and_providers_fail_closed(src,reason):
    with pytest.raises(RustError,match=reason):
        scopes(src)


def test_private_group_self_reexport_of_type_cannot_block_unrelated_value_lookup():
    src='mod a {pub mod b{}} mod b { use crate::a::b::{self as v}; }'
    r=scopes(src)
    with pytest.raises(RustError,match='private use'):
        r.resolve('b::v','type',r.root,r.crate.root.body.modules[0].token)
    assert r.resolve('b::v','value',r.root,r.crate.root.body.modules[0].token,_optional_final=True) is None


ALIASED_PROGRAM = (
    'mod producer{ pub mod group {pub fn one()->u64{17} pub fn two()->u64{23}} }'
    'use producer::group::{self as named, one}; '
    'fn named()->u64{2} pub fn answer()->u64{named::two()+one()+named()}'
)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
def test_three_originally_distinct_imports_produce_a_real_native_value(tmp_path, monkeypatch):
    assert run_owned_u64_function(ALIASED_PROGRAM, 'answer', tmp_path, monkeypatch,
                                  experimental_rustc_checked=False) == 42


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE or (None if shutil.which('rustc') else 'development-only rustc unavailable'))
def test_same_group_self_program_returns_42_in_the_development_rustc_reference(tmp_path):
    result = run_rustc_str_function(ALIASED_PROGRAM, 'answer', tmp_path, edition=2024, output='&{call}.to_le_bytes()')
    assert result == (42).to_bytes(8, 'little')


def test_type_only_alias_cannot_shadow_a_macro_of_the_same_name():
    src='mod m{} use m::{self as println}; fn main(){println!("actual std dependency");}'
    parser=Parser(src)
    parser.parse()
    assert [(x.name,x.callee) for x in parser.macro_expansions]==[('println',('std','io','_print'))]
    with pytest.raises(RustError,match=r'owned std::io'):
        compile_rust(src)
    # An ordinary alias still cannot prove a macro binding is absent.
    with pytest.raises(RustError,match='same-name import'):
        Parser(src.replace('m::{self as println}','m as println')).parse()
