"""The weak keyword `union` and the compound assignments `&=`, `|=`, `^=`, `/=`, `%=`.

Rust Reference keywords.html#weak-keywords: `union` is a keyword only where
it starts a union item (rustc is_union_item: before a non-reserved
identifier); elsewhere it names functions, methods, locals, fields and
modules. Union items themselves keep an explicit boundary.
Rust Reference expressions/operator-expr.html#compound-assignment-expressions:
`&=`, `|=` and `^=` on integers and on bool (core's BitAndAssign, BitOrAssign
and BitXorAssign for bool); `/=` and `%=` follow `/` and `%`, so until owned
panics exist their divisor must be a known nonzero constant (not signed -1).
Known `&=`, `|=`, `^=` results propagate in the owned arithmetic_overflow
lint; `/=` and `%=` end a basic block (the divisor check), as in rustc's MIR.
Every corpus row and program is original; verdicts and results were compared
with a local rustc 1.92.0 build (-O), which stays a labeled reference.
"""
import shutil
import subprocess

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function, run_rustc_str_function
from tests.rust.test_rust_shift_operators import rustc_verdict

# (name, original source, verdict of a rustc 1.92.0 build): 'panic' is its
# unconditional_panic lint, 'overflow' its arithmetic_overflow lint.
CORPUS = (
    ('fn-named-union', 'pub fn union() -> u32 { 1 }', 'ok'),
    ('local-named-union', 'pub fn f() -> u32 { let union = 3; union + 1 }', 'ok'),
    ('field-named-union', 'pub struct S { pub union: u32 }\npub fn f(s: S) -> u32 { s.union }', 'ok'),
    ('method-named-union', 'pub struct S { a: u32 }\nimpl S { pub fn union(&self, o: &S) -> u32 { self.a | o.a } }\n'
                           'pub fn f(a: S, b: S) -> u32 { a.union(&b) }', 'ok'),
    ('module-named-union', 'pub mod union { pub fn f() -> u32 { 1 } }\npub fn g() -> u32 { union::f() }', 'ok'),
    ('generic-fn-named-union', 'pub fn union<T>(t: T) -> T { t }', 'ok'),
    ('param-named-union', 'pub fn f(union: u32) -> u32 { union << 1 }', 'ok'),
    ('bitor-assign', 'pub fn f(mut x: u64, y: u64) -> u64 { x |= y; x }', 'ok'),
    ('xor-and-assign', 'pub fn f(mut x: u32) -> u32 { x ^= 0xFF; x &= 0xF0; x }', 'ok'),
    ('bool-bit-assign', 'pub fn f(mut a: bool, b: bool) -> bool { a &= b; a |= !b; a ^= true; a }', 'ok'),
    ('div-rem-assign', 'pub fn f(mut x: u32) -> u32 { x /= 3; x %= 2; x }', 'ok'),
    ('div-assign-zero', 'pub fn f(mut x: u32) -> u32 { x /= 0; x }', 'panic'),
    ('bitor-assign-mismatch', 'pub fn f(mut x: u32) -> u32 { x |= 1u64; x }', 'E0277,E0308'),
    ('bitor-assign-known', 'pub fn f() -> u32 { let mut a = 0xF0u32; a |= 0x0F; 4294967295u32 - 255 + a + 1 }',
     'overflow'),
    ('bitand-assign-known', 'pub fn f() -> u32 { let mut a = 0xFFu32; a &= 1; 4294967295u32 + a }', 'overflow'),
    ('bitand-assign-in-range', 'pub fn f() -> u32 { let mut a = 0xFFu32; a &= 0; 4294967295u32 + a }', 'ok'),
    ('div-assign-forgets', 'pub fn f() -> u32 { let mut a = 10u32; a /= 3; 4294967295u32 + a - a }', 'ok'),
    ('static-bitor-assign', 'pub static mut S: u32 = 0;\npub fn f() { unsafe { S |= 4; } }', 'ok'),
    ('deref-bitor-assign', 'pub fn f(w: &mut u64, o: &u64) { *w |= *o; }', 'ok'),
    ('bitor-assign-while-borrowed', 'pub fn f(mut x: u32) -> u32 { let r = &x; x |= 1; *r }', 'E0506'),
    ('immutable-bitor-assign', 'pub fn f(x: u32) -> u32 { x |= 1; x }', 'E0384'),
)
MESSAGES = {
    'panic': 'panic-capable division/remainder is unsupported',
    'E0277,E0308': 'type mismatch: expected u32, found u64',
    'overflow': r'this arithmetic operation will overflow \(rustc arithmetic_overflow\)',
    'E0506': r'because it is borrowed \(E0506\)',
    'E0384': "cannot assign to immutable binding 'x'",
}


