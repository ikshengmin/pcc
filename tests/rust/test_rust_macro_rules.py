"""macro_rules! macro-by-example, expanded by the owned parser.

Step 2 (first part) of pcc/frontends/rust/README.md, "Plan: the DataSketches
Rust arm". Rust Reference macros-by-example.html: rules are tried in order;
matchers hold literal tokens, delimited groups, fragments (expr, ty, ident,
tt, literal, pat, path, block, lifetime) and repetitions `$(..) sep? op`;
`expr` fragments are transcribed as one parenthesized expression; `$crate`
is `crate`. Hygiene is mixed-site: locals a transcriber writes are invisible
to the invocation and vice versa (E0425), items are not. Scoping is textual
(a macro is visible after its definition, in blocks until their end and in
child modules declared later). Expansion depth follows rustc's default
recursion_limit 128. `stringify!` of one token and `concat!` of literals are
core's. Every corpus row and program is original; verdicts and results were
compared with a local rustc 1.92.0 build (-O), which stays a labeled
reference.
"""
import re
import shutil
import subprocess

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function, run_rustc_str_function

# (name, original source, verdict of a rustc 1.92.0 build): 'norule', 'nomacro',
# 'repeat' and 'recursion' are its code-less macro errors.
CORPUS = (
    ('expr-macro', 'macro_rules! double { ($e:expr) => { $e * 2 }; }\npub fn f(x: u64) -> u64 { double!(x + 1) }', 'ok'),
    ('item-macro-fn', 'macro_rules! getter { ($name:ident, $v:expr) => { pub fn $name() -> u64 { $v } }; }\ngetter!(seven, 7);\ngetter!(eight, 8);\npub fn f() -> u64 { seven() + eight() }', 'ok'),
    ('repetition-sum', 'macro_rules! sum { ($($e:expr),*) => { 0 $(+ $e)* }; }\npub fn f() -> u64 { sum!(1, 2, 3) }', 'ok'),
    ('optional-repetition', 'macro_rules! opt { ($a:expr $(, $b:expr)?) => { $a $(+ $b)? }; }\npub fn f() -> u64 { opt!(1) + opt!(2, 3) }', 'ok'),
    ('hygiene-macro-local', 'macro_rules! twice { ($e:expr) => {{ let y = $e; y * 2 }}; }\npub fn f() -> u64 { let y = 3; twice!(y + 1) + y }', 'ok'),
    ('hygiene-no-capture', 'macro_rules! uses_x { () => { x + 1 }; }\npub fn f() -> u64 { let x = 1; uses_x!() }', 'E0425'),
    ('hygiene-local-not-visible', 'macro_rules! def { () => { let z = 1; }; }\npub fn f() -> u64 { def!(); z }', 'E0425'),
    ('caller-ident-binding', 'macro_rules! def { ($n:ident) => { let $n = 1; }; }\npub fn f() -> u64 { def!(z); z }', 'ok'),
    ('statement-macro', 'macro_rules! set { ($v:ident, $e:expr) => { $v = $e; }; }\npub fn f() -> u64 { let mut a = 1; set!(a, 5); a }', 'ok'),
    ('impl-macro', 'pub trait T { fn t(&self) -> u64; }\npub struct A { v: u64 }\npub struct B { v: u64 }\nmacro_rules! imp { ($ty:ty, $k:expr) => { impl T for $ty { fn t(&self) -> u64 { self.v * $k } } }; }\nimp!(A, 2);\nimp!(B, 3);\npub fn f() -> u64 { let a = A { v: 1 }; let b = B { v: 1 }; a.t() + b.t() }', 'ok'),
    ('no-rule', 'macro_rules! one { (a) => { 1 }; }\npub fn f() -> u64 { one!(b) }', 'norule'),
    ('unknown-macro', 'pub fn f() -> u64 { nope!() }', 'nomacro'),
    ('used-before-definition', 'pub fn f() -> u64 { later!() }\nmacro_rules! later { () => { 1 }; }', 'nomacro'),
    ('block-scoped-definition', 'pub fn f() -> u64 { { macro_rules! inner { () => { 1 }; } } inner!() }', 'nomacro'),
    ('concat-stringify', 'macro_rules! name { ($t:ty) => { concat!(stringify!($t), " summary") }; }\npub fn f() -> usize { name!(u32).len() }', 'ok'),
    ('expr-fragment-precedence', 'macro_rules! as64 { ($e:expr) => { $e as u64 }; }\npub fn f(x: u32) -> u64 { as64!(x + 1) }', 'ok'),
    ('tt-recursion', 'macro_rules! count { () => { 0 }; ($h:tt $($t:tt)*) => { 1 + count!($($t)*) }; }\npub fn f() -> u64 { count!(a b c d) }', 'ok'),
    ('repeat-mismatch', 'macro_rules! zip { ($($a:expr),* ; $($b:expr),*) => { 0 $(+ $a * $b)* }; }\npub fn f() -> u64 { zip!(1, 2; 3) }', 'repeat'),
    ('recursion-limit', 'macro_rules! inf { () => { inf!() }; }\npub fn f() -> u64 { inf!() }', 'recursion'),
    ('crate-path', 'pub fn helper() -> u64 { 4 }\nmacro_rules! call { () => { $crate::helper() }; }\npub fn f() -> u64 { call!() }', 'ok'),
    ('macro-calls-macro', 'macro_rules! one { () => { 1 }; }\nmacro_rules! two { () => { one!() + one!() }; }\npub fn f() -> u64 { two!() }', 'ok'),
    ('expression-after-statement-macro', 'macro_rules! val { () => { 5u64 }; }\npub fn f() -> u64 { val!() + 1 }', 'ok'),
    ('first-rule-wins', 'macro_rules! pick { ($e:expr) => { 1 }; ($e:expr) => { 2 }; }\npub fn f() -> u64 { pick!(0) }', 'ok'),
    ('struct-from-macro', 'macro_rules! wrapper { ($name:ident) => { pub struct $name { pub v: u64 } impl $name { pub fn get(&self) -> u64 { self.v } } }; }\nwrapper!(W);\npub fn f() -> u64 { let w = W { v: 9 }; w.get() }', 'ok'),
)
MESSAGES = {
    'E0425': "unknown local '(x|z)'",
    'norule': r'no rules expected this token in macro call `one!`',
    'nomacro': r'cannot find macro `(nope|later|inner)` in this scope',
    'repeat': r'meta-variable `a` repeats 2 times, but `b` repeats 1 times',
    'recursion': r'recursion limit reached while expanding `inf!`',
}


