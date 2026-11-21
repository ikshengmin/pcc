"""Owned SHA-256 and MD5 compression, incremental state and SHA file hashing.

Private hashlib snapshots are immutable GC-owned bytes. SHA-256 uses 144
bytes; MD5 uses 112: state words, count, partial length and 64 block bytes.
All compression uses compiler-owned memory and machine-bit intrinsics.
"""

__pcc_runtime_port__ = True

from pcc.extern import c_abi_export, c_int64, c_ptr, extern
from pcc.unsafe import (
    cstr, define_global_i64_array, global_addr, load_i8, load_i32, load_i64,
    logical_shift_left_i64, logical_shift_right_i64, memcpy, memset,
    null, open_readonly, ptr_add, ptr_is_null, ptr_to_int, seek_file,
    stack_alloc, store_i8, store_i64, uname,
)

py_str_utf8 = extern("py_str_utf8", (c_ptr,), c_ptr)
py_str_new = extern("py_str_new", (c_ptr, c_int64), c_ptr)
py_bytes_len = extern("py_bytes_len", (c_ptr,), c_int64)
py_bytes_new = extern("py_bytes_new", (c_ptr, c_int64), c_ptr)
py_bytes_data_ptr = extern("py_bytes_data_ptr", (c_ptr,), c_ptr)
pcc_platform_read = extern("pcc_platform_read", (c_int64, c_ptr, c_int64), c_int64)
pcc_platform_close = extern("pcc_platform_close", (c_int64,), c_int64)


define_global_i64_array(
    "pcc_sha256_round_constants",
    0x428A2F98, 0x71374491, 0xB5C0FBCF, 0xE9B5DBA5,
    0x3956C25B, 0x59F111F1, 0x923F82A4, 0xAB1C5ED5,
    0xD807AA98, 0x12835B01, 0x243185BE, 0x550C7DC3,
    0x72BE5D74, 0x80DEB1FE, 0x9BDC06A7, 0xC19BF174,
    0xE49B69C1, 0xEFBE4786, 0x0FC19DC6, 0x240CA1CC,
    0x2DE92C6F, 0x4A7484AA, 0x5CB0A9DC, 0x76F988DA,
    0x983E5152, 0xA831C66D, 0xB00327C8, 0xBF597FC7,
    0xC6E00BF3, 0xD5A79147, 0x06CA6351, 0x14292967,
    0x27B70A85, 0x2E1B2138, 0x4D2C6DFC, 0x53380D13,
    0x650A7354, 0x766A0ABB, 0x81C2C92E, 0x92722C85,
    0xA2BFE8A1, 0xA81A664B, 0xC24B8B70, 0xC76C51A3,
    0xD192E819, 0xD6990624, 0xF40E3585, 0x106AA070,
    0x19A4C116, 0x1E376C08, 0x2748774C, 0x34B0BCB5,
    0x391C0CB3, 0x4ED8AA4A, 0x5B9CCA4F, 0x682E6FF3,
    0x748F82EE, 0x78A5636F, 0x84C87814, 0x8CC70208,
    0x90BEFFFA, 0xA4506CEB, 0xBEF9A3F7, 0xC67178F2,
)


def _rotr32(value: int, shift: int) -> int:
    value = value & 0xFFFFFFFF
    # SHA's fixed rotate counts are in 1..31. This is a machine bit shift;
    # Python multiplication/shift would box intermediates in every round.
    high: int = logical_shift_left_i64(value, 32 - shift) & 0xFFFFFFFF
    return (logical_shift_right_i64(value, shift) | high) & 0xFFFFFFFF


