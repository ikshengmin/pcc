"""Direct-only entry placement must not rescan a proven owned compact block."""
import pytest

from pcc.ir import ir
from pcc.frontends.python.codegen.core_helpers import _instruction_is_terminator_text
from pcc.frontends.python.codegen.ownership_lowering import OwnershipLoweringMixin


class Probe(OwnershipLoweringMixin):
    def __init__(self, fn, builder):
        self.current_function = fn
        self.builder = builder
        self._entry_inline_edge_anchor_function = None
        self._entry_inline_edge_anchor_record = None
        self.scans = 0
    def _instruction_is_terminator(self, instr):
        self.scans += 1
        return _instruction_is_terminator_text(instr)


def _direct(monkeypatch, block_type=ir.Block):
    monkeypatch.setenv('PCC_DIRECT_INDEXED_KERNEL_CAPTURE', '1')
    monkeypatch.setenv('PCC_DIRECT_INDEXED_KERNEL_EMIT', '1')
    monkeypatch.delenv('PCC_DIRECT_INDEXED_KERNEL_VALIDATE', raising=False)
    monkeypatch.delenv('PCC_TEXT_INDEXED_KERNEL_EMIT', raising=False)
    module = ir.Module(name='entry-hoist')
    fn = ir.Function(module, ir.FunctionType(ir.VoidType(), []), name='f')
    if block_type is ir.Block:
        entry = fn.append_basic_block('entry')
    else:
        entry = block_type(fn, 'entry')
        fn.blocks.append(entry)
    other = fn.append_basic_block('other')
    builder = ir.IRBuilder(entry)
    builder.alloca(ir.IntType(64), name='slot')
    assert fn._direct_indexed_builder is not None and builder._direct_indexed_no_text
    assert entry._instrs and all(not row.text for row in entry._instrs)
    return module, fn, entry, other, builder, Probe(fn, builder)


def test_owned_direct_unterminated_entry_never_scans(monkeypatch):
    module, fn, entry, other, builder, probe = _direct(monkeypatch)
    builder.alloca(ir.IntType(64), name='another')
    builder.position_at_end(other)
    probe._position_at_entry_hoist_point()
    assert probe.scans == 0
    assert builder._block is entry and builder._fn is fn and builder._pos == builder._END
    assert not fn._has_raw_text_records


def test_owned_direct_last_terminator_uses_numeric_position(monkeypatch):
    _, fn, entry, other, builder, probe = _direct(monkeypatch)
    builder.branch(other)
    def forbidden(_anchor):
        pytest.fail('owned final terminator position performed identity rescan')
    monkeypatch.setattr(builder, 'position_before', forbidden)
    builder.position_at_end(other)
    probe._position_at_entry_hoist_point()
    assert probe.scans == 1
    assert builder._block is entry and builder._fn is fn and builder._pos == len(entry._instrs)-1


@pytest.mark.parametrize('mutation', ('append','insert','replace'))
def test_raw_text_mutations_force_existing_reverse_scan(monkeypatch, mutation):
    module, fn, entry, other, builder, probe = _direct(monkeypatch)
    if mutation == 'append':
        entry.append('ret void')
        expected = len(entry._instrs)-1
    elif mutation == 'insert':
        entry.insert(0, 'ret void')
        expected = 0
    else:
        entry._replace_record_text(entry._instrs[0], 'ret void')
        expected = 0
    assert fn._has_raw_text_records
    # Raw API does not have to set the builder's termination bit.
    assert not entry._terminated
    builder.position_at_end(other)
    probe._position_at_entry_hoist_point()
    assert probe.scans > 0 and builder._pos == expected
    assert builder._block is entry and builder._fn is fn


def test_raw_text_anywhere_in_function_conservatively_disables_no_term_fast_path(monkeypatch):
    module, fn, entry, other, builder, probe = _direct(monkeypatch)
    other.append('ret void')
    assert fn._has_raw_text_records
    probe._position_at_entry_hoist_point()
    assert probe.scans == len(entry._instrs) and builder._pos == builder._END


def test_unowned_block_and_inline_anchor_keep_old_positioning(monkeypatch):
    class CustomBlock(ir.Block):
        pass
    _, fn, entry, other, builder, probe = _direct(monkeypatch, CustomBlock)
    probe._position_at_entry_hoist_point()
    assert probe.scans == len(entry._instrs)
    probe.scans = 0
    probe._entry_inline_edge_anchor_function = fn
    probe._entry_inline_edge_anchor_record = entry._instrs[0]
    builder.position_at_end(other)
    probe._position_at_entry_hoist_point()
    assert probe.scans == 0 and builder._pos == 0 and builder._block is entry


def test_true_flag_with_trailing_compact_record_keeps_reverse_oracle(monkeypatch):
    _, _, entry, other, builder, probe = _direct(monkeypatch)
    builder.branch(other)
    expected = len(entry._instrs)-1
    # Deliberately malformed/trailing compact record exercises old fallback.
    builder._emit_direct('alloca')
    probe._position_at_entry_hoist_point()
    assert probe.scans >= 2 and builder._pos == expected


def test_unrelated_function_raw_text_does_not_disable_owned_entry(monkeypatch):
    module, fn, entry, _, builder, probe = _direct(monkeypatch)
    foreign = ir.Function(module, ir.FunctionType(ir.VoidType(), []), name='foreign')
    foreign.append_basic_block('entry').append('ret void')
    assert foreign._has_raw_text_records and not fn._has_raw_text_records
    probe._position_at_entry_hoist_point()
    assert probe.scans == 0 and builder._pos == builder._END


def test_metadata_replacement_marks_actual_function(monkeypatch):
    module, fn, entry, _, builder, probe = _direct(monkeypatch)
    # set_metadata's only text replacement path goes through Block.
    metadata = module.add_metadata([])
    entry._instrs[0].set_metadata('dbg', metadata)
    assert fn._has_raw_text_records
    probe._position_at_entry_hoist_point()
    assert probe.scans == len(entry._instrs)


def test_true_flag_wrong_record_owner_keeps_existing_oracle(monkeypatch):
    _, fn, entry, other, builder, probe = _direct(monkeypatch)
    builder.branch(other)
    record = entry._instrs[-1]
    # The old position_before follows record.block, even when malformed.
    record.block = other
    other._instrs.append(record)
    probe._position_at_entry_hoist_point()
    assert probe.scans == 1
    assert builder._block is other and builder._fn is fn and builder._pos == 0


def test_cross_function_text_replacement_marks_record_owner(monkeypatch):
    module, fn, entry, _, builder, probe = _direct(monkeypatch)
    foreign = ir.Function(module, ir.FunctionType(ir.VoidType(), []), name='foreign')
    other = foreign.append_basic_block('other')
    # Legacy replacement writes rec.text before looking up block membership.
    other._replace_record_text(entry._instrs[0], 'ret void')
    assert foreign._has_raw_text_records and fn._has_raw_text_records
    probe._position_at_entry_hoist_point()
    assert probe.scans > 0 and builder._pos == 0