def rustc_verdict(stderr, returncode):
    if returncode == 0:
        return 'ok'
    for text, verdict in (('no rules expected', 'norule'), ('cannot find macro', 'nomacro'),
                          ('repeats', 'repeat'), ('recursion limit reached', 'recursion')):
        if text in stderr:
            return verdict
    return ','.join(sorted(set(re.findall(r'error\[(E\d+)\]', stderr)))) or 'error'


@pytest.mark.parametrize('name,source,verdict', CORPUS, ids=[row[0] for row in CORPUS])
def test_macro_verdicts_match_a_rustc_build_without_running_rustc(name, source, verdict, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('owned admission ran a tool'))
    if verdict == 'ok':
        assert not compile_rust(source).externally_checked
    else:
        with pytest.raises(RustError, match=MESSAGES[verdict]):
            compile_rust(source)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_builds_give_the_same_verdicts(tmp_path):
    for index, (name, source, verdict) in enumerate(CORPUS):
        path = tmp_path / f'case{index}.rs'
        path.write_text(source + '\n')
        run = subprocess.run([shutil.which('rustc'), '--crate-type=lib', '--edition=2021', '--emit=obj', '-O',
                              '-A', 'warnings', str(path), '-o', str(tmp_path / f'case{index}.o')],
                             capture_output=True, text=True, timeout=60)
        assert rustc_verdict(run.stderr, run.returncode) == verdict, (name, run.stderr)