def _sha256_transform(context, block) -> None:
    words = stack_alloc(512)
    index: int = 0
    while index < 16:
        offset: int = index * 4
        word: int = (
            ((load_i8(block, offset) & 255) * 0x1000000)
            | ((load_i8(block, offset + 1) & 255) * 0x10000)
            | ((load_i8(block, offset + 2) & 255) * 0x100)
            | (load_i8(block, offset + 3) & 255)
        )
        store_i64(words, index * 8, word)
        index = index + 1
    while index < 64:
        prior15: int = load_i64(words, (index - 15) * 8) & 0xFFFFFFFF
        prior2: int = load_i64(words, (index - 2) * 8) & 0xFFFFFFFF
        sigma0: int = (
            _rotr32(prior15, 7)
            ^ _rotr32(prior15, 18)
            ^ logical_shift_right_i64(prior15, 3)
        )
        sigma1: int = (
            _rotr32(prior2, 17)
            ^ _rotr32(prior2, 19)
            ^ logical_shift_right_i64(prior2, 10)
        )
        word = (
            load_i64(words, (index - 16) * 8)
            + sigma0
            + load_i64(words, (index - 7) * 8)
            + sigma1
        ) & 0xFFFFFFFF
        store_i64(words, index * 8, word)
        index = index + 1

    a: int = load_i64(context, 0) & 0xFFFFFFFF
    b: int = load_i64(context, 8) & 0xFFFFFFFF
    c: int = load_i64(context, 16) & 0xFFFFFFFF
    d: int = load_i64(context, 24) & 0xFFFFFFFF
    e: int = load_i64(context, 32) & 0xFFFFFFFF
    f: int = load_i64(context, 40) & 0xFFFFFFFF
    g: int = load_i64(context, 48) & 0xFFFFFFFF
    h: int = load_i64(context, 56) & 0xFFFFFFFF
    constants = global_addr("pcc_sha256_round_constants")
    index = 0
    while index < 64:
        sigma1 = _rotr32(e, 6) ^ _rotr32(e, 11) ^ _rotr32(e, 25)
        choice: int = (e & f) ^ ((~e) & g)
        temp1: int = (
            h
            + sigma1
            + choice
            + load_i64(constants, index * 8)
            + load_i64(words, index * 8)
        ) & 0xFFFFFFFF
        sigma0 = _rotr32(a, 2) ^ _rotr32(a, 13) ^ _rotr32(a, 22)
        majority: int = (a & b) ^ (a & c) ^ (b & c)
        temp2: int = (sigma0 + majority) & 0xFFFFFFFF
        h = g
        g = f
        f = e
        e = (d + temp1) & 0xFFFFFFFF
        d = c
        c = b
        b = a
        a = (temp1 + temp2) & 0xFFFFFFFF
        index = index + 1
    store_i64(context, 0, (load_i64(context, 0) + a) & 0xFFFFFFFF)
    store_i64(context, 8, (load_i64(context, 8) + b) & 0xFFFFFFFF)
    store_i64(context, 16, (load_i64(context, 16) + c) & 0xFFFFFFFF)
    store_i64(context, 24, (load_i64(context, 24) + d) & 0xFFFFFFFF)
    store_i64(context, 32, (load_i64(context, 32) + e) & 0xFFFFFFFF)
    store_i64(context, 40, (load_i64(context, 40) + f) & 0xFFFFFFFF)
    store_i64(context, 48, (load_i64(context, 48) + g) & 0xFFFFFFFF)
    store_i64(context, 56, (load_i64(context, 56) + h) & 0xFFFFFFFF)


def _sha256_init(context) -> None:
    # Context snapshots include the partial block; never publish stack bytes.
    memset(context, 0, 144)
    store_i64(context, 0, 0x6A09E667)
    store_i64(context, 8, 0xBB67AE85)
    store_i64(context, 16, 0x3C6EF372)
    store_i64(context, 24, 0xA54FF53A)
    store_i64(context, 32, 0x510E527F)
    store_i64(context, 40, 0x9B05688C)
    store_i64(context, 48, 0x1F83D9AB)
    store_i64(context, 56, 0x5BE0CD19)
    store_i64(context, 64, 0)
    store_i64(context, 72, 0)


def _sha256_update(context, data, length: int) -> None:
    store_i64(context, 64, load_i64(context, 64) + length * 8)
    source_offset: int = 0
    block_length: int = load_i64(context, 72)
    if block_length > 0:
        # Complete the staged partial block first.
        take: int = 64 - block_length
        if take > length:
            take = length
        memcpy(ptr_add(context, 80 + block_length), data, take)
        block_length = block_length + take
        source_offset = take
        if block_length == 64:
            _sha256_transform(context, ptr_add(context, 80))
            block_length = 0
    if block_length == 0:
        # Whole blocks compress straight from the input: the transform does
        # not allocate, so the caller's buffer stays put.
        while length - source_offset >= 64:
            _sha256_transform(context, ptr_add(data, source_offset))
            source_offset = source_offset + 64
        remaining: int = length - source_offset
        if remaining > 0:
            memcpy(ptr_add(context, 80), ptr_add(data, source_offset), remaining)
            block_length = remaining
    store_i64(context, 72, block_length)


