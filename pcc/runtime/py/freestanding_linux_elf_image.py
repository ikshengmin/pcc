"""Private Linux x86-64 mapper for the version-1 PCC ET_REL extension profile.

This is not dlopen or an ET_DYN loader. The caller supplies the linker's explicit
main-image table and owns each returned image exclusively: lookups borrow it,
and close requires the last owner. No process-global namespace, dependency
loader, runtime archive, managed heap or GC is introduced. The public extension
import owner must establish handle sharing/lifetime before using this API.
"""

from pcc import i64
from pcc.extern import c_abi_export, c_ptr
from pcc.unsafe import (
    calloc, close, cstr, free, int_to_ptr, load_i8, load_i32, load_i64,
    load_ptr, malloc, memcpy, null, open_readonly, ptr_add,
    ptr_is_null, ptr_to_int, read, store_i8, store_i32, store_i64, store_ptr,
    syscall6, target_platform_machine, target_sys_platform, unsigned_greater_i64,
)

__pcc_freestanding__ = True

# Raw library functions require explicit C-ABI declarations, including helpers.
# The current decorator emits external linkage, so implementation-only helpers
# use the pcc_linux_elf_image__ namespace, like freestanding_re_engine's core
# helpers. These are not additional caller-facing image operations or host-table
# exports: callers use only pcc_linux_elf_image_{open,symbol,close} below.

# Private image record. All addresses refer to raw owned storage, never to a
# second Python object heap. The file copy owns symbol/name metadata too.
_FILE = 0
_FILE_SIZE = 8
_SECTIONS = 16
_SECTION_COUNT = 24
_SYMBOLS = 32
_SYMBOL_COUNT = 40
_STRINGS = 48
_STRING_SIZE = 56
_EXPORTS = 64
_EXPORT_COUNT = 72
_MAPPING = 80
_MAPPING_SIZE = 88
_SECTION_ADDRESSES = 96
_SYMBOL_ADDRESSES = 104
_NOTE_INDEX = 112
_SYMTAB_INDEX = 120
_READY = 128
_RECORD_SIZE = 136

# Error codes are returned directly, not through PCC exception state. The
# caller must check status before borrowing executable addresses.
IMAGE_FORMAT = 1
IMAGE_UNSUPPORTED = 2
IMAGE_HOST_SYMBOL = 3
IMAGE_ALLOCATION = 4
IMAGE_SYSTEM = 5
IMAGE_RELOCATION = 6


@c_abi_export("pcc_linux_elf_image__u16")
def _u16(data: c_ptr, offset: i64) -> i64:
    return (load_i8(data, offset) & 255) | ((load_i8(data, offset + 1) & 255) << 8)


@c_abi_export("pcc_linux_elf_image__u32")
def _u32(data: c_ptr, offset: i64) -> i64:
    return load_i32(data, offset) & 4294967295


@c_abi_export("pcc_linux_elf_image__span")
def _span(offset: i64, length: i64, size: i64) -> i64:
    if offset < 0 or length < 0 or offset > size or length > size - offset:
        return 0
    return 1


@c_abi_export("pcc_linux_elf_image__same")
def _same(left: c_ptr, right: c_ptr) -> i64:
    index: i64 = 0
    while load_i8(left, index) == load_i8(right, index):
        if load_i8(left, index) == 0:
            return 1
        index = index + 1
    return 0


@c_abi_export("pcc_linux_elf_image__name_length")
def _name_length(table: c_ptr, size: i64, offset: i64, ascii_only: i64) -> i64:
    if offset < 0 or offset >= size:
        return -1
    index: i64 = offset
    while index < size:
        value: i64 = load_i8(table, index) & 255
        if value == 0:
            return index - offset
        if ascii_only and value > 127:
            return -1
        index = index + 1
    return -1


@c_abi_export("pcc_linux_elf_image__contains")
def _contains(name: c_ptr, pattern: c_ptr) -> i64:
    start: i64 = 0
    while load_i8(name, start) != 0:
        index: i64 = 0
        while load_i8(pattern, index) != 0:
            if load_i8(name, start + index) != load_i8(pattern, index):
                break
            index = index + 1
        if load_i8(pattern, index) == 0:
            return 1
        start = start + 1
    return 0


