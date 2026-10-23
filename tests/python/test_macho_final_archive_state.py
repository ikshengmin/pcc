"""Host-only archive-state lifecycle; no compiler or executable invocation."""

import struct

import pytest

from pcc.backend import macho_exec, macho_spec as spec
from pcc.backend.ar_writer import write_archive
from pcc.backend.macho_link import LinkError
from pcc.backend.macho_obj import Section, TEXT_SECTION_FLAGS, TextSymbol
from pcc.backend.native_object import NativeObject


def _unit(name, references=()):
    return NativeObject.from_sections(
        [Section(
            sectname="__text", segname="__TEXT", data=b"\xc0\x03\x5f\xd6",
            flags=TEXT_SECTION_FLAGS, align_log2=2,
            symbols=(TextSymbol(name, 0),),
        )],
        undefined=tuple(references),
    )


def _archives():
    return [
        write_archive([
            ("early.o", _unit("_early", ("_main",)).to_macho()),
            ("late.o", _unit("_late", ("_early", "_next")).to_macho()),
            ("unused.o", _unit("_unused").to_macho()),
        ]),
        write_archive([
            ("duplicate.o", _unit("_early").to_macho()),
            ("next.o", _unit("_next", ("_early",)).to_macho()),
        ]),
    ]


def _observe(monkeypatch):
    events = []
    state = macho_exec._external_symbol_state
    validate = macho_exec._validate_input_load_commands
    read = macho_exec.read_archive
    output = object()

    def scan(objects):
        result = state(objects)
        events.append(("state", result))
        return result

    def validate_inputs(objects):
        events.append(("validate", len(objects)))
        return validate(objects)

    def read_input(data):
        events.append(("read",))
        return read(data)

    def merge(objects, **kwargs):
        events.append(("merge", len(objects), kwargs))
        return output

    monkeypatch.setattr(macho_exec, "_external_symbol_state", scan)
    monkeypatch.setattr(macho_exec, "_validate_input_load_commands", validate_inputs)
    monkeypatch.setattr(macho_exec, "read_archive", read_input)
    monkeypatch.setattr(macho_exec, "link_relocatable_native", merge)
    return events, output


@pytest.mark.parametrize("container", (list, tuple))
@pytest.mark.parametrize("count", (0, 1, 2))
@pytest.mark.parametrize("consume", (False, True))
def test_final_builtin_archive_does_not_rescan_discarded_state(
    monkeypatch, container, count, consume,
):
    archives = container(_archives()[:count])
    inputs = [_unit("_main", ("_late",))]
    original = inputs[:]
    selected = []
    events, output = _observe(monkeypatch)
    assert macho_exec.prepare_executable_object(
        inputs, archives=archives, archive_selections=selected,
        _consume_inputs=consume,
    ) is output
    assert inputs == ([] if consume else original)
    states = [event[1] for event in events if event[0] == "state"]
    assert len(states) == count
    if count:
        assert states[0] == ({"_main"}, {"_late"})
    if count == 2:
        assert states[1] == ({"_main", "_early", "_late"}, {"_next"})
    expected_names = ["early.o", "late.o", "next.o"][:0 if not count else count + 1]
    assert [row["member"] for row in selected] == expected_names
    assert [row["archive_index"] for row in selected] == ([0, 0, 1][:len(expected_names)])
    assert [event[0] for event in events] == (
        ["validate"] + ["state", "read"] * count
        + (["validate"] if count else []) + ["merge"]
    )
    assert events[-1] == ("merge", 1 + len(expected_names), {
        "_consume_inputs": consume, "_source_view": False,
        "_final_executable": True,
    })


class _ListWithoutLength(list):
    def __len__(self):
        raise AssertionError("custom archive length must not be queried")

    def __bool__(self):
        return True


class _TupleWithoutLength(tuple):
    def __len__(self):
        raise AssertionError("custom archive length must not be queried")

    def __bool__(self):
        return True


