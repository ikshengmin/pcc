"""Private synthetic roots may move scalars; other roots keep atomic handoff."""

import re

import pytest

from pcc.frontends.python.codegen.call_object_lowering import CallObjectLoweringMixin
from pcc.ir.compat import ir
from tests.owned_ir_validation import verify_ir_text
from tests.python.test_explicit_machine_arithmetic_boundaries import (
    _pipeline_ir,
    _run_native_all_gc,
)


class MoveEmitter(CallObjectLoweringMixin):
    def __init__(self, source_kind, destination_kind, alias=False, stale=False):
        pointer = ir.IntType(8).as_pointer()
        word = ir.IntType(64)
        self.module = ir.Module(name='private_root_probe')
        self.current_function = ir.Function(
            self.module, ir.FunctionType(word, [pointer.as_pointer(), pointer.as_pointer()]),
            name='probe',
        )
        self.builder = ir.IRBuilder(self.current_function.append_basic_block('entry'))
        self.runtime = {'pcc_gc_root_move': ir.Function(
            self.module, ir.FunctionType(word, [pointer, pointer]), name='pcc_gc_root_move',
        )}
        self._slot_call_root_records = []
        self._slot_call_root_record_index = {}
        self.serial = 0

        def slot(kind, index):
            if kind == 'external':
                return self.current_function.args[index]
            if kind == 'global':
                value = ir.GlobalVariable(self.module, pointer, name='global_root')
                value.initializer = ir.Constant(pointer, None)
                return value
            value = self.builder.alloca(pointer, name='slot' + str(index))
            self.builder.store(ir.Constant(pointer, None), value)
            if kind != 'return':
                record = (value, None, kind == 'private')
                position = len(self._slot_call_root_records)
                self._slot_call_root_records.append(record)
                self._slot_call_root_record_index[id(value)] = (position, record)
            return value

        source = slot(source_kind, 0)
        destination = source if alias else slot(destination_kind, 1)
        if stale:
            self._slot_call_root_records = []
        result = self._emit_root_slot_move(
            destination, source, label='test.move', private_destination=destination_kind == 'return',
        )
        self.builder.ret(result)

    def _fresh(self, label):
        self.serial += 1
        return label + '.' + str(self.serial)

    def _as_gc_ptr(self, value):
        return self.builder.bitcast(value, ir.IntType(8).as_pointer())


@pytest.mark.parametrize('source,destination,eligible', (
    ('private', 'private', True),
    ('private', 'return', True),
    ('private', 'external', False),
    ('external', 'private', False),
    ('persistent', 'private', False),
    ('private', 'persistent', False),
    ('private', 'global', False),
    ('global', 'private', False),
))
def test_inline_move_requires_exclusive_slot_proof(source, destination, eligible):
    text = str(MoveEmitter(source, destination).module)
    verify_ir_text(text)
    assert ('root.move.publish' in text) is eligible
    assert text.count('call i64 (ptr, ptr) @pcc_gc_root_move(') == 1
    if eligible:
        publish = re.search(r'(?ms)^root.move.publish[^:]*:\n(.*?)(?=^\S|\Z)', text).group(1)
        assert publish.count('store ptr') == 2
        assert 'store ptr null' in publish
        assert '@pcc_gc_root_move' not in publish
        occupied = re.search(r'(?ms)^root.move.occupied[^:]*:\n(.*?)(?=^\S|\Z)', text).group(1)
        assert 'store ' not in occupied
        assert 'phi i64' in text
        assert '[-4,' in text


@pytest.mark.parametrize('fault', ('alias', 'stale-record'))
def test_alias_or_stale_proof_keeps_runtime_validation(fault):
    text = str(MoveEmitter('private', 'private', alias=fault == 'alias', stale=fault == 'stale-record').module)
    verify_ir_text(text)
    assert 'root.move.publish' not in text
    assert '@pcc_gc_root_move(' in text


PROGRAM = '''import gc

events = []
class Item:
    def __init__(self, value):
        self.value = value
    def __del__(self):
        events.append(self.value)
class Maker:
    def __add__(self, other):
        gc.collect()
        return Item("sum")
class Fail:
    def __add__(self, other):
        raise ValueError("original")
def combine(left, right):
    try:
        return left + right
    finally:
        gc.collect()
def choose(flag, left, right):
    return left if flag else right
def short(left, right):
    return left and right
def main():
    assert combine(0, 0) == 0
    assert combine(-9, 4) == -5
    assert combine(17, 25) == 42
    huge = 1267650600228229401496703205376
    assert combine(huge, huge) == 2535301200456458802993406410752
    assert choose(True, 7, 13) == 7
    assert choose(False, 7, 13) == 13
    assert short(0, 13) == 0
    assert short(7, 13) == 13
    held = ["held"]
    assert choose(True, held, None) is held
    assert short(7, held) is held
    result = combine(Maker(), None)
    gc.collect()
    assert result.value == "sum"
    assert events == []
    del result
    gc.collect()
    assert events == ["sum"]
    try:
        combine(Fail(), None)
    except ValueError as error:
        assert str(error) == "original"
    else:
        raise AssertionError("lost callback exception")
    assert "{}:{}".format(7, 13) == "7:13"
    print("PRIVATE_ROOT_MOVES_OK")
main()
'''


def test_actual_return_and_operand_ir_contains_scalar_and_heap_paths(tmp_path, monkeypatch):
    text = _pipeline_ir(tmp_path, monkeypatch, PROGRAM)
    assert 'root.move.scalar' in text
    assert 'root.move.publish' in text
    assert 'root.move.heap' in text
    assert '@pcc_gc_root_move(' in text


@pytest.mark.integration
def test_private_root_moves_native_all_gc(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    _run_native_all_gc(
        tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
        PROGRAM, 'PRIVATE_ROOT_MOVES_OK',
    )
