"""Shared helpers and constants for the gc-threading-substrate split.

Consumed via star-import by the gcsubstrate_* modules; re-exported by
the ``test_gc_threading_substrate`` facade so external importers and
pytest node ids stay unchanged.
"""
from pathlib import Path
import os
import signal
import subprocess
import textwrap

import pytest

from pcc.frontends.python.codegen.runtime_abi import (
    FREESTANDING_GC_CROSS_OBJECT_SIGNATURES,
    FREESTANDING_GC_I64_GLOBALS,
    RUNTIME_SIGNATURES,
)
from tests.runtime_build_cache import (
    cached_pcc_python_runtime,
    cached_threaded_pcc_python_runtime,
)


REPO_ROOT = Path(__file__).absolute().parents[2]
RUNTIME_HEADER = REPO_ROOT / "pcc" / "runtime" / "include" / "py_runtime.h"
RUNTIME_DIR = REPO_ROOT / "pcc" / "runtime"
RUNTIME_MAKEFILE = REPO_ROOT / "pcc" / "runtime" / "Makefile"
PY_OBJ_PORT = REPO_ROOT / "pcc" / "runtime" / "py" / "py_obj.py"
THREAD_KERNEL = (
    REPO_ROOT / "pcc" / "runtime" / "py" / "freestanding_thread_kernel.py"
)
THREAD_KERNEL_PTHREAD = (
    REPO_ROOT
    / "pcc" / "runtime"
    / "py"
    / "freestanding_thread_kernel_pthread.py"
)
RUNTIME_LOG_PORT = REPO_ROOT / "pcc" / "runtime" / "py" / "py_runtime_log.py"
PY_OBJ_GC_PORT = (
    REPO_ROOT
    / "pcc" / "runtime"
    / "py"
    / "freestanding_gc_backend0_collector.py"
)
PY_GC_BACKEND_PORT = REPO_ROOT / "pcc" / "runtime" / "py" / "py_gc_backend.py"
PY_GC_BARRIER_DISPATCHER = (
    REPO_ROOT
    / "pcc" / "runtime"
    / "py"
    / "freestanding_gc_barrier_dispatcher.py"
)
PY_GC_GENERATIONAL_SCHEDULER = (
    REPO_ROOT
    / "pcc" / "runtime"
    / "py"
    / "freestanding_gc_generational_scheduler.py"
)
PY_GC_GENERATIONAL_PROMOTION = (
    REPO_ROOT
    / "pcc" / "runtime"
    / "py"
    / "freestanding_gc_generational_promotion.py"
)
PY_GC_OBJECT_SLOTS = (
    REPO_ROOT
    / "pcc" / "runtime"
    / "py"
    / "freestanding_gc_object_slots.py"
)
PY_GC_RELOCATION_SELECTOR = (
    REPO_ROOT
    / "pcc" / "runtime"
    / "py"
    / "freestanding_gc_relocation_selector.py"
)
PY_GC_STATE = (
    REPO_ROOT / "pcc" / "runtime" / "py" / "freestanding_gc_state.py"
)
PY_GC_COMMON_MARK_CYCLE = (
    REPO_ROOT
    / "pcc" / "runtime"
    / "py"
    / "freestanding_gc_common_mark_cycle.py"
)
PY_GC_INCREMENTAL_CONCURRENT_SCHEDULER = (
    REPO_ROOT
    / "pcc" / "runtime"
    / "py"
    / "freestanding_gc_incremental_concurrent_scheduler.py"
)
CORE_HELPERS = REPO_ROOT / "pcc" / "frontends" / "python" / "codegen" / "core_helpers.py"
CONTROL_FLOW_LOWERING = (
    REPO_ROOT / "pcc" / "frontends" / "python" / "codegen" / "control_flow_lowering.py"
)
FOR_LOOP_LOWERING = (
    REPO_ROOT / "pcc" / "frontends" / "python" / "codegen" / "for_loop_lowering.py"
)
USER_FUNCTION_LOWERING = (
    REPO_ROOT / "pcc" / "frontends" / "python" / "codegen" / "user_function_lowering.py"
)


def _build_threaded_runtime(tmp_path: Path) -> Path:
    del tmp_path
    return cached_threaded_pcc_python_runtime()


