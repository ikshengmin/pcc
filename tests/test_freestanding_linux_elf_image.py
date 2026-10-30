"""Host contracts for the real mapper over checked memory/kernel boundaries.

These tests import the production module and consume the real owned publisher's
ELF bytes. Only unsafe memory/syscall intrinsics are modeled; no generated code
is executed. Native pcc1, OS mapping and GC qualification are separate gates.
"""

import struct

import pytest

from pcc.backend import elf_x86_64 as elf
from pcc.backend.elf_extension import link_relocatable_extension
from pcc.runtime.py import freestanding_linux_elf_image as loader


class Memory:
    def __init__(self):
        self.blocks = {}
        self.next_address = 0x100000000
        self.constants = {}
        self.allocations = 0
        self.fail_allocation = 0
        self.fail_mmap = False
        self.fail_protect = 0
        self.fail_unmap = False
        self.fail_close = False
        self.protections = []
        self.mapping_address = 0x10000000
        self.machine = "x86_64"
        self.system = "linux"
        self.file = b""
        self.file_position = 0
        self.fd_open = False
        self.fail_open = False
        self.read_results = []
        self.events = []

    def allocate(self, size, kind="raw", address=None):
        if kind == "raw":
            self.allocations += 1
            if self.allocations == self.fail_allocation:
                return 0
        assert 0 <= size < 1000000, "fixture attempted an unbounded allocation"
        if address is None:
            address = self.next_address
            self.next_address += max(size, 1) + 64
        assert address not in self.blocks
        self.blocks[address] = (bytearray(size), kind)
        self.events.append(("allocate", address, size, kind))
        return address

    def locate(self, pointer, size):
        for address, (data, kind) in self.blocks.items():
            if address <= pointer and pointer + size <= address + len(data):
                return data, pointer - address
        raise AssertionError(f"out-of-bounds or released memory access {pointer:#x}+{size}")

    def get(self, pointer, size):
        data, offset = self.locate(pointer, size)
        return bytes(data[offset:offset + size])

    def put(self, pointer, value):
        data, offset = self.locate(pointer, len(value))
        data[offset:offset + len(value)] = value

    def load(self, pointer, offset, size):
        return int.from_bytes(self.get(pointer + offset, size), "little", signed=True)

    def store(self, pointer, offset, value, size):
        self.put(pointer + offset, (value & ((1 << (size * 8)) - 1)).to_bytes(size, "little"))

    def cstr(self, text):
        if text not in self.constants:
            value = text.encode() + b"\0"
            pointer = self.allocate(len(value), "constant")
            self.put(pointer, value)
            self.constants[text] = pointer
        return self.constants[text]

    def free(self, pointer):
        if pointer:
            assert self.blocks[pointer][1] == "raw"
            del self.blocks[pointer]
            self.events.append(("free", pointer))

    def copy(self, destination, source, size):
        self.put(destination, self.get(source, size))
        return destination

    def open(self, path):
        assert path == self.cstr("extension.pcc-elf")
        if self.fail_open:
            return -2
        assert not self.fd_open
        self.fd_open = True
        self.file_position = 0
        return 9

    def close(self, fd):
        assert fd == 9 and self.fd_open
        self.fd_open = False
        return -5 if self.fail_close else 0

    def read(self, fd, destination, size):
        assert fd == 9 and self.fd_open
        amount = self.read_results.pop(0) if self.read_results else size
        if amount <= 0:
            return amount
        amount = min(amount, size, len(self.file) - self.file_position)
        self.put(destination, self.file[self.file_position:self.file_position + amount])
        self.file_position += amount
        return amount

    def syscall(self, number, a, b, c, d, e, f):
        self.events.append(("syscall", number, a, b, c, d, e, f))
        if number == 8:
            assert a == 9 and self.fd_open and b == 0 and d == e == f == 0
            assert c in (0, 2)
            self.file_position = len(self.file) if c == 2 else 0
            return self.file_position
        if number == 9:
            assert (a, c, d, e, f) == (0, 3, 98, -1, 0)
            if self.fail_mmap:
                return -12
            return self.allocate(b, "mapping", self.mapping_address)
        if number == 10:
            assert a % 4096 == b % 4096 == 0 and c in (1, 3, 5) and d == e == f == 0
            self.locate(a, b)
            self.protections.append((a, b, c))
            return -13 if len(self.protections) == self.fail_protect else 0
        if number == 11:
            assert self.blocks[a][1] == "mapping" and len(self.blocks[a][0]) == b
            assert c == d == e == f == 0
            if self.fail_unmap:
                return -12
            del self.blocks[a]
            return 0
        raise AssertionError("unexpected syscall " + str(number))

    def install(self, monkeypatch):
        bindings = {
            "calloc": lambda count, size: self.allocate(count * size),
            "malloc": self.allocate, "free": self.free,
            "memcpy": self.copy,
            "cstr": self.cstr, "ptr_add": lambda ptr, offset: ptr + offset,
            "ptr_to_int": lambda ptr: ptr, "int_to_ptr": lambda value: value,
            "ptr_is_null": lambda ptr: ptr == 0, "null": lambda: 0,
            "load_ptr": lambda ptr, offset: self.load(ptr, offset, 8),
            "store_ptr": lambda ptr, offset, value: self.store(ptr, offset, value, 8),
            "target_platform_machine": lambda: self.cstr(self.machine),
            "target_sys_platform": lambda: self.cstr(self.system),
            "unsigned_greater_i64": lambda left, right: (left & ((1 << 64) - 1)) > (right & ((1 << 64) - 1)),
            "open_readonly": self.open, "read": self.read, "close": self.close,
            "syscall6": self.syscall,
        }
        for size in (1, 4, 8):
            bindings["load_i" + str(size * 8)] = lambda ptr, off, size=size: self.load(ptr, off, size)
            bindings["store_i" + str(size * 8)] = lambda ptr, off, value, size=size: self.store(ptr, off, value, size)
        for name, value in bindings.items():
            monkeypatch.setattr(loader, name, value)

    def hosts(self, rows):
        pointer = self.allocate(16 + len(rows) * 16, "fixture")
        self.put(pointer, b"PCCEXP01" + struct.pack("<Q", len(rows)))
        for index, (name, value) in enumerate(rows):
            self.store(pointer, 16 + 16 * index, self.cstr(name), 8)
            self.store(pointer, 24 + 16 * index, value, 8)
        return pointer

    def assert_released(self):
        assert not self.fd_open
        assert all(kind in ("constant", "fixture") for _, kind in self.blocks.values())


