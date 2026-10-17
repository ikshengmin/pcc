"""Aggregate host payload retirement, exact bytes, and fail-closed storage."""

import errno
import sys

import pytest

from pcc.backend import macho_host_payload as payloads, macho_host_prepare as host
from pcc.backend import macho_exec, macho_host_output as output
from pcc.backend import precise_stackmap as maps
from tests.python.test_macho_consuming_join import _track_buffers, _section_targets
from tests.python.test_macho_owned_output import _factory_budget, _stack_inputs
from tests.python.test_macho_windowed_parity import _prepared_state, _write_pcos


def _parts(sizes, seed=1):
    return [bytes([seed + index]) * size for index, size in enumerate(sizes)]


def _store(groups, limit=20_000_000):
    budget = _factory_budget(limit)
    budget.retained_bytes = 137
    ledger = host._Ledger(budget)
    ledger.retained = 251 + sum(ledger.immutable(len(part)) for group in groups for part in group)
    store = payloads._StagedSectionPayloads(len(groups), budget, ledger.keep)
    return store, ledger


def _stage(store, ledger, groups):
    for parts in groups:
        retired = store.write_owned_parts(parts)
        ledger.retained -= retired
    store.seal()


def _files(monkeypatch, mode=None, failure=0):
    original = payloads.tempfile.TemporaryFile
    files = []

    class File:
        def __init__(self):
            self.inner = original(mode="w+b", buffering=0)
            self.calls, self.mode = [], mode
            files.append(self)
        def fileno(self):
            return self.inner.fileno()
        def seek(self, *args):
            result = self.inner.seek(*args)
            return result + 1 if self.mode == "seek" else result
        def write(self, value):
            self.calls.append(("write", len(value)))
            if self.mode == "write":
                if failure == "raise":
                    child = value[:]
                    raise OSError(errno.ENOSPC, "injected payload write")
                return len(value) + 1 if failure == "long" else failure
            with value[:97] as part:
                return self.inner.write(part)
        def readinto(self, value):
            self.calls.append(("read", len(value)))
            if self.mode in ("read", "read-close"):
                if failure == "raise" or self.mode == "read-close":
                    child = value[:]
                    raise OSError(errno.EIO, "injected payload read")
                return len(value) + 1 if failure == "long" else failure
            with value[:83] as part:
                return self.inner.readinto(part)
        def close(self):
            self.inner.close()
            if self.mode in ("close", "read-close"):
                raise OSError(errno.EIO, "injected payload close")

    monkeypatch.setattr(payloads.tempfile, "TemporaryFile", lambda **_kwargs: File())
    return files


def _assert_retired(error):
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        trace = current.__traceback__
        while trace is not None:
            if trace.tb_frame.f_code.co_filename == payloads.__file__:
                local = trace.tb_frame.f_locals
                for name in ("buffer", "view", "payload", "result", "closing", "patch", "stream", "source", "part", "parts"):
                    if name in local:
                        assert local[name] is None
            trace = trace.tb_next
        pending.extend(other for other in (current.__cause__, current.__context__) if other is not None)


@pytest.mark.parametrize("sizes", ((), (0,), (1,), (65539,), (65539, 3, 99999)))
def test_all_chunks_retire_before_first_backing_and_exact_sequential_reads(monkeypatch, sizes):
    groups = [_parts(sizes, seed=index + 1) for index in range(3)]
    files = _files(monkeypatch)
    owners = _track_buffers(monkeypatch)
    store, ledger = _store(groups)
    _stage(store, ledger, groups)
    assert not owners and all(group == [] for group in groups)
    assert ledger.retained == 251 + store.metadata_bytes
    for index in range(3):
        expected = b"".join(_parts(sizes, seed=index + 1))
        assert store.descriptor(index)[1:3] == (len(expected), len(sizes))
        actual = store.read_owned(index, ledger)
        assert type(actual) is bytes and actual == expected
    assert store.consumed == 3 and store.read_bytes == store.logical_bytes
    store.close()
    assert all(owner.closed and len(set(owner.addresses)) <= 1 for owner in owners)
    assert all(file.inner.closed for file in files)
    assert max((size for file in files for _, size in file.calls), default=0) <= payloads._BLOCK
    assert ledger.budget.retained_bytes == 137


