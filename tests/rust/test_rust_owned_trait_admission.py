"""Owned trait admission: marker bounds, local trait bounds, defaults, associated types.

Stage 7 of pcc/frontends/rust/README.md, "Replacing the rustc admission gate
with owned checking": explicit standard marker bounds (Copy, Sized, ?Sized),
local trait bounds and where clauses, supertraits, default trait methods and
associated types are admitted by PCC's own obligations, with no rustc and no
caller attestation. A supplied actual check still has to match the selected
edition. The trait diagnostics carry rustc's codes (E0034, E0046, E0053,
E0119, E0201, E0276, E0277, E0404, E0407, E0428, E0437, E0599).

Evidence here: every originally gated example program runs with owned
compile, assemble and link and matches rustc; every invalid row the trait test
modules list (with the rustc code they were written against) is rejected
without rustc, with that code whenever PCC's message names one; and an
original corpus matches a rustc 1.92.0 build, rustc staying a labeled
reference.
"""
import importlib
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function, run_rustc_str_function

# Example programs formerly admitted only after an actual rustc check.
PROGRAMS = (
    'impl_bound_dispatch', 'impl_bound_mutable', 'impl_bound_result', 'inherent_local_dispatch',
    'inherent_local_mutable', 'inherent_local_result', 'inherent_local_shadow', 'local_bound_dispatch',
    'local_bound_marker_shadow', 'local_bound_method_inference', 'local_bound_mutable',
    'local_bound_qualified', 'local_bound_supertraits', 'owner_where_dispatch', 'owner_where_mutable',
    'owner_where_precedence', 'owner_where_result', 'trait_local_marker_names', 'trait_local_mutable',
    'trait_local_nested_dispatch', 'trait_local_result', 'trait_local_weaker_identity',
    'trait_where_dispatch', 'trait_where_markers', 'trait_where_receivers', 'trait_where_result',
    'trait_where_signature', 'where_local_dispatch', 'where_mutable', 'where_shadow', 'impl_bound_copy',
    'impl_bound_lookup_order', 'inherent_where_markers', 'method_marker_explicit_sized',
    'method_marker_forward_result', 'method_marker_owner_prefix', 'method_marker_receivers',
    'method_marker_shadow', 'owner_where_copy', 'standard_copy_forwarding', 'standard_copy_values',
    'standard_marker_module', 'standard_marker_reference', 'standard_marker_shadow',
    'trait_marker_concrete_owner', 'trait_marker_forward_result', 'trait_marker_identity',
    'trait_marker_receivers', 'trait_marker_shadow', 'trait_weaker_identity', 'trait_weaker_receivers',
    'trait_weaker_sized', 'where_copy_inference', 'where_sized_order', 'default_associated',
    'default_associated_inherited', 'default_bound_dispatch', 'default_generic', 'default_inherent_shadow',
    'default_mutable', 'default_override', 'default_parameter_dispatch', 'default_recursion',
    'default_static', 'default_supertraits', 'default_unsized_self', 'self_sized_by_value',
    'self_sized_generic_caller', 'self_sized_nested', 'self_sized_result', 'associated_type_concrete_owners',
    'associated_type_diamond', 'associated_type_generic_method', 'associated_type_hierarchy_names',
    'associated_type_inherited', 'associated_type_relaxed', 'associated_type_relaxed_parameter',
    'associated_type_relaxed_return', 'associated_type_self', 'associated_type_trait_identity',
    'associated_type_values',
)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE or (None if shutil.which('rustc') else
                                                  'development-only rustc is not installed'))
@pytest.mark.parametrize('name', PROGRAMS)
def test_trait_programs_run_with_owned_admission_and_match_rustc(name, tmp_path, monkeypatch):
    source = Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()
    owned = run_owned_u64_function(source, 'answer', tmp_path, monkeypatch, experimental_rustc_checked=False)
    reference = run_rustc_str_function(source, 'answer', tmp_path, output='&{call}.to_le_bytes()')
    assert owned == int.from_bytes(reference, 'little')