def _extension():
    obj = elf.ElfObject((
        elf.ElfSection(".text", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_EXECINSTR,
                       16, b"\xb8\x2a\0\0\0\xc3"),
        elf.ElfSection(".data", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_WRITE,
                       8, bytes(40), relocations=(
                           elf.ElfRelocation(0, 5, elf.R_X86_64_64),
                           elf.ElfRelocation(8, 5, elf.R_X86_64_PC32, -4),
                           elf.ElfRelocation(12, 6, elf.R_X86_64_32),
                           elf.ElfRelocation(16, 2, elf.R_X86_64_32S, -7),
                           elf.ElfRelocation(20, 5, elf.R_X86_64_PLT32, -4),
                           elf.ElfRelocation(24, 3, elf.R_X86_64_64),
                           elf.ElfRelocation(32, 7, elf.R_X86_64_64),
                           elf.ElfRelocation(40, 0, elf.R_X86_64_NONE),
                       )),
        elf.ElfSection(".bss", elf.SHT_NOBITS, elf.SHF_ALLOC | elf.SHF_WRITE,
                       8, b"", 8),
        elf.ElfSection(".rodata", elf.SHT_PROGBITS, elf.SHF_ALLOC, 1, b"owned\0"),
    ), (
        elf.ElfSymbol.null(),
        elf.ElfSymbol("private", 1, 5, 1, elf.STB_LOCAL, elf.STT_FUNC),
        elf.ElfSymbol("zero_absolute", elf.SHN_ABS, 0, 0, elf.STB_LOCAL, elf.STT_NOTYPE),
        elf.ElfSymbol("answer", 1, 0, 6, elf.STB_GLOBAL, elf.STT_FUNC),
        elf.ElfSymbol("table", 2, 0, 40, elf.STB_GLOBAL, elf.STT_OBJECT),
        elf.ElfSymbol("host_api", elf.SHN_UNDEF, 0, 0, elf.STB_GLOBAL, elf.STT_FUNC),
        elf.ElfSymbol("host_counter", elf.SHN_UNDEF, 0, 0, elf.STB_GLOBAL, elf.STT_OBJECT),
        elf.ElfSymbol("zero", 3, 0, 8, elf.STB_GLOBAL, elf.STT_OBJECT),
    ))
    return link_relocatable_extension([obj], export_symbols=("answer", "table", "zero"),
                                      host_import_symbols=("host_api", "host_counter"))


@pytest.fixture
def memory(monkeypatch):
    model = Memory()
    model.install(monkeypatch)
    model.file = _extension()
    return model


