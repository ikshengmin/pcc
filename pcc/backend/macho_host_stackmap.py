"""Bounded CPython stack-map merge with file-owned construction bytes.

The windowed Mach-O caller transfers an exclusive list of detached bytes.
Functions and exact whole root vectors are spooled, not retained as heap
chunks. Only integer descriptors and digest buckets survive between inputs.
Digest matches are always compared byte-for-byte before sharing storage.
The native and ordinary shared mergers do not use this implementation.
"""

import hashlib
import io
import sys
import tempfile

from . import precise_stackmap as wire


_BLOCK_BYTES = 64 * 1024
_FIXED_BYTES = 2 * _BLOCK_BYTES + 16384


class _SpoolWork:
    """Bound disk traffic independently of byte-owner admission."""

    def __init__(self, budget):
        # Every output byte is written once and read once. Collision checks
        # have a separate MAX_LOCATIONS allowance, charged before each read.
        self.write_limit = budget.limit_bytes
        self.read_limit = budget.limit_bytes + wire.MAX_LOCATIONS * wire.LOCATION_SIZE
        self.written = 0
        self.read = 0
        self.compared_locations = 0
        self.comparison_limit = 0

    def charge_comparison(self, count):
        # Bound collision work by both the unchanged absolute location cap
        # and a constant multiple of distinct source-range work admitted so
        # far. Even adversarial digests cannot turn U ranges into U**2 work.
        limit = min(wire.MAX_LOCATIONS, self.comparison_limit)
        if count > limit - self.compared_locations:
            raise wire.PreciseStackMapError("stack-map spool comparison work budget exceeded")
        self.compared_locations += count

    def write_all(self, stream, value):
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

    def read_exact(self, stream, view):
        size = len(view)
        if size > self.read_limit - self.read:
            raise wire.PreciseStackMapError("stack-map spool read work budget exceeded")
        self.read += size
        cursor = 0
        while cursor < size:
            with view[cursor:min(size, cursor + _BLOCK_BYTES)] as remaining:
                count = stream.readinto(remaining)
                capacity = len(remaining)
            if count is None or count <= 0 or count > capacity:
                raise wire.PreciseStackMapError("short stack-map spool read")
            cursor += count

    def equal(self, stream, index, candidate, scratch):
        count = len(candidate) // wire.LOCATION_SIZE
        self.charge_comparison(count)
        stream.seek(index * wire.LOCATION_SIZE)
        for cursor in range(0, len(candidate), _BLOCK_BYTES):
            size = min(_BLOCK_BYTES, len(candidate) - cursor)
            with memoryview(scratch)[:size] as actual:
                self.read_exact(stream, actual)
                with candidate[cursor:cursor + size] as expected:
                    if actual != expected:
                        return False
        return True


