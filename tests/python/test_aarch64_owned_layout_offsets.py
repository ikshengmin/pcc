"""Pure host ownership/byte controls; no native linking or execution."""
import ast
import hashlib
from pathlib import Path

import pytest

from pcc.backend import arm64_asm_driver as driver
from pcc.backend import arm64_encode as encoder
from pcc.backend import self_backend_aarch64_darwin as emitter
from pcc.backend import self_backend_indexed_emit as worker
from pcc.backend.macho_obj import Section, TEXT_SECTION_FLAGS
from pcc.backend.native_object import encode_native_object_from_sections
from pcc.backend.self_backend_value_arena import CompilerIntArena
from pcc.backend.self_backend_indexed_codec import encode_indexed_module_file
from pcc.backend.self_backend_parse import parse_self_backend_module


REAL_SHAPE = '.section __DATA,__const\n.p2align 2\n_tb_func_1:\n  .long 1\n.section __DATA,__const\n.p2align 0\n_tb_name_1:\n  .byte 102, 105, 98, 0\n.section __DATA,__data\n.p2align 3\n_pystr_obj_1:\n  .quad 1\n  .long 4\n  .long 1\n  .quad 5\n  .quad -1\n  .quad -1\n  .byte 104, 101, 108, 108, 111, 0\n  .space 6\n.section __DATA,__data\n.p2align 3\n_table_1:\n  .quad _tb_func_1\n  .quad _pystr_obj_1+8\n  .quad 42\n.section __TEXT,__text,regular,pure_instructions\n.p2align 2\n.globl _leaf_fn\n_leaf_fn:\n  movz w0, #7\n  ret\n.p2align 2\n.globl _recur_fn\n_recur_fn:\n  paciasp\n  stp x29, x30, [sp, #-16]!\n  mov x29, sp\n  cmp w0, #1\n  b.le L_recur_base\n  sub w0, w0, #1\n  bl _recur_fn\n  add w0, w0, #1\nL_recur_base:\n  ldp x29, x30, [sp], #16\n  autiasp\n  ret\n.p2align 2\n.globl _entry_fn\n_entry_fn:\n  paciasp\n  stp x29, x30, [sp, #-16]!\n  mov x29, sp\n  bl _leaf_fn\n  cbz w0, L_entry_zero\n  adrp x9, _pystr_obj_1@PAGE\n  add x9, x9, _pystr_obj_1@PAGEOFF\n  ldur x10, [x9, #16]\n  add w0, w0, w10\n  b L_entry_done\nL_entry_zero:\n  movz w0, #0\nL_entry_done:\n  bl _external_hook\n  ldp x29, x30, [sp], #16\n  autiasp\n  ret\n.subsections_via_symbols\n'

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

def _failure(fn):
    try:
        fn()
    except Exception as exc:
        return type(exc), str(exc), None if exc.__cause__ is None else (type(exc.__cause__), str(exc.__cause__))
    raise AssertionError('expected failure')

def _ir(shape):
    head = 'target triple = "arm64-apple-darwin"\n'
    if shape == 'global':
        return head + '@datum = global i64 7\ndefine i64 @probe() {\nentry:\n  %x = load i64, ptr @datum\n  ret i64 %x\n}\n'
    if shape == 'branch':
        return head + 'define i64 @probe(i64 %x) {\nentry:\n  %q = icmp eq i64 %x, 0\n  br i1 %q, label %yes, label %no\nyes:\n  ret i64 11\nno:\n  ret i64 29\n}\n'
    return head + 'define i64 @probe(i64 %x) {\nentry:\n  %a = add i64 %x, 1\n  ret i64 %a\n}\n'

def _sidecar(tmp_path, shape):
    path = tmp_path / (shape + '.pidx')
    encode_indexed_module_file(str(path), parse_self_backend_module(_ir(shape)))
    return path

def _result(prefix, records=None, names=(), sections=(), suffix=_unwind, owned=False):
    measure = driver._measure_owned_text_layout if owned else driver.measure_text_layout
    offsets, size = measure(prefix, records, names)
    transport = driver.StructuredAArch64Module(
        list(prefix) + suffix(offsets, size), tuple(sections), records, tuple(names),
    )
    final_sections, undefined = transport.assemble_sections()
    payload = encode_native_object_from_sections(final_sections, undefined=undefined)
    return offsets, size, final_sections, undefined, payload


