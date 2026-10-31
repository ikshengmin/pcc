"""Regression-only addition: declared payload types with diverging operands."""
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution as _native
from tests.rust.test_rust_frontend import test_development_only_rustc_checks_the_same_source as _rustc
from tests.rust.test_rust_targets import test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite as _cross


@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine() in {'x86_64','AMD64'} else 'requires Linux x86-64 owned execution')
def test_constructor_return_skips_later_operands_in_complete_original_program(tmp_path,monkeypatch):
    _native('payload_early_return',tmp_path,monkeypatch)


def test_constructor_return_original_program_cross_emits_macho(monkeypatch):
    _cross('payload_early_return',monkeypatch)


@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_development_only_rustc_checks_original_constructor_return_program(tmp_path):
    _rustc('payload_early_return',tmp_path)


def test_syntactically_complete_drop_implementation_is_not_silently_ignored():
    source='enum E{Value(i64)} impl Drop for E{fn drop(&mut self){}} fn f(){let e=E::Value(7);}'
    with pytest.raises(RustError,match="expected 'fn', found 'impl'"):
        compile_rust(source,experimental_rustc_checked=True)