MODULES = (
    'traits', 'trait_local_contracts', 'trait_marker_bounds', 'trait_weaker_contracts', 'trait_where_contracts',
    'standard_markers', 'method_marker_bounds', 'default_methods', 'associated_types', 'where_bounds',
    'owner_where_bounds', 'inherent_impl_bounds', 'inherent_local_bounds', 'supertraits', 'local_trait_bounds',
    'self_sized_predicates', 'trait_bound_arguments',
)


def invalid_rows():
    """(id, source, rustc code) of every invalid row the trait test modules list."""
    rows, seen = [], set()
    for module in MODULES:
        for attribute, value in vars(importlib.import_module('tests.rust.test_rust_' + module)).items():
            if not isinstance(value, (tuple, list)):
                continue
            for item in value:
                if (isinstance(item, tuple) and len(item) in (3, 4) and all(isinstance(x, str) for x in item)
                        and re.fullmatch(r'E\d{4}', item[-1]) and item[1] not in seen):
                    seen.add(item[1])
                    rows.append((f'{module}.{attribute}.{item[0]}', item[1], item[-1]))
    return rows


INVALID = invalid_rows()


def test_the_invalid_rows_are_collected():
    assert len(INVALID) > 200


@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_listed_invalid_trait_programs_are_rejected_without_rustc(name, source, code, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('owned admission ran a tool'))
    with pytest.raises(RustError) as caught:
        compile_rust(source)
    named = re.search(r'\((?:rustc )?(E\d{4})\)', str(caught.value))
    assert named is None or named.group(1) == code, str(caught.value)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_reports_the_listed_codes(tmp_path):
    for index, (name, source, code) in enumerate(INVALID):
        path = tmp_path / f'case{index}.rs'
        path.write_text(source + '\n')
        run = subprocess.run([shutil.which('rustc'), '--crate-type=lib', '--edition=2021', '--emit=metadata',
                              '-A', 'warnings', str(path), '-o', str(tmp_path / f'case{index}.rmeta')],
                             capture_output=True, text=True, timeout=60)
        assert run.returncode != 0 and code in re.findall(r'error\[(E\d+)\]', run.stderr), (name, run.stderr)


S = 'pub struct S { pub a: u64 } '
T = 'pub struct T { pub b: u64 } '
A = 'pub trait Area { fn area(&self) -> u64; fn twice(&self) -> u64 { self.area() * 2 } } '

