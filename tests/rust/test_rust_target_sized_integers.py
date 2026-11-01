"""Target-sized primitive prerequisites for Rust arrays; not u64 type aliases.

Rust Reference, retrieved 2026-10-31:
https://doc.rust-lang.org/reference/types/numeric.html#machine-dependent-integer-types
https://doc.rust-lang.org/reference/expressions/operator-expr.html#numeric-cast
https://doc.rust-lang.org/reference/expressions/literal-expr.html#integer-literal-expressions
No32-bit Rust target or usize/isize foreign ABI is qualified by this domain.
"""
from dataclasses import replace
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.targets import resolve_target

PROGRAMS = ('target_sized_integers', 'target_sized_casts')


@pytest.mark.parametrize('program', PROGRAMS)
@pytest.mark.parametrize('target,magic', [
    ('x86_64-unknown-linux-gnu', b'\x7fELF'),
    ('arm64-apple-darwin', b'\xcf\xfa\xed\xfe'),
])
def test_target_sized_integer_width_comes_from_the_admitted_layout(program, target, magic):
    source = Path(__file__).with_name('programs').joinpath(program + '.rs').read_text()
    result = compile_rust(source, experimental_rustc_checked=True, target_triple=target)
    assert resolve_target(target).pointer_bits == 64
    assert 'p:64:64' in result.data_layout
    assert 'i64' in result.ir_text
    assert result.emit_object().startswith(magic)


@pytest.mark.parametrize('source,message', [
    ('fn f(x:usize)->u64{x}', 'type mismatch'),
    ('fn f(x:isize)->i64{x}', 'type mismatch'),
    ('fn f(x:usize,y:u64)->usize{x+y}', 'type mismatch'),
    ('fn f()->usize{18446744073709551616usize}', 'out of range'),
    ('fn f()->isize{9223372036854775808isize}', 'out of range'),
    ('fn f()->usize{-1usize}', 'signed integer'),
    ('extern "C" fn f(x:usize)->usize{x}', 'ABI'),
])
def test_target_sized_types_do_not_silently_alias_fixed_width_or_expand_foreign_abi(source, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid type reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(source, experimental_rustc_checked=True)


def test_unqualified_pointer_width_fails_before_semantic_work():
    with pytest.raises(RustError, match='pointer data model'):
        Checker([], pointer_bits=32)
    target = resolve_target('x86_64-unknown-linux-gnu')
    with pytest.raises(RustError, match='pointer data model'):
        _ = replace(target, data_layout=target.data_layout.replace('p:64:64', 'p:32:32')).pointer_bits


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'} and shutil.which('rustc') and shutil.which('cc') else 'requires actual development rustc and system linker on Linux x86-64')
def test_original_target_sized_program_has_actual_full_width_native_reference(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference, 'PROGRAMS', PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch)


def test_generic_instances_keep_pointer_sized_and_fixed_width_types_distinct():
    from pcc.frontends.rust.syntax import Parser
    parser = Parser('fn id<T>(x:T)->T{x} fn f()->usize{let a=id(1usize);let b=id(1u64);a}')
    functions = parser.parse()
    checker = Checker(functions, parser.records, parser.enums, parser.implementations)
    checker.check()
    entries = tuple(checker.function_generics.instances.entries.values())
    assert {entry.value.instance_arguments for entry in entries} == {('usize',), ('u64',)}
    assert len({entry.value.identity for entry in entries}) == 2
