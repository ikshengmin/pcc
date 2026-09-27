"""Owned IR-to-object boundary shared by runtime builds and the host tool.

Keep the reference emitter and its llvmlite parser out of the native
compiler's dependency closure. This module accepts already prepared IR and
uses only pcc's assembler, object writers and self backend.
"""

from __future__ import annotations

def _contains_module_asm(ir_text: str) -> bool:
    for line in ir_text.splitlines():
        if line.lstrip().startswith('module asm "'):
            return True
    return False


def emit_owned_object(ir_text: str, triple: str) -> bytes:
    from .arm64_asm_driver import assemble_file
    from .native_object import NativeObject
    from .self_backend_dispatch import emit_self_asm, self_backend_target_identity

    if _contains_module_asm(ir_text):
        raise ValueError("module-level assembly is unsupported by the pcc object emitter")

    asm = emit_self_asm(ir_text, triple)
    identity = self_backend_target_identity(triple)
    if identity in ("self-x86_64-linux-v0", "self-aarch64-linux-v0"):
        from .owned_elf_link import assemble as assemble_elf
        from .elf_x86_64 import emit_relocatable

        return emit_relocatable(assemble_elf(asm, triple))
    if identity == "self-x86_64-windows-v0":
        from .coff_x86_64 import assemble_object

        return assemble_object(asm)
    if identity != "self-aarch64-darwin-v0":
        raise ValueError("pcc does not own object emission for " + repr(triple))
    sections, undefined = assemble_file(asm)
    return NativeObject.from_sections(sections, undefined=undefined).to_macho()
