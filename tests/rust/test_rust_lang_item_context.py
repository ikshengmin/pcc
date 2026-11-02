"""Canonical compiler-context registration, not core/marker semantic acceptance.

Rules are pinned to rustc1.90 lang-item collection/target/generic requirements.
Stable rustc1.91.1 is used only for genuine rejection of internal source syntax,
not as a silently bootstrapped/nightly core1.90 reference.
"""
from hashlib import sha256
import json
from pathlib import Path

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.crate_context import parse_crate_sources
from pcc.frontends.rust.lang_item_context import inspect_lang_item_context
from pcc.frontends.rust.module_resolution import ModuleResolution
from pcc.frontends.rust.syntax import Parser
from tests.rust.certification_support import actual_check_prerequisite


def inspect(sources, *, name='example', edition=2021):
    if isinstance(sources, str):
        sources = {'src/lib.rs': sources}
    return inspect_lang_item_context('src/lib.rs', sources, crate_name=name, edition=edition)


@pytest.mark.parametrize('lang', ['sized', 'meta_sized', 'pointee_sized', 'copy'])
def test_marker_registration_uses_attribute_and_canonical_item_not_spelling(lang):
    text=f'#![feature(lang_items)] mod marker{{#[lang="{lang}"]pub trait Arbitrary{{}}}}'
    context=inspect(text)
    item=context.crate.root.children[0].items['type','Arbitrary']
    binding=context.language_items[lang]
    assert binding.identity==item.identity==context.require(lang, item.syntax.token)
    assert binding.identity.module.components==('marker',)
    assert binding.identity.module.crate.source_closure_sha256==context.crate.root.identity.crate.source_closure_sha256
    assert context.crate.sources['src/lib.rs'].text==text
    start,end=binding.attribute.source_span
    assert text[start:end]==f'lang="{lang}"'
    assert all(obligation.declared for obligation in context.obligations)
    with pytest.raises(TypeError):
        context.language_items[lang]=binding
    with pytest.raises(RustError,match='unresolved owned compiler context'):
        ModuleResolution(context.crate)
    with pytest.raises(RustError,match='unresolved owned compiler context'):
        context.require_owned_semantics()


def test_plain_trait_names_do_not_register_builtin_identity_or_enable_no_core():
    context=inspect('#![feature(no_core)]#![no_core]trait Sized{} trait Copy{} fn f()->u64{42}')
    assert not context.language_items
    assert context.crate.policy.standard_prelude_path is None
    token=context.crate.root.items['type','Sized'].syntax.token
    with pytest.raises(RustError,match="required canonical language item 'sized' is missing"):
        context.require('sized',token)
    with pytest.raises(RustError,match='no_core requires an owned compiler/core context'):
        ModuleResolution(context.crate)


def test_complete_registration_still_does_not_establish_implicit_copy_or_sized_semantics():
    source='#![feature(no_core,lang_items)]#![no_core]'+''.join(
        f'#[lang="{kind}"]trait Marker{i}{{}}' for i,kind in enumerate(
            ('sized','meta_sized','pointee_sized','copy')))
    context=inspect(source)
    assert len(context.language_items)==4
    assert not context.crate.semantic_context_complete
    assert context.obligations
    with pytest.raises(RustError,match='canonical lang-item registration and semantics are not implemented'):
        context.require_owned_semantics()


def test_source_feature_requests_are_obligations_not_activation():
    context=inspect('#[lang="sized"]trait Arbitrary{}')
    assert context.language_items['sized']
    obligation=next(o for o in context.obligations if o.feature=='lang_items')
    assert not obligation.declared
    assert 'source feature declaration' in obligation.description
    declared=inspect('#![feature(lang_items)]fn f()->u64{42}')
    assert declared.obligations and not declared.language_items
    with pytest.raises(RustError,match='no owned feature activation'):
        ModuleResolution(declared.crate)


def test_outlined_language_items_share_loader_identity_and_duplicate_scope():
    sources={'src/lib.rs':'#![feature(lang_items)]mod marker;',
             'src/marker.rs':'#[lang="copy"]trait Marker{}'}
    context=inspect(sources)
    binding=context.language_items['copy']
    assert binding.identity==context.crate.root.children[0].items['type','Marker'].identity
    assert binding.attribute.token.source_path=='src/marker.rs'
    assert context.obligations[0].declared
    changed=inspect({**sources,'src/marker.rs':sources['src/marker.rs']+'\n'})
    assert binding.identity!=changed.language_items['copy'].identity
    assert binding.identity!=inspect(sources,name='other').language_items['copy'].identity
    assert binding.identity!=inspect(sources,edition=2024).language_items['copy'].identity
    duplicate={**sources,'src/lib.rs':sources['src/lib.rs']+'#[lang="copy"]trait Other{}'}
    with pytest.raises(RustError,match="duplicate language item 'copy'"):
        inspect(duplicate)


