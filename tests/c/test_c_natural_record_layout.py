"""Shared C natural-record extraction: authored CI obligations, execution UNRUN.

This shared-owner change requires the whole affected C/C++ layout and ABI
validation, not just these shape/target assertions. No host ctypes layouts.
"""

import os
import shutil
import subprocess

import pytest

from pcc.frontends.c.c_abi_layout import (
    CAbiScalarLayout, builtin_scalar_layout, natural_record_layout,
)
from pcc.frontends.c.codegen.c_layout import ir_type_align, ir_type_size
from pcc.frontends.c.codegen.c_types import get_ir_type_from_names
from pcc.ir.compat import ir_c as ir


TARGETS = (
    'x86_64-unknown-linux-gnu', 'x86_64-pc-windows-msvc',
    'x86_64-pc-windows-gnu', 'aarch64-unknown-linux-gnu', 'aarch64-apple-darwin',
)


def test_natural_record_padding_offsets_empty_minimum_and_explicit_alignment():
    fields = (CAbiScalarLayout(1, 1), CAbiScalarLayout(4, 4), CAbiScalarLayout(1, 1))
    record = natural_record_layout(fields)
    assert (record.size, record.alignment, record.offsets) == (12, 4, (0, 4, 8))
    assert natural_record_layout(()).size == 0  # Preserve empty C IR records.
    assert natural_record_layout((), minimum_size=1).size == 1
    assert natural_record_layout(fields, record_alignment=16).size == 16


@pytest.mark.parametrize('triple', TARGETS)
@pytest.mark.parametrize('middle', ('long', 'long double'))
def test_target_natural_records_match_existing_c_ir_storage(triple, middle):
    spellings = ('char', middle, 'char')
    fields = tuple(builtin_scalar_layout(spelling.split(), triple) for spelling in spellings)
    record = natural_record_layout(fields)
    physical = ir.LiteralStructType([get_ir_type_from_names(spelling.split(), triple) for spelling in spellings])
    assert (ir_type_size(physical), ir_type_align(physical)) == (record.size, record.alignment)
    array = ir.ArrayType(physical, 3)
    assert ir_type_size(array) == 3 * record.size


def test_c_ir_custom_size_and_alignment_precedence_are_preserved():
    record = ir.LiteralStructType([ir.IntType(8), ir.IntType(64), ir.IntType(8)])
    record.custom_align = 16
    assert ir_type_size(record) == 32 and ir_type_align(record) == 16
    record.custom_size = 48
    assert ir_type_size(record) == 48
    assert ir_type_size(ir.LiteralStructType([])) == 0


SOURCE = '''struct Inner {char first;int middle;char last;};
struct Outer {short first;struct Inner inner;char last;};
int main(void){
    struct Inner values[2];
    if(sizeof(struct Inner)!=12 || sizeof(struct Outer)!=20 || sizeof(values)!=24)return 1;
    if(__builtin_offsetof(struct Inner,middle)!=4 || __builtin_offsetof(struct Inner,last)!=8)return 2;
    if(__builtin_offsetof(struct Outer,inner)!=4 || __builtin_offsetof(struct Outer,last)!=16)return 3;
    return 42;
}
'''


@pytest.mark.integration
def test_c_natural_record_original_owned_and_reference_execution(tmp_path):
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    compiler = shutil.which(os.environ.get('PCC_C_ORACLE', 'cc'))
    assert compiler, 'explicit native C reference compiler is required'
    source = tmp_path / 'record.c'
    source.write_text(SOURCE)
    binary = tmp_path / 'reference'
    build = subprocess.run([compiler, '-std=c11', str(source), '-o', str(binary)],
                           capture_output=True, timeout=30)
    (tmp_path / 'reference-build.stdout.bin').write_bytes(build.stdout)
    (tmp_path / 'reference-build.stderr.bin').write_bytes(build.stderr)
    assert build.returncode == 0
    actual = subprocess.run([str(binary)], capture_output=True, timeout=10)
    (tmp_path / 'reference.stdout.bin').write_bytes(actual.stdout)
    (tmp_path / 'reference.stderr.bin').write_bytes(actual.stderr)
    assert actual.returncode == 42 and actual.stdout == actual.stderr == b''
    assert CEvaluator(backend='self').evaluate(SOURCE, optimize=False) == actual.returncode
