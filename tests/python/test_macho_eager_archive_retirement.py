"""Host lifetime and image controls for native-capable eager Mach-O linking.

These tests run the actual merge/link code on CPython. They neither execute
arm64 code nor qualify native GC, speed, Stage1, or the self-host fixed point.
"""

import hashlib
import weakref

import pytest

from pcc.backend import macho_exec, macho_link, macho_spec as spec
from pcc.backend import precise_stackmap as maps
from pcc.backend.ar_writer import write_archive
from pcc.backend.macho_archive import Member
from pcc.backend.macho_obj import Relocation, Section
from pcc.backend.native_object import (
    NativeObject,
    decode_packed_native_object,
    encode_native_object,
)
from tests.python.test_macho_archive_symbol_state import _unit


def _member(name, references=()):
    ordinary = _unit(name, references)
    roots = (maps.StackMapLocation(
        maps.LOCATION_STACK_INDIRECT, maps.LOCATION_MANAGED, register=29, offset=-8,
    ),)
    payload = maps.encode_stack_map(maps.PreciseStackMap(
        maps.ARCH_AARCH64, (maps.FunctionStackMap(
            function_id=maps.function_id(name), function_address=0,
            code_size=4, frame_size=16,
            records=(maps.SafepointRecord(
                maps.function_id(name), 0, maps.SAFEPOINT_CALL, roots,
            ),),
        ),),
    ))
    offset, = maps.function_address_offsets(payload)
    sections, _undefined = ordinary.to_sections()
    sections.append(Section(
        "__pcc_stackmaps", "__DATA", data=payload, align_log2=3,
        relocations=(Relocation(offset, name, spec.ARM64_RELOC_UNSIGNED, False, 3),),
    ))
    return NativeObject.from_sections(sections, undefined=references)


def _fixture(last_selected=True):
    entry = _unit("_main", ("_needed", "_optional"), weak=("_optional",))
    early = _member("_early", ("_main",))
    needed = _member("_needed", ("_early", "_next") if last_selected else ("_early",))
    following = _member("_next")
    groups = [
        [("early.o", early.to_macho()), ("needed.o", needed.to_macho()),
         ("duplicate.o", _unit("_main").to_macho()),
         ("optional.o", _unit("_optional", ("_unprovided",)).to_macho()),
         ("unused.o", _unit("_unused").to_macho())],
        [("next.o", following.to_macho()), ("last-unused.o", _unit("_last_unused").to_macho())],
    ]
    archives = [write_archive(group) for group in groups]
    selected = [(0, "early.o"), (0, "needed.o")]
    if last_selected:
        selected.append((1, "next.o"))
    expected_inputs = [entry, early, needed] + ([following] if last_selected else [])
    receipts = [{
        "archive_index": index, "member": name,
        "archive_sha256": hashlib.sha256(archives[index]).hexdigest(),
        "member_sha256": hashlib.sha256(dict(groups[index])[name]).hexdigest(),
    } for index, name in selected]
    return entry, archives, selected, expected_inputs, receipts


def _observe_selection(monkeypatch):
    read = macho_exec.read_archive
    state = macho_exec._external_symbol_state
    members, symbol_states, retired = [], [], []
    archive_count = 0

    class TrackedBytes(bytes):
        def __new__(cls, value, key):
            result = super().__new__(cls, value)
            result.key = key
            return result

        def __del__(self):
            retired.append(self.key)

    def observe_read(data):
        nonlocal archive_count
        index = archive_count
        archive_count += 1
        result = [Member(
            item.name, TrackedBytes(item.data, (index, item.name)),
            item.defines, item.undefined,
        ) for item in read(data)]
        members.extend((index, item.name, weakref.ref(item)) for item in result)
        return result

    def observe_state(objects):
        result = state(objects)
        symbol_states.extend(weakref.ref(item) for item in result)
        return result

    monkeypatch.setattr(macho_exec, "read_archive", observe_read)
    monkeypatch.setattr(macho_exec, "_external_symbol_state", observe_state)
    return members, symbol_states, retired