def reference_verdict(stderr, returncode):
    if 'will panic at runtime' in stderr:
        return 'panic'
    return rustc_verdict(stderr, returncode)


@pytest.mark.parametrize('name,source,verdict', CORPUS, ids=[row[0] for row in CORPUS])
def test_verdicts_match_a_rustc_build_without_running_rustc(name, source, verdict, monkeypatch):
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
        assert reference_verdict(run.stderr, run.returncode) == verdict, (name, run.stderr)


# Each check sets one failure bit.
PROGRAM = '''\
pub struct Bits { word: u64 }

impl Bits {
    fn union(&mut self, other: &Bits) { self.word |= other.word; }
    fn intersect(&mut self, other: &Bits) { self.word &= other.word; }
    fn toggle(&mut self, other: &Bits) { self.word ^= other.word; }
}

fn union(a: u32, b: u32) -> u32 { a | b }
fn or_into(w: &mut u64, o: &u64) { *w |= *o; }
fn flags(mut a: bool, b: bool) -> u64 {
    a &= b;
    let x = a as u64;
    a |= !b;
    let y = a as u64;
    a ^= true;
    x | y << 1 | (a as u64) << 2
}
fn divide(mut x: u64, mut y: i64) -> u64 { x /= 3; x %= 1000; y /= -7; y %= 5; x ^ y as u64 }

pub fn answer() -> u64 {
    let mut bad = 0u64;
    let mut a = Bits { word: 0b1100 };
    let b = Bits { word: 0b1010 };
    let c = Bits { word: 0b0110 };
    a.union(&b);
    if a.word != 0b1110 { bad |= 1 << 0; }
    a.intersect(&b);
    if a.word != 0b1010 { bad |= 1 << 1; }
    a.toggle(&c);
    if a.word != 0b1100 { bad |= 1 << 2; }
    if union(0x0F, 0xF0) != 0xFF { bad |= 1 << 3; }
    let mut w = 0x10u64;
    let o = 0x01u64;
    or_into(&mut w, &o);
    if w != 0x11 { bad |= 1 << 4; }
    if flags(true, false) != 2 || flags(true, true) != 3 { bad |= 1 << 5; }
    if divide(1000000, -100) != 329 { bad |= 1 << 6; }
    if divide(7, 100) != 0xFFFF_FFFF_FFFF_FFFE { bad |= 1 << 7; }
    let union = 0x5u32;
    let mut m = union;
    m ^= 0xF;
    if m != 0xA { bad |= 1 << 8; }
    if bad == 0 { 0xfedcba9876543210 } else { bad }
}
'''


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE or (None if shutil.which('rustc') else
                                                  'development-only rustc is not installed'))
def test_program_runs_with_owned_admission_and_matches_rustc(tmp_path, monkeypatch):
    owned = run_owned_u64_function(PROGRAM, 'answer', tmp_path, monkeypatch, experimental_rustc_checked=False)
    reference = run_rustc_str_function(PROGRAM, 'answer', tmp_path, output='&{call}.to_le_bytes()')
    assert owned == int.from_bytes(reference, 'little') == 0xfedcba9876543210


@pytest.mark.parametrize('source,boundary', [
    ('union U { a: u32, b: u64 }', 'union items require untagged overlapping field layout'),
    ('pub union U { a: u32 }', 'union items require untagged overlapping field layout'),
    ('pub fn f(mut x: u32, d: u32) -> u32 { x /= d; x }', 'panic-capable division/remainder is unsupported'),
    ('pub fn f(mut x: i32) -> i32 { x %= -1; x }', 'panic-capable division/remainder is unsupported'),
])
def test_forms_beyond_the_owned_operators_keep_explicit_boundaries(source, boundary):
    with pytest.raises(RustError, match=boundary):
        compile_rust(source)
