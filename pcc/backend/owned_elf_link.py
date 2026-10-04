"""In-process Linux linker used by host pcc and native pcc stages."""

import os
import hashlib

from .elf_x86_64 import (
    EM_AARCH64, ElfError, link_static_executable, parse_relocatable,
    parse_static_executable,
    read_archive_payloads,
)
from .macho_internal_inputs import read_internal_input_manifest
from .self_backend_target_match import is_aarch64_linux_triple, is_x86_64_linux_triple


def assemble(text: str, target: str):
    if is_aarch64_linux_triple(target):
        from .arm64_elf_driver import assemble_file
    elif is_x86_64_linux_triple(target):
        from .x86_64_asm_driver import assemble_file
    else:
        raise ElfError("unsupported owned ELF target: " + target)
    return assemble_file(text)


def _thread_pointer_object(target: str):
    if is_aarch64_linux_triple(target):
        text = """.section __TEXT,__text,regular,pure_instructions
.globl pcc_linux_set_thread_pointer
pcc_linux_set_thread_pointer:
  msr tpidr_el0, x0
  mov x0, #0
  ret
"""
    else:
        text = """.intel_syntax noprefix
.text
.globl pcc_linux_set_thread_pointer
.type pcc_linux_set_thread_pointer, @function
pcc_linux_set_thread_pointer:
  mov rsi, rdi
  mov edi, 4098
  mov eax, 158
  syscall
  ret
.size pcc_linux_set_thread_pointer, .-pcc_linux_set_thread_pointer
"""
    return assemble(text, target)


def link_inputs(*, target: str, output: str, assembly=(), objects=(),
                archives=(), manifest: str = "", entry: str = "_start",
                map_path: str = "") -> None:
    if not (is_aarch64_linux_triple(target) or is_x86_64_linux_triple(target)):
        raise ElfError("unsupported owned ELF target: " + target)
    archives = list(archives)
    if manifest and (assembly or objects):
        raise ElfError("manifest cannot be mixed with direct internal inputs")
    ordered = read_internal_input_manifest(manifest) if manifest else (
        [("ASM", path) for path in assembly] + [("PCO", path) for path in objects]
    )
    if map_path:
        protected = [output, output + ".pcc-link.tmp", manifest] + [path for _kind, path in ordered] + archives
        if any(path and os.path.realpath(candidate) == os.path.realpath(path)
               for candidate in (map_path, map_path + ".pcc-link.tmp")
               for path in protected):
            raise ElfError("link map output aliases an input or executable")
    inputs = []
    for kind, path in ordered:
        if os.path.abspath(path) == os.path.abspath(output):
            raise ElfError("link output aliases an input")
        if kind == "ASM":
            with open(path, "r", encoding="utf-8") as stream:
                inputs.append(assemble(stream.read(), target))
        else:
            with open(path, "rb") as stream:
                inputs.append(parse_relocatable(stream.read()))
    archive_data = []
    for path in archives:
        with open(path, "rb") as stream:
            archive_data.append(stream.read())
    if not inputs:
        raise ElfError("owned ELF link requires an input")
    expected = EM_AARCH64 if is_aarch64_linux_triple(target) else 62
    if any(obj.machine != expected for obj in inputs):
        raise ElfError("input machine differs from selected ELF target")
    inputs.append(_thread_pointer_object(target))
    from .linux_thread_intrinsics import assembly as thread_assembly
    inputs.append(assemble(thread_assembly(expected == EM_AARCH64), target))
    selections = [] if map_path else None
    image = link_static_executable(inputs, archives=archive_data, entry=entry,
                                   archive_selections=selections)
    parse_static_executable(image)
    map_text = ""
    if map_path:
        # These names come from the very selection that produced the image.
        # Preserve the actual parent archive identity; never invent a C-only
        # archive alias or manufacture a host-linker map.
        payloads = []
        for data in archive_data:
            members = read_archive_payloads(data)
            member_bytes = dict(members)
            if len(member_bytes) != len(members):
                raise ElfError("link map cannot identify duplicate archive member names")
            payloads.append(member_bytes)
        archive_hashes = [hashlib.sha256(data).hexdigest() for data in archive_data]
        lines = ["# pcc owned ELF link map v1", "# target " + target, "# entry " + entry,
                 "# image sha256=" + hashlib.sha256(image).hexdigest()]
        for index, member in selections:
            lines.append(str(archives[index]) + "(" + member + ")"
                         + " archive_sha256=" + archive_hashes[index]
                         + " member_sha256=" + hashlib.sha256(payloads[index][member]).hexdigest())
        map_text = "\n".join(lines) + "\n"
    temporary = output + ".pcc-link.tmp"
    try:
        with open(temporary, "wb") as stream:
            stream.write(image)
        os.chmod(temporary, 0o755)
        os.replace(temporary, output)
        if map_path:
            map_temporary = map_path + ".pcc-link.tmp"
            try:
                with open(map_temporary, "w", encoding="utf-8") as stream:
                    stream.write(map_text)
                os.replace(map_temporary, map_path)
            finally:
                if os.path.exists(map_temporary):
                    os.unlink(map_temporary)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
