"""Owned admission of reference-free Rust and the arithmetic_overflow lint.

Reference-free input is admitted by PCC's own checks with no rustc and no
caller attestation (pcc/frontends/rust/README.md, "Replacing the rustc
admission gate", stage 2): types, mutability, moves (in generic bodies too and
in every admission mode), match-guard borrows, exhaustiveness, and rustc's
deny-by-default arithmetic_overflow lint, which a real rustc build applies but
a metadata check never runs. pcc/frontends/rust/known_panics.py follows that
lint's reach (https://doc.rust-lang.org/rustc/lints/listing/deny-by-default.html#arithmetic-overflow):
locals assigned once stay known, locals assigned more than once are known
within one basic block, tuple/struct fields, casts and match bindings
propagate, and known conditions prune unreachable branches and arms. The
corpus below is original; each verdict was compared with a local rustc 1.92.0
build (-O), which stays a labeled reference.
"""
import shutil
import subprocess

import pytest

from pcc.frontends.rust import RustError, compile_rust

# (name, original source, verdict of a rustc 1.92.0 build): 'overflow' is its
# "this arithmetic operation will overflow" error.
CORPUS = (
    ('ovf-imm-u64', 'pub fn f() -> u64 { let x: u64 = 18446744073709551615; x + 1 }', 'overflow'),
    ('ovf-mut-u64', 'pub fn f() -> u64 { let mut x: u64 = 18446744073709551600; x = x + 10; x + 10 }', 'overflow'),
    ('ovf-literal-u64', 'pub fn f() -> u64 { 18446744073709551615u64 + 1 }', 'overflow'),
    ('ovf-i32-mul', 'pub fn f() -> i32 { let a: i32 = 65536; a * 65536 }', 'overflow'),
    ('ovf-i64-neg', 'pub fn f() -> i64 { let x: i64 = -9223372036854775807 - 1; -x }', 'overflow'),
    ('ovf-sub-u32', 'pub fn f() -> u32 { let a: u32 = 1; a - 2 }', 'overflow'),
    ('ovf-through-call', 'fn id(v: u64) -> u64 { v } pub fn f() -> u64 { let x = id(18446744073709551615); x + 1 }', 'ok'),
    ('ovf-in-loop', 'pub fn f() -> u64 { let mut x: u64 = 18446744073709551600; let mut i = 0; while i < 3 { x = x + 10; i += 1; } x }', 'ok'),
    ('ovf-after-branch', 'pub fn f(c: bool) -> u64 { let mut x: u64 = 18446744073709551615; if c { x = 0; } x + 1 }', 'ok'),
    ('ovf-tuple-field', 'pub fn f() -> u64 { let t = (18446744073709551615u64, 1u64); t.0 + t.1 }', 'overflow'),
    ('ovf-cast', 'pub fn f() -> u32 { let x: u64 = 4294967296; (x as u32) + 4294967295 }', 'ok'),
    ('ovf-compound', 'pub fn f() -> u64 { let mut x: u64 = 18446744073709551615; x += 1; x }', 'overflow'),
    ('mut-across-call', 'fn g() -> u64 { 1 } pub fn f() -> u64 { let mut x: u64 = 1; x = 18446744073709551615; let y = g(); x + y }', 'ok'),
    ('mut-across-index', 'pub fn f(a: [u64; 2]) -> u64 { let mut x: u64 = 1; x = 18446744073709551615; let y = a[0]; x + 1 + y }', 'ok'),
    ('once-across-call', 'fn g() -> u64 { 1 } pub fn f() -> u64 { let x: u64 = 18446744073709551615; let y = g(); x + y }', 'ok'),
    ('once-in-branch', 'pub fn f(c: bool) -> u64 { let x: u64 = 18446744073709551615; if c { x + 1 } else { 0 } }', 'overflow'),
    ('mut-in-branch', 'pub fn f(c: bool) -> u64 { let mut x: u64 = 1; x = 18446744073709551615; if c { x + 1 } else { 0 } }', 'ok'),
    ('dead-branch', 'pub fn f() -> u64 { let x: u64 = 18446744073709551615; if false { x + 1 } else { 0 } }', 'ok'),
    ('struct-field', 'pub struct S { pub a: u64 } pub fn f() -> u64 { let s = S { a: 18446744073709551615 }; s.a + 1 }', 'overflow'),
    ('param-unknown', 'pub fn f(x: u64) -> u64 { x + 18446744073709551615 }', 'ok'),
    ('assign-op-chain', 'pub fn f() -> i32 { let mut x: i32 = 2147483000; x += 600; x += 100; x }', 'overflow'),
    ('mut-after-loop', 'pub fn f(n: u64) -> u64 { let mut x: u64 = 1; let mut i = 0; while i < n { i += 1; } x = 18446744073709551615; x + 1 }', 'overflow'),
    ('once-in-loop', 'pub fn f(n: u64) -> u64 { let x: u64 = 18446744073709551615; let mut i = 0; let mut t = 0; while i < n { t = x + 1; i += 1; } t }', 'overflow'),
    ('cast-then-add', 'pub fn f() -> i32 { let x: i64 = 2147483647; (x as i32) + 1 }', 'overflow'),
    ('match-binding', 'pub fn f() -> u64 { let t = (18446744073709551615u64, 0u64); match t { (a, _) => a + 1 } }', 'overflow'),
    ('block-tail', 'pub fn f() -> u64 { let x = { 18446744073709551615u64 }; x + 1 }', 'overflow'),
    ('block-tail-stmt', 'pub fn f() -> u64 { let x = { let y: u64 = 18446744073709551615; y }; x + 1 }', 'overflow'),
    ('known-local-condition', 'pub fn f() -> u64 { let c = false; let x: u64 = 18446744073709551615; if c { x + 1 } else { 0 } }', 'ok'),
    ('known-match-unreachable', 'pub fn f() -> u64 { let v: u64 = 1; let x: u64 = 18446744073709551615; match v { 1 => 0, _ => x + 1 } }', 'ok'),
    ('while-false', 'pub fn f() -> u64 { let x: u64 = 18446744073709551615; let mut t = 0; while false { t = x + 1; } t }', 'ok'),
    ('generic-concrete', 'pub fn f<T>(_t: T) -> u64 { let x: u64 = 18446744073709551615; x + 1 }', 'overflow'),
    ('usize-ovf', 'pub fn f() -> usize { let x: usize = 18446744073709551615; x + 1 }', 'overflow'),
    ('i32-sub-neg', 'pub fn f() -> i32 { let a: i32 = -2147483647; a - 2 }', 'overflow'),
    ('no-ovf-exact', 'pub fn f() -> u64 { let x: u64 = 18446744073709551614; x + 1 }', 'ok'),
    ('shadow', 'pub fn f() -> u64 { let x: u64 = 18446744073709551615; let x: u64 = 0; x + 1 }', 'ok'),
    ('assign-then-call-arg', 'fn g(v: u64) -> u64 { v } pub fn f() -> u64 { let mut x: u64 = 0; x = 18446744073709551615; g(x + 1) }', 'overflow'),
    # A local whose address is taken is never propagated (CanConstProp).
    ('shared-borrowed-local', 'pub fn f() -> u64 { let x: u64 = 18446744073709551615; let r = &x; x + 1 }', 'ok'),
    ('borrowed-after-use', 'pub fn f() -> u64 { let x: u64 = 18446744073709551615; let v = x + 1; let r = &x; v + *r }', 'ok'),
    ('borrow-in-dead-branch', 'pub fn f(c: bool) -> u64 { let x: u64 = 18446744073709551615; if c { let r = &x; } x + 1 }', 'ok'),
    ('guard-fake-borrow', 'pub fn f() -> u64 { let x: u64 = 18446744073709551615; match x { v if v > 0 => x + 1, _ => 0 } }', 'ok'),
    ('unguarded-match-propagates', 'pub fn f() -> u64 { let x: u64 = 18446744073709551615; match x { 0 => 0, _ => x + 1 } }', 'overflow'),
    ('borrow-through-reference', 'pub fn f(p: &u64) -> u64 { let x: u64 = 18446744073709551615; let r = &*p; x + *r }', 'ok'),
    ('autoref-receiver', 'pub struct S { pub a: u64 } impl S { pub fn g(&self) -> u64 { self.a } } pub fn f() -> u64 { let s = S { a: 18446744073709551615 }; let v = s.g(); s.a + 1 + v }', 'ok'),
    ('field-root-not-borrowed', 'pub struct S { pub a: u64, pub b: u64 } pub fn f() -> u64 { let s = S { a: 18446744073709551615, b: 1 }; s.a + 1 }', 'overflow'),
)


