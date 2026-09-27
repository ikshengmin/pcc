"""Deterministic PE32+ executable linking over owned AMD64 COFF objects.

System imports are named DLL ABI entries. Import libraries, CRT startup,
external linkers and host interpreters do not participate in this boundary.
"""

import struct

from .coff_x86_64 import (
    CoffError, CoffObject, CoffSection, CoffSymbol, ADDR64, ADDR32, ADDR32NB,
    REL32, SECREL, SECTION, parse_object,
)
from .elf_x86_64 import read_archive_payloads

_KERNEL32 = frozenset("""
FormatMessageW ExitProcess GetCurrentProcess GetCurrentProcessId GetCurrentThreadId GetProcessHandleCount GetHandleInformation
VirtualAlloc VirtualFree VirtualProtect GetSystemInfo GetNativeSystemInfo
GetProcessHeap HeapAlloc HeapReAlloc HeapFree GetLastError SetLastError
GetStdHandle SetStdHandle ReadFile WriteFile CloseHandle CreateFileW LockFileEx UnlockFileEx
DeleteFileW MoveFileExW CreateDirectoryW RemoveDirectoryW GetFileAttributesW
SetFileAttributesW GetFileInformationByHandle GetFileInformationByHandleEx GetFileSizeEx SetFilePointerEx
SetEndOfFile FlushFileBuffers GetCurrentDirectoryW SetCurrentDirectoryW
GetFullPathNameW GetFinalPathNameByHandleW GetTempPathW GetTempFileNameW
FindFirstFileW FindNextFileW FindClose GetModuleFileNameW GetModuleHandleW
LoadLibraryW FreeLibrary GetProcAddress GetCommandLineW LocalFree
GetEnvironmentStringsW FreeEnvironmentStringsW GetEnvironmentVariableW
SetEnvironmentVariableW MultiByteToWideChar WideCharToMultiByte
CompareStringOrdinal
CreateProcessW CreatePipe SetHandleInformation DuplicateHandle
InitializeProcThreadAttributeList UpdateProcThreadAttribute DeleteProcThreadAttributeList
WaitForSingleObject WaitForMultipleObjects GetExitCodeProcess TerminateProcess
CreateJobObjectW AssignProcessToJobObject SetInformationJobObject
TerminateJobObject CreateThread ResumeThread GetExitCodeThread Sleep
TlsAlloc TlsFree TlsGetValue TlsSetValue InitializeCriticalSection
DeleteCriticalSection EnterCriticalSection LeaveCriticalSection
InitializeSRWLock AcquireSRWLockExclusive TryAcquireSRWLockExclusive ReleaseSRWLockExclusive
AcquireSRWLockShared ReleaseSRWLockShared InitializeConditionVariable
SleepConditionVariableSRW WakeConditionVariable WakeAllConditionVariable
WaitOnAddress WakeByAddressSingle WakeByAddressAll
QueryPerformanceCounter QueryPerformanceFrequency GetSystemTimeAsFileTime
GetSystemTimePreciseAsFileTime GetTickCount64 GetProcessTimes GetThreadTimes
GetConsoleMode SetConsoleMode GetConsoleScreenBufferInfo WriteConsoleW SetConsoleCtrlHandler OpenProcess
GetActiveProcessorCount GetProcessAffinityMask GetLogicalProcessorInformationEx
CreateEventW SetEvent ResetEvent CreateSemaphoreW ReleaseSemaphore
GetDynamicTimeZoneInformation SystemTimeToTzSpecificLocalTimeEx GetFileType GetFileAttributesExW GetTimeZoneInformation DeviceIoControl GetComputerNameW PeekNamedPipe
""".split())
_WS2 = frozenset("""WSAStartup WSACleanup WSAGetLastError socket closesocket
connect bind listen accept shutdown send recv sendto recvfrom getsockopt
setsockopt getsockname getpeername ioctlsocket WSAPoll getaddrinfo freeaddrinfo
""".split())


