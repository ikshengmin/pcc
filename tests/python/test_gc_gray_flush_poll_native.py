"""A real generated allocation-free loop publishes its short gray TLS tail.

This is the GC1/2 poll/publication boundary from
docs/plans/p0-threaded-concurrent-gc-runtime.md, not collector termination or
O(live) acceptance. Queue entries are opaque tokens, no collector is started,
and the consumer drives a synthetic level-triggered publication request.
"""
from __future__ import annotations

import ast
import os
import platform
import re

import pytest

from pcc.frontends.python.pipeline import compile_python
from pcc.ir.optimization.ir_mutator import MutableModule
from pcc.ir.optimization.inline import _blocks_on_cycles
from tests.python.process_timeout import run_process_group_timeout


PROGRAM = '''from pcc.extern import c_abi_typed_export, c_int64, c_ptr, c_rawptr, c_void, extern
from pcc.unsafe import (
    atomic_load_i32, atomic_store_i32, atomic_store_i64,
    define_global_i32, function_addr, global_addr, int_to_ptr,
    load_ptr, null, ptr_to_int, stack_alloc,
)

start = extern("pcc_thread_start", (c_ptr, c_ptr, c_ptr), c_int64)
join = extern("pcc_thread_join", (c_ptr, c_ptr), c_int64)
clock = extern("pcc_runtime_monotonic_us", (), c_int64)
note = extern("pcc_gc_gray_worklist_note", (c_ptr,), c_void)
take = extern("pcc_gc_gray_worklist_take_locked", (), c_rawptr)
lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
configure = extern("pcc_gc_config_ensure", (), c_int64)
disable = extern("py_gc_disable", (), c_void)

define_global_i32("gray_poll_entered", 0)
define_global_i32("gray_poll_release", 0)
define_global_i32("gray_poll_exited", 0)
define_global_i32("gray_poll_saw_stop", 0)

@c_abi_typed_export("generated_gray_tail_worker", "ptr", ("ptr",))
def generated_gray_tail_worker(arg):
    # One token is shorter than a batch. After entry the producer has no
    # explicit safepoint, flush, park, collection or ordinary loop call.
    note(int_to_ptr(40))
    atomic_store_i32(global_addr("gray_poll_entered"), 0, 1, "release")
    while atomic_load_i32(global_addr("gray_poll_release"), 0, "relaxed") == 0:
        if atomic_load_i32(global_addr("pcc_thread_stop_requested"), 0, "relaxed") != 0:
            atomic_store_i32(global_addr("gray_poll_saw_stop"), 0, 1, "release")
    atomic_store_i32(global_addr("gray_poll_exited"), 0, 1, "release")
    return null()

@c_abi_typed_export("generated_gray_tail_probe", "i64", ("i64",))
def run(backend: int) -> int:
    # Configure quiescent raw worklist controls without starting CMS. The
    # selected backend changes only after the previous producer is joined;
    # c_int64 externs have Python-int semantic types. The typed C ABI and
    # generated-call audit below prove scalar control without managed work.
    if atomic_load_i32(global_addr("pcc_gc_config_initialized"), 0, "acquire") != 1:
        return 16
    if atomic_load_i32(global_addr("py_gc_enabled"), 0, "acquire") != 0:
        return 17
    if atomic_load_i32(global_addr("pcc_gc_cms_worker_started"), 0, "acquire") != 0:
        return 18
    if atomic_load_i32(global_addr("pcc_gc_cms_worker_starts"), 0, "acquire") != 0:
        return 19
    if atomic_load_i32(global_addr("pcc_thread_stop_requested"), 0, "acquire") != 0:
        return 1
    atomic_store_i32(global_addr("pcc_gc_nonmoving_count_free"), 0, 1, "release")
    atomic_store_i32(global_addr("pcc_gc_backend_selected"), 0, backend, "release")
    atomic_store_i64(global_addr("pcc_gc_tracing_cycle_epoch"), 0, 17, "release")
    atomic_store_i32(global_addr("pcc_gc_gray_worklist_flush_requested"), 0, 0, "release")
    atomic_store_i32(global_addr("gray_poll_entered"), 0, 0, "release")
    atomic_store_i32(global_addr("gray_poll_release"), 0, 0, "release")
    atomic_store_i32(global_addr("gray_poll_exited"), 0, 0, "release")
    atomic_store_i32(global_addr("gray_poll_saw_stop"), 0, 0, "release")
    handle = stack_alloc(8)
    if start(handle, function_addr("generated_gray_tail_worker"), null()) != 0:
        atomic_store_i32(global_addr("pcc_gc_backend_selected"), 0, 0, "release")
        atomic_store_i32(global_addr("pcc_gc_nonmoving_count_free"), 0, 0, "release")
        return 2
    status: int = 0
    deadline: int = clock() + 5000000
    while atomic_load_i32(global_addr("gray_poll_entered"), 0, "acquire") == 0:
        if clock() >= deadline:
            status = 3
            break
    token = null()
    if status == 0:
        lock()
        token = take()
        unlock()
        if ptr_to_int(token) != 0:
            status = 4
    if status == 0:
        atomic_store_i32(global_addr("pcc_gc_gray_worklist_flush_requested"), 0, 1, "release")
        deadline = clock() + 5000000
        while ptr_to_int(token) == 0:
            if atomic_load_i32(global_addr("pcc_thread_stop_requested"), 0, "acquire") != 0:
                status = 5
                break
            if atomic_load_i32(global_addr("gray_poll_exited"), 0, "acquire") != 0:
                status = 6
                break
            lock()
            token = take()
            unlock()
            if clock() >= deadline:
                status = 7
                break
        if status == 0 and ptr_to_int(token) != 40:
            status = 8
        if atomic_load_i32(global_addr("pcc_gc_gray_worklist_flush_requested"), 0, "acquire") != 1:
            status = 9
        if atomic_load_i32(global_addr("gray_poll_exited"), 0, "acquire") != 0:
            status = 10
        if atomic_load_i32(global_addr("gray_poll_saw_stop"), 0, "acquire") != 0:
            status = 11
        if atomic_load_i32(global_addr("pcc_thread_stop_requested"), 0, "acquire") != 0:
            status = 12
    # Release only after observing publication while the producer is alive.
    # Its exit/unregister flush therefore cannot satisfy the successful path.
    atomic_store_i32(global_addr("gray_poll_release"), 0, 1, "release")
    if join(load_ptr(handle, 0), null()) != 0:
        status = 13
    atomic_store_i32(global_addr("pcc_gc_gray_worklist_flush_requested"), 0, 0, "release")
    if atomic_load_i32(global_addr("gray_poll_exited"), 0, "acquire") != 1:
        status = 14
    lock()
    extra = take()
    unlock()
    if ptr_to_int(extra) != 0:
        status = 15
    if atomic_load_i32(global_addr("pcc_gc_cms_worker_started"), 0, "acquire") != 0:
        status = 20
    if atomic_load_i32(global_addr("pcc_gc_cms_worker_starts"), 0, "acquire") != 0:
        status = 21
    if atomic_load_i32(global_addr("pcc_gc_backend_selected"), 0, "acquire") != backend:
        status = 22
    if atomic_load_i32(global_addr("py_gc_enabled"), 0, "acquire") != 0:
        status = 23
    # Restore the configured selector before returning to managed control or
    # printing. This is a controlled queue component test, not a GC switch.
    atomic_store_i32(global_addr("pcc_gc_backend_selected"), 0, 0, "release")
    atomic_store_i32(global_addr("pcc_gc_nonmoving_count_free"), 0, 0, "release")
    return status

def main() -> None:
    # The process starts with PCC_GC_BACKEND=0. Complete configuration before
    # the synthetic worklist selector changes, then disable automatic GC.
    if configure() != 0:
        print("gray-poll-initial-backend")
        return
    disable()
    first = run(1)
    if first != 0:
        print("gray-poll-failed", 1, first)
        return
    second = run(2)
    if second != 0:
        print("gray-poll-failed", 2, second)
        return
    print("gray-poll-tail-ok", 1, 2)

main()
'''