def _sha256_final(context, digest) -> None:
    block_length: int = load_i64(context, 72)
    store_i8(context, 80 + block_length, 0x80)
    block_length = block_length + 1
    if block_length > 56:
        while block_length < 64:
            store_i8(context, 80 + block_length, 0)
            block_length = block_length + 1
        _sha256_transform(context, ptr_add(context, 80))
        block_length = 0
    while block_length < 56:
        store_i8(context, 80 + block_length, 0)
        block_length = block_length + 1
    bit_count: int = load_i64(context, 64)
    index: int = 0
    while index < 8:
        store_i8(
            context,
            80 + 63 - index,
            logical_shift_right_i64(bit_count, index * 8) & 255,
        )
        index = index + 1
    _sha256_transform(context, ptr_add(context, 80))
    index = 0
    while index < 8:
        value: int = load_i64(context, index * 8)
        store_i8(digest, index * 4, logical_shift_right_i64(value, 24) & 255)
        store_i8(digest, index * 4 + 1, logical_shift_right_i64(value, 16) & 255)
        store_i8(digest, index * 4 + 2, logical_shift_right_i64(value, 8) & 255)
        store_i8(digest, index * 4 + 3, value & 255)
        index = index + 1


def _sha256_file_hex_bounded(path_object, max_bytes: int):
    if max_bytes <= 0:
        return py_str_new(cstr(""), 0)
    path = py_str_utf8(path_object)
    fd: int = open_readonly(path)
    if fd < 0:
        return py_str_new(cstr(""), 0)
    context = stack_alloc(144)
    buffer = stack_alloc(32768)
    _sha256_init(context)
    failed: int = 0
    total: int = 0
    while True:
        remaining: int = max_bytes - total
        read_limit: int = 32768
        if remaining < read_limit:
            # One sentinel byte distinguishes an exact-bound artifact from an
            # oversized file without hashing or reading an unbounded suffix.
            read_limit = remaining + 1
        count: int = pcc_platform_read(fd, buffer, read_limit)
        if count < 0:
            if count == -4:
                continue
            failed = 1
            break
        if count == 0:
            break
        if count > remaining:
            failed = 1
            break
        total = total + count
        _sha256_update(context, buffer, count)
    pcc_platform_close(fd)
    if failed != 0:
        return py_str_new(cstr(""), 0)
    digest = stack_alloc(32)
    output = stack_alloc(65)
    _sha256_final(context, digest)
    index: int = 0
    while index < 32:
        value: int = load_i8(digest, index) & 255
        high: int = logical_shift_right_i64(value, 4) & 15
        low: int = value & 15
        if high < 10:
            store_i8(output, index * 2, 48 + high)
        else:
            store_i8(output, index * 2, 87 + high)
        if low < 10:
            store_i8(output, index * 2 + 1, 48 + low)
        else:
            store_i8(output, index * 2 + 1, 87 + low)
        index = index + 1
    store_i8(output, 64, 0)
    return py_str_new(output, 64)


@c_abi_export("py_sha256_bytes_digest")
def py_sha256_bytes_digest(data_object):
    """SHA-256 of a bytes-like object, as a 32-byte ``bytes``.

    The file entry points already drive this native transform; code signing
    needs the in-memory form.  Without it `macho_codesign` fell through to the
    pure-Python `pcc.stdlib.hashlib`, whose per-round boxed-integer
    arithmetic turned one 6.6 MiB image signature into ~28 minutes and 5 GiB
    of RSS under self-host.
    """
    if ptr_is_null(data_object):
        return null()
    length: int = py_bytes_len(data_object)
    payload = py_bytes_data_ptr(data_object)
    if ptr_is_null(payload) and length > 0:
        return null()
    context = stack_alloc(160)
    _sha256_init(context)
    if length > 0:
        _sha256_update(context, payload, length)
    digest = stack_alloc(32)
    _sha256_final(context, digest)
    return py_bytes_new(digest, 32)


