"""Deterministic, build-only GC pressure instrumentation for owned runtimes.

The default path returns the original source byte-for-byte. Positive settings
produce separately receipted sources; no runtime environment lookup or branch
is introduced into ordinary allocations or safepoints.
"""

import os


ENV = "PCC_RUNTIME_GC_STRESS_EVERY"
MODULE = "freestanding_gc_pressure"
KIND = "managed-publication-pressure-v1"
INSTRUMENTED_MODULES = (
    MODULE, "py_gc_backend", "py_obj", "freestanding_ascii_chars", "freestanding_allocator",
    "freestanding_thread_kernel_pthread", "freestanding_gc_backend0_collector",
)


def interval_from_environment() -> int:
    raw = str(os.environ.get(ENV, "") or "").strip()
    if not raw or raw == "0":
        return 0
    if not raw.isdigit() or int(raw) < 1 or int(raw) > 2147483647:
        raise ValueError(ENV + " must be an integer in [0, 2147483647]")
    return int(raw)


def _replace(text, before, after, *, owner):
    if text.count(before) != 1:
        raise ValueError("GC pressure source shape changed: " + owner)
    return text.replace(before, after, 1)


def _function(source, name, before, after):
    # A narrow textual transform also runs in native pcc1, without delegating
    # parsing to a host ast module. Match an exact enclosing definition and
    # require the complete mutation preimage exactly once inside it.
    lines = source.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.startswith("def " + name + "(")]
    if len(matches) != 1:
        raise ValueError("GC pressure function owner changed: " + name)
    start = matches[0]
    end = start + 1
    while end < len(lines):
        if lines[end].startswith(("def ", "@c_abi", "class ")):
            break
        end += 1
    body = _replace("".join(lines[start:end]), before, after, owner=name)
    return "".join(lines[:start]) + body + "".join(lines[end:])


def instrument_source(source: str, module: str, every: int) -> str:
    if every == 0 or module not in INSTRUMENTED_MODULES:
        return source
    if every < 0 or every > 2147483647:
        raise ValueError("invalid GC pressure interval")
    if module == MODULE:
        return _replace(source, "_PRESSURE_EVERY: i64 = 0", "_PRESSURE_EVERY: i64 = " + str(every), owner=module)

    imports = '''
from pcc.extern import c_int64, c_void, extern
pcc_gc_pressure_note_publication = extern("pcc_gc_pressure_note_publication", (), c_void)
pcc_gc_pressure_image_enter = extern("pcc_gc_pressure_image_enter", (), c_void)
pcc_gc_pressure_image_leave = extern("pcc_gc_pressure_image_leave", (), c_void)
pcc_gc_pressure_poll = extern("pcc_gc_pressure_poll", (), c_int64)
pcc_gc_pressure_collection_completed = extern("pcc_gc_pressure_collection_completed", (), c_void)
pcc_gc_pressure_tracing_completed = extern("pcc_gc_pressure_tracing_completed", (c_int64, c_int64), c_void)
'''
    marker = "__pcc_freestanding__ = True" if "__pcc_freestanding__ = True" in source else "__pcc_runtime_port__ = True"
    source = _replace(source, marker, marker + "\n" + imports, owner=module)
    if module == "py_gc_backend":
        source = _function(source, "pcc_gc_pointer_register",
            "    pcc_gc_reclaim_retired_index_tables_if_alone()\n    return result",
            "    pcc_gc_reclaim_retired_index_tables_if_alone()\n    if result > 0:\n        pcc_gc_pressure_note_publication()\n    return result")
        source = _function(source, "pcc_gc_pointer_register_static_literal",
            "    result: int = pcc_gc_pointer_register(o)",
            "    pcc_gc_pressure_image_enter()\n    result: int = pcc_gc_pointer_register(o)\n    pcc_gc_pressure_image_leave()")
    elif module == "freestanding_allocator":
        source = _function(source, "pcc_gc_granule_object_publish",
            "    if observed == GC_STATE_RESERVED or observed == GC_STATE_LIVE:\n",
            "    if observed == GC_STATE_RESERVED:\n        pcc_gc_pressure_note_publication()\n    if observed == GC_STATE_RESERVED or observed == GC_STATE_LIVE:\n")
        # pcc_gc_alloc publishes fresh object cells here without the general
        # pointer-registration path. Count the same RESERVED-to-LIVE CAS,
        # before its cache seed; an idempotent LIVE answer is not a new object.
        source = _function(source, "pcc_allocator_object_publish_fresh",
            "    if observed != GC_STATE_RESERVED and observed != GC_STATE_LIVE:\n",
            "    if observed == GC_STATE_RESERVED:\n        pcc_gc_pressure_note_publication()\n    if observed != GC_STATE_RESERVED and observed != GC_STATE_LIVE:\n")
    elif module == "freestanding_ascii_chars":
        source = _function(source, "ascii_image_char",
            "    if pcc_gc_pointer_register(out) < 0:\n        return null()",
            "    pcc_gc_pressure_image_enter()\n    pressure_status: i64 = pcc_gc_pointer_register(out)\n    pcc_gc_pressure_image_leave()\n    if pressure_status < 0:\n        return null()")
    elif module == "py_obj":
        source = _function(source, "pcc_gc_safepoint",
            "    pcc_thread_safepoint_gate()\n",
            "    pcc_thread_safepoint_gate()\n    pcc_gc_pressure_poll()\n")
        source = _function(source, "pcc_gc_collect",
            "        pcc_gc_begin_explicit_tracing_collect()\n",
            "        pressure_commits: int = load_i64(global_addr(\"pcc_gc_tracing_finish_commits\"), 0)\n        pressure_had_sweep: int = pcc_gc_sweep_owed()\n        pcc_gc_begin_explicit_tracing_collect()\n")
        source = _function(source, "pcc_gc_collect",
            "        pcc_gc_end_explicit_tracing_collect()\n",
            "        pcc_gc_pressure_tracing_completed(pressure_commits, pressure_had_sweep)\n        pcc_gc_end_explicit_tracing_collect()\n")
    elif module == "freestanding_gc_backend0_collector":
        source = _function(source, "_collect",
            "    if materialized < 0 or tracked <= 0:\n        _finish_subset()",
            "    if materialized < 0 or tracked <= 0:\n        if materialized >= 0 and tracked == 0 and ptr_is_null(pairs) != 0:\n            pcc_gc_pressure_collection_completed()\n        _finish_subset()")
        source = _function(source, "_collect",
            "    free(unreachable)\n    _finish_subset()",
            "    free(unreachable)\n    if ptr_is_null(pairs) != 0:\n        pcc_gc_pressure_collection_completed()\n    _finish_subset()")
    elif module == "freestanding_thread_kernel_pthread":
        source += '''

@c_abi_export("pcc_thread_gc_pressure_safe")
def pcc_thread_gc_pressure_safe() -> int:
    state: int = load_i64(global_addr("pcc_tls_thread_state_py"), 0)
    # Existing registered/no-park/graph/scheduler/container state bits.
    if (state & 562949953421312) == 0:
        return 0
    if (state & (16777215 | 281474959933440 | 281474976710656 | 9007199254740992)) != 0:
        return 0
    if pcc_thread_owns_stopped_world() != 0:
        return 0
    return 1
'''
    return source