@pytest.mark.parametrize('shape', ['real', 'encoded', 'data', 'unwind', 'bcond', 'cbz'])
def test_offsets_sections_and_complete_pco_equal(shape):
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
                   '  .space 4096', '  .quad _probe', '  .long 19', '  .byte 2']
    elif shape in ('bcond', 'cbz'):
        branch = '  b.eq L_resume' if shape == 'bcond' else '  cbz x0, L_resume'
        # Preserve the exact 1-MiB branch gap while respecting Mach-O's
        # uint16 length for each data-in-code record; no validator bypass.
        prefix = PREFIX[:3] + [branch]
        for length in [65532] * 16 + [64]:
            prefix += ['.data_region', '.space ' + str(length), '.end_data_region']
        prefix += ['L_resume:', '  ret']
    try:
        before = list(prefix)
        original = _result(prefix, records, names, suffix=suffix)
        candidate = _result(prefix, records, names, suffix=suffix, owned=True)
        assert candidate == original
        assert prefix == before
        if records is not None:
            assert len(records) == 8 and not records._closed
        if shape in ('bcond', 'cbz'):
            assert candidate[0]['L_resume'] == 1048576 + 8
    finally:
        if records is not None:
            records.close()


@pytest.mark.parametrize('case', ['prefix', 'indices', 'word', 'suffix', 'private', 'duplicate', 'float'])
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
    elif case == 'float':
        prefix += ['.section __DATA,__data', '.float not_a_float']
    try:
        original = _failure(lambda: _result(prefix, records, sections=sections, suffix=suffix))
        candidate = _failure(lambda: _result(prefix, records, sections=sections, suffix=suffix, owned=True))
        assert candidate == original
    finally:
        if records is not None:
            records.close()


def _capture_builders(monkeypatch):
    original = driver.AArch64ModuleBuilder
    builders = []
    def create(*args, **kwargs):
        builder = original(*args, **kwargs)
        builders.append(builder)
        return builder
    monkeypatch.setattr(driver, 'AArch64ModuleBuilder', create)
    return builders


@pytest.mark.parametrize('owned', [False, True])
def test_public_copy_vs_private_owned_identity_and_close(monkeypatch, owned):
    builders = _capture_builders(monkeypatch)
    measure = driver._measure_owned_text_layout if owned else driver.measure_text_layout
    offsets, size = measure(PREFIX)
    assert len(builders) == 1
    builder = builders[0]
    labels = builder.buffers[('__TEXT', '__text')].text_builder.labels
    assert (offsets is labels) is owned
    assert offsets == {'_probe': 0} and size == 8
    assert builder.closed
    for buffer in builder.buffers.values():
        if buffer.text_builder is not None:
            assert buffer.text_builder.closed
            assert buffer.text_builder.entries._closed
            assert buffer.text_builder.structured_relocations._closed
    if not owned:
        labels['_after_close'] = 123
        assert '_after_close' not in offsets


def test_owned_returns_fresh_independent_mapping_for_each_call():
    first, first_size = driver._measure_owned_text_layout(PREFIX)
    second, second_size = driver._measure_owned_text_layout(PREFIX)
    assert first == second and first is not second and first_size == second_size
    first['_private_mutation'] = 99
    assert '_private_mutation' not in second
    empty_a, size_a = driver._measure_owned_text_layout([])
    empty_b, size_b = driver._measure_owned_text_layout([])
    assert empty_a == empty_b == {} and empty_a is not empty_b
    assert size_a == size_b == 0


@pytest.mark.parametrize('shape', ['scalar', 'global', 'branch'])
def test_actual_fresh_indexed_worker_pco_bytes_equal(tmp_path, monkeypatch, shape):
    sidecar = _sidecar(tmp_path, shape)
    source = sidecar.read_bytes()
    old, new = tmp_path / 'old.pco', tmp_path / 'new.pco'
    measured = []
    candidate = driver._measure_owned_text_layout
    def owned(*args, **kwargs):
        result = candidate(*args, **kwargs)
        measured.append(result)
        return result
    monkeypatch.setattr(emitter, '_measure_owned_text_layout', driver.measure_text_layout)
    worker.emit_indexed_module_file(str(sidecar), str(old), 'PCO', optimize=True)
    monkeypatch.setattr(emitter, '_measure_owned_text_layout', owned)
    worker.emit_indexed_module_file(str(sidecar), str(new), 'PCO', optimize=True)
    assert len(measured) == 1 and measured[0][1] > 0
    assert new.read_bytes() == old.read_bytes()
    assert sidecar.read_bytes() == source
    assert not (tmp_path / 'old.pco.tmp').exists()
    assert not (tmp_path / 'new.pco.tmp').exists()


