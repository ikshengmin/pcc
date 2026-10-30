"""Exact immutable spool writes retain legacy I/O, count and view contracts."""

import errno

import pytest

from pcc.backend import macho_host_stackmap as spool
from pcc.backend import macho_host_stackmap_index as packed
from pcc.backend import precise_stackmap as wire
from tests.python.test_macho_stackmap_consumption import _payload
from tests.python.test_macho_stackmap_record_codec import _trace as _merge_trace
from tests.python.test_macho_windowed_prepare import _budget


def _legacy_write_all(self, stream, value):
    # Exact preceding implementation, including its permissive count protocol.
    size = len(value)
    if size > self.write_limit - self.written:
        raise wire.PreciseStackMapError("stack-map spool write work budget exceeded")
    self.written += size
    with memoryview(value) as view:
        cursor = 0
        while cursor < size:
            with view[cursor:] as remaining:
                count = stream.write(remaining)
            if count is None or count <= 0 or count > size - cursor:
                raise wire.PreciseStackMapError("short stack-map spool write")
            cursor += count


def _released(view):
    try:
        view.obj
    except ValueError:
        return True
    return False


def _write_trace(method, kind, mode, *, size=8, fail_at=None, limit=100):
    class Bytes(bytes):
        pass

    owner = bytearray(b"abcdefgh"[:size]) if kind in ("bytearray", "memoryview") else None
    value = {
        "bytes": lambda: b"abcdefgh"[:size],
        "subclass": lambda: Bytes(b"abcdefgh"[:size]),
        "bytearray": lambda: owner,
        "memoryview": lambda: memoryview(owner),
    }[kind]()
    work = spool._SpoolWork(_budget(limit))
    work.written = 17
    events, views = [], []
    output = bytearray()

    def observe(label, *fields):
        events.append((label, fields, tuple(_released(view) for view in views), work.written))
        if owner is not None:
            try:
                owner.append(0)
            except BufferError:
                events.append(("resize", "BufferError"))
            else:
                owner.pop()
                events.append(("resize", "allowed"))

    class Cursor:
        def __init__(self, value):
            self.value = value

        def __lt__(self, other):
            observe("cursor.lt", other)
            return self.value < other

        def __index__(self):
            observe("cursor.index")
            if mode == "index-error":
                raise RuntimeError("injected cursor index error")
            return self.value

        def __int__(self):
            raise AssertionError("legacy cursor must not be coerced to int")

        def __add__(self, other):
            observe("cursor.add", other)
            return self.value + other

    class Count:
        def __le__(self, other):
            observe("count.le", other)
            if mode == "compare-error":
                raise RuntimeError("injected count comparison error")
            return 2 <= other

        def __gt__(self, other):
            observe("count.gt", other)
            return 2 > other

        def __radd__(self, other):
            observe("count.radd", other)
            if mode == "add-error":
                raise RuntimeError("injected count addition error")
            return Cursor(other + 2)

        def __eq__(self, other):
            raise AssertionError("custom counts must not enter the full-int shortcut")

        def __int__(self):
            raise AssertionError("legacy count must not be coerced to int")

        def __index__(self):
            raise AssertionError("legacy count is not the slice cursor")

    class IntCount(int):
        def __le__(self, other):
            observe("int.le", other)
            return super().__le__(other)

        def __gt__(self, other):
            observe("int.gt", other)
            return super().__gt__(other)

        def __radd__(self, other):
            observe("int.radd", other)
            return other + 2

        def __int__(self):
            raise AssertionError("int subclass must not be coerced")

        def __index__(self):
            raise AssertionError("int subclass must not be used as cursor")

    class IntOnly:
        def __int__(self):
            raise AssertionError("write count int conversion is not legacy behavior")

    class IndexOnly:
        def __index__(self):
            raise AssertionError("write count index conversion is not legacy behavior")

    class Writer:
        def write(self, argument):
            assert type(argument) is memoryview
            assert argument.obj is (owner if owner is not None else value)
            observe("write", bytes(argument), argument.readonly)
            assert all(_released(view) for view in views)
            views.append(argument)
            if fail_at == len(views):
                raise OSError(errno.ENOSPC, "injected write failure")
            if len(views) > 1 and mode != "partial":
                count, consumed = len(argument), len(argument)
            elif mode == "full":
                count, consumed = len(argument), len(argument)
            elif mode == "partial":
                count = consumed = max(1, len(argument) // 2)
            elif mode in ("zero", "negative", "oversized", "none", "true", "false"):
                count = {"zero": 0, "negative": -1, "oversized": len(argument) + 1,
                         "none": None, "true": True, "false": False}[mode]
                consumed = 1 if mode == "true" else 0
            elif mode == "full-float":
                count, consumed = float(len(argument)), len(argument)
            elif mode == "partial-float":
                count, consumed = 1.5, 1
            elif mode == "int-subclass":
                count, consumed = IntCount(2), 2
            elif mode == "int-only":
                count, consumed = IntOnly(), 0
            elif mode == "index-only":
                count, consumed = IndexOnly(), 0
            else:
                count, consumed = Count(), 2
            output.extend(argument[:consumed])
            return count

    error = None
    try:
        method(work, Writer(), value)
    except Exception as exc:
        error = (type(exc).__name__, str(exc), type(exc.__cause__).__name__)
    assert all(_released(view) for view in views)
    if type(value) is memoryview:
        value.release()
    if owner is not None:
        owner.append(0)  # no export survives success or any failure
        owner.pop()
    return events, bytes(output), error, work.written


@pytest.mark.parametrize("kind", ("bytes", "subclass", "bytearray", "memoryview"))
@pytest.mark.parametrize("mode", (
    "full", "partial", "zero", "negative", "oversized", "none", "true", "false",
    "full-float", "partial-float", "int-subclass", "custom", "compare-error",
    "add-error", "index-error", "int-only", "index-only",
))
def test_write_count_protocol_views_and_mutable_exports_match_legacy(kind, mode):
    expected = _write_trace(_legacy_write_all, kind, mode)
    actual = _write_trace(spool._SpoolWork.write_all, kind, mode)
    assert actual == expected
    assert actual[3] == 25
    assert all(event[3] == 25 for event in actual[0] if event[0] != "resize")


@pytest.mark.parametrize("kind", ("bytes", "subclass", "bytearray", "memoryview"))
@pytest.mark.parametrize("fail_at", (1, 2, 3))
def test_oserror_at_any_partial_write_keeps_order_and_releases_views(kind, fail_at):
    expected = _write_trace(_legacy_write_all, kind, "partial", fail_at=fail_at)
    actual = _write_trace(spool._SpoolWork.write_all, kind, "partial", fail_at=fail_at)
    assert actual == expected and actual[2][0] == "OSError" and actual[3] == 25


@pytest.mark.parametrize("returned", (None, 0, -1, 7, False))
def test_invalid_return_after_partial_progress_uses_remaining_bound(returned):
    def run(method):
        views, calls = [], []
        work = spool._SpoolWork(_budget())

        class Writer:
            def write(self, value):
                assert all(_released(view) for view in views)
                calls.append((bytes(value), work.written))
                views.append(value)
                return 2 if len(calls) == 1 else returned

        with pytest.raises(wire.PreciseStackMapError, match="short stack-map spool write") as caught:
            method(work, Writer(), b"abcdefgh")
        assert all(_released(view) for view in views)
        return calls, str(caught.value), work.written

    actual = run(spool._SpoolWork.write_all)
    assert actual == run(_legacy_write_all)
    assert actual[0] == [(b"abcdefgh", 8), (b"cdefgh", 8)]


@pytest.mark.parametrize("mode", ("full", "partial", "true", "false"))
@pytest.mark.parametrize("size", (0, 1, 8))
@pytest.mark.parametrize("delta", (-1, 0, 1))
def test_exact_budget_boundary_precedes_io_and_charges_whole_value_once(mode, size, delta):
    options = dict(size=size, limit=17 + size + delta)
    expected = _write_trace(_legacy_write_all, "bytes", mode, **options)
    actual = _write_trace(spool._SpoolWork.write_all, "bytes", mode, **options)
    assert actual == expected
    if delta < 0:
        assert actual[0] == [] and actual[3] == 17
        assert actual[2][0] == "PreciseStackMapError"
    elif size == 0:
        assert actual == ([], b"", None, 17)


def test_complete_immutable_write_borrows_the_only_view_not_a_full_child(monkeypatch):
    created = []

    def tracked(value):
        view = memoryview(value)
        created.append(view)
        return view

    class Writer:
        def write(self, argument):
            assert argument is created[-1]
            return len(argument)

    monkeypatch.setattr(spool, "memoryview", tracked, raising=False)
    work = spool._SpoolWork(_budget())
    work.write_all(Writer(), b"12345678")
    assert len(created) == 1 and _released(created[0]) and work.written == 8


@pytest.mark.parametrize("runtime", (False, True))
@pytest.mark.parametrize("single", (False, True))
@pytest.mark.parametrize("arch", (wire.ARCH_AARCH64, wire.ARCH_X86_64))
def test_full_merge_preserves_bytes_offsets_and_all_spool_index_admissions(monkeypatch, runtime, single, arch):
    payloads = (_payload((9,), arch=arch, records=4097), _payload((2,), arch=arch, records=4))
    owners = {spool._SpoolWork: [], packed._IndexWork: []}
    for cls, seen in owners.items():
        original = cls.__init__

        def observe(self, *args, _original=original, _seen=seen):
            _original(self, *args)
            _seen.append(self)

        monkeypatch.setattr(cls, "__init__", observe)
    actual = _merge_trace(payloads, runtime, single)
    monkeypatch.setattr(spool._SpoolWork, "write_all", _legacy_write_all)
    expected = _merge_trace(payloads, runtime, single)
    assert actual == expected
    assert actual[2:] == (4101, 137, [None, None])
    assert len(owners[spool._SpoolWork]) == 2
    assert vars(owners[spool._SpoolWork][0]) == vars(owners[spool._SpoolWork][1])
    left, right = owners[packed._IndexWork]
    assert all(getattr(left, key) == getattr(right, key) for key in packed._IndexWork.__slots__)


@pytest.mark.parametrize("failure", (False, True))
def test_runtime_short_io_closes_storage_and_early_write_error_beats_later_conversion(monkeypatch, failure):
    temporary = spool.tempfile.TemporaryFile
    opened, converted = [], []

    class Wrapped:
        def __init__(self, *args, **kwargs):
            self.inner = temporary(*args, **kwargs)
            self.ordinal = len(opened)
            opened.append(self.inner)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.inner.close()

        def seek(self, *args):
            return self.inner.seek(*args)

        def flush(self):
            return self.inner.flush()

        def write(self, value):
            if failure and self.ordinal == 3:  # runtime record output
                raise OSError(errno.ENOSPC, "first runtime record write")
            with value[:3] as part:
                return self.inner.write(part)

        def readinto(self, value):
            with value[:3] as part:
                return self.inner.readinto(part)

    def reject_later_root(*args):
        converted.append(True)
        raise RuntimeError("later root conversion must not win")

    payload = _payload((1,), records=4)
    expected = _merge_trace([payload], True, True)[0]
    monkeypatch.setattr(spool.tempfile, "TemporaryFile", Wrapped)
    if failure:
        monkeypatch.setattr(wire, "_runtime_location_word", reject_later_root)
        with pytest.raises(wire.PreciseStackMapError, match="first runtime record write") as caught:
            _merge_trace([payload], True, True)
        assert isinstance(caught.value.__cause__, OSError) and not converted
    else:
        assert _merge_trace([payload], True, True)[0] == expected
    assert len(opened) == 7 and all(stream.closed for stream in opened)