# (name, original source, verdict of a rustc 1.92.0 build)
CORPUS = (
    ('copy-bound-twice', 'pub fn dup<T: Copy>(t: T) -> (T, T) { (t, t) }', 'ok'),
    ('no-copy-bound-twice', 'pub fn dup<T>(t: T) -> (T, T) { (t, t) }', 'E0382'),
    ('copy-bound-call-noncopy', S + 'pub fn dup<T: Copy>(t: T) -> (T, T) { (t, t) } pub fn f() { let x = dup(S { a: 1 }); }', 'E0277'),
    ('copy-bound-call-copy', 'pub fn dup<T: Copy>(t: T) -> (T, T) { (t, t) } pub fn f() -> u64 { let (a, b) = dup(3u64); a + b }', 'ok'),
    ('where-copy', 'pub fn dup<T>(t: T) -> (T, T) where T: Copy { (t, t) }', 'ok'),
    ('sized-relaxed-by-value', 'pub fn f<T: ?Sized>(t: T) {}', 'E0277'),
    ('sized-relaxed-by-ref', 'pub fn f<T: ?Sized>(t: &T) -> u64 { 1 }', 'ok'),
    ('derive-copy-struct-to-bound', '#[derive(Clone, Copy)] pub struct P { pub x: u64 } pub fn dup<T: Copy>(t: T) -> (T, T) { (t, t) } pub fn f() -> u64 { let (a, b) = dup(P { x: 2 }); a.x + b.x }', 'ok'),
    ('trait-impl-call', S + A + 'impl Area for S { fn area(&self) -> u64 { self.a } } pub fn f() -> u64 { let s = S { a: 3 }; s.area() + s.twice() }', 'ok'),
    ('trait-generic-bound', S + A + 'impl Area for S { fn area(&self) -> u64 { self.a } } pub fn g<X: Area>(x: &X) -> u64 { x.twice() } pub fn f() -> u64 { let s = S { a: 2 }; g(&s) }', 'ok'),
    ('trait-bound-missing-impl', S + T + A + 'impl Area for S { fn area(&self) -> u64 { self.a } } pub fn g<X: Area>(x: &X) -> u64 { x.area() } pub fn f() -> u64 { let t = T { b: 2 }; g(&t) }', 'E0277'),
    ('trait-missing-method', S + A + 'impl Area for S { }', 'E0046'),
    ('trait-extra-method', S + A + 'impl Area for S { fn area(&self) -> u64 { 1 } fn other(&self) -> u64 { 2 } }', 'E0407'),
    ('trait-wrong-signature', S + A + 'impl Area for S { fn area(&self) -> u32 { 1 } }', 'E0053'),
    ('trait-override-default', S + A + 'impl Area for S { fn area(&self) -> u64 { 1 } fn twice(&self) -> u64 { 7 } } pub fn f() -> u64 { let s = S { a: 0 }; s.twice() }', 'ok'),
    ('trait-unbounded-generic-call', A + 'pub fn g<X>(x: &X) -> u64 { x.area() }', 'E0599'),
    ('trait-static-method', 'pub trait Make { fn make() -> Self; } pub struct S { pub a: u64 } impl Make for S { fn make() -> Self { S { a: 4 } } } pub fn build<X: Make>() -> X { X::make() } pub fn f() -> u64 { let s: S = build(); s.a }', 'ok'),
    ('trait-ambiguous', S + 'pub trait P { fn v(&self) -> u64; } pub trait Q { fn v(&self) -> u64; } impl P for S { fn v(&self) -> u64 { 1 } } impl Q for S { fn v(&self) -> u64 { 2 } } pub fn f() -> u64 { let s = S { a: 0 }; s.v() }', 'E0034'),
    ('trait-inherent-precedence', S + 'pub trait P { fn v(&self) -> u64; } impl P for S { fn v(&self) -> u64 { 1 } } impl S { pub fn v(&self) -> u64 { 2 } } pub fn f() -> u64 { let s = S { a: 0 }; s.v() }', 'ok'),
    ('trait-conflicting-impls', S + A + 'impl Area for S { fn area(&self) -> u64 { 1 } } impl Area for S { fn area(&self) -> u64 { 2 } }', 'E0119'),
    ('trait-mut-self', S + 'pub trait Bump { fn bump(&mut self); } impl Bump for S { fn bump(&mut self) { self.a += 1; } } pub fn go<X: Bump>(x: &mut X) { x.bump(); x.bump(); } pub fn f() -> u64 { let mut s = S { a: 0 }; go(&mut s); s.a }', 'ok'),
    ('trait-mut-self-immutable', S + 'pub trait Bump { fn bump(&mut self); } impl Bump for S { fn bump(&mut self) { self.a += 1; } } pub fn f() -> u64 { let s = S { a: 0 }; s.bump(); s.a }', 'E0596'),
    ('trait-by-value-self', S + 'pub trait Eat { fn eat(self) -> u64; } impl Eat for S { fn eat(self) -> u64 { self.a } } pub fn f() -> u64 { let s = S { a: 2 }; let v = s.eat(); v + s.a }', 'E0382'),
    ('supertrait-call', S + 'pub trait Base { fn base(&self) -> u64; } pub trait Derived: Base { fn derived(&self) -> u64 { self.base() + 1 } } impl Base for S { fn base(&self) -> u64 { self.a } } impl Derived for S {} pub fn g<X: Derived>(x: &X) -> u64 { x.base() + x.derived() } pub fn f() -> u64 { let s = S { a: 1 }; g(&s) }', 'ok'),
    ('supertrait-missing', S + 'pub trait Base { fn base(&self) -> u64; } pub trait Derived: Base {} impl Derived for S {}', 'E0277'),
    ('assoc-type-missing', S + 'pub trait Get { type Out; fn get(&self) -> Self::Out; } impl Get for S { fn get(&self) -> u64 { self.a } }', 'E0046'),
    ('assoc-type-mismatch', S + 'pub trait Get { type Out; fn get(&self) -> Self::Out; } impl Get for S { type Out = u32; fn get(&self) -> u64 { self.a } }', 'E0053'),
    ('assoc-type-used-wrong', S + 'pub trait Get { type Out; fn get(&self) -> Self::Out; } impl Get for S { type Out = u64; fn get(&self) -> u64 { self.a } } pub fn f() -> u32 { let s = S { a: 1 }; s.get() }', 'E0308'),
    ('generic-struct-bounded-impl-unmet', 'pub struct Box1<T> { pub v: T } pub trait Val { fn val(&self) -> u64; } impl<T: Val> Box1<T> { pub fn get(&self) -> u64 { self.v.val() } } pub fn f() -> u64 { Box1 { v: true }.get() }', 'E0599'),
    ('where-local-bound', S + A + 'impl Area for S { fn area(&self) -> u64 { self.a } } pub fn g<X>(x: &X) -> u64 where X: Area { x.area() } pub fn f() -> u64 { let s = S { a: 6 }; g(&s) }', 'ok'),
    ('bound-moves-generic', A + 'pub fn g<X: Area>(x: X) -> u64 { let y = x; y.area() + x.area() }', 'E0382'),
    ('default-calls-required', 'pub trait Counter { fn count(&self) -> u64; fn double(&self) -> u64 { self.count() + self.count() } } pub struct C { pub n: u64 } impl Counter for C { fn count(&self) -> u64 { self.n } } pub fn f() -> u64 { let c = C { n: 21 }; c.double() }', 'ok'),
)
MESSAGES = {
    'E0034': r'\(rustc E0034\)', 'E0046': r'\(rustc E0046\)', 'E0053': r'\(rustc E0053\)',
    'E0119': r'\(rustc E0119\)', 'E0277': r'\(rustc E0277\)', 'E0407': r'\(rustc E0407\)',
    'E0599': r'\(rustc E0599\)', 'E0308': 'type mismatch', 'E0382': 'use of moved or possibly moved value',
    'E0596': 'cannot borrow an immutable receiver as mutable',
}