@c_abi_export("py_sha256_state_new")
def py_sha256_state_new():
    """Return an immutable GC-owned snapshot of the native SHA-256 context."""
    context = stack_alloc(144)
    _sha256_init(context)
    return py_bytes_new(context, 144)


@c_abi_export("py_sha256_state_update")
def py_sha256_state_update(state_object, data_object):
    # Private ABI: hashlib supplies a 144-byte state and normalized bytes.
    # Read borrowed payloads before allocating the returned snapshot. The
    # transform and its machine operations do not allocate Python objects.
    context = stack_alloc(144)
    memcpy(context, py_bytes_data_ptr(state_object), 144)
    _sha256_update(context, py_bytes_data_ptr(data_object), py_bytes_len(data_object))
    return py_bytes_new(context, 144)


@c_abi_export("py_sha256_state_digest")
def py_sha256_state_digest(state_object):
    # Finalization mutates only a stack copy: digest() is repeatable and the
    # original hash object can still be updated or copied independently.
    context = stack_alloc(144)
    memcpy(context, py_bytes_data_ptr(state_object), 144)
    digest = stack_alloc(32)
    _sha256_final(context, digest)
    return py_bytes_new(digest, 32)


@c_abi_export("py_sha256_file_hex")
def py_sha256_file_hex(path_object):
    return _sha256_file_hex_bounded(path_object, 0x7FFFFFFFFFFFFFFF)


@c_abi_export("py_sha256_file_hex_bounded")
def py_sha256_file_hex_bounded(path_object, max_bytes: int):
    return _sha256_file_hex_bounded(path_object, max_bytes)




define_global_i64_array(
    "pcc_md5_round_constants",
    0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee,
    0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
    0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be,
    0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
    0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d,
    0x02441453, 0xd8a1e681, 0xe7d3fbc8, 0x21e1cde6,
    0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905,
    0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a, 0xfffa3942,
    0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44,
    0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70, 0x289b7ec6,
    0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039,
    0xe6db99e5, 0x1fa27cf8, 0xc4ac5665, 0xf4292244,
    0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3,
    0x8f0ccc92, 0xffeff47d, 0x85845dd1, 0x6fa87e4f,
    0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82,
    0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
)


def _rotl32(value: int, shift: int) -> int:
    value = value & 0xFFFFFFFF
    # MD5's rotate counts are in 4..23; use machine shifts as in SHA-256.
    low: int = logical_shift_right_i64(value, 32 - shift) & 0xFFFFFFFF
    return (logical_shift_left_i64(value, shift) | low) & 0xFFFFFFFF


define_global_i64_array(
    "pcc_md5_rotate_counts",
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
    5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
    4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
    6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
)

_MD5_CONTEXT_BYTES = 112


