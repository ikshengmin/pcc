"""Exact immediate pre-x86-application owner for host differential controls.

This helper only removes the new specialization. Earlier reference transforms
can call it on the already restored owner without changing their pinned hashes.
It imports no production module and performs no link or compiler run on import.
"""

import __future__
import ast
import hashlib
from pathlib import Path

from tests.elf_host_exports_preimage import without_host_exports


BASE_OWNER_SHA256 = '3b85ba9dde549bb6b2a1d272657a6efc02c58277d8059c7a77df120871c66929'
OWNER_REVERSE_EDITS = (('def _x86_packed_application_rows(relocations):\n    """Decode application fields without projecting a public relocation record."""\n    for position in range(relocations.offset, relocations.offset + relocations.size, _RELA.size):\n        offset, info, addend = _RELA.unpack_from(relocations.payload, position)\n        yield offset, info >> 32, info & 0xFFFFFFFF, addend\n\n\n', ''), ('        elif (type(machine) is int and machine == EM_X86_64\n                and type(relocations) is _PackedElfRelocations\n                and type(getattr(relocations, "payload", None)) is bytes\n                and type(relocations.offset) is int and type(relocations.size) is int\n                and type(_RELA) is struct.Struct and _RELA.format == "<QQq"):\n            # Keep decoding in its generator: a decoder exception retains its\n            # source, but an application exception must release the iterator.\n            relocation_sources = [relocations]\n            del relocations\n            for relocation_offset, symbol_index, relocation_type, addend in _x86_packed_application_rows(relocation_sources.pop()):\n                # As in the generic loop, decode before releasing a prior row.\n                relocation = None\n                if relocation_type == R_X86_64_NONE:\n                    continue\n                patch = placement.file_offset + relocation_offset\n                place = placement.address + relocation_offset\n                symbol_address, _symbol = resolve_symbol(object_index, symbol_index)\n                if relocation_type == R_X86_64_64:\n                    _write_int(image, patch, symbol_address + addend, 64, False)\n                elif relocation_type in (R_X86_64_PC32, R_X86_64_PLT32):\n                    _write_int(image, patch, symbol_address + addend - place, 32, True)\n                elif relocation_type == R_X86_64_32:\n                    _write_int(image, patch, symbol_address + addend, 32, False)\n                elif relocation_type == R_X86_64_32S:\n                    _write_int(image, patch, symbol_address + addend, 32, True)\n                elif relocation_type in (\n                    R_X86_64_GOTPCREL,\n                    R_X86_64_GOTPCRELX,\n                    R_X86_64_REX_GOTPCRELX,\n                    R_X86_64_GOTTPOFF,\n                ):\n                    identity = _symbol_identity(objects, definitions, object_index, symbol_index)\n                    kind = "tpoff" if relocation_type == R_X86_64_GOTTPOFF else "address"\n                    slot = got_address + got_offsets[(identity, kind)]\n                    _write_int(image, patch, slot + addend - place, 32, True)\n                elif relocation_type == R_X86_64_TPOFF32:\n                    value = tls_offset(object_index, symbol_index)\n                    _write_int(image, patch, value + addend, 32, True)\n                else:\n                    raise ElfError(f"relocation type {relocation_type} has no link rule")\n            del relocation_sources\n', ''))


