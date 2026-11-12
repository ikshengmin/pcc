"""Shared, fail-closed function-definition linkage protocol.

``external_odr`` means one externally observable function address across
translation units. It is emitted as IR ``weak_odr``; it is not C inline linkage.
Unmarked ParsedFunction instances retain the historical is_global contract.
"""
from . import BackendUnavailable


def supports_external_odr_function_definitions(target_triple: str) -> bool:
    from .self_backend_target_match import (
        is_aarch64_darwin_triple,
        is_x86_64_linux_triple,
    )
    return is_x86_64_linux_triple(target_triple) or is_aarch64_darwin_triple(target_triple)


def definition_linkage_from_ir(linkage: str) -> str:
    if linkage in ("", "external"):
        return "external"
    if linkage == "internal":
        return "internal"
    if linkage in ("weak_odr", "weak"):
        # Both lower to one weak symbol definition (Mach-O N_WEAK_DEF, ELF
        # STB_WEAK); a strong definition elsewhere wins at link time. The
        # IR inliner refuses to inline either, so a C weak body never
        # replaces a call another definition may win.
        return "external_odr"
    raise BackendUnavailable("unsupported function definition linkage: " + linkage)


def function_definition_linkage(function) -> str:
    linkage = function.definition_linkage
    if linkage == "":
        linkage = "external" if function.is_global else "internal"
    if linkage not in ("external", "internal", "external_odr"):
        raise BackendUnavailable("unsupported function definition linkage: " + linkage)
    if function.is_global != (linkage != "internal"):
        raise BackendUnavailable("inconsistent function definition linkage: " + function.name)
    return linkage


def checked_function_definition_linkage(function, target_triple: str) -> str:
    linkage = function_definition_linkage(function)
    if linkage == "external_odr" and not supports_external_odr_function_definitions(target_triple):
        raise BackendUnavailable("external_odr function definitions unavailable for target: " + target_triple)
    return linkage