@pytest.mark.parametrize("container", (iter, _ListWithoutLength, _TupleWithoutLength))
def test_custom_archive_collection_keeps_original_rescans(monkeypatch, container):
    events, output = _observe(monkeypatch)
    selected = []
    assert macho_exec.prepare_executable_object(
        [_unit("_main", ("_late",))], archives=container(_archives()),
        archive_selections=selected,
    ) is output
    assert [event[0] for event in events] == [
        "validate", "state", "read", "state", "read", "state", "validate", "merge",
    ]
    assert [row["member"] for row in selected] == ["early.o", "late.o", "next.o"]


def test_unsized_iterator_failure_stays_after_completed_archive_rescan(monkeypatch):
    archives = _archives()
    events, _output = _observe(monkeypatch)

    def failing_iterator():
        yield archives[0]
        events.append(("iterator-error",))
        raise RuntimeError("archive iterator failed")

    with pytest.raises(RuntimeError, match="archive iterator failed"):
        macho_exec.prepare_executable_object(
            [_unit("_main", ("_late",))], archives=failing_iterator(),
        )
    assert [event[0] for event in events] == [
        "validate", "state", "read", "state", "iterator-error",
    ]


@pytest.mark.parametrize("container", (list, tuple))
def test_malformed_later_archive_keeps_between_archive_state_and_error(monkeypatch, container):
    events, _output = _observe(monkeypatch)
    with pytest.raises(LinkError, match="archive 1 is outside the proven subset"):
        macho_exec.prepare_executable_object(
            [_unit("_main", ("_late",))],
            archives=container([_archives()[0], b"not an archive"]),
        )
    assert [event[0] for event in events] == ["validate", "state", "read", "state", "read"]


def test_input_validation_still_precedes_archive_state(monkeypatch):
    events, _output = _observe(monkeypatch)
    with pytest.raises(LinkError, match="not a valid object"):
        macho_exec.prepare_executable_object([b"not an object"], archives=_archives())
    assert events == []


def test_final_merge_error_is_not_suppressed(monkeypatch):
    events, _output = _observe(monkeypatch)

    def reject(objects, **kwargs):
        events.append(("merge-error",))
        raise LinkError("final merge validation failed")

    monkeypatch.setattr(macho_exec, "link_relocatable_native", reject)
    with pytest.raises(LinkError, match="final merge validation failed"):
        macho_exec.prepare_executable_object(
            [_unit("_main", ("_late",))], archives=_archives(),
        )
    assert [event[0] for event in events][-2:] == ["validate", "merge-error"]


def test_selected_archive_load_command_validation_still_runs(monkeypatch):
    bad = bytearray(_unit("_late").to_macho())
    build = spec.parse_object(bytes(bad)).command(spec.LC_BUILD_VERSION)
    assert build is not None
    struct.pack_into("<I", bad, build.offset, spec.LC_LINKER_OPTION)
    archive = write_archive([("late.o", bytes(bad))])
    events, _output = _observe(monkeypatch)
    with pytest.raises(LinkError, match="input 1 has unsupported load command"):
        macho_exec.prepare_executable_object(
            [_unit("_main", ("_late",))], archives=[archive],
        )
    assert [event[0] for event in events] == ["validate", "state", "read", "validate"]


def test_builtin_list_growth_keeps_state_for_new_next_archive(monkeypatch):
    first, following = _archives()
    archives = [first]
    events, output = _observe(monkeypatch)
    read = macho_exec.read_archive

    def append_next(data):
        if data is first:
            archives.append(following)
        return read(data)

    monkeypatch.setattr(macho_exec, "read_archive", append_next)
    selected = []
    assert macho_exec.prepare_executable_object(
        [_unit("_main", ("_late",))], archives=archives,
        archive_selections=selected,
    ) is output
    assert [event[0] for event in events] == [
        "validate", "state", "read", "state", "read", "validate", "merge",
    ]
    assert [row["member"] for row in selected] == ["early.o", "late.o", "next.o"]
