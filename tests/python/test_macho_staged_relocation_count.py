"""Host-only count/materialize parity; no native execution qualification."""

import pytest

from pcc.backend import (
    macho_host_prepare as host,
    macho_link as link,
    macho_spec as spec,
    native_object as native,
)


def _row(address=0, symbol=0, pcrel=0, length=3, external=1,
         kind=spec.ARM64_RELOC_UNSIGNED):
    return (address, symbol, pcrel, length, external, kind)


_SYMBOLS = [{"name": "_main", "n_type": spec.N_SECT | spec.N_EXT}]
_SECTIONS = [{"segname_str": "__TEXT", "sectname_str": "__text"}]
_NAMES = ("_main",)
_ADDEND = _row(symbol=7, external=0, kind=spec.ARM64_RELOC_ADDEND)
_SUBTRACTOR = _row(kind=spec.ARM64_RELOC_SUBTRACTOR)


def _raw(rows):
    return b"".join(native._FINAL_LINK_RELOCATION.pack(*row) for row in rows)


_CASES = (
    (),
    (_row(),),
    (_ADDEND, _row()),
    (_SUBTRACTOR, _row()),
    (_row(symbol=1, external=0),),
    (_row(), _row(address=8), _ADDEND, _row(), _SUBTRACTOR, _row()),
    (_ADDEND,),
    (_SUBTRACTOR,),
    (_ADDEND, _ADDEND),
    (_ADDEND, _SUBTRACTOR),
    (_ADDEND, _row(address=8)),
    (_ADDEND, _row(length=2)),
    (_ADDEND, _row(external=0)),
    (_row(external=1, kind=spec.ARM64_RELOC_ADDEND),),
    (_row(pcrel=1, external=0, kind=spec.ARM64_RELOC_ADDEND),),
    (_SUBTRACTOR, _row(address=8)),
    (_SUBTRACTOR, _row(length=2)),
    (_SUBTRACTOR, _row(pcrel=1)),
    (_SUBTRACTOR, _row(external=0)),
    (_SUBTRACTOR, _row(kind=spec.ARM64_RELOC_BRANCH26)),
    (_row(symbol=2, kind=spec.ARM64_RELOC_SUBTRACTOR), _row()),
    (_SUBTRACTOR, _row(symbol=2)),
    (_row(pcrel=1, kind=spec.ARM64_RELOC_SUBTRACTOR), _row()),
    (_row(symbol=2),),
    (_row(symbol=0, external=0),),
    (_row(symbol=2, external=0),),
    (_row(symbol=1, external=0, length=2),),
    (_row(symbol=1, external=0, pcrel=1),),
    (_row(external=0, kind=spec.ARM64_RELOC_BRANCH26),),
    (_row(external=2),),
    (_row(pcrel=2),),
    # An early name error must still precede a later malformed companion.
    (_row(symbol=2), _ADDEND),
    (_row(symbol=2, kind=spec.ARM64_RELOC_SUBTRACTOR), _row(symbol=3), _ADDEND),
)


@pytest.mark.parametrize("rows", _CASES)
@pytest.mark.parametrize("names", (None, (), _NAMES))
def test_count_matches_canonical_rows_and_first_error(rows, names):
    data = _raw(rows)
    outcomes = []
    for count_only in (False, True):
        try:
            result = (host._count_logical_staged_rows(data, _SECTIONS, _SYMBOLS, names)
                      if count_only else
                      len(list(host._logical_staged_rows(data, _SECTIONS, _SYMBOLS, names))))
            outcomes.append(("count", result))
        except (link.LinkError, native.NativeObjectError) as error:
            outcomes.append((type(error), str(error)))
    assert outcomes[0] == outcomes[1]


def test_count_does_not_construct_relocations(monkeypatch):
    original = link.new_relocation
    constructed = []

    def observed(*args):
        constructed.append(None)
        return original(*args)

    def forbidden(*args):
        raise AssertionError("count allocated a Relocation")

    rows = (_row(), _ADDEND, _row(), _SUBTRACTOR, _row(),
            _row(symbol=1, external=0))
    data = _raw(rows)
    monkeypatch.setattr(link, "new_relocation", observed)
    assert len(list(host._logical_staged_rows(data, _SECTIONS, _SYMBOLS, _NAMES))) == 4
    assert len(constructed) == 4
    monkeypatch.setattr(link, "new_relocation", forbidden)
    assert host._count_logical_staged_rows(
        data, _SECTIONS, _SYMBOLS, _NAMES,
    ) == 4