def test_measurement_builder_closed_before_metadata_error(tmp_path, monkeypatch):
    builders = _capture_builders(monkeypatch)
    def fail(*args, **kwargs):
        assert len(builders) == 1 and builders[0].closed
        assert all(b.text_builder is None or b.text_builder.closed
                   for b in builders[0].buffers.values())
        raise RuntimeError('metadata control failure')
    monkeypatch.setattr(emitter, '_append_compact_unwind', fail)
    sidecar = _sidecar(tmp_path, 'scalar')
    output = tmp_path / 'failed.pco'
    with pytest.raises(RuntimeError, match='metadata control failure'):
        worker.emit_indexed_module_file(str(sidecar), str(output), 'PCO', optimize=True)
    assert not output.exists() and not (tmp_path / 'failed.pco.tmp').exists()


@pytest.mark.parametrize('point', ['text_size', 'close'])
def test_late_error_and_close_order_matches_public(monkeypatch, point):
    original_size = driver.AArch64ModuleBuilder.text_size
    original_close = driver.AArch64ModuleBuilder.close
    events = []
    def size(self):
        events.append('size')
        if point == 'text_size':
            raise RuntimeError('size control failure')
        return original_size(self)
    def close(self):
        events.append('close')
        original_close(self)
        if point == 'close':
            raise RuntimeError('close control failure')
    monkeypatch.setattr(driver.AArch64ModuleBuilder, 'text_size', size)
    monkeypatch.setattr(driver.AArch64ModuleBuilder, 'close', close)
    public = _failure(lambda: driver.measure_text_layout(PREFIX))
    public_events = list(events)
    events.clear()
    owned = _failure(lambda: driver._measure_owned_text_layout(PREFIX))
    assert owned == public and events == public_events == ['size', 'close']


PUBLIC_SOURCE_SHA256 = {'StructuredAArch64Module': '3a810e829270db9913e4e3d1ded0e7dfc3862604a8112d1b2abb4bbbe58cdfae', 'measure_text_layout': '8dffc7539a5f257cac2bb985abbcdb7991fa8ebe386969872df8d6b741f2ccef'}

def test_public_measurement_and_transport_source_unchanged():
    source = Path(driver.__file__).read_text()
    nodes = {n.name: n for n in ast.parse(source).body
             if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    for name, expected in PUBLIC_SOURCE_SHA256.items():
        assert hashlib.sha256(ast.get_source_segment(source, nodes[name]).encode()).hexdigest() == expected


def test_all_nineteen_ordered_transforms_and_optimize_policy_retained():
    source = Path(emitter.__file__).read_text()
    tree = ast.parse(source)
    active = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == '_emit_prepared_aarch64_darwin_lines_active')
    expected = [
        '_forward_adjacent_stack_store_load', '_forward_one_intervening_stack_store_load',
        '_fold_zero_store_source', '_fold_mov_store_source', '_fold_zero_compare_immediate',
        '_fold_mov_compare_source', '_fold_mov_zero_branch_source', '_fold_mov_arith_self_update',
        '_fold_scratch_def_into_move', '_fold_mov_mov_chain', '_fold_zero_test_branch',
        '_fold_forwarded_cset_branch', '_fold_cset_zero_branch', '_drop_dead_cset_branch_stores',
        '_thread_trampoline_branches', '_fold_cond_branch_to_fallthrough',
        '_drop_fallthrough_uncond_branches', '_drop_unreferenced_empty_local_labels',
        'pair_adjacent_aarch64_64bit_memory_ops',
    ]
    calls = sorted((n.lineno, n) for n in ast.walk(active)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id in expected)
    assert [n.func.id for _, n in calls] == expected
    assert len([n for n in ast.walk(active) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == '_measure_owned_text_layout']) == 1
    assert any(isinstance(n, ast.If) and isinstance(n.test, ast.Name) and n.test.id == 'optimize'
               for n in ast.walk(active))
    assert any(k.arg == 'enabled' and isinstance(k.value, ast.Name) and k.value.id == 'optimize'
               for k in calls[-1][1].keywords)
