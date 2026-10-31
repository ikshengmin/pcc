"""Bounded direct-call shared loans; NOT a complete Rust borrow checker.

Rust Reference chapters (retrieved 2026-10-31):
https://doc.rust-lang.org/reference/types/pointer.html#shared-references-
https://doc.rust-lang.org/reference/expressions/operator-expr.html#borrow-operators
https://doc.rust-lang.org/reference/expressions/method-call-expr.html
https://doc.rust-lang.org/reference/expressions.html#evaluation-order-of-operands
https://doc.rust-lang.org/reference/lifetime-elision.html#lifetime-elision-in-functions

Ledger feature: rust.references.direct-shared-call-loans.
"""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.test_rust_frontend import test_development_only_rustc_checks_the_same_source as _rustc
from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution as _native
from tests.rust.test_rust_targets import test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite as _cross

PROGRAMS = ('shared_scalar','shared_methods','shared_generic','shared_order','shared_partial','shared_reborrow','shared_explicit','shared_local_rebinding')
PRELUDE = 'struct R{x:u64} impl R{fn read(&self,n:u64)->u64{self.x+n}} fn take(r:R)->u64{r.x} '
INVALID_BODIES = [
    'let r=R{x:1};r.read(take(r));',
    'let mut r=R{x:1};r.read({r.x=2;0});',
    'let mut r=R{x:1};r.read({r=R{x:2};0});',
    'let mut r=R{x:1};r.read(if true {r.x=2;0}else{0});',
    'let mut r=R{x:1};r.read({while false {r.x=2;}0});',
    'let r=R{x:1};r.read({let r=r;0});',
]


@pytest.mark.parametrize('body', INVALID_BODIES)
def test_receiver_loan_stays_live_during_all_later_arguments(body, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('conflicting loan reached IR'))
    with pytest.raises(RustError,match='while borrowed'):
        compile_rust(PRELUDE+'fn f(){'+body+'}',experimental_rustc_checked=True)


@pytest.mark.parametrize('source,message', [
    ('fn f(){let x=1;let p=&x;}', 'direct shared-reference call argument'),
    ('fn f(x:&u64)->&u64{x}', 'reference returns'),
    ('fn read(x:&u64){} fn f(){read(&1);}', 'temporaries'),
    ('struct R{x:u64} impl R{fn read(&self){}} fn f(){R{x:1}.read();}', 'temporaries'),
    ('fn f(p:&mut u64){}', 'mutable references'),
    ('fn read(p:&u64){} fn f(){let mut x=1;read(&mut x);}', 'mutable borrowing'),
    ('struct R{x:u64} fn f(p:&R)->R{*p}', 'non-Copy'),
    ('struct R{x:u64} struct O{r:R} fn f(p:&O)->R{p.r}', 'non-Copy'),
    ('struct R<T>{x:T} impl<T> R<T>{fn take(&self)->T{self.x}}', 'non-Copy'),
    ('fn f<T>(p:&T)->T{*p}', 'non-Copy'),
    ('fn read(p:&u64){} fn f(){let r=1;read({&r});}', 'direct shared-reference'),
    ('fn f(p:&u64){let t=(p,);}', 'reference storage'),
    ('extern "C" fn f(p:&u64){}', 'foreign shared references'),
    ('struct R{x:u64} extern "C" fn f(p:&R){}', 'foreign shared references'),
    ('fn f(p:&&u64){}', 'nested references'),
    ('fn f(p:&()){}', 'unit and nested references'),
    ('fn f(){let x=1;*x;}', 'dereference requires'),
])
def test_reference_lifetime_alias_move_and_foreign_boundaries_fail_closed(source,message,monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('unsupported reference reached IR'))
    with pytest.raises(RustError,match=message):
        compile_rust(source,experimental_rustc_checked=True)


# These are the EXACT older unsupported inputs, moved into positive coverage;
# neither their source nor their existing failed history is rewritten.
PREVIOUS_UNIMPLEMENTED = [
    'fn f(x:&i32){}',
    'struct P{x:i32} fn f(p:&P){}',
    '#[repr(C)] struct P{x:bool} fn f(p:&P){}',
    '#[repr(C)] struct P{x:()} fn f(p:&P){}',
    'fn bad<T>(x:&T){}',
    'struct R{x:i64} impl R{fn f(&self){}}',
]


@pytest.mark.parametrize('source', PREVIOUS_UNIMPLEMENTED + [
    'fn read(p:&u64)->u64{*p} fn f()->u64{let x=42;read(&x)}',
    PRELUDE+'fn f()->u64{let mut r=R{x:1};let n=r.read(0);r.x=2;n+r.read(0)}',
    PRELUDE+'fn f()->u64{let r=R{x:1};r.read(r.read(0))}',
    'struct R{x:()} fn f(p:&R){p.x;}',
])
def test_readonly_private_references_and_closed_call_loans_are_admitted(source):
    compile_rust(source,experimental_rustc_checked=True)