def _md5_transform(context, block) -> None:
    """RFC 1321 compression over one little-endian 64-byte block.

    Context: four i64 state words, byte count at 32, buffered length at 40,
    and 64 block bytes at 48. Compression uses unboxed machine operations.
    """
    words = stack_alloc(128)
    constants = global_addr("pcc_md5_round_constants")
    shifts = global_addr("pcc_md5_rotate_counts")
    index: int = 0
    while index < 16:
        offset: int = index * 4
        word: int = (
            (load_i8(block, offset) & 255)
            | ((load_i8(block, offset + 1) & 255) * 0x100)
            | ((load_i8(block, offset + 2) & 255) * 0x10000)
            | ((load_i8(block, offset + 3) & 255) * 0x1000000)
        )
        store_i64(words, index * 8, word)
        index = index + 1
    a: int = load_i64(context, 0) & 0xFFFFFFFF
    b: int = load_i64(context, 8) & 0xFFFFFFFF
    c: int = load_i64(context, 16) & 0xFFFFFFFF
    d: int = load_i64(context, 24) & 0xFFFFFFFF
    index = 0
    while index < 64:
        if index < 16:
            mixed: int = (b & c) | ((~b) & d)
            word_index: int = index
        elif index < 32:
            mixed = (d & b) | ((~d) & c)
            word_index = (5 * index + 1) & 15
        elif index < 48:
            mixed = b ^ c ^ d
            word_index = (3 * index + 5) & 15
        else:
            mixed = c ^ (b | (~d))
            word_index = (7 * index) & 15
        value: int = (
            a
            + mixed
            + load_i64(constants, index * 8)
            + load_i64(words, word_index * 8)
        ) & 0xFFFFFFFF
        rotated: int = _rotl32(value, load_i64(shifts, index * 8))
        prior_b: int = b
        b = (prior_b + rotated) & 0xFFFFFFFF
        a = d
        d = c
        c = prior_b
        index = index + 1
    store_i64(context, 0, (load_i64(context, 0) + a) & 0xFFFFFFFF)
    store_i64(context, 8, (load_i64(context, 8) + b) & 0xFFFFFFFF)
    store_i64(context, 16, (load_i64(context, 16) + c) & 0xFFFFFFFF)
    store_i64(context, 24, (load_i64(context, 24) + d) & 0xFFFFFFFF)


def _md5_init(context) -> None:
    memset(context, 0, _MD5_CONTEXT_BYTES)
    store_i64(context, 0, 0x67452301)
    store_i64(context, 8, 0xEFCDAB89)
    store_i64(context, 16, 0x98BADCFE)
    store_i64(context, 24, 0x10325476)


def _md5_update(context, data, length: int) -> None:
    store_i64(context, 32, load_i64(context, 32) + length)
    source_offset: int = 0
    while source_offset < length:
        block_length: int = load_i64(context, 40)
        room: int = 64 - block_length
        take: int = length - source_offset
        if take > room:
            take = room
        index: int = 0
        while index < take:
            store_i8(
                context,
                48 + block_length + index,
                load_i8(data, source_offset + index),
            )
            index = index + 1
        block_length = block_length + take
        store_i64(context, 40, block_length)
        source_offset = source_offset + take
        if block_length == 64:
            _md5_transform(context, ptr_add(context, 48))
            store_i64(context, 40, 0)


def _md5_final(context, digest) -> None:
    block_length: int = load_i64(context, 40)
    store_i8(context, 48 + block_length, 0x80)
    block_length = block_length + 1
    if block_length > 56:
        while block_length < 64:
            store_i8(context, 48 + block_length, 0)
            block_length = block_length + 1
        _md5_transform(context, ptr_add(context, 48))
        block_length = 0
    while block_length < 56:
        store_i8(context, 48 + block_length, 0)
        block_length = block_length + 1
    bit_count: int = logical_shift_left_i64(load_i64(context, 32), 3)
    index: int = 0
    while index < 8:
        store_i8(
            context,
            48 + 56 + index,
            logical_shift_right_i64(bit_count, index * 8) & 255,
        )
        index = index + 1
    _md5_transform(context, ptr_add(context, 48))
    index = 0
    while index < 4:
        value: int = load_i64(context, index * 8)
        store_i8(digest, index * 4, value & 255)
        store_i8(digest, index * 4 + 1, logical_shift_right_i64(value, 8) & 255)
        store_i8(digest, index * 4 + 2, logical_shift_right_i64(value, 16) & 255)
        store_i8(digest, index * 4 + 3, logical_shift_right_i64(value, 24) & 255)
        index = index + 1


@c_abi_export("py_md5_bytes_digest")
def py_md5_bytes_digest(data_object):
    """MD5 of a bytes-like object, as a 16-byte ``bytes``."""
    if ptr_is_null(data_object):
        return null()
    context = stack_alloc(_MD5_CONTEXT_BYTES)
    _md5_init(context)
    length: int = py_bytes_len(data_object)
    if length > 0:
        _md5_update(context, py_bytes_data_ptr(data_object), length)
    digest = stack_alloc(16)
    _md5_final(context, digest)
    return py_bytes_new(digest, 16)