def test_materialized_fold_keeps_list_bias_and_local_rename():
    symbols = [{"name": "Llocal", "n_type": spec.N_SECT}]
    data = _raw((_row(address=8),))
    result = link._folded_relocation_rows(
        host._raw_row_dicts(data), _SECTIONS, symbols,
        {"Llocal": "Llocal$link0"}, offset_bias=24,
    )
    assert type(result) is list
    assert result[0].offset == 32
    assert result[0].symbol == "Llocal$link0"
    assert link._count_folded_relocation_rows(
        host._raw_row_dicts(data), _SECTIONS, symbols,
        {"Llocal": "Llocal$link0"}, offset_bias=24,
    ) == 1


def test_count_keeps_first_name_error_before_trailing_addend():
    data = _raw((_row(symbol=2), _ADDEND))
    with pytest.raises(link.LinkError) as error:
        host._count_logical_staged_rows(data, _SECTIONS, _SYMBOLS, _NAMES)
    assert str(error.value) == (
        "extern relocation names symbol index 2, but the input has 1 symbols"
    )


@pytest.mark.parametrize("field", ("segname_str", "sectname_str"))
def test_count_preserves_section_name_access_errors(field):
    sections = [dict(_SECTIONS[0])]
    del sections[0][field]
    data = _raw((_row(symbol=1, external=0),))
    for fold in (host._count_logical_staged_rows, host._logical_staged_rows):
        with pytest.raises(KeyError) as error:
            result = fold(data, sections, _SYMBOLS, _NAMES)
            if not isinstance(result, int):
                list(result)
        assert error.value.args == (field,)


@pytest.mark.parametrize("fail", (False, True))
def test_count_closes_raw_generator(monkeypatch, fail):
    original = host._raw_row_dicts
    closed = []

    def observed(data):
        try:
            yield from original(data)
        finally:
            closed.append(True)

    monkeypatch.setattr(host, "_raw_row_dicts", observed)
    data = _raw((_row(symbol=2 if fail else 0),))
    if fail:
        with pytest.raises(link.LinkError):
            host._count_logical_staged_rows(data, _SECTIONS, _SYMBOLS, _NAMES)
    else:
        assert host._count_logical_staged_rows(data, _SECTIONS, _SYMBOLS, _NAMES) == 1
    assert closed == [True]


def test_count_failure_precedes_payload_window_and_retires_stores(tmp_path, monkeypatch):
    from tests.python.test_macho_windowed_parity import (
        _budget, _companion_inputs, _write_pcos,
    )

    paths = _write_pcos(tmp_path, _companion_inputs())
    original_raw = host._raw_row_dicts
    original_rows_close = host._StagedRelocationRows.close
    original_payload_close = host._StagedSectionPayloads.close
    closed, stores, raw_closed, payload_opened = [], [], [], []

    def malformed(data):
        try:
            yield from original_raw(_raw((_ADDEND,)))
        finally:
            raw_closed.append(True)

    def forbidden_window(*args, **kwargs):
        payload_opened.append(True)
        raise AssertionError("payload opened before raw count validation")

    def close_rows(self, **kwargs):
        original_rows_close(self, **kwargs)
        closed.append(("rows", self.stream is None, self.active_bytes))
        stores.append(self)

    def close_payloads(self, **kwargs):
        original_payload_close(self, **kwargs)
        closed.append(("payloads", self.stream is None, self.active_bytes))
        stores.append(self)

    def inject(event):
        if event == "source_view_begin":
            monkeypatch.setattr(host, "_raw_row_dicts", malformed)
            monkeypatch.setattr(host._StagedSectionPayloads, "window", forbidden_window)
            monkeypatch.setattr(host._StagedRelocationRows, "close", close_rows)
            monkeypatch.setattr(host._StagedSectionPayloads, "close", close_payloads)

    budget = _budget()
    budget.retained_bytes = 1232
    with pytest.raises(link.LinkError, match="trailing ADDEND entry with nothing to modify"):
        host.prepare_executable_from_paths(
            paths, compact_budget=budget, _staged_rows=True, _phase_callback=inject,
        )
    assert raw_closed == [True]
    assert not payload_opened
    assert {kind for kind, _, _ in closed} == {"rows", "payloads"}
    assert all(is_closed for _, is_closed, _ in closed)
    # A window abort closes its store before its finally retires active_bytes.
    # Observe the completed unwind, not that intermediate close-time charge.
    assert all(store.stream is None and store.active_bytes == 0 for store in stores)
    assert budget.retained_bytes == 1232