def _open(memory, rows=(("host_api", 0x401000), ("host_counter", 0x402000))):
    output = memory.allocate(8, "fixture")
    status = loader.image_open(memory.cstr("extension.pcc-elf"), memory.hosts(rows), output)
    return memory.load(output, 0, 8), status


def _headers(data):
    header = elf._ELF_HEADER.unpack_from(data)
    return header, [elf._SECTION_HEADER.unpack_from(data, header[6] + index * 64)
                    for index in range(header[12])]


def _set(data, offset, format, value):
    result = bytearray(data)
    struct.pack_into("<" + format, result, offset, value)
    return bytes(result)


def test_real_publication_maps_and_relocates_exact_host_and_private_addresses(memory):
    image, error = _open(memory)
    assert image and error == 0
    answer = loader.image_symbol(image, memory.cstr("answer"))
    table = loader.image_symbol(image, memory.cstr("table"))
    zero = loader.image_symbol(image, memory.cstr("zero"))
    assert memory.get(answer, 6) == b"\xb8\x2a\0\0\0\xc3"
    assert memory.load(table, 0, 8) == 0x401000
    assert memory.load(table, 8, 4) == 0x401000 - 4 - (table + 8)
    assert memory.load(table, 12, 4) == 0x402000
    assert memory.load(table, 16, 4) == -7
    assert memory.load(table, 20, 4) == 0x401000 - 4 - (table + 20)
    assert memory.load(table, 24, 8) == answer
    assert memory.load(table, 32, 8) == zero
    assert memory.get(zero, 8) == bytes(8)
    assert loader.image_symbol(image, memory.cstr("private")) == 0
    assert loader.image_symbol(image, memory.cstr("host_api")) == 0
    assert loader.image_symbol(image, memory.cstr("missing")) == 0
    assert [flags for _, _, flags in memory.protections] == [5, 3, 3, 1]
    assert loader.image_close(image) == 0
    memory.assert_released()


def test_short_reads_and_eintr_complete_one_private_snapshot(memory):
    memory.read_results = [-4, 1, 7, -4, 13]
    image, error = _open(memory)
    assert image and error == 0
    loader.image_close(image)
    memory.assert_released()


@pytest.mark.parametrize("allocation", range(1, 6))
def test_every_raw_allocation_failure_is_explicit_and_releases_prior_ownership(memory, allocation):
    memory.fail_allocation = allocation
    image, error = _open(memory)
    assert image == 0 and error == loader.IMAGE_ALLOCATION
    memory.assert_released()


@pytest.mark.parametrize("protection", range(1, 5))
def test_each_protection_failure_unmaps_even_partially_protected_images(memory, protection):
    memory.fail_protect = protection
    image, error = _open(memory)
    assert image == 0 and error == loader.IMAGE_SYSTEM
    memory.assert_released()


@pytest.mark.parametrize("failure", ["open", "eof", "read", "close", "mmap", "high_mapping", "zero_mapping", "machine", "system"])
def test_kernel_and_target_failures_release_every_acquired_resource(memory, failure):
    if failure == "open":
        memory.fail_open = True
    elif failure == "eof":
        memory.read_results = [0]
    elif failure == "read":
        memory.read_results = [-5]
    elif failure == "mmap":
        memory.fail_mmap = True
    elif failure == "close":
        memory.fail_close = True
    elif failure == "high_mapping":
        memory.mapping_address = 0x90000000
    elif failure == "zero_mapping":
        memory.mapping_address = 0
    elif failure == "machine":
        memory.machine = "aarch64"
    else:
        memory.system = "darwin"
    image, error = _open(memory)
    assert image == 0 and error != 0
    memory.assert_released()


def test_failed_open_returns_unready_ownership_if_cleanup_itself_fails(memory):
    memory.fail_protect = 1
    memory.fail_unmap = True
    image, error = _open(memory)
    assert image != 0 and error == loader.IMAGE_SYSTEM
    assert loader.image_symbol(image, memory.cstr("answer")) == 0
    assert loader.image_close(image) == loader.IMAGE_SYSTEM
    memory.fail_unmap = False
    assert loader.image_close(image) == 0
    memory.assert_released()


def test_failed_close_preserves_owned_handle_until_successful_release(memory):
    image, error = _open(memory)
    assert image and error == 0
    memory.fail_unmap = True
    assert loader.image_close(image) == loader.IMAGE_SYSTEM
    memory.fail_unmap = False
    assert loader.image_close(image) == 0
    memory.assert_released()


@pytest.mark.parametrize("rows", [(), (("host_api", 0x401000),),
    (("host_api", 0x401000), ("host_counter", 0x402000), ("answer", 0x403000))])
