from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path

import pytest

from pcc.frontends.python import pipeline
from pcc.frontends.python.pipeline_targets import host_target_triple
from pcc.frontends.python.codegen.runtime_abi import (
    FREESTANDING_GC_CROSS_OBJECT_SIGNATURES,
    FREESTANDING_GC_RUNTIME_GLOBALS,
    FREESTANDING_GC_THREAD_LOCAL_GLOBALS,
    RUNTIME_SIGNATURES,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "runtime"
FRAME_SOURCE = RUNTIME_DIR / "py" / "freestanding_gc_frame_registry.py"
MANAGED_SOURCE = RUNTIME_DIR / "py" / "py_gc_backend.py"
WRAPPER_SOURCE = RUNTIME_DIR / "py" / "py_obj.py"
MAKEFILE = RUNTIME_DIR / "Makefile"
STATE_SOURCE = RUNTIME_DIR / "py" / "freestanding_gc_state.py"

PUBLIC_SYMBOLS = {
    "pcc_gc_note_frame_enter",
    "pcc_gc_note_frame_enter_lifo",
    "pcc_gc_note_frame_leave",
    "pcc_gc_note_frame_leave_lifo",
}
INTERNAL_SYMBOLS = {
    "pcc_gc_note_frame_enter_checked",
    "pcc_gc_frame_node_alloc",
    "pcc_gc_frame_node_bucket",
    "pcc_gc_frame_node_create",
    "pcc_gc_frame_node_release",
    "pcc_gc_frame_node_size",
    "pcc_gc_frame_node_tls_pool_cached_count",
    "pcc_gc_frame_node_tls_pool_drain",
    "pcc_gc_frame_node_unlink",
    "pcc_gc_frame_roots_disabled_fast",
    "pcc_gc_should_track_frame_roots",
    # Whether the precise stack walk roots a mapped frame's slots.
    "pcc_gc_frame_slots_walked",
    # Explicit activation-private scopes are owned by the same raw module.
    "pcc_gc_local_frame_find",
    "pcc_gc_local_frame_take",
    "pcc_gc_local_frame_enter",
    "pcc_gc_local_frame_leave",
    "pcc_gc_local_frame_contains",
    "pcc_gc_local_frame_visit_stopped",
    "pcc_gc_local_frame_slot_count",
    "pcc_gc_local_frame_destroy",
    "pcc_gc_note_frame_enter_local_checked",
    "pcc_gc_note_frame_leave_local",
    "pcc_gc_frame_enter_local_mode",
    "pcc_gc_frame_leave_local_mode",
    "pcc_gc_frame_enter_local",
    "pcc_gc_frame_leave_local",
    "pcc_gc_frame_enter_lifo_local",
    "pcc_gc_frame_leave_lifo_local",
    "pcc_gc_frame_enter_local_mapped",
    "pcc_gc_frame_leave_local_mapped",
    "pcc_gc_frame_enter_lifo_local_mapped",
    "pcc_gc_frame_leave_lifo_local_mapped",
}
TLS_STORAGE_SYMBOLS = {
    "pcc_gc_frame_node_pool_counts",
    "pcc_gc_frame_node_pool_heads",
    "pcc_gc_frame_node_pool_total",
}
# These words belong to other GC providers, not to the frame registry. Keep
# the real storage owner, TLS kind and initializer independent of the ABI list.
OTHER_GC_TLS_STORAGE = {
    "pcc_gc_scheduler_root_tls_cache": (
        "freestanding_gc_root_registry", "define_thread_local_ptr_null", (),
    ),
    "pcc_gc_scheduler_root_tls_cache_count": (
        "freestanding_gc_root_registry", "define_thread_local_i32", (0,),
    ),
    "pcc_gc_tls_live_bytes_delta": (
        "freestanding_gc_object_nodes", "define_thread_local_i32", (0,),
    ),
    "pcc_process_main_anchor": (
        "freestanding_process_main", "define_thread_local_ptr_null", (),
    ),
    "pcc_tls_young_log_unbuffered": (
        "freestanding_gc_tracking", "define_thread_local_i32", (0,),
    ),
}


def _assert_other_gc_tls_storage(sources):
    actual = {name: [] for name in OTHER_GC_TLS_STORAGE}
    for provider, source in sources:
        # The runtime DSL requires a literal storage name at module scope.
        if "define_" not in source:
            continue
        body = ast.parse(source).body
        # Match the frontend's from-pcc.unsafe binding, including as-names.
        # An unrelated Python function with the same spelling is not storage.
        definitions = {
            alias.asname or alias.name: alias.name
            for stmt in body if isinstance(stmt, ast.ImportFrom)
            and stmt.level == 0 and stmt.module == "pcc.unsafe"
            for alias in stmt.names if alias.name.startswith("define_")
        }
        for stmt in body:
            if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
                continue
            call = stmt.value
            if (not isinstance(call.func, ast.Name)
                    or call.func.id not in definitions or not call.args
                    or not isinstance(call.args[0], ast.Constant)):
                continue
            name = call.args[0].value
            if name in actual:
                args = tuple(ast.literal_eval(arg) for arg in call.args[1:])
                assert not call.keywords
                actual[name].append((provider, definitions[call.func.id], args))
    assert actual == {name: [definition] for name, definition in OTHER_GC_TLS_STORAGE.items()}


def _assert_thread_exit_drains(unregister):
    functions = [stmt for stmt in ast.parse(unregister).body
                 if isinstance(stmt, ast.FunctionDef) and stmt.name == "pcc_gc_thread_unregister_buffers"]
    assert len(functions) == 1
    required = ("pcc_gc_frame_node_tls_pool_drain", "pcc_gc_scheduler_root_tls_cache_drain", "_live_bytes_flush")
    # Keep all three in the entry's unconditional call block. Comments,
    # conditionals, calls after a return and unevaluated expressions do not run.
    entry = []
    for stmt in functions[0].body:
        if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name)):
            break
        if not stmt.value.args and not stmt.value.keywords:
            entry.append(stmt.value.func.id)
    all_calls = [node.func.id for node in ast.walk(functions[0]) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name)]
    assert all(entry.count(name) == all_calls.count(name) == 1 for name in required)


