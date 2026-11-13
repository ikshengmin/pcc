"""The actual pinned core crate root motivates documentation attachment.

Rust Reference, retrieved 2026-11-01: comments.html, attributes.html,
tokens.html#string-literals and expressions/literal-expr.html#string-continuation-escapes.
This is grammar/AST evidence, not core, std, String or upstream-test execution.
"""
import hashlib
from pathlib import Path
import pytest
from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser


def test_unchanged_core_crate_root_retains_docs_before_compiler_context_frontier():
    path = Path(__file__).parent/'upstream/rust-1.90.0/library/core/src/lib.rs'
    original = path.read_bytes()
    assert hashlib.sha256(original).hexdigest() == 'c22a6133d900f42b3d447840e84013b26b7ec3757274dfb31b9d514b901edd50'
    parser = Parser(original.decode())
    with pytest.raises(RustError, match="no_core requires an owned compiler/core context"):
        parser.parse()
    assert parser.crate_documentation[0].text == ' # The Rust Core Library'
    assert all(a.inner for a in parser.crate_documentation)
    for a in parser.crate_documentation:
        start, end = a.source_span
        assert original.decode()[start:end].startswith('//!' if a.syntax == 'line' else '#![doc')
    assert path.read_bytes() == original


def test_crate_item_and_member_documents_are_attached_not_discarded():
    text = ('//! crate\n#![no_std]\n#![doc="extra"]\n'
            '/// record\nstruct R{/// field\nx:u64}\n'
            '/** enum */enum E{/// variant\nV(#[doc="payload"] u64)}\n'
            '/// trait\ntrait T{/// required\nfn n(self)->u64;}\n'
            '/// implementation\nimpl T for R{/*! impl inner */'
            '/// provided\nfn n(self)->u64{//! function inner\nself.x}}\n')
    parser = Parser(text); functions = parser.parse()
    assert [a.text for a in parser.crate_documentation] == [' crate', 'extra']
    record, enum, trait, implementation = parser.records[0], parser.enums[0], parser.traits[0], parser.implementations[0]
    assert record.documentation[0].text == ' record'
    assert record.field_documentation[0][0].text == ' field'
    assert enum.documentation[0].text == ' enum '
    assert enum.variant_documentation[0][0].text == ' variant'
    assert enum.payload_documentation['V'][0][0].text == 'payload'
    assert trait.methods[0].documentation[0].text == ' required'
    assert [a.text for a in implementation.documentation] == [' implementation', ' impl inner ']
    assert [a.inner for a in functions[0].documentation] == [False, True]
    assert [a.text for a in functions[0].documentation] == [' provided', ' function inner']


@pytest.mark.parametrize('prefix,value,style', [
    ('/// hello\n', ' hello', 'line'),
    ('/** nested /* retained */ text */', ' nested /* retained */ text ', 'block'),
    ('#[doc="hello"]', 'hello', 'attribute'),
    ('#[doc=r##"a"#b\\n"##]', 'a"#b\\n', 'attribute'),
    ('#[doc="a\\n\\x41\\u{1F_980}\\\"\\\\"]', 'a\nA🦀"\\', 'attribute'),
    ('#[doc="a\\\n  \t b"]', 'ab', 'attribute'),
])
def test_doc_payloads_preserve_value_and_exact_source_span(prefix, value, style):
    source = prefix + 'fn f(){}'
    fn = Parser(source).parse()[0]; doc = fn.documentation[0]
    assert (doc.text,doc.syntax,doc.inner) == (value,style,False)
    start,end=doc.source_span
    assert start == 0 and source[start:end] == prefix.rstrip('\n')
    assert (doc.token.line,doc.token.column) == (1,1)


def test_crlf_normalization_does_not_destroy_source_offsets():
    source = '//! one\r\n/*!two\r\nthree*/\r\n/// café\r\nfn f(){}'
    parser=Parser(source);fn=parser.parse()[0]
    assert [a.text for a in parser.crate_documentation] == [' one','two\nthree']
    doc=fn.documentation[0]
    assert source[slice(*doc.source_span)] == '/// café\r'
    assert (doc.token.line,doc.token.column)==(4,1)


def test_ordinary_comment_prefixes_do_not_create_documentation():
    parser=Parser('//// ordinary\n/**/ /***/ /**** ordinary */fn f(){}')
    fn=parser.parse()[0]
    assert not fn.documentation and not parser.crate_documentation