@c_abi_export("py_md5_state_new")
def py_md5_state_new():
    """Return an immutable GC-owned snapshot of the native MD5 context."""
    context = stack_alloc(_MD5_CONTEXT_BYTES)
    _md5_init(context)
    return py_bytes_new(context, _MD5_CONTEXT_BYTES)


@c_abi_export("py_md5_state_update")
def py_md5_state_update(state_object, data_object):
    context = stack_alloc(_MD5_CONTEXT_BYTES)
    memcpy(context, py_bytes_data_ptr(state_object), _MD5_CONTEXT_BYTES)
    _md5_update(context, py_bytes_data_ptr(data_object), py_bytes_len(data_object))
    return py_bytes_new(context, _MD5_CONTEXT_BYTES)


@c_abi_export("py_md5_state_digest")
def py_md5_state_digest(state_object):
    context = stack_alloc(_MD5_CONTEXT_BYTES)
    memcpy(context, py_bytes_data_ptr(state_object), _MD5_CONTEXT_BYTES)
    digest = stack_alloc(16)
    _md5_final(context, digest)
    return py_bytes_new(digest, 16)


# Experimental application seal: unkeyed integrity correlation with an
# independently issued private copy, NOT executable authentication. Scope is
# deliberately the owned x86-64 Linux static ET_EXEC, before warmup/carriers.
def _exp2_seal_hex(byte: int) -> int:
    if 48 <= byte and byte <= 57:
        return byte - 48
    if 97 <= byte and byte <= 102:
        return byte - 87
    return -1


def _exp2_seal_expected(marker, digest) -> int:
    # Prefix=31, 64 lowercase hex bytes, suffix ":END", then NUL.
    i: int = 0
    nonzero: int = 0
    while i < 32:
        high: int = _exp2_seal_hex(load_i8(marker, 31 + i * 2) & 255)
        low: int = _exp2_seal_hex(load_i8(marker, 32 + i * 2) & 255)
        if high < 0 or low < 0:
            return 0
        value: int = high * 16 + low
        store_i8(digest, i, value)
        nonzero = nonzero | value
        i += 1
    return 1 if nonzero != 0 else 0


def _exp2_seal_read(fd: int, buffer, count: int) -> int:
    attempts: int = 0
    while attempts < 8:
        n: int = pcc_platform_read(fd, buffer, count)
        if n != -4:  # Linux EINTR
            return n
        attempts += 1
    return -1


def _exp2_seal_read_exact(fd: int, buffer, count: int) -> int:
    done: int = 0
    while done < count:
        n: int = _exp2_seal_read(fd, ptr_add(buffer, done), count - done)
        if n <= 0 or n > count - done:
            return 0
        done += n
    return 1


def _exp2_seal_marker_offset(fd: int, size: int, address: int) -> int:
    ph = stack_alloc(64)
    if _exp2_seal_read_exact(fd, ph, 64) != 1:
        return -1
    if (load_i32(ph, 0) != 0x464c457f or load_i8(ph, 4) != 2
            or load_i8(ph, 5) != 1 or load_i8(ph, 6) != 1
            or load_i32(ph, 16) != 0x003e0002  # ET_EXEC, EM_X86_64
            or load_i32(ph, 20) != 1 or load_i32(ph, 52) != 0x00380040):
        return -1
    phoff: int = load_i64(ph, 32)
    phnum: int = (load_i8(ph, 56) & 255) | ((load_i8(ph, 57) & 255) * 256)
    entry: int = load_i64(ph, 24)
    if (phnum < 1 or phnum > 64 or phoff < 64 or phoff > size - phnum * 56
            or entry <= 0 or address <= 0 or address > 140737488355228
            or seek_file(fd, phoff, 0) != phoff):
        return -1
    out: int = -1
    exec_entry: int = 0
    i: int = 0
    while i < phnum:
        if _exp2_seal_read_exact(fd, ph, 56) != 1:
            return -1
        kind: int = load_i32(ph, 0)
        flags: int = load_i32(ph, 4)
        # No interpreter, dynamic section, or writable+execute segment, even
        # a GNU stack header. Stage1's static owned image uses RX and RW LOADs.
        if kind == 2 or kind == 3 or (flags & 3) == 3:
            return -1
        if kind == 1:  # PT_LOAD
            offset: int = load_i64(ph, 8)
            va: int = load_i64(ph, 16)
            file_bytes: int = load_i64(ph, 32)
            mem_bytes: int = load_i64(ph, 40)
            if (offset < 0 or offset > size or file_bytes < 0 or file_bytes > size - offset
                    or va <= 0 or va >= 140737488355328 or mem_bytes < file_bytes
                    or mem_bytes > 140737488355328 - va or (va & 4095) != (offset & 4095)):
                return -1
            if (flags & 1) != 0 and entry >= va and entry - va < file_bytes:
                exec_entry = 1
            # Reject even another partial mapping of a marker page. Page
            # sharing with an overlapping writable LOAD cannot count as RO.
            if (mem_bytes > 0 and (va & -4096) <= ((address + 99) & -4096)
                    and ((va + mem_bytes - 1) & -4096) >= (address & -4096)):
                delta: int = address - va
                if (out >= 0 or (flags & 6) != 4 or delta < 0 or delta > file_bytes - 100):
                    return -1
                out = offset + delta
        i += 1
    return out if exec_entry == 1 else -1