@pytest.mark.parametrize('source,message', [
    ('#[lang="copy"]fn f(){}','trait target'),
    ('#[lang="sized"]struct S{}','trait target'),
    ('#[lang="sized"]trait S<T>{}','exactly zero generic'),
    ("#[lang=\"sized\"]trait S<'a>{}",'exactly zero generic'),
    ('#[lang="copy"]#[lang="copy"]trait S{}','multiple lang attributes'),
    ('#[lang="add"]trait S{}','not implemented in this context slice'),
    ('#[lang="made_up"]trait S{}','not implemented in this context slice'),
    ('#[lang=42]trait S{}','literal string'),
    ('#[lang="sized"]trait S:Other{}','unknown type-namespace'),
    ('#![feature(arbitrary_feature)]fn f(){}','not implemented in context inspection'),
    ('#![cfg(any())]fn f(){}','unsupported semantic crate attribute'),
    ('#[derive(Copy)]struct R{}','unsupported Rust attribute'),
    ('mod m{#![feature(lang_items)]trait T{}}','semantic inner module attributes'),
])
def test_context_analysis_rejects_unsupported_shapes_without_discarding_semantics(source,message):
    with pytest.raises(RustError,match=message):
        inspect(source)


@pytest.mark.parametrize('source', [
    '#![no_core]trait Sized{}',
    '#![feature(no_core,lang_items)]#![no_core]#[lang="sized"]trait S{}',
    '#[lang="copy"]trait C{}',
])
def test_inspection_never_changes_normal_public_parser_or_lowering_admission(source,monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    from pcc.frontends.rust.semantics import Checker
    inspect(source)
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('internal source reached IR'))
    monkeypatch.setattr(Checker,'check',lambda *a:pytest.fail('internal source reused normal Sized/Copy assumptions'))
    with pytest.raises(RustError):
        Parser(source).parse()
    with pytest.raises(RustError):
        parse_crate_sources('src/lib.rs',{'src/lib.rs':source},crate_name='example',edition=2021)
    with pytest.raises(RustError):
        compile_rust(source,experimental_rustc_checked=True)


def test_original_core_context_analysis_stops_at_next_unknown_attribute_without_ignoring_it():
    path=Path(__file__).parent/'upstream/rust-1.90.0/library/core/src/lib.rs'
    original=path.read_bytes()
    assert sha256(original).hexdigest()=='c22a6133d900f42b3d447840e84013b26b7ec3757274dfb31b9d514b901edd50'
    # Analysis is not a changed owned-compile frontier: the public path still
    # rejects no_core at75. Unknown coherence semantics at76 are not discarded.
    with pytest.raises(RustError,match=r'library/core/src/lib.rs: 76:4: unsupported semantic crate attribute'):
        inspect_lang_item_context('library/core/src/lib.rs',{'library/core/src/lib.rs':original.decode()},
                                  crate_name='core',edition=2024)
    with pytest.raises(RustError,match='75:4: no_core requires'):
        Parser(original.decode(),edition=2024).parse()
    assert path.read_bytes()==original


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_stable_reference_rejects_internal_attributes_without_bootstrap(tmp_path,monkeypatch):
    from pcc.frontends.rust import RustExternalCheckError,compile_rust_checked
    from pcc.frontends.rust.lowering import Lowerer
    from tests.rust.test_rust_certified_references import toolchain
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('stable rejected input reached IR'))
    tool=toolchain()
    compiler=str((Path(tool.root)/'bin/rustc').resolve())
    for index,(source,diagnostic) in enumerate((
        ('#[lang="sized"]trait Arbitrary{}','E0658'),
        ('#![feature(no_core)]#![no_core]','E0554'),
    )):
        directory=tmp_path/str(index)
        with pytest.raises(RustExternalCheckError):
            compile_rust_checked(source,toolchain=tool,evidence_directory=directory)
        assert (directory/'original.rs').read_text()==source
        processes=json.loads((directory/'processes.json').read_text())
        assert len(processes)==3 and all(p['status']=='COMPLETE' for p in processes)
        assert [p['exit_code'] for p in processes[:2]]==[0,0] and processes[2]['exit_code']>0
        assert processes[0]['command']==[compiler,'-vV']
        assert processes[1]['command']==[compiler,'--print=sysroot']
        assert processes[2]['command']==[compiler,'--edition=2021','--target=x86_64-unknown-linux-gnu',
            '-Coverflow-checks=off','--emit=metadata','--crate-type=lib','--crate-name=certified_input',
            str((directory/'original.rs').resolve()),'-o',str((directory/'checked.rmeta').resolve())]
        assert (directory/'rustc-version.stdout').read_text()==tool.verbose_version
        assert Path((directory/'rustc-sysroot.stdout').read_text().strip()).resolve()==Path(tool.root).resolve()
        for label,process in zip(('rustc-version','rustc-sysroot','check'),processes):
            for stream in ('stdout','stderr'):
                assert sha256((directory/(label+'.'+stream)).read_bytes()).hexdigest()==process[stream+'_sha256']
        assert diagnostic in (directory/'check.stderr').read_text()
        assert not(directory/'checked.rmeta').exists()
