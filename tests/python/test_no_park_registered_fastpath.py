"""No-park entry reuses registration without weakening admission or nesting."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / "pcc/runtime/py/freestanding_thread_kernel_pthread.py"
REGISTERED = 1 << 49
DEPTH_MASK = (1 << 24) - 1
STATE = "pcc_tls_thread_state_py"


class AdmissionFailure(Exception):
    pass


def _entry_model(initial: int, *, registration_state: int | None = None,
                 registration_result: int = 7):
    """Execute the production entry body; observe the registration boundary."""
    state = {STATE: initial}
    calls = []

    def register():
        calls.append("register")
        if registration_result != 0:
            state[STATE] = (
                initial | REGISTERED
                if registration_state is None else registration_state
            )
        return registration_result

    def abort():
        raise AdmissionFailure

    tree = ast.parse(KERNEL.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name == "pcc_thread_no_park_enter"]
    assert len(nodes) == 1
    nodes[0].decorator_list = []
    namespace = {
        "global_addr": lambda name: name,
        "load_i64": lambda slot, offset: state[slot],
        "store_i64": lambda slot, offset, value: state.__setitem__(slot, value),
        "pcc_current_thread_id": register,
        "pcc_platform_abort": abort,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(nodes, [])), str(KERNEL), "exec"), namespace)
    return namespace["pcc_thread_no_park_enter"], state, calls


@pytest.mark.parametrize("other_bits", (0, 3 << 24, 1 << 48))
def test_registered_outer_entry_preserves_flags_without_id_lookup(other_bits):
    initial = REGISTERED | other_bits
    enter, state, calls = _entry_model(initial)
    enter()
    assert state[STATE] == initial + 1
    assert calls == []


@pytest.mark.parametrize("depth", (1, 2, DEPTH_MASK - 1))
def test_nested_entry_preserves_existing_admission(depth):
    initial = REGISTERED | depth
    enter, state, calls = _entry_model(initial)
    enter()
    assert state[STATE] == initial + 1
    assert calls == []


@pytest.mark.parametrize("registered", (0, REGISTERED))
def test_overflow_still_fails_before_state_changes_or_registration(registered):
    initial = registered | DEPTH_MASK
    enter, state, calls = _entry_model(initial)
    with pytest.raises(AdmissionFailure):
        enter()
    assert state[STATE] == initial
    assert calls == []


def test_cold_entry_reloads_registration_state_and_next_entry_reuses_it():
    # Admission may update more than the ID: retain the actual post-admission
    # state instead of overwriting it with the pre-registration word.
    admitted = REGISTERED | (1 << 50)
    enter, state, calls = _entry_model(0, registration_state=admitted)
    enter()
    assert state[STATE] == admitted + 1
    assert calls == ["register"]
    enter()
    assert state[STATE] == admitted + 2
    assert calls == ["register"]


def test_failed_cold_admission_does_not_create_no_park_depth():
    enter, state, calls = _entry_model(0, registration_result=0)
    with pytest.raises(AdmissionFailure):
        enter()
    assert state[STATE] == 0
    assert calls == ["register"]


def test_native_registered_entry_unregisters_and_readmits(tmp_path):
    from _gc_substrate_common import _compile_runtime_probe

    executable = _compile_runtime_probe(
        tmp_path,
        kind="pcc_python",
        threaded=True,
        stem="no_park_registered_readmission",
        source_text=r'''
            #include "py_internal.h"
            #include <stdint.h>
            #include <stdio.h>

            extern int64_t pcc_thread_registered(void);

            int main(void) {
                int64_t previous = 0;
                pcc_thread_unregister_current();
                for (int cycle = 0; cycle < 3; cycle++) {
                    if (pcc_thread_registered() != 0) return 2;
                    pcc_thread_no_park_enter();
                    if (pcc_thread_registered() != 1) return 3;
                    if (pcc_thread_no_park_depth() != 1) return 4;
                    int64_t current = pcc_current_thread_id();
                    if (current <= 0 || current == previous) return 5;
                    pcc_thread_no_park_exit();
                    for (int repeat = 0; repeat < 64; repeat++) {
                        pcc_thread_no_park_enter();
                        pcc_thread_no_park_enter();
                        if (pcc_thread_no_park_depth() != 2) return 6;
                        pcc_thread_safepoint();
                        if (pcc_thread_no_park_depth() != 2) return 7;
                        pcc_thread_no_park_exit();
                        if (pcc_thread_no_park_depth() != 1) return 8;
                        pcc_thread_no_park_exit();
                        if (pcc_thread_no_park_depth() != 0) return 9;
                        if (pcc_current_thread_id() != current) return 10;
                    }
                    previous = current;
                    pcc_thread_unregister_current();
                    if (pcc_thread_registered() != 0) return 11;
                }
                puts("NO_PARK_REGISTERED_OK");
                return 0;
            }
        ''',
    )
    run = subprocess.run(
        [str(executable)],
        env={**os.environ, "PCC_GC_BACKEND": "0"},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout == "NO_PARK_REGISTERED_OK\n"


def test_native_no_park_keeps_stw_and_newcomer_admission_contract(tmp_path):
    # Reuse the existing state-driven raw-pthread fixture: nested no-park
    # delays an active stop until outer exit, and newcomers wait for resume.
    from gcsubstrate_a_threading_no_park import (
        test_thread_no_park_and_stopped_world_newcomers_use_real_pthreads,
    )

    test_thread_no_park_and_stopped_world_newcomers_use_real_pthreads(
        tmp_path, "pcc_python"
    )
