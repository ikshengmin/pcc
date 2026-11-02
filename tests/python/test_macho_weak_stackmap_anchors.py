"""Physical weak-body stack-map anchors; object/link checks, not execution.

The six C++ originals reproduce the b215 cloud-Mac linker failures. Calls
and public addresses still coalesce; metadata retains its own emitted body.
"""

from dataclasses import replace
import subprocess

import pytest

from pcc.backend import macho_spec as spec
from pcc.backend.arm64_asm_driver import assemble_file, assemble_lines
from pcc.backend.macho_link import LinkError, link_relocatable_native
from pcc.backend.macho_obj import MachOEmitError, Relocation, Section, emit_object
from pcc.backend.native_object import (
    NativeObject,
    NativeObjectError,
    decode_packed_native_object,
    encode_native_object,
)
from pcc.backend.precise_stackmap import (
    ARCH_AARCH64,
    FunctionStackMap,
    PreciseStackMap,
    SAFEPOINT_ENTRY,
    SafepointRecord,
    decode_stack_map,
    encode_stack_map,
    function_address_offsets,
    function_id,
    render_stack_map_assembly,
    safepoint_id,
)
from tests.cpp.platform_execution import (
    DARWIN_TARGET,
    compile_target_objects,
    darwin_observer_object,
)
from tests.cpp.test_cpp_platform_execution import CASES as PLATFORM_CASES


_FAILED_CASE_NAMES = frozenset((
    "inline-c1-complete-class-value",
    "inline-method-later-field",
    "inline-static-receiver-order",
    "class-template-member-const-overload",
    "class-template-member-constructor-later-member",
    "default-receiver-before-default",
))
_FAILED_CASES = tuple(case for case in PLATFORM_CASES if case[0] in _FAILED_CASE_NAMES)


def _table(identity):
    return PreciseStackMap(ARCH_AARCH64, (FunctionStackMap(
        function_id(identity), 0, 8, 0,
        (SafepointRecord(safepoint_id(identity, 0, SAFEPOINT_ENTRY),
                         0, SAFEPOINT_ENTRY, ()),),
    ),))


def _stack_section(identity="first", **changes):
    payload = encode_stack_map(_table(identity))
    relocation = Relocation(function_address_offsets(payload)[0], "_f",
                            spec.ARM64_RELOC_UNSIGNED, False, length=3)
    return Section(
        "__pcc_stackmaps", "__DATA", payload, align_log2=3,
        relocations=(replace(relocation, **changes),),
    )


def _sections(identity="first", value=1, weak=True, structured=True, collision=False,
              recursive=False):
    lines = [".section __TEXT,__text,regular,pure_instructions", ".globl _f"]
    if weak:
        lines.append(".weak_definition _f")
    lines.extend(("_f:", "bl _f" if recursive else f"mov w0, #{value}", "ret"))
    lines.extend((".section __DATA,__data", ".quad _f"))
    if collision:
        lines.extend(("l_pcc_stackmap_body_0:", ".quad l_pcc_stackmap_body_1",
                      ".globl l_pcc_stackmap_body_2"))
    table = _table(identity)
    if not structured:
        lines.append(render_stack_map_assembly(table, ("_f",), target="aarch64-darwin"))
        return assemble_file("\n".join(lines))
    return assemble_lines(lines, (_stack_section(identity),))


def _representation(sections, undefined, kind):
    obj = NativeObject.from_sections(sections, undefined=undefined)
    if kind == "native":
        return obj
    if kind == "packed":
        return decode_packed_native_object(encode_native_object(obj))
    return obj.to_macho()


