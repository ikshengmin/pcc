"""Explicit Rust target contracts backed by registered PCC owned emitters."""
from __future__ import annotations

from dataclasses import dataclass

from .syntax import RustError

DEFAULT_TARGET = "x86_64-unknown-linux-gnu"
# Both admitted ABIs are little-endian, use 64-bit AS0 pointers, and preserve
# Rust's explicit i32/i64 widths. These layouts describe the admitted scalar
# subset; they are not a promise of rustc aggregate layout or usize support.
LINUX_LAYOUT = "e-m:e-p:64:64-i64:64-n8:16:32:64-S128"
DARWIN_LAYOUT = "e-m:o-p:64:64-i64:64-i128:128-n32:64-S128"


@dataclass(frozen=True)
class RustTarget:
    triple: str
    data_layout: str
    identity: str
    object_format: str


def resolve_target(triple: str) -> RustTarget:
    if type(triple) is not str or not triple or triple.strip() != triple:
        raise RustError("invalid Rust target_triple: expected a nonempty target string without surrounding whitespace")
    from pcc.backend import BackendUnavailable
    from pcc.backend.self_backend_targets import resolve_self_backend_target

    normalized = triple.lower()
    try:
        target = resolve_self_backend_target(normalized)
    except BackendUnavailable as exc:
        raise RustError("unsupported Rust target_triple: " + repr(triple)) from exc
    parts = normalized.split("-")
    if target.identity == "self-x86_64-linux-v0":
        # A backend family match alone does not establish the frontend data
        # model: gnux32 uses 32-bit pointers despite its x86_64 architecture.
        # Admit only the qualified LP64 environments, plus the backend's
        # unambiguous compact/no-environment aliases. Never guess an ABI.
        if len(parts) == 4 and parts[3] not in {"gnu", "musl"}:
            raise RustError("Rust target environment is not yet qualified: " + repr(triple))
        return RustTarget(normalized, LINUX_LAYOUT, target.identity, "elf")
    if target.identity == "self-aarch64-darwin-v0":
        if len(parts) != 3:
            raise RustError("Rust Darwin target environment is not yet qualified: " + repr(triple))
        return RustTarget(normalized, DARWIN_LAYOUT, target.identity, "macho")
    raise RustError("Rust frontend target is not yet qualified: " + repr(triple)
                    + "; supported target families are x86_64 Linux and arm64 Apple Darwin")