@c_abi_export("pcc_linux_elf_image__unsupported_section_name")
def _unsupported_section_name(name: c_ptr, length: i64) -> i64:
    if length >= 5:
        tail = ptr_add(name, length - 5)
        if _same(tail, cstr(".init")) or _same(tail, cstr(".fini")):
            return 1
    if (_contains(name, cstr(".init_array")) or _contains(name, cstr(".fini_array"))
            or _contains(name, cstr(".preinit_array")) or _contains(name, cstr(".ctors"))
            or _contains(name, cstr(".dtors")) or _contains(name, cstr(".pcc_stackmaps"))
            or _contains(name, cstr(".eh_frame")) or _contains(name, cstr(".gcc_except_table"))):
        return 1
    return 0


@c_abi_export("pcc_linux_elf_image__section")
def _section(image: c_ptr, index: i64) -> c_ptr:
    return ptr_add(load_ptr(image, _SECTIONS), index * 64)


@c_abi_export("pcc_linux_elf_image__symbol")
def _symbol(image: c_ptr, index: i64) -> c_ptr:
    return ptr_add(load_ptr(image, _SYMBOLS), index * 24)


@c_abi_export("pcc_linux_elf_image__host_address")
def _host_address(hosts: c_ptr, name: c_ptr) -> c_ptr:
    count: i64 = load_i64(hosts, 8)
    index: i64 = 0
    while index < count:
        row: i64 = 16 + index * 16
        if _same(name, load_ptr(hosts, row)):
            return load_ptr(hosts, row + 8)
        index = index + 1
    return null()


