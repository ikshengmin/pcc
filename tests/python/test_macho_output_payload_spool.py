"""Bounded host output payloads; full Mach-O bytes are the public oracle."""

import struct
import weakref
from pathlib import Path

import pytest

from pcc.backend import (
    macho_exec,
    macho_host_output as output,
    macho_host_prepare as host,
    macho_obj as obj,
    macho_spec as spec,
    native_object as native,
    precise_stackmap as wire,
)
from tests.python.test_macho_fixup_retirement import _fixup_input, _link
from tests.python.test_macho_owned_output import (
    _factory_budget,
    _stack_inputs,
    _tracked_buffers,
)
from tests.python.test_macho_payload_windows import _source, _stage, _signature_matches


@pytest.mark.parametrize("sizes, expected", (
    ((36, 36, 64, 33, 216), (48, 768, 224)),
    ((36, 36, 80, 33, 216), (48, 784, 224)),
    ((36, 49, 65, 49, 225), (64, 848, 240)),
))
def test_factory_budget_tracks_host_object_sizes_without_relaxing_admission(monkeypatch, sizes, expected):
    from tests.python import test_stackmap_host_budget as fixtures

    configure = fixtures.configured_host
    positive, negative, triple, empty_bytes, empty_set = sizes

    def configured(patch, **kwargs):
        environment, runtime = configure(patch, **kwargs)

        def getsizeof(value):
            if type(value) is int:
                assert value in ((1 << 64) - 1, -(1 << 63))
                return positive if value > 0 else negative
            if type(value) is tuple:
                assert value == (0, 0, 0)
                return triple
            if type(value) is bytes:
                assert value == b""
                return empty_bytes
            assert type(value) is set and not value
            return empty_set

        runtime.getsizeof = getsizeof
        return environment, runtime

    monkeypatch.setattr(fixtures, "configured_host", configured)
    metadata = expected[1]
    budget = _factory_budget(2 * metadata + 17)
    assert (budget.integer_bytes, budget.metadata_entry_bytes, budget.set_header_bytes) == expected
    assert budget.allocation_quantum == budget.set_entry_bytes == 16
    budget.retained_bytes = 17
    budget.check_peak(0, entries=2)
    with pytest.raises(wire.PreciseStackMapError, match="byte budget exceeded"):
        budget.check_peak(1, entries=2)


def _staged_link(paths, budget=None, **kwargs):
    budget = _factory_budget() if budget is None else budget
    return output.link_owned_executable(
        host.prepare_executable_from_paths(paths, compact_budget=budget, _staged_rows=True),
        compact_budget=budget, **kwargs,
    )


def test_ci_selects_staged_output_regressions_on_mac_with_default_workers():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/pcc1-package-parity.yml").read_text()
    tests = (
        "tests/python/test_macho_output_payload_spool.py",
        "tests/python/test_macho_output_spool_failures.py",
        "tests/python/test_macho_payload_windows.py",
    )
    for path in tests:
        assert workflow.count('      - "' + path + '"') == 2
    selection = "${{ matrix.platform == 'macos-arm64' && '" + " ".join(tests) + "' || '' }}"
    command = next(line for line in workflow.splitlines()
                   if "--junitxml=build/worker-prior-focused-host.xml" in line)
    assert selection in command and "python -m pytest -x" in command
    assert "-n0" not in command and "--dist" not in command and "--noconftest" not in command
    assert '-n 6 --dist=loadgroup' in (root / "pyproject.toml").read_text()


