import weakref

import pytest

from pcc.backend import self_backend_precise_stackmaps as maps
from pcc.backend import self_backend_x86_64_linux as emitter
from pcc.backend import x86_64_asm_driver as assembler
from pcc.backend.target_objects import emit_indexed_assembly, encode_assembly_object
from tests.python.test_x86_64_emission_lifetime import ROOTED_PROGRAM, TARGET, _indexed_module


def _input(tmp_path):
    plans = []
    assembly = emit_indexed_assembly(_indexed_module(tmp_path, ROOTED_PROGRAM), stack_map_plans_out=plans)
    return assembly, plans


def test_explicit_consumer_releases_plans_before_encoding_and_preserves_bytes(tmp_path, monkeypatch):
    assembly, retained = _input(tmp_path)
    expected = encode_assembly_object(assembly, TARGET, stack_map_plans=retained)
    assert retained and all(plan.records for plan in retained)
    assembly, plans = _input(tmp_path)
    references = [weakref.ref(plan) for plan in plans]
    packed = False
    calls_after_pack = 0
    real_pack = maps.build_x86_64_stack_map_payload
    real_encode = assembler.encode_instruction

    def observe_pack(*args, **kwargs):
        nonlocal packed
        result = real_pack(*args, **kwargs)
        packed = True
        return result

    def observe_encode(*args, **kwargs):
        nonlocal calls_after_pack
        if packed:
            assert not plans
            assert all(reference() is None for reference in references)
            calls_after_pack += 1
        return real_encode(*args, **kwargs)

    monkeypatch.setattr(maps, 'build_x86_64_stack_map_payload', observe_pack)
    monkeypatch.setattr(assembler, 'encode_instruction', observe_encode)
    actual = encode_assembly_object(assembly, TARGET, stack_map_plans=plans, consume_stack_map_plans=True)
    assert packed and calls_after_pack
    assert actual == expected


def test_consumption_keeps_plans_if_final_offset_validation_fails(tmp_path):
    assembly, plans = _input(tmp_path)
    original = tuple(plans)
    assembly = assembly.replace('.section .pcc_stackmaps,"a",@progbits', '')
    with pytest.raises(assembler.X86EncodeError, match='empty section marker'):
        encode_assembly_object(assembly, TARGET, stack_map_plans=plans, consume_stack_map_plans=True)
    assert tuple(plans) == original


def test_consuming_path_rejects_immutable_plan_owner(tmp_path):
    assembly, plans = _input(tmp_path)
    with pytest.raises(assembler.X86EncodeError, match='mutable plan list'):
        assembler.assemble_file_with_stack_maps(
            assembly, tuple(plans), function_symbol=emitter._asm_symbol,
            block_label=emitter._block_label, consume_stack_map_plans=True,
        )