@c_abi_export("pcc_linux_elf_image__validate_headers")
def _validate_headers(image: c_ptr) -> i64:
    data = load_ptr(image, _FILE)
    size: i64 = load_i64(image, _FILE_SIZE)
    if size < 64 or _u32(data, 0) != 1179403647:
        return IMAGE_FORMAT
    if (load_i8(data, 4) != 2 or load_i8(data, 5) != 1 or load_i8(data, 6) != 1
            or _u16(data, 16) != 1 or _u16(data, 18) != 62 or _u32(data, 20) != 1):
        return IMAGE_UNSUPPORTED
    index: i64 = 7
    while index < 16:
        if load_i8(data, index) != 0:
            return IMAGE_UNSUPPORTED
        index = index + 1
    if (load_i64(data, 24) != 0 or load_i64(data, 32) != 0 or _u32(data, 48) != 0
            or _u16(data, 52) != 64 or _u16(data, 54) != 0 or _u16(data, 56) != 0
            or _u16(data, 58) != 64):
        return IMAGE_UNSUPPORTED
    count: i64 = _u16(data, 60)
    table_offset: i64 = load_i64(data, 40)
    if count < 5 or count >= 65280 or _u16(data, 62) != count - 1:
        return IMAGE_FORMAT
    if (table_offset < 64 or table_offset % 8 != 0
            or not _span(table_offset, count * 64, size)
            or table_offset + count * 64 != size):
        return IMAGE_FORMAT
    store_ptr(image, _SECTIONS, ptr_add(data, table_offset))
    store_i64(image, _SECTION_COUNT, count)
    index = 0
    while index < 64:
        if load_i8(load_ptr(image, _SECTIONS), index) != 0:
            return IMAGE_FORMAT
        index = index + 1
    names_header = _section(image, count - 1)
    names_offset: i64 = load_i64(names_header, 24)
    names_size: i64 = load_i64(names_header, 32)
    if (names_size < 1 or not _span(names_offset, names_size, table_offset)
            or names_offset < 64):
        return IMAGE_FORMAT
    names = ptr_add(data, names_offset)
    if load_i8(names, 0) != 0:
        return IMAGE_FORMAT
    note_index: i64 = 0
    previous_end: i64 = 64
    previous_relocation_target: i64 = 0
    index = 1
    while index < count:
        section = _section(image, index)
        kind: i64 = _u32(section, 4)
        flags: i64 = load_i64(section, 8)
        offset: i64 = load_i64(section, 24)
        length: i64 = load_i64(section, 32)
        alignment: i64 = load_i64(section, 48)
        name_offset: i64 = _u32(section, 0)
        name_length: i64 = _name_length(names, names_size, name_offset, 0)
        if (name_length <= 0 or alignment <= 0 or alignment > 4096
                or alignment & (alignment - 1) or offset % alignment != 0
                or offset < previous_end or load_i64(section, 16) != 0):
            return IMAGE_FORMAT
        if kind == 8:
            if length < 0 or offset > table_offset:
                return IMAGE_FORMAT
            previous_end = offset
        else:
            if not _span(offset, length, table_offset):
                return IMAGE_FORMAT
            previous_end = offset + length
        name = ptr_add(names, name_offset)
        if kind != 7 and _same(name, cstr(".note.pcc.extension")):
            return IMAGE_FORMAT
        if index >= count - 3:
            expected_kind: i64 = 3
            if index == count - 3:
                expected_kind = 2
            if kind != expected_kind or flags != 0:
                return IMAGE_FORMAT
            if index == count - 3:
                if (not _same(name, cstr(".symtab")) or alignment != 8
                        or load_i64(section, 56) != 24 or length < 24 or length % 24
                        or _u32(section, 40) != count - 2):
                    return IMAGE_FORMAT
                store_ptr(image, _SYMBOLS, ptr_add(data, offset))
                store_i64(image, _SYMBOL_COUNT, length // 24)
                store_i64(image, _SYMTAB_INDEX, index)
            else:
                if (alignment != 1 or _u32(section, 40) or _u32(section, 44)
                        or load_i64(section, 56) or length < 1 or load_i8(data, offset) != 0):
                    return IMAGE_FORMAT
                if index == count - 2:
                    if not _same(name, cstr(".strtab")):
                        return IMAGE_FORMAT
                    store_ptr(image, _STRINGS, ptr_add(data, offset))
                    store_i64(image, _STRING_SIZE, length)
                elif not _same(name, cstr(".shstrtab")):
                    return IMAGE_FORMAT
        elif kind == 7:
            if (note_index or flags or alignment != 4 or _u32(section, 40)
                    or _u32(section, 44) or load_i64(section, 56)
                    or not _same(name, cstr(".note.pcc.extension"))):
                return IMAGE_FORMAT
            note_index = index
        elif note_index:
            target: i64 = _u32(section, 44)
            if (kind != 4 or flags or alignment != 8 or load_i64(section, 56) != 24
                    or _u32(section, 40) != count - 3 or length == 0 or length % 24
                    or target <= previous_relocation_target or target >= note_index):
                return IMAGE_FORMAT
            if _u32(_section(image, target), 4) != 1:
                return IMAGE_UNSUPPORTED
            previous_relocation_target = target
        else:
            if (kind != 1 and kind != 8) or (flags != 2 and flags != 3 and flags != 6):
                return IMAGE_UNSUPPORTED
            if (kind == 8 and flags != 3) or _u32(section, 40) or _u32(section, 44) or load_i64(section, 56):
                return IMAGE_UNSUPPORTED
            if _unsupported_section_name(name, name_length):
                return IMAGE_UNSUPPORTED
        index = index + 1
    if note_index == 0:
        return IMAGE_FORMAT
    store_i64(image, _NOTE_INDEX, note_index)
    return 0


@c_abi_export("pcc_linux_elf_image__validate_note_and_symbols")
def _validate_note_and_symbols(image: c_ptr, hosts: c_ptr) -> i64:
    section = _section(image, load_i64(image, _NOTE_INDEX))
    note = ptr_add(load_ptr(image, _FILE), load_i64(section, 24))
    length: i64 = load_i64(section, 32)
    count: i64 = load_i64(image, _SYMBOL_COUNT)
    if length < 40 or _u32(note, 0) != 4 or _u32(note, 8) != 1346585345:
        return IMAGE_FORMAT
    if (_u32(note, 4) != length - 16 or _u32(note, 12) != 4408144
            or _u32(note, 16) != 1 or _u32(note, 20) or _u32(note, 36)
            or _u32(note, 24) != count):
        return IMAGE_FORMAT
    exports: i64 = _u32(note, 28)
    imports: i64 = _u32(note, 32)
    if exports == 0 or exports + imports > count - 1 or length != 40 + 4 * (exports + imports):
        return IMAGE_FORMAT
    store_ptr(image, _EXPORTS, ptr_add(note, 40))
    store_i64(image, _EXPORT_COUNT, exports)
    index: i64 = 0
    while index < 24:
        if load_i8(load_ptr(image, _SYMBOLS), index) != 0:
            return IMAGE_FORMAT
        index = index + 1
    strings = load_ptr(image, _STRINGS)
    string_size: i64 = load_i64(image, _STRING_SIZE)
    first_global: i64 = _u32(_section(image, load_i64(image, _SYMTAB_INDEX)), 44)
    if first_global < 1 or first_global > count:
        return IMAGE_FORMAT
    import_index: i64 = 0
    index = 1
    while index < count:
        symbol = _symbol(image, index)
        info: i64 = load_i8(symbol, 4) & 255
        binding: i64 = info >> 4
        kind: i64 = info & 15
        visibility: i64 = load_i8(symbol, 5) & 255
        section_index: i64 = _u16(symbol, 6)
        name_offset: i64 = _u32(symbol, 0)
        name_length: i64 = _name_length(strings, string_size, name_offset, binding)
        if (binding > 2 or kind > 3 or (visibility != 0 and visibility != 2) or name_length < 0
                or (binding != 0 and name_length == 0)
                or (binding == 0 and index >= first_global)
                or (binding != 0 and index < first_global)):
            return IMAGE_FORMAT
        name = ptr_add(strings, name_offset)
        if binding != 0:
            other: i64 = first_global
            while other < index:
                if _same(name, ptr_add(strings, _u32(_symbol(image, other), 0))):
                    return IMAGE_FORMAT
                other = other + 1
        if section_index == 0:
            if binding == 0 or visibility or load_i64(symbol, 8) != 0 or import_index >= imports:
                return IMAGE_FORMAT
            if _u32(note, 40 + 4 * (exports + import_index)) != index:
                return IMAGE_FORMAT
            if ptr_is_null(_host_address(hosts, name)):
                return IMAGE_HOST_SYMBOL
            import_index = import_index + 1
        elif section_index != 65521:
            if section_index >= load_i64(image, _NOTE_INDEX):
                return IMAGE_FORMAT
            value: i64 = load_i64(symbol, 8)
            size: i64 = load_i64(symbol, 16)
            if not _span(value, size, load_i64(_section(image, section_index), 32)):
                return IMAGE_FORMAT
        if binding != 0 and section_index != 0 and not ptr_is_null(_host_address(hosts, name)):
            return IMAGE_HOST_SYMBOL
        index = index + 1
    if import_index != imports:
        return IMAGE_FORMAT
    index = 0
    while index < exports:
        symbol_index: i64 = _u32(note, 40 + index * 4)
        if symbol_index < first_global or symbol_index >= count:
            return IMAGE_FORMAT
        symbol = _symbol(image, symbol_index)
        section_index = _u16(symbol, 6)
        if section_index == 0 or section_index >= load_i64(image, _NOTE_INDEX) or load_i8(symbol, 5):
            return IMAGE_FORMAT
        other = 0
        while other < index:
            if _u32(note, 40 + other * 4) == symbol_index:
                return IMAGE_FORMAT
            other = other + 1
        index = index + 1
    return 0


@c_abi_export("pcc_linux_elf_image__relocation_width")
def _relocation_width(kind: i64) -> i64:
    if kind == 0:
        return 0
    if kind == 1:
        return 8
    if kind == 2 or kind == 4 or kind == 10 or kind == 11:
        return 4
    return -1


@c_abi_export("pcc_linux_elf_image__validate_relocations")
def _validate_relocations(image: c_ptr) -> i64:
    index: i64 = load_i64(image, _NOTE_INDEX) + 1
    while index < load_i64(image, _SYMTAB_INDEX):
        section = _section(image, index)
        target = _section(image, _u32(section, 44))
        size: i64 = load_i64(target, 32)
        rows = ptr_add(load_ptr(image, _FILE), load_i64(section, 24))
        length: i64 = load_i64(section, 32)
        # A temporary bit per target byte proves non-overlap in linear work,
        # without changing RELA order or assigning an invented composition rule.
        marks = calloc((size + 7) // 8 + 1, 1)
        if ptr_is_null(marks):
            return IMAGE_ALLOCATION
        status: i64 = 0
        position: i64 = 0
        while position < length:
            offset: i64 = load_i64(rows, position)
            width: i64 = _relocation_width(_u32(rows, position + 8))
            if width < 0:
                status = IMAGE_UNSUPPORTED
                break
            if not _span(offset, width, size) or _u32(rows, position + 12) >= load_i64(image, _SYMBOL_COUNT):
                status = IMAGE_FORMAT
                break
            byte: i64 = 0
            while byte < width:
                at: i64 = offset + byte
                mask: i64 = 1 << (at % 8)
                previous: i64 = load_i8(marks, at // 8) & 255
                if previous & mask:
                    status = IMAGE_UNSUPPORTED
                    break
                store_i8(marks, at // 8, previous | mask)
                byte = byte + 1
            if status:
                break
            position = position + 24
        free(marks)
        if status:
            return status
        index = index + 1
    return 0


@c_abi_export("pcc_linux_elf_image__dispose")
def _dispose(image: c_ptr) -> i64:
    # The size, not pointer truthiness, records a successful mmap: address zero
    # is still an owned mapping when the range check rejects it.
    if load_i64(image, _MAPPING_SIZE) > 0:
        if syscall6(11, load_ptr(image, _MAPPING), load_i64(image, _MAPPING_SIZE), 0, 0, 0, 0) != 0:
            return IMAGE_SYSTEM
    free(load_ptr(image, _SYMBOL_ADDRESSES))
    free(load_ptr(image, _SECTION_ADDRESSES))
    free(load_ptr(image, _FILE))
    free(image)
    return 0


@c_abi_export("pcc_linux_elf_image__map_sections")
def _map_sections(image: c_ptr) -> i64:
    count: i64 = load_i64(image, _SECTION_COUNT)
    addresses = calloc(count, 8)
    if ptr_is_null(addresses):
        return IMAGE_ALLOCATION
    store_ptr(image, _SECTION_ADDRESSES, addresses)
    symbols = calloc(load_i64(image, _SYMBOL_COUNT), 8)
    if ptr_is_null(symbols):
        return IMAGE_ALLOCATION
    store_ptr(image, _SYMBOL_ADDRESSES, symbols)
    size: i64 = 0
    index: i64 = 1
    while index < load_i64(image, _NOTE_INDEX):
        length: i64 = load_i64(_section(image, index), 32)
        if length > 2147479552 or size > 2147479552 - length:
            return IMAGE_UNSUPPORTED
        store_i64(addresses, index * 8, size)
        size = size + ((length + 4095) // 4096) * 4096
        if length == 0:
            size = size + 4096
        index = index + 1
    if size == 0 or size > 2147479552:
        return IMAGE_UNSUPPORTED
    # MAP_PRIVATE | MAP_ANONYMOUS | MAP_32BIT. No fixed address replaces an
    # existing mapping. Low placement helps PC32, but every relocation is still
    # range-checked; this is not a promise that arbitrary host addresses fit.
    address: i64 = syscall6(9, 0, size, 3, 98, -1, 0)
    if address < 0:
        return IMAGE_SYSTEM
    store_ptr(image, _MAPPING, int_to_ptr(address))
    store_i64(image, _MAPPING_SIZE, size)
    if address == 0 or address > 2147483648 - size:
        return IMAGE_RELOCATION
    index = 1
    while index < load_i64(image, _NOTE_INDEX):
        section = _section(image, index)
        destination = int_to_ptr(address + load_i64(addresses, index * 8))
        store_ptr(addresses, index * 8, destination)
        if _u32(section, 4) == 1:
            memcpy(destination, ptr_add(load_ptr(image, _FILE), load_i64(section, 24)), load_i64(section, 32))
        index = index + 1
    return 0


@c_abi_export("pcc_linux_elf_image__resolve_symbols")
def _resolve_symbols(image: c_ptr, hosts: c_ptr) -> None:
    index: i64 = 1
    while index < load_i64(image, _SYMBOL_COUNT):
        symbol = _symbol(image, index)
        section_index: i64 = _u16(symbol, 6)
        address: i64 = load_i64(symbol, 8)
        if section_index == 0:
            address = ptr_to_int(_host_address(hosts, ptr_add(load_ptr(image, _STRINGS), _u32(symbol, 0))))
        elif section_index != 65521:
            address = address + load_i64(load_ptr(image, _SECTION_ADDRESSES), section_index * 8)
        store_i64(load_ptr(image, _SYMBOL_ADDRESSES), index * 8, address)
        index = index + 1


@c_abi_export("pcc_linux_elf_image__apply_one")
def _apply_one(patch: c_ptr, target: i64, addend: i64, kind: i64) -> i64:
    if kind == 0:
        return 0
    if kind == 1 or kind == 10:
        value: i64 = target + addend
        if ((addend >= 0 and unsigned_greater_i64(target, value))
                or (addend < 0 and unsigned_greater_i64(value, target))):
            return IMAGE_RELOCATION
        if kind == 1:
            store_i64(patch, 0, value)
        else:
            if unsigned_greater_i64(value, 4294967295):
                return IMAGE_RELOCATION
            store_i32(patch, 0, value)
        return 0
    # User-space mapped/host addresses are positive signed i64. High-half ABS
    # targets in signed/PC-relative 32-bit relocations are not this profile.
    if target < 0:
        return IMAGE_RELOCATION
    delta: i64 = target
    if kind == 2 or kind == 4:
        delta = target - ptr_to_int(patch)
    if ((addend > 0 and delta > 9223372036854775807 - addend)
            or (addend < 0 and delta < -9223372036854775808 - addend)):
        return IMAGE_RELOCATION
    value = delta + addend
    if value < -2147483648 or value > 2147483647:
        return IMAGE_RELOCATION
    store_i32(patch, 0, value)
    return 0


@c_abi_export("pcc_linux_elf_image__relocate_and_protect")
def _relocate_and_protect(image: c_ptr) -> i64:
    index: i64 = load_i64(image, _NOTE_INDEX) + 1
    while index < load_i64(image, _SYMTAB_INDEX):
        section = _section(image, index)
        rows = ptr_add(load_ptr(image, _FILE), load_i64(section, 24))
        destination = load_ptr(load_ptr(image, _SECTION_ADDRESSES), _u32(section, 44) * 8)
        position: i64 = 0
        while position < load_i64(section, 32):
            target: i64 = load_i64(load_ptr(image, _SYMBOL_ADDRESSES), _u32(rows, position + 12) * 8)
            status: i64 = _apply_one(ptr_add(destination, load_i64(rows, position)), target,
                                     load_i64(rows, position + 16), _u32(rows, position + 8))
            if status:
                return status
            position = position + 24
        index = index + 1
    index = 1
    while index < load_i64(image, _NOTE_INDEX):
        section = _section(image, index)
        flags: i64 = load_i64(section, 8)
        protection: i64 = 1
        if flags & 1:
            protection = 3
        elif flags & 4:
            protection = 5
        length: i64 = ((load_i64(section, 32) + 4095) // 4096) * 4096
        if length == 0:
            length = 4096
        if syscall6(10, load_ptr(load_ptr(image, _SECTION_ADDRESSES), index * 8), length, protection, 0, 0, 0) != 0:
            return IMAGE_SYSTEM
        index = index + 1
    return 0


@c_abi_export("pcc_linux_elf_image_open")
def image_open(path: c_ptr, hosts: c_ptr, output: c_ptr) -> i64:
    """Return status and transfer any remaining image ownership through output.

    Success returns zero and a ready image. Ordinary failure returns nonzero
    and NULL. If unmapping a failed image itself fails, nonzero status transfers
    an unready handle instead of losing its ownership; the caller must close it.
    image_symbol refuses such a handle. output must name one writable pointer.
    """
    if ptr_is_null(output):
        return IMAGE_FORMAT
    store_ptr(output, 0, null())
    status: i64 = IMAGE_UNSUPPORTED
    if (not _same(target_sys_platform(), cstr("linux"))
            or not _same(target_platform_machine(), cstr("x86_64"))):
        return status
    if ptr_is_null(hosts) or ptr_is_null(path):
        return IMAGE_HOST_SYMBOL
    index: i64 = 0
    magic = cstr("PCCEXP01")
    while index < 8:
        if load_i8(hosts, index) != load_i8(magic, index):
            return IMAGE_HOST_SYMBOL
        index = index + 1
    if load_i64(hosts, 8) < 0:
        return IMAGE_HOST_SYMBOL
    image = calloc(1, _RECORD_SIZE)
    if ptr_is_null(image):
        return IMAGE_ALLOCATION
    fd: i64 = open_readonly(path)
    status = IMAGE_SYSTEM
    if fd >= 0:
        size: i64 = syscall6(8, fd, 0, 2, 0, 0, 0)
        if size >= 64 and size <= 2147483647 and syscall6(8, fd, 0, 0, 0, 0, 0) == 0:
            data = malloc(size)
            status = IMAGE_ALLOCATION
            if not ptr_is_null(data):
                store_ptr(image, _FILE, data)
                store_i64(image, _FILE_SIZE, size)
                position: i64 = 0
                status = 0
                while position < size:
                    amount: i64 = read(fd, ptr_add(data, position), size - position)
                    if amount == -4:
                        continue
                    if amount <= 0 or amount > size - position:
                        status = IMAGE_SYSTEM
                        break
                    position = position + amount
        elif size >= 0:
            status = IMAGE_FORMAT
        if close(fd) != 0:
            status = IMAGE_SYSTEM
    if status == 0:
        status = _validate_headers(image)
    if status == 0:
        status = _validate_note_and_symbols(image, hosts)
    if status == 0:
        status = _validate_relocations(image)
    if status == 0:
        status = _map_sections(image)
    if status == 0:
        _resolve_symbols(image, hosts)
        status = _relocate_and_protect(image)
    if status:
        if _dispose(image) != 0:
            store_ptr(output, 0, image)
            return IMAGE_SYSTEM
        return status
    store_i64(image, _READY, 1)
    store_ptr(output, 0, image)
    return 0


@c_abi_export("pcc_linux_elf_image_symbol")
def image_symbol(image: c_ptr, name: c_ptr) -> c_ptr:
    """Borrow an explicitly exported address from a live, caller-owned image."""
    if ptr_is_null(image) or ptr_is_null(name):
        return null()
    if load_i64(image, _READY) != 1:
        return null()
    index: i64 = 0
    while index < load_i64(image, _EXPORT_COUNT):
        symbol_index: i64 = _u32(load_ptr(image, _EXPORTS), index * 4)
        symbol = _symbol(image, symbol_index)
        if _same(name, ptr_add(load_ptr(image, _STRINGS), _u32(symbol, 0))):
            return load_ptr(load_ptr(image, _SYMBOL_ADDRESSES), symbol_index * 8)
        index = index + 1
    return null()


@c_abi_export("pcc_linux_elf_image_close")
def image_close(image: c_ptr) -> i64:
    """Release the last owner; on failure it retains the handle for recovery."""
    if not ptr_is_null(image):
        return _dispose(image)
    return 0
