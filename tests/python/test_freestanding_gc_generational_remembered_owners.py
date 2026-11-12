from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

from pcc.frontends.python import pipeline
from pcc.frontends.python.codegen.runtime_abi import FREESTANDING_GC_RUNTIME_GLOBALS


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "runtime"
STRICT_SOURCE = (
    RUNTIME_DIR / "py" / "freestanding_gc_generational_remembered_owners.py"
)
MANAGED_SOURCE = RUNTIME_DIR / "py" / "py_gc_backend.py"
MAKEFILE = RUNTIME_DIR / "Makefile"
SCHEDULER_SOURCE = (
    RUNTIME_DIR / "py" / "freestanding_gc_generational_scheduler.py"
)

OWNED_SYMBOLS = {
    "pcc_gc_backend3_remember_owner",
    "pcc_gc_backend3_remembered_owner_list_head",
    "pcc_gc_backend3_remembered_owner_list_set_head",
}
RAW_FUNCTION_IMPORTS = {
    "malloc",
}
RAW_GLOBAL_IMPORTS = {
    "pcc_gc_backend3_remembered_owner_allocation_limit",
    "pcc_gc_backend3_remembered_overflow",
    "pcc_gc_backend3_remembered_owner_head",
    "pcc_gc_backend3_remembered_scan_cursor",
    "pcc_gc_backend3_remembered_scan_revision",
}


def _exported_symbols(source: str) -> set[str]:
    return set(re.findall(r'@c_abi_export\("([^"]+)"\)', source))


def _literal_global_imports() -> set[str]:
    globals_: set[str] = set()
    tree = ast.parse(STRICT_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in {"global_addr", "global_load_ptr", "global_store_ptr"}:
            continue
        if not node.args:
            continue
        value = node.args[0]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            globals_.add(value.value)
    return globals_


def _export_body(source: str, symbol: str) -> str:
    return source.split(f'@c_abi_export("{symbol}")', 1)[1].split(
        "\n@c_abi_export", 1
    )[0]


def test_telemetry_reset_preserves_pending_gc3_work_and_gc4_epoch_contract() -> None:
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    reset = _export_body(managed, "pcc_gc_telemetry_reset")
    assert "_backend3_clear_remembered_owners(" not in reset
    assert "_backend3_finish_detached_remembered_owners(" not in reset
    assert "_backend4_reset_store_buffer_epoch_state()" in reset
    assert "_backend4_reset_remembered_set_epoch_state()" in reset
    assert "_backend4_reseed_relocation_epoch_state()" in reset
    assert "_backend4_clear_large_deferred_flags()" in reset


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_generational_remembered_owners_have_exact_strict_object_closure(
    tmp_path: Path, emitter: str
) -> None:
    llvm_ir = tmp_path / ("generational_remembered_owners_" + emitter + ".ll")
    pipeline.compile_python(
        str(STRICT_SOURCE),
        str(llvm_ir),
        emit_llvm_only=True,
        python_library=True,
    )
    source = llvm_ir
    if emitter == "self":
        from pcc.backend.self_backend_dispatch import emit_self_asm

        source = tmp_path / "generational_remembered_owners.s"
        source.write_text(
            emit_self_asm(llvm_ir.read_text(encoding="utf-8")), encoding="utf-8"
        )
    obj = tmp_path / ("generational_remembered_owners_" + emitter + ".o")
    result = subprocess.run(
        ["clang", "-c", str(source), "-o", str(obj)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _literal_global_imports() == RAW_GLOBAL_IMPORTS
    assert RAW_GLOBAL_IMPORTS <= FREESTANDING_GC_RUNTIME_GLOBALS

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
    assert undefined == RAW_FUNCTION_IMPORTS | RAW_GLOBAL_IMPORTS

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


def test_production_archive_has_one_generational_remembered_owner_provider(
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
        assert ":freestanding_gc_generational_remembered_owners.o:" in owners[0]
        assert ":py_gc_backend.o:" not in owners[0]
