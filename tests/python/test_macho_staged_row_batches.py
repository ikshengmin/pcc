"""Bounded host row consumption keeps the canonical count and folding rules."""

from dataclasses import replace

import pytest

from pcc.backend import macho_host_prepare as host, macho_link as link
from pcc.backend import macho_spec as spec, native_object as native
from pcc.backend import macho_exec, macho_obj as obj
from tests.python.test_macho_staged_relocation_count import (
    _ADDEND, _NAMES, _SECTIONS, _SUBTRACTOR, _SYMBOLS, _raw, _row,
)


@pytest.mark.parametrize("prefix", (0, 1, 254, 255, 256, 257, 511))
@pytest.mark.parametrize("companion", (_ADDEND, _SUBTRACTOR))
def test_checked_batches_preserve_companions_and_canonical_records(prefix, companion):
    rows = [_row(address=8 * index) for index in range(prefix)]
    rows.extend((companion, _row(), _row(symbol=1, external=0)))
    data = _raw(rows)
    count = host._count_logical_staged_rows(data, _SECTIONS, _SYMBOLS, _NAMES)
    expected = list(host._logical_staged_rows(data, _SECTIONS, _SYMBOLS, _NAMES))
    actual = list(host._logical_staged_rows(data, _SECTIONS, _SYMBOLS, _NAMES, count))
    assert actual == expected
    assert len(actual) == count


def test_first_record_does_not_allocate_the_full_section_graph(monkeypatch):
    data = _raw([_row(address=8 * index) for index in range(4097)])
    count = host._count_logical_staged_rows(data, _SECTIONS, _SYMBOLS, _NAMES)
    original = link._folded_relocation_rows
    batches = []

    def observe(raw, *args, **kwargs):
        batches.append(len(raw))
        return original(raw, *args, **kwargs)

    monkeypatch.setattr(link, "_folded_relocation_rows", observe)
    rows = host._logical_staged_rows(data, _SECTIONS, _SYMBOLS, _NAMES, count)
    try:
        assert next(rows).offset == 0
        assert batches == [host._STAGED_ROW_BATCH]
        assert sum(1 for _ in rows) == count - 1
        assert max(batches) <= host._STAGED_ROW_BATCH + 1
        assert sum(batches) == count
    finally:
        rows.close()


def test_companion_at_boundary_is_one_bounded_batch(monkeypatch):
    data = _raw([_row()] * 255 + [_ADDEND, _row()] + [_row()] * 255)
    count = host._count_logical_staged_rows(data, _SECTIONS, _SYMBOLS, _NAMES)
    original = link._folded_relocation_rows
    sizes = []

    def observe(raw, *args, **kwargs):
        sizes.append(len(raw))
        return original(raw, *args, **kwargs)

    monkeypatch.setattr(link, "_folded_relocation_rows", observe)
    assert len(list(host._logical_staged_rows(data, _SECTIONS, _SYMBOLS, _NAMES, count))) == count
    assert sizes == [257, 255]


@pytest.mark.parametrize("fail", (False, True))
def test_early_close_and_constructor_error_retire_raw_iterator(monkeypatch, fail):
    data = _raw([_row()] * 600)
    count = host._count_logical_staged_rows(data, _SECTIONS, _SYMBOLS, _NAMES)
    original = host._raw_row_dicts
    closed = []

    def observe(data):
        try:
            yield from original(data)
        finally:
            closed.append(True)

    def refuse(*args):
        raise RuntimeError("injected record allocation failure")

    monkeypatch.setattr(host, "_raw_row_dicts", observe)
    if fail:
        monkeypatch.setattr(link, "new_relocation", refuse)
    rows = host._logical_staged_rows(data, _SECTIONS, _SYMBOLS, _NAMES, count)
    try:
        if fail:
            with pytest.raises(RuntimeError, match="injected record allocation"):
                next(rows)
        else:
            next(rows)
    finally:
        rows.close()
    assert closed == [True]