# Undo only the reviewed 48c46 JUMP26 owner delta. Keep the historical
# scalar-store/application/branch-slot/classification hashes unchanged.
# These exact hunks reconstruct the authenticated immediate pre-JUMP26 owner.
_AARCH64_JUMP26_PREIMAGE_SHA256 = 'ba10325e3646ed8b4f101ce8af15294f39fc7d8f5fdb00988808bf6c5ca81228'
_AARCH64_JUMP26_REVERSE_EDITS = (
    (
        '                # follow every original per-row validation and never resolve symbols.\n'
        '                if type(relocation) is not ElfRelocation or type(relocation.type) is not int:\n'
        '                    classification_candidate[0] = None\n'
        '                elif relocation.type in (283, 282, 311, 312, 541, 542):\n'
        '                    classification_candidate[0] = False\n'
        '    for index, symbol in enumerate(obj.symbols):\n'
        '        if index == 0:\n'
        ,
        '                # follow every original per-row validation and never resolve symbols.\n'
        '                if type(relocation) is not ElfRelocation or type(relocation.type) is not int:\n'
        '                    classification_candidate[0] = None\n'
        '                elif relocation.type in (283, 311, 312, 541, 542):\n'
        '                    classification_candidate[0] = False\n'
        '    for index, symbol in enumerate(obj.symbols):\n'
        '        if index == 0:\n'
    ),
    (
        '    ))\n'
        '\n'
        '\n'
        'def _aarch64_veneer_branch(kind, ordinary_record):\n'
        '    # New JUMP26 support belongs to ordinary integer records. Preserve the\n'
        "    # old CALL26 comparison (including its result's truth conversion) for\n"
        '    # opaque records and integer subclasses, without a second type getter.\n'
        '    if ordinary_record and type(kind) is int:\n'
        '        return kind in (283, 282)\n'
        '    return kind == 283\n'
        '\n'
        '\n'
        'def _staged_classification_is_empty(store, objects, object_index, section_index, section, relocations):\n'
        '    try:\n'
        '        if (type(objects) is not list or type(object_index) is not int\n'
        ,
        '    ))\n'
        '\n'
        '\n'
        'def _staged_classification_is_empty(store, objects, object_index, section_index, section, relocations):\n'
        '    try:\n'
        '        if (type(objects) is not list or type(object_index) is not int\n'
    ),
    (
        '                        # The ordinary loop overwrites its previous row here. Release\n'
        '                        # a preceding custom row at the same boundary before callbacks.\n'
        '                        relocation = None\n'
        '                        if (machine == EM_AARCH64 and relocation_type in (283, 282)\n'
        '                                and section.flags & SHF_EXECINSTR\n'
        '                                and not section.flags & SHF_WRITE):\n'
        '                            slots = branch_slots.get((object_index, section_index))\n'
        ,
        '                        # The ordinary loop overwrites its previous row here. Release\n'
        '                        # a preceding custom row at the same boundary before callbacks.\n'
        '                        relocation = None\n'
        '                        if (machine == EM_AARCH64 and relocation_type == 283\n'
        '                                and section.flags & SHF_EXECINSTR\n'
        '                                and not section.flags & SHF_WRITE):\n'
        '                            slots = branch_slots.get((object_index, section_index))\n'
    ),
    (
        '            relocation_sources = [relocations]\n'
        '            del relocations\n'
        '            for relocation in relocation_sources.pop():\n'
        '                if (machine == EM_AARCH64 and _aarch64_veneer_branch(relocation.type, type(relocation) is ElfRelocation)\n'
        '                        and section.flags & SHF_EXECINSTR\n'
        '                        and not section.flags & SHF_WRITE):\n'
        '                    slots = branch_slots.get((object_index, section_index))\n'
        ,
        '            relocation_sources = [relocations]\n'
        '            del relocations\n'
        '            for relocation in relocation_sources.pop():\n'
        '                if (machine == EM_AARCH64 and relocation.type == 283\n'
        '                        and section.flags & SHF_EXECINSTR\n'
        '                        and not section.flags & SHF_WRITE):\n'
        '                    slots = branch_slots.get((object_index, section_index))\n'
    ),
    (
        '                        object_index, symbol_index\n'
        '                    )\n'
        '                    from .elf_aarch64_relocations import (\n'
        '                        apply_branch26_with_veneer,\n'
        '                        apply_relocation,\n'
        '                        veneer_slot_offset,\n'
        '                    )\n'
        ,
        '                        object_index, symbol_index\n'
        '                    )\n'
        '                    from .elf_aarch64_relocations import (\n'
        '                        apply_call26_with_veneer,\n'
        '                        apply_relocation,\n'
        '                        veneer_slot_offset,\n'
        '                    )\n'
    ),
    (
        '                    elif relocation_type in (549, 551):\n'
        '                        target = tls_offset(object_index, symbol_index)\n'
        '                    try:\n'
        '                        if (relocation_type in (283, 282) and _symbol.type == STT_FUNC\n'
        '                                and _symbol.section_index != SHN_UNDEF\n'
        '                                and not -(1 << 27) <= target + addend - place < (1 << 27)\n'
        '                                and (object_index, section_index) in branch_pools):\n'
        ,
        '                    elif relocation_type in (549, 551):\n'
        '                        target = tls_offset(object_index, symbol_index)\n'
        '                    try:\n'
        '                        if (relocation_type == 283 and _symbol.type == STT_FUNC\n'
        '                                and _symbol.section_index != SHN_UNDEF\n'
        '                                and not -(1 << 27) <= target + addend - place < (1 << 27)\n'
        '                                and (object_index, section_index) in branch_pools):\n'
    ),
    (
        '                            pool_starts = branch_pools[(object_index, section_index)]\n'
        '                            veneer_offsets = tuple(veneer_slot_offset(pool, slot_index)\n'
        '                                                   for pool in pool_starts)\n'
        '                            apply_branch26_with_veneer(image, patch, relocation_type,\n'
        '                                                    target + addend, place,\n'
        '                                                    veneer_offsets)\n'
        '                        else:\n'
        ,
        '                            pool_starts = branch_pools[(object_index, section_index)]\n'
        '                            veneer_offsets = tuple(veneer_slot_offset(pool, slot_index)\n'
        '                                                   for pool in pool_starts)\n'
        '                            apply_call26_with_veneer(image, patch,\n'
        '                                                    target + addend, place,\n'
        '                                                    veneer_offsets)\n'
        '                        else:\n'
    ),
    (
        '                )\n'
        '                if machine == EM_AARCH64:\n'
        '                    from .elf_aarch64_relocations import (\n'
        '                        apply_branch26_with_veneer,\n'
        '                        apply_call26_with_veneer,\n'
        '                        apply_relocation,\n'
        '                        veneer_slot_offset,\n'
        ,
        '                )\n'
        '                if machine == EM_AARCH64:\n'
        '                    from .elf_aarch64_relocations import (\n'
        '                        apply_call26_with_veneer,\n'
        '                        apply_relocation,\n'
        '                        veneer_slot_offset,\n'
    ),
    (
        '                    elif relocation.type in (549, 551):\n'
        '                        target = tls_offset(object_index, relocation.symbol_index)\n'
        '                    try:\n'
        '                        # Decide new eligibility before a custom CALL comparison can\n'
        '                        # mutate its owning field. Custom records/subclass kinds\n'
        '                        # keep the original predicate and constant CALL helper.\n'
        '                        jump26 = (type(relocation) is ElfRelocation\n'
        '                                  and type(relocation.type) is int\n'
        '                                  and relocation.type == 282)\n'
        '                        if (relocation.type == 283 and _symbol.type == STT_FUNC\n'
        '                                and _symbol.section_index != SHN_UNDEF\n'
        '                                and not -(1 << 27) <= target + relocation.addend - place < (1 << 27)\n'
        ,
        '                    elif relocation.type in (549, 551):\n'
        '                        target = tls_offset(object_index, relocation.symbol_index)\n'
        '                    try:\n'
        '                        if (relocation.type == 283 and _symbol.type == STT_FUNC\n'
        '                                and _symbol.section_index != SHN_UNDEF\n'
        '                                and not -(1 << 27) <= target + relocation.addend - place < (1 << 27)\n'
    ),
    (
        '                            veneer_offsets = tuple(veneer_slot_offset(pool, slot_index)\n'
        '                                                   for pool in pool_starts)\n'
        '                            apply_call26_with_veneer(image, patch,\n'
        '                                                    target + relocation.addend, place,\n'
        '                                                    veneer_offsets)\n'
        '                        elif (jump26 and _symbol.type == STT_FUNC\n'
        '                                and _symbol.section_index != SHN_UNDEF\n'
        '                                and not -(1 << 27) <= target + relocation.addend - place < (1 << 27)\n'
        '                                and (object_index, section_index) in branch_pools):\n'
        '                            slot_index = branch_slots[(object_index, section_index)][\n'
        '                                (object_index, relocation.symbol_index, relocation.addend)\n'
        '                            ]\n'
        '                            pool_starts = branch_pools[(object_index, section_index)]\n'
        '                            veneer_offsets = tuple(veneer_slot_offset(pool, slot_index)\n'
        '                                                   for pool in pool_starts)\n'
        '                            apply_branch26_with_veneer(image, patch, 282,\n'
        '                                                    target + relocation.addend, place,\n'
        '                                                    veneer_offsets)\n'
        '                        else:\n'
        ,
        '                            veneer_offsets = tuple(veneer_slot_offset(pool, slot_index)\n'
        '                                                   for pool in pool_starts)\n'
        '                            apply_call26_with_veneer(image, patch,\n'
        '                                                    target + relocation.addend, place,\n'
        '                                                    veneer_offsets)\n'
        '                        else:\n'
    ),
)