@pytest.mark.parametrize("kind", ("bytearray", "view", "alias", "repeated"))
def test_invalid_or_aliased_chunks_fail_before_any_write(monkeypatch, kind):
    groups = [_parts((103,))]
    files = _files(monkeypatch)
    store, ledger = _store(groups)
    held = None
    if kind == "bytearray":
        groups[0][0] = bytearray(groups[0][0])
    elif kind == "view":
        held = memoryview(groups[0][0])
        groups[0][0] = held
    elif kind == "alias":
        held = groups[0][0]
    else:
        groups[0].append(groups[0][0])
    with pytest.raises(maps.PreciseStackMapError, match="exact bytes|exclusively owned") as caught:
        store.write_owned_parts(groups[0])
    assert store.stream is None and groups == [[]]
    assert files[0].calls == [] and files[0].inner.closed
    _assert_retired(caught.value)
    if kind == "view":
        held.release()


@pytest.mark.parametrize("mode", ("write", "read"))
@pytest.mark.parametrize("failure", (0, -1, True, None, "long", "raise"))
def test_invalid_partial_io_releases_chunks_views_and_file(monkeypatch, mode, failure):
    groups = [_parts((1001, 1003))]
    files = _files(monkeypatch, mode=mode, failure=failure)
    owners = _track_buffers(monkeypatch)
    store, ledger = _store(groups)
    with pytest.raises(maps.PreciseStackMapError) as caught:
        _stage(store, ledger, groups)
        store.read_owned(0, ledger)
    assert store.stream is None and groups == [[]]
    assert all(file.inner.closed for file in files) and all(owner.closed for owner in owners)
    _assert_retired(caught.value)


@pytest.mark.parametrize("damage", ("short", "long", "content", "offset", "order", "repeat"))
def test_corruption_extent_and_replay_refuse_without_publishing(monkeypatch, damage):
    groups = [_parts((1001,)), _parts((1003,), seed=4)]
    store, ledger = _store(groups)
    _stage(store, ledger, groups)
    if damage == "short":
        store.stream.truncate(store.logical_bytes - 1)
    elif damage == "long":
        store.stream.seek(0, 2)
        store.stream.write(b"x")
    elif damage == "content":
        store.stream.seek(0)
        store.stream.write(b"x")
    elif damage == "offset":
        offset, size, count, digest = store.rows[0]
        store.rows[0] = (offset + 1, size, count, digest)
    elif damage == "repeat":
        store.read_owned(0, ledger)
    with pytest.raises(maps.PreciseStackMapError, match="extent|content|offset|order") as caught:
        store.read_owned(1 if damage == "order" else 0, ledger)
    assert store.stream is None
    _assert_retired(caught.value)


@pytest.mark.parametrize("delta", (-1, 0, 1))
def test_exact_backing_admission_happens_after_aggregate_retirement(monkeypatch, delta):
    groups = [_parts((1001,)), _parts((1003,), seed=4)]
    store, ledger = _store(groups)
    _stage(store, ledger, groups)
    ledger.budget.limit_bytes = ledger.retained + ledger.immutable(1001) + 137 + delta
    owners = _track_buffers(monkeypatch)
    try:
        if delta < 0:
            with pytest.raises(maps.PreciseStackMapError, match="Mach-O staged payload backing"):
                store.read_owned(0, ledger)
            assert not owners and store.stream is None
        else:
            assert len(store.read_owned(0, ledger)) == 1001
    finally:
        store.close()


@pytest.mark.parametrize("delta", (-1, 0, 1))
def test_descriptor_admission_precedes_file_open(monkeypatch, delta):
    store, ledger = _store([[]])
    amount = ledger.retained + ledger.budget.retained_bytes
    store.close()
    files = _files(monkeypatch)
    if delta < 0:
        with pytest.raises(maps.PreciseStackMapError, match="Mach-O staged payload descriptors"):
            _store([[]], limit=amount + delta)
        assert files == []
    else:
        store, ledger = _store([[]], limit=amount + delta)
        store.close()
        assert len(files) == 1 and files[0].inner.closed


