from __future__ import annotations

"""Compiler-private dynamic scalar arena with a host oracle projection.

The self-hosted compiler needs variable-length, pointer-free payload tables;
``pcc.array[ValueClass, N]`` deliberately models only small fixed aggregates.
This arena is the dynamic companion for proven machine-range compiler IDs,
offsets, flags, and spans.

Under CPython, ``pcc.unsafe`` traps and the ordinary ``list[int]`` side is the
semantic oracle.  When pcc compiles this module, the unsafe operations lower to
raw allocation and i64 loads/stores.  The raw pointer is *never* stored in an
object/Dyn field: only its integer address is retained and converted back at
the exact intrinsic seam.  That is the static provenance proof which the old
global managed-refcount shortcut lacked.
"""

from pcc.unsafe import (
    free,
    int_to_ptr,
    load_i64,
    malloc,
    memcpy,
    memset,
    ptr_add,
    ptr_is_null,
    ptr_to_int,
    read as raw_read,
    realloc,
    store_i64,
    store_i32,
    store_i8,
    write as raw_write,
)
from pcc.extern import c_int64, c_ptr, extern, c_obj


_py_bytes_new: "extern" = extern("py_bytes_new", (c_ptr, c_int64), c_obj)


def valueclass(cls):
    """Self-contained host marker for compiler-private value records.

    The frontend recognizes the decorator spelling and emits the aggregate
    projection.  Importing the public host oracle here would pull all of
    ``pcc.value_model`` into the self-host compiler closure.
    """

    from dataclasses import dataclass

    result = dataclass(frozen=True)(cls)
    result.__pcc_valueclass__ = True
    return result


@valueclass
class CompilerInt2:
    first: int
    second: int


@valueclass
class CompilerInt3:
    first: int
    second: int
    third: int


@valueclass
class CompilerInt4:
    first: int
    second: int
    third: int
    fourth: int


