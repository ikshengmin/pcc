"""Shift operators `<<`, `>>`, `<<=`, `>>=` and Rust operator precedence.

Rust Reference, expressions/operator-expr.html#arithmetic-and-logical-binary-operators
and expressions.html#expression-precedence. Shifts bind tighter than `&`,
which binds tighter than `^` and `|`; all of them bind tighter than
comparisons (unlike C). A shift's operands are any two integer types, never
unified (rustc enforce_builtin_binop_types): the result is the LHS type, and
an unconstrained literal amount is i32. With overflow checks off, the amount
is truncated or zero-extended to the shifted width and masked to `width - 1`
(rustc codegen_ssa build_masked_shift); `>>` is arithmetic for signed types.
A known amount whose unsigned bit pattern is at least the width is rustc's
arithmetic_overflow lint where reachable (KnownPanicsLint), and E0080 in
mandatory constant evaluation. rustc reads `<<` after `x as T` as generic
arguments and rejects it. Every corpus row and program is original; verdicts
and results were compared with a local rustc 1.92.0 build (-O), which stays
a labeled reference.
"""
import re
import shutil
import subprocess

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function, run_rustc_str_function

# (name, original source, verdict of a rustc 1.92.0 build): 'overflow' is its
# arithmetic_overflow lint or an E0080 shift overflow in constant evaluation,
# 'generic' its parse error for `<<` / `<` after a cast type.
CORPUS = (
    ('shl-literal', 'pub fn f(x: u32) -> u32 { x << 3 }', 'ok'),
    ('shr-mixed-types', 'pub fn f(x: u64, s: u32) -> u64 { x >> s }', 'ok'),
    ('shl-i64-amount', 'pub fn f(x: u32, s: i64) -> u32 { x << s }', 'ok'),
    ('shl-width-amount', 'pub fn f(x: u32) -> u32 { x << 32 }', 'overflow'),
    ('shl-negative-amount', 'pub fn f(x: i32) -> i32 { x << -1 }', 'overflow'),
    ('shl-negative-isize-amount', 'pub fn f(x: u64) -> u64 { x << -1isize }', 'overflow'),
    ('shr-wide-amount', 'pub fn f(x: u32) -> u32 { x >> 4294967296u64 }', 'overflow'),
    ('shr-propagated-amount', 'pub fn f(x: u64) -> u64 { let s = 64; x >> s }', 'overflow'),
    ('shl-propagated-amount-in-range', 'pub fn f(x: u32) -> u32 { let s = 31; x << s }', 'ok'),
    ('shr-last-bit', 'pub fn f(x: u64) -> u64 { x >> 63 }', 'ok'),
    ('shl-amount-from-shift', 'pub fn f(x: u32) -> u32 { x << (1 << 5) }', 'overflow'),
    ('shl-reassigned-amount-joined', 'pub fn f(x: u32, c: bool) -> u32 { let mut s = 40; if c { s = 1; } x << s }', 'ok'),
    ('shl-reassigned-amount-known', 'pub fn f(x: u32) -> u32 { let mut s = 1; s = 40; x << s }', 'overflow'),
    ('shl-unreachable', 'pub fn f(x: u32) -> u32 { if false { x << 40 } else { x } }', 'ok'),
    ('shl-literal-unreachable', 'pub fn f(x: u32) -> u32 { if false { 1u32 << 40 } else { x } }', 'ok'),
    ('shl-after-known-false-and', 'pub fn f(x: u32) -> u32 { let c = true & false; if c { x << 40 } else { x } }', 'ok'),
    ('shl-after-known-true-or', 'pub fn f(x: u32) -> u32 { let c = true | false; if c { x << 40 } else { x } }', 'overflow'),
    ('compound-width-amount', 'pub fn f(mut x: u32) -> u32 { x <<= 32; x }', 'overflow'),
    ('compound-mixed-types', 'pub fn f(mut x: u32) -> u32 { x >>= 1u64; x <<= 2i32; x }', 'ok'),
    ('compound-static-mut', 'pub static mut S: u64 = 1; pub fn f() { unsafe { S <<= 3u32; } }', 'ok'),
    ('const-width-amount', 'pub const C: u32 = 1 << 32;', 'overflow'),
    ('const-sign-bit', 'pub const C: i32 = 1 << 31;', 'ok'),
    ('const-bits-shifted-out', 'pub const C: u32 = 0xFF << 28;', 'ok'),
    ('enum-repr-discriminants', '#[repr(u32)] pub enum E { A = 1 << 0, B = 1 << 4 }', 'ok'),
    ('enum-default-discriminants', 'pub enum F { A = 1 << 2, B = 1 << 40 }', 'ok'),
    ('enum-discriminant-width-amount', '#[repr(u32)] pub enum E { A = 1 << 32 }', 'overflow'),
    ('array-length', 'pub fn f() -> [u32; 1 << 3] { [0; 8] }', 'ok'),
    ('bool-shl', 'pub fn f() -> bool { true << 1 }', 'E0369'),
    ('result-is-lhs-type', 'pub fn f(x: i32) -> i64 { x << 1 }', 'E0308'),
    ('literal-lhs-from-context', 'pub fn f() -> u64 { 1 << 40 }', 'ok'),
    ('literal-lhs-defaults-i32', 'pub fn f() -> i64 { let _k = 1 << 40; 0 }', 'overflow'),
    ('shift-result-propagates', 'pub fn f() -> i32 { let a = 1 << 31; a + a }', 'overflow'),
    ('shl-result-mul', 'pub fn f() -> u32 { let a = 1u32 << 31; a * 2 }', 'overflow'),
    ('shr-result-add', 'pub fn f() -> u32 { let a = 0x8000_0000u32 >> 31; 4294967295u32 + a }', 'overflow'),
    ('signed-shr-result', 'pub fn f() -> i32 { let a = -8 >> 1; a - 2147483644 }', 'ok'),
    ('signed-shr-result-overflow', 'pub fn f() -> i32 { let a = -8 >> 1; a - 2147483645 }', 'overflow'),
    ('bitand-result-propagates', 'pub fn f() -> u32 { let a = 0xFFu32 & 1; 4294967295u32 + a }', 'overflow'),
    ('bitor-result-propagates', 'pub fn f() -> u32 { let a = 0u32 | 1; 4294967295u32 + a }', 'overflow'),
    ('bitxor-result-propagates', 'pub fn f() -> i32 { let a = -1 ^ 2147483647; a - 1 }', 'overflow'),
    ('bitand-result-in-range', 'pub fn f() -> u32 { let a = 0xFFu32 & 0; 4294967295u32 + a }', 'ok'),
    ('not-result-propagates', 'pub fn f() -> u32 { let a = !0u32; a + 1 }', 'overflow'),
    ('not-signed-result', 'pub fn f() -> i32 { let a = !0i32; a - 2147483647 }', 'ok'),
    ('cast-then-shl', 'pub fn f(x: u64) -> u32 { x as u32 << 2 }', 'generic'),
    ('cast-then-lt', 'pub fn f(x: u64) -> bool { x as u32 < 2 }', 'generic'),
    ('parenthesized-cast-shl', 'pub fn f(x: u64) -> u32 { (x as u32) << 2 }', 'ok'),
    ('bitor-before-eq', 'pub fn f(a: bool, b: bool) -> bool { a | b == b }', 'ok'),
    ('bitand-before-eq', 'pub fn f(a: u32) -> bool { a & 1 == 1 }', 'ok'),
    ('bitxor-before-ne', 'pub fn f(a: u32, b: u32) -> bool { a ^ b != 0 }', 'ok'),
    ('shr-before-gt', 'pub fn f(x: u32) -> bool { x >> 1 > 3 }', 'ok'),
    ('add-before-shl', 'pub fn f(x: u32) -> u32 { 2 + x << 1 }', 'ok'),
    ('shl-left-associative', 'pub fn f(x: i64) -> i64 { x << 1 << 2 }', 'ok'),
)
MESSAGES = {
    'overflow': r'this arithmetic operation will overflow \(rustc arithmetic_overflow\)'
                r'|attempt to shift with overflow \(rustc E0080\)',
    'generic': r'`<<` is interpreted as a start of generic arguments for `u32`, not a shift'
               r"|unsupported Rust type '2'",
    'E0369': r"operator '<<' does not accept bool.*\(rustc E0369\)",
    'E0308': r'type mismatch: expected i64, found i32',
}


