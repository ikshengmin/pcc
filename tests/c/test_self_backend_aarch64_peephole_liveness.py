"""Host semantic models for the actual AArch64 peephole helpers.

The model covers only the instructions below. It checks exact integer bits,
W/X zero-extension, memory observations and compare flags; it is not target
execution or a platform qualification.
"""

import re
import struct

import pytest

from pcc.backend.arm64_encode import assemble_text
from pcc.backend.self_backend_aarch64_darwin import (
    _can_drop_zero_mov_after_store,
    _fold_mov_compare_source,
    _fold_mov_mov_chain,
    _fold_mov_zero_branch_source,
    _fold_mov_store_source,
    _fold_scratch_def_into_move,
    _fold_zero_compare_immediate,
    _fold_zero_store_source,
    _fold_zero_test_branch,
    _line_defines_reg_alias,
    _line_uses_reg_alias,
    _scratch_dead_after,
)
from pcc.backend.self_backend_target_passes import (
    fold_aarch64_frame_addresses,
    fold_aarch64_immediates,
)


def _model(lines, stale=0xFEDCBA9876543210):
    registers = {str(index): 0 for index in range(31)}
    registers.update({'0': 0xCAFE1234DEADBEEF, '10': stale,
                      '11': 0x123456789ABCDEF0})
    stores = []
    flags = None

    def read(operand):
        if operand.startswith('#'):
            return int(operand[1:], 0)
        width = 32 if operand.startswith('w') else 64
        return (0 if operand[1:] == 'zr' else registers[operand[1:]]) & ((1 << width) - 1)

    def write(register, value):
        width = 32 if register.startswith('w') else 64
        if register[1:] != 'zr':
            registers[register[1:]] = value & ((1 << width) - 1)

    for line in lines:
        opcode, _, rest = line.strip().partition(' ')
        operands = rest.split(', ')
        dest = operands[0]
        if opcode == 'mov':
            write(dest, read(operands[1]))
        elif opcode in ('movz', 'movk'):
            shift = int(operands[2].split('#')[1]) if len(operands) == 3 else 0
            preserved = read(dest) & ~(0xFFFF << shift) if opcode == 'movk' else 0
            write(dest, preserved | (read(operands[1]) << shift))
        elif opcode in ('bfi', 'bfxil'):
            lsb, count = read(operands[2]), read(operands[3])
            field_mask = (1 << count) - 1
            if opcode == 'bfi':
                value = (read(dest) & ~(field_mask << lsb)) | ((read(operands[1]) & field_mask) << lsb)
            else:
                value = (read(dest) & ~field_mask) | ((read(operands[1]) >> lsb) & field_mask)
            write(dest, value)
        elif opcode in ('str', 'stur'):
            stores.append((rest.partition(', ')[2], read(dest)))
        elif opcode == 'cmp':
            width = 32 if dest.startswith('w') else 64
            lhs, rhs = read(dest), read(operands[1])
            difference = (lhs - rhs) & ((1 << width) - 1)
            sign = 1 << (width - 1)
            flags = (bool(difference & sign), difference == 0, lhs >= rhs,
                     bool((lhs ^ rhs) & (lhs ^ difference) & sign))
        elif opcode == 'add':
            write(dest, read(operands[1]) + read(operands[2]))
        else:
            raise AssertionError(f'Unmodelled instruction: {line}')
    return stores, flags


def _rmw(opcode, width):
    if opcode == 'movk':
        return f'  movk {width}10, #1, lsl #16'
    return f'  {opcode} {width}10, {width}11, #8, #8'


def _observed_x1(lines):
    # The shared scalar word interpreter models these MOVZ/MOV/B/CBZ/CBNZ
    # instructions, not assembly strings. This is a host check, not CPU execution.
    from tests.python.test_aarch64_branch_live_registers import execute
    words = struct.iter_unpack('<I', assemble_text('\n'.join(lines)).code)
    return execute([word for (word,) in words])['X1']


@pytest.mark.parametrize('branch', ['cbz', 'cbnz'])
@pytest.mark.parametrize('source', [0, 19])
def test_forwarded_branch_keeps_destination_on_both_successors(branch, source):
    pre = ['  movz x10, #7', f'  movz x9, #{source}']
    body = ['  mov w10, w9', f'  {branch} w10, L_take', '  b L_fall',
            'L_take:', '  mov x1, x10', '  b L_done',
            'L_fall:', '  mov x1, x10', 'L_done:']
    actual = _fold_mov_zero_branch_source(body)
    assert actual == [body[0], f'  {branch} w9, L_take', *body[2:]]
    assert _observed_x1(pre + body) == _observed_x1(pre + actual) == source
    assert _observed_x1(pre + actual[1:]) == 7  # Wrong if the MOV is removed.


def test_folded_zero_branch_preserves_taken_target_value_in_machine_words():
    pre = ['  movz x10, #7']
    body = ['  movz w10, #0', '  cbz w10, L_done', '  movz x1, #3',
            'L_done:', '  mov x1, x10']
    actual = _fold_zero_test_branch(body)
    assert actual == [body[0], '  b L_done', *body[2:]]
    assert _observed_x1(pre + body) == _observed_x1(pre + actual) == 0
    assert _observed_x1(pre + actual[1:]) == 7


