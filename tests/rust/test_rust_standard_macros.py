"""The core/std macros the owned parser expands (std_macros.py).

Step 2 (second part) of pcc/frontends/rust/README.md, "Plan: the DataSketches
Rust arm". Format strings follow std::fmt's grammar and rustc's
expansion-time checks (unmatched braces, unknown traits, argument counts and
references, unused and positional-after-named arguments, implicit captures);
their arguments are borrowed. `debug_assert!` and its relatives are
`if cfg!(debug_assertions) { .. }`: type- and borrow-checked always (a move
inside is E0382, as in rustc), emitted only when debug assertions are on;
`cfg!` is evaluated by the owned configuration. Panics, core::fmt and alloc
are semantic gaps until their plan steps. Every corpus row and program is
original; verdicts and results were compared with a local rustc 1.92.0
build with debug assertions off, which stays a labeled reference.
"""
import re
import shutil
import subprocess

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.configuration import CfgOptions
from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function, run_rustc_str_function

OFF = CfgOptions(debug_assertions=False)
S = 'pub struct S { v: u64 }\n'

# (name, original source, verdict of rustc 1.92.0 with -C debug-assertions=off):
# 'fmt' is its code-less format_args! error, 'compile_error' a compile_error!.
CORPUS = (
    ('debug-assert', 'pub fn f(x: u64) -> u64 { debug_assert!(x > 0); x + 1 }', 'ok'),
    ('debug-assert-message', 'pub fn f(x: u64) -> u64 { debug_assert!(x <= 63, "value {} too big", x); x }', 'ok'),
    ('debug-assert-capture', 'pub fn f(x: u64) -> u64 { debug_assert!(x <= 63, "value {x} too big"); x }', 'ok'),
    ('debug-assert-eq', 'pub fn f(x: u64) -> u64 { debug_assert_eq!(x, 3, "x={}", x); x }', 'ok'),
    ('debug-assert-ne', 'pub fn f(x: u64) -> u64 { debug_assert_ne!(x, 3); x }', 'ok'),
    ('debug-assert-eq-mismatch', 'pub fn f(x: u64) -> u64 { debug_assert_eq!(x, 3u32); x }', 'E0308'),
    ('debug-assert-not-bool', 'pub fn f(x: u64) -> u64 { debug_assert!(x); x }', 'E0308'),
    ('debug-assert-unknown-capture', 'pub fn f(x: u64) -> u64 { debug_assert!(x > 0, "{y}"); x }', 'E0425'),
    ('debug-assert-unused-argument', 'pub fn f(x: u64) -> u64 { debug_assert!(x > 0, "{}", x, x); x }', 'fmt'),
    ('debug-assert-missing-argument', 'pub fn f(x: u64) -> u64 { debug_assert!(x > 0, "{} {}", x); x }', 'fmt'),
    ('unmatched-close-brace', 'pub fn f(x: u64) -> u64 { debug_assert!(x > 0, "}"); x }', 'fmt'),
    ('unclosed-brace', 'pub fn f(x: u64) -> u64 { debug_assert!(x > 0, "{"); x }', 'fmt'),
    ('escaped-braces', 'pub fn f(x: u64) -> u64 { debug_assert!(x > 0, "{{}} {}", x); x }', 'ok'),
    ('unknown-trait', 'pub fn f(x: u64) -> u64 { debug_assert!(x > 0, "{:z}", x); x }', 'fmt'),
    ('repeated-index', 'pub fn f(x: u64) -> u64 { debug_assert!(x > 0, "{0} {0}", x); x }', 'ok'),
    ('bad-index', 'pub fn f(x: u64) -> u64 { debug_assert!(x > 0, "{1}", x); x }', 'fmt'),
    ('named-argument', 'pub fn f(x: u64) -> u64 { debug_assert!(x > 0, "{name}", name = x); x }', 'ok'),
    ('unused-named-argument', 'pub fn f(x: u64) -> u64 { debug_assert!(x > 0, "{}", x, name = x); x }', 'fmt'),
    ('positional-after-named', 'pub fn f(x: u64) -> u64 { debug_assert!(x > 0, "{name}", name = x, x); x }', 'fmt'),
    ('width-and-star-precision', 'pub fn f(x: u64, w: usize) -> u64 { debug_assert!(x > 0, "{:>w$} {:.*}", x, 2, x); x }',
     'ok'),
    ('width-argument-not-usize', 'pub fn f(x: u64) -> u64 { debug_assert!(x > 0, "{:w$}", x, w = 3u32); x }', 'E0308'),
    ('hex-and-debug', 'pub fn f(x: u64) -> u64 { debug_assert!(x > 0, "{:#x} {:?} {:08b}", x, x, x); x }', 'ok'),
    ('move-in-disabled-assert', S + 'pub fn take(s: S) -> bool { s.v > 0 }\n'
                                    'pub fn f(s: S) -> u64 { debug_assert!(take(s)); s.v }', 'E0382'),
    ('borrow-in-disabled-assert', S + 'pub fn peek(s: &S) -> bool { s.v > 0 }\n'
                                      'pub fn f(s: S) -> u64 { debug_assert!(peek(&s)); s.v }', 'ok'),
    ('cfg-debug-assertions', 'pub fn f() -> u64 { if cfg!(debug_assertions) { 1 } else { 2 } }', 'ok'),
    ('cfg-not-test', 'pub fn f() -> u64 { if cfg!(not(test)) { 1 } else { 2 } }', 'ok'),
    ('compile-error', 'pub fn f() -> u64 { compile_error!("stop here"); 0 }', 'compile_error'),
    ('format-string-not-literal', 'pub fn f(x: u64) -> u64 { let m = "a"; debug_assert!(x > 0, m); x }', 'fmt'),
)
MESSAGES = {
    'E0308': 'type mismatch',
    'E0425': "unknown local 'y'",
    'E0382': "use of moved or possibly moved value 's'",
    'fmt': r'argument never used|positional arguments? in format string|invalid format string|unknown format '
           r'trait|invalid reference to positional argument|positional arguments cannot follow named|format '
           r'argument must be a string literal|named argument never used',
    'compile_error': 'stop here',
}