def system_dll(symbol: str) -> str:
    if symbol in _KERNEL32:
        return "KERNEL32.dll"
    if symbol in _WS2:
        return "WS2_32.dll"
    if symbol == "RtlGetVersion":
        return "NTDLL.dll"
    if symbol == "CommandLineToArgvW":
        return "SHELL32.dll"
    if symbol in ("GetProcessMemoryInfo", "K32GetProcessMemoryInfo"):
        return "PSAPI.dll" if symbol == "GetProcessMemoryInfo" else "KERNEL32.dll"
    if symbol in ("BCryptGenRandom",):
        return "BCRYPT.dll"
    raise CoffError("undefined Windows symbol has no named system DLL ABI: " + symbol)


def _align(value, alignment):
    return (value + alignment - 1) & -alignment


def _symbols(objects):
    defined = {}
    needed = set()
    for oi, obj in enumerate(objects):
        for si, symbol in enumerate(obj.symbols):
            if not symbol.external:
                continue
            if symbol.section:
                if symbol.name in defined:
                    raise CoffError("duplicate COFF symbol: " + symbol.name)
                defined[symbol.name] = (oi, si)
            else:
                needed.add(symbol.name)
    return defined, needed - set(defined)


def _import_data(names, base_rva):
    groups = {}
    for name in sorted(names):
        groups.setdefault(system_dll(name), []).append(name)
    payload = bytearray(b"\0" * (20 * (len(groups) + 1)))
    payload.extend(b"\0" * ((-len(payload)) & 7))
    lookups = {}
    iats = {}
    slots = {}
    for dll in sorted(groups):
        lookups[dll] = len(payload)
        payload.extend(b"\0" * ((len(groups[dll]) + 1) * 8))
    iat_start = len(payload)
    for dll in sorted(groups):
        iats[dll] = len(payload)
        payload.extend(b"\0" * ((len(groups[dll]) + 1) * 8))
    iat_end = len(payload)
    for ordinal, dll in enumerate(sorted(groups)):
        dll_offset = len(payload)
        payload.extend(dll.encode("ascii") + b"\0")
        for index, name in enumerate(groups[dll]):
            payload.extend(b"\0" * (len(payload) & 1))
            hint = len(payload)
            payload.extend(b"\0\0" + name.encode("ascii") + b"\0")
            struct.pack_into("<Q", payload, lookups[dll] + index * 8, base_rva + hint)
            struct.pack_into("<Q", payload, iats[dll] + index * 8, base_rva + hint)
            slots[name] = base_rva + iats[dll] + index * 8
        struct.pack_into("<IIIII", payload, ordinal * 20,
                         base_rva + lookups[dll], 0, 0, base_rva + dll_offset, base_rva + iats[dll])
    directory = (base_rva + iat_start, iat_end - iat_start) if groups else (0, 0)
    return payload, slots, directory


