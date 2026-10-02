"""Owned Windows directory streams over FindFirstFileW/FindNextFileW."""

from pcc import i64
from pcc.extern import c_abi_export, c_ptr, c_int, c_int64, extern
from pcc.unsafe import (
    malloc, free, null, ptr_is_null, ptr_to_int, ptr_add, load_i8, load_i64,
    load_ptr, store_i8, store_i64, store_ptr,
)

__pcc_freestanding__ = True

FindFirstFileW = extern("FindFirstFileW", (c_ptr, c_ptr), c_ptr)
FindNextFileW = extern("FindNextFileW", (c_ptr, c_ptr), c_int)
FindClose = extern("FindClose", (c_ptr,), c_int)
GetLastError = extern("GetLastError", (), c_int)
GetFileAttributesW = extern("GetFileAttributesW", (c_ptr,), c_int)
utf16 = extern("pcc_win_utf16", (c_ptr,), c_ptr)
utf8 = extern("pcc_win_utf8", (c_ptr,), c_ptr)
error = extern("pcc_win_error", (), c_int64)


@c_abi_export("pcc_win_directory_open")
def directory_open(path: c_ptr) -> c_ptr:
    check = utf16(path)
    if ptr_is_null(check):
        return null()
    attributes: i64 = GetFileAttributesW(check)
    free(check)
    if attributes == -1 or not (attributes & 16):
        return null()
    length: i64 = 0
    while load_i8(path, length) != 0:
        length = length + 1
    pattern = malloc(length + 3)
    stream = malloc(624)
    if ptr_is_null(pattern) or ptr_is_null(stream):
        free(pattern)
        free(stream)
        return null()
    index: i64 = 0
    while index < length:
        store_i8(pattern, index, load_i8(path, index))
        index = index + 1
    if length and load_i8(pattern, length - 1) != 47 and load_i8(pattern, length - 1) != 92:
        store_i8(pattern, length, 92)
        length = length + 1
    store_i8(pattern, length, 42)
    store_i8(pattern, length + 1, 0)
    wide = utf16(pattern)
    free(pattern)
    if ptr_is_null(wide):
        free(stream)
        return null()
    handle = FindFirstFileW(wide, ptr_add(stream, 32))
    failure: i64 = GetLastError()
    free(wide)
    empty: i64 = 0
    if ptr_to_int(handle) == -1:
        if failure != 2:
            free(stream)
            return null()
        empty = 1
    store_ptr(stream, 0, handle)
    store_i64(stream, 8, 2 if empty else 1)
    store_ptr(stream, 16, null())
    store_i64(stream, 24, 0)
    return stream


@c_abi_export("pcc_win_directory_next")
def directory_next(stream: c_ptr) -> c_ptr:
    if load_i64(stream, 8) == 2:
        return null()
    if load_i64(stream, 8) == 1:
        store_i64(stream, 8, 0)
    elif not FindNextFileW(load_ptr(stream, 0), ptr_add(stream, 32)):
        store_i64(stream, 24, 0 if GetLastError() == 18 else error())
        return null()
    previous = load_ptr(stream, 16)
    text = utf8(ptr_add(stream, 76))  # WIN32_FIND_DATAW.cFileName is byte 44.
    store_ptr(stream, 16, text)
    free(previous)
    if ptr_is_null(text):
        store_i64(stream, 24, -12)
    return text


@c_abi_export("pcc_win_directory_error")
def directory_error(stream: c_ptr) -> i64:
    return load_i64(stream, 24)


@c_abi_export("pcc_win_directory_close")
def directory_close(stream: c_ptr) -> i64:
    result: i64 = 0
    if ptr_to_int(load_ptr(stream, 0)) != -1:
        result = 0 if FindClose(load_ptr(stream, 0)) else error()
    free(load_ptr(stream, 16))
    free(stream)
    return result
