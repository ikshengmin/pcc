"""The owned final link validates each relocation once.

``decode_packed_native_object`` validates every relocation record against its
input section.  ``link_relocatable_native`` then shifts the records into the
merged sections, and ``OwnedMergedSourceView`` used to run the full
``_validate_relocation`` over all of them again -- 13.7% of a native Stage2
link.  Records copied from packed inputs now form a proven prefix per merged
section when the merge kept them in range and on the instruction grid; the
view only bounds-checks and claims their offsets.  Records the merge creates
(rebased section targets) and records from any other input source are still
validated in full.
"""
from __future__ import annotations

import pytest

from pcc.backend import macho_spec as spec
from pcc.backend import native_object as native_object_module
from pcc.backend.macho_exec import link_executable
from pcc.backend.macho_link import link_relocatable_native
from pcc.backend.macho_obj import (
    DATA_SECTION_FLAGS,
    TEXT_SECTION_FLAGS,
    Relocation,
    Section,
    TextSymbol,
)
from pcc.backend.native_object import (
    NativeObject,
    NativeObjectError,
    OwnedMergedSourceView,
    decode_packed_native_object,
    encode_native_object,
)

_RET = bytes.fromhex("c0035fd6")
_BL_PLACEHOLDER = bytes.fromhex("00000094")


def _caller() -> NativeObject:
    return NativeObject.from_sections([Section(
        sectname="__text", segname="__TEXT", flags=TEXT_SECTION_FLAGS,
        align_log2=2, data=_BL_PLACEHOLDER + _BL_PLACEHOLDER + _RET,
        symbols=(TextSymbol("_main", 0),),
        relocations=(
            Relocation(offset=0, symbol="_helper", type=spec.ARM64_RELOC_BRANCH26, pcrel=True),
            Relocation(offset=4, symbol="_helper", type=spec.ARM64_RELOC_BRANCH26, pcrel=True),
        ),
    )], undefined=["_helper"])


def _helper() -> NativeObject:
    return NativeObject.from_sections([Section(
        sectname="__text", segname="__TEXT", flags=TEXT_SECTION_FLAGS,
        align_log2=2, data=_RET, symbols=(TextSymbol("_helper", 0),),
    )])


def _packed(obj: NativeObject):
    return decode_packed_native_object(encode_native_object(obj))


def _count_full_validations(monkeypatch) -> list:
    calls = []
    original = native_object_module._validate_relocation

    def counting(section, relocation, known, section_by_name):
        calls.append(relocation.offset)
        return original(section, relocation, known, section_by_name)

    monkeypatch.setattr(native_object_module, "_validate_relocation", counting)
    return calls


def test_packed_inputs_link_identically_without_revalidation(monkeypatch):
    expected = link_executable([_caller(), _helper()])
    inputs = [_packed(_caller()), _packed(_helper())]
    calls = _count_full_validations(monkeypatch)
    actual = link_executable(
        inputs, _consume_inputs=True, _direct_source_view=True,
    )
    assert actual == expected
    assert calls == []


def test_non_packed_input_closes_the_proven_prefix(monkeypatch):
    inputs = [_caller(), _packed(_helper())]
    calls = _count_full_validations(monkeypatch)
    view = link_relocatable_native(inputs, _source_view=True)
    assert sorted(calls) == [0, 4]
    assert view.sections()[0]["nreloc"] == 2


def test_rebased_section_targets_are_still_validated(monkeypatch):
    source = NativeObject.from_sections([
        Section(
            sectname="__text", segname="__TEXT", flags=TEXT_SECTION_FLAGS,
            align_log2=2, data=b"\x40\x05\x80\x52" + _RET,
            symbols=(TextSymbol("_main", 0),),
        ),
        Section(
            sectname="__data", segname="__DATA", flags=DATA_SECTION_FLAGS,
            align_log2=3, data=b"\0" * 8,
            symbols=(TextSymbol("_value", 0),),
        ),
        Section(
            sectname="__ptrs", segname="__DATA", flags=DATA_SECTION_FLAGS,
            align_log2=3, data=b"\0" * 8,
            relocations=(Relocation(
                0, "", spec.ARM64_RELOC_UNSIGNED, False, length=3,
                section=("__DATA", "__data"), target_offset=0,
            ),),
        ),
    ])
    expected = link_executable([source])
    inputs = [_packed(source)]
    calls = _count_full_validations(monkeypatch)
    actual = link_executable(
        inputs, _consume_inputs=True, _direct_source_view=True,
    )
    assert actual == expected
    assert calls == [0]


def test_data_input_merged_into_a_code_section_is_revalidated(monkeypatch):
    """Decode held a data section's records to no instruction grid; once
    attributes make the merged section code, the view checks them in full."""
    data_flags = TEXT_SECTION_FLAGS & ~spec.S_ATTR_PURE_INSTRUCTIONS
    data_side = NativeObject.from_sections([Section(
        sectname="__text", segname="__TEXT", flags=data_flags,
        align_log2=3, data=b"\0" * 8, symbols=(TextSymbol("_table", 0),),
        relocations=(Relocation(
            0, "_main", spec.ARM64_RELOC_UNSIGNED, False, length=3,
        ),),
    )], undefined=["_main"])
    code_side = NativeObject.from_sections([Section(
        sectname="__text", segname="__TEXT", flags=TEXT_SECTION_FLAGS,
        align_log2=2, data=_RET, symbols=(TextSymbol("_main", 0),),
    )])
    inputs = [_packed(data_side), _packed(code_side)]
    calls = _count_full_validations(monkeypatch)
    link_relocatable_native(inputs, _source_view=True)
    assert calls == [0]


def _one_section(relocations) -> tuple:
    return (Section(
        sectname="__text", segname="__TEXT", flags=TEXT_SECTION_FLAGS,
        align_log2=2, data=_BL_PLACEHOLDER + _RET,
        symbols=(TextSymbol("_main", 0),),
        relocations=list(relocations),
    ),)


def test_proven_records_still_fail_closed_on_range_and_duplicates():
    branch = spec.ARM64_RELOC_BRANCH26
    outside = Relocation(offset=8, symbol="_x", type=branch, pcrel=True)
    with pytest.raises(NativeObjectError, match="outside"):
        OwnedMergedSourceView(
            _one_section([outside]), undefined=["_x"], proven_relocations=(1,),
        )
    twice = Relocation(offset=0, symbol="_x", type=branch, pcrel=True)
    with pytest.raises(NativeObjectError, match="multiple relocation"):
        OwnedMergedSourceView(
            _one_section([twice, twice]), undefined=["_x"],
            proven_relocations=(2,),
        )
    with pytest.raises(NativeObjectError, match="proven relocation prefix"):
        OwnedMergedSourceView(
            _one_section([twice]), undefined=["_x"], proven_relocations=(2,),
        )


def test_records_after_the_prefix_are_validated_in_full():
    branch = spec.ARM64_RELOC_BRANCH26
    good = Relocation(offset=0, symbol="_x", type=branch, pcrel=True)
    unknown = Relocation(offset=4, symbol="_nowhere", type=branch, pcrel=True)
    with pytest.raises(NativeObjectError, match="unknown symbol"):
        OwnedMergedSourceView(
            _one_section([good, unknown]), undefined=["_x"],
            proven_relocations=(1,),
        )