@pytest.mark.parametrize("consume,source_view", ((False, False), (True, False), (True, True)))
@pytest.mark.parametrize("receipts", (False, True))
@pytest.mark.parametrize("last_selected", (False, True))
@pytest.mark.parametrize("table_format", ("2", "3"))
def test_eager_archive_scratch_retires_before_merge_and_preserves_image(
    monkeypatch, consume, source_view, receipts, last_selected, table_format,
):
    monkeypatch.setenv(maps.PRECISE_STACKMAP_FORMAT_ENV, table_format)
    entry, archives, selected, expected_inputs, expected_receipts = _fixture(last_selected)
    expected = macho_exec.link_executable(expected_inputs)
    # Both input representations use the actual eager entry; no host windowed
    # or native-execution shim supplies the behavior being checked here.
    if receipts:
        entry = decode_packed_native_object(encode_native_object(entry))
    inputs = [entry]
    original_archives = list(archives)
    members, symbol_states, retired = _observe_selection(monkeypatch)
    validate = macho_exec._validate_input_load_commands
    merge = macho_exec.link_relocatable_native
    merge_maps = macho_link.merge_stack_map_payloads
    events = []

    class Receipts(list):
        def append(self, item):
            # Receipt callbacks must still see the current archive's census.
            assert all(ref() is not None for index, _name, ref in members
                       if index == item["archive_index"])
            events.append("receipt")
            super().append(item)

    output_receipts = Receipts() if receipts else None

    def validate_before_retirement(objects):
        if members:
            # The selected-input validation boundary precedes retirement.
            assert all(ref() is not None for index, _name, ref in members if index == 1)
            events.append("validate-selected")
        return validate(objects)

    def merge_after_retirement(objects, **kwargs):
        assert members and all(ref() is None for _index, _name, ref in members)
        assert symbol_states and all(ref() is None for ref in symbol_states)
        unused = {(index, name) for index, name, _ref in members} - set(selected)
        assert set(retired) == unused
        events.append("merge")
        return merge(objects, **kwargs)

    def maps_after_input_retirement(*args, **kwargs):
        if consume:
            # The core has now joined payload views and released each source.
            # No outer census/pulled/member alias may keep selected bytes alive.
            assert set(selected) <= set(retired)
        else:
            assert set(selected).isdisjoint(retired)
        events.append("stackmaps")
        return merge_maps(*args, **kwargs)

    monkeypatch.setattr(macho_exec, "_validate_input_load_commands", validate_before_retirement)
    monkeypatch.setattr(macho_exec, "link_relocatable_native", merge_after_retirement)
    monkeypatch.setattr(macho_link, "merge_stack_map_payloads", maps_after_input_retirement)
    prepared = macho_exec.prepare_executable_object(
        inputs, archives=archives, archive_selections=output_receipts,
        _consume_inputs=consume, _source_view=source_view,
    )
    assert macho_exec.link_prepared_executable(prepared) == expected
    assert events == (["receipt"] * len(selected) if receipts else []) + [
        "validate-selected", "merge", "stackmaps",
    ]
    assert inputs == ([] if consume else [entry])
    if not consume:
        assert inputs[0] is entry
    assert archives == original_archives
    assert all(actual is original for actual, original in zip(archives, original_archives))
    assert output_receipts == (expected_receipts if receipts else None)
    assert set(retired) == {(index, name) for index, name, _ref in members}


def test_eager_archive_merge_failure_does_not_keep_private_census(monkeypatch):
    entry, archives, selected, _expected, _receipts = _fixture()
    members, symbol_states, retired = _observe_selection(monkeypatch)
    error = macho_link.LinkError("deliberate selected merge failure")

    def fail(objects, **kwargs):
        assert all(ref() is None for _index, _name, ref in members)
        assert all(ref() is None for ref in symbol_states)
        raise error

    monkeypatch.setattr(macho_exec, "link_relocatable_native", fail)
    with pytest.raises(macho_link.LinkError) as caught:
        macho_exec.prepare_executable_object([entry], archives=archives, _consume_inputs=True)
    assert caught.value is error
    unused = {(index, name) for index, name, _ref in members} - set(selected)
    assert unused <= set(retired)
    assert all(ref() is None for _index, _name, ref in members)


def test_eager_archive_validation_failure_still_precedes_retirement(monkeypatch):
    entry, archives, _selected, _expected, _receipts = _fixture()
    members, _symbol_states, _retired = _observe_selection(monkeypatch)
    validate = macho_exec._validate_input_load_commands
    error = macho_link.LinkError("deliberate selected validation failure")

    def fail_selected(objects):
        if members:
            assert all(ref() is not None for index, _name, ref in members if index == 1)
            raise error
        return validate(objects)

    def reject_merge(*args, **kwargs):
        pytest.fail("selected-input validation failure entered merge")

    monkeypatch.setattr(macho_exec, "_validate_input_load_commands", fail_selected)
    monkeypatch.setattr(macho_exec, "link_relocatable_native", reject_merge)
    with pytest.raises(macho_link.LinkError) as caught:
        macho_exec.prepare_executable_object([entry], archives=archives, _consume_inputs=True)
    assert caught.value is error
