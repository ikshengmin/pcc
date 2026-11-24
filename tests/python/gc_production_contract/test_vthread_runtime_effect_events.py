"""Production vthread transitions emit a checkable root/effect event path."""

from __future__ import annotations

from pcc.diagnostics.contracts.runtime_effects import (
    ProductionVThreadEvent,
    ProductionVThreadEventKind,
    RuntimeEffect,
    check_production_vthread_events,
    compose_production_vthread_event_effects,
)


from tests.python.gc_production_contract.native_abi_probe import RUNTIME_DIR, run_native_abi_probe


_SOURCE = r"""
#define _POSIX_C_SOURCE 200809L
#include "py_runtime.h"
#include "native_scheduler_roots.h"

#include <poll.h>
#include <stdint.h>
#include <stdlib.h>
#include <unistd.h>

static PyObject *new_vthread(void) {
    return py_virtual_thread_new(py_None);
}

static int finish_ready(PyObject *expected) {
    PyObject *ready = py_virtual_thread_poll_ready();
    if (ready == NULL || ready != expected) return 0;
    int64_t completed = py_virtual_thread_complete(ready, py_None);
    pcc_gc_release(ready);
    return completed == 0;
}

static int exercise_path(void) {
    if (py_virtual_thread_effect_reset() != 0) return 0;
    PyObject *vt = new_vthread();
    if (vt == NULL) return 0;

    if (py_virtual_thread_start(vt) != 0 || !finish_ready(vt)) return 0;
    pcc_gc_release(vt);

    /* Manual poll takes a running claim. Finish that task before starting
     * the next sequence, just as the normal scheduler ends its owned step. */
    vt = new_vthread();
    if (vt == NULL || py_virtual_thread_park(vt) != 0) return 0;
    if (py_virtual_thread_unpark(vt) != 0 || !finish_ready(vt)) return 0;

    pcc_gc_release(vt);
    vt = new_vthread();
    if (vt == NULL || py_virtual_thread_sleep(vt, 2) != 0) return 0;
    (void)pcc_gc_collect(0);
    if (pcc_runtime_sleep_ns(6000000) != 0) return 0;
    if (py_virtual_thread_poll_timers() != 1 || !finish_ready(vt)) return 0;

    pcc_gc_release(vt);
    vt = new_vthread();
    if (vt == NULL) return 0;
    int fds[2];
    if (pipe(fds) != 0) return 0;
    if (py_virtual_thread_block_on_fd(vt, fds[0], POLLIN, -1) != 0) return 0;
    (void)pcc_gc_collect(0);
    if (write(fds[1], "r", 1) != 1) return 0;
    if (py_virtual_thread_poll_io(0) != 1 || !finish_ready(vt)) return 0;
    char consumed = 0;
    if (read(fds[0], &consumed, 1) != 1 || consumed != 'r') return 0;

    pcc_gc_release(vt);
    vt = new_vthread();
    if (vt == NULL || py_virtual_thread_sleep(vt, 1000) != 0) return 0;
    if (py_virtual_thread_cancel_timer(vt) != 1) return 0;
    if (py_virtual_thread_block_on_fd(vt, fds[0], POLLIN, -1) != 0) return 0;
    if (py_virtual_thread_complete(vt, py_None) != 0) return 0;
    pcc_gc_release(vt);
    close(fds[0]);
    close(fds[1]);

    return py_virtual_thread_ready_count() == 0
        && py_virtual_thread_timer_count() == 0
        && py_virtual_thread_io_wait_count() == 0
        && active_vthread_roots() == 0;
}

int64_t pcc_test_gc_probe(void) {
    return exercise_path() ? 0 : 1;
}
"""

_READBACK = """from pcc.extern import c_int64
count = extern('py_virtual_thread_effect_count', (), c_int64)
dropped = extern('py_virtual_thread_effect_dropped', (), c_int64)
kind = extern('py_virtual_thread_effect_kind_at', (c_int64,), c_int64)
detail = extern('py_virtual_thread_effect_detail_at', (c_int64,), c_int64)
delta = extern('py_virtual_thread_effect_root_delta_at', (c_int64,), c_int64)
state = extern('py_virtual_thread_effect_state_at', (c_int64,), c_int64)
n = count()
print(n, dropped())
for i in range(n):
    print(kind(i), detail(i), delta(i), state(i))
"""


def test_production_vthread_effect_path_is_balanced(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    run = run_native_abi_probe(
        tmp_path, _SOURCE, pcc_runtime_archive, python_program_compiler,
        readback=_READBACK, run_env={"PCC_VTHREAD_IO_BACKEND": "poll"},
    )
    assert run.returncode == 0, run.stderr
    lines = run.stdout.splitlines()
    assert lines[0] == "0", run.stdout
    count, dropped = map(int, lines[1].split())
    assert count == len(lines) - 2 and count > 0
    assert dropped == 0
    events = []
    for line in lines[2:]:
        kind, detail, root_delta, state = map(int, line.split())
        events.append(
            ProductionVThreadEvent(
                ProductionVThreadEventKind(kind), detail, root_delta, state
            )
        )
    assert check_production_vthread_events(events) == ()
    observed = {event.kind for event in events}
    assert {
        ProductionVThreadEventKind.ROOT_ENTER,
        ProductionVThreadEventKind.ROOT_LEAVE,
        ProductionVThreadEventKind.START,
        ProductionVThreadEventKind.PARK,
        ProductionVThreadEventKind.UNPARK,
        ProductionVThreadEventKind.RESUME,
        ProductionVThreadEventKind.TIMER_PARK,
        ProductionVThreadEventKind.TIMER_WAKE,
        ProductionVThreadEventKind.IO_PARK,
        ProductionVThreadEventKind.IO_WAKE,
        ProductionVThreadEventKind.CANCEL_TIMER,
        ProductionVThreadEventKind.CANCEL_IO,
        ProductionVThreadEventKind.COMPLETE,
    }.issubset(observed)
    effects = compose_production_vthread_event_effects(events)
    assert {
        RuntimeEffect.SCHEDULER_ROOT_ENTER,
        RuntimeEffect.SCHEDULER_ROOT_LEAVE,
        RuntimeEffect.VTHREAD_START,
        RuntimeEffect.VTHREAD_PARK,
        RuntimeEffect.VTHREAD_RESUME,
        RuntimeEffect.VTHREAD_SLEEP,
        RuntimeEffect.VTHREAD_BLOCK_IO,
        RuntimeEffect.VTHREAD_CANCEL,
        RuntimeEffect.VTHREAD_COMPLETE,
    }.issubset(effects)


def test_production_event_abi_is_bounded_and_public() -> None:
    header = (RUNTIME_DIR / "include" / "py_runtime.h").read_text(
        encoding="utf-8"
    )
    assert "py_virtual_thread_effect_count" in header
    assert "PCC_VTHREAD_EFFECT_COMPLETE" in header
