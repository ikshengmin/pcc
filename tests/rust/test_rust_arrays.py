"""Fixed arrays preserve element identity/value semantics, not tuple flattening.

Rust Reference, retrieved2026-10-31:
https://doc.rust-lang.org/reference/types/array.html
https://doc.rust-lang.org/reference/expressions/array-expr.html
https://doc.rust-lang.org/reference/types/numeric.html#machine-dependent-integer-types
https://doc.rust-lang.org/reference/expressions.html#evaluation-order-of-operands
https://doc.rust-lang.org/reference/expressions.html#moved-and-copied-types
Dynamic index/panic, slices, const generics, destructuring and Drop remain gaps.
"""
import hashlib
import os
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import source_has_references

PLAIN = ('arrays_values', 'arrays_generic_moves', 'arrays_nested_fields',
         'arrays_zero_and_unit', 'arrays_reinitialize')
BORROWED = ('arrays_repeat_effects', 'arrays_borrowed_assignment', 'arrays_index_temporary',
            'arrays_argument_snapshot', 'arrays_noncopy_repeats', 'arrays_indexed_borrows', 'arrays_len_effects')
PROGRAMS = PLAIN + BORROWED


def compile_source(source, **kwargs):
    return compile_rust(source, experimental_rustc_checked=True, **kwargs)


@pytest.mark.parametrize('name', PLAIN)
@pytest.mark.parametrize('target,magic', [('x86_64-unknown-linux-gnu', b'\x7fELF'),
                                         ('arm64-apple-darwin', b'\xcf\xfa\xed\xfe')])
def test_original_nonborrow_arrays_have_typed_owned_objects(name, target, magic):
    source = Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()
    assert compile_source(source, target_triple=target).emit_object().startswith(magic)


def test_array_private_function_type_is_real_ir_array_not_flattened_tuple():
    from pcc.ir.compat import ir
    source = 'fn echo(a:[u64;3])->[u64;3]{a}'
    result = compile_source(source)
    fn = next(fn for fn in result.module.functions if fn.name == result.symbols['echo'])
    return_type = fn.function_type.return_type
    assert isinstance(return_type, ir.ArrayType)
    assert return_type.count == 3 and return_type.element.width == 64
    assert str(return_type) == '[3 x i64]'


