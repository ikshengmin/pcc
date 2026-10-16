"""Private consuming joins for detached CPython Mach-O section/row parts."""

import io
import sys
import tempfile

from .precise_stackmap import PreciseStackMapError


_BLOCK = 64 * 1024
_FIXED = 8192


def _join_instrumentation_absent():
    # Frame hooks can retain a BytesIO snapshot before getbuffer() and cause
    # an unbudgeted copy-on-write. The caller also checks its verified host
    # model. Keep the ordinary conservative path when instrumentation exists.
    return (sys.gettrace() is None and sys.getprofile() is None
            and all(sys.monitoring.get_tool(tool) is None for tool in range(6)))


def _consume_parts(parts, ledger, *, patch=None):
    """Retire input chunks before constructing one immutable joined backing.

    The private caller has cleared any other lists containing the chunks.
    ``patch`` is an internal section-target callback borrowing the writable
    view only during this call. Neither the view nor BytesIO owner escapes.
    """
    if type(parts) is not list or any(type(part) is not bytes for part in parts):
        raise PreciseStackMapError("consuming Mach-O join requires owned bytes parts")
    buffer = view = payload = result = closing = stream = source = part = None
    fixed_kept = False
    written = read = 0
    try:
        if not _join_instrumentation_absent():
            raise PreciseStackMapError("consuming Mach-O join requires an uninstrumented host")
        total = sum(map(len, parts))
        if total > ledger.budget.limit_bytes:
            raise PreciseStackMapError("Mach-O consuming join storage budget exceeded")
        # All input bytes are still in ledger.retained. Only the fixed file/view
        # workspace is added now; the joined backing is admitted after retirement.
        ledger.keep(_FIXED, label="Mach-O consuming join workspace")
        fixed_kept = True
        with tempfile.TemporaryFile(mode="w+b", buffering=0) as stream:
            for index in range(len(parts)):
                payload = parts[index]
                size = len(payload)
                if size > total - written:
                    raise PreciseStackMapError("Mach-O consuming join extent changed")
                with memoryview(payload) as source:
                    cursor = 0
                    while cursor < size:
                        with source[cursor:min(size, cursor + _BLOCK)] as part:
                            count = stream.write(part)
                            capacity = len(part)
                        if type(count) is not int or not 0 < count <= capacity:
                            raise PreciseStackMapError("short Mach-O consuming join write")
                        cursor += count
                        written += count
                parts[index] = None
                payload = None
                ledger.retained -= ledger.immutable(size)
            parts.clear()
            if written != total or stream.seek(0, 2) != total or stream.seek(0) != 0:
                raise PreciseStackMapError("Mach-O consuming join extent changed")
            ledger.keep(ledger.immutable(total), label="Mach-O consuming join backing")
            if not _join_instrumentation_absent():
                raise PreciseStackMapError("consuming Mach-O join instrumentation changed")
            # No input chunk, source view or immutable output snapshot remains
            # at the allocation/export boundary. The exact temporary bytes
            # expression is transferred to BytesIO, as in the verified reader.
            buffer = io.BytesIO(b"\0" * total)
            view = buffer.getbuffer()
            while read < total:
                with view[read:min(total, read + _BLOCK)] as part:
                    count = stream.readinto(part)
                    capacity = len(part)
                if type(count) is not int or not 0 < count <= capacity:
                    raise PreciseStackMapError("short Mach-O consuming join read")
                read += count
        # Close storage before patching/freezing. A close failure cannot publish
        # a payload. Every byte has been written/read exactly once on success.
        if patch is not None:
            patch(view)
        if not _join_instrumentation_absent():
            raise PreciseStackMapError("consuming Mach-O join instrumentation changed")
        view.release()
        view = None
        buffer.truncate(total)  # reject any escaped child export, never copy
        result = buffer.getvalue()
        closing, buffer = buffer, None
        try:
            closing.close()
        finally:
            closing = None
        return result
    except BaseException as exc:
        # Closed callbacks may retain an exported child view in their traceback.
        # Retire those frames before closing the backing. The local import avoids
        # a module-initialization cycle with this helper's sole production caller.
        from .macho_host_prepare import _release_closed_tracebacks
        _release_closed_tracebacks(exc)
        if isinstance(exc, OSError):
            raise PreciseStackMapError("Mach-O consuming join storage failed: " + str(exc)) from exc
        raise
    finally:
        try:
            if view is not None:
                view.release()
        finally:
            try:
                if buffer is not None:
                    buffer.close()
            finally:
                parts.clear()
                buffer = view = payload = result = closing = patch = stream = source = part = None
                if fixed_kept:
                    ledger.retained -= _FIXED
