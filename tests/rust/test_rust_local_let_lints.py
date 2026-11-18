"""Lexically scoped let suppressions (Rust Reference: statements/let and attributes/diagnostics).

The public owned compiler has no command-line lint levels, and does not
implement a general unused_variables warning pass. Local allow is a
suppression, not a source-certification token or a waiver of other checks.
"""
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import AttributedLet, Parser
from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function


ALLOW = '#[allow(unused_variables)]'
SCOPED = '''pub fn answer() -> u64 {
    let visible_sibling = 23u64;
    #[allow(unused_variables)]
    let hidden_owner = {
        let hidden_child = 1u64;
        2u64
    };
    42u64
}
'''


def test_attached_to_one_exact_declaration_not_crate_block_or_following_let():
    source = ('pub fn answer()->u64{\r\n'
              '    let before=1u64;\r\n'
              '    #[allow(unused_variables, reason="λ")] /* gap */\r\n'
              '    #[allow(unused_variables,)] let (mut x,_)=({\r\n'
              '        #[allow(unused_variables)] let _=before; 40u64\r\n'
              '    },0u64);\r\n'
              '    let after=2u64; x+after\r\n}')
    parser = Parser(source, source_path='original.rs', edition=2024)
    fn, = parser.parse()
    before, target, after, _ = fn.body.children
    assert not isinstance(before, AttributedLet) and not isinstance(after, AttributedLet)
    assert not parser.crate_lint_attributes
    assert isinstance(target, AttributedLet) and target.kind == 'pattern_let'
    assert source[slice(*target.source_span)].startswith('#[allow(')
    assert source[slice(*target.source_span)].endswith('},0u64);')
    one, two = target.lint_attributes
    assert one.token.source_path == 'original.rs' and (one.token.line, one.token.column) == (3, 7)
    assert one.reason.value == 'λ' and two.reason is None
    assert source[slice(*one.source_span)] == '#[allow(unused_variables, reason="λ")]'
    assert source[slice(*one.lint.source_span)] == 'unused_variables'
    assert source[slice(*one.reason.source_span)] == 'reason="λ"'
    assert source[slice(*two.source_span)] == '#[allow(unused_variables,)]'
    nested = target.children[0].children[0].children[0]
    assert isinstance(nested, AttributedLet) and nested.kind == 'wildcard_let'
    assert source[slice(*nested.source_span)] == ALLOW + ' let _=before;'
    assert deepcopy(target).lint_attributes == target.lint_attributes
    with pytest.raises(FrozenInstanceError):
        one.lint = None


@pytest.mark.parametrize('edition', [2015, 2018, 2021, 2024])
def test_lint_is_not_a_codegen_effect_or_checked_source(edition):
    tagged = compile_rust(SCOPED, edition=edition)
    plain = compile_rust(SCOPED.replace(ALLOW, ''), edition=edition)
    assert tagged.ir_text == plain.ir_text and tagged.emit_object() == plain.emit_object()
    assert not tagged.externally_checked and tagged.external_rustc_lints is None


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
@pytest.mark.parametrize('body', [
    'let mut n=40u64; @ let ignored={n+=2u64;9u64}; n',
    'let x=39u64; @ let (mut x,_)=({x+2u64},0u64); x+=1u64; x',
    'let mut n=0u64; @ let _={n+=42u64;n}; n',
    '@ let b=matches!(42u64,42); if b{42u64}else{0u64}',
    'let n=40u64; { @ let n=3u64; n; }; n+2u64',
    'let mut n=39u64; @ let _={ @ let _={n+=3u64;n}; n }; n',
])
def test_initializer_scope_shadowing_and_effects_execute_once(body, tmp_path, monkeypatch):
    source = 'pub fn answer()->u64{' + body.replace('@', ALLOW) + '}'
    assert run_owned_u64_function(source, 'answer', tmp_path, monkeypatch,
                                  edition=2024, experimental_rustc_checked=False) == 42


