"""Pinned core-root metadata is retained, not a staged_api/no_core bypass.

Semantic sources are rust-lang/rust 1159e78c4747b02ef996e55082b704c09b970588:
compiler/rustc_attr_parsing/src/attributes/stability.rs and util.rs,
compiler/rustc_passes/src/check_attr.rs, rustc_ast_passes/src/feature_gate.rs.
This suite is not execution of core or doctests; actual checker controls retain
complete distinct inputs and do not install or enable a bootstrapping context.
"""
from pathlib import Path
import hashlib
import json
import pytest
from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from tests.rust.certification_support import actual_check_prerequisite


def compile_source(source):
    return compile_rust(source, experimental_rustc_checked=True)


def test_original_root_retains_stability_and_doc_options_before_no_core_frontier():
    path=Path(__file__).parent/'upstream/rust-1.90.0/library/core/src/lib.rs'
    original=path.read_bytes()
    assert hashlib.sha256(original).hexdigest()=='c22a6133d900f42b3d447840e84013b26b7ec3757274dfb31b9d514b901edd50'
    parser=Parser(original.decode())
    with pytest.raises(RustError,match='no_core requires an owned compiler/core context'):
        parser.parse()
    stable=parser.crate_stability
    assert (stable.feature,stable.since,stable.version)==('core','1.6.0',(1,6,0))
    assert original.decode()[slice(*stable.source_span)]=='#![stable(feature = "core", since = "1.6.0")]'
    options=[o for doc in parser.crate_documentation for o in doc.options]
    assert [o.name for o in options]==['html_playground_url','issue_tracker_base_url','test','test','rust_logo','cfg_hide']
    assert options[0].value=='https://play.rust-lang.org/'
    test=options[2]
    assert test.children[0].name=='no_crate_inject'
    assert test.children[1].children[0].name=='deny'
    assert test.children[1].children[0].children[0].name=='warnings'
    assert options[-1].children[0].name=='no_fp_fmt_parse'
    assert [requirement for _,requirement in parser.attribute_requirements]==['staged_api','rustdoc_internals','doc_cfg_hide']
    assert path.read_bytes()==original


@pytest.mark.parametrize('since,version', [('1.6.0',(1,6,0)),('1.6',(1,6,0)),('+1.06.+0',(1,6,0)),('CURRENT_RUSTC_VERSION',None)])
def test_stable_metadata_is_validated_and_retained_but_does_not_enable_staging(since,version):
    parser=Parser('#![stable(since="'+since+'",feature="example")]fn f(){}')
    with pytest.raises(RustError,match='staged_api compiler context'):
        parser.parse()
    assert (parser.crate_stability.feature,parser.crate_stability.version)==('example',version)


@pytest.mark.parametrize('metadata,message', [
    ('feature="x"','both feature and since'),
    ('since="1.0.0"','both feature and since'),
    ('feature="not an ident",since="1.0.0"','identifier'),
    ('feature="x",since="1.0.0",feature="y"','duplicate'),
    ('feature="x",since="1.0.0",other="x"','only feature and since'),
    ('feature(x),since="1.0.0"','literal strings'),
    ('feature="x",since="1.0.0-nightly"','Rust version'),
    ('feature="x",since="1"','Rust version'),
    ('feature="x",since="1.0.0.1"','Rust version'),
    ('feature="x",since="1.65536.0"','u16'),
    ('feature="x",since="1.'+'9'*5000+'.0"','u16'),
])
def test_malformed_stability_metadata_is_not_silently_retained(metadata,message):
    with pytest.raises(RustError,match=message):
        Parser('#![stable('+metadata+')]fn f(){}').parse()


def test_duplicate_stability_levels_rejected_before_context_validation():
    with pytest.raises(RustError,match='multiple stability levels'):
        Parser('#![stable(feature="x",since="1.0.0")]#![stable(feature="x",since="1.0.0")]fn f(){}').parse()


@pytest.mark.parametrize('source,message', [
    ('#![stable(feature="x",since="1.0.0")]fn f(){}','staged_api'),
    ('#![stable(feature="x",since="1.0.0")]#![feature(staged_api)]fn f(){}','unsupported semantic crate attribute'),
    ('#![doc(rust_logo)]fn f(){}','rustdoc_internals'),
    ('#![doc(cfg_hide(target_pointer_width="64"))]fn f(){}','doc_cfg_hide'),
    ('#![no_core]fn f(){}','no_core requires'),
    ('#![lang="x"]fn f(){}','unsupported semantic crate attribute'),
    ('#![cfg(any())]fn f(){}','unsupported semantic crate attribute'),
])
def test_metadata_cannot_authorize_unimplemented_semantic_context(source,message,monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('unimplemented attribute context reached IR'))
    with pytest.raises(RustError,match=message):
        compile_source(source)