def link_executable(objects, *, archives=(), entry="pcc_windows_start",
                    image_base=0x140000000) -> bytes:
    objects = list(objects)
    pool = []
    for archive in archives:
        pool.extend(parse_object(payload) for _, payload in read_archive_payloads(archive))
    while True:
        definitions, needed = _symbols(objects)
        if entry not in definitions:
            needed.add(entry)
        selected = -1
        for index, obj in enumerate(pool):
            if any(sym.external and sym.section and sym.name in needed for sym in obj.symbols):
                selected = index
                break
        if selected < 0:
            break
        objects.append(pool.pop(selected))
    definitions, needed = _symbols(objects)
    if entry not in definitions:
        raise CoffError("Windows entry is not defined: " + entry)
    # Reserve loader-written TLS index storage even for images without TLS.
    index_object = CoffObject((CoffSection(".data$tlsidx", b"\0" * 4, 0xC0000040, 4),),
                              (CoffSymbol("_tls_index", 1),))
    if "_tls_index" not in definitions:
        objects.append(index_object)
    definitions, needed = _symbols(objects)
    boundary_names = {"__init_array_start", "__init_array_end", "__fini_array_start", "__fini_array_end"}
    if boundary_names & set(definitions):
        raise CoffError("initializer boundary symbols are owned by the linker")
    needed -= boundary_names
    imports = {name[6:] if name.startswith("__imp_") else name for name in needed}
    for name in imports:
        system_dll(name)

    # Merge sections by PE name, sorting COFF $ suffixes before concatenation.
    groups = {}
    for oi, obj in enumerate(objects):
        for si, sec in enumerate(obj.sections, 1):
            if sec.name in (".note.GNU-stack", ".comment"):
                continue
            base = sec.name.split("$", 1)[0]
            if base.startswith(".rodata") or base == ".pcc_stackmaps":
                base = ".rdata"
            if len(base.encode()) > 8:
                raise CoffError("PE section name exceeds eight bytes: " + base)
            groups.setdefault(base, []).append((sec.name, oi, si, sec))
    groups.setdefault(".text", [])
    groups.setdefault(".idata", [])
    groups.setdefault(".rdata", [])
    sections = []
    placements = {}
    rva = 0x1000
    text_thunks = {}
    tls_start = tls_end = tls_directory = 0
    tls_pointers = []
    iat_directory = (0, 0)
    import_directory = (0, 0)
    for name in sorted(groups, key=lambda value: (value != ".text", value)):
        payload = bytearray()
        flags = 0x40000040
        if name == ".text":
            flags = 0x60000020
        for _source_name, oi, si, sec in sorted(groups[name], key=lambda item: (item[0], item[1], item[2])):
            if sec.align > 4096:
                raise CoffError("PE section alignment above page size is unsupported")
            payload.extend(b"\0" * ((-len(payload)) & (sec.align - 1)))
            placements[(oi, si)] = (len(sections), len(payload))
            payload.extend(sec.data)
            flags |= sec.flags & 0xE00000E0
        if name == ".text":
            for symbol in sorted(imports):
                text_thunks[symbol] = rva + len(payload)
                payload.extend(b"\xff\x25\0\0\0\0")
        if name == ".idata":
            if payload:
                raise CoffError("explicit import sections conflict with owned imports")
            payload, import_slots, iat_directory = _import_data(imports, rva)
            flags = 0xC0000040
            import_directory = (rva, 20 * (len({system_dll(n) for n in imports}) + 1)) if imports else (0, 0)
        if name == ".tls":
            tls_start, tls_end = rva, rva + len(payload)
        sections.append([name, rva, payload, flags])
        rva = _align(rva + max(1, len(payload)), 4096)

    boundaries = {name: 0 for name in boundary_names}
    for name, address, payload, flags in sections:
        if name in (".pccinit", ".pccfini"):
            family = "init" if name == ".pccinit" else "fini"
            boundaries["__" + family + "_array_start"] = address
            boundaries["__" + family + "_array_end"] = address + len(payload)

    def resolve(oi, si):
        sym = objects[oi].symbols[si]
        if sym.external and sym.name in definitions:
            oi, si = definitions[sym.name]
            sym = objects[oi].symbols[si]
        if not sym.section:
            if sym.name in boundaries:
                return boundaries[sym.name], None
            if sym.name.startswith("__imp_"):
                return import_slots[sym.name[6:]], None
            return text_thunks[sym.name], None
        if (oi, sym.section) not in placements:
            raise CoffError("symbol belongs to a discarded section: " + sym.name)
        index, offset = placements[(oi, sym.section)]
        return sections[index][1] + offset + sym.value, index

    fixups = []
    for oi, obj in enumerate(objects):
        for si, source in enumerate(obj.sections, 1):
            if (oi, si) not in placements:
                continue
            section_index, base = placements[(oi, si)]
            output = sections[section_index]
            for reloc in source.relocations:
                patch = base + reloc.offset
                target, target_section = resolve(oi, reloc.symbol)
                place = output[1] + patch
                width = 8 if reloc.kind == ADDR64 else 2 if reloc.kind == SECTION else 4
                signed = 4 <= reloc.kind <= 9
                addend = int.from_bytes(output[2][patch:patch + width], "little", signed=signed)
                if reloc.kind == ADDR64:
                    value = image_base + target + addend
                    fixups.append(place)
                elif reloc.kind == ADDR32NB:
                    value = target + addend
                elif reloc.kind == ADDR32:
                    value = image_base + target + addend
                elif 4 <= reloc.kind <= 9:
                    value = target + addend - (place + 4 + reloc.kind - 4)
                elif reloc.kind == SECREL:
                    if target_section is None:
                        raise CoffError("SECREL cannot reference an import")
                    value = target - sections[target_section][1] + addend
                elif reloc.kind == SECTION:
                    if target_section is None:
                        raise CoffError("SECTION cannot reference an import")
                    value = target_section + 1 + addend
                else:
                    raise CoffError("unhandled PE relocation")
                if signed:
                    if value < -(1 << 31) or value >= 1 << 31:
                        raise CoffError("PE rel32 out of range")
                elif value < 0 or value >= 1 << (width * 8):
                    raise CoffError("PE relocation overflow")
                output[2][patch:patch + width] = (value & ((1 << (width * 8)) - 1)).to_bytes(width, "little")
    for name, address in text_thunks.items():
        text = sections[0]
        struct.pack_into("<i", text[2], address - text[1] + 2, import_slots[name] - address - 6)
    if tls_start:
        # A separate section keeps all previous RVAs stable.
        tls_directory = rva
        oi, si = definitions["_tls_index"]
        index_rva = resolve(oi, si)[0]
        payload = bytearray(struct.pack("<QQQQII", image_base + tls_start, image_base + tls_end,
                                        image_base + index_rva, 0, 0, 0))
        sections.append([".tlsdir", rva, payload, 0x40000040])
        fixups.extend((rva, rva + 8, rva + 16))
        rva += 4096
    exception_directory = (0, 0)
    for name, address, payload, flags in sections:
        if name == ".pdata":
            if len(payload) % 12:
                raise CoffError("malformed runtime function table")
            records = [bytes(payload[i:i + 12]) for i in range(0, len(payload), 12)]
            records.sort(key=lambda record: struct.unpack_from("<I", record)[0])
            payload[:] = b"".join(records)
            exception_directory = (address, len(payload))
    reloc_data = bytearray()
    pages = {}
    for address in sorted(set(fixups)):
        pages.setdefault(address & -4096, []).append(0xA000 | (address & 4095))
    for page, entries in sorted(pages.items()):
        if len(entries) & 1:
            entries.append(0)
        reloc_data.extend(struct.pack("<II", page, 8 + len(entries) * 2))
        for word in entries:
            reloc_data.extend(struct.pack("<H", word))
    reloc_directory = (rva, len(reloc_data)) if reloc_data else (0, 0)
    if reloc_data:
        sections.append([".reloc", rva, reloc_data, 0x42000040])
        rva = _align(rva + len(reloc_data), 4096)
    headers = _align(128 + 4 + 20 + 240 + len(sections) * 40, 512)
    image = bytearray(b"\0" * headers)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 60, 128)
    image[128:132] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", image, 132, 0x8664, len(sections), 0, 0, 0, 240, 0x22 if reloc_data else 0x23)
    opt = 152
    entry_rva = resolve(*definitions[entry])[0]
    struct.pack_into("<HBBIIIIIQ", image, opt, 0x20B, 1, 0,
                     sum(_align(len(row[2]), 512) for row in sections if row[3] & 0x20),
                     sum(_align(len(row[2]), 512) for row in sections if row[3] & 0x40),
                     0, entry_rva, sections[0][1], image_base)
    struct.pack_into("<IIHHHHHHIIIIHHQQQQII", image, opt + 32,
                     4096, 512, 6, 0, 0, 0, 6, 0, 0, rva, headers, 0,
                     3, 0x0160 if reloc_data else 0x0100, 8 * 1024 * 1024, 65536, 1024 * 1024, 4096, 0, 16)
    for index, (address, size) in ((1, import_directory), (3, exception_directory),
                                  (5, reloc_directory), (9, (tls_directory, 40 if tls_directory else 0)),
                                  (12, iat_directory)):
        struct.pack_into("<II", image, opt + 112 + index * 8, address, size)
    for index, (name, address, payload, flags) in enumerate(sections):
        raw = len(image)
        size = _align(len(payload), 512)
        image.extend(payload)
        image.extend(b"\0" * (size - len(payload)))
        struct.pack_into("<8sIIIIIIHHI", image, opt + 240 + index * 40,
                         name.encode().ljust(8, b"\0"), len(payload), address, size, raw,
                         0, 0, 0, 0, flags)
    return bytes(image)
