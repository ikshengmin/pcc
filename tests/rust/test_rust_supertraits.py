"""Real supertrait obligations needed by the pinned core marker hierarchy.

https://doc.rust-lang.org/reference/items/traits.html#supertraits
Normal concrete impl checking and canonical context collection share one graph.
No builtin Sized/Copy semantics, generic solver, no_core or core build is claimed.
"""
from pathlib import Path
from hashlib import sha256
import json
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.lang_item_context import inspect_lang_item_context
from pcc.frontends.rust.module_resolution import ModuleResolution
from tests.rust.certification_support import actual_check_prerequisite

PROGRAMS=('supertrait_concrete_hierarchy_grouped','supertrait_shared_mutable')


def source(name):
    return Path(__file__).with_name('programs').joinpath(name+'.rs').read_text()


def check(text):
    parser=Parser(text);functions=parser.parse()
    checker=Checker(functions,parser.records,parser.enums,parser.implementations,parser.traits)
    checker.check()
    return checker


def test_original_bare_if_struct_source_and_distinct_grouped_positive_are_preserved():
    original=source('supertrait_concrete_hierarchy')
    assert sha256(original.encode()).hexdigest()=='577256cce9f502752efab686b899bc920ba7dc6f41d02fa96a610251ba8cd5f3'
    assert source(PROGRAMS[0])==original.replace('&& R::<u64>{x:40}.','&& (R::<u64>{x:40}).')


def test_transitive_diamond_and_forward_impls_use_existing_concrete_registry():
    checker=check(source(PROGRAMS[0]))
    hierarchy=checker.traits.hierarchy
    assert hierarchy.ancestors['Leaf']==frozenset({'Left','Right','Root'})
    assert hierarchy.ancestors['Root']==frozenset()
    assert set(trait for trait,owner in checker.traits.impls)=={'Root','Left','Right','Leaf'}
    assert len(checker.traits.impls)==4
    assert 'value' not in checker.traits.traits['Leaf']


def test_ordinary_trait_name_copy_does_not_create_builtin_copy():
    text='trait Clone{}trait Copy:Clone{} struct R{x:u64}impl Clone for R{}impl Copy for R{}'
    checker=check(text)
    assert checker.traits.hierarchy.ancestors['Copy']==frozenset({'Clone'})
    assert checker.copy_type('R') is False


def test_repeated_parent_bounds_are_not_duplicate_impls():
    checker=check('trait A{}trait B:A+A+{}struct R{x:u64}impl B for R{}impl A for R{}')
    assert checker.traits.hierarchy.direct['B']==('A',)


@pytest.mark.parametrize('target,magic',[('x86_64-unknown-linux-gnu',b'\x7fELF'),
                                       ('arm64-apple-darwin',b'\xcf\xfa\xed\xfe')])
def test_value_original_emits_real_target_object(target,magic):
    result=compile_rust(source(PROGRAMS[0]),experimental_rustc_checked=True,target_triple=target)
    assert result.emit_object().startswith(magic)


INVALID=(
    ('direct','trait A{}trait B:A{}struct R{x:u64}impl B for R{}','missing required supertrait','E0277'),
    ('transitive','trait A{}trait B:A{}trait C:B{}struct R{x:u64}impl B for R{}impl C for R{}','missing required supertrait','E0277'),
    ('other-owner','trait A{}trait B:A{}struct R<T>{x:T}impl A for R<u64>{}impl B for R<bool>{}','missing required supertrait','E0277'),
    ('cycle','trait A:B{}trait B:A{}','cycle in supertrait','E0391'),
    ('self-cycle','trait A:A{}','cycle in supertrait','E0391'),
    ('unknown','trait A:Missing{}','supertraits require declared','E0405'),
    ('nontrait','struct R{x:u64}trait A:R{}','supertraits require declared','E0404'),
    ('no-member-inheritance','trait A{fn n(self)->u64;}trait B:A{}struct R{x:u64}impl A for R{fn n(self)->u64{self.x}}impl B for R{}fn f()->u64{<R as B>::n(R{x:42})}','unknown trait associated','E0576'),
    ('local-copy-not-builtin','trait Clone{}trait Copy:Clone{}struct R{x:u64}impl Clone for R{}impl Copy for R{}fn f(){let r=R{x:42};let a=r;let b=r;}','moved','E0382'),
    # Original failed positive is preserved exactly. The public parser must not
    # widen Rust's brace-restricted if-head grammar to make it pass. The actual
    # rustc lane below must confirm this intended negative before qualification.
    ('brace-condition',source('supertrait_concrete_hierarchy'),
     r"13:22: expected ';' or end of block",'struct literals are not allowed here'),
    ('same-name-not-override','trait A{fn n(self)->u64;}trait B:A{fn n(self)->u64;}struct R{x:u64}impl A for R{fn n(self)->u64{40}}impl B for R{fn n(self)->u64{42}}fn f()->u64{R{x:1}.n()}','ambiguous','E0034'),
)


