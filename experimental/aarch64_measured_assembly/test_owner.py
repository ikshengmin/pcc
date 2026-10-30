"""One-shot host assembly parity; never link or execute target/native code."""
import ast
import hashlib
from pathlib import Path

import pytest

from experimental.aarch64_measured_assembly.owner import OwnedMeasuredAssembly
from pcc.backend import arm64_asm_driver as driver
from pcc.backend import arm64_encode as encoder
from pcc.backend.macho_obj import Section, DATA_SECTION_FLAGS, TEXT_SECTION_FLAGS
from pcc.backend.self_backend_value_arena import CompilerIntArena

# Literal copied, not imported/executed, from authenticated existing
# tests/python/test_arm64_asm_driver.py REAL_SHAPE (706ec9e55a91...).
REAL_SHAPE = '.section __DATA,__const\n.p2align 2\n_tb_func_1:\n  .long 1\n.section __DATA,__const\n.p2align 0\n_tb_name_1:\n  .byte 102, 105, 98, 0\n.section __DATA,__data\n.p2align 3\n_pystr_obj_1:\n  .quad 1\n  .long 4\n  .long 1\n  .quad 5\n  .quad -1\n  .quad -1\n  .byte 104, 101, 108, 108, 111, 0\n  .space 6\n.section __DATA,__data\n.p2align 3\n_table_1:\n  .quad _tb_func_1\n  .quad _pystr_obj_1+8\n  .quad 42\n.section __TEXT,__text,regular,pure_instructions\n.p2align 2\n.globl _leaf_fn\n_leaf_fn:\n  movz w0, #7\n  ret\n.p2align 2\n.globl _recur_fn\n_recur_fn:\n  paciasp\n  stp x29, x30, [sp, #-16]!\n  mov x29, sp\n  cmp w0, #1\n  b.le L_recur_base\n  sub w0, w0, #1\n  bl _recur_fn\n  add w0, w0, #1\nL_recur_base:\n  ldp x29, x30, [sp], #16\n  autiasp\n  ret\n.p2align 2\n.globl _entry_fn\n_entry_fn:\n  paciasp\n  stp x29, x30, [sp, #-16]!\n  mov x29, sp\n  bl _leaf_fn\n  cbz w0, L_entry_zero\n  adrp x9, _pystr_obj_1@PAGE\n  add x9, x9, _pystr_obj_1@PAGEOFF\n  ldur x10, [x9, #16]\n  add w0, w0, w10\n  b L_entry_done\nL_entry_zero:\n  movz w0, #0\nL_entry_done:\n  bl _external_hook\n  ldp x29, x30, [sp], #16\n  autiasp\n  ret\n.subsections_via_symbols\n'
PUBLIC_CLASS_SOURCE_SHA256 = 'f334c3061818d2ca27a01ffeb8a569df30814ee03024d32d391cc85f72fc4ff5'
PREFIX = ['.section __TEXT,__text,regular,pure_instructions',
          '.globl _probe', '_probe:', '  nop', '  ret']


def _unwind(offsets, size):
    assert offsets['_probe'] == 0 and size > 0
    return ['.section __LD,__compact_unwind,regular,debug', '.p2align 3',
            '  .quad _probe', '  .long ' + str(size), '  .long 67108864',
            '  .quad 0', '  .quad 0', '.subsections_via_symbols']


def _arena(rows):
    arena = CompilerIntArena()
    for row in rows:
        arena.append4(*row)
    return arena


def _result(prefix, records=None, names=(), sections=(), suffix=_unwind, reuse=False):
    owner = OwnedMeasuredAssembly() if reuse else None
    try:
        if owner is None:
            offsets, size = driver.measure_text_layout(prefix, records, names)
        else:
            offsets, size = owner.measure(prefix, records, names)
        lines = list(prefix) + suffix(offsets, size)
        transport = driver.StructuredAArch64Module(
            lines, tuple(sections), records, tuple(names),
        )
        if owner is None:
            return offsets, size, transport.assemble_sections()
        result = owner.assemble(transport)
        assert owner.closed and owner.builder is None
        return offsets, size, result
    finally:
        if owner is not None:
            owner.close()