def test_staged_output_fits_one_window_below_aggregate_preparation_peak(monkeypatch):
    # Symbol metadata remains resident during section transfer. The final
    # image fits, but retaining every payload beside that graph does not.
    def text():
        return obj.Section(
            "__text", "__TEXT", data=b"\xc0\x03\x5f\xd6", align_log2=2,
            flags=obj.TEXT_SECTION_FLAGS,
            symbols=(obj.TextSymbol("_main", 0),) + tuple(
                obj.TextSymbol("_symbol" + str(i), 0) for i in range(1000)
            ),
        )

    def data(index):
        return obj.Section(
            "__data" + str(index), "__DATA", data=b"\0" * 8 + bytes([index + 1]) * (131072 - 8),
            align_log2=3,
            relocations=(obj.Relocation(0, "_main", spec.ARM64_RELOC_UNSIGNED, False, 3),),
        )

    builders = (text,) + tuple(lambda i=i: data(i) for i in range(8))
    expected = macho_exec.link_prepared_executable(
        native.OwnedMergedSourceView(tuple(build() for build in builders)),
        compact_budget=_factory_budget(),
    )
    budget = _factory_budget()
    budget.retained_bytes = 137
    def row():
        return native._FINAL_LINK_RELOCATION.pack(0, 0, 0, 3, 1, spec.ARM64_RELOC_UNSIGNED)

    pending = [_source(_stage(builders, budget, (lambda: b"",) + (row,) * 8), budget)]
    source_ref = weakref.ref(pending[0])
    payload_store = pending[0]._host_payload_store
    aggregate = sum(payload_store.backing_bytes(i) for i in range(9))
    initial = output._OwnedOutput.__init__
    admission = budget.check_peak
    observed = {}
    transfers = []

    def limited(state, *args, **kwargs):
        initial(state, *args, **kwargs)
        known = state.known_bytes()
        budget.limit_bytes = known + 2 * 131072 + budget.retained_bytes
        observed.update(known=known, limit=budget.limit_bytes)
        assert len(expected) + output._FIXED + budget.retained_bytes < budget.limit_bytes
        assert known + aggregate + budget.retained_bytes > budget.limit_bytes

    def checked(raw, *, records=0, entries=0, label="stack-map"):
        if label == "Mach-O owned source payload transfer":
            transfers.append(raw + budget.retained_bytes)
        return admission(raw, records=records, entries=entries, label=label)

    monkeypatch.setattr(output._OwnedOutput, "__init__", limited)
    monkeypatch.setattr(budget, "check_peak", checked)
    actual = output.link_owned_executable(pending.pop(), compact_budget=budget)
    assert actual == expected
    assert source_ref() is None and payload_store.stream is None
    assert len(transfers) == 9 and max(transfers) <= observed["limit"]
    assert budget.retained_bytes == 137
    _signature_matches(actual, budget)