class CompilerIntArena:
    """One growable vector of signed 64-bit compiler-internal scalars."""

    __slots__ = (
        "_address",
        "_values",
        "_length",
        "_capacity",
        "_closed",
    )

    def __init__(self, initial_capacity: int = 8) -> None:
        if initial_capacity < 0:
            raise ValueError("compiler int arena capacity must be nonnegative")
        capacity = max(1, initial_capacity)
        self._address: int = 0
        self._values: list[int] = []
        self._length: int = 0
        self._capacity: int = 0
        self._closed: bool = False
        try:
            allocation = malloc(capacity * 8)
        except NotImplementedError:
            # CPython host oracle.  The compiler consumes the same source but
            # lowers malloc directly, so the compiled path never takes this.
            return
        address = ptr_to_int(allocation)
        if address == 0:
            raise MemoryError("compiler int arena allocation failed")
        self._address = address
        self._capacity = capacity

    def __len__(self) -> int:
        # Hot backend readers read _length directly: on CPython len() runs
        # this method as a Python frame, about 15% of a codegen worker.
        return self._length

    @property
    def uses_native_storage(self) -> bool:
        return self._address != 0

    def native_address(self) -> int:
        """Raw storage address as an integer; 0 while the CPython list oracle holds the values.

        Hot kernels that run the same loop shape tens of millions of times
        (the final stack-map record heapsort) read and write this storage
        directly instead of paying one out-of-line getter or setter call per
        scalar.  Callers must not append while holding the address.
        """
        return self._address

    def _grow(self, required: int) -> None:
        capacity = max(required, self._capacity * 2)
        allocation = realloc(int_to_ptr(self._address), capacity * 8)
        address = ptr_to_int(allocation)
        if address == 0:
            raise MemoryError("compiler int arena growth failed")
        self._address = address
        self._capacity = capacity

    def append(self, value: int) -> None:
        if self._address != 0:
            if self._length >= self._capacity:
                self._grow(self._length + 1)
            store_i64(int_to_ptr(self._address), self._length * 8, value)
        else:
            if self._closed:
                raise RuntimeError("compiler int arena is closed")
            self._values.append(value)
        self._length += 1

    def append2(self, first: int, second: int) -> None:
        if self._address != 0:
            required = self._length + 2
            if required > self._capacity:
                self._grow(required)
            address = int_to_ptr(self._address)
            store_i64(address, self._length * 8, first)
            store_i64(address, (self._length + 1) * 8, second)
        else:
            if self._closed:
                raise RuntimeError("compiler int arena is closed")
            self._values.append(first)
            self._values.append(second)
        self._length += 2

    def append3(self, first: int, second: int, third: int) -> None:
        if self._address != 0:
            required = self._length + 3
            if required > self._capacity:
                self._grow(required)
            address = int_to_ptr(self._address)
            store_i64(address, self._length * 8, first)
            store_i64(address, (self._length + 1) * 8, second)
            store_i64(address, (self._length + 2) * 8, third)
        else:
            if self._closed:
                raise RuntimeError("compiler int arena is closed")
            self._values.append(first)
            self._values.append(second)
            self._values.append(third)
        self._length += 3

    def append4(
        self,
        first: int,
        second: int,
        third: int,
        fourth: int,
    ) -> None:
        if self._address != 0:
            required = self._length + 4
            if required > self._capacity:
                self._grow(required)
            address = int_to_ptr(self._address)
            store_i64(address, self._length * 8, first)
            store_i64(address, (self._length + 1) * 8, second)
            store_i64(address, (self._length + 2) * 8, third)
            store_i64(address, (self._length + 3) * 8, fourth)
        else:
            if self._closed:
                raise RuntimeError("compiler int arena is closed")
            self._values.append(first)
            self._values.append(second)
            self._values.append(third)
            self._values.append(fourth)
        self._length += 4

    def append_zeros(self, count: int) -> None:
        """Append a zero-filled compiler-proven scalar span."""

        if count < 0:
            raise ValueError("compiler int arena zero span must be nonnegative")
        if self._address != 0:
            required = self._length + count
            if required > self._capacity:
                self._grow(required)
            if count:
                memset(
                    ptr_add(int_to_ptr(self._address), self._length * 8),
                    0,
                    count * 8,
                )
        else:
            if self._closed:
                raise RuntimeError("compiler int arena is closed")
            if (type(count) is int and count >= 128
                    and type(self) is CompilerIntArena
                    and type(self._values) is list):
                values = self._values
                if not values and count <= 9223372036854775807:
                    # A fresh dense plane needs one final-size allocation.
                    # In-place repetition keeps existing list aliases valid
                    # without a full-size temporary or repeated growth.
                    values.append(0)
                    values *= count
                else:
                    # Nonempty spans retain their prefix with bounded scratch.
                    block_words = min(count, 4096)
                    zeros = [0] * block_words
                    remaining = count
                    while remaining >= block_words:
                        values.extend(zeros)
                        remaining -= block_words
                    if remaining:
                        values.extend(zeros[:remaining])
            else:
                # Preserve custom scalar/container callbacks and the small
                # spans for which a separate block would add allocation.
                index = 0
                while index < count:
                    self._values.append(0)
                    index += 1
        self._length += count

    def get(self, index: int) -> int:
        if self._address != 0:
            if index < 0 or index >= self._length:
                raise IndexError("compiler int arena index out of range")
            return load_i64(int_to_ptr(self._address), index * 8)
        if self._closed:
            raise RuntimeError("compiler int arena is closed")
        if index < 0 or index >= self._length:
            raise IndexError("compiler int arena index out of range")
        return self._values[index]

    def get_unchecked(self, index: int) -> int:
        """Read one compiler-proven in-range index without semantic guards."""

        if self._address != 0:
            return load_i64(int_to_ptr(self._address), index * 8)
        return self._values[index]

    def get2_unchecked(self, record_index: int) -> CompilerInt2:
        index = record_index * 2
        if self._address != 0:
            address = int_to_ptr(self._address)
            return CompilerInt2(
                load_i64(address, index * 8),
                load_i64(address, (index + 1) * 8),
            )
        return CompilerInt2(self._values[index], self._values[index + 1])

    def get3_unchecked(self, record_index: int) -> CompilerInt3:
        index = record_index * 3
        if self._address != 0:
            address = int_to_ptr(self._address)
            return CompilerInt3(
                load_i64(address, index * 8),
                load_i64(address, (index + 1) * 8),
                load_i64(address, (index + 2) * 8),
            )
        return CompilerInt3(
            self._values[index],
            self._values[index + 1],
            self._values[index + 2],
        )

    def get4_unchecked(self, record_index: int) -> CompilerInt4:
        index = record_index * 4
        if self._address != 0:
            address = int_to_ptr(self._address)
            return CompilerInt4(
                load_i64(address, index * 8),
                load_i64(address, (index + 1) * 8),
                load_i64(address, (index + 2) * 8),
                load_i64(address, (index + 3) * 8),
            )
        return CompilerInt4(
            self._values[index],
            self._values[index + 1],
            self._values[index + 2],
            self._values[index + 3],
        )

    def set(self, index: int, value: int) -> None:
        if self._address != 0:
            if index < 0 or index >= self._length:
                raise IndexError("compiler int arena index out of range")
            store_i64(int_to_ptr(self._address), index * 8, value)
        else:
            if self._closed:
                raise RuntimeError("compiler int arena is closed")
            if index < 0 or index >= self._length:
                raise IndexError("compiler int arena index out of range")
            self._values[index] = value

    def set_unchecked(self, index: int, value: int) -> None:
        """Write one compiler-proven in-range index without semantic guards."""

        if self._address != 0:
            store_i64(int_to_ptr(self._address), index * 8, value)
        else:
            self._values[index] = value

    def set3_unchecked(
        self,
        index: int,
        first: int,
        second: int,
        third: int,
    ) -> None:
        """Overwrite three contiguous compiler-proven in-range scalars."""

        if self._address != 0:
            address = int_to_ptr(self._address)
            store_i64(address, index * 8, first)
            store_i64(address, (index + 1) * 8, second)
            store_i64(address, (index + 2) * 8, third)
        else:
            self._values[index] = first
            self._values[index + 1] = second
            self._values[index + 2] = third

    def set2_unchecked(self, index: int, first: int, second: int) -> None:
        """Overwrite two contiguous compiler-proven in-range scalars."""

        if self._address != 0:
            address = int_to_ptr(self._address)
            store_i64(address, index * 8, first)
            store_i64(address, (index + 1) * 8, second)
        else:
            self._values[index] = first
            self._values[index + 1] = second

    def clear(self) -> None:
        """Reuse allocated capacity while dropping every logical scalar."""

        if self._closed:
            raise RuntimeError("compiler int arena is closed")
        self._length = 0
        if self._address == 0:
            self._values.clear()

    def truncate(self, length: int) -> None:
        """Drop a proven suffix while retaining allocated native capacity."""

        if length < 0 or length > self._length:
            raise IndexError("compiler int arena truncate length out of range")
        if self._closed:
            raise RuntimeError("compiler int arena is closed")
        self._length = length
        if self._address == 0:
            del self._values[length:]

    def zero_prefix_unchecked(self, count: int) -> None:
        """Zero one compiler-proven prefix in a single arena call."""

        index = 0
        if self._address != 0:
            address = int_to_ptr(self._address)
            while index < count:
                store_i64(address, index * 8, 0)
                index += 1
            return
        while index < count:
            self._values[index] = 0
            index += 1

    def copy_prefix_from_unchecked(
        self,
        source: CompilerIntArena,
        source_start: int,
        count: int,
    ) -> None:
        """Copy a proven source span into this arena's prefix."""

        index = 0
        if self._address != 0:
            if source._address == 0:
                raise RuntimeError("compiler arena storage projections disagree")
            target_address = int_to_ptr(self._address)
            source_address = int_to_ptr(source._address)
            while index < count:
                store_i64(
                    target_address,
                    index * 8,
                    load_i64(source_address, (source_start + index) * 8),
                )
                index += 1
            return
        if source._address != 0:
            raise RuntimeError("compiler arena storage projections disagree")
        while index < count:
            self._values[index] = source._values[source_start + index]
            index += 1

    def extend_from(self, source: CompilerIntArena, start: int, count: int) -> None:
        """Append ``source[start:start + count]`` with one bulk copy."""

        if start < 0 or count < 0 or start + count > len(source):
            raise IndexError("compiler int arena extension span out of range")
        if self._address != 0:
            if source._address == 0:
                raise RuntimeError("compiler arena storage projections disagree")
            required = self._length + count
            if required > self._capacity:
                self._grow(required)
            if count:
                # Read the source address after growth: it may be this arena.
                memcpy(
                    ptr_add(int_to_ptr(self._address), self._length * 8),
                    ptr_add(int_to_ptr(source._address), start * 8),
                    count * 8,
                )
        else:
            if self._closed:
                raise RuntimeError("compiler int arena is closed")
            if source._address != 0:
                raise RuntimeError("compiler arena storage projections disagree")
            # A complete built-in run already is the sequence to extend.
            # list.extend handles self-extension; retain slicing for partial
            # ranges and any custom lookup, arithmetic, or container hooks.
            if (type(self) is CompilerIntArena and type(source) is CompilerIntArena
                    and type(start) is int and type(count) is int and start == 0
                    and type(self._values) is list and type(source._values) is list
                    and count == len(source._values)):
                self._values.extend(source._values)
            else:
                self._values.extend(source._values[start:start + count])
        self._length += count

    def extend_list(self, values: list[int]) -> None:
        """Append every scalar of one proven ``list[int]`` in order."""

        count = len(values)
        if self._address != 0:
            required = self._length + count
            if required > self._capacity:
                self._grow(required)
            address = int_to_ptr(self._address)
            index = 0
            while index < count:
                store_i64(address, (self._length + index) * 8, values[index])
                index += 1
        else:
            if self._closed:
                raise RuntimeError("compiler int arena is closed")
            self._values.extend(values)
        self._length += count

    def min_from(self, start: int, count: int) -> int:
        """Smallest scalar of one nonempty proven in-range span."""

        if start < 0 or count <= 0 or start + count > self._length:
            raise IndexError("compiler int arena minimum span out of range")
        if self._address != 0:
            address = int_to_ptr(self._address)
            smallest = load_i64(address, start * 8)
            index = start + 1
            while index < start + count:
                value = load_i64(address, index * 8)
                if value < smallest:
                    smallest = value
                index += 1
            return smallest
        if self._closed:
            raise RuntimeError("compiler int arena is closed")
        return min(self._values[start:start + count])

    def or_prefix_from_unchecked(
        self,
        source: CompilerIntArena,
        source_start: int,
        count: int,
    ) -> None:
        """OR a proven source span into this arena's prefix."""

        index = 0
        if self._address != 0:
            if source._address == 0:
                raise RuntimeError("compiler arena storage projections disagree")
            target_address = int_to_ptr(self._address)
            source_address = int_to_ptr(source._address)
            while index < count:
                store_i64(
                    target_address,
                    index * 8,
                    load_i64(target_address, index * 8)
                    | load_i64(source_address, (source_start + index) * 8),
                )
                index += 1
            return
        if source._address != 0:
            raise RuntimeError("compiler arena storage projections disagree")
        while index < count:
            self._values[index] = (
                self._values[index] | source._values[source_start + index]
            )
            index += 1

    def converge_liveness_row_unchecked(
        self,
        uses: CompilerIntArena,
        definitions: CompilerIntArena,
        live_out: CompilerIntArena,
        live_in: CompilerIntArena,
        row_start: int,
        count: int,
        word_mask: int,
    ) -> bool:
        """Publish one liveness transfer row and report whether it changed."""

        changed = False
        index = 0
        if self._address != 0:
            if (
                uses._address == 0
                or definitions._address == 0
                or live_out._address == 0
                or live_in._address == 0
            ):
                raise RuntimeError("compiler arena storage projections disagree")
            scratch_address = int_to_ptr(self._address)
            uses_address = int_to_ptr(uses._address)
            definitions_address = int_to_ptr(definitions._address)
            live_out_address = int_to_ptr(live_out._address)
            live_in_address = int_to_ptr(live_in._address)
            while index < count:
                matrix_index = row_start + index
                outgoing = load_i64(scratch_address, index * 8)
                incoming = load_i64(uses_address, matrix_index * 8) | (
                    outgoing
                    & (
                        word_mask
                        ^ load_i64(definitions_address, matrix_index * 8)
                    )
                )
                if load_i64(live_out_address, matrix_index * 8) != outgoing:
                    store_i64(live_out_address, matrix_index * 8, outgoing)
                    changed = True
                if load_i64(live_in_address, matrix_index * 8) != incoming:
                    store_i64(live_in_address, matrix_index * 8, incoming)
                    changed = True
                index += 1
            return changed
        if (
            uses._address != 0
            or definitions._address != 0
            or live_out._address != 0
            or live_in._address != 0
        ):
            raise RuntimeError("compiler arena storage projections disagree")
        while index < count:
            matrix_index = row_start + index
            outgoing = self._values[index]
            incoming = uses._values[matrix_index] | (
                outgoing & (word_mask ^ definitions._values[matrix_index])
            )
            if live_out._values[matrix_index] != outgoing:
                live_out._values[matrix_index] = outgoing
                changed = True
            if live_in._values[matrix_index] != incoming:
                live_in._values[matrix_index] = incoming
                changed = True
            index += 1
        return changed

    def converge_liveness_incoming_row_unchecked(
        self,
        uses: CompilerIntArena,
        definitions: CompilerIntArena,
        live_out: CompilerIntArena,
        live_in: CompilerIntArena,
        row_start: int,
        count: int,
        word_mask: int,
    ) -> bool:
        """Publish both rows; report only the incoming dependency change.

        Owned dirty-row scheduling consumes only successor live-in. A new
        live-out bit killed by this block's definitions cannot affect any
        predecessor, but must still be stored in the outgoing matrix.
        Keep the original helper for the callback-sensitive full sweep.
        """

        changed = False
        index = 0
        if self._address != 0:
            if (
                uses._address == 0
                or definitions._address == 0
                or live_out._address == 0
                or live_in._address == 0
            ):
                raise RuntimeError("compiler arena storage projections disagree")
            scratch_address = int_to_ptr(self._address)
            uses_address = int_to_ptr(uses._address)
            definitions_address = int_to_ptr(definitions._address)
            live_out_address = int_to_ptr(live_out._address)
            live_in_address = int_to_ptr(live_in._address)
            while index < count:
                matrix_index = row_start + index
                outgoing = load_i64(scratch_address, index * 8)
                incoming = load_i64(uses_address, matrix_index * 8) | (
                    outgoing
                    & (
                        word_mask
                        ^ load_i64(definitions_address, matrix_index * 8)
                    )
                )
                if load_i64(live_out_address, matrix_index * 8) != outgoing:
                    store_i64(live_out_address, matrix_index * 8, outgoing)
                if load_i64(live_in_address, matrix_index * 8) != incoming:
                    store_i64(live_in_address, matrix_index * 8, incoming)
                    changed = True
                index += 1
            return changed
        if (
            uses._address != 0
            or definitions._address != 0
            or live_out._address != 0
            or live_in._address != 0
        ):
            raise RuntimeError("compiler arena storage projections disagree")
        while index < count:
            matrix_index = row_start + index
            outgoing = self._values[index]
            incoming = uses._values[matrix_index] | (
                outgoing & (word_mask ^ definitions._values[matrix_index])
            )
            if live_out._values[matrix_index] != outgoing:
                live_out._values[matrix_index] = outgoing
            if live_in._values[matrix_index] != incoming:
                live_in._values[matrix_index] = incoming
                changed = True
            index += 1
        return changed

    def sort_nonnegative_radix(self) -> None:
        """Sort nonnegative machine-range keys in linear passes over raw bytes.

        The final Mach-O relocation order encodes (descending address,
        original index) into one nonnegative i64. Heapsort performs random
        loads O(N log N) on millions of those keys; an LSD radix pass keeps
        the exact numeric/stable-tie order with sequential raw accesses.
        """
        if self._closed:
            raise RuntimeError("compiler int arena is closed")
        length = self._length
        if length < 2:
            return
        if self._address == 0:
            for value in self._values:
                if value < 0:
                    raise ValueError("radix sort requires nonnegative keys")
            self._values.sort()
            return

        original = int_to_ptr(self._address)
        maximum = 0
        index = 0
        while index < length:
            value = load_i64(original, index * 8)
            if value < 0:
                raise ValueError("radix sort requires nonnegative keys")
            if value > maximum:
                maximum = value
            index += 1
        if maximum == 0:
            return

        scratch = malloc(length * 8)
        counts = malloc(256 * 8)
        if ptr_is_null(scratch) or ptr_is_null(counts):
            free(scratch)
            free(counts)
            raise MemoryError("compiler radix sort allocation failed")
        try:
            source = original
            destination = scratch
            shift = 0
            while (maximum >> shift) != 0:
                memset(counts, 0, 256 * 8)
                index = 0
                while index < length:
                    value = load_i64(source, index * 8)
                    bucket = (value >> shift) & 255
                    at = bucket * 8
                    store_i64(counts, at, load_i64(counts, at) + 1)
                    index += 1
                offset = 0
                bucket = 0
                while bucket < 256:
                    at = bucket * 8
                    count = load_i64(counts, at)
                    store_i64(counts, at, offset)
                    offset += count
                    bucket += 1
                index = 0
                while index < length:
                    value = load_i64(source, index * 8)
                    bucket = (value >> shift) & 255
                    at = bucket * 8
                    position = load_i64(counts, at)
                    store_i64(destination, position * 8, value)
                    store_i64(counts, at, position + 1)
                    index += 1
                previous = source
                source = destination
                destination = previous
                shift += 8
            if ptr_to_int(source) != self._address:
                memcpy(original, source, length * 8)
        finally:
            free(counts)
            free(scratch)

    def sort(self) -> None:
        """Sort the logical signed-i64 payload without object projection.

        CPython's list is only the semantic oracle, so its native sort keeps
        host pcc from paying a Python-level comparison loop.  A pcc-compiled
        arena always owns raw storage and executes the in-place heapsort below;
        no list or boxed record is reachable on that path.
        """

        length = self._length
        if length < 2:
            return
        if self._address == 0:
            if self._closed:
                raise RuntimeError("compiler int arena is closed")
            self._values.sort()
            return

        address = int_to_ptr(self._address)
        start = length // 2 - 1
        while start >= 0:
            root = start
            value = load_i64(address, root * 8)
            child = root * 2 + 1
            while child < length:
                right = child + 1
                if right < length and load_i64(
                    address, child * 8
                ) < load_i64(address, right * 8):
                    child = right
                child_value = load_i64(address, child * 8)
                if value >= child_value:
                    break
                store_i64(address, root * 8, child_value)
                root = child
                child = root * 2 + 1
            store_i64(address, root * 8, value)
            start -= 1

        end = length - 1
        while end > 0:
            tail_value = load_i64(address, end * 8)
            store_i64(address, end * 8, load_i64(address, 0))
            root = 0
            child = 1
            while child < end:
                right = child + 1
                if right < end and load_i64(
                    address, child * 8
                ) < load_i64(address, right * 8):
                    child = right
                child_value = load_i64(address, child * 8)
                if tail_value >= child_value:
                    break
                store_i64(address, root * 8, child_value)
                root = child
                child = root * 2 + 1
            store_i64(address, root * 8, tail_value)
            end -= 1

    def write_raw_fd(self, fd: int) -> None:
        """Write the native logical payload without scalar projection."""

        if self._closed or self._address == 0:
            raise RuntimeError("compiler arena has no native payload")
        total = self._length * 8
        offset = 0
        address = int_to_ptr(self._address)
        while offset < total:
            written = raw_write(fd, ptr_add(address, offset), total - offset)
            if written <= 0:
                raise OSError("compiler arena raw write failed")
            offset += written

    def read_raw_fd(self, fd: int, count: int) -> None:
        """Replace an empty native arena from exactly ``count`` i64 words."""

        if count < 0:
            raise ValueError("compiler arena raw count must be nonnegative")
        if self._closed or self._address == 0:
            raise RuntimeError("compiler arena has no native payload")
        if self._length != 0:
            raise RuntimeError("compiler arena raw read requires an empty arena")
        if count > self._capacity:
            self._grow(count)
        total = count * 8
        offset = 0
        address = int_to_ptr(self._address)
        while offset < total:
            consumed = raw_read(fd, ptr_add(address, offset), total - offset)
            if consumed <= 0:
                raise OSError("compiler arena raw read was truncated")
            offset += consumed
        self._length = count

    def sleb_size(self) -> int:
        """Exact signed LEB128 size for the logical i64 column."""
        size = 0
        index = 0
        while index < self._length:
            value = self.get_unchecked(index)
            while True:
                byte = value & 127
                value >>= 7
                size += 1
                if (value == 0 and byte & 64 == 0) or (value == -1 and byte & 64 != 0):
                    break
            index += 1
        return size

    def pack_sleb_bytes(self) -> bytes:
        size = self.sleb_size()
        if size == 0:
            return b""
        if self._address == 0:
            data = bytearray()
            index = 0
            while index < self._length:
                value = self.get_unchecked(index)
                while True:
                    byte = value & 127
                    value >>= 7
                    done = (value == 0 and byte & 64 == 0) or (value == -1 and byte & 64 != 0)
                    data.append(byte if done else byte | 128)
                    if done:
                        break
                index += 1
            return bytes(data)
        allocation = malloc(size)
        if ptr_is_null(allocation):
            raise MemoryError("compiler arena compact allocation failed")
        try:
            index = 0
            offset = 0
            while index < self._length:
                value = self.get_unchecked(index)
                while True:
                    byte = value & 127
                    value >>= 7
                    done = (value == 0 and byte & 64 == 0) or (value == -1 and byte & 64 != 0)
                    store_i8(allocation, offset, byte if done else byte | 128)
                    offset += 1
                    if done:
                        break
                index += 1
            result = _py_bytes_new(allocation, size)
            if ptr_is_null(result):
                raise MemoryError("compiler arena compact bytes allocation failed")
            return result
        finally:
            free(allocation)

    def read_sleb_bytes(self, raw: bytes, count: int) -> None:
        """Read exactly count canonical signed i64 values."""
        if count < 0 or self._length != 0 or self._closed:
            raise ValueError("compiler arena compact read requires empty arena")
        size = len(raw)
        if size < count or size > count * 10:
            raise ValueError("compiler arena compact size mismatch")
        index = 0
        offset = 0
        while index < count:
            value = 0
            shift = 0
            previous = 0
            while True:
                if offset >= size:
                    raise ValueError("compiler arena compact payload is truncated")
                byte = raw[offset]
                offset += 1
                if shift == 63:
                    if byte != 0 and byte != 127:
                        raise ValueError("compiler arena compact integer out of range")
                    if byte == 127:
                        value |= -9223372036854775808
                else:
                    value |= (byte & 127) << shift
                if byte & 128 == 0:
                    if shift > 0 and ((byte == 0 and previous & 64 == 0)
                                      or (byte == 127 and previous & 64 != 0)):
                        raise ValueError("compiler arena compact integer is not canonical")
                    if shift < 63 and byte & 64:
                        value |= -1 << (shift + 7)
                    break
                previous = byte
                shift += 7
            self.append(value)
            index += 1
        if offset != size:
            raise ValueError("compiler arena compact payload has trailing bytes")

    def pack_u32_bytes(self) -> bytes:
        """Pack proven uint32 words into one bytes allocation."""

        if self._closed:
            raise RuntimeError("compiler int arena is closed")
        if self._length == 0:
            return b""
        index = 0
        while index < self._length:
            value = self.get_unchecked(index)
            if value < 0 or value > 0xFFFFFFFF:
                raise ValueError("compiler word arena value is outside uint32")
            index += 1
        if self._address == 0:
            # Joining one bytes object per word retains both the entire
            # object list and join's per-item scratch beside the input arena.
            # Bound that scratch before joining the final compact payloads.
            chunks = []
            word_chunks = []
            host_pack_chunk_words = 1024
            index = 0
            while index < self._length:
                word_chunks.append(
                    self._values[index].to_bytes(4, "little")
                )
                index += 1
                if len(word_chunks) == host_pack_chunk_words:
                    chunks.append(b"".join(word_chunks))
                    word_chunks.clear()
            if word_chunks:
                chunks.append(b"".join(word_chunks))
            return b"".join(chunks)
        total = self._length * 4
        allocation = malloc(total)
        if ptr_is_null(allocation):
            raise MemoryError("compiler word arena pack allocation failed")
        index = 0
        while index < self._length:
            store_i32(
                allocation,
                index * 4,
                self.get_unchecked(index),
            )
            index += 1
        result = _py_bytes_new(allocation, total)
        free(allocation)
        if ptr_is_null(result):
            raise MemoryError("compiler word arena bytes allocation failed")
        return result

    def close(self) -> None:
        if self._closed:
            return
        if self._address != 0:
            free(int_to_ptr(self._address))
        self._address = 0
        self._capacity = 0
        self._length = 0
        self._values.clear()
        self._closed = True

    def diagnostic_values(self) -> list[int]:
        """Materialize the object projection only for tests/diagnostics."""

        if self._closed:
            raise RuntimeError("compiler int arena is closed")
        result: list[int] = []
        index = 0
        while index < self._length:
            result.append(self.get(index))
            index += 1
        return result