@pytest.mark.parametrize('name,source,verdict', CORPUS, ids=[row[0] for row in CORPUS])
def test_trait_corpus_matches_a_rustc_build_without_running_rustc(name, source, verdict, monkeypatch):
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
        codes = sorted(set(re.findall(r'error\[(E\d+)\]', run.stderr)))
        assert (run.returncode == 0, codes) == ((True, []) if verdict == 'ok' else (False, [verdict])), \
            (name, run.stderr)


@pytest.mark.parametrize('source,boundary', [
    (S + A + 'impl Area for S { fn area(&self) -> u64 { self.a } } pub fn f() -> u64 { let s = S { a: 5 }; Area::area(&s) + <S as Area>::area(&s) }', 'needs Self inferred from the arguments, which is not implemented'),
    (S + 'pub trait Get { type Out; fn get(&self) -> Self::Out; } impl Get for S { type Out = u64; fn get(&self) -> u64 { self.a } } pub fn g<X: Get>(x: &X) -> X::Out { x.get() } pub fn f() -> u64 { let s = S { a: 9 }; g(&s) }', 'require projection normalization, which is not implemented'),
    ('pub struct Box1<T> { pub v: T } pub trait Val { fn val(&self) -> u64; } impl Val for u64 { fn val(&self) -> u64 { *self } } impl<T: Val> Box1<T> { pub fn get(&self) -> u64 { self.v.val() } } pub fn f() -> u64 { Box1 { v: 3u64 }.get() }', 'trait impl requires a concrete local nominal struct or enum'),
    ('pub trait Half { fn half(&self) -> u64; } impl Half for u64 { fn half(&self) -> u64 { *self / 2 } } pub fn f() -> u64 { 8u64.half() }', 'trait impl requires a concrete local nominal struct or enum'),
    # Inline attributes keep their own gate (stage 8).
    ('pub trait T { #[inline] fn f(&self) -> u64 { 1 } }', 'inline attribute admission requires an actual'),
])
def test_trait_shapes_beyond_owned_admission_keep_explicit_boundaries(source, boundary):
    with pytest.raises(RustError, match=boundary):
        compile_rust(source)
