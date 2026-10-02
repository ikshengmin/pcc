from __future__ import annotations

"""Fresh-process publication of one packed indexed module as a native object."""

import os
import sys

from pcc.extern import c_int64, extern
from pcc.unsafe import darwin_current_rss_bytes

from .native_object import encode_native_object_from_sections
from .self_backend_aarch64_darwin import (
    emit_aarch64_darwin_indexed_module,
    emit_aarch64_darwin_indexed_transport,
)
from .self_backend_indexed_codec import decode_indexed_module_file
from .self_backend_target_match import (is_aarch64_darwin_triple, is_aarch64_linux_triple,
    is_x86_64_linux_triple, is_x86_64_windows_triple)


_pcc_os_heap_in_use_bytes: "extern" = extern(
    "pcc_os_heap_in_use_bytes", (), c_int64
)
_pcc_os_heap_capacity_bytes: "extern" = extern(
    "pcc_os_heap_capacity_bytes", (), c_int64
)


def _debug_phase(phase: str) -> None:
    if str(os.environ.get("PCC_DEBUG_INDEXED_EMIT", "") or "").strip():
        rss_bytes = -1
        heap_in_use_bytes = -1
        heap_capacity_bytes = -1
        try:
            rss_bytes = darwin_current_rss_bytes()
            heap_in_use_bytes = _pcc_os_heap_in_use_bytes()
            heap_capacity_bytes = _pcc_os_heap_capacity_bytes()
        except NotImplementedError:
            # CPython is the source-level oracle; these counters are native
            # runtime diagnostics and deliberately have no host emulation.
            pass
        sys.stderr.write(
            "pcc indexed emit phase="
            + phase
            + " rss_bytes="
            + str(rss_bytes)
            + " heap_in_use_bytes="
            + str(heap_in_use_bytes)
            + " heap_capacity_bytes="
            + str(heap_capacity_bytes)
            + "\n"
        )


def emit_indexed_module_file(
    sidecar_path: str,
    output_path: str,
    artifact_kind: str,
    optimize: bool = False,
) -> None:
    """Decode one frozen pre-stackprep module and atomically publish its PCO."""

    if artifact_kind not in ("ASM", "PCO"):
        raise ValueError("indexed module artifact kind must be ASM or PCO")
    _debug_phase("decode-start")
    module = decode_indexed_module_file(sidecar_path)
    _debug_phase("decode-complete")
    if is_aarch64_linux_triple(module.triple) or is_x86_64_linux_triple(module.triple) or is_x86_64_windows_triple(module.triple):
        stack_map_plans = []
        packed_stack_maps = artifact_kind == "PCO" and is_x86_64_linux_triple(module.triple)
        if is_aarch64_linux_triple(module.triple):
            assembly = emit_aarch64_darwin_indexed_module(module, optimize=optimize)
        else:
            from .self_backend_x86_64_linux import _emit_x86_64_module
            assembly = _emit_x86_64_module(
                "", module=module, windows=is_x86_64_windows_triple(module.triple),
                stack_map_plans_out=stack_map_plans if packed_stack_maps else None,
            )
        if artifact_kind == "ASM":
            payload = assembly.encode("utf-8")
        elif is_x86_64_windows_triple(module.triple):
            from .coff_x86_64 import assemble_object
            payload = assemble_object(assembly)
        else:
            from .target_objects import encode_assembly_object
            payload = encode_assembly_object(
                assembly, module.triple,
                stack_map_plans=stack_map_plans if packed_stack_maps else None,
            )
        temporary = output_path + ".tmp"
        try:
            with open(temporary, "wb") as stream:
                stream.write(payload)
            os.replace(temporary, output_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return
    if (
        module.triple != "unknown-unknown-unknown"
        and not is_aarch64_darwin_triple(module.triple)
    ):
        raise ValueError(
            "indexed module emitter does not support target " + module.triple
        )
    _debug_phase("target-complete")
    temporary = output_path + ".tmp"
    if artifact_kind == "ASM":
        assembly = emit_aarch64_darwin_indexed_module(
            module,
            optimize=optimize,
        )
        _debug_phase("assembly-complete")
        try:
            with open(temporary, "w", encoding="utf-8") as stream:
                stream.write(assembly)
            os.replace(temporary, output_path)
            _debug_phase("publish-complete")
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return
    transport = emit_aarch64_darwin_indexed_transport(
        module,
        optimize=optimize,
        structured_instructions=True,
    )
    _debug_phase("transport-complete")
    sections, undefined = transport.assemble_sections()
    _debug_phase("assemble-complete")
    if transport.encoded_line_records is not None:
        transport.encoded_line_records.close()
    encoded = encode_native_object_from_sections(
        sections,
        undefined=undefined,
    )
    _debug_phase("encode-complete")
    try:
        with open(temporary, "wb") as stream:
            stream.write(encoded)
        os.replace(temporary, output_path)
        _debug_phase("publish-complete")
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


__all__ = ["emit_indexed_module_file"]