@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine() in {'x86_64','AMD64'} else 'requires Linux x86-64 owned execution')
@pytest.mark.parametrize('name',PROGRAMS)
def test_original_shared_loan_program_full_width_owned_execution(name,tmp_path,monkeypatch):
    _native(name,tmp_path,monkeypatch)


@pytest.mark.parametrize('name',PROGRAMS)
def test_original_shared_loan_program_arm64_macho_cross_only(name,monkeypatch):
    _cross(name,monkeypatch)


@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development rustc unavailable')
@pytest.mark.parametrize('name',PROGRAMS)
def test_original_shared_loan_program_rustc_typechecks(name,tmp_path):
    _rustc(name,tmp_path)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine() in {'x86_64','AMD64'} and shutil.which('rustc') and shutil.which('cc') else 'requires Linux rustc/system linker oracle')
def test_original_shared_loan_programs_match_native_rustc(tmp_path,monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference,'PROGRAMS',PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path,monkeypatch)


@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development rustc unavailable')
@pytest.mark.parametrize('body',INVALID_BODIES)
def test_original_conflicting_loans_are_rejected_by_rustc(body,tmp_path):
    source=PRELUDE+'fn f(){'+body+'}'
    path=tmp_path/'original.rs';path.write_text(source)
    output=tmp_path/'reference.rmeta'
    command=[shutil.which('rustc'),'--edition=2021','--crate-type=lib','--emit=metadata',str(path),'-o',str(output)]
    result=subprocess.run(command,capture_output=True,timeout=15)
    (tmp_path/'reference.stdout').write_bytes(result.stdout)
    (tmp_path/'reference.stderr').write_bytes(result.stderr)
    (tmp_path/'rejection.json').write_text(json.dumps({'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'command':command,'exit_code':result.returncode,'stdout_sha256':hashlib.sha256(result.stdout).hexdigest(),'stderr_sha256':hashlib.sha256(result.stderr).hexdigest()},indent=2)+'\n')
    assert result.returncode!=0 and not output.exists()
    assert any(code in result.stderr for code in (b'E0505',b'E0506'))
    assert path.read_text()==source


# c416's unchanged original produced an actual rustc lifetime error. Keep it as
# a negative; shared_local_rebinding.rs is a separate, locally inferred binding.
INVALID_PARAMETER_REBINDING = Path(__file__).with_name('programs') / 'shared_rebinding.rs'


def test_exact_original_parameter_rebinding_requires_lifetime_constraints(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('unqualified parameter lifetime reached IR'))
    with pytest.raises(RustError,match='reference parameter reassignment requires lifetime constraints'):
        compile_rust(INVALID_PARAMETER_REBINDING.read_text(),experimental_rustc_checked=True,overflow_checks=False)


@pytest.mark.parametrize('source', [
    'fn f(mut p:&u64,q:&u64){p=q;}',
    'fn f<T>(mut p:&T,q:&T){p=q;}',
    'fn f(mut p:&u64){p=p;}',  # Valid Rust, explicitly unsupported until regions.
])
def test_parameter_reference_rebinding_has_an_explicit_conservative_boundary(source):
    with pytest.raises(RustError,match='reference parameter reassignment requires lifetime constraints'):
        compile_rust(source,experimental_rustc_checked=True)


@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development rustc unavailable')
def test_exact_original_parameter_rebinding_is_rejected_by_rustc(tmp_path):
    original=INVALID_PARAMETER_REBINDING.read_bytes()
    path=tmp_path/'original.rs';path.write_bytes(original)
    output=tmp_path/'reference.rmeta'
    command=[shutil.which('rustc'),'--edition=2021','--crate-type=lib','--emit=metadata',str(path),'-o',str(output)]
    result=subprocess.run(command,capture_output=True,timeout=15)
    (tmp_path/'reference.stdout').write_bytes(result.stdout)
    (tmp_path/'reference.stderr').write_bytes(result.stderr)
    (tmp_path/'rejection.json').write_text(json.dumps({'source_sha256':hashlib.sha256(original).hexdigest(),'command':command,'exit_code':result.returncode,'stdout_sha256':hashlib.sha256(result.stdout).hexdigest(),'stderr_sha256':hashlib.sha256(result.stderr).hexdigest()},indent=2)+'\n')
    assert result.returncode!=0 and not output.exists()
    assert b'lifetime may not live long enough' in result.stderr
    assert path.read_bytes()==original==INVALID_PARAMETER_REBINDING.read_bytes()