def test_folded_w_self_move_clears_the_live_x_upper_half_in_machine_words():
    pre = ['  movz x9, #1, lsl #32']
    body = ['  mov w10, w9', '  mov w9, w10', '  movz w10, #5, lsl #0',
            '  mov x1, x9']
    actual = _fold_mov_mov_chain(body)
    assert actual == ['  mov w9, w9', *body[2:]]
    assert _observed_x1(pre + body) == _observed_x1(pre + actual) == 0
    assert _observed_x1(pre + actual[1:]) == 1 << 32


@pytest.mark.parametrize('predicate,inverse', [
    ('eq', 'ne'), ('ne', 'eq'), ('slt', 'ge'), ('sle', 'gt'), ('sgt', 'le'),
    ('sge', 'lt'), ('ult', 'hs'), ('ule', 'hi'), ('ugt', 'ls'), ('uge', 'lo'),
])
@pytest.mark.parametrize('width', ['i32', 'i64'])
def test_live_terminal_comparison_uses_its_flags_in_both_real_output_paths(tmp_path, predicate, inverse, width):
    from pcc.backend.arm64_asm_driver import assemble_file
    from pcc.backend.native_object import encode_native_object_from_sections
    from pcc.backend.self_backend_indexed_codec import encode_indexed_module_file
    from pcc.backend.self_backend_indexed_emit import emit_indexed_module_file
    from pcc.backend.self_backend_parse import parse_self_backend_module
    ir = f'''target triple = "arm64-apple-darwin"
define i32 @probe({width} %x, {width} %y) {{
entry:
  %q = icmp {predicate} {width} %x, %y
  br i1 %q, label %yes, label %no
yes:
  %v = zext i1 %q to i32
  ret i32 %v
no:
  ret i32 0
}}
'''
    indexed, asm, pco = (tmp_path / name for name in ('input.pidx', 'out.s', 'out.pco'))
    encode_indexed_module_file(str(indexed), parse_self_backend_module(ir))
    emit_indexed_module_file(str(indexed), str(asm), 'ASM', optimize=True)
    text = asm.read_text()
    assert '  cset w11, ' in text and '  sturb w11, ' in text and '  ldurb w9, ' in text
    assert f'  b.{inverse} L_probe_no\n' in text
    sections, undefined = assemble_file(text)
    emit_indexed_module_file(str(indexed), str(pco), 'PCO', optimize=True)
    assert pco.read_bytes() == encode_native_object_from_sections(sections, undefined=undefined)


def test_branch_on_earlier_condition_cannot_borrow_later_cmp_flags():
    from pcc.backend.self_backend import emit_aarch64_darwin_asm
    ir = '''target triple = "arm64-apple-darwin"
define i32 @probe(i64 %x, i64 %y) {
entry:
  %q = icmp slt i64 %x, %y
  %other = icmp sgt i64 %x, %y
  br i1 %q, label %yes, label %no
yes:
  %v = zext i1 %other to i32
  ret i32 %v
no:
  ret i32 7
}
'''
    lines = emit_aarch64_darwin_asm(ir).splitlines()
    assert '  cset w11, lt' in lines and '  cset w11, gt' in lines
    assert '  cbz w9, L_probe_no' in lines
    assert not any(x.startswith('  b.') and x.endswith(' L_probe_no') for x in lines)


@pytest.mark.parametrize('opcode', ['movk', 'bfi', 'bfxil'])
@pytest.mark.parametrize('written, observed', [('x', 'x'), ('x', 'w'), ('w', 'x'), ('w', 'w')])
def test_read_modify_write_destinations_both_use_and_define_aliases(opcode, written, observed):
    line = _rmw(opcode, written)
    assert _line_defines_reg_alias(line, observed + '10')
    assert _line_uses_reg_alias(line, observed + '10')
    assert not _can_drop_zero_mov_after_store([line], 0, observed + '10')
    assert not _scratch_dead_after([line], 0, observed + '10')


_FOLDS = [
    (_fold_zero_store_source, 'movz {w}10, #0', 'str {w}10, [x9]'),
    (_fold_mov_store_source, 'mov {w}10, {w}0', 'str {w}10, [x9]'),
    (_fold_zero_compare_immediate, 'movz {w}10, #0', 'cmp {w}11, {w}10'),
    (_fold_mov_compare_source, 'mov {w}10, {w}0', 'cmp {w}11, {w}10'),
    (_fold_mov_mov_chain, 'mov {w}10, {w}0', 'mov {w}19, {w}10'),
    (_fold_scratch_def_into_move, 'movz {w}10, #0', 'mov {w}19, {w}10'),
    (fold_aarch64_immediates, 'movz {w}10, #7', 'cmp {w}11, {w}10'),
]


