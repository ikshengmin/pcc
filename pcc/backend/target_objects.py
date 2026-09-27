"""Owned indexed emission and object serialization selected by target ABI."""

from . import BackendUnavailable
from .self_backend_target_match import (
    is_aarch64_darwin_triple, is_aarch64_linux_triple,
    is_x86_64_linux_triple, is_x86_64_windows_triple,
)


def emit_indexed_assembly(module, optimize=False):
    if is_aarch64_darwin_triple(module.triple) or is_aarch64_linux_triple(module.triple):
        from .self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_module
        return emit_aarch64_darwin_indexed_module(module, optimize=optimize)
    if is_x86_64_linux_triple(module.triple) or is_x86_64_windows_triple(module.triple):
        from .self_backend_x86_64_linux import _emit_x86_64_module
        return _emit_x86_64_module("", module=module, windows=is_x86_64_windows_triple(module.triple))
    raise BackendUnavailable("indexed emission target is unavailable: " + module.triple)


def encode_assembly_object(assembly, target):
    if is_aarch64_linux_triple(target) or is_x86_64_linux_triple(target):
        from .owned_elf_link import assemble
        from .elf_x86_64 import emit_relocatable
        return emit_relocatable(assemble(assembly, target))
    if is_x86_64_windows_triple(target):
        from .coff_x86_64 import assemble_object
        return assemble_object(assembly)
    if is_aarch64_darwin_triple(target):
        from .arm64_asm_driver import assemble_file
        from .native_object import encode_native_object_from_sections
        sections, undefined = assemble_file(assembly)
        return encode_native_object_from_sections(sections, undefined=undefined)
    raise BackendUnavailable("object emission target is unavailable: " + target)
