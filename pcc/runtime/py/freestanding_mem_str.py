"""Freestanding pcc-Python memory and string substrate.

This module is compiled into raw C-ABI objects.  It deliberately uses only
``pcc.unsafe`` byte loads/stores and pointer arithmetic: no libc call, managed
Python object, allocator, exception, boxing, or GC service is available here.
The retained musl sources are differential oracles, not this implementation.
"""

from pcc import i64
from pcc.extern import c_abi_export, c_abi_typed_export, c_ptr
from pcc.unsafe import (
    load_i32,
    load_i64,
    load_i8,
    null,
    ptr_add,
    ptr_diff,
    store_i32,
    store_i8,
    store_i64,
    wrapping_mul_i64,
)

__pcc_freestanding__ = True


@c_abi_export("memcpy")
def pcc_memcpy(dst, src, size: i64) -> c_ptr:
    # Eight bytes per iteration.  A byte-at-a-time copy left `memmove` as the
    # #2 leaf (562 of ~10000 samples) when pcc1 compiles a real module; every
    # string, list-grow and object copy in the runtime funnels through here.
    # The tail is one more word ending at `size` (it may overlap the last
    # loop word), and a copy below 16 bytes is a first and a last word (or
    # 4-byte half) read before either is stored: str.join copied its short
    # parts at five instructions a byte.
    if size >= 16:
        last: i64 = load_i64(src, size - 8)
        index: i64 = 0
        limit: i64 = size - 8
        while index < limit:
            store_i64(dst, index, load_i64(src, index))
            index = index + 8
        store_i64(dst, size - 8, last)
        return dst
    if size >= 8:
        head: i64 = load_i64(src, 0)
        tail: i64 = load_i64(src, size - 8)
        store_i64(dst, 0, head)
        store_i64(dst, size - 8, tail)
        return dst
    if size >= 4:
        head_half: i64 = load_i32(src, 0)
        tail_half: i64 = load_i32(src, size - 4)
        store_i32(dst, 0, head_half)
        store_i32(dst, size - 4, tail_half)
        return dst
    byte_index: i64 = 0
    while byte_index < size:
        store_i8(dst, byte_index, load_i8(src, byte_index))
        byte_index = byte_index + 1
    return dst


@c_abi_export("memmove")
def pcc_memmove(dst, src, size: i64) -> c_ptr:
    # Below 16 bytes every word is read before any is stored, so overlap in
    # either direction is safe. Longer copies run forward (dst below src, or
    # disjoint) or backward, eight bytes at a time; the word the loop does
    # not reach is read before the loop stores anything and stored last.
    if size < 16:
        if size >= 8:
            head: i64 = load_i64(src, 0)
            tail: i64 = load_i64(src, size - 8)
            store_i64(dst, 0, head)
            store_i64(dst, size - 8, tail)
            return dst
        if size >= 4:
            head_half: i64 = load_i32(src, 0)
            tail_half: i64 = load_i32(src, size - 4)
            store_i32(dst, 0, head_half)
            store_i32(dst, size - 4, tail_half)
            return dst
        small_delta: i64 = ptr_diff(dst, src)
        if small_delta <= 0 or small_delta >= size:
            up: i64 = 0
            while up < size:
                store_i8(dst, up, load_i8(src, up))
                up = up + 1
            return dst
        down: i64 = size
        while down > 0:
            down = down - 1
            store_i8(dst, down, load_i8(src, down))
        return dst
    delta: i64 = ptr_diff(dst, src)
    if delta <= 0 or delta >= size:
        last: i64 = load_i64(src, size - 8)
        forward: i64 = 0
        forward_limit: i64 = size - 8
        while forward < forward_limit:
            store_i64(dst, forward, load_i64(src, forward))
            forward = forward + 8
        store_i64(dst, size - 8, last)
        return dst
    # Overlapping and dst > src: copy downward, still eight bytes at a time.
    first: i64 = load_i64(src, 0)
    back: i64 = size
    while back > 8:
        back = back - 8
        store_i64(dst, back, load_i64(src, back))
    store_i64(dst, 0, first)
    return dst


@c_abi_export("memset")
def pcc_memset(dst, value: i64, size: i64) -> c_ptr:
    # Fill eight bytes per store.  A byte-at-a-time fill is what a naive
    # port looks like and it is what the whole runtime pays for: `memset` plus
    # the allocator's zeroing loop were ~23% of all samples in a
    # `pcc1 -> pcc2` emit worker.  As in memcpy, the word stores need not be
    # aligned (AArch64 and x86-64 user memory take unaligned words), so
    # nothing is stored a byte at a time from 4 bytes up: 16 bytes or more
    # are word pairs from the start and one pair ending at the size (it may
    # overlap the last loop pair), 8 to 15 a first and a last word, 4 to 7 a
    # first and a last half.  Every object allocation zeroes its cell here;
    # the aligned head and byte tail this replaces cost 56 instructions for
    # the handler's typical 48 to 80 bytes.  Constants stay inline:
    # module-level ints are zeroed in stripped freestanding objects, and
    # helper functions are not permitted here (every function must be a
    # c_abi_export).  A zero fill needs no byte pattern.
    byte: i64 = value & 255
    word: i64 = 0
    if byte != 0:
        word = wrapping_mul_i64(byte, 72340172838076673)
    if size >= 16:
        cursor = dst
        last_pair = ptr_add(dst, size - 16)
        while ptr_diff(cursor, last_pair) < 0:
            store_i64(cursor, 0, word)
            store_i64(cursor, 8, word)
            cursor = ptr_add(cursor, 16)
        store_i64(last_pair, 0, word)
        store_i64(last_pair, 8, word)
        return dst
    if size >= 8:
        store_i64(dst, 0, word)
        store_i64(dst, size - 8, word)
        return dst
    if size >= 4:
        store_i32(dst, 0, word)
        store_i32(dst, size - 4, word)
        return dst
    index: i64 = 0
    while index < size:
        store_i8(dst, index, byte)
        index = index + 1
    return dst


