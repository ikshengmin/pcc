"""GC3 copies a young object only while every other mutator is at a gate.

Oldification moves a young object to a new address. Like GC4 relocation it
needs every other mutator parked at an application gate: a mutator stopped
at an ordinary runtime poll may hold a raw address that no root describes,
and that address cannot be healed after the copy. Such a step promotes the
object in place instead. With the other mutator at a gate, the copy still
happens and the registered root is rewritten to it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


NATIVE_SOURCE = r'''
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern int64_t pcc_gc_object_is_known(PyObject *);
extern int64_t pcc_thread_start(void **, void *(*)(void *), void *);
extern int64_t pcc_thread_join(void *, void **);
static PyObject *source;
static PyObject *captured;
static int ready;
static int at_gate;

static void *mutator(void *unused) {
    (void)unused;
    pcc_current_thread_id();
    captured = source;
    __atomic_store_n(&ready, 1, __ATOMIC_RELEASE);
    while (!pcc_thread_stop_requested_acquire()) { }
    if (at_gate) pcc_thread_safepoint_gate();
    else pcc_thread_safepoint();
    return 0;
}

int main(int argc, char **argv) {
    at_gate = argc > 1 && strcmp(argv[1], "gate") == 0;
    if (pcc_gc_backend() != 3 || pcc_threads_enabled() != 1) return 2;
    pcc_current_thread_id();
    /* threshold0=0 pauses automatic collections; the step below is explicit. */
    py_gc_enable();
    py_gc_set_threshold(0, 10, 10);
    source = py_bytes_new("gc3-young-bytes", 15);
    if (!source) return 3;
    int32_t flags = __atomic_load_n(&((PyObjectHeader *)source)->flags, __ATOMIC_ACQUIRE);
    if (pcc_gc_object_is_known(source) != 1 || !(flags & 128)) return 4;
    void *root = pcc_gc_scheduler_root_register_handle(&source);
    if (!root) return 5;
    pcc_gc_unpin(source);
    void *thread = 0;
    if (pcc_thread_start(&thread, mutator, 0) != 0) return 6;
    while (!__atomic_load_n(&ready, __ATOMIC_ACQUIRE)) { }
    if (pcc_stop_the_world() != 0) return 7;
    int64_t gates = pcc_thread_all_stopped_at_gates();
    int64_t before = pcc_gc_telemetry(15);
    int64_t progressed = pcc_gc_step(4096);
    int64_t forwards = pcc_gc_telemetry(15) - before;
    int moved = source != captured;
    int32_t after = __atomic_load_n(&((PyObjectHeader *)source)->flags, __ATOMIC_ACQUIRE);
    pcc_resume_world();
    void *result = 0;
    if (pcc_thread_join(thread, &result) != 0 || result != 0) return 8;
    printf("gate=%d gates=%lld progressed=%lld moved=%d forwards=%lld young=%d old=%d\n",
           at_gate, (long long)gates, (long long)progressed, moved,
           (long long)forwards, (after & 128) != 0, (after & 256) != 0);
    pcc_gc_scheduler_root_unregister_handle(root);
    return 0;
}
'''


@pytest.mark.integration
def test_gc3_copies_young_objects_only_when_mutators_are_at_gates(
    tmp_path: Path, pcc_runtime_archive
) -> None:
    from tests.owned_runtime_c_fixture import link_c_harness

    source = tmp_path / "gc3_oldify_gates.c"
    source.write_text(NATIVE_SOURCE, encoding="utf-8")
    binary = tmp_path / "gc3_oldify_gates"
    link_c_harness(source, binary, runtime_archive=pcc_runtime_archive)
    # The default minor size limit keeps every bytes object out of the young
    # index; this limit admits the probe object to oldification.
    env = dict(os.environ, PCC_GC_BACKEND="3", PCC_GC_MINOR_ALLOC_MAX="4096", PATH="/nonexistent")
    expected = {
        # Stopped at a runtime poll: promoted in place, address unchanged.
        "poll": "gate=0 gates=0 progressed=1 moved=0 forwards=0 young=0 old=1\n",
        # Stopped at an application gate: copied, root rewritten.
        "gate": "gate=1 gates=1 progressed=1 moved=1 forwards=1 young=0 old=1\n",
    }
    for mode, stdout in expected.items():
        ran = subprocess.run([str(binary), mode], capture_output=True, text=True,
                             timeout=60, env=env)
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, stdout, ""), mode