def test_no_missing_import_or_host_definition_collision_is_admitted(memory, rows):
    image, error = _open(memory, rows)
    assert image == 0 and error == loader.IMAGE_HOST_SYMBOL
    memory.assert_released()


@pytest.mark.parametrize("offset,format,value", [
    (0, "I", 0), (4, "B", 1), (7, "B", 3), (16, "H", 3), (18, "H", 183),
    (24, "Q", 1), (32, "Q", 64), (40, "Q", 2**63), (48, "I", 1),
    (52, "H", 63), (54, "H", 56), (56, "H", 1), (58, "H", 63),
    (60, "H", 0), (60, "H", 0xff00), (62, "H", 0xffff),
])
def test_malformed_or_non_profile_headers_are_rejected_before_mapping(memory, offset, format, value):
    memory.file = _set(memory.file, offset, format, value)
    image, error = _open(memory)
    assert image == 0 and error != 0
    assert not any(event[:2] == ("syscall", 9) for event in memory.events)
    memory.assert_released()


@pytest.mark.parametrize("mutation", ["tls", "rwx", "note_version", "note_count", "note_import",
    "rela_kind", "rela_symbol", "rela_offset", "rela_overlap", "symbol_index", "symbol_size",
    "symbol_name", "symbol_tls", "export_local", "export_duplicate"])
def test_real_elf_metadata_mutations_fail_closed_with_checked_memory(memory, mutation):
    header, rows = _headers(memory.file)
    note = next(row for row in rows if row[1] == elf.SHT_NOTE)
    rela = next(row for row in rows if row[1] == elf.SHT_RELA)
    symtab = next(row for row in rows if row[1] == elf.SHT_SYMTAB)
    choices = {
        "tls": (header[6] + 64 + 8, "Q", elf.SHF_ALLOC | elf.SHF_TLS),
        "rwx": (header[6] + 64 + 8, "Q", 7),
        "note_version": (note[4] + 16, "I", 2),
        "note_count": (note[4] + 28, "I", 0xffffffff),
        "note_import": (note[4] + 40 + 3 * 4, "I", 3),
        "rela_kind": (rela[4] + 8, "I", elf.R_X86_64_GOTPCREL),
        "rela_symbol": (rela[4] + 12, "I", 0xffffffff),
        "rela_offset": (rela[4], "Q", 2**63),
        "rela_overlap": (rela[4] + 24, "Q", 1),
        "symbol_index": (symtab[4] + 3 * 24 + 6, "H", 0xff00),
        "symbol_size": (symtab[4] + 3 * 24 + 16, "Q", 2**63),
        "symbol_name": (symtab[4] + 3 * 24, "I", 0xffffffff),
        "symbol_tls": (symtab[4] + 3 * 24 + 4, "B", 16 + elf.STT_TLS),
        "export_local": (note[4] + 40, "I", 1),
        "export_duplicate": (note[4] + 44, "I", 3),
    }
    memory.file = _set(memory.file, *choices[mutation])
    image, error = _open(memory)
    assert image == 0 and error != 0
    assert not any(event[:2] == ("syscall", 9) for event in memory.events)
    memory.assert_released()


@pytest.mark.parametrize("kind,target,addend,expected", [
    (elf.R_X86_64_64, 0x1000, -1, 0xfff),
    (elf.R_X86_64_64, -2, 1, -1),
    (elf.R_X86_64_64, 9223372036854775807, 1, -9223372036854775808),
    (elf.R_X86_64_64, -1, 1, None),
    (elf.R_X86_64_64, 0, -1, None),
    (elf.R_X86_64_32, 0xffffffff, 0, -1),
    (elf.R_X86_64_32, 0xffffffff, 1, None),
    (elf.R_X86_64_32S, 0, -2147483648, -2147483648),
    (elf.R_X86_64_32S, 2147483647, 0, 2147483647),
    (elf.R_X86_64_32S, 2147483647, 1, None),
    (elf.R_X86_64_32S, 0, -2147483649, None),
])
def test_exact_integer_relocation_boundaries(memory, kind, target, addend, expected):
    patch = memory.allocate(8, "fixture")
    status = loader._apply_one(patch, target, addend, kind)
    if expected is None:
        assert status == loader.IMAGE_RELOCATION and memory.get(patch, 8) == bytes(8)
    else:
        assert status == 0
        assert memory.load(patch, 0, 8 if kind == elf.R_X86_64_64 else 4) == expected


def test_host_pc_relative_overflow_does_not_publish_an_image(memory):
    image, error = _open(memory, (("host_api", 0x700000000000), ("host_counter", 0x402000)))
    assert image == 0 and error == loader.IMAGE_RELOCATION
    assert not memory.protections
    memory.assert_released()
