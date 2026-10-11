"""Snapshot-only control for existing real allocator/native CI comparisons.

No new compiler program or invocation is introduced. This helper is used only
inside an existing reference arm; candidate arms retain production guards.
"""

from importlib import import_module


PROJECTION_MODULES = (
    "pcc.backend.self_backend_kernel",
    "pcc.backend.self_backend_precise_stackmaps",
    "pcc.backend.self_backend_verify",
    "pcc.backend.self_backend_aarch64_darwin_regalloc",
    "pcc.backend.self_backend_aarch64_darwin_flow",
    "pcc.backend.self_backend_aarch64_darwin_memory",
    "pcc.backend.self_backend_aarch64_darwin_calls",
    "pcc.backend.self_backend_aarch64_darwin_ops",
    "pcc.backend.self_backend_aarch64_darwin_prologue",
    "pcc.backend.self_backend_aarch64_darwin_returns",
)


def disable_scalar_projections(patch, allocator=None):
    def snapshot_only(arena, record_id, stride, window_offset):
        return False

    for name in PROJECTION_MODULES:
        patch.setattr(import_module(name), "_scalar_window_is_complete", snapshot_only)
    if allocator is not None:
        # old_scan_allocator deliberately has its own copied global namespace.
        patch.setitem(allocator.__globals__, "_scalar_window_is_complete", snapshot_only)