def _exp2_seal_digest(fd: int, size: int, marked_at: int, marker, expected) -> int:
    context = stack_alloc(144)
    buffer = stack_alloc(32768)
    _sha256_init(context)
    pos: int = 0
    slot: int = marked_at + 31
    while pos < size:
        ask: int = 32768
        if size - pos < ask:
            ask = size - pos
        count: int = _exp2_seal_read(fd, buffer, ask)
        if count <= 0 or count > ask:
            return 0
        if pos < marked_at + 100 and pos + count > marked_at:
            # Compare the saved literal with the same descriptor bytes we
            # hash, BEFORE normalization (including suffix and NUL).
            first: int = marked_at - pos
            if first < 0:
                first = 0
            end: int = marked_at + 100 - pos
            if end > count:
                end = count
            i: int = first
            while i < end:
                if load_i8(buffer, i) != load_i8(marker, pos + i - marked_at):
                    return 0
                i += 1
            zfirst: int = slot - pos
            if zfirst < 0:
                zfirst = 0
            zend: int = slot + 64 - pos
            if zend > count:
                zend = count
            if zfirst < zend:
                memset(ptr_add(buffer, zfirst), 48, zend - zfirst)
        _sha256_update(context, buffer, count)
        pos += count
    if _exp2_seal_read(fd, buffer, 1) != 0:
        return 0
    digest = stack_alloc(32)
    _sha256_final(context, digest)
    differences: int = 0
    index: int = 0
    while index < 32:
        differences = differences | (load_i8(digest, index) ^ load_i8(expected, index))
        index += 1
    return 1 if differences == 0 else 0


def _exp2_seal_check_fd(fd: int, marker, expected) -> int:
    size: int = seek_file(fd, 0, 2)  # SEEK_END, one bounded descriptor
    if size < 64 or size > 67108864 or seek_file(fd, 0, 0) != 0:
        return 0
    offset: int = _exp2_seal_marker_offset(fd, size, ptr_to_int(marker))
    if offset < 0 or seek_file(fd, 0, 0) != 0:
        return 0
    return _exp2_seal_digest(fd, size, offset, marker, expected)


@c_abi_export("pcc_exp2_own_image_matches_seal")
def pcc_exp2_own_image_matches_seal() -> int:
    """Check exactly one compiled marker; no disk access for an unsealed image."""
    marker = cstr("PCC_EXP2_DIAGNOSTIC_HANDLER_V1:0000000000000000000000000000000000000000000000000000000000000000:END")
    expected = stack_alloc(32)
    if _exp2_seal_expected(marker, expected) == 0:
        return 0
    system = stack_alloc(1536)
    if (uname(system) != 0 or load_i32(system, 0) != 0x756e694c
            or load_i8(system, 4) != 120 or load_i8(system, 5) != 0):  # "Linux\0"
        return 0
    fd: int = open_readonly(cstr("/proc/self/exe"))
    if fd < 0:
        return 0
    accepted: int = _exp2_seal_check_fd(fd, marker, expected)
    if pcc_platform_close(fd) != 0:
        return 0
    return accepted
