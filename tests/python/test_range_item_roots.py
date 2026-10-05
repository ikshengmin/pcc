"""Materialized ranges retain their item root across the generated backedge."""
import pytest

from tests.python.test_slot_call_lexical_roots import _emit
from pcc.backend.self_backend_precise_stackmaps import build_stack_map_plans
from pcc.backend.self_backend_prepare import prepare_module_for_target
from pcc.backend.self_backend_x86_64_linux import _aggregate_returned_indirect
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.target_objects import emit_indexed_assembly, encode_assembly_object
from pcc.backend.elf_x86_64 import parse_relocatable


def _owned_object(text):
    plans = []
    assembly = emit_indexed_assembly(parse_self_backend_module(text), stack_map_plans_out=plans)
    encoded = encode_assembly_object(assembly, 'x86_64-unknown-linux-gnu', stack_map_plans=plans)
    obj = parse_relocatable(encoded)
    assert any(section.name == '.text' and section.data for section in obj.sections)
    assert any(section.name == '.pcc_stackmaps' and section.data for section in obj.sections)


@pytest.mark.parametrize('body', [
    '    return sorted(range(len(commands)), key=lambda index: (-sizes[index], index))\n',
    '    values = range(count)\n    return values\n',
    '    return range(5, 0, -2)\n',
    '    return range(0)\n',
    '    return take(values=range(count))\n',
    '    try:\n        return range(count)\n    except ValueError:\n        return []\n',
], ids=['scheduler-sorted-lambda', 'assigned', 'negative-step', 'empty', 'argument', 'handler'])
def test_range_item_frame_survives_backedge(body):
    source = 'def take(*, values):\n    return values\n'
    source += 'def probe(commands, sizes, count):\n' + body
    codegen, text = _emit(source, 'range_root')
    prepared = prepare_module_for_target(
        text, aggregate_returned_indirect=_aggregate_returned_indirect,
    )
    plans = build_stack_map_plans(prepared.functions, prepared.globals_, target='x86_64-linux')
    assert len(plans) == len(prepared.functions)
    _owned_object(text)


@pytest.mark.parametrize('expression', ['reversed(commands)', 'reversed([])', 'reversed({"a": 1, "b": 2})', 'take(values=reversed(commands))'])
def test_reversed_item_frames_survive_backedge(expression):
    source = 'def take(*, values):\n    return values\n'
    source += 'def probe(commands):\n    return ' + expression + '\n'
    _codegen, text = _emit(source, 'reversed_root')
    prepared = prepare_module_for_target(text, aggregate_returned_indirect=_aggregate_returned_indirect)
    build_stack_map_plans(prepared.functions, prepared.globals_, target='x86_64-linux')
    _owned_object(text)