def merge_owned_stack_map_payloads(payloads, *, compact_budget, single_buffer):
    """Return the original merger's exact bytes and relocation offsets.

    ``single_buffer`` is supplied only by the caller's verified GIL CPython
    3.13--3.15/64-bit guard. Other hosts retain a two-buffer freeze admission.
    As with the shared consuming API, only completed input slots are retired;
    the caller must clear remaining inputs on failure and charge any aliases.
    """
    if not payloads:
        raise wire.PreciseStackMapError("cannot merge an empty stack-map collection")
    if (sys.implementation.name != "cpython" or compact_budget is None
            or type(payloads) is not list
            or any(type(payload) is not bytes for payload in payloads)):
        raise wire.PreciseStackMapError("consuming stack-map merge requires owned host bytes")
    budget = compact_budget
    input_bytes = sum(map(len, payloads))
    base_entries = len(payloads) + 16
    budget.check_peak(input_bytes + _FIXED_BYTES, entries=base_entries,
                      label="spool merge inputs")
    functions, seen_ids, root_index = [], set(), {}
    scanned = source_index_cache = payload = blob = buffer = view = None
    result, closing, address_offsets = None, None, []
    scratch = bytearray(_BLOCK_BYTES)
    function_bytes = table_locations = merge_locations = unique_vectors = 0
    empty_shape_index, arch = -1, 0
    work = _SpoolWork(budget)

    def admit(extra=0, *, pending_functions=0, pending_vectors=0, label):
        # Function tuple/integers, seen-id set and sort-pointer workspace;
        # digest dict, collision bucket, destination integers; source range
        # cache; and the current scanner's complete descriptors are retained.
        entries = (base_entries + 4 * (len(functions) + pending_functions)
                   + 3 * (unique_vectors + pending_vectors)
                   + 2 * (len(source_index_cache) if source_index_cache is not None else 0)
                   + (len(scanned) if scanned is not None else 0))
        budget.check_peak(input_bytes + _FIXED_BYTES + extra,
                          entries=entries, label=label)

    def output_bound(size, count):
        # A future single output, and therefore disk storage, must itself fit
        # the unchanged allowance. This never credits currently live inputs.
        budget.check_peak(size + _FIXED_BYTES, entries=base_entries + 5 * count,
                          label="spool output bound")

    try:
        with tempfile.TemporaryFile(mode="w+b", buffering=0) as roots, \
                tempfile.TemporaryFile(mode="w+b", buffering=0) as records:
            for consumed_index in range(len(payloads)):
                payload = payloads[consumed_index]
                previous = budget.retained_bytes
                budget.retained_bytes += (
                    input_bytes - len(payload) + _FIXED_BYTES
                    + (base_entries + 4 * len(functions) + 3 * unique_vectors)
                    * budget.metadata_entry_bytes
                )
                try:
                    _count, scanned, table_start, table_count = wire._scan_stack_map_payload(
                        payload, compact_budget=budget,
                    )
                finally:
                    budget.retained_bytes = previous
                source_arch = payload[10]
                if arch and arch != source_arch:
                    raise wire.PreciseStackMapError("cannot merge stack maps for different targets")
                arch = source_arch
                source_index_cache = {}
                with memoryview(payload) as source:
                    for function_id, fn_start, fn_end in scanned:
                        if len(functions) >= wire.MAX_FUNCTIONS:
                            raise wire.PreciseStackMapError("too many stack-map functions")
                        if function_id in seen_ids:
                            raise wire.PreciseStackMapError(
                                f"duplicate stack-map function id {function_id}")
                        admit(pending_functions=1, label="spool merge function")
                        size = fn_end - fn_start
                        output_bound(wire.HEADER_SIZE + function_bytes + size
                                     + table_locations * wire.LOCATION_SIZE,
                                     len(functions) + 1)
                        seen_ids.add(function_id)
                        functions.append((function_id, function_bytes, size))
                        function_bytes += size
                        with source[fn_start:fn_start + wire.FUNCTION_SIZE] as header:
                            blob = bytearray(header)
                        blob[8:16] = b"\0" * 8
                        work.write_all(records, blob)
                        blob = None
                        record_count = (size - wire.FUNCTION_SIZE) // wire.RECORD_SIZE
                        budget.charge_records(record_count)
                        for start in range(fn_start + wire.FUNCTION_SIZE, fn_end, _BLOCK_BYTES):
                            with source[start:min(fn_end, start + _BLOCK_BYTES)] as block:
                                blob = bytearray(block)
                            for cursor in range(0, len(blob), wire.RECORD_SIZE):
                                count = blob[cursor + 20] | (blob[cursor + 21] << 8)
                                index = (blob[cursor + 28] | (blob[cursor + 29] << 8)
                                         | (blob[cursor + 30] << 16) | (blob[cursor + 31] << 24))
                                if index + count > table_count:
                                    raise wire.PreciseStackMapError(
                                        "stack-map record names locations outside its table")
                                source_key = (index << 16) | count
                                if count == 0:
                                    if empty_shape_index < 0:
                                        empty_shape_index = table_locations
                                    destination = empty_shape_index
                                elif source_key in source_index_cache:
                                    destination = source_index_cache[source_key]
                                else:
                                    merge_locations += count
                                    if merge_locations > wire.MAX_LOCATIONS:
                                        raise wire.PreciseStackMapError("too much stack-map location merge work")
                                    work.comparison_limit = 4 * merge_locations
                                    admit(extra=2 * budget.metadata_entry_bytes,
                                          pending_vectors=1, label="spool merge cache")
                                    offset = table_start + index * wire.LOCATION_SIZE
                                    with source[offset:offset + count * wire.LOCATION_SIZE] as candidate:
                                        digest = hashlib.sha256(candidate).digest()
                                        candidates = root_index.get(digest, ())
                                        destination = -1
                                        for existing_count, existing_index in candidates:
                                            # Charge even unequal-length digest collisions
                                            # so forged buckets cannot create unbounded work.
                                            if existing_count != count:
                                                work.charge_comparison(1)
                                            elif work.equal(roots, existing_index, candidate, scratch):
                                                destination = existing_index
                                                break
                                        if destination < 0:
                                            destination = table_locations
                                            if count > wire.MAX_LOCATIONS - table_locations:
                                                raise wire.PreciseStackMapError("too many stack-map locations")
                                            output_bound(wire.HEADER_SIZE + function_bytes
                                                         + (table_locations + count) * wire.LOCATION_SIZE,
                                                         len(functions))
                                            roots.seek(table_locations * wire.LOCATION_SIZE)
                                            work.write_all(roots, candidate)
                                            root_index.setdefault(digest, []).append((count, destination))
                                            table_locations += count
                                            unique_vectors += 1
                                        candidates = digest = None
                                    source_index_cache[source_key] = destination
                                blob[cursor + 28:cursor + 32] = destination.to_bytes(4, "little")
                            work.write_all(records, blob)
                            blob = None
                # No view, chunk or cache owns input bytes past this point.
                retired = len(payload)
                scanned = source_index_cache = None
                payloads[consumed_index] = None
                payload = None
                input_bytes -= retired

            root_index.clear()
            seen_ids.clear()
            root_index = seen_ids = candidates = digest = None
            scratch = None
            total = wire.HEADER_SIZE + function_bytes + table_locations * wire.LOCATION_SIZE
            # Admit before sorting, offset-list growth or output allocation.
            # The portable freeze keeps both allocations charged. The verified
            # BytesIO route keeps one exact bytes backing, never append growth.
            copies = 1 if single_buffer else 2
            budget.check_peak(copies * budget._rounded(sys.getsizeof(b"") + total)
                              + _FIXED_BYTES,
                              entries=base_entries + 5 * len(functions),
                              label="spool merge materialize")
            functions.sort(key=lambda item: item[0])
            buffer = io.BytesIO(b"\0" * total) if single_buffer else bytearray(total)
            view = buffer.getbuffer() if single_buffer else memoryview(buffer)
            view[:wire.HEADER_SIZE] = wire._HEADER.pack(
                wire.MAGIC, wire.VERSION, arch, wire.POINTER_SIZE,
                len(functions), table_locations, 0,
            )
            cursor = wire.HEADER_SIZE
            for function_id, offset, size in functions:
                if size > total - cursor:
                    raise wire.PreciseStackMapError("stack-map spool function outside output")
                records.seek(offset)
                with view[cursor:cursor + size] as target:
                    work.read_exact(records, target)
                address_offsets.append((function_id, cursor + 8))
                cursor += size
            roots.seek(0)
            with view[cursor:] as target:
                work.read_exact(roots, target)
            functions.clear()
            view.release()
            view = None
            result = buffer.getvalue() if single_buffer else bytes(buffer)
            if single_buffer:
                closing, buffer = buffer, None
                try:
                    closing.close()
                finally:
                    closing = None
            buffer = None
            return result, tuple(address_offsets)
    except OSError as exc:
        raise wire.PreciseStackMapError("stack-map temporary storage failed: " + str(exc)) from exc
    finally:
        try:
            if view is not None:
                view.release()
        finally:
            try:
                if single_buffer and buffer is not None:
                    buffer.close()
            finally:
                functions.clear()
                address_offsets.clear()
                if root_index is not None:
                    root_index.clear()
                if seen_ids is not None:
                    seen_ids.clear()
                scanned = source_index_cache = payload = blob = None
                buffer = view = scratch = result = closing = None