def _runtime_variant(kind: str, *, threaded: bool) -> tuple[Path, Path]:
    explicit = os.environ.get(
        "PCC_THREADED_RUNTIME_ARCHIVE" if threaded else "PCC_RUNTIME_ARCHIVE"
    )
    if explicit:
        from tests.runtime_fixture_provenance import _verified_test_runtime_archive

        archive, _manifest = _verified_test_runtime_archive(
            explicit, threads=True if threaded else None, runtime_root=RUNTIME_DIR,
        )
        return RUNTIME_DIR, archive
    runtime = (
        cached_threaded_pcc_python_runtime()
        if threaded
        else cached_pcc_python_runtime()
    )
    return runtime, runtime / "libpy_runtime_pcc_py.a"


def _compile_runtime_probe(
    tmp_path: Path,
    *,
    kind: str,
    threaded: bool,
    stem: str,
    source_text: str,
    extra_include_dirs: tuple[Path, ...] = (),
    extra_sources: tuple[Path, ...] = (),
    extra_compile_args: tuple[str, ...] = (),
) -> Path:
    from tests.owned_runtime_c_fixture import link_c_harness_bounded

    if extra_compile_args:
        raise ValueError(
            "owned runtime probe does not support host compiler arguments: "
            + repr(extra_compile_args)
        )
    runtime, archive = _runtime_variant(kind, threaded=threaded)
    source = tmp_path / f"{stem}_{kind}.c"
    executable = tmp_path / f"{stem}_{kind}"
    source.write_text(textwrap.dedent(source_text).lstrip(), encoding="utf-8")
    # This archive owns process entry, libc and TLS. A hosted cc link would
    # mix its _start with the system CRT and omit owned link-time intrinsics.
    build = link_c_harness_bounded(
        source,
        executable,
        runtime_archive=archive,
        extra_include_dirs=(
            *(str(directory) for directory in extra_include_dirs),
            str(runtime / "include"),
            str(runtime / "src"),
        ),
        extra_sources=extra_sources,
        timeout=60,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return executable


THREADING_SURFACE = [
    "pcc_threads_enabled",
    "pcc_current_thread_id",
    "pcc_current_native_thread_token",
    "pcc_refcount_strategy",
    "pcc_thread_safepoint",
    "pcc_thread_stop_requested_acquire",
    "pcc_thread_no_park_enter",
    "pcc_thread_no_park_exit",
    "pcc_thread_no_park_depth",
    "pcc_thread_owns_stopped_world",
    "pcc_thread_registration_waiter_count",
    "pcc_thread_unregister_current",
    "pcc_stop_the_world",
    "pcc_resume_world",
]

__all__ = [
    "Path",
    "os",
    "signal",
    "subprocess",
    "textwrap",
    "pytest",
    "FREESTANDING_GC_CROSS_OBJECT_SIGNATURES",
    "FREESTANDING_GC_I64_GLOBALS",
    "RUNTIME_SIGNATURES",
    "cached_pcc_python_runtime",
    "cached_threaded_pcc_python_runtime",
    "cached_threaded_pcc_python_runtime",
    "REPO_ROOT",
    "RUNTIME_HEADER",
    "RUNTIME_DIR",
    "RUNTIME_MAKEFILE",
    "PY_OBJ_PORT",
    "THREAD_KERNEL",
    "THREAD_KERNEL_PTHREAD",
    "RUNTIME_LOG_PORT",
    "PY_OBJ_GC_PORT",
    "PY_GC_BACKEND_PORT",
    "PY_GC_BARRIER_DISPATCHER",
    "PY_GC_GENERATIONAL_SCHEDULER",
    "PY_GC_GENERATIONAL_PROMOTION",
    "PY_GC_OBJECT_SLOTS",
    "PY_GC_RELOCATION_SELECTOR",
    "PY_GC_STATE",
    "PY_GC_COMMON_MARK_CYCLE",
    "PY_GC_INCREMENTAL_CONCURRENT_SCHEDULER",
    "CORE_HELPERS",
    "CONTROL_FLOW_LOWERING",
    "FOR_LOOP_LOWERING",
    "USER_FUNCTION_LOWERING",
    "_build_threaded_runtime",
    "_runtime_variant",
    "_compile_runtime_probe",
    "THREADING_SURFACE",
    "ALL_GC_KINDS",
    "TRACER_RACE_GC_KINDS",
    "GC4_STOP_PINS_NAMED_OWNER_C",
    "GC4_STOP_MOVES_ROOTED_C",
]

# GC4 moves an object only inside a stopped world, and only when no thread's
# stack or registers name it: the selection pins what the stopped threads'
# words name before it chooses, and pcc_gc_relocate_copy refuses outside a
# stop (6bda7e91). A callback that runs while a runtime frame still holds an
# owner therefore cannot move that owner. This helper is what a callback can
# check instead: inside its own stop, after the selection's conservative pin,
# the owner (read from its static root only after the selection, so the pin
# comes from the frames that held it, not from this one) is pinned, is not in
# the relocation set, and an explicit copy is refused; the operation then
# continues with the owner at the same address.
# The moving half of the same protocol: an object that only a registered
# root names (built out of line, the builder's stack residue scrubbed) is
# not pinned by the stop's selection, an explicit copy moves it, and the
# resume rewrites the registered root (pcc_gc_relocation_settle_stopped), so
# the running code reaches the copy through that root and never holds a
# forwarded address. Returns 0, or a code from 80 naming the failed step.
GC4_STOP_MOVES_ROOTED_C = r"""
            extern int64_t pcc_gc_backend4_relocation_set_add(PyObject *obj);
            static PyObject *gc4_moved_from;

            __attribute__((noinline)) static void gc4_scrub_stack(void) {
                volatile unsigned char words[65536];
                for (int i = 0; i < (int)sizeof(words); i++) words[i] = 0;
            }

            __attribute__((noinline)) static int gc4_stop_moves_rooted(
                PyObject *const *root, int64_t size
            ) {
                if (pcc_stop_the_world() != 0) return 80;
                pcc_gc_reset_relocation_set();
                (void)pcc_gc_select_relocation_set(1000);
                PyObject *obj = *(PyObject *volatile const *)root;
                gc4_moved_from = obj;
                int rc = 0;
                if (pcc_gc_object_is_address_pinned(obj) != 0) {
                    rc = 81;
                } else if (pcc_gc_relocation_set_contains(obj) != 1
                           && pcc_gc_backend4_relocation_set_add(obj) != 1) {
                    rc = 82;
                } else {
                    PyObject *copy = pcc_gc_relocate_copy(obj, size);
                    if (copy == NULL || copy == obj) rc = 83;
                }
                pcc_gc_reset_relocation_set();
                if (pcc_resume_world() != 0) return 84;
                if (rc == 0
                    && *(PyObject *volatile const *)root == gc4_moved_from) {
                    rc = 85;
                }
                return rc;
            }
"""

GC4_STOP_PINS_NAMED_OWNER_C = r"""
            static int gc4_stop_pins_named_owner(
                PyObject *const *owner_root, int64_t size
            ) {
                if (pcc_stop_the_world() != 0) return 0;
                pcc_gc_reset_relocation_set();
                (void)pcc_gc_select_relocation_set(1000);
                PyObject *owner = *(PyObject *volatile const *)owner_root;
                int pinned = pcc_gc_object_is_address_pinned(owner) != 0
                    && pcc_gc_relocation_set_contains(owner) == 0
                    && pcc_gc_relocate_copy(owner, size) == NULL;
                pcc_gc_reset_relocation_set();
                if (pcc_resume_world() != 0) return 0;
                return pinned
                    && *(PyObject *volatile const *)owner_root == owner;
            }
"""

ALL_GC_KINDS = (
    "PCC_GC_KIND_REFCOUNT_CYCLE",
    "PCC_GC_KIND_INCREMENTAL_TRICOLOR",
    "PCC_GC_KIND_CONCURRENT_MARK_SWEEP",
    "PCC_GC_KIND_GENERATIONAL_MINOR_MAJOR",
    "PCC_GC_KIND_COLORED_RELOCATING",
)

TRACER_RACE_GC_KINDS = tuple(
    kind
    for kind in ALL_GC_KINDS
    if kind != "PCC_GC_KIND_COLORED_RELOCATING"
)