@pytest.mark.parametrize('source,message', [
    ('/// dangling','no following item'),
    ('/// field\n//! wrong\nfn f(){}','inner documentation'),
    ('fn f(){}\n//! late','inner documentation'),
    ('struct R{//! wrong\nx:u64}','inner documentation'),
    ('trait T{//! wrong\nfn f(self);}','inner documentation'),
    ('/// bare\rcarriage\nfn f(){}','bare carriage'),
    ('/** bare\rcarriage */fn f(){}','bare carriage'),
    ('#[doc="bad\\xFF"]fn f(){}','ASCII'),
    ('#[doc="bad\\u{D800}"]fn f(){}','scalar value'),
    ('#[doc="bad\\u{_41}"]fn f(){}','Unicode'),
    ('#[doc="bad\\q"]fn f(){}','string escape'),
    ('#[doc=r##"unterminated"#]fn f(){}','unterminated raw'),
    ('#[doc=include_str!("x.md")]fn f(){}','literal string'),
    ('#[doc(unknown)]fn f(){}','unsupported doc option'),
    ('fn f(){ {/// }\n} }','documentation attributes'),
    ('#["doc"="text"]fn f(){}','unsupported Rust attribute'),
    ('#!["no_std"]fn f(){}','unsupported semantic crate attribute'),
])
def test_invalid_or_explicitly_unsupported_doc_forms_do_not_reach_ir(source,message,monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('unsupported documentation reached IR'))
    with pytest.raises(RustError,match=message):
        compile_rust(source,experimental_rustc_checked=True)


@pytest.mark.parametrize('attribute,message', [('stable(feature="x",since="1.0.0")','staged_api'),('feature(no_core)','unsupported semantic crate attribute'),('no_core','no_core requires'),('cfg(any())','unsupported semantic crate attribute'),('lang="Option"','unsupported semantic crate attribute'),('derive(Copy)','unsupported semantic crate attribute')])
def test_semantic_crate_attributes_are_never_blanket_ignored(attribute,message,monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('semantic attribute ignored'))
    with pytest.raises(RustError,match=message):
        compile_rust('//! documentation\n#!['+attribute+']\nfn f(){}',experimental_rustc_checked=True)


@pytest.mark.parametrize('truncated',['#','#!','#[','#!['])
def test_truncated_attribute_syntax_has_a_located_error_not_index_error(truncated):
    with pytest.raises(RustError):
        Parser(truncated).parse()


def test_doc_payload_reference_text_does_not_authorize_or_invent_references():
    from pcc.frontends.rust.reference_admission import contains_references
    parser=Parser('/// &mut T is only prose\nfn f(){}');functions=parser.parse()
    assert not contains_references(functions)
    parser=Parser('/// prose\nfn f(x:&u64)->u64{*x}');functions=parser.parse()
    assert contains_references(functions)
    with pytest.raises(RustError,match='actual source/options-bound'):
        compile_rust(parser.source,experimental_rustc_checked=True)


def test_literal_payloads_cannot_impersonate_syntax_tokens():
    for source in ('"fn" f(){}', 'fn f()->"u64"{1}', '#[doc="text" "]" fn f(){}'):
        with pytest.raises(RustError):
            Parser(source).parse()
    parser=Parser('#[doc="}"]extern "C"{fn f();}')
    with pytest.raises(RustError,match='attributes and visibility on extern blocks'):
        parser.parse()
    function=Parser('extern r#"C"# {fn f();}').parse()[0]
    assert function.abi == 'C'


@pytest.mark.parametrize('source', [
    'extern "C"fn f(){}',
    'extern r#"C"#fn f(){}',
    'extern "\\x43"fn f(){}',
    '#[doc="value"suffix]fn f(){}',
    '#[doc=r"value"_suffix]fn f(){}',
])
def test_string_suffixes_are_not_split_into_following_grammar(source,monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('suffixed string reached IR'))
    with pytest.raises(RustError,match='string literal suffixes'):
        compile_rust(source,experimental_rustc_checked=True)


def test_interleaved_crate_documentation_keeps_order_without_repeated_freezing():
    source=''.join(f'//! {i}\n#![no_std]\n' for i in range(128))+'fn f(){}'
    parser=Parser(source);parser.parse()
    assert [a.text for a in parser.crate_documentation] == [f' {i}' for i in range(128)]