def test_producer_source_has_only_intrinsic_work_after_queuing():
    tree = ast.parse(PROGRAM)
    producer = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "generated_gray_tail_worker")
    loop = next(node for node in producer.body if isinstance(node, ast.While))
    calls = {node.func.id for node in ast.walk(loop)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert calls == {"atomic_load_i32", "atomic_store_i32", "global_addr"}
    all_calls = [node.func.id for node in ast.walk(producer)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    assert all_calls.count("note") == 1
    assert set(all_calls) == calls | {"note", "int_to_ptr", "null", "c_abi_typed_export"}


def _assert_generated_loop(ir_text, *, polls):
    module = MutableModule.parse(ir_text)
    function = module.function("generated_gray_tail_worker")
    assert function is not None
    cyclic = _blocks_on_cycles(function)
    assert cyclic, "the live producer must not fall through or have a finite loop"
    loop = "\n".join(instruction.text for block in function.blocks
                     if block.name in cyclic for instruction in block.instructions)
    calls = [instruction.text for block in function.blocks if block.name in cyclic
             for instruction in block.instructions if instruction.opcode == "call"]
    if polls:
        assert "@pcc_gc_gray_worklist_flush_requested acquire, align 4" in loop
        assert "@pcc_thread_stop_requested acquire, align 4" in loop
        assert " = or i32 " in loop
        assert calls and all("@pcc_thread_safepoint" in call for call in calls), calls
    else:
        assert "@pcc_gc_gray_worklist_flush_requested" not in loop
        assert not calls, calls
    probe = module.function("generated_gray_tail_probe")
    assert probe is not None
    probe_calls = [instruction.text for block in probe.blocks
                   for instruction in block.instructions if instruction.opcode == "call"]
    permitted = {
        "pcc_thread_start", "pcc_thread_join", "pcc_runtime_monotonic_us",
        "pcc_gc_gray_worklist_take_locked", "pcc_py_gc_minor_graph_lock",
        "pcc_py_gc_minor_graph_unlock", "pcc_thread_safepoint", "pcc_thread_safepoint_gate",
    }
    for call in probe_calls:
        target = re.search(r"@([\w.$-]+)\(", call)
        assert target is not None and target[1] in permitted, call


@pytest.mark.parametrize("polls", [True, False])
def test_compiled_worker_loop_preserves_implicit_poll_switch(tmp_path, monkeypatch, polls):
    monkeypatch.setenv("PCC_IMPLICIT_THREAD_SAFEPOINTS", "1" if polls else "0")
    source = tmp_path / "gray_tail_poll.py"
    source.write_text(PROGRAM, encoding="utf-8")
    output = tmp_path / "gray_tail_poll.ll"
    compile_python(str(source), str(output), backend="self", ir_scaffold_mode="on",
                   emit_llvm_only=True)
    _assert_generated_loop(output.read_text(encoding="utf-8"), polls=polls)


def _linux_x86_64():
    return platform.system() == "Linux" and platform.machine().lower() in ("x86_64", "amd64")


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=_linux_x86_64)
def test_generated_allocation_free_loop_publishes_gray_tail_without_stop(
    tmp_path, monkeypatch, threaded_pcc_runtime_archive,
):
    monkeypatch.setenv("PCC_IMPLICIT_THREAD_SAFEPOINTS", "1")
    monkeypatch.delenv("PCC_SELF_LINK", raising=False)
    source = tmp_path / "gray_tail_poll.py"
    source.write_text(PROGRAM, encoding="utf-8")
    output = tmp_path / "gray_tail_poll.ll"
    compile_python(str(source), str(output), backend="self", ir_scaffold_mode="on",
                   emit_llvm_only=True)
    _assert_generated_loop(output.read_text(encoding="utf-8"), polls=True)
    binary = tmp_path / "gray_tail_poll"
    compile_python(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    result = run_process_group_timeout(
        [str(binary)], timeout=20,
        env=dict(os.environ, PCC_THREADS="1", PCC_REFCOUNT_KIND="atomic", PCC_GC_BACKEND="0"),
    )
    (tmp_path / "gray-tail-poll.stdout").write_text(result.stdout, encoding="utf-8")
    (tmp_path / "gray-tail-poll.stderr").write_text(result.stderr, encoding="utf-8")
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "gray-poll-tail-ok 1 2\n", "",
    )
