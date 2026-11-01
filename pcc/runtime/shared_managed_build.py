"""Explicit compile/link selection for the experimental shared managed ABI.

No Python/default frontend imports this module. Frontend opt-in callers append
its returned object to their link inputs alongside the ordinary *same* owned
runtime archive. It is not a second allocator, collector, or language runtime.
"""


def managed_runtime_objects(runtime_dir, output_dir, target, *, enabled=False, gc_backend=0):
    """Return optional owned objects; disabled selection has no build side effects.

    Only the first native qualification target/collector is admitted. New lanes
    must preserve envelope extent and run real collection/root/reclaim tests.
    The explicit boolean (not an ambient environment variable) is default off.
    """
    if not enabled:
        return []
    if target != "x86_64-unknown-linux-gnu" or gc_backend != 0:
        raise ValueError("experimental shared managed runtime supports Linux x86-64 GC0 only")
    import os
    from pcc.frontends.python.owned_runtime_build import (
        _build_runtime_module,
        runtime_build_config,
        runtime_ir_passes,
    )
    from pcc.driver.native_provisioning import require_native_provisioning_allowed

    require_native_provisioning_allowed()
    os.makedirs(output_dir, exist_ok=True)
    name = "py_shared_managed"
    config = dict(runtime_build_config())
    _build_runtime_module(runtime_dir, name, target, output_dir,
                          runtime_ir_passes(runtime_dir), config)
    return [os.path.join(output_dir, name + ".o")]