def test_bad_late_name_still_fails_in_count_before_payload(tmp_path, monkeypatch):
    from tests.python.test_macho_windowed_parity import _budget, _companion_inputs, _write_pcos

    paths = _write_pcos(tmp_path, _companion_inputs())
    original = host._raw_row_dicts
    opened = []
    # The first row would fail semantic bounds on the small fixture's section.
    # The later raw name must still win before opening ANY payload window.
    data = _raw([_row(address=999999)] * 600 + [_row(symbol=999999)])

    def invalid(raw):
        yield from original(data)

    def forbidden(*args, **kwargs):
        opened.append(True)
        raise AssertionError("payload opened before complete canonical count")

    def inject(event):
        if event == "source_view_begin":
            monkeypatch.setattr(host, "_raw_row_dicts", invalid)
            monkeypatch.setattr(host._StagedSectionPayloads, "window", forbidden)

    with pytest.raises(link.LinkError, match="symbol index"):
        host.prepare_executable_from_paths(paths, compact_budget=_budget(),
                                           _staged_rows=True, _phase_callback=inject)
    assert not opened


def test_checked_replay_rejects_mutable_input_and_changed_count():
    data = _raw([_row()])
    with pytest.raises(native.NativeObjectError, match="invalid checked"):
        list(host._logical_staged_rows(bytearray(data), _SECTIONS, _SYMBOLS, _NAMES, 1))
    with pytest.raises(native.NativeObjectError, match="count changed"):
        list(host._logical_staged_rows(data, _SECTIONS, _SYMBOLS, _NAMES, 0))


def test_replay_scratch_is_positive_and_bounded_independently_of_section_size():
    from tests.python.test_macho_windowed_parity import _budget

    ledger = host._Ledger(_budget())
    bound = host._staged_row_replay_scratch(257, ledger)
    assert 0 < host._staged_row_replay_scratch(1, ledger) < bound
    assert host._staged_row_replay_scratch(10_000_000, ledger) == bound


def _many_pointer_inputs(*, unresolved=False):
    from tests.python.test_macho_windowed_parity import _companion_inputs

    main, helper = _companion_inputs()
    sections, undefined = main.to_sections()
    index = next(i for i, section in enumerate(sections) if section.sectname == "__data")
    rows = tuple(obj.Relocation(i * 8, "_helper", spec.ARM64_RELOC_UNSIGNED, False, 3)
                 for i in reversed(range(599)))
    # Descending storage order visits this unresolved target before the first
    # batch boundary. Offset zero is visited after more than two full batches.
    rows = (obj.Relocation(599 * 8, "", spec.ARM64_RELOC_UNSIGNED, False, 3,
                          section=("__TEXT", "__text"),
                          target_offset=4 if unresolved else 12),) + rows
    if unresolved:
        rows = tuple(obj.Relocation(i * 8, "", spec.ARM64_RELOC_UNSIGNED, False, 3,
                                    section=("__TEXT", "__text"), target_offset=4)
                     for i in reversed(range(600)))
    sections[index] = replace(sections[index], data=b"\0" * 4800, relocations=rows)
    return [native.NativeObject.from_sections(sections, undefined=undefined), helper]


def _observe_store_closes(monkeypatch):
    closed = []
    for kind, cls in (("rows", host._StagedRelocationRows),
                      ("payload", host._StagedSectionPayloads)):
        original = cls.close

        def close(self, _original=original, _kind=kind, **kwargs):
            _original(self, **kwargs)
            closed.append((_kind, self.stream is None, self.active_bytes))

        monkeypatch.setattr(cls, "close", close)
    return closed


def _assert_stores_retired(closed, budget):
    assert {kind for kind, _, _ in closed} == {"rows", "payload"}
    # A close may occur inside the unwinding window before its active charge
    # retires; the final close for each owner must see zero active backing.
    assert all(is_closed for _, is_closed, _ in closed), closed
    assert {kind: active for kind, _, active in closed} == {"rows": 0, "payload": 0}
    assert budget.retained_bytes == 0


