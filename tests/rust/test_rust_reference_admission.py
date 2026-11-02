"""Public reference admission requires actual same-input external checking.

Rust Reference: https://doc.rust-lang.org/reference/types/pointer.html
https://doc.rust-lang.org/reference/expressions/operator-expr.html#borrow-operators
https://doc.rust-lang.org/reference/expressions/method-call-expr.html
An attestation flag is not a borrow check; internal model tests are not execution.
"""
from pathlib import Path
import os
import pytest

from pcc.frontends.rust import RustError, RustExternalCheckError, compile_rust, compile_rust_checked
from tests.rust.test_rust_certified_references import certify, toolchain

LEGACY_REFERENCE_PROGRAMS = (
    'shared_explicit', 'shared_generic', 'shared_local_rebinding', 'shared_methods',
    'shared_order', 'shared_partial', 'shared_reborrow', 'shared_scalar', 'inferred_shared',
)
REFERENCE_FORMS = (
    'fn unused(x:&u64){}',
    'fn unused(x:&mut u64){}',
    "fn unused<'a>(x:&'a u64){}",
    'struct R{x:u64} impl R{fn read(&self)->u64{self.x}} fn f(){let r=R{x:1};r.read();}',
    'struct R{x:u64} impl R{fn read(self:&Self)->u64{self.x}}',
    'fn unused<T>(x:&T){}',
    'fn f(){let x=1;let p=&x;let q=p;}',
    'fn f(){let x=1;if false {let p=&x;}}',
    'fn f(p:(&u64,)){}',
    'fn f(p:[&u64;2]){}',
    'struct R<T>{x:T} fn f(p:R<&u64>){}',
    'struct R{p:&u64}',
    'enum E{A(&u64)}',
    'fn f(){let x=1;*x;}',
    'extern "C"{fn f(p:&u64);}',
)


def forbid_lowering(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('uncertified reference reached IR'))


@pytest.mark.parametrize('source', REFERENCE_FORMS)
def test_bare_flag_cannot_authorize_any_reference_shape(source, monkeypatch):
    forbid_lowering(monkeypatch)
    with pytest.raises(RustError, match='reference-bearing Rust source requires an actual'):
        compile_rust(source, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', LEGACY_REFERENCE_PROGRAMS)
def test_unchanged_legacy_reference_originals_now_require_actual_check(name, monkeypatch):
    path=Path(__file__).with_name('programs')/(name+'.rs');original=path.read_bytes()
    forbid_lowering(monkeypatch)
    with pytest.raises(RustError, match='reference-bearing Rust source requires an actual'):
        compile_rust(original.decode(), experimental_rustc_checked=True)
    assert path.read_bytes()==original


@pytest.mark.parametrize('source', ['type Ref = &u64; fn f(x:Ref){}', 'fn f(x:*const u64){}'])
def test_unsupported_reference_alias_or_raw_pointer_never_becomes_an_escape(source, monkeypatch):
    forbid_lowering(monkeypatch)
    with pytest.raises(RustError):
        compile_rust(source, experimental_rustc_checked=True)


def test_value_bitwise_and_multiplication_are_not_reference_token_heuristics():
    source='// &self is only comment text\npub fn answer()->u64{let x=14u64;x&3|2*4}'
    result=compile_rust(source,experimental_rustc_checked=True)
    assert not result.externally_checked and result.emit_object().startswith(b'\x7fELF')


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if os.environ.get('PCC_RUSTC_INSTALL_RECEIPT') else 'actual audited rustc certification required; no attestation fallback')
@pytest.mark.parametrize('name', LEGACY_REFERENCE_PROGRAMS)
def test_checked_legacy_readonly_originals_bypass_partial_move_admission(name,tmp_path,monkeypatch):
    path=Path(__file__).with_name('programs')/(name+'.rs');original=path.read_bytes()
    checked=certify(path,tmp_path/'certifier')
    from pcc.frontends.rust.moves import MoveChecker
    monkeypatch.setattr(MoveChecker,'check',lambda *a:pytest.fail('checked source used partial borrow gate'))
    monkeypatch.setattr(MoveChecker,'check_function',lambda *a:pytest.fail('checked generic body used partial borrow gate'))
    result=compile_rust(original.decode(),rustc_check=checked)
    assert result.externally_checked and result.emit_object().startswith(b'\x7fELF')
    assert path.read_bytes()==original


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if os.environ.get('PCC_RUSTC_INSTALL_RECEIPT') else 'actual audited rustc certification required')
def test_actual_checker_rejects_unchanged_noncopy_move_before_reference_lowering(tmp_path,monkeypatch):
    source='struct R{x:u64} fn read(p:&R)->u64{p.x} fn f(){let r=R{x:1};let moved=r;read(&r);}'
    forbid_lowering(monkeypatch)
    with pytest.raises(RustExternalCheckError,match='E0382'):
        compile_rust_checked(source,toolchain=toolchain(),evidence_directory=tmp_path/'certifier')
    assert not(tmp_path/'certifier/checked.rmeta').exists()


def test_semantic_reference_synthesis_cannot_bypass_the_second_guard(monkeypatch):
    from pcc.frontends.rust.semantics import Checker
    from pcc.frontends.rust.types import SharedReference
    original=Checker.check
    def synthesize(self):
        original(self)
        self.functions[0].result=SharedReference('u64')
    monkeypatch.setattr(Checker,'check',synthesize)
    forbid_lowering(monkeypatch)
    with pytest.raises(RustError,match='reference-bearing Rust source requires an actual'):
        compile_rust('fn f()->u64{1}',experimental_rustc_checked=True)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if os.environ.get('PCC_RUSTC_INSTALL_RECEIPT') else 'actual audited rustc certification required')
def test_actual_minted_token_cannot_change_target_or_overflow_options(tmp_path,monkeypatch):
    path=Path(__file__).with_name('programs')/'shared_scalar.rs';source=path.read_text()
    checked=certify(path,tmp_path/'certifier');forbid_lowering(monkeypatch)
    with pytest.raises(RustError,match='target'):
        compile_rust(source,rustc_check=checked,target_triple='aarch64-apple-darwin')
    with pytest.raises(RustError,match='overflow'):
        compile_rust(source,rustc_check=checked,overflow_checks=True)