def _without_aarch64_jump26_veneers(source):
    # Later host-export plumbing is a separate, complete authenticated delta.
    # Historical owners have no export parameter and retain their old hashes.
    if "export_symbols" in source:
        source = without_host_exports(source)
    if "def _aarch64_veneer_branch(kind, ordinary_record):\n" not in source:
        return source
    for changed, original in reversed(_AARCH64_JUMP26_REVERSE_EDITS):
        assert source.count(changed) == 1
        source = source.replace(changed, original)
    assert hashlib.sha256(source.encode()).hexdigest() == _AARCH64_JUMP26_PREIMAGE_SHA256
    return source


# Restore only the scalar-store delta before applying historical loop inverses.
_SCALAR_STORE_PREIMAGE_SHA256 = 'f2476a2c75257d5a051b5d5af21b82a1d19be53096de28a3267f81cf4680d20f'
_SCALAR_STORE_REVERSE_EDITS = (
    (
        'import sys\n',
        '',
    ),
    (
        (
            '\n'
            'from pcc.unsafe import (\n'
            '    abi_constant, null, ptr_is_null, store_i32, store_i64,\n'
            ')\n'
        ),
        '',
    ),
    (
        (
            '\n'
            '\n'
            'def _native_patch_words_available() -> bool:\n'
            '    try:\n'
            '        return ptr_is_null(null()) != 0\n'
            '    except NotImplementedError:\n'
            '        return False\n'
            '\n'
            '\n'
            '_NATIVE_PATCH_WORDS = _native_patch_words_available()\n'
        ),
        '',
    ),
    (
        (
            '    # Keep the original validation and fallback behavior, including slicing\n'
            '    # outside the image and callbacks on non-exact operands. Native relocation\n'
            '    # sites can write the scalar without allocating an intermediate bytes value.\n'
            '    if (\n'
            '        _NATIVE_PATCH_WORDS and sys.byteorder == "little"\n'
            '        and type(image) is bytearray and type(offset) is int\n'
            '        and type(value) is int and type(bits) is int and type(signed) is bool\n'
            '        and bits in (32, 64) and 0 <= offset <= len(image) - bits // 8\n'
            '    ):\n'
            '        # Pass the rooted object itself, not a retained derived payload pointer.\n'
            '        # store_i32 truncates its signed-i64 operand to the low 32 bits; an\n'
            "        # unsigned high-bit u64 must first use its signed two's-complement form.\n"
            '        if bits == 32:\n'
            '            store_i32(image, abi_constant("object.bytearray.data_offset") + offset, value)\n'
            '        else:\n'
            '            if value >= (1 << 63):\n'
            '                value -= 1 << 64\n'
            '            store_i64(image, abi_constant("object.bytearray.data_offset") + offset, value)\n'
            '        return\n'
        ),
        '',
    ),
)