@pytest.mark.parametrize('fold, initialization, consumer', _FOLDS, ids=[row[0].__name__ for row in _FOLDS])
@pytest.mark.parametrize('opcode', ['movk', 'bfi', 'bfxil'])
@pytest.mark.parametrize('initial_width, update_width', [('x', 'x'), ('x', 'w'), ('w', 'x'), ('w', 'w')])
def test_folds_preserve_partial_destination_updates(fold, initialization, consumer, opcode,
                                                   initial_width, update_width):
    lines = ['  ' + initialization.format(w=initial_width),
             '  ' + consumer.format(w=initial_width), _rmw(opcode, update_width),
             '  str x10, [x12]', '  str x19, [x13]']
    output = fold(lines)
    for stale in (1, 0xFFFF, 0x100000000, 0xFEDCBA9876543210):
        assert _model(output, stale) == _model(lines, stale)
    assert output == lines


@pytest.mark.parametrize('width, expected', [('x', 0xFEDCBA9800013210), ('w', 0x00013210)])
def test_model_movk_keeps_unwritten_bits_and_zero_extends_w(width, expected):
    assert _model([_rmw('movk', width), '  str x10, [x12]'])[0] == [('[x12]', expected)]


@pytest.mark.parametrize('opcode, expected', [('bfi', 0xFEDCBA987654F010), ('bfxil', 0xFEDCBA98765432DE)])
def test_model_bitfield_insert_preserves_complement(opcode, expected):
    assert _model([_rmw(opcode, 'x'), '  str x10, [x12]'])[0] == [('[x12]', expected)]


@pytest.mark.parametrize('initial_width, overwrite_width', [('x', 'x'), ('x', 'w'), ('w', 'x'), ('w', 'w')])
def test_full_destination_overwrite_still_allows_zero_store_fold(initial_width, overwrite_width):
    lines = [f'  movz {initial_width}10, #0', f'  str {initial_width}10, [x9]',
             f'  movz {overwrite_width}10, #5', '  str x10, [x12]']
    output = _fold_zero_store_source(lines)
    assert output == [f'  str {initial_width}zr, [x9]', *lines[2:]]
    assert _model(output) == _model(lines)


@pytest.mark.parametrize('read', ['  stlr w10, [x9]', '  stlrb w10, [x9]', '  stlrh w10, [x9]',
                                '  tst w10, w11', '  cmn x10, x11',
                                '  ldr x9, [x10], #8', '  ldp x9, x11, [x10, #16]!'])
def test_store_flag_and_writeback_operands_consume_old_register(read):
    assert _line_uses_reg_alias(read, 'x10')
    assert not _can_drop_zero_mov_after_store([read, '  movz x10, #5'], 0, 'x10')


@pytest.mark.parametrize('barrier', ['  br x8', '  blr x8', '  tbz x8, #0, L_next',
                                   '  tbnz x8, #0, L_next', 'L_next:', '.pcc_barrier'])
def test_unproved_control_flow_never_makes_previous_value_dead(barrier):
    lines = ['  movz x10, #0', '  str x10, [x9]', barrier, '  movz x10, #5']
    assert _fold_zero_store_source(lines) == lines
    assert not _scratch_dead_after(lines, 2, 'x10')


@pytest.mark.parametrize('opcode', ['movk', 'bfi', 'bfxil'])
@pytest.mark.parametrize('width', ['x', 'w'])
def test_frame_address_fold_preserves_later_partial_alias_use(opcode, width):
    lines = ['_f:', '  stp x29, x30, [sp, #-16]!', '  mov x29, sp',
             '  sub sp, sp, #1088', '  sub x10, x29, #1024', '  stur x0, [x10]',
             _rmw(opcode, width), '  str x10, [x12]', '  ret']
    assert fold_aarch64_frame_addresses(lines) == lines


def test_immediate_fold_preserves_w_x_alias_read_and_release_store():
    for use in ('  str x10, [x12]', '  stlr w10, [x9]', '  br x10', '  ret x10'):
        lines = ['  movz w10, #7', '  cmp w11, w10', use]
        assert fold_aarch64_immediates(lines) == lines


@pytest.mark.parametrize('width', ['w', 'x'])
def test_scratch_destination_rename_still_removes_harmless_copy(width):
    lines = [f'  movz {width}10, #7', f'  mov {width}19, {width}10',
             f'  movz {width}10, #5', '  str x19, [x13]']
    output = _fold_scratch_def_into_move(lines)
    assert output == [f'  movz {width}19, #7', *lines[2:]]
    assert _model(output) == _model(lines)


@pytest.mark.parametrize('producer', ['  ldp x10, x11, [x9]',
                                     '  ldr x10, [x9, #8]!',
                                     '  ldr x10, [x9], #8'])
def test_destination_rename_keeps_multi_destination_instructions(producer):
    lines = [producer, '  mov x19, x10', '  movz x10, #5']
    assert _fold_scratch_def_into_move(lines) == lines


def test_owned_encoder_retains_movz_before_movk():
    lines = ['  movz x10, #0', '  str x10, [x9]', '  movk x10, #1, lsl #16', '  str x10, [x12]']
    output = _fold_zero_store_source(lines)
    code = assemble_text('\n'.join(output)).code
    assert struct.unpack('<4I', code) == (0xD280000A, 0xF900012A, 0xF2A0002A, 0xF900018A)
    assert not re.search(r'\bxzr\b', '\n'.join(output))
