from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from pcc.frontends.python import pipeline
from pcc.frontends.python.codegen.runtime_abi import FREESTANDING_GC_RUNTIME_GLOBALS
from tests.runtime_build_cache import cached_threaded_pcc_python_runtime


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "runtime"
REGISTRY_SOURCE = RUNTIME_DIR / "py" / "freestanding_gc_root_registry.py"
MANAGED_SOURCE = RUNTIME_DIR / "py" / "py_gc_backend.py"
MAKEFILE = RUNTIME_DIR / "Makefile"

PUBLIC_SYMBOLS = {
    "pcc_gc_scheduler_root_register_handle",
    "pcc_gc_scheduler_root_register",
    "pcc_gc_scheduler_root_unregister_handle",
    "pcc_gc_scheduler_root_unregister",
    "pcc_gc_register_continuation_root",
    "pcc_gc_unregister_continuation_root",
    # Node-level forms the coroutine runtime registers frames through.
    "pcc_gc_register_continuation_root_node",
    "pcc_gc_unregister_continuation_root_node",
}
INTERNAL_SYMBOLS = {
    # A count-free collector's conservative scan roots this thread's stack.
    "pcc_gc_stack_slot_root_is_conservative",
    "pcc_gc_scheduler_handle_needs_graph",
    "pcc_gc_cycle_requested_store_release",
    "pcc_gc_root_registry_note_mutation_locked",
    "pcc_gc_scheduler_root_link_locked",
    "pcc_gc_scheduler_root_unlink_locked",
    "pcc_gc_continuation_root_unlink_locked",
    "pcc_gc_root_slot_count_from_map",
    "pcc_gc_root_map_is_borrowed",
    # The scheduler-root list word, its held-word link/unlink and node reuse.
    "pcc_gc_scheduler_root_list_acquire",
    "pcc_gc_scheduler_root_list_release",
    "pcc_gc_scheduler_root_link_held",
    "pcc_gc_scheduler_root_unlink_held",
    "pcc_gc_scheduler_root_node_take",
    "pcc_gc_scheduler_root_node_give",
    # The continuation-root list word, and whether its writers also need
    # the graph lock (not in container mode).
    "pcc_gc_continuation_root_list_acquire",
    "pcc_gc_continuation_root_list_release",
    "pcc_gc_continuation_root_writers_need_graph",
    # A started thread's emptied scheduler-root nodes leave the list at exit.
    "pcc_gc_scheduler_root_tls_cache_drain",
}
# The list word and the free-node list this module owns.
OWNED_GLOBALS = {
    "pcc_py_gc_scheduler_root_spin",
    "pcc_py_gc_scheduler_root_free_head",
    "pcc_py_gc_scheduler_root_free_count",
    "pcc_py_gc_scheduler_root_stack_handle",
    "pcc_py_gc_continuation_root_spin",
    # This thread's emptied scheduler-root nodes, still linked, for reuse.
    "pcc_gc_scheduler_root_tls_cache",
    "pcc_gc_scheduler_root_tls_cache_count",
}
RAW_FUNCTION_IMPORTS = {
    "pcc_gc_backend3_revoke_closed_young_domain",
    "pcc_thread_is_running",
    "pcc_thread_exit_unregisters",
    "pcc_thread_stack_high",
    "free",
    "malloc",
    "memset",
    "pcc_gc_backend",
    "pcc_py_gc_minor_graph_lock",
    "pcc_py_gc_minor_graph_unlock",
}
RAW_GLOBAL_IMPORTS = {
    "pcc_gc_backend_selected",
    "pcc_gc_config_initialized",
    "pcc_gc_backend_frozen",
    "pcc_gc_backend4_no_refcounts",
    "pcc_gc_backend3_continuation_root_scan_cursor",
    "pcc_gc_backend3_frame_root_scan_slot",
    "pcc_gc_backend3_scheduler_root_scan_cursor",
    "pcc_gc_backend3_scheduler_root_scan_slot",
    "pcc_gc_cycle_requested",
    "pcc_gc_scheduler_root_head",
    "pcc_gc_continuation_root_head",
    "pcc_gc_root_registry_revision",
    "pcc_gc_list_container_mode",
}


def _exported_symbols(source: str) -> set[str]:
    return set(re.findall(r'@c_abi_export\("([^"]+)"\)', source))