@pytest.mark.parametrize("tlv", (False, True))
@pytest.mark.parametrize("count", (8, 4096))
def test_staged_global_fixup_chains_match_public_across_sections_and_pages(tmp_path, monkeypatch, tlv, count):
    paths = _fixup_input(tmp_path, count=count, tlv=tlv)
    expected = _link(paths, "public")
    patched = []
    patch_word = output._OwnedOutput.patch_word

    def tracked(state, out, offset, word):
        if type(out.data) is output._FilePayload:
            patched.append((out.sectname, out.addr + offset, word))
        return patch_word(state, out, offset, word)

    monkeypatch.setattr(output._OwnedOutput, "patch_word", tracked)
    actual = _staged_link(paths)
    assert actual == expected
    assert {name for name, _, _ in patched} >= {"__data", "__const"}
    if count == 8:
        # The last __const pointer chains into __data within the same page.
        last_const = max((site for site in patched if site[0] == "__const"), key=lambda site: site[1])
        first_data = min((site for site in patched if site[0] == "__data"), key=lambda site: site[1])
        assert last_const[1] // macho_exec.PAGE == first_data[1] // macho_exec.PAGE
        assert (last_const[2] >> 51) & 0xFFF == (first_data[1] - last_const[1]) // 4
    else:
        assert len({address // macho_exec.PAGE for _, address, _ in patched}) > 2
    assert bool(spec.parse_object(actual).header["flags"] & 0x00800000) is tlv
    _signature_matches(actual, _factory_budget())


@pytest.mark.parametrize("fault, message", (
    ("addend", "does not fit a chained bind"),
    ("high-rebase", "exceeds 36 bits"),
    ("negative-rebase", "format requires"),
    ("unaligned", "four-byte-aligned pointer"),
    ("page-crossing", "crosses a 0x4000-byte page boundary"),
))
def test_staged_fixup_error_parity(tmp_path, fault, message):
    paths = _fixup_input(tmp_path, fault=fault)
    errors = []
    kind = struct.error if fault == "negative-rebase" else macho_exec.LinkError
    for link in (lambda: _link(paths, "public"), lambda: _staged_link(paths)):
        with pytest.raises(kind, match=message) as caught:
            link()
        errors.append((type(caught.value), str(caught.value)))
    assert errors[0] == errors[1]


def test_staged_payloads_retire_each_section_and_before_single_final_backing(tmp_path, monkeypatch):
    paths = _stack_inputs(tmp_path, count=3, records=512, data_bytes=65536)
    expected = _link(paths, "public")
    budget = _factory_budget()
    pending = [host.prepare_executable_from_paths(paths, compact_budget=budget, _staged_rows=True)]
    source_ref = weakref.ref(pending[0])
    source_files = (pending[0]._host_row_store.stream, pending[0]._host_payload_store.stream)
    owners = _tracked_buffers(monkeypatch)
    begin, end = output._OwnedOutput.begin_section, output._OwnedOutput.end_section
    open_sizes, retired, stages = [], [], []

    def starts(state, source, section, out):
        assert not state.views and not state.buffers and state.active_payload is None
        assert all(owner.closed for owner in owners)
        begin(state, source, section, out)
        assert len(state.views) == len(state.buffers) == 1
        assert sum(not owner.closed for owner in owners) == 1
        open_sizes.append(len(out.data))

    def ends(state, out):
        end(state, out)
        assert type(out.data) is output._FilePayload
        assert not state.views and not state.buffers and state.active_payload is None
        assert all(owner.closed for owner in owners)
        retired.append(out.sectname)
        if not stages:
            stages.append(state.payload_file)

    def signing(_event):
        assert source_ref() is None
        assert all(stream.closed for stream in source_files + tuple(stages))
        assert sum(not owner.closed for owner in owners) == 1

    monkeypatch.setattr(output._OwnedOutput, "begin_section", starts)
    monkeypatch.setattr(output._OwnedOutput, "end_section", ends)
    actual = output.link_owned_executable(pending.pop(), compact_budget=budget, phase_callback=signing)
    assert actual == expected
    assert retired == ["__text", "__data", "__pcc_stackmaps"]
    assert len(open_sizes) == 3 and all(owner.closed for owner in owners)
    assert all(len(set(owner.addresses)) <= 1 for owner in owners)
    assert len(actual) == owners[-1].size


@pytest.mark.parametrize("under", (0, 1))
def test_staged_validation_replay_exact_admission_and_one_byte_refusal(tmp_path, monkeypatch, under):
    paths = _stack_inputs(tmp_path, records=64, data_bytes=128)
    budget = _factory_budget()
    budget.retained_bytes = 137
    pending = [host.prepare_executable_from_paths(paths, compact_budget=budget, _staged_rows=True)]
    files = (pending[0]._host_row_store.stream, pending[0]._host_payload_store.stream)
    owners = _tracked_buffers(monkeypatch)
    check = budget.check_peak
    replay = []

    def boundary(raw, *, records=0, entries=0, label="stack-map"):
        if label == "Mach-O owned payload validation replay":
            previous = budget.limit_bytes
            budget.limit_bytes = raw + budget.retained_bytes - under
            replay.append((raw, budget.limit_bytes, len(owners)))
            try:
                return check(raw, records=records, entries=entries, label=label)
            finally:
                budget.limit_bytes = previous
        return check(raw, records=records, entries=entries, label=label)

    monkeypatch.setattr(budget, "check_peak", boundary)
    if under:
        with pytest.raises(output.LinkError, match="Mach-O owned payload validation replay"):
            output.link_owned_executable(pending.pop(), compact_budget=budget)
        assert replay[0][0] + 137 == replay[0][1] + 1
        assert len(owners) == replay[0][2]
    else:
        actual = output.link_owned_executable(pending.pop(), compact_budget=budget)
        assert replay[0][0] + 137 == replay[0][1]
        _signature_matches(actual, budget)
    assert len(replay) == 1 and all(owner.closed for owner in owners)
    assert all(stream.closed for stream in files)
    assert budget.retained_bytes == 137