@pytest.mark.parametrize('name,text,message,code',INVALID)
def test_invalid_unused_obligations_fail_before_ir(name,text,message,code,monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('invalid supertrait reached IR'))
    with pytest.raises(RustError,match=message):
        compile_rust(text,experimental_rustc_checked=True)


def test_shared_mutable_supertrait_original_requires_actual_check(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('unchecked references reached IR'))
    with pytest.raises(RustError,match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(PROGRAMS[1]),experimental_rustc_checked=True)


def test_context_hierarchy_uses_canonical_module_paths_not_marker_names():
    sources={'src/lib.rs':'#![feature(no_core,lang_items)]#![no_core]mod marker;',
      'src/marker.rs':('mod lower{#[lang="pointee_sized"]pub trait P{}}'
         '#[lang="meta_sized"]pub trait M:self::lower::P{}'
         '#[lang="sized"]pub trait S:M{}')}
    context=inspect_lang_item_context('src/lib.rs',sources,crate_name='example',edition=2024)
    sized=context.language_items['sized'].identity
    meta=context.language_items['meta_sized'].identity
    pointee=context.language_items['pointee_sized'].identity
    assert context.trait_hierarchy.ancestors[sized]==frozenset({meta,pointee})
    assert pointee.module.components==('marker','lower')
    assert len({sized,meta,pointee})==3
    with pytest.raises(RustError,match='no_core requires'):
        ModuleResolution(context.crate)
    with pytest.raises(RustError,match='no_core requires'):
        context.require_owned_semantics()


@pytest.mark.parametrize('text,message',[
    ('mod a{trait A{}}mod b{trait B:crate::a::A{}}','private item'),
    ('struct A{x:u64}trait B:A{}','does not name a declared trait'),
    ('trait A:B{}trait B:A{}','cycle in supertrait'),
    ('mod a{pub trait A{}}mod b{trait B:A{}}','unknown type-namespace'),
])
def test_context_supertraits_preserve_privacy_target_and_lexical_scope(text,message):
    with pytest.raises(RustError,match=message):
        inspect_lang_item_context('lib.rs',{'lib.rs':text},crate_name='example',edition=2021)


@pytest.mark.parametrize('text,message',[
    ('trait A{}trait B:for<\'a>A{}','higher-ranked'),
    ('trait B:?Sized{}','relaxed'),
    ("trait B:'static{}",'lifetime'),
    ('trait A{}trait B:A<u64>{}','generic supertraits'),
    ('trait B where Self:Copy{}','where constraints'),
    ('trait B:Copy{}','external/builtin trait loading'),
])
def test_remaining_supertrait_and_builtin_dependencies_are_explicit(text,message):
    with pytest.raises(RustError,match=message):
        compile_rust(text,experimental_rustc_checked=True)


@pytest.mark.parametrize('reverse',[False,True])
def test_deep_supertrait_dependencies_fail_with_bounded_owned_diagnostic(reverse):
    declarations=['trait T0{}']+[f'trait T{i}:T{i-1}{{}}' for i in range(1,130)]
    text=''.join(reversed(declarations) if reverse else declarations)
    with pytest.raises(RustError,match='supertrait dependency depth'):
        compile_rust(text,experimental_rustc_checked=True)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64','AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_originals_match_unchanged_rustc(tmp_path,monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference,'PROGRAMS',PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path,monkeypatch)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path,monkeypatch):
    from pcc.frontends.rust import RustExternalCheckError,compile_rust_checked
    from pcc.frontends.rust.lowering import Lowerer
    from tests.rust.test_rust_certified_references import toolchain
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('invalid source reached IR'))
    tool=toolchain();compiler=str((Path(tool.root)/'bin/rustc').resolve())
    for name,text,message,code in INVALID:
        directory=tmp_path/name
        with pytest.raises(RustExternalCheckError):
            compile_rust_checked(text,toolchain=tool,evidence_directory=directory)
        assert (directory/'original.rs').read_text()==text
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
        assert code in (directory/'check.stderr').read_text()
        assert not(directory/'checked.rmeta').exists()
