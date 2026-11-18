"""Original Rust std print macro source plans, not a proof of std I/O at run time."""
import pytest

from pcc.frontends.rust import CfgOptions, RustError, compile_rust
from pcc.frontends.rust.configuration import OwnedRustConfiguration
from pcc.frontends.rust.print_macros import StdOutputExpansion
from pcc.frontends.rust.syntax import Parser

TARGET = 'x86_64-unknown-linux-gnu'


@pytest.mark.parametrize('call,callee,pieces,captures', [
    ('println!()', '_print', ('\n',), ()),
    ('eprintln![]', '_eprint', ('\n',), ()),
    ('println!{"",}', '_print', ('\n',), ()),
    (r'println!("\u{e9}\0{{}}{{\n}} 😀")', '_print', ('é\0{}{\n} 😀\n',), ()),
    ('eprintln![r#"{{a}} {who}: {what}{who}"#,]', '_eprint', ('{a} ', ': ', '', '\n'), ('who','what','who')),
    ('println!("already LF\\n")', '_print', ('already LF\n\n',), ()),
])
def test_output_plan_is_source_bound_ordered_and_not_a_python_or_c_write(call, callee, pieces, captures):
    s = '// 😀\r\nfn f(){' + call + ';}'
    p = Parser(s, source_path='original.rs')
    fn, = p.parse()
    e, = p.macro_expansions
    assert isinstance(e,StdOutputExpansion)
    assert fn.body.children[0].children[0].value is e
    assert e.callee == ('std','io',callee)
    assert e.literal_pieces == pieces and e.captures == captures
    assert s[slice(*e.source_span)] == call
    assert e.token.source_path == 'original.rs' and e.token.line == 2
    assert e.value is None
    with pytest.raises(RustError,match='stream locking and write-error panic'):
        compile_rust(s)


@pytest.mark.parametrize('call,message', [
    ('println!x', 'expected delimited input'),
    ('println!(42)', 'format string literal'),
    ('eprintln!(env!("FOO"))', 'format strings produced by other macros'),
    ('println!("}")', 'unmatched'),
    ('println!("{")', 'unclosed'),
    ('println!("{}")', 'positional arguments'),
    ('println!("{:04}")', 'format specifications'),
    ('println!("{4}")', 'positional arguments'),
    ('println!("{a.x}")', 'implicit plain identifier'),
    ('println!(r"{_}")', 'implicit plain identifier'),
    ('println!("{}", 123)', 'explicit positional'),
    ('println!("{a}", a=42)', 'explicit positional'),
    ('println!("x"]', "expected"),
])
def test_invalid_or_unimplemented_input_never_returns_a_false_literal(call,message):
    with pytest.raises(RustError,match=message):
        Parser('fn f(){'+call+';}').parse()


@pytest.mark.parametrize('s,message', [
    ('#![no_std] fn f(){println!("x");}', 'macro-by-example expansion'),
    ('use m::other as println; fn f(){println!("x");}', 'same-name import'),
    ('fn f(){eprintln!("x");} use m::eprintln;', 'same-name import'),
    ('fn f(){println!();} use m::*;', 'glob import'),
    ('fn f(){use m::println; println!();}', 'block-scoped use'),
    ('macro_rules! println { () => (()) } fn f(){println!();}', 'macro-by-example expansion'),
])
def test_no_pretense_of_a_prelude_if_the_source_could_shadow_it(s,message):
    with pytest.raises(RustError,match=message):
        Parser(s).parse()


@pytest.mark.parametrize('s', [
    'fn unused<T>(){ println!("x"); } fn f(){}',
    'fn f(){ if false { println!("would not execute"); } }',
    'mod nested { fn unused(){eprintln!("x");} } fn f(){}',
])
def test_no_codegen_even_in_an_unused_generic_a_dead_branch_or_an_inline_module(s):
    with pytest.raises(RustError,match='requires the owned std::io::'):
        compile_rust(s)


def test_inactive_cfg_does_not_expand_or_call_unavailable_io():
    cfg = CfgOptions()
    s=('pub fn f()->u64{ #[cfg(any())] println!(not_a_format_literal); 42 } '
       '#[cfg(any())] pub fn gone(){eprintln!("x");}')
    p=Parser(s,configuration=OwnedRustConfiguration(TARGET,2021,cfg))
    p.parse()
    assert not p.macro_expansions
    assert compile_rust(s,cfg=cfg).emit_object().startswith(b'\x7fELF')


def test_a_separate_modules_alias_does_not_shadow_the_macro():
    p=Parser('mod a { use m::x as println; } mod b { fn f(){println!("yes");} }')
    p.parse()
    e,=p.macro_expansions
    assert e.literal_pieces == ('yes\n',)