# Each check sets one failure bit.
PROGRAM = 'pub trait Weight { fn weight(&self) -> u64; }\n\nmacro_rules! wrapper {\n    ($name:ident, $factor:expr) => {\n        pub struct $name { pub v: u64 }\n        impl Weight for $name {\n            fn weight(&self) -> u64 { self.v * $factor }\n        }\n    };\n}\n\nwrapper!(Light, 2);\nwrapper!(Heavy, 10 + 1);\n\nmacro_rules! sum {\n    () => { 0 };\n    ($head:expr $(, $tail:expr)*) => { $head + sum!($($tail),*) };\n}\n\nmacro_rules! count {\n    () => { 0u64 };\n    ($h:tt $($t:tt)*) => { 1u64 + count!($($t)*) };\n}\n\nmacro_rules! twice {\n    ($e:expr) => {{ let y = $e; y * 2 }};\n}\n\nmacro_rules! set_all {\n    ($($v:ident = $e:expr);* $(;)?) => { $($v = $e;)* };\n}\n\nmacro_rules! label_len {\n    ($t:ty) => { concat!(stringify!($t), " summary").len() as u64 };\n}\n\npub fn helper() -> u64 { 5 }\n\nmacro_rules! via_crate {\n    () => { $crate::helper() * 3 };\n}\n\nmacro_rules! narrow {\n    ($e:expr) => { $e as u8 as u64 };\n}\n\npub fn answer() -> u64 {\n    let mut bad = 0u64;\n    let l = Light { v: 4 };\n    let h = Heavy { v: 2 };\n    if l.weight() != 8 || h.weight() != 22 { bad |= 1 << 0; }\n    if sum!(1, 2, 3, 4) != 10 || sum!() != 0 { bad |= 1 << 1; }\n    if count!(a b (c d) [e] f) != 5 { bad |= 1 << 2; }\n    let y = 7u64;\n    if twice!(y + 1) != 16 || y != 7 { bad |= 1 << 3; }\n    let mut a = 0u64;\n    let mut b = 0u64;\n    set_all!(a = 3; b = a + 4;);\n    if a != 3 || b != 7 { bad |= 1 << 4; }\n    if label_len!(u32) != 11 { bad |= 1 << 5; }\n    if via_crate!() != 15 { bad |= 1 << 6; }\n    if narrow!(255u64 + 2) != 1 { bad |= 1 << 7; }\n    if bad == 0 { 0xfedcba9876543210 } else { bad }\n}\n'


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE or (None if shutil.which('rustc') else
                                                  'development-only rustc is not installed'))
def test_macro_program_runs_with_owned_admission_and_matches_rustc(tmp_path, monkeypatch):
    owned = run_owned_u64_function(PROGRAM, 'answer', tmp_path, monkeypatch, experimental_rustc_checked=False)
    reference = run_rustc_str_function(PROGRAM, 'answer', tmp_path, output='&{call}.to_le_bytes()')
    assert owned == int.from_bytes(reference, 'little') == 0xfedcba9876543210


def test_expansion_tokens_carry_their_hygiene_mark():
    from pcc.frontends.rust.syntax import Parser
    parser = Parser('macro_rules! m { ($e:expr) => { let y = $e; y }; }\nfn f() -> u64 { let y = 1; m!(y) }')
    parser.parse()
    expansion, = [item for item in parser.macro_expansions if item.name == 'm']
    marks = {token.text: token.hygiene for token in expansion.tokens if token.text in {'let', 'y'}}
    assert marks['let'] > 0
    assert {token.hygiene for token in expansion.tokens if token.text == 'y'} == {0, marks['let']}


@pytest.mark.parametrize('source,boundary', [
    ('#[macro_export]\nmacro_rules! m { () => { 1 }; }', 'unsupported Rust attribute'),
    ('macro_rules! m { ($s:stmt) => { $s }; }', 'the `stmt` macro fragment is not implemented'),
    ('pub fn f() -> usize { stringify!(a b).len() }', 'stringify! of several tokens'),
    ('pub fn f() -> u64 { let v = vec![1u64]; 0 }', r'vec! needs alloc::vec::Vec'),
])
def test_macro_forms_beyond_the_owned_expander_keep_explicit_boundaries(source, boundary):
    with pytest.raises(RustError, match=boundary):
        compile_rust(source)
