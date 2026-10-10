"""Owned indexed emission and object serialization selected by target ABI."""

from . import BackendUnavailable
from .self_backend_target_match import (
    is_aarch64_darwin_triple, is_aarch64_linux_triple,
    is_x86_64_linux_triple, is_x86_64_windows_triple,
)


def emit_indexed_assembly(
    module, optimize=False, *, stack_map_plans_out=None, phase_timing=None,
):
    if is_aarch64_darwin_triple(module.triple) or is_aarch64_linux_triple(module.triple):
        from .self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_module
        return emit_aarch64_darwin_indexed_module(
            module, optimize=optimize, phase_timing=phase_timing,
        )
    if is_x86_64_linux_triple(module.triple) or is_x86_64_windows_triple(module.triple):
        from .self_backend_x86_64_linux import _emit_x86_64_module
        return _emit_x86_64_module(
            "", module=module, windows=is_x86_64_windows_triple(module.triple),
            stack_map_plans_out=stack_map_plans_out, phase_timing=phase_timing,
        )
    raise BackendUnavailable("indexed emission target is unavailable: " + module.triple)


def encode_assembly_object(
    assembly, target, *, stack_map_plans=None, consume_stack_map_plans=False,
    phase_timing=None,
):
    if stack_map_plans is not None:
        if not is_x86_64_linux_triple(target):
            raise BackendUnavailable("packed stack maps require the Linux x86 target")
        from .x86_64_asm_driver import assemble_file_with_stack_maps
        from .self_backend_x86_64_linux import _asm_symbol, _block_label
        from .elf_x86_64 import emit_relocatable
        if phase_timing is None:
            return emit_relocatable(assemble_file_with_stack_maps(
                assembly, stack_map_plans,
                function_symbol=_asm_symbol, block_label=_block_label,
                consume_stack_map_plans=consume_stack_map_plans,
            ))
        started = phase_timing.start()
        obj = assemble_file_with_stack_maps(
            assembly, stack_map_plans,
            function_symbol=_asm_symbol, block_label=_block_label,
            consume_stack_map_plans=consume_stack_map_plans,
        )
        phase_timing.add(9, started)
        started = phase_timing.start()
        encoded = emit_relocatable(obj)
        phase_timing.add(10, started)
        return encoded
    if is_aarch64_linux_triple(target) or is_x86_64_linux_triple(target):
        from .owned_elf_link import assemble
        from .elf_x86_64 import emit_relocatable
        if phase_timing is None:
            return emit_relocatable(assemble(assembly, target))
        started = phase_timing.start()
        obj = assemble(assembly, target)
        phase_timing.add(9, started)
        started = phase_timing.start()
        encoded = emit_relocatable(obj)
        phase_timing.add(10, started)
        return encoded
    if is_x86_64_windows_triple(target):
        from .coff_x86_64 import assemble_object
        return assemble_object(assembly, phase_timing=phase_timing)
    if is_aarch64_darwin_triple(target):
        from .arm64_asm_driver import assemble_file
        from .native_object import encode_native_object_from_sections
        started = phase_timing.start() if phase_timing is not None else 0
        sections, undefined = assemble_file(assembly)
        if phase_timing is not None:
            phase_timing.add(9, started)
            started = phase_timing.start()
        encoded = encode_native_object_from_sections(sections, undefined=undefined)
        if phase_timing is not None:
            phase_timing.add(10, started)
        return encoded
    raise BackendUnavailable("object emission target is unavailable: " + target)