class CompilerRecordSpanArena:
    """Scope-owned integer sequences with identity-free, generation-checked keys.

    Span headers are mutable roots of immutable concatenation trees. Extending
    snapshots the source root, so later source appends and self-extension have
    ordinary sequence semantics. Nodes and traversal state contain only scalar
    IDs; the normal replay API never constructs Python per-record containers.

    Node triples are a leaf ``(-1, record_id, 1)``, a concatenation
    ``(left, right, 0)`` or a run ``(-2, start, count)`` of ``count`` record
    IDs stored contiguously in ``runs``. A whole sequence appended at once is
    one run: one node and one scalar per record instead of two node triples.
    A checked run ``(-3, start, count)`` is identical except that its producer
    validated every ID against the arena's owner before publication; the
    arena itself never interprets that mark.
    A cursor holds pending node IDs and, for a run being replayed, the pair
    ``(end, -(position + 1))``; node IDs are never negative.
    """

    def __init__(self) -> None:
        self.nodes: CompilerIntArena = CompilerIntArena()
        try:
            self.spans: CompilerIntArena = CompilerIntArena()
        except Exception:
            self.nodes.close()
            raise
        try:
            self.runs: CompilerIntArena = CompilerIntArena()
        except Exception:
            self.spans.close()
            self.nodes.close()
            raise
        self.generation: int = 1
        self.closed: bool = False
        self.projection_count: int = 0

    def _require_open(self) -> None:
        if self.closed:
            raise RuntimeError("compiler record span arena is closed")

    def _root(self, span: CompilerInt2) -> CompilerInt2:
        self._require_open()
        if span.second != self.generation:
            raise RuntimeError("compiler record span handle is stale")
        if span.first < 0 or span.first >= self.spans._length // 2:
            raise IndexError("compiler record span handle is out of range")
        return self.spans.get2_unchecked(span.first)

    def new_span(self) -> CompilerInt2:
        self._require_open()
        index = len(self.spans) // 2
        self.spans.append2(-1, 0)
        return CompilerInt2(index, self.generation)

    def length(self, span: CompilerInt2) -> int:
        root: CompilerInt2 = self._root(span)
        return root.second

    def append(self, span: CompilerInt2, record_id: int) -> None:
        root: CompilerInt2 = self._root(span)
        if record_id < 0:
            raise ValueError("compiler record id must be nonnegative")
        if root.second >= 0x7FFFFFFF:
            raise OverflowError("compiler record span exceeds its length limit")
        leaf = self.nodes._length // 3
        self.nodes.append3(-1, record_id, 1)
        next_root = leaf
        if root.first >= 0:
            next_root = self.nodes._length // 3
            # Concat traversal reads only child IDs. Length belongs to the
            # mutable span header; do not retain duplicate count objects.
            self.nodes.append3(root.first, leaf, 0)
        self.spans.set2_unchecked(span.first * 2, next_root, root.second + 1)

    def extend(self, destination: CompilerInt2, source: CompilerInt2) -> None:
        left: CompilerInt2 = self._root(destination)
        right: CompilerInt2 = self._root(source)
        if right.second == 0:
            return
        if left.second > 0x7FFFFFFF - right.second:
            raise OverflowError("compiler record span exceeds its length limit")
        next_root = right.first
        if left.first >= 0:
            next_root = self.nodes._length // 3
            self.nodes.append3(left.first, right.first, 0)
        self.spans.set2_unchecked(
            destination.first * 2, next_root, left.second + right.second,
        )

    def append_ids(self, span: CompilerInt2, ids: CompilerIntArena, checked: bool = False) -> None:
        """Append every record ID of ``ids`` in order as one run node."""

        root: CompilerInt2 = self._root(span)
        count = len(ids)
        if count == 0:
            return
        if root.second > 0x7FFFFFFF - count:
            raise OverflowError("compiler record span exceeds its length limit")
        start = self.runs._length
        self.runs.extend_from(ids, 0, count)
        self._publish_run(span, root, start, count, -3 if checked else -2)

    def append_id_list(self, span: CompilerInt2, records: list[int]) -> None:
        """Append every record ID of one ``list[int]`` in order as one run node."""

        root: CompilerInt2 = self._root(span)
        count = len(records)
        if count == 0:
            return
        if root.second > 0x7FFFFFFF - count:
            raise OverflowError("compiler record span exceeds its length limit")
        start = self.runs._length
        self.runs.extend_list(records)
        self._publish_run(span, root, start, count, -2)

    def _publish_run(self, span: CompilerInt2, root: CompilerInt2, start: int, count: int, kind: int) -> None:
        # The copied payload is unreachable until the root commit below, so a
        # rejected sequence leaves every published span unchanged.
        try:
            if self.runs.min_from(start, count) < 0:
                raise ValueError("compiler record id must be nonnegative")
            run = self.nodes._length // 3
            self.nodes.append3(kind, start, count)
            next_root = run
            if root.first >= 0:
                next_root = self.nodes._length // 3
                self.nodes.append3(root.first, run, 0)
        except Exception:
            self.runs.truncate(start)
            raise
        self.spans.set2_unchecked(span.first * 2, next_root, root.second + count)

    def is_checked_run(self, span: CompilerInt2) -> bool:
        """Whether the span is exactly one checked run (a snapshot shares it)."""

        root: CompilerInt2 = self._root(span)
        if root.first < 0 or root.first >= self.nodes._length // 3:
            return False
        node: CompilerInt3 = self.nodes.get3_unchecked(root.first)
        return node.first == -3 and node.third == root.second

    def copy_ids(self, span: CompilerInt2, out: CompilerIntArena) -> None:
        """Append the span's record IDs to ``out``; runs are copied in bulk."""

        root: CompilerInt2 = self._root(span)
        if root.first < 0:
            return
        pending = CompilerIntArena()
        try:
            pending.append(root.first)
            while pending._length > 0:
                index = pending._length - 1
                node_id = pending.get_unchecked(index)
                pending.truncate(index)
                if node_id < 0 or node_id >= self.nodes._length // 3:
                    raise RuntimeError("compiler record span node is out of range")
                node: CompilerInt3 = self.nodes.get3_unchecked(node_id)
                if node.first == -1:
                    if node.second < 0 or node.third != 1:
                        raise RuntimeError("compiler record span leaf is invalid")
                    out.append(node.second)
                elif node.first == -2 or node.first == -3:
                    if node.second < 0 or node.third < 1 or node.second + node.third > self.runs._length:
                        raise RuntimeError("compiler record span run is invalid")
                    out.extend_from(self.runs, node.second, node.third)
                else:
                    if (
                        node.first < 0 or node.first >= node_id
                        or node.second < 0 or node.second >= node_id
                    ):
                        raise RuntimeError("compiler record span tree is not acyclic")
                    pending.append2(node.second, node.first)
        finally:
            pending.close()

    def start_cursor(self, span: CompilerInt2, cursor: CompilerIntArena) -> None:
        root: CompilerInt2 = self._root(span)
        cursor.clear()
        cursor.append(self.generation)
        if root.first >= 0:
            cursor.append(root.first)

    def next_record(self, cursor: CompilerIntArena) -> int:
        self._require_open()
        if cursor._length < 1 or cursor.get_unchecked(0) != self.generation:
            raise RuntimeError("compiler record span cursor is stale")
        while cursor._length > 1:
            index = cursor._length - 1
            node_id = cursor.get_unchecked(index)
            if node_id < 0:
                # A run in progress: (end, -(position + 1)) above the generation.
                position = -node_id - 1
                if index < 2:
                    raise RuntimeError("compiler record span cursor is invalid")
                end = cursor.get_unchecked(index - 1)
                if position >= end or end > self.runs._length:
                    raise RuntimeError("compiler record span run is invalid")
                if position + 1 < end:
                    cursor.set_unchecked(index, -(position + 2))
                else:
                    cursor.truncate(index - 1)
                return self.runs.get_unchecked(position)
            cursor.truncate(index)
            if node_id >= self.nodes._length // 3:
                raise RuntimeError("compiler record span node is out of range")
            node: CompilerInt3 = self.nodes.get3_unchecked(node_id)
            if node.first == -1:
                if node.second < 0 or node.third != 1:
                    raise RuntimeError("compiler record span leaf is invalid")
                return node.second
            if node.first == -2 or node.first == -3:
                if node.second < 0 or node.third < 1 or node.second + node.third > self.runs._length:
                    raise RuntimeError("compiler record span run is invalid")
                if node.third > 1:
                    cursor.append2(node.second + node.third, -(node.second + 2))
                return self.runs.get_unchecked(node.second)
            if (
                node.first < 0 or node.first >= node_id
                or node.second < 0 or node.second >= node_id
            ):
                raise RuntimeError("compiler record span tree is not acyclic")
            cursor.append2(node.second, node.first)
        return -1

    def reset(self) -> None:
        self._require_open()
        if self.generation >= 0x7FFFFFFF:
            raise OverflowError("compiler record span generation limit reached")
        self.nodes.clear()
        self.spans.clear()
        self.runs.clear()
        self.generation += 1

    def close(self) -> None:
        if self.closed:
            return
        self.nodes.close()
        self.spans.close()
        self.runs.close()
        self.closed = True

    def diagnostic_values(self, span: CompilerInt2) -> list[int]:
        """Counted oracle projection; normal consumers replay into their owner."""
        self.projection_count += 1
        result: list[int] = []
        cursor = CompilerIntArena()
        try:
            self.start_cursor(span, cursor)
            value = self.next_record(cursor)
            while value >= 0:
                result.append(value)
                value = self.next_record(cursor)
            return result
        finally:
            cursor.close()


__all__ = [
    "CompilerInt2",
    "CompilerInt3",
    "CompilerInt4",
    "CompilerIntArena",
    "CompilerRecordSpanArena",
]