@pytest.mark.parametrize("structured", (False, True))
def test_weak_stackmap_keeps_zero_symbol_abi_and_public_function_pointer(structured):
    sections, undefined = _sections(structured=structured)
    assert undefined == []
    text = next(section for section in sections if section.is_code)
    stack = next(section for section in sections if section.sectname == "__pcc_stackmaps")
    data = next(section for section in sections if section.sectname == "__data")
    symbols = {symbol.name: symbol for symbol in text.symbols}
    public = symbols["_f"]
    relocation, = stack.relocations
    anchor = symbols[relocation.symbol]
    assert public.external and public.weak_definition
    assert not anchor.external and not anchor.weak_definition
    assert anchor.offset == public.offset == 0
    assert relocation.section is None and relocation.minuend is None
    assert relocation.type == spec.ARM64_RELOC_UNSIGNED and relocation.length == 3
    assert not relocation.pcrel and relocation.addend == 0
    assert stack.data[relocation.offset:relocation.offset + 8] == b"\0" * 8
    assert decode_stack_map(stack.data, expected_arch=ARCH_AARCH64) == _table("first")
    assert data.relocations[0].symbol == "_f"
    assert _sections(structured=False) == _sections(structured=True)


def test_body_anchor_avoids_both_defined_and_undefined_symbol_collisions():
    sections, undefined = _sections(collision=True)
    assert undefined == ["l_pcc_stackmap_body_1"]
    stack = next(section for section in sections if section.sectname == "__pcc_stackmaps")
    assert stack.relocations[0].symbol == "l_pcc_stackmap_body_3"
    data = next(section for section in sections if section.sectname == "__data")
    assert [symbol.name for symbol in data.symbols] == ["l_pcc_stackmap_body_0"]
    assert [relocation.symbol for relocation in data.relocations] == [
        "_f", "l_pcc_stackmap_body_1",
    ]


def test_body_anchor_does_not_resolve_an_unknown_subtractor_minuend():
    lines = [".section __TEXT,__text,regular,pure_instructions", ".globl _f",
             ".weak_definition _f", "_f:", "mov w0, #1", "ret"]
    difference = Section(
        "__const", "__DATA", b"\0" * 8,
        relocations=(Relocation(0, "_f", spec.ARM64_RELOC_SUBTRACTOR, False,
                                length=3, minuend="l_pcc_stackmap_body_0"),),
    )
    sections, undefined = assemble_lines(lines, (_stack_section(), difference))
    assert undefined == []
    stack = next(section for section in sections if section.sectname == "__pcc_stackmaps")
    assert stack.relocations[0].symbol == "l_pcc_stackmap_body_1"
    with pytest.raises(MachOEmitError, match="SUBTRACTOR targets unknown minuend"):
        emit_object(sections, undefined=undefined)


@pytest.mark.parametrize("field,value,message", (
    ("offset", 32.0, "relocation offset must be an integer"),
    ("type", False, "relocation type must be an integer"),
    ("length", 3.0, "relocation length must be an integer"),
    ("pcrel", 0, "relocation pcrel must be bool"),
    ("addend", 0.0, "relocation addend must be an integer"),
))
def test_body_anchor_does_not_launder_malformed_relocation_types(field, value, message):
    lines = [".section __TEXT,__text,regular,pure_instructions", ".globl _f",
             ".weak_definition _f", "_f:", "mov w0, #1", "ret"]
    original = _stack_section(**{field: value})
    sections, undefined = assemble_lines(lines, (original,))
    stack = next(section for section in sections if section.sectname == "__pcc_stackmaps")
    assert stack is original
    with pytest.raises(NativeObjectError, match=message):
        NativeObject.from_sections(sections, undefined=undefined)


def test_recursive_weak_call_is_not_redirected_to_the_metadata_anchor():
    sections, undefined = _sections(recursive=True)
    assert undefined == []
    text = next(section for section in sections if section.is_code)
    stack = next(section for section in sections if section.sectname == "__pcc_stackmaps")
    call, = text.relocations
    assert call.symbol == "_f" and call.type == spec.ARM64_RELOC_BRANCH26
    assert stack.relocations[0].symbol != "_f"