def test_normal_doc_options_are_retained_without_applying_them_to_program_semantics():
    source='#![doc(html_playground_url="https://example.invalid/",test(no_crate_inject,attr(deny(warnings))))]#[doc(hidden)]fn f(){}'
    parser=Parser(source);functions=parser.parse()
    assert functions[0].documentation[0].options[0].name=='hidden'
    assert parser.crate_documentation[0].options[1].children[1].children[0].name=='deny'
    assert not parser.attribute_requirements
    assert 'define' in compile_source(source).ir_text


@pytest.mark.parametrize('source,message', [
    ('#[doc(html_playground_url="x")]fn f(){}','crate placement'),
    ('#![doc(html_playground_url)]fn f(){}','literal string'),
    ('#![doc(hidden="x")]fn f(){}','word option'),
    ('#![doc(test= "x")]fn f(){}','requires a list'),
    ('#[doc(test(no_crate_inject))]fn f(){}','crate placement'),
    ('#![doc(test(attr(feature(x))))]fn f(){}','doctest semantic context'),
    ('#![doc(test(unknown))]fn f(){}','unsupported doc'),
    ('#![doc(unknown)]fn f(){}','unsupported doc'),
])
def test_unsupported_doc_metadata_placement_and_shape_fail_explicitly(source,message):
    with pytest.raises(RustError,match=message):
        Parser(source).parse()


def test_attribute_metadata_depth_has_an_explicit_bound():
    with pytest.raises(RustError,match='nesting exceeds'):
        Parser('#![doc('+ 'test('*33 + 'hidden' + ')'*33 +')]fn f(){}').parse()


def test_structured_doc_prose_does_not_bypass_real_reference_admission():
    with pytest.raises(RustError,match='actual source/options-bound'):
        compile_source('#[doc(hidden)]fn f(x:&u64)->u64{*x}')


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_ordinary_stability_and_literal_suffix_controls(tmp_path,monkeypatch):
    from pcc.frontends.rust import compile_rust_checked, RustExternalCheckError
    from pcc.frontends.rust.lowering import Lowerer
    from tests.rust.test_rust_certified_references import toolchain
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('invalid attribute source reached owned IR'))
    originals=('#![stable(feature="example",since="1.0.0")]fn f(){}',
               'extern "C"fn f(){}', 'extern r#"C"#fn f(){}', 'extern "\\x43"fn f(){}')
    tool = toolchain()
    compiler = str((Path(tool.root)/'bin/rustc').resolve())
    for i,source in enumerate(originals):
        directory=tmp_path/str(i)
        with pytest.raises(RustExternalCheckError):
            compile_rust_checked(source,toolchain=tool,evidence_directory=directory)
        assert (directory/'original.rs').read_text()==source
        assert not (directory/'checked.rmeta').exists()
        processes=json.loads((directory/'processes.json').read_text())
        assert len(processes)==3
        assert processes[0]['command']==[compiler,'-vV']
        assert processes[1]['command']==[compiler,'--print=sysroot']
        assert processes[2]['command']==[compiler,'--edition=2021','--target=x86_64-unknown-linux-gnu',
            '-Coverflow-checks=off','--emit=metadata','--crate-type=lib','--crate-name=certified_input',
            str((directory/'original.rs').resolve()),'-o',str((directory/'checked.rmeta').resolve())]
        assert all(p['status']=='COMPLETE' for p in processes)
        assert [p['exit_code'] for p in processes[:2]]==[0,0]
        assert processes[2]['exit_code']>0  # Signals/timeouts are not source rejection.
        assert (directory/'rustc-version.stdout').read_text()==tool.verbose_version
        assert Path((directory/'rustc-sysroot.stdout').read_text().strip()).resolve()==Path(tool.root).resolve()
        for label,process in zip(('rustc-version','rustc-sysroot','check'),processes):
            for stream in ('stdout','stderr'):
                assert hashlib.sha256((directory/(label+'.'+stream)).read_bytes()).hexdigest()==process[stream+'_sha256']
        diagnostic=(directory/'check.stderr').read_text()
        assert ('stability attributes' if i==0 else 'suffix') in diagnostic
        assert 'original.rs' in diagnostic


def test_original_hidden_option_input_is_now_retained_as_documentation():
    source='#[doc(hidden)]fn f(){}'
    parser=Parser(source);function=parser.parse()[0]
    assert function.documentation[0].options[0].name=='hidden'
    assert parser.source==source
    assert 'define' in compile_source(source).ir_text