def _without_native_scalar_writes(source):
    if "_NATIVE_PATCH_WORDS = _native_patch_words_available()\n" not in source:
        return source
    for changed, original in reversed(_SCALAR_STORE_REVERSE_EDITS):
        assert source.count(changed) == 1
        source = source.replace(changed, original)
    assert hashlib.sha256(source.encode()).hexdigest() == _SCALAR_STORE_PREIMAGE_SHA256
    return source


def without_x86_packed_application(source):
    source = _without_aarch64_jump26_veneers(source)
    source = _without_native_scalar_writes(source)
    if hashlib.sha256(source.encode()).hexdigest() == BASE_OWNER_SHA256:
        return source
    for changed, original in reversed(OWNER_REVERSE_EDITS):
        assert source.count(changed) == 1
        source = source.replace(changed, original)
    assert hashlib.sha256(source.encode()).hexdigest() == BASE_OWNER_SHA256
    return source


def original_linker(elf):
    owner = Path(__file__).resolve().parents[1] / "pcc/backend/elf_x86_64.py"
    assert Path(elf.__file__).resolve() == owner.resolve()
    source = without_x86_packed_application(owner.read_text(encoding="utf-8"))
    function = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "_link_static_executable_image")
    body = "".join(source.splitlines(keepends=True)[function.lineno - 1:function.end_lineno])
    namespace = dict(vars(elf))
    exec(compile(body, "<frozen pre-x86 packed application linker>", "exec",
                 flags=__future__.annotations.compiler_flag, dont_inherit=True),
         namespace, namespace)
    return namespace[function.name]