def rustc_verdict(stderr, returncode):
    if returncode == 0:
        return 'ok'
    if 'this arithmetic operation will overflow' in stderr or 'which would overflow' in stderr:
        return 'overflow'
    if 'is interpreted as a start of generic arguments' in stderr:
        return 'generic'
    return ','.join(sorted(set(re.findall(r'error\[(E\d+)\]', stderr)))) or 'error'


@pytest.mark.parametrize('name,source,verdict', CORPUS, ids=[row[0] for row in CORPUS])
def test_shift_verdicts_match_a_rustc_build_without_running_rustc(name, source, verdict, monkeypatch):
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


def test_shift_operands_keep_their_own_types():
    from pcc.frontends.rust.syntax import Parser
    from pcc.frontends.rust.semantics import Checker
    parser = Parser('fn f(x: u64, s: u32) -> u64 { let k = 1 << s; x >> k }')
    functions = parser.parse()
    Checker(functions).check()
    body = functions[0].body
    shift_k, shift_x = body.children[0].children[0], body.children[1]
    assert (shift_k.ty, shift_k.children[1].ty) == ('i32', 'u32')
    assert (shift_x.ty, shift_x.children[1].ty) == ('u64', 'i32')


# Each check sets one failure bit; every amount reaches the callee at run time.
PROGRAM = '''\
const SHIFTED_OUT: u32 = 0xFF << 28;
const FLAG: u64 = 1 << 2;

enum Bits { Low = 1 << 1, High = 1 << 40 }

fn shl32(x: u32, s: u32) -> u32 { x << s }
fn shr_i32(x: i32, s: i64) -> i32 { x >> s }
fn shr_u64(x: u64, s: i32) -> u64 { x >> s }
fn shl_i64(x: i64, s: i32) -> i64 { x << s }
fn shl_wide(x: u32, s: u64) -> u32 { x << s }
fn shr_isize(x: isize, s: usize) -> isize { x >> s }
fn compound(mut x: u64, a: u32, b: i64) -> u64 { x <<= a; x >>= b; x }
fn cast_then_shift(x: u64) -> u32 { (x as u32) << 4 }
fn precedence(a: bool, b: bool, x: u32) -> u64 {
    let p = (a | b == b) as u64;
    let q = (x & 1 == 1) as u64;
    let r = (x ^ 3 != 0) as u64;
    p | q << 1 | r << 2
}

pub fn answer() -> u64 {
    let mut bad: u64 = 0;
    if shl32(1, 33) != 2 { bad = bad | 1 << 0; }
    if shl32(0xFF, 28) != 0xF000_0000 { bad = bad | 1 << 1; }
    if shr_i32(-8, 1) != -4 { bad = bad | 1 << 2; }
    if shr_i32(-1, 100) != -1 { bad = bad | 1 << 3; }
    if shr_i32(-2147483648, 31) != -1 { bad = bad | 1 << 4; }
    if shr_u64(0x8000_0000_0000_0000, 63) != 1 { bad = bad | 1 << 5; }
    if shr_u64(0xF0, -60) != 0xF { bad = bad | 1 << 6; }
    if shl_i64(3, -1) as u64 != 0x8000_0000_0000_0000 { bad = bad | 1 << 7; }
    if shl_wide(1, 0x1_0000_0003) != 8 { bad = bad | 1 << 8; }
    if shr_isize(-1024, 67) != -128 { bad = bad | 1 << 9; }
    if compound(1, 40, 2) != 0x40_0000_0000 { bad = bad | 1 << 10; }
    if compound(0xFFFF_FFFF_FFFF_FFFF, 68, -1) != 1 { bad = bad | 1 << 11; }
    if SHIFTED_OUT != 0xF000_0000 || FLAG != 4 { bad = bad | 1 << 12; }
    if Bits::Low as i64 != 2 || Bits::High as i64 != 0x100_0000_0000 { bad = bad | 1 << 13; }
    if 2 + 3 << 1 != 10 { bad = bad | 1 << 14; }
    if precedence(true, false, 5) != 6 || precedence(false, false, 2) != 5 { bad = bad | 1 << 15; }
    if cast_then_shift(0x1_0000_0001) != 16 { bad = bad | 1 << 16; }
    let wide: u64 = 1 << 40;
    let lengths = [wide; 1 << 2];
    if lengths[3] != 0x100_0000_0000 { bad = bad | 1 << 17; }
    if bad == 0 { 0xfedcba9876543210 } else { bad }
}
'''


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE or (None if shutil.which('rustc') else
                                                  'development-only rustc is not installed'))
def test_shift_program_runs_with_owned_admission_and_matches_rustc(tmp_path, monkeypatch):
    owned = run_owned_u64_function(PROGRAM, 'answer', tmp_path, monkeypatch, experimental_rustc_checked=False)
    reference = run_rustc_str_function(PROGRAM, 'answer', tmp_path, output='&{call}.to_le_bytes()')
    assert owned == int.from_bytes(reference, 'little') == 0xfedcba9876543210


@pytest.mark.parametrize('source,boundary', [
    ('pub struct S { pub a: u64 }\npub fn f(s: S) -> S { s << 1 }',
     "operator '<<' does not accept S: .*user operator trait implementations are not implemented"),
])
def test_shifts_beyond_the_integer_types_keep_explicit_boundaries(source, boundary):
    with pytest.raises(RustError, match=boundary):
        compile_rust(source)