@pytest.mark.parametrize('source,message', [
    ('fn f(){let a:[u64;2]=[1];}', 'type mismatch'),
    ('fn f(){let a=[1,true];}', 'type mismatch'),
    ('fn f(){let a=[];}', 'cannot infer empty array'),
    ('fn f(){let mut a=[];a=[a;0];}', 'infinite inferred'),
    ('fn f(){let a=[1,2];a[2];}', 'out of bounds'),
    ('fn f(){let a=[1,2];a[0u64];}', 'type mismatch'),
    ('fn f(a:[u64;0]){a[0];}', 'out of bounds'),
    ('fn f(a:[u64;3],i:usize)->u64{a[i]}', 'closed scalar constant'),
    ('fn f(a:[u64;3])->u64{a[{let x=0;x}]}', 'closed scalar constant'),
    ('fn f(a:[u64;3u64]){}', 'type mismatch'),
    ('fn f(a:[u64;4097]){}', 'implementation limit'),
    ('fn f(a:[[u64;4096];4096]){}', 'storage shape'),
    ('fn f(a:[u64]){}', 'slice types'),
    ('fn f<const N:usize>(a:[u64;N]){}', 'const generic'),
    ('fn f(a:[u64;2],b:[u64;2])->bool{a==b}', 'operator'),
    ('struct R{n:u64} fn f(){let a=[R{n:1};2];}', 'require Copy'),
    ('struct R{n:u64} fn f(){let a=[R{n:1}];let b=a[0];}', 'non-Copy value out of an array'),
    ('struct R{n:u64} impl R{fn take(self)->u64{self.n}} fn f(){let a=[R{n:1}];let b=a[0].take();}', 'non-Copy value receiver out of an array'),
    ('struct R{n:u64} struct S{r:R} impl R{fn take(self)->u64{self.n}} fn f(){let a=[S{r:R{n:1}}];let b=(a[0].r).take();}', 'non-Copy value receiver out of an array'),
    ('struct R{n:u64} struct S{r:R} fn f(){let a=[S{r:R{n:1}}];let b=a[0].r;}', 'non-Copy field out of an array'),
    ('struct R{n:u64} fn f(){let a=[R{n:1}];let b=a; a.len();}', 'moved'),
    ('struct R{n:u64} fn f(){let a:[R;0]=[];let b=a;let c=a;}', 'moved'),
    ('struct R{n:u64} fn f(){let p=R{n:1};let z=[p;0];p.n;}', 'moved'),
    ('fn f(){let a=[1u64];a[0]=2;}', 'immutable'),
    ('fn f(a:&[u64;2]){a[0]=3;}', 'shared reference'),
    ('extern "C" fn f(a:[u64;2]){}', 'ABI'),
    ('fn read(p:&u64)->u64{*p} fn f(){let a=[1u64,2];read(&a[0]);}', 'indexed borrowing requires an actual'),
    ('struct R{n:u64} impl R{fn read(&self)->u64{self.n}} fn f(){let a=[R{n:1}];a[0].read();}', 'indexed receiver borrowing requires an actual'),
    ('fn f(a:[u64;2]){match a{_=>()}}', 'array match coverage'),
    ('fn f(){let [a,b]=[1,2];}', 'array destructuring'),
    ('fn f(a:[u64;2]){match a{[x,y]=>()}}', 'array destructuring'),
])
def test_invalid_and_explicit_array_capabilities_fail_before_ir(source, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported/invalid array reached IR'))
    with pytest.raises(RustError, match=message):
        # Reference diagnostics here inspect an internal, non-certifying model.
        # They do not authorize public compilation or produce pointer IR.
        if source_has_references(source): analyze_source(source)
        else: compile_source(source)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'} and shutil.which('rustc') and shutil.which('cc') and os.environ.get('PCC_RUSTC_INSTALL_RECEIPT') else 'requires explicit audited actual rustc installation and Linux native reference linker')
def test_original_array_programs_have_actual_checked_full_width_native_pairs(tmp_path, monkeypatch):
    from tests.rust import test_rust_certified_references as certification
    from tests.rust import test_rust_method_reference as reference
    from tests.rust import test_rust_patterns as owned
    for name in PROGRAMS:
        directory = tmp_path / name
        directory.mkdir()
        path = Path(__file__).with_name('programs') / (name + '.rs')
        check = certification.certify(path, directory / 'certifier')
        with monkeypatch.context() as patch:
            patch.setattr(reference, 'PROGRAMS', (name,))
            patch.setattr(owned, 'compile_source', (lambda source, **options: compile_rust(source, rustc_check=check)) if name in BORROWED else compile_source)
            reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(directory, patch)


def test_former_array_literal_capability_negative_is_retained_as_positive():
    from tests.rust.test_rust_frontend import RETAINED_ARRAY_LITERAL_SOURCE
    assert RETAINED_ARRAY_LITERAL_SOURCE == "fn f(){ [1,2]; }"
    assert compile_source(RETAINED_ARRAY_LITERAL_SOURCE).emit_object().startswith(b'\x7fELF')


def test_complete_pinned_core_array_remains_an_explicit_upstream_frontier():
    path = Path(__file__).parent / 'upstream/rust-1.90.0/core_array.rs'
    original = path.read_bytes()
    assert hashlib.sha256(original).hexdigest() == '29adca8337dddeddf9d43ec943767bf9ffb917e43e234b91630573b5484f991e'
    with pytest.raises(RustError, match='documentation attributes'):
        compile_source(original.decode())
    assert path.read_bytes() == original


@pytest.mark.pcc_gate(unavailable=None if os.environ.get('PCC_RUSTC_INSTALL_RECEIPT') else 'explicit audited rustc installation receipt required')
def test_array_index_alias_invalidity_is_owned_by_actual_external_checker(tmp_path, monkeypatch):
    from pcc.frontends.rust import RustExternalCheckError, compile_rust_checked
    from pcc.frontends.rust.lowering import Lowerer
    from tests.rust.test_rust_certified_references import toolchain
    original = 'fn take(p:&u64,q:u64)->u64{*p+q} fn f(){let mut a=[1u64,2];take(&a[0],{a[1]=3;0});}'
    path = tmp_path / 'original.rs'
    path.write_text(original)
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('rejected borrow reached owned IR'))
    with pytest.raises(RustExternalCheckError, match='E0506'):
        compile_rust_checked(original, toolchain=toolchain(), evidence_directory=tmp_path / 'certifier')
    assert not (tmp_path / 'certifier/checked.rmeta').exists()
    assert path.read_text() == original