def test_storage_cap_and_open_failure_fail_without_allocating_backing(monkeypatch):
    groups = [_parts((1001,))]
    owners = _track_buffers(monkeypatch)
    store, ledger = _store(groups)
    ledger.budget.limit_bytes = 1000
    with pytest.raises(maps.PreciseStackMapError, match="storage budget"):
        store.write_owned_parts(groups[0])
    assert not owners and store.stream is None and groups == [[]]
    def fail(**_kwargs):
        raise OSError(errno.ENOSPC, "injected payload open")
    monkeypatch.setattr(payloads.tempfile, "TemporaryFile", fail)
    with pytest.raises(maps.PreciseStackMapError, match="injected payload open") as caught:
        _store([[]])
    _assert_retired(caught.value)


def test_instrumentation_during_last_seek_refuses_before_backing(monkeypatch):
    groups = [_parts((1001,))]
    files = _files(monkeypatch)
    store, ledger = _store(groups)
    _stage(store, ledger, groups)
    owners = _track_buffers(monkeypatch)
    seek = files[0].seek
    def instrument(*args):
        result = seek(*args)
        if args == (0,):
            monkeypatch.setattr(payloads, "_join_instrumentation_absent", lambda: False)
        return result
    files[0].seek = instrument
    with pytest.raises(maps.PreciseStackMapError, match="instrumentation changed"):
        store.read_owned(0, ledger)
    assert not owners and store.stream is None and files[0].inner.closed


@pytest.mark.parametrize("point", ("admit", "patch", "seal"))
def test_instrumentation_changes_fail_before_publishing(monkeypatch, point):
    groups = [_parts((1001,))]
    store, ledger = _store(groups)
    retired = store.write_owned_parts(groups[0])
    ledger.retained -= retired
    owners = _track_buffers(monkeypatch)
    def enable(*_args):
        monkeypatch.setattr(payloads, "_join_instrumentation_absent", lambda: False)
    if point == "seal":
        enable()
    else:
        store.seal()
        if point == "admit":
            check = ledger.check
            def checked(extra=0, *, label="Mach-O input window"):
                check(extra, label=label)
                enable()
            monkeypatch.setattr(ledger, "check", checked)
    with pytest.raises(maps.PreciseStackMapError, match="instrument") as caught:
        if point == "seal":
            store.seal()
        else:
            store.read_owned(0, ledger, patch=enable if point == "patch" else None)
    assert store.stream is None and all(owner.closed for owner in owners)
    _assert_retired(caught.value)


def test_read_error_survives_close_failure_and_patch_failure_releases_child_view(monkeypatch):
    groups = [_parts((1001,))]
    files = _files(monkeypatch, mode="read-close")
    owners = _track_buffers(monkeypatch)
    store, ledger = _store(groups)
    _stage(store, ledger, groups)
    with pytest.raises(maps.PreciseStackMapError, match="injected payload read") as caught:
        store.read_owned(0, ledger)
    assert all(file.inner.closed for file in files) and all(owner.closed for owner in owners)
    _assert_retired(caught.value)
    files[0].mode = None
    groups = [_parts((1001,))]
    store, ledger = _store(groups)
    files[-1].mode = None
    _stage(store, ledger, groups)
    def fail(view):
        child = view[:]
        raise ValueError("injected patch")
    with pytest.raises(ValueError, match="injected patch") as caught:
        store.read_owned(0, ledger, patch=fail)
    assert all(file.inner.closed for file in files) and all(owner.closed for owner in owners)
    _assert_retired(caught.value)