def _compile_object(tmp_path: Path, emitter: str) -> tuple[Path, str]:
    llvm_ir = tmp_path / ("freestanding_gc_root_registry_" + emitter + ".ll")
    pipeline.compile_python(
        str(REGISTRY_SOURCE),
        str(llvm_ir),
        emit_llvm_only=True,
        python_library=True,
    )
    ir_text = llvm_ir.read_text(encoding="utf-8")
    source = llvm_ir
    if emitter == "self":
        from pcc.backend.self_backend_dispatch import emit_self_asm

        source = tmp_path / "freestanding_gc_root_registry.s"
        source.write_text(
            emit_self_asm(ir_text), encoding="utf-8"
        )
    obj = tmp_path / ("freestanding_gc_root_registry_" + emitter + ".o")
    result = subprocess.run(
        ["clang", "-c", str(source), "-o", str(obj)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return obj, ir_text


def test_root_registry_has_one_strict_source_owner_and_one_lock_contract():
    strict = REGISTRY_SOURCE.read_text(encoding="utf-8")
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "__pcc_freestanding__ = True" in strict
    assert _exported_symbols(strict) == PUBLIC_SYMBOLS | INTERNAL_SYMBOLS
    assert _exported_symbols(managed).isdisjoint(PUBLIC_SYMBOLS | INTERNAL_SYMBOLS)
    assert "freestanding_gc_root_registry" in makefile
    assert 'atomic_store_i32(slot, 0, value, "release")' in strict
    for name in PUBLIC_SYMBOLS:
        body = strict.split("def " + name, 1)[1].split("\n\n@", 1)[0]
        assert "gc_backend_current()" in body or name in {
            "pcc_gc_scheduler_root_register",
        }


def test_continuation_unregister_frees_only_after_graph_unlock():
    strict = REGISTRY_SOURCE.read_text(encoding="utf-8")
    strict_body = strict.split("def pcc_gc_unregister_continuation_root(", 1)[
        1
    ].split("\n\n@", 1)[0]
    assert "dead = null()" in strict_body
    assert strict_body.rindex("pcc_py_gc_minor_graph_unlock()") < (
        strict_body.rindex("free(dead)")
    )


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_root_registry_object_has_exact_raw_closure(tmp_path: Path, emitter: str):
    obj, ir_text = _compile_object(tmp_path, emitter)
    assert RAW_GLOBAL_IMPORTS <= FREESTANDING_GC_RUNTIME_GLOBALS
    assert "store atomic i32" in ir_text
    assert "release, align 4" in ir_text
    undefined_result = subprocess.run(
        ["nm", "-u", str(obj)], capture_output=True, text=True, timeout=30
    )
    assert undefined_result.returncode == 0, (
        undefined_result.stdout + undefined_result.stderr
    )
    undefined = {
        line.split()[-1].lstrip("_")
        for line in undefined_result.stdout.splitlines()
        if line.strip()
    }
    expected = (RAW_FUNCTION_IMPORTS | RAW_GLOBAL_IMPORTS) - {"memset"}
    # The thread-local node cache reaches its TLS through the platform's
    # access helper (Darwin's _tlv_bootstrap, ELF's __tls_get_addr).
    tls_helpers = undefined & {"tlv_bootstrap", "tls_get_addr"}
    assert len(tls_helpers) <= 1
    if sys.platform == "darwin":
        assert tls_helpers == {"tlv_bootstrap"}
    undefined = undefined - tls_helpers
    # The explicit zero-fill intrinsic can lower to either libc spelling in
    # the external LLVM oracle. Keep all other dependencies exact; owned
    # lowering retains memset for the dynamic-size operation.
    zero_helpers = undefined & {"memset", "bzero"}
    assert undefined - zero_helpers == expected
    if emitter == "self":
        assert zero_helpers == {"memset"}
    else:
        assert zero_helpers in ({"memset"}, {"bzero"}, {"memset", "bzero"})

    symbols_result = subprocess.run(
        ["nm", "-g", str(obj)], capture_output=True, text=True, timeout=30
    )
    assert symbols_result.returncode == 0, symbols_result.stdout + symbols_result.stderr
    defined = {
        line.split()[-1].lstrip("_")
        for line in symbols_result.stdout.splitlines()
        if line.strip() and " U " not in line
    }
    assert defined == PUBLIC_SYMBOLS | INTERNAL_SYMBOLS | OWNED_GLOBALS


def _harness_source(concurrent: bool) -> str:
    if concurrent:
        return r'''
#include "py_runtime.h"
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>

enum { THREADS = 4, ROUNDS = 512 };
static PyObject *scheduler_slots[THREADS];
static PyObject *continuation_slots[THREADS][2];
static int32_t continuation_maps[THREADS][1] = {{-2}, {-2}, {-2}, {-2}};

static void *mutate(void *raw) {
    intptr_t index = (intptr_t)raw;
    for (int i = 0; i < ROUNDS; i++) {
        void *handle = pcc_gc_scheduler_root_register_handle(
            &scheduler_slots[index]
        );
        if (handle == NULL) return (void *)1;
        pcc_gc_register_continuation_root(
            continuation_maps[index], continuation_slots[index]
        );
        pcc_gc_unregister_continuation_root(continuation_slots[index]);
        pcc_gc_scheduler_root_unregister_handle(handle);
    }
    return NULL;
}

static void *observe(void *raw) {
    (void)raw;
    for (int i = 0; i < THREADS * ROUNDS; i++) {
        int64_t scheduler = pcc_gc_scheduler_root_count();
        int64_t continuation = pcc_gc_continuation_root_slot_count();
        if (scheduler < 0 || scheduler > THREADS) return (void *)1;
        if (continuation < 0 || continuation > THREADS * 2) return (void *)1;
    }
    return NULL;
}

int main(void) {
    pthread_t workers[THREADS];
    pthread_t observer;
    for (intptr_t i = 0; i < THREADS; i++) {
        if (pthread_create(&workers[i], NULL, mutate, (void *)i) != 0) return 2;
    }
    if (pthread_create(&observer, NULL, observe, NULL) != 0) return 3;
    for (int i = 0; i < THREADS; i++) {
        void *result = NULL;
        pthread_join(workers[i], &result);
        if (result != NULL) return 4;
    }
    void *result = NULL;
    pthread_join(observer, &result);
    if (result != NULL) return 5;
    printf("final:%lld,%lld\n",
           (long long)pcc_gc_scheduler_root_count(),
           (long long)pcc_gc_continuation_root_slot_count());
    return 0;
}
'''
    return r'''
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>

/* Not in a thread stack: every collector links these registrations. */
static PyObject *scheduler_slots[2];

int main(void) {
    /* A stack slot: count-free collectors root it by their conservative
     * stack scan and hand back their stack handle without linking it. */
    PyObject *stack_slot = NULL;
    void *stack_handle = pcc_gc_scheduler_root_register_handle(&stack_slot);
    if (stack_handle == NULL) return 1;
    printf("stack:%lld\n", (long long)pcc_gc_scheduler_root_count());
    pcc_gc_scheduler_root_unregister_handle(stack_handle);
    printf("stack-empty:%lld\n", (long long)pcc_gc_scheduler_root_count());

    void *first = pcc_gc_scheduler_root_register_handle(&scheduler_slots[0]);
    pcc_gc_scheduler_root_register(&scheduler_slots[1]);
    void *second = pcc_gc_scheduler_root_register_handle(&scheduler_slots[0]);
    if (first == NULL || second == NULL) return 1;
    printf("scheduler:%lld\n", (long long)pcc_gc_scheduler_root_count());
    pcc_gc_scheduler_root_unregister(&scheduler_slots[0]);
    printf("scheduler-slot:%lld\n", (long long)pcc_gc_scheduler_root_count());
    pcc_gc_scheduler_root_unregister_handle(first);
    pcc_gc_scheduler_root_unregister(&scheduler_slots[1]);
    printf("scheduler-empty:%lld\n", (long long)pcc_gc_scheduler_root_count());

    int32_t owned_map[1] = {2};
    int32_t borrowed_map[1] = {-2};
    int32_t zero_map[1] = {0};
    int32_t min_map[1] = {(int32_t)0x80000000u};
    int32_t huge_map[1] = {100001};
    PyObject *owned[2] = {NULL, NULL};
    PyObject *borrowed[2] = {NULL, NULL};
    pcc_gc_register_continuation_root(owned_map, owned);
    pcc_gc_register_continuation_root(borrowed_map, borrowed);
    pcc_gc_register_continuation_root(zero_map, owned);
    pcc_gc_register_continuation_root(min_map, owned);
    pcc_gc_register_continuation_root(huge_map, owned);
    printf("continuation:%lld\n",
           (long long)pcc_gc_continuation_root_slot_count());
    pcc_gc_unregister_continuation_root(owned);
    printf("continuation-owned:%lld\n",
           (long long)pcc_gc_continuation_root_slot_count());
    pcc_gc_unregister_continuation_root(borrowed);
    printf("continuation-empty:%lld\n",
           (long long)pcc_gc_continuation_root_slot_count());
    return 0;
}
'''


def _link_harness(
    tmp_path: Path, name: str, archive: Path, *, concurrent: bool = False
) -> Path:
    source = tmp_path / (name + ".c")
    executable = tmp_path / name
    source.write_text(_harness_source(concurrent), encoding="utf-8")
    command = [
        "clang",
        "-std=c11",
        f"-I{RUNTIME_DIR / 'include'}",
        str(source),
        str(archive),
    ]
    if concurrent:
        command.append("-pthread")
    command.extend(["-o", str(executable)])
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return executable


# What the harness printed against the retired C runtime oracle, after the
# stack-slot lines: counted GC0 links a stack slot, count-free GC1-4 do not.
def _registry_expected(backend: int) -> str:
    return (
        f"stack:{1 if backend == 0 else 0}\n"
        "stack-empty:0\n"
    ) + REGISTRY_EXPECTED


REGISTRY_EXPECTED = (
    "scheduler:3\n"
    "scheduler-slot:2\n"
    "scheduler-empty:0\n"
    "continuation:4\n"
    "continuation-owned:2\n"
    "continuation-empty:0\n"
)


def test_production_archive_uniquely_owns_registry(
    tmp_path: Path,
    pcc_runtime_archive: Path,
):
    members_result = subprocess.run(
        ["ar", "-t", str(pcc_runtime_archive)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert members_result.returncode == 0, members_result.stdout + members_result.stderr
    assert "freestanding_gc_root_registry.o" in members_result.stdout.splitlines()

    symbols_result = subprocess.run(
        ["nm", "-A", "-g", str(pcc_runtime_archive)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert symbols_result.returncode == 0, symbols_result.stdout + symbols_result.stderr
    owners: dict[str, list[str]] = {
        symbol: [] for symbol in PUBLIC_SYMBOLS | INTERNAL_SYMBOLS
    }
    for line in symbols_result.stdout.splitlines():
        symbol = line.split()[-1].lstrip("_") if line.strip() else ""
        if symbol in owners and " U " not in line:
            owners[symbol].append(line)
    assert all(len(lines) == 1 for lines in owners.values())
    assert all(
        ":freestanding_gc_root_registry.o:" in lines[0]
        for lines in owners.values()
    )
    assert not any(
        ":py_gc_backend.o:" in line for lines in owners.values() for line in lines
    )

    implementation = _link_harness(
        tmp_path, "gc_registry_pcc_python", pcc_runtime_archive
    )
    for backend in range(5):
        env = {**os.environ, "PCC_GC_BACKEND": str(backend)}
        result = subprocess.run(
            [str(implementation)], env=env, capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == _registry_expected(backend)


def test_root_registry_survives_pthread_mutation_and_observation(
    tmp_path: Path,
):
    threaded_pcc_python_archive = (
        cached_threaded_pcc_python_runtime() / "libpy_runtime_pcc_py.a"
    )
    implementation = _link_harness(
        tmp_path,
        "gc_registry_threads_pcc_python",
        threaded_pcc_python_archive,
        concurrent=True,
    )
    for backend in range(5):
        env = {**os.environ, "PCC_GC_BACKEND": str(backend)}
        result = subprocess.run(
            [str(implementation)], env=env, capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == "final:0,0\n"


@pytest.mark.parametrize("name", [
    "pcc_gc_register_continuation_root_node",
    "pcc_gc_unregister_continuation_root_node",
    "pcc_gc_unregister_continuation_root",
])
def test_continuation_writers_hold_the_list_word_and_the_graph_lock_only_when_needed(name):
    strict = REGISTRY_SOURCE.read_text(encoding="utf-8")
    body = strict.split("def " + name + "(", 1)[1].split("\n\n@", 1)[0]
    # The graph lock is conditional on the mode and encloses the list word;
    # the list change happens inside the word.
    assert "graph: i64 = _continuation_writers_need_graph()" in body
    assert body.index("if graph != 0:\n        pcc_py_gc_minor_graph_lock()") < (
        body.index("_continuation_root_spin_acquire()")
    )
    assert body.index("_continuation_root_spin_release()") < (
        body.rindex("pcc_py_gc_minor_graph_unlock()")
    )
    change = ("global_store_ptr(\"pcc_gc_continuation_root_head\", node)"
              if "register" in name and "unregister" not in name
              else "pcc_gc_continuation_root_unlink_locked(node)")
    assert body.index("_continuation_root_spin_acquire()") < body.index(change) < (
        body.index("_continuation_root_spin_release()")
    )
    if "unregister" in name:
        assert body.rindex("pcc_py_gc_minor_graph_unlock()") < body.rindex("free(")


def test_continuation_writers_need_the_graph_lock_only_outside_container_mode():
    strict = REGISTRY_SOURCE.read_text(encoding="utf-8")
    body = strict.split("def _continuation_writers_need_graph(", 1)[1].split("\n\n@", 1)[0]
    assert 'load_i32(global_addr("pcc_gc_list_container_mode"), 0) != 0' in body
    # Root revisions bump atomically in container mode: continuation writers
    # then change roots without the graph lock that frame writers hold.
    note = strict.split("def pcc_gc_root_registry_note_mutation_locked(", 1)[1].split("\n\n@", 1)[0]
    serial = note.split("serial_revision", 1)[0]
    assert 'load_i32(global_addr("pcc_gc_list_container_mode"), 0) == 0' in serial