@pytest.mark.parametrize('body,message', [
    ('#[unknown] let n=1;', 'unsupported local statement attribute'),
    ('#[deny(unused_variables)] let n=1;', 'unsupported local statement attribute'),
    ('#[warn(unused_variables)] let n=1;', 'unsupported local statement attribute'),
    ('#[forbid(unused_variables)] let n=1;', 'unsupported local statement attribute'),
    ('#[expect(unused_variables)] let n=1;', 'unsupported local statement attribute'),
    ('#[cfg(all())] let n=1;', 'unsupported local statement attribute'),
    ('#![allow(unused_variables)] let n=1;', 'inner attributes'),
    ('#[allow()] let n=1;', 'only the unused_variables'),
    ('#[allow(does_not_exist)] let n=1;', 'only the unused_variables'),
    ('#[allow(unused)] let n=1;', 'only the unused_variables'),
    ('#[allow(clippy::unused_variables)] let n=1;', 'only the unused_variables'),
    ('#[allow(unused_variables())] let n=1;', 'only the unused_variables'),
    ('#[allow(unused_variables="x")] let n=1;', 'only the unused_variables'),
    ('#[allow(unused_variables, unknown)] let n=1;', 'optional literal reason'),
    ('#[allow(unused_variables, reason="x", unknown)] let n=1;', 'optional literal reason'),
    ('#[allow(unused_variables, reason)] let n=1;', 'optional literal reason'),
    ('#[allow(unused_variables, reason=include_str!("a"))] let n=1;', 'literal string'),
    (ALLOW + ' #[unknown] let n=1;', 'unsupported local statement attribute'),
    (ALLOW + '; let n=1;', 'immediately to a let'),
    (ALLOW + ' if true {}', 'immediately to a let'),
    (ALLOW, 'immediately to a let'),
    (ALLOW + ' let [x]=[1];', 'array destructuring'),
    (ALLOW + ' let x:i32=true;', 'type mismatch'),
    (ALLOW + ' let x=missing;', 'unknown local'),
    (ALLOW + ' let x=1; x=2;', 'immutable'),
    (ALLOW + ' let x=2147483647i32+1i32;', 'constant integer overflow'),
    (ALLOW + ' let z={let x=1; x}; x;', 'unknown local'),
])
def test_other_rules_targets_payloads_and_semantic_errors_are_not_waived(body, message):
    with pytest.raises(RustError, match=message):
        compile_rust('fn f(){'+body+'}', experimental_rustc_checked=True)


@pytest.mark.parametrize('source', [
    '#![forbid(unused_variables)] fn f(){'+ALLOW+' let n=1;}',
    '#[forbid(unused_variables)] fn f(){'+ALLOW+' let n=1;}',
    'mod nested {#![forbid(unused_variables)] fn f(){'+ALLOW+' let n=1;}}',
    'fn f(){#[forbid(unused_variables)] let a={'+ALLOW+' let n=1;0};}',
])
def test_enclosing_lint_levels_are_not_discarded(source):
    with pytest.raises(RustError):
        compile_rust(source, experimental_rustc_checked=True)


from tests.rust.certification_support import actual_check_prerequisite
from tests.rust.test_rust_certified_references import toolchain


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_reference_rustc_keeps_the_unannotated_sibling_warning(tmp_path):
    # Independent reference for this small Rust program; orjson is not built
    # with a below-MSRV compiler. Do not replace real diagnostics with an oracle flag.
    from pcc.frontends.rust import check_rust_source
    from hashlib import sha256
    target = 'x86_64-unknown-linux-gnu'
    for label, source in (('local', SCOPED), ('plain', SCOPED.replace(ALLOW, ''))):
        directory = tmp_path / label
        check = check_rust_source(source, toolchain=toolchain(), evidence_directory=directory,
                                  target_triple=target, edition=2024)
        assert (directory / 'original.rs').read_bytes() == source.encode()
        assert sha256(source.encode()).hexdigest() == check.provenance.source_sha256
        assert check.stderr == (directory / 'check.stderr').read_bytes()
        assert b'unused variable: `visible_sibling`' in check.stderr
        for name in (b'hidden_owner', b'hidden_child'):
            assert (b'unused variable: `'+name+b'`' in check.stderr) == (label == 'plain')
