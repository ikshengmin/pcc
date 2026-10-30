"""Current JUMP26 semantics on the independent frozen generic-application oracle.

The historical literal and owner hash stay untouched. These five exact forward
edits add only the separately qualified branch contract, not packed application
or other optimizations. Import performs no PCC import, link, or compiler run.
"""
import __future__
import hashlib
import sys

from tests.elf_packed_application_preimage import ORIGINAL_LINK_IMAGE_SOURCE

HISTORICAL_FUNCTION_SHA256 = 'a613a9dee2637ea20e5e464a1a5f5149cf51c42eeaeaa73d23bda54810cba73f'
CURRENT_FUNCTION_SHA256 = 'bc41958cd6ce34356bd167ffa3cc7e689a7c71044225f625cc9f4c6749571279'
JUMP26_FORWARD_EDITS = (
    (
        'if (machine == EM_AARCH64 and relocation_type == 283\n'
        ,
        'if (machine == EM_AARCH64 and relocation_type in (283, 282)\n'
    ),
    (
        'if (machine == EM_AARCH64 and relocation.type == 283\n'
        ,
        'if (machine == EM_AARCH64 and _reference_veneer_branch(relocation.type, type(relocation) is ElfRelocation)\n'
    ),
    (
        '                from .elf_aarch64_relocations import (\n'
        '                    apply_call26_with_veneer,\n'
        ,
        '                from .elf_aarch64_relocations import (\n'
        '                    apply_branch26_with_veneer,\n'
        '                    apply_call26_with_veneer,\n'
    ),
    (
        '                try:\n'
        '                    if (relocation.type == 283 and _symbol.type == STT_FUNC\n'
        ,
        '                try:\n'
        '                    jump26 = (type(relocation) is ElfRelocation\n'
        '                              and type(relocation.type) is int\n'
        '                              and relocation.type == 282)\n'
        '                    if (relocation.type == 283 and _symbol.type == STT_FUNC\n'
    ),
    (
        '                        apply_call26_with_veneer(image, patch,\n'
        '                                                target + relocation.addend, place,\n'
        '                                                veneer_offsets)\n'
        '                    else:\n'
        ,
        '                        apply_call26_with_veneer(image, patch,\n'
        '                                                target + relocation.addend, place,\n'
        '                                                veneer_offsets)\n'
        '                    elif (jump26 and _symbol.type == STT_FUNC\n'
        '                            and _symbol.section_index != SHN_UNDEF\n'
        '                            and not -(1 << 27) <= target + relocation.addend - place < (1 << 27)\n'
        '                            and (object_index, section_index) in branch_pools):\n'
        '                        slot_index = branch_slots[(object_index, section_index)][\n'
        '                            (object_index, relocation.symbol_index, relocation.addend)\n'
        '                        ]\n'
        '                        pool_starts = branch_pools[(object_index, section_index)]\n'
        '                        veneer_offsets = tuple(veneer_slot_offset(pool, slot_index)\n'
        '                                               for pool in pool_starts)\n'
        '                        apply_branch26_with_veneer(image, patch, 282,\n'
        '                                                 target + relocation.addend, place,\n'
        '                                                 veneer_offsets)\n'
        '                    else:\n'
    ),
)


def current_jump26_source(source):
    assert hashlib.sha256(source.encode()).hexdigest() == HISTORICAL_FUNCTION_SHA256
    for before, after in JUMP26_FORWARD_EDITS:
        assert source.count(before) == 1
        source = source.replace(before, after)
    assert hashlib.sha256(source.encode()).hexdigest() == CURRENT_FUNCTION_SHA256
    return source


def _reference_veneer_branch(kind, ordinary_record):
    # Keep the old CALL comparison/truth conversion for opaque/subclass rows.
    if ordinary_record and type(kind) is int:
        return kind in (283, 282)
    return kind == 283


def original_linker(elf):
    source = current_jump26_source(ORIGINAL_LINK_IMAGE_SOURCE)
    namespace = dict(vars(elf), _reference_veneer_branch=_reference_veneer_branch)
    code = compile(source, "<frozen generic application plus JUMP26>", "exec",
                   flags=__future__.annotations.compiler_flag, dont_inherit=True,
                   optimize=sys.flags.optimize)
    exec(code, namespace)
    return namespace["_link_static_executable_image"]
