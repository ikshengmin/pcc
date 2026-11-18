"""Whole-statement cfg, not cfg on a required operand or full crate support."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
import shutil
import subprocess

import pytest

from pcc.frontends.rust import CfgOptions, RustError, compile_rust
from pcc.frontends.rust.configuration import OwnedRustConfiguration
from pcc.frontends.rust.syntax import ConfiguredBlock, Parser
from tests.rust.str_execution import HOST_TARGET, UNAVAILABLE, run_owned_u64_function


GNU = 'x86_64-unknown-linux-gnu'
DARWIN = 'arm64-apple-darwin'
YES, NO = '#[cfg(all())]', '#[cfg(any())]'
SOURCE = '''
pub fn answer()->u64{
    let mut n=0u64;
    #[cfg(any(target_arch="x86_64", target_arch="aarch64"))] if true {
        n+=40u64;
        #[cfg(target_endian="little")] matches!(n,40);
    }
    #[cfg(all(target_arch="x86_64",not(target_os="macos")))]
    if n==40u64 { n+=2u64; } else if false { n+=99u64; };
    #[cfg(target_os="macos")] if n==40u64 { n+=3u64; }
    n
}
'''
PROMOTED = 'pub fn answer()->u64{ if true {42u64}else{1u64} ' + NO + ' if missing(){ absent!(); }; }'
EXPLICIT = PROMOTED.replace('1u64} ', '1u64}; ')
EMPTY = PROMOTED[:-1] + ';}'


def parsed(source, target=GNU, **options):
    ctx = OwnedRustConfiguration(target, 2021, CfgOptions(**options))
    parser = Parser(source, source_path='original.rs', configuration=ctx)
    return parser, parser.parse()


@pytest.mark.parametrize('target,second,third', [(GNU,True,False), (DARWIN,False,True),
    ('x86_64-unknown-linux-musl', True,False), ('aarch64-apple-darwin',False,True)])
def test_original_scope_metadata_target_selection_and_owned_object(target, second, third, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('owned phase invoked a tool'))
    source = SOURCE.replace('\n', '\r\n')
    p, (fn,) = parsed(source, target)
    assert isinstance(fn.body, ConfiguredBlock)
    one,two,three = fn.body.conditional_statements
    assert [i.enabled for i in (one,two,three)] == [True,second,third]
    assert all(i.configuration.target_triple == target and i.kind == 'if' for i in (one,two,three))
    assert source[slice(*two.source_span)].endswith('else if false { n+=99u64; };')
    assert source[slice(*two.syntax[0].source_span)] == '#[cfg(all(target_arch="x86_64",not(target_os="macos")))]'
    assert two.syntax[0].token.source_path == 'original.rs' and two.syntax[0].token.line == 8
    live_if = fn.body.children[1].children[0]
    inner, = live_if.children[1].conditional_statements
    assert inner.enabled and inner.kind == 'macro'
    assert source[slice(*inner.source_span)] == '#[cfg(target_endian="little")] matches!(n,40);'
    assert [m.name for m in p.macro_expansions] == ['matches']
    assert deepcopy(fn.body).conditional_statements == fn.body.conditional_statements
    with pytest.raises(FrozenInstanceError):
        one.kind = 'different'
    result = compile_rust(source, cfg=CfgOptions(), target_triple=target)
    assert result.emit_object().startswith(b'\xcf\xfa\xed\xfe' if 'apple' in target else b'\x7fELF')
    assert not result.externally_checked


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
@pytest.mark.parametrize('source,expected', [
    (SOURCE, 43 if HOST_TARGET == DARWIN else 42),
    (PROMOTED, 42),
    ('pub fn answer()->u64{let mut x=39u64; '+YES+' if true{x+=3u64;}else{x+=97u64;} '
     +NO+' if missing(x){x+=100u64;} x}', 42),
    ('pub fn answer()->u64{ '+YES+' if false {0u64} else if true {42u64} else {2u64} }', 42),
    ('pub fn answer()->u64{ let mut x=40u64; '+NO+' absent!{[env!("NO"),(abc)]}; '
     +YES+' matches!(x,40); x+=2u64; x }',42),
    ('pub fn answer()->u64{ '+YES+' if let 11=11u64 {42u64} else {0u64} }',42),
])
def test_live_and_omitted_statements_execute_with_the_right_tail(source, expected, tmp_path, monkeypatch):
    assert run_owned_u64_function(source,'answer',tmp_path,monkeypatch,cfg=CfgOptions()) == expected


@pytest.mark.parametrize('source', [EXPLICIT, EMPTY])
def test_an_explicit_or_surviving_empty_statement_does_not_promote_a_prior_value(source):
    with pytest.raises(RustError):
        compile_rust(source, cfg=CfgOptions())


def test_outer_false_does_not_evaluate_descendant_or_later_cfg_and_never_expands_macros():
    source = ('pub fn f()->u64{ '+NO+' #[cfg(debug_assertions)] if dead_call(){ '
              '#[cfg(panic="abort")] absent!(env!("NO")); '
              'let mut x:Wrap<Wrap<u64>>=dead::call(); x=other![(x)]; } '
              'let z=live(); z } fn live()->u64{42}')
    p = Parser(source, configuration=OwnedRustConfiguration(GNU,2021,CfgOptions()))
    original = tuple(p.tokens)
    f,_ = p.parse()
    meta, = f.body.conditional_statements
    assert meta.decisions == (False,None) and not meta.enabled
    assert p.call_serial == 1 and p.macro_expansions == []
    # An inactive type parser can split a >>, >= or >>= in context, but the
    # original token objects and their original coordinates remain intact.
    assert '>>=' in [t.text for t in p.tokens]
    assert len(original) == len(p.tokens) and all(a is b for a,b in zip(original,p.tokens))
    assert f.body.children[0].children[0].source_call_id == 1
    assert compile_rust(source,cfg=CfgOptions()).ir_text == compile_rust(
        'pub fn f()->u64{let z=live();z} fn live()->u64{42}',cfg=CfgOptions()).ir_text


@pytest.mark.parametrize('term,msg', [
    (NO+' if true {let x=;}', 'unsupported expression'),
    (NO+' if true {bogus!([)];}', 'mismatched closing delimiter'),
    (NO+' if true {bogus!x;}', 'expected a delimited macro input'),
    (NO+' absent![]', "requires its own ';'"),
    (YES+' absent!();', 'macro invocations require'),
    (YES+' env!("MISSING");', 'not defined at compile time'),
    (YES+' if missing(){ }', 'unknown'),
    (YES+' if true { let x:u64=false; }', 'expected'),
    (YES+' if true{} .field;', 'postfix expression'),
    (NO+' let x=1;', 'unsupported local statement attribute target'),
    ('#[cfg(any(unix,debug_assertions))] if true{}', 'explicit CfgOptions.debug_assertions'),
    ('#[cfg(not(unix,windows))] if true{}', 'exactly one'),
    ('#[cfg()] if true{}', 'exactly one'),
    ('#[cfg(all())] #[unknown] if true{}', 'unsupported local statement attribute target'),
])
def test_invalid_grammar_active_errors_and_unsupported_targets_still_fail(term,msg):
    with pytest.raises(RustError,match=msg):
        compile_rust('pub fn f(){'+term+'}',cfg=CfgOptions(),env={})


@pytest.mark.parametrize('checked', [None,True,object()])
def test_legacy_or_absent_configuration_does_not_supply_local_cfg(checked):
    with pytest.raises(RustError,match='explicit OwnedRustConfiguration'):
        Parser('fn f(){'+YES+' if true{}}',rustc_check=checked).parse()


@pytest.mark.parametrize('body', ['let x='+YES+' if true{1}else{2};',
                                  'let x=('+YES+' if true{1}else{2});',
                                  'let x=1+'+YES+' if true{1}else{2};'])
def test_required_expression_positions_remain_outside_the_statement_owner(body):
    with pytest.raises(RustError,match='unsupported expression'):
        compile_rust('pub fn f(){'+body+'}',cfg=CfgOptions())


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_actual_rustc_statement_cfg_tail_removal_and_inactive_macro_frontiers(tmp_path):
    rustc = shutil.which('rustc')
    for n, (source, error) in enumerate([(SOURCE,None), (PROMOTED,None), (EXPLICIT,'E0308'), (EMPTY,'E0308')]):
        original = tmp_path / f'input{n}.rs'
        original.write_text(source)
        run = subprocess.run([rustc,'--edition=2021','--crate-type=lib','--emit=metadata','--error-format=json',
                              str(original),'-o',str(tmp_path / f'input{n}.rmeta')],
                             capture_output=True,text=True,timeout=30)
        assert (run.returncode == 0) is (error is None), run.stderr
        if error:
            assert error in [d['code']['code'] for line in run.stderr.splitlines()
                             if (d:=json.loads(line)).get('level') == 'error' and d.get('code')], run.stderr