def test_production_retires_every_section_before_first_allocation_and_keeps_image(tmp_path, monkeypatch):
    paths = _stack_inputs(tmp_path, count=3, records=32, data_bytes=65537)
    expected = macho_exec.link_prepared_executable(
        host.prepare_executable_from_paths(paths, compact_budget=_factory_budget()),
        compact_budget=_factory_budget(),
    )
    original = host._StagedSectionPayloads.read_owned
    reads, phases = [], []
    def read(self, index, ledger, **kwargs):
        frame = sys._getframe(1)
        local = frame.f_locals
        assert all(target.data_parts == [] for target in local["merged"].values())
        assert self.sealed and self.appended == self.count
        assert len(local["out_sections"]) == index
        del frame, local
        reads.append(index)
        return original(self, index, ledger, **kwargs)
    monkeypatch.setattr(host._StagedSectionPayloads, "read_owned", read)
    budget = _factory_budget()
    pending = [host.prepare_executable_from_paths(
        paths, compact_budget=budget, _staged_rows=True,
        _phase_callback=lambda event, **fields: phases.append((event, fields)),
    )]
    assert reads == list(range(len(reads))) and reads
    ends = [(index, fields) for index, (event, fields) in enumerate(phases)
            if event == "section_payload_spool_end"]
    joins = [(index, fields) for index, (event, fields) in enumerate(phases)
             if event == "section_payload_begin"]
    assert len(ends) == len(joins) == len(reads) and ends[-1][0] < joins[0][0]
    assert [fields for _, fields in ends] == [fields for _, fields in joins]
    actual = output.link_owned_executable(pending.pop(), compact_budget=budget)
    assert actual == expected


def test_section_targets_after_stackmap_resize_keep_prepared_bytes(tmp_path):
    paths = _write_pcos(tmp_path, _section_targets())
    expected = _prepared_state(host.prepare_executable_from_paths(paths, compact_budget=_factory_budget()))
    source = host.prepare_executable_from_paths(paths, compact_budget=_factory_budget(), _staged_rows=True)
    try:
        assert _prepared_state(source) == expected
    finally:
        host.close_staged_source_rows(source)


def test_payload_store_close_failure_cannot_publish_prepared_source(tmp_path, monkeypatch):
    paths = _stack_inputs(tmp_path, count=2, records=8, data_bytes=64)
    original = host._StagedSectionPayloads.close
    stores, phases = [], []
    def close(self, *, _error=None):
        stores.append(self)
        original(self, _error=_error)
        if _error is None:
            raise OSError("injected final payload close")
    monkeypatch.setattr(host._StagedSectionPayloads, "close", close)
    with pytest.raises(OSError, match="injected final payload close") as caught:
        host.prepare_executable_from_paths(
            paths, compact_budget=_factory_budget(), _staged_rows=True,
            _phase_callback=lambda event, **fields: phases.append(event),
        )
    assert stores and all(store.stream is None for store in stores)
    assert "section_joins_end" not in phases and "source_view_begin" not in phases
    _assert_retired(caught.value)


@pytest.mark.parametrize("point", ("section_payload_spool_begin", "section_payload_spool_end", "section_payload_begin"))
@pytest.mark.parametrize("instrument", ("trace", "profile"))
def test_production_phase_instrumentation_fails_closed(tmp_path, monkeypatch, point, instrument):
    paths = _stack_inputs(tmp_path, count=2, records=8, data_bytes=64)
    stores, phases = [], []
    create = host._StagedSectionPayloads
    def capture(*args, **kwargs):
        store = create(*args, **kwargs)
        stores.append(store)
        return store
    monkeypatch.setattr(host, "_StagedSectionPayloads", capture)
    setter = getattr(sys, "set" + instrument)
    previous = getattr(sys, "get" + instrument)()
    def hook(*_args):
        return hook
    def phase(event, **fields):
        phases.append(event)
        if event == point:
            setter(hook)
    try:
        with pytest.raises(macho_exec.LinkError, match="instrument"):
            host.prepare_executable_from_paths(
                paths, compact_budget=_factory_budget(), _staged_rows=True, _phase_callback=phase,
            )
    finally:
        setter(previous)
    assert stores and all(store.stream is None for store in stores)
    assert "section_joins_end" not in phases


def test_escaped_patch_export_refuses_without_copy(monkeypatch):
    groups = [_parts((1001,))]
    store, ledger = _store(groups)
    _stage(store, ledger, groups)
    owners = _track_buffers(monkeypatch)
    escaped = []
    try:
        with pytest.raises(BufferError) as caught:
            store.read_owned(0, ledger, patch=lambda view: escaped.append(view[:]))
        assert store.stream is None
        assert len(owners) == 1 and len(owners[0].addresses) == 2
        _assert_retired(caught.value)
    finally:
        for view in escaped:
            view.release()
        for owner in owners:
            owner.close()