def rustc_verdict(stderr, returncode):
    if returncode == 0:
        return 'ok'
    codes = ','.join(sorted(set(re.findall(r'error\[(E\d+)\]', stderr))))
    if codes:
        return codes
    if 'stop here' in stderr:
        return 'compile_error'
    return 'fmt'


@pytest.mark.parametrize('name,source,verdict', CORPUS, ids=[row[0] for row in CORPUS])
def test_standard_macro_verdicts_match_a_rustc_build_without_running_rustc(name, source, verdict, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('owned admission ran a tool'))
    if verdict == 'ok':
        assert not compile_rust(source, cfg=OFF).externally_checked
    else:
        with pytest.raises(RustError, match=MESSAGES[verdict]):
            compile_rust(source, cfg=OFF)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_builds_give_the_same_verdicts(tmp_path):
    for index, (name, source, verdict) in enumerate(CORPUS):
        path = tmp_path / f'case{index}.rs'
        path.write_text(source + '\n')
        run = subprocess.run([shutil.which('rustc'), '--crate-type=lib', '--edition=2021', '--emit=obj', '-O',
                              '-C', 'debug-assertions=off', '-A', 'warnings', str(path),
                              '-o', str(tmp_path / f'case{index}.o')], capture_output=True, text=True, timeout=60)
        assert rustc_verdict(run.stderr, run.returncode) == verdict, (name, run.stderr)