@pytest.mark.parametrize('name,source,verdict', CORPUS, ids=[row[0] for row in CORPUS])
def test_overflow_lint_matches_a_rustc_build_without_running_rustc(name, source, verdict, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('admission ran an external tool'))
    if verdict == 'ok':
        compile_rust(source)
    else:
        with pytest.raises(RustError, match='this arithmetic operation will overflow|constant integer overflow'):
            compile_rust(source)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_builds_give_the_same_verdicts(tmp_path):
    for index, (name, source, verdict) in enumerate(CORPUS):
        path = tmp_path / f'case{index}.rs'
        path.write_text(source + '\n')
        run = subprocess.run([shutil.which('rustc'), '--crate-type=lib', '--edition=2021', '--emit=obj', '-O',
                              '-A', 'dead_code', '-A', 'unused', str(path), '-o', str(tmp_path / f'case{index}.o')],
                             capture_output=True, text=True, timeout=60)
        overflow = 'this arithmetic operation will overflow' in run.stderr
        assert (overflow, run.returncode != 0) == ((verdict == 'overflow'),) * 2, (name, run.stderr)


def test_reference_free_programs_compile_with_no_rustc_and_no_attestation():
    result = compile_rust('pub struct P(pub u64, pub u64); pub fn f(p: P) -> u64 { p.0 + p.1 }')
    assert result.externally_checked is False and 'f' in result.symbols
    assert compile_rust('pub fn f() -> u64 { 1 }', experimental_rustc_checked=True).symbols


def test_references_and_lifetimes_are_owned_and_several_data_lifetimes_need_the_check():
    # Stages 4-6 own loans, signature regions and one data lifetime; several
    # lifetime parameters on a data type are still gated.
    assert not compile_rust('pub fn f(x: &u64) -> u64 { *x }').externally_checked
    assert not compile_rust('pub fn f(x: &mut u64) -> u64 { *x }').externally_checked
    assert not compile_rust("pub fn f<'a>(x: &'a u64) -> &'a u64 { x }").externally_checked
    assert not compile_rust("pub struct W<'a> { pub r: &'a u64 }").externally_checked
    with pytest.raises(RustError, match='several lifetime parameters'):
        compile_rust("pub struct W<'a, 'b> { pub r: &'a u64, pub s: &'b u64 }")