@pytest.mark.parametrize("kind", ("native", "packed", "macho"))
@pytest.mark.parametrize("strengths,winner", (((True, True), 0), ((True, False), 8), ((False, True), 0)))
def test_stackmap_addresses_stay_with_bodies_when_public_symbol_coalesces(kind, strengths, winner):
    inputs = []
    for identity, value, weak in zip(("first", "second"), (1, 2), strengths):
        sections, undefined = _sections(identity, value, weak)
        inputs.append(_representation(sections, undefined, kind))
    merged = link_relocatable_native(inputs)
    public = next(symbol for symbol in merged.symbols if symbol.name == "_f")
    assert public.offset == winner
    stack = next(section for section in merged.sections if section.sectname == "__pcc_stackmaps")
    decoded = decode_stack_map(stack.data, expected_arch=ARCH_AARCH64)
    relocations = {relocation.offset: relocation for relocation in stack.relocations}
    expected = {function_id("first"): 0, function_id("second"): 8}
    for function, offset in zip(decoded.functions, function_address_offsets(stack.data)):
        relocation = relocations[offset]
        target = merged.symbols[relocation.symbol_index]
        assert target.offset == expected[function.function_id]
        assert not target.weak_definition
        assert stack.data[offset:offset + 8] == b"\0" * 8
    data = next(section for section in merged.sections if section.sectname == "__data")
    assert all(merged.symbols[row.symbol_index] == public for row in data.relocations)


@pytest.mark.parametrize("kind", ("native", "packed", "macho"))
def test_raw_symbolic_weak_stackmap_and_duplicate_ids_still_fail_closed(kind):
    sections, undefined = _sections()
    raw = []
    for section in sections:
        if section.sectname == "__pcc_stackmaps":
            section = replace(section, relocations=tuple(
                replace(row, symbol="_f") for row in section.relocations
            ))
        raw.append(section)
    with pytest.raises(LinkError, match="metadata requires a physical"):
        link_relocatable_native([_representation(raw, undefined, kind)])
    valid = _representation(sections, undefined, kind)
    with pytest.raises(LinkError, match="duplicate stable function id"):
        link_relocatable_native([valid, valid])


def test_all_six_original_ci_cases_remain_in_the_regression_inventory():
    assert len(_FAILED_CASES) == 6
    assert {case[0] for case in _FAILED_CASES} == _FAILED_CASE_NAMES


@pytest.mark.usefixtures("precise_stackmap_tables")
@pytest.mark.parametrize("name,source,value", _FAILED_CASES, ids=[case[0] for case in _FAILED_CASES])
def test_six_original_cpp_failures_have_local_stackmap_targets_and_link(name, source, value, monkeypatch):
    assert {case[0] for case in _FAILED_CASES} == _FAILED_CASE_NAMES

    def forbidden(*args, **kwargs):
        raise AssertionError("owned object/link check invoked an external process")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    compiled = compile_target_objects(((name + ".cpp", source),), target=DARWIN_TARGET)
    obj = spec.parse_object(compiled.objects[0])
    symbols = obj.symbols()
    weak = [symbol for symbol in symbols if symbol["n_desc"] & spec.N_WEAK_DEF]
    assert weak
    stack = next(section for section in obj.sections()
                 if section["sectname_str"] == "__pcc_stackmaps")
    targets = []
    for relocation in obj.relocations(stack):
        assert relocation["r_extern"] == 1
        target = symbols[relocation["r_symbolnum"]]
        assert not target["n_desc"] & spec.N_WEAK_DEF
        targets.append(target)
    for public in weak:
        assert any(not target["n_type"] & spec.N_EXT
                   and target["n_sect"] == public["n_sect"]
                   and target["n_value"] == public["n_value"] for target in targets)
    # Existing platform native tests remain the execution gate; this only
    # closes the exact owned-link failure with unchanged original inputs.
    merged = link_relocatable_native([
        darwin_observer_object(compiled.entry_symbol), *compiled.objects,
    ])
    assert any(symbol.weak_definition for symbol in merged.symbols)
