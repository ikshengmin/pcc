"""Source-derived policy and core's exact crate-self alias dependency.

Pinned implementation: Rust1.90 standard_library_imports::inject; extern-crate
scope rules: https://doc.rust-lang.org/reference/items/extern-crates.html.
This is not no_core/staged_api/lang-item semantic acceptance. The real stable
reference consumer uses no internal flags, altered edition, bootstrap or MIR.
"""
from hashlib import sha256
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.crate_context import parse_crate_sources
from pcc.frontends.rust.module_resolution import ModuleResolution, analyze_crate_syntax
from pcc.frontends.rust.syntax import Parser
from tests.rust.certification_support import actual_check_prerequisite


def load(text, *, edition=2021):
    return parse_crate_sources('src/lib.rs', {'src/lib.rs': text}, crate_name='example', edition=edition)


@pytest.mark.parametrize('edition', [2015, 2018, 2021, 2024])
@pytest.mark.parametrize('prefix,library,extern_names', [('', 'std', ('core','std')), ('#![no_std]', 'core', ('core',))])
def test_policy_records_real_edition_prelude_dependencies_without_fabricating_items(edition, prefix, library, extern_names):
    crate = load(prefix+'mod inner{}', edition=edition)
    resolution = ModuleResolution(crate)
    assert crate.policy is resolution.policy
    assert crate.policy.edition == edition
    assert crate.policy.implicit_library_import == library
    assert crate.policy.implicit_extern_prelude == extern_names
    assert crate.policy.standard_prelude_path == (library, 'prelude', 'rust_'+str(edition))
    assert resolution.extern_prelude == {}
    assert set(crate.root.items) == {('type','inner')}


@pytest.mark.parametrize('prefix', ['#![no_core]', '#![no_std]#![no_core]'])
def test_no_core_policy_disables_both_injections_but_never_waives_lang_items(prefix, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    text=prefix+'trait Sized{} trait Copy{} fn f()->u64{42}'
    parser=Parser(text)
    with pytest.raises(RustError, match='canonical lang-item registration and semantics'):
        parser.parse()
    assert parser.crate_policy.implicit_library_import is None
    assert parser.crate_policy.implicit_extern_prelude == ()
    assert parser.crate_policy.standard_prelude_path is None
    assert parser.source == text
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unimplemented no_core reached IR'))
    with pytest.raises(RustError, match='no_core requires an owned compiler/core context'):
        compile_rust(text, experimental_rustc_checked=True)


def test_original_core_root_policy_and_obligations_stay_fail_closed():
    path=Path(__file__).parent/'upstream/rust-1.90.0/library/core/src/lib.rs'
    original=path.read_bytes()
    assert sha256(original).hexdigest()=='c22a6133d900f42b3d447840e84013b26b7ec3757274dfb31b9d514b901edd50'
    parser=Parser(original.decode(), source_path='library/core/src/lib.rs', edition=2024)
    with pytest.raises(RustError, match=r'library/core/src/lib.rs: 75:4: no_core requires'):
        parser.parse()
    assert parser.crate_policy.edition==2024 and parser.crate_policy.no_core is not None
    assert parser.crate_policy.standard_prelude_path is None
    assert parser.crate_stability.feature=='core'
    assert {feature for _,feature in parser.attribute_requirements}=={'staged_api','rustdoc_internals','doc_cfg_hide'}
    assert path.read_bytes()==original


@pytest.mark.parametrize('name', ['core','project','renamed'])
def test_root_crate_alias_joins_extern_prelude_without_changing_nominal_identity(name):
    text=(f'extern crate self as {name}; struct R{{x:u64}} '
          f'mod child{{pub fn id(x:{name}::R)->crate::R{{x}}}} '
          'fn f()->u64{child::id(R{x:42}).x}')
    checker=analyze_crate_syntax(load(text))
    root_type=checker.module_resolution.crate.root.items['type','R'].identity.binding_key
    function=next(f for f in checker.functions if f.token.text=='id')
    assert function.params[0][1]==function.result==root_type
    assert set(checker.module_resolution.extern_prelude)=={name}


def test_local_item_shadows_extern_prelude_but_explicit_crate_path_does_not():
    text=('extern crate self as project; fn value()->u64{40}'
          'mod child{mod project{pub fn value()->u64{900}}'
          'pub fn local()->u64{project::value()} pub fn root()->u64{crate::project::value()}}')
    checker=analyze_crate_syntax(load(text))
    functions={f.export_name:f for f in checker.functions}
    assert functions['child::local'].body.children[-1].binding.item_identity.module.components==('child','project')
    assert functions['child::root'].body.children[-1].binding.item_identity.module.components==()


def test_nested_crate_alias_is_not_promoted_to_root_extern_prelude():
    text='mod a{extern crate self as hidden;} mod b{pub fn f(){hidden::g();}} fn g(){}'
    with pytest.raises(RustError, match='unknown type-namespace item'):
        analyze_crate_syntax(load(text))


def test_extern_prelude_alias_is_not_a_self_module_member():
    text='extern crate self as project; mod a{pub fn f(){self::project::g();}} fn g(){}'
    with pytest.raises(RustError, match='unknown type-namespace item'):
        analyze_crate_syntax(load(text))


def test_crate_alias_only_root_uses_owned_scoped_lowering():
    result=compile_rust('extern crate self as project; fn f()->u64{project::g()} fn g()->u64{42}', experimental_rustc_checked=True)
    assert result.emit_object().startswith(b'\x7fELF')
    assert {'f','g'} <= result.symbols.keys()


@pytest.mark.parametrize('text,message', [
    ('extern crate dependency;', 'external crate dependency loading'),
    ('extern crate self;', "expected 'as'"),
    ('extern crate self as _;', 'anonymous crate aliases'),
    ('extern crate self as a; mod a{}', 'duplicate type-namespace'),
    ('mod a{} fn f(){std::missing();}', 'implicit library/prelude source dependency'),
    ('#![no_std] mod a{} fn f(){core::missing();}', 'implicit library/prelude source dependency'),
    ('#![no_std] mod a{} fn f(){std::missing();}', 'unknown type-namespace'),
    ('#![feature(no_core)]#![no_core]fn f(){}', 'unsupported semantic crate attribute'),
    ('#[lang="sized"]trait Sized{}', 'unsupported'),
])
def test_unsupported_dependencies_and_unowned_context_never_reach_ir(text, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unresolved context reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


def test_self_alias_reference_consumer_cannot_use_a_flag_instead_of_actual_check(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    text=Path(__file__).with_name('programs').joinpath('core_self_alias_consumer.rs').read_text()
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unchecked reference reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64','AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_self_alias_consumer_matches_unchanged_stable_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference, 'PROGRAMS', ('core_self_alias_consumer',))
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch)