@pytest.mark.parametrize('shape', ['real', 'encoded', 'data', 'unwind', 'bcond', 'cbz'])
def test_sections_metadata_and_relaxation_equal(shape):
    records = None
    suffix = _unwind
    names = ()
    prefix = list(PREFIX)
    if shape == 'real':
        prefix = REAL_SHAPE.splitlines()
        suffix = lambda offsets, size: ['.subsections_via_symbols']
    elif shape == 'encoded':
        prefix[-2:] = ['', '']
        records = _arena([(3, 0xD503201F, 0, -1), (4, 0xD65F03C0, 0, -1)])
    elif shape == 'data':
        prefix += ['.section __DATA,__data', '.p2align 3', '_datum:',
                   '  .quad _probe', '  .long 19', '  .byte 2']
    elif shape in ('bcond', 'cbz'):
        branch = '  b.eq L_resume' if shape == 'bcond' else '  cbz x0, L_resume'
        prefix = PREFIX[:3] + [branch, '.data_region', '.space 1048576',
                              '.end_data_region', 'L_resume:', '  ret']
    try:
        before = list(prefix)
        original = _result(prefix, records, names, suffix=suffix)
        candidate = _result(prefix, records, names, suffix=suffix, reuse=True)
        assert candidate == original
        assert prefix == before
        if shape in ('bcond', 'cbz'):
            assert candidate[0]['L_resume'] == 1048576 + 8
    finally:
        if records is not None:
            records.close()


def _failure(fn):
    try:
        fn()
    except Exception as exc:
        return type(exc), str(exc), None if exc.__cause__ is None else (type(exc.__cause__), str(exc.__cause__))
    raise AssertionError('expected failure')


@pytest.mark.parametrize('case', ['prefix', 'indices', 'word', 'suffix', 'private', 'duplicate'])
def test_first_error_and_message_equal(case):
    prefix = list(PREFIX)
    records = None
    sections = ()
    suffix = _unwind
    if case == 'prefix':
        prefix += ['not_an_instruction', '.unsupported']
    elif case == 'indices':
        records = _arena([(99, 0, 0, -1)])
        prefix += ['.unsupported']
    elif case == 'word':
        records = _arena([(3, 0x100000000, 0, -1)])
    elif case == 'suffix':
        suffix = lambda offsets, size: ['.unsupported']
    elif case == 'private':
        prefix.insert(0, '.private_extern _missing')
    elif case == 'duplicate':
        sections = (Section(sectname='__text', segname='__TEXT', data=b'', flags=TEXT_SECTION_FLAGS),)
    try:
        original = _failure(lambda: _result(prefix, records, sections=sections, suffix=suffix))
        candidate = _failure(lambda: _result(prefix, records, sections=sections, suffix=suffix, reuse=True))
        assert candidate == original
    finally:
        if records is not None:
            records.close()










def test_public_transport_is_repeatable():
    transport = driver.StructuredAArch64Module(list(PREFIX))
    assert transport.assemble_sections() == transport.assemble_sections()


def test_public_transport_still_observes_mutation():
    lines = list(PREFIX)
    transport = driver.StructuredAArch64Module(lines)
    before = transport.assemble_sections()
    lines[-2] = '  brk #0'
    assert transport.assemble_sections() != before


def test_existing_layout_cache_is_used_once(monkeypatch):
    original = encoder._relax_conditional_branches
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(encoder, '_relax_conditional_branches', counted)
    _result(PREFIX, reuse=True)
    assert len(calls) == 1


def test_owner_close_is_idempotent_and_releases_builder():
    owner = OwnedMeasuredAssembly()
    lines = list(PREFIX)
    records = _arena([(3, 0xD503201F, 0, -1), (4, 0xD65F03C0, 0, -1)])
    offsets, size = owner.measure(lines, records, ())
    suffix = _unwind(offsets, size)
    expected = driver.assemble_lines(lines + suffix, encoded_line_records=records)
    builder = owner.builder
    owner.release_prefix(lines, records)
    assert not lines and records._closed and owner.prefix_count == 0
    assert not builder.closed
    transport = driver.StructuredAArch64Module(lines + suffix)
    assert owner.assemble(transport) == expected
    owner.close()
    owner.close()
    assert owner.builder is None and owner.closed and builder.closed
    assert all(x.text_builder is None or x.text_builder.closed for x in builder.buffers.values())


def test_prefix_is_parsed_once_not_twice(monkeypatch):
    original = driver._append_assembly_lines
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(driver, '_append_assembly_lines', counted)
    _result(PREFIX)
    assert len(calls) == 2
    calls.clear()
    _result(PREFIX, reuse=True)
    assert len(calls) == 1


def test_owner_rejects_a_second_measurement():
    owner = OwnedMeasuredAssembly()
    try:
        owner.measure(PREFIX, None, ())
        with pytest.raises(encoder.EncodeError, match='not fresh'):
            owner.measure(PREFIX, None, ())
    finally:
        owner.close()


def test_unused_owner_preserves_normal_fallback():
    owner = OwnedMeasuredAssembly()
    transport = driver.StructuredAArch64Module(list(PREFIX))
    assert owner.assemble(transport) == transport.assemble_sections()
    assert owner.closed and owner.builder is None


def test_public_transport_source_unchanged():
    source = Path(driver.__file__).read_bytes()
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'StructuredAArch64Module')
    first = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
    public_class = b"".join(source.splitlines(keepends=True)[first - 1:node.end_lineno])
    assert hashlib.sha256(public_class).hexdigest() == PUBLIC_CLASS_SOURCE_SHA256
