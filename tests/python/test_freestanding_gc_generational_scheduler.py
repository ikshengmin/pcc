from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from pcc.frontends.python import pipeline
from pcc.frontends.python.codegen.runtime_abi import (
    FREESTANDING_GC_CROSS_OBJECT_SIGNATURES,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "runtime"
STRICT_SOURCE = RUNTIME_DIR / "py" / "freestanding_gc_generational_scheduler.py"
MANAGED_SOURCE = RUNTIME_DIR / "py" / "py_gc_backend.py"
MAKEFILE = RUNTIME_DIR / "Makefile"

OWNED_SYMBOLS = {
    "pcc_gc_backend3_enable_closed_young_domain",
    "pcc_gc_backend3_revoke_closed_young_domain",
    "pcc_gc_backend4_step_generation_aging",
    "pcc_gc_backend4_step_remembered_roots",
    "pcc_gc_backend4_drain_remembered_batch",
}
RAW_FUNCTION_IMPORTS = {
    # A complete, compiler-admitted young cut uses the stopped root snapshot
    # and existing typed disposal owner. Budgeted promotion does not run it.
    "pcc_gc_backend3_closed_young_domain",
    "pcc_platform_getenv",
    "free",
    # The managed runtime's builtin type root table (slots past the 65 exception
    # classes are scanned from it).
    # The remembered-roots drain attributes medium nodes another thread
    # enqueued (983254526).
    "pcc_current_thread_id",
    "pcc_gc_backend4_store_buffer_cross_thread_medium_flushed_entries_count",
    "pcc_gc_backend4_store_buffer_cross_thread_medium_flushes_count",
    "pcc_gc_backend4_store_buffer_drain_batches_count",
    "pcc_gc_backend4_store_buffer_drained_entries_count",
    "pcc_gc_backend4_store_buffer_entries_count",
    "pcc_gc_backend4_store_buffer_full_batches_count",
    "pcc_gc_backend4_store_buffer_head",
    "pcc_gc_backend4_store_buffer_incomplete_drains_count",
    "pcc_gc_backend4_store_buffer_max_batch_size_count",
    "pcc_gc_backend4_store_buffer_medium_count",
    "pcc_gc_backend4_store_buffer_medium_flushed_entries_count",
    "pcc_gc_backend4_store_buffer_medium_flushes_count",
    "pcc_gc_backend4_store_buffer_medium_full_flushes_count",
    "pcc_gc_backend4_store_buffer_medium_head",
    "pcc_gc_backend4_young_promotions",
    "pcc_gc_backend3_drain_promotion_worklist",
    "pcc_gc_backend3_young_list_head",
    "pcc_gc_backend3_young_unlink",
    "pcc_gc_generational_promote_owned_slot_mode",
    "pcc_gc_generational_promote_young_if_known",
    "pcc_gc_object_is_known_no_lock",
    "pcc_gc_object_node_is_active",
    "pcc_gc_store_buffer_member_forget",
    "pcc_gc_store_buffer_entry_forget",
    "pcc_gc_store_buffer_owner_entries",
    # The young step visits the walked mutator stack roots.
    "pcc_gc_trace_referents_for_promotion",
    "pcc_py_gc_minor_graph_lock",
    "pcc_py_gc_minor_graph_unlock",
    "pcc_thread_safepoint",
    "py_decref",
}


def _exported_symbols(source: str) -> set[str]:
    return set(re.findall(r'@c_abi_export\("([^"]+)"\)', source))


def _export_body(source: str, symbol: str) -> str:
    return source.split(f'@c_abi_export("{symbol}")', 1)[1].split(
        "\n@c_abi_export", 1
    )[0]


def test_generational_scheduler_has_one_strict_source_owner() -> None:
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "__pcc_freestanding__ = True" in strict
    assert _exported_symbols(strict) == OWNED_SYMBOLS
    assert _exported_symbols(managed).isdisjoint(OWNED_SYMBOLS)
    assert "freestanding_gc_generational_scheduler" in makefile
    assert "def _promote_frame_roots(" not in managed
    assert "def _promote_tls_exception_root(" not in managed


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_generational_scheduler_has_exact_strict_object_closure(
    tmp_path: Path, emitter: str
) -> None:
    llvm_ir = tmp_path / ("generational_scheduler_" + emitter + ".ll")
    pipeline.compile_python(
        str(STRICT_SOURCE),
        str(llvm_ir),
        emit_llvm_only=True,
        python_library=True,
    )
    source = llvm_ir
    if emitter == "self":
        from pcc.backend.self_backend_dispatch import emit_self_asm

        source = tmp_path / "generational_scheduler.s"
        source.write_text(
            emit_self_asm(llvm_ir.read_text(encoding="utf-8")), encoding="utf-8"
        )
    obj = tmp_path / ("generational_scheduler_" + emitter + ".o")
    result = subprocess.run(
        ["clang", "-c", str(source), "-o", str(obj)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

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
    assert undefined == RAW_FUNCTION_IMPORTS

    symbols_result = subprocess.run(
        ["nm", "-g", str(obj)], capture_output=True, text=True, timeout=30
    )
    assert symbols_result.returncode == 0, symbols_result.stdout + symbols_result.stderr
    defined = {
        line.split()[-1].lstrip("_")
        for line in symbols_result.stdout.splitlines()
        if line.strip() and " U " not in line
    }
    assert defined == OWNED_SYMBOLS


def test_production_archive_has_one_generational_scheduler_owner(
    pcc_runtime_archive: Path,
) -> None:
    symbols_result = subprocess.run(
        ["nm", "-A", "-g", str(pcc_runtime_archive)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert symbols_result.returncode == 0, symbols_result.stdout + symbols_result.stderr
    for symbol in OWNED_SYMBOLS:
        owners = [
            line
            for line in symbols_result.stdout.splitlines()
            if line.strip()
            and line.split()[-1].lstrip("_") == symbol
            and " U " not in line
        ]
        assert len(owners) == 1, (symbol, owners)
        assert ":freestanding_gc_generational_scheduler.o:" in owners[0]
        assert ":py_gc_backend.o:" not in owners[0]