RAW_ONLY_CROSS_OBJECT_SYMBOLS = {
    "pcc_gc_index_slot_size",
    "pcc_gc_cycle_requested_store_release",
    "pcc_gc_frame_index_find",
    "pcc_gc_frame_index_plan_capacity",
    "pcc_gc_frame_index_plan_commit",
    "pcc_gc_frame_index_remove",
    "pcc_gc_frame_index_replace_preallocated",
    "pcc_gc_root_map_is_borrowed",
    "pcc_gc_root_registry_note_mutation_locked",
    "pcc_gc_root_slot_count_from_map",
    "pcc_thread_local_root_arena_get",
    "pcc_thread_local_root_arena_set",
    "pcc_thread_is_running",
}
RAW_FUNCTION_IMPORTS = RAW_ONLY_CROSS_OBJECT_SYMBOLS | {
    "free",
    "malloc",
    "memset",
    "pcc_gc_backend",
    "pcc_py_gc_minor_graph_lock",
    "pcc_py_gc_minor_graph_unlock",
    # Whether a mapped frame's slots lie on this thread's walked stack.
    "pcc_gc_stackmap_roots_mode",
    "pcc_thread_stack_high",
    "pcc_thread_no_park_enter",
    "pcc_thread_no_park_exit",
    "pcc_thread_no_park_depth",
    "pcc_current_thread_id",
    "pcc_thread_owns_stopped_world",
    "pcc_platform_abort",
}
RAW_GLOBAL_IMPORTS = {
    "pcc_gc_backend0_frame_roots_enabled",
    "pcc_gc_backend3_frame_root_scan_cursor",
    "pcc_gc_backend3_frame_root_scan_slot",
    "pcc_gc_config_initialized",
    "pcc_gc_frame_head",
    # The cached final pcc_gc_stackmap_roots_mode answer.
    "pcc_gc_stackmap_walk_state",
}