def test_format_strings_parse_into_pieces_and_placeholders():
    from pcc.frontends.rust.std_macros import Placeholder
    from pcc.frontends.rust.syntax import Parser
    parser = Parser('fn f(x: u64, w: usize) -> u64 { debug_assert!(x > 0, "a{{{:>w$}}} {x:#x} {:.*}", x, 3, x); x }',
                    configuration=None)
    with pytest.raises(RustError, match='debug_assertions'):
        parser.parse()
    from pcc.frontends.rust.configuration import OwnedRustConfiguration
    from pcc.frontends.rust.targets import DEFAULT_TARGET
    parser = Parser('fn f(x: u64, w: usize) -> u64 { debug_assert!(x > 0, "a{{{:>w$}}} {x:#x} {:.*}", x, 3, x); x }',
                    configuration=OwnedRustConfiguration(DEFAULT_TARGET, 2021, OFF))
    function, = parser.parse()
    assertion = function.body.children[0].children[0]
    assert assertion.kind == 'debug_assert' and assertion.value is False
    message = assertion.children[0].children[1].children[0].children[0]
    pieces = message.value.pieces
    assert pieces[0] == 'a{' and pieces[2] == '} '
    assert isinstance(pieces[1], Placeholder) and pieces[1].align == '>' and pieces[1].width.argument
    assert pieces[3].trait == 'LowerHex' and pieces[3].alternate
    assert pieces[5].precision.argument and message.value.names[-2:] == ('w', 'x')


# Each check sets one failure bit. With debug assertions off, an assertion's
# condition is never evaluated: `bump` must not run.
PROGRAM = '''\
fn bump(n: &mut u64) -> bool { *n += 1; true }

fn checked_add(a: u64, b: u64) -> u64 {
    debug_assert!(a < 1000, "a was {a}");
    debug_assert_eq!(a % 2, 0, "{} is odd", a);
    debug_assert_ne!(b, 7);
    a + b
}

pub fn answer() -> u64 {
    let mut bad = 0u64;
    let mut calls = 0u64;
    debug_assert!(bump(&mut calls));
    if calls != 0 { bad |= 1 << 0; }
    if checked_add(2000, 7) != 2007 { bad |= 1 << 1; }
    if cfg!(debug_assertions) { bad |= 1 << 2; }
    if !cfg!(not(test)) { bad |= 1 << 3; }
    let flag = if cfg!(any(debug_assertions, target_pointer_width = "64")) { 1 } else { 0 };
    if flag != 1 { bad |= 1 << 4; }
    if bad == 0 { 0xfedcba9876543210 } else { bad }
}
'''


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE or (None if shutil.which('rustc') else
                                                  'development-only rustc is not installed'))
def test_disabled_debug_assertions_run_with_owned_admission_and_match_rustc(tmp_path, monkeypatch):
    owned = run_owned_u64_function(PROGRAM, 'answer', tmp_path, monkeypatch, experimental_rustc_checked=False,
                                   cfg=OFF)
    reference = run_rustc_str_function(PROGRAM, 'answer', tmp_path, output='&{call}.to_le_bytes()',
                                       debug_assertions=False)
    assert owned == int.from_bytes(reference, 'little') == 0xfedcba9876543210


@pytest.mark.parametrize('source,boundary', [
    ('pub fn f(x: u64) -> u64 { assert!(x > 0); x }', r'assert! needs owned panic support.*plan step 5'),
    ('pub fn f(x: u64) -> u64 { if x == 0 { panic!("zero {}", x) } x }', 'panic! needs owned panic support'),
    ('pub fn f(x: u64) -> u64 { if x == 0 { unreachable!("x={x}") } x }', 'unreachable! needs owned panic support'),
    ('pub fn f() -> u64 { let v = vec![0u8; 4]; 0 }', r'vec! needs alloc::vec::Vec.*plan step 7'),
    ('pub fn f(x: u64) -> u64 { let s = format!("{x:>5}"); 0 }', r'format! needs alloc::string::String and core::fmt'),
    (S + 'pub fn f(s: S) -> u64 { debug_assert!(s.v > 0, "{:?}", s); s.v }',
     r'formatting S with \{:\?\} needs the owned core::fmt trait model'),
])
def test_runtime_models_the_expansions_need_keep_explicit_boundaries(source, boundary):
    with pytest.raises(RustError, match=boundary):
        compile_rust(source, cfg=OFF)


def test_debug_assertions_need_an_explicit_choice():
    with pytest.raises(RustError, match='explicit CfgOptions.debug_assertions choice'):
        compile_rust('pub fn f(x: u64) -> u64 { debug_assert!(x > 0); x }')