def test_large_staged_section_matches_eager_final_image(tmp_path, monkeypatch):
    from pathlib import Path
    from pcc.backend import macho_host_output as output
    from tests.python.test_macho_windowed_parity import _budget, _write_pcos

    paths = _write_pcos(tmp_path, _many_pointer_inputs())
    eager_budget, budget = _budget(), _budget()
    eager = macho_exec.prepare_executable_object(
        [native.decode_packed_native_object(Path(path).read_bytes()) for path in paths],
        _consume_inputs=True, _source_view=True, compact_budget=eager_budget,
    )
    expected = macho_exec.link_prepared_executable(eager, compact_budget=eager_budget)
    original = link._folded_relocation_rows
    sizes = []

    def observe(raw, *args, **kwargs):
        sizes.append(len(raw))
        return original(raw, *args, **kwargs)

    def phase(event):
        if event == "source_view_begin":
            monkeypatch.setattr(link, "_folded_relocation_rows", observe)
        elif event == "source_view_end":
            monkeypatch.setattr(link, "_folded_relocation_rows", original)

    view = host.prepare_executable_from_paths(
        paths, compact_budget=budget, _staged_rows=True, _phase_callback=phase,
    )
    assert max(sizes) == host._STAGED_ROW_BATCH
    assert sizes.count(host._STAGED_ROW_BATCH) >= 2
    pending, view = [view], None
    actual = output.link_owned_executable(pending.pop(), compact_budget=budget)
    assert actual == expected
    assert eager_budget.retained_bytes == budget.retained_bytes == 0


def test_later_batch_semantic_error_beats_earlier_deferred_target(tmp_path, monkeypatch):
    from tests.python.test_macho_windowed_parity import _budget, _write_pcos

    paths = _write_pcos(tmp_path, _many_pointer_inputs(unresolved=True))
    closed = _observe_store_closes(monkeypatch)
    original = native._validate_relocation
    normalize = link._normalized_section_target_value
    visited, targets = [], []

    def validate(section, row, *args):
        if section.sectname == "__data":
            visited.append(row.offset)
            if row.offset == 0:
                raise obj.MachOEmitError("semantic error after two batches")
        return original(section, row, *args)

    def bad_target(*args):
        targets.append(args[0])
        return normalize(*args) + 1

    def phase(event):
        if event == "source_view_begin":
            monkeypatch.setattr(native, "_validate_relocation", validate)
            monkeypatch.setattr(link, "_normalized_section_target_value", bad_target)

    budget = _budget()
    with pytest.raises(link.LinkError, match="semantic error after two batches"):
        host.prepare_executable_from_paths(
            paths, compact_budget=budget, _staged_rows=True, _phase_callback=phase,
        )
    assert targets and visited[0] == 599 * 8 and len(visited) == 600, (targets, visited)
    _assert_stores_retired(closed, budget)


def test_batch_scratch_refusal_precedes_materialization_and_retires_stores(tmp_path, monkeypatch):
    from tests.python.test_macho_windowed_parity import _budget, _companion_inputs, _write_pcos

    paths = _write_pcos(tmp_path, _companion_inputs())
    budget = _budget()
    check = budget.check_peak
    closed = _observe_store_closes(monkeypatch)
    refused = []

    def limited(raw, *, records=0, entries=0, label="stack-map"):
        if label == "Mach-O compact row validation scratch":
            assert not records and not entries
            budget.limit_bytes = budget.retained_bytes + raw - 1
            refused.append(raw)
        return check(raw, records=records, entries=entries, label=label)

    def forbidden(*args):
        pytest.fail("materialized rows before scratch admission")

    def phase(event):
        if event == "source_view_begin":
            monkeypatch.setattr(host, "_logical_staged_rows", forbidden)

    budget.check_peak = limited
    with pytest.raises(link.LinkError, match="Mach-O compact row validation scratch"):
        host.prepare_executable_from_paths(
            paths, compact_budget=budget, _staged_rows=True, _phase_callback=phase,
        )
    assert len(refused) == 1
    _assert_stores_retired(closed, budget)