def _exported_symbols(source: str) -> set[str]:
    return set(re.findall(r'@c_abi_export\("([^"]+)"\)', source))


def _compile_object(tmp_path: Path, emitter: str, target_triple: str) -> Path:
    llvm_ir = tmp_path / ("freestanding_gc_frame_registry_" + emitter + ".ll")
    pipeline.compile_python(
        str(FRAME_SOURCE),
        str(llvm_ir),
        emit_llvm_only=True,
        python_library=True,
        target_triple=target_triple,
    )
    ir_text = llvm_ir.read_text(encoding="utf-8")
    obj = tmp_path / ("freestanding_gc_frame_registry_" + emitter + ".o")
    if emitter == "self":
        from pcc.backend.self_backend_dispatch import emit_self_asm
        from pcc.backend.target_objects import encode_assembly_object

        obj.write_bytes(encode_assembly_object(emit_self_asm(ir_text), target_triple))
    else:
        result = subprocess.run(
            ["clang", "-c", str(llvm_ir), "-o", str(obj)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    return obj


def _object_external_symbols(payload, target):
    """Inspect real object tables with the compiler-owned target reader."""
    if "linux" in target:
        from pcc.backend.elf_x86_64 import parse_relocatable, STB_GLOBAL, STB_WEAK
        rows = [(symbol.name, symbol.section_index)
                for symbol in parse_relocatable(payload).symbols
                if symbol.binding in (STB_GLOBAL, STB_WEAK)]
    elif "windows" in target:
        from pcc.backend.coff_x86_64 import parse_object
        rows = [(symbol.name, symbol.section) for symbol in parse_object(payload).symbols
                if symbol.external]
    elif payload.startswith(b"\xcf\xfa\xed\xfe"):
        from pcc.backend.macho_spec import parse_object, N_EXT, N_TYPE, N_UNDF
        rows = [(symbol["name"].lstrip("_"),
                 0 if symbol["n_type"] & N_TYPE == N_UNDF else 1)
                for symbol in parse_object(payload).symbols() if symbol["n_type"] & N_EXT]
    else:
        from pcc.backend.native_object import decode_packed_native_object
        rows = [(symbol.name.lstrip("_"), symbol.section_index)
                for symbol in decode_packed_native_object(payload).symbols if symbol.external]
    return ({name for name, section in rows if section != 0},
            {name for name, section in rows if section == 0})


def test_frame_registry_has_one_strict_source_owner():
    strict = FRAME_SOURCE.read_text(encoding="utf-8")
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    wrappers = WRAPPER_SOURCE.read_text(encoding="utf-8")
    state = STATE_SOURCE.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "__pcc_freestanding__ = True" in strict
    assert "return 64 + root_count * 8" in strict
    assert _exported_symbols(strict) == PUBLIC_SYMBOLS | INTERNAL_SYMBOLS
    assert _exported_symbols(managed).isdisjoint(PUBLIC_SYMBOLS | INTERNAL_SYMBOLS)
    assert RAW_ONLY_CROSS_OBJECT_SYMBOLS.isdisjoint(RUNTIME_SIGNATURES)
    assert FREESTANDING_GC_CROSS_OBJECT_SIGNATURES[
        "pcc_gc_frame_node_tls_pool_drain"
    ] == ((), "c_void")
    assert FREESTANDING_GC_CROSS_OBJECT_SIGNATURES[
        "pcc_gc_frame_index_plan_capacity"
    ] == (("c_int64",), "c_int64")
    assert FREESTANDING_GC_CROSS_OBJECT_SIGNATURES[
        "pcc_gc_frame_index_plan_commit"
    ] == (("c_ptr", "c_int64", "c_int64"), "c_int64")
    assert FREESTANDING_GC_CROSS_OBJECT_SIGNATURES[
        "pcc_gc_frame_index_replace_preallocated"
    ] == (("c_ptr", "c_ptr"), "c_ptr")
    # Checked raw adapter scopes have an explicit fallible entry and raw
    # leave. They are not unconditional compiler frame-protocol declarations.
    assert FREESTANDING_GC_CROSS_OBJECT_SIGNATURES[
        "pcc_gc_note_frame_enter_local_checked"
    ] == (("c_ptr", "c_ptr"), "c_int64")
    assert FREESTANDING_GC_CROSS_OBJECT_SIGNATURES[
        "pcc_gc_note_frame_leave_local"
    ] == (("c_ptr",), "c_void")
    assert {
        "pcc_gc_note_frame_enter_local_checked",
        "pcc_gc_note_frame_leave_local",
    }.isdisjoint(RUNTIME_SIGNATURES)
    internal_header = (RUNTIME_DIR / "src" / "py_internal.h").read_text(
        encoding="utf-8"
    )
    for symbol in (
        "pcc_gc_frame_index_plan_capacity",
        "pcc_gc_frame_index_plan_commit",
        "pcc_gc_frame_index_replace_preallocated",
    ):
        assert symbol in internal_header
    # Collector identity, pressure and deferred work have independent TLS owners.
    # Keep the complete current inventory, not the earlier pre-deferred-sweep set.
    assert FREESTANDING_GC_THREAD_LOCAL_GLOBALS == TLS_STORAGE_SYMBOLS | set(OTHER_GC_TLS_STORAGE) | {
        "pcc_gc_cms_worker_current", "pcc_gc_pressure_image_depth", "pcc_gc_pressure_owner",
        "pcc_gc_gray_local_batch",
        # The allocation staging ring of freestanding_gc_alloc_stage.
        "pcc_gc_alloc_stage_local_ring",
        "pcc_gc_deferred_free_active", "pcc_gc_deferred_free_head",
        "pcc_gc_deferred_free_tail", "pcc_gc_deferred_free_pending_count",
        "pcc_gc_deferred_free_pending_bytes", "pcc_gc_deferred_free_local_attempts",
        "pcc_gc_deferred_free_local_fallbacks",
        # A thread's unpublished allocation debt and count (pcc_gc_cms_note_alloc).
        "pcc_gc_tls_alloc_debt", "pcc_gc_tls_alloc_pushes",
    }
    _assert_other_gc_tls_storage(
        (path.stem, path.read_text(encoding="utf-8"))
        for path in (RUNTIME_DIR / "py").glob("*.py")
    )
    assert 'define_thread_local_ptr_null("pcc_gc_frame_node_pool_heads")' in strict
    assert 'define_thread_local_ptr_null("pcc_gc_frame_node_pool_counts")' in strict
    assert 'define_thread_local_i32("pcc_gc_frame_node_pool_total", 0)' in strict
    for symbol in TLS_STORAGE_SYMBOLS:
        assert f'"{symbol}"' not in state
    unregister = managed.split(
        '@c_abi_export("pcc_gc_thread_unregister_buffers")', 1
    )[1].split("\n@c_abi_export", 1)[0]
    assert "pcc_gc_frame_node_tls_pool_drain()" in unregister
    _assert_thread_exit_drains(unregister)
    assert "freestanding_gc_frame_registry" in makefile
    for symbol in PUBLIC_SYMBOLS:
        assert f'"{symbol}"' in wrappers


@pytest.mark.parametrize("change", ["missing", "duplicate", "aliased-duplicate", "split-literal", "wrong-owner", "not-tls", "wrong-width", "not-zero"])
def test_other_gc_tls_storage_rejects_wrong_definitions(change):
    rows = [
        ("freestanding_gc_root_registry", 'define_thread_local_ptr_null("pcc_gc_scheduler_root_tls_cache")'),
        ("freestanding_gc_root_registry", 'define_thread_local_i32("pcc_gc_scheduler_root_tls_cache_count", 0)'),
        ("freestanding_process_main", 'define_thread_local_ptr_null("pcc_process_main_anchor")'),
        ("freestanding_gc_tracking", 'define_thread_local_i32("pcc_tls_young_log_unbuffered", 0)'),
        ("freestanding_gc_object_nodes", 'define_thread_local_i32("pcc_gc_tls_live_bytes_delta", 0)'),
    ]
    imports = ("from pcc.unsafe import define_thread_local_ptr_null, define_thread_local_i32, "
               "define_thread_local_i64, define_global_i32\n")
    rows = [(owner, imports + body) for owner, body in rows]
    _assert_other_gc_tls_storage(rows)
    owner, source = rows.pop()
    if change == "split-literal":
        rows.extend(((owner, source), ("another_provider", imports +
            'define_thread_local_i32("pcc_gc_tls_" "live_bytes_delta", 0)')))
    elif change == "aliased-duplicate":
        rows.extend(((owner, source), ("another_provider",
            'from pcc.unsafe import define_thread_local_i32 as storage\n'
            'storage("pcc_gc_tls_live_bytes_delta", 0)')))
    elif change == "duplicate":
        rows.extend(((owner, source), ("another_provider", source)))
    elif change == "wrong-owner":
        rows.append(("freestanding_gc_frame_registry", source))
    elif change == "not-tls":
        rows.append((owner, source.replace("define_thread_local_", "define_global_")))
    elif change == "wrong-width":
        rows.append((owner, source.replace("_i32", "_i64")))
    elif change == "not-zero":
        rows.append((owner, source.replace(", 0)", ", 1)")))
    with pytest.raises(AssertionError):
        _assert_other_gc_tls_storage(rows)


@pytest.mark.parametrize("change", [
    "# _live_bytes_flush()", "if sometimes: _live_bytes_flush()", "return; _live_bytes_flush()",
    "_live_bytes_flush(); _live_bytes_flush()", "_live_bytes_flush(0)",
])
def test_thread_exit_drain_contract_rejects_ineffective_cleanup(change):
    prefix = ("def pcc_gc_thread_unregister_buffers():\n"
              "    pcc_gc_frame_node_tls_pool_drain()\n"
              "    pcc_gc_scheduler_root_tls_cache_drain()\n    ")
    _assert_thread_exit_drains(prefix + "_live_bytes_flush()\n")
    with pytest.raises(AssertionError):
        _assert_thread_exit_drains(prefix + change + "\n")


def test_frame_registry_allocator_and_release_tails_are_outside_graph_lock():
    strict = FRAME_SOURCE.read_text(encoding="utf-8")

    for name in ("pcc_gc_note_frame_enter", "pcc_gc_note_frame_enter_lifo"):
        strict_body = strict.split(f"def {name}", 1)[1].split("\n\n@", 1)[0]
        assert strict_body.index("pcc_gc_frame_node_create(") < strict_body.index(
            "pcc_py_gc_minor_graph_lock()"
        )

    for name in ("pcc_gc_note_frame_leave", "pcc_gc_note_frame_leave_lifo"):
        strict_body = strict.split(f"def {name}", 1)[1].split("\n\n@", 1)[0]
        assert strict_body.rindex("pcc_py_gc_minor_graph_unlock()") < (
            strict_body.rindex("pcc_gc_frame_node_release(")
        )

    strict_enter = strict.split("def pcc_gc_note_frame_enter", 1)[1].split(
        "\n\n@", 1
    )[0]
    assert "pcc_gc_frame_index_plan_capacity(1)" in strict_enter
    assert "pcc_gc_frame_index_plan_commit(" in strict_enter
    assert "pcc_gc_frame_index_replace_preallocated(" in strict_enter
    assert "pcc_gc_frame_index_replace(" not in strict_enter
    for forbidden in ("malloc(", "free("):
        start = 0
        while (position := strict_enter.find(forbidden, start)) >= 0:
            preceding_lock = strict_enter.rfind(
                "pcc_py_gc_minor_graph_lock()", 0, position
            )
            preceding_unlock = strict_enter.rfind(
                "pcc_py_gc_minor_graph_unlock()", 0, position
            )
            assert preceding_lock < preceding_unlock
            start = position + len(forbidden)


def _assert_exact_raw_imports(undefined, *, target_triple, emitter):
    expected = RAW_FUNCTION_IMPORTS | RAW_GLOBAL_IMPORTS
    darwin = target_triple.endswith("-apple-darwin")
    if darwin:
        expected = expected | {"tlv_bootstrap"}
    elif "windows" in target_triple:
        expected = expected | {"_tls_index"}
    zero_helpers = undefined & {"memset", "bzero"}
    assert undefined - zero_helpers == expected - {"memset"}
    if target_triple.startswith("x86_64") and emitter == "self":
        # Owned x86 raw memset lowers to a machine repeat-store, no import.
        assert zero_helpers == set()
    elif darwin and emitter == "llvm":
        # Only the Darwin LLVM oracle may turn explicit zero-fill calls into
        # bzero. Keep the rest of its imports exact; owned lowering and Linux
        # still require memset, not an arbitrary libc helper.
        assert zero_helpers in ({"memset"}, {"bzero"}, {"memset", "bzero"})
    else:
        assert zero_helpers == {"memset"}


@pytest.mark.parametrize("target_triple", [
    "x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu", "arm64-apple-darwin",
    "x86_64-pc-windows-msvc",
])
@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_frame_registry_raw_import_expectations_are_target_qualified(target_triple, emitter):
    expected = RAW_FUNCTION_IMPORTS | RAW_GLOBAL_IMPORTS
    darwin = target_triple.endswith("-apple-darwin")
    if darwin:
        expected = expected | {"tlv_bootstrap"}
    elif "windows" in target_triple:
        expected = expected | {"_tls_index"}
    kwargs = dict(target_triple=target_triple, emitter=emitter)
    actual_expected = expected - {"memset"} if target_triple.startswith("x86_64") and emitter == "self" else expected
    _assert_exact_raw_imports(actual_expected, **kwargs)
    for zero_helpers in ({"bzero"}, {"memset", "bzero"}):
        actual = (expected - {"memset"}) | zero_helpers
        if darwin and emitter == "llvm":
            _assert_exact_raw_imports(actual, **kwargs)
        else:
            with pytest.raises(AssertionError):
                _assert_exact_raw_imports(actual, **kwargs)
    missing_candidates = ("memset", "pcc_thread_no_park_enter", "pcc_thread_no_park_exit")
    for missing in missing_candidates:
        if missing not in actual_expected:
            continue
        with pytest.raises(AssertionError):
            _assert_exact_raw_imports(actual_expected - {missing}, **kwargs)
    with pytest.raises(AssertionError):
        _assert_exact_raw_imports(actual_expected | {"py_incref"}, **kwargs)


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_frame_registry_object_has_exact_raw_closure(tmp_path: Path, emitter: str):
    target_triple = host_target_triple()
    obj = _compile_object(tmp_path, emitter, target_triple)
    assert RAW_GLOBAL_IMPORTS <= FREESTANDING_GC_RUNTIME_GLOBALS
    defined, undefined = _object_external_symbols(obj.read_bytes(), target_triple)
    _assert_exact_raw_imports(undefined, target_triple=target_triple, emitter=emitter)
    expected = PUBLIC_SYMBOLS | INTERNAL_SYMBOLS | TLS_STORAGE_SYMBOLS
    tls_initializers = {symbol + "$tlv$init" for symbol in TLS_STORAGE_SYMBOLS}
    assert expected <= defined
    assert defined <= expected | tls_initializers


@pytest.mark.parametrize("target", [
    "x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu",
    "arm64-apple-darwin", "x86_64-pc-windows-msvc",
])
def test_frame_registry_owned_object_has_exact_cross_target_symbols(tmp_path, target):
    obj = _compile_object(tmp_path, "self", target)
    defined, undefined = _object_external_symbols(obj.read_bytes(), target)
    _assert_exact_raw_imports(undefined, target_triple=target, emitter="self")
    assert defined == PUBLIC_SYMBOLS | INTERNAL_SYMBOLS | TLS_STORAGE_SYMBOLS


_FRAME_PROGRAM = """
from pcc.extern import extern, c_ptr, c_int64, c_void
from pcc.unsafe import stack_alloc, store_i32, store_ptr, null
import threading

count: "extern" = extern("pcc_gc_frame_root_slot_count", (), c_int64)
enter: "extern" = extern("pcc_gc_frame_enter", (c_ptr, c_ptr), c_void)
leave: "extern" = extern("pcc_gc_frame_leave", (c_ptr,), c_void)
enter_local: "extern" = extern("pcc_gc_frame_enter_lifo_local", (c_ptr, c_ptr), c_void)
leave_local: "extern" = extern("pcc_gc_frame_leave_lifo_local", (c_ptr,), c_void)
pool_count: "extern" = extern("pcc_gc_frame_node_tls_pool_cached_count", (), c_int64)
pool_drain: "extern" = extern("pcc_gc_frame_node_tls_pool_drain", (), c_void)

def mutate(rounds: int) -> None:
    slots = stack_alloc(16)
    store_ptr(slots, 0, null())
    store_ptr(slots, 8, null())
    frame_map = stack_alloc(4)
    store_i32(frame_map, 0, 2)
    for i in range(rounds):
        enter(frame_map, slots)
        enter_local(frame_map, slots)
        leave_local(slots)
        leave(slots)
    assert pool_count() == 0
    pool_drain()
    assert pool_count() == 0

def mutate_thread(ready, release) -> None:
    slots = stack_alloc(16)
    store_ptr(slots, 0, null())
    store_ptr(slots, 8, null())
    frame_map = stack_alloc(4)
    store_i32(frame_map, 0, 2)
    enter(frame_map, slots)
    enter_local(frame_map, slots)
    ready.set()
    release.wait()
    leave_local(slots)
    leave(slots)
    mutate(512)

def observe(ready, release) -> None:
    for event in ready:
        event.wait()
    assert count() == 0
    release.set()
    for i in range(128):
        assert count() == 0

def main() -> None:
    assert count() == 0
    mutate(16)
    assert count() == 0
    if THREADED:
        ready = [threading.Event() for i in range(4)]
        release = threading.Event()
        workers = [threading.Thread(target=mutate_thread, args=(ready[i], release)) for i in range(4)]
        observer = threading.Thread(target=observe, args=(ready, release))
        for worker in workers:
            worker.start()
        observer.start()
        for worker in workers:
            worker.join()
        observer.join()
    assert count() == 0
    print("frame-registry-ok")

main()
"""


def _run_single_collector_frame_probe(tmp_path, archive, *, threaded):
    source = tmp_path / "frame_probe.py"
    binary = tmp_path / ("frame_probe.exe" if os.name == "nt" else "frame_probe")
    source.write_text(_FRAME_PROGRAM.replace("THREADED", "True" if threaded else "False"))
    pipeline.compile_python(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                            runtime_archive=str(archive))
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    assert (result.returncode, result.stdout, result.stderr) == (0, "frame-registry-ok\n", ""), result


def test_archive_owns_frame_registry_single_collector(tmp_path: Path, pcc_runtime_archive: Path):
    from pcc.backend.elf_x86_64 import read_archive_payloads
    target = host_target_triple()
    owners: dict[str, list[str]] = {symbol: [] for symbol in PUBLIC_SYMBOLS | INTERNAL_SYMBOLS}
    for member, payload in read_archive_payloads(pcc_runtime_archive.read_bytes()):
        defined, _ = _object_external_symbols(payload, target)
        for symbol in defined & owners.keys():
            owners[symbol].append(member)
    assert all(names == ["freestanding_gc_frame_registry.o"] for names in owners.values())
    # The old collector-specific global registration and relocation pool
    # assertions are retired. Keep the actual exported ABI and no-op path.
    _run_single_collector_frame_probe(tmp_path, pcc_runtime_archive, threaded=False)


def test_frame_registry_survives_threaded_mutation_and_observation(tmp_path: Path, pcc_runtime_archive: Path):
    _run_single_collector_frame_probe(tmp_path, pcc_runtime_archive, threaded=True)