@c_abi_export("bzero")
def pcc_bzero(dst, size: i64) -> None:
    pcc_memset(dst, 0, size)


@c_abi_export("explicit_bzero")
def pcc_explicit_bzero(dst, size: i64) -> None:
    # The freestanding compilation contract does not run an optimizer that
    # may erase this loop. Keep it separate from memset so the secure-clear
    # symbol remains an explicit byte-store body in both backends.
    i: i64 = 0
    while i < size:
        store_i8(dst, i, 0)
        i = i + 1


@c_abi_export("memcmp")
def pcc_memcmp(lhs, rhs, size: i64) -> i64:
    i: i64 = 0
    while i < size:
        a: i64 = load_i8(lhs, i) & 255
        b: i64 = load_i8(rhs, i) & 255
        if a != b:
            return a - b
        i = i + 1
    return 0


@c_abi_export("memchr")
def pcc_memchr(ptr, value: i64, size: i64) -> c_ptr:
    # Eight bytes per test while a whole word remains: a word holds the
    # target when (x - 0x01..) & ~x & 0x80.. is nonzero for x = word ^
    # target-pattern (subtraction wraps), and the byte loop then finds the
    # first one. `in` on a str ran five instructions a byte here.
    target: i64 = value & 255
    i: i64 = 0
    if size >= 8:
        pattern: i64 = wrapping_mul_i64(target, 72340172838076673)
        limit: i64 = size - 8
        searching: i64 = 1
        while searching != 0 and i <= limit:
            x: i64 = load_i64(ptr, i) ^ pattern
            if ((x - 72340172838076673) & (x ^ -1) & -9187201950435737472) != 0:
                searching = 0
            else:
                i = i + 8
    while i < size:
        if (load_i8(ptr, i) & 255) == target:
            return ptr_add(ptr, i)
        i = i + 1
    return null()


@c_abi_export("memrchr")
def pcc_memrchr(ptr, value: i64, size: i64) -> c_ptr:
    target: i64 = value & 255
    i: i64 = size
    while i > 0:
        i = i - 1
        if (load_i8(ptr, i) & 255) == target:
            return ptr_add(ptr, i)
    return null()


@c_abi_export("strlen")
def pcc_strlen(ptr) -> i64:
    # A byte loop: the C strings measured here (module and attribute names)
    # are short, and an aligned word scan with its byte head measured 25%
    # more instructions per call on the handler (arm h23-swar).
    size: i64 = 0
    while load_i8(ptr, size) != 0:
        size = size + 1
    return size


@c_abi_export("strnlen")
def pcc_strnlen(ptr, limit: i64) -> i64:
    size: i64 = 0
    while size < limit and load_i8(ptr, size) != 0:
        size = size + 1
    return size


@c_abi_export("strcpy")
def pcc_strcpy(dst, src) -> c_ptr:
    index: i64 = 0
    while True:
        byte: i64 = load_i8(src, index) & 255
        store_i8(dst, index, byte)
        if byte == 0:
            return dst
        index = index + 1


@c_abi_typed_export("strncpy", "ptr", ("ptr", "ptr", "i64"))
def pcc_strncpy(dst, src, size: i64) -> c_ptr:
    # Copy at most size source bytes. Once NUL is reached, pad the remaining
    # destination without reading past the source. A full copy adds no NUL.
    index: i64 = 0
    while index < size:
        byte: i64 = load_i8(src, index) & 255
        if byte == 0:
            break
        store_i8(dst, index, byte)
        index = index + 1
    while index < size:
        store_i8(dst, index, 0)
        index = index + 1
    return dst


@c_abi_export("strcat")
def pcc_strcat(dst, src) -> c_ptr:
    length = pcc_strlen(dst)
    pcc_strcpy(ptr_add(dst, length), src)
    return dst


@c_abi_export("strchrnul")
def pcc_strchrnul(ptr, value: i64) -> c_ptr:
    target: i64 = value & 255
    i: i64 = 0
    while True:
        byte: i64 = load_i8(ptr, i) & 255
        if byte == target or byte == 0:
            return ptr_add(ptr, i)
        i = i + 1


@c_abi_export("strchr")
def pcc_strchr(ptr, value: i64) -> c_ptr:
    found = pcc_strchrnul(ptr, value)
    target: i64 = value & 255
    if (load_i8(found, 0) & 255) == target:
        return found
    return null()


@c_abi_export("strrchr")
def pcc_strrchr(ptr, value: i64) -> c_ptr:
    target: i64 = value & 255
    last = null()
    i: i64 = 0
    while True:
        byte: i64 = load_i8(ptr, i) & 255
        if byte == target:
            last = ptr_add(ptr, i)
        if byte == 0:
            return last
        i = i + 1


@c_abi_export("strcmp")
def pcc_strcmp(lhs, rhs) -> i64:
    i: i64 = 0
    while True:
        a: i64 = load_i8(lhs, i) & 255
        b: i64 = load_i8(rhs, i) & 255
        if a != b:
            return a - b
        if a == 0:
            return 0
        i = i + 1


@c_abi_export("strncmp")
def pcc_strncmp(lhs, rhs, size: i64) -> i64:
    i: i64 = 0
    while i < size:
        a: i64 = load_i8(lhs, i) & 255
        b: i64 = load_i8(rhs, i) & 255
        if a != b:
            return a - b
        if a == 0:
            return 0
        i = i + 1
    return 0
