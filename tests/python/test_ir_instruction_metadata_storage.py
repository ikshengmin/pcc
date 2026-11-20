"""Unannotated builder records must not own unused metadata containers."""

from copy import copy

import pytest

from pcc.ir import ir


def _builder(monkeypatch, direct):
    for key in ("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "PCC_DIRECT_INDEXED_KERNEL_EMIT"):
        monkeypatch.setenv(key, "1" if direct else "0")
    for key in ("PCC_DIRECT_INDEXED_KERNEL_VALIDATE", "PCC_TEXT_INDEXED_KERNEL_EMIT"):
        monkeypatch.delenv(key, raising=False)
    module = ir.Module(name="metadata_test")
    i32 = ir.IntType(32)
    func = ir.Function(module, ir.FunctionType(i32, [i32]), name="probe")
    block = func.append_basic_block("entry")
    return module, func, block, ir.IRBuilder(block)


@pytest.mark.parametrize("direct", [False, True])
def test_unannotated_record_owns_no_metadata_dict(monkeypatch, direct):
    module, func, block, builder = _builder(monkeypatch, direct)
    result = builder.add(func.args[0], ir.Constant(ir.IntType(32), 7))
    builder.ret(result)
    assert [rec.opname for rec in block.instructions] == ["add", "ret"]
    if direct:
        assert all(rec._direct_record_id >= 0 and not rec.text for rec in block.instructions)
        assert module.direct_indexed_module().functions[0].name == "probe"
        assert module._direct_indexed_fallback_records == 0
    # Inspect backing fields without invoking the on-demand mapping getter.
    for rec in block.instructions:
        assert not any(isinstance(v, dict) for v in vars(rec).values())


def test_metadata_mapping_stays_writable_stable_and_per_instruction(monkeypatch):
    module, func, block, builder = _builder(monkeypatch, False)
    result = builder.add(func.args[0], ir.Constant(ir.IntType(32), 7))
    builder.ret(result)
    first, second = block.instructions
    alias = first._metadata
    assert isinstance(alias, dict) and alias == {}
    assert second._metadata is not alias and first._metadata is alias
    node = module.add_metadata(["marker"])
    alias["mark"] = node
    assert first._metadata["mark"] is node and second._metadata == {}
    assert copy(first)._metadata is alias and alias.copy() is not alias
    replacement = {}
    first._metadata = replacement
    assert first._metadata is replacement and alias["mark"] is node


def test_debug_stamp_and_replacement_keep_text_and_owner_in_sync(monkeypatch):
    module, func, block, builder = _builder(monkeypatch, False)
    old = module.add_metadata(["first"])
    new = module.add_metadata(["second"])
    builder.debug_location = old
    result = builder.add(func.args[0], ir.Constant(ir.IntType(32), 7))
    builder.debug_location = None
    builder.ret(result)
    tagged, plain = block.instructions
    alias = tagged._metadata
    assert alias["dbg"] is old and "!dbg" not in plain.text
    tagged.set_metadata("dbg", new)
    builder.debug_location = old
    assert builder._stamp(tagged) is tagged and alias["dbg"] is new
    assert tagged.text.count("!dbg") == 1
    assert tagged.text.endswith(", !dbg " + new.get_reference())
    assert block._text_lines[0] == tagged.text and tagged.text in str(module)
    with pytest.raises(TypeError, match="metadata node"):
        plain.set_metadata("bad", object())
    assert not any(isinstance(v, dict) for v in vars(plain).values())
