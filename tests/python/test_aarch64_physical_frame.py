"""Physical frames preserve the compiler-contract root/reload and native ABI.

The collect fixture is emitted production IR, not a replacement runtime. The
standalone native test owns its tiny root-rewrite fixture in ordinary IR.
"""
from pathlib import Path
import platform
import subprocess
import sys

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import self_backend_aarch64_darwin as backend
from pcc.backend.self_backend_aarch64_darwin_abi import (
    aggregate_returned_indirect,
    aggregate_returned_indirect_indexed,
)
from pcc.backend.self_backend_aarch64_darwin_regalloc import (
    allocate_aarch64_block_registers,
    note_aarch64_reload_destinations,
)
from pcc.backend.self_backend_aarch64_frame import compact_aarch64_frame
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_prepare import prepare_module_for_target
from pcc.backend.self_backend_precise_stackmaps import build_function_stack_map_plan
from tests.python.test_precise_stackmap_abi import _stale_managed_ssa_ir


TRIPLE = 'target triple = "arm64-apple-darwin"\n'
COLLECT = Path(__file__).parents[1] / "fixtures/ir/aarch64_collect_frame.ll"


def prepare(source, dense=True):
    return prepare_module_for_target(
        source, aggregate_returned_indirect=aggregate_returned_indirect,
        aggregate_returned_indirect_indexed=aggregate_returned_indirect_indexed,
        materialize_legacy_slots=not dense,
    )


def plan(function, module):
    return build_function_stack_map_plan(function, module.globals_, target="aarch64-darwin")


def test_real_collect_shape_reclaims_unused_spill_holes(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    observations = []
    original = backend.compact_aarch64_frame
    def capture(function, old_plan):
        layout = original(function, old_plan)
        observations.append((layout.original_size, function.frame_size,
                             list(layout.padding), len(function.aarch64_callee_saved)))
        return layout
    monkeypatch.setattr(backend, "compact_aarch64_frame", capture)
    assembly = backend.emit_aarch64_darwin_asm(COLLECT.read_text())
    assert observations == [(144, 32, [(24, 32)], 6)]
    body = assembly.split("_pcc_gc_collect:", 1)[1].split(".section", 1)[0]
    assert "[sp, #-80]!" in body
    assert "[x29, #-32]" in body and "xzr" in body
    for old in (120, 128, 136):
        assert f"[x29, #-{old}]" not in body
    for new in (8, 16, 24):
        assert f"[x29, #-{new}]" in body


@pytest.mark.parametrize("dense", (False, True))
def test_rebuilt_managed_derived_reload_moves_both_endpoints(dense):
    module = prepare(_stale_managed_ssa_ir("arm64-apple-darwin"), dense)
    function, = module.functions
    kernel = get_indexed_function_kernel(function)
    old_plan = plan(function, module)
    old_slots = list(kernel.slot_scalars.get_unchecked(i) for i in range(len(kernel.slot_scalars)))
    note_aarch64_reload_destinations(function, old_plan)
    allocate_aarch64_block_registers(function)
    layout = compact_aarch64_frame(function, old_plan)
    try:
        updated = plan(function, module)
        try:
            assert updated.frame_size == function.frame_size
            reloads = [reload for record in updated.diagnostic_records() for reload in record.reloads]
            assert reloads and all(reload.derived_offset == 24 for reload in reloads)
            root_id = kernel.value_id("root")
            root_offset = -kernel.alloca_offset(root_id)
            assert all(reload.source_offset == root_offset for reload in reloads)
            assert all(-function.frame_size <= reload.destination_offset < 0 for reload in reloads)
            # Dense slot identity/type lanes stay unchanged, including aliases.
            assert [kernel.slot_scalars.get_unchecked(i) for i in range(1, len(kernel.slot_scalars), 2)] == old_slots[1::2]
        finally:
            updated.packed_records.close()
    finally:
        layout.restore(function)
        old_plan.packed_records.close()
    assert [kernel.slot_scalars.get_unchecked(i) for i in range(len(kernel.slot_scalars))] == old_slots


def test_whole_addressable_alloca_and_hidden_sret_remain_disjoint():
    source = TRIPLE + '''
declare void @consume(ptr)
define {i64, i64, i64} @aggregate(i64 %arg) {
entry:
  %buf = alloca [25 x i8], align 8
  %end = getelementptr i8, ptr %buf, i64 24
  store i8 77, ptr %end
  call void @consume(ptr %buf)
  ret {i64, i64, i64} zeroinitializer
}
'''
    module = prepare(source)
    function, = module.functions
    kernel = get_indexed_function_kernel(function)
    old_plan = plan(function, module)
    note_aarch64_reload_destinations(function, old_plan)
    allocate_aarch64_block_registers(function)
    layout = compact_aarch64_frame(function, old_plan)
    try:
        buf = kernel.alloca_offset(kernel.value_id("buf"))
        sret = kernel.slot_offset(kernel.hidden_sret_slot_id)
        assert buf >= 25 and buf <= function.frame_size and buf % 8 == 0
        assert sret <= buf - 25 or sret - 8 >= buf
        for low, high in layout.padding:
            assert high <= buf - 25 or low >= buf
            assert high <= sret - 8 or low >= sret
    finally:
        layout.restore(function)
        old_plan.packed_records.close()
    assembly = backend.emit_aarch64_darwin_asm(source)
    assert "sturb wzr" in assembly  # byte padding beside the 25-byte object


@pytest.mark.parametrize("kind", ("dynamic", "tls", "linux_vararg"))
def test_compaction_keeps_distinct_platform_frame_obligations(kind):
    if kind == "dynamic":
        source = TRIPLE + '''
declare ptr @llvm.pcc.dynamic.alloca(i64, i64)
define i64 @probe(i64 %size) {
entry:
  %data = call ptr @llvm.pcc.dynamic.alloca(i64 %size, i64 16)
  store i64 57, ptr %data
  %value = load i64, ptr %data
  ret i64 %value
}
'''
        assembly = backend.emit_aarch64_darwin_asm(source)
        assert "mov sp, x29" in assembly or "sub sp, x29," in assembly
        assert "paciasp" in assembly and "autiasp" in assembly
    elif kind == "tls":
        source = TRIPLE + '''
@cell = thread_local global i64 4
define i64 @probe(i64 %arg) {
entry:
  %value = load i64, ptr @cell
  %result = add i64 %value, %arg
  ret i64 %result
}
'''
        assembly = backend.emit_aarch64_darwin_asm(source)
        assert "blr x16" in assembly and "paciasp" in assembly and "autiasp" in assembly
    else:
        source = '''target triple = "aarch64-unknown-linux-gnu"
define i64 @probe(i64 %arg, ...) {
entry:
  %result = add i64 %arg, 1
  ret i64 %result
}
'''
        assembly = backend.emit_aarch64_darwin_asm(source)
        assert "str q7, [sp, #176]" in assembly
        assert "str x7, [sp, #56]" in assembly


def test_branch_root_groups_and_array_interior_slots_rebuild():
    source = TRIPLE + '''
@frame_map = internal constant i32 2
declare void @pcc_gc_frame_enter(ptr, ptr)
declare void @pcc_gc_frame_leave(ptr)
declare void @pcc_thread_safepoint()
define void @branch(ptr %obj, i1 %choose) {
entry:
  %roots = alloca [2 x ptr], align 8
  %other = getelementptr i8, ptr %roots, i64 8
  store ptr null, ptr %roots
  store ptr null, ptr %other
  call void @pcc_gc_frame_enter(ptr @frame_map, ptr %roots)
  br i1 %choose, label %left, label %right
left:
  store ptr %obj, ptr %roots
  br label %join
right:
  store ptr %obj, ptr %other
  br label %join
join:
  call void @pcc_thread_safepoint()
  call void @pcc_gc_frame_leave(ptr %roots)
  ret void
}
'''
    module = prepare(source)
    function, = module.functions
    kernel = get_indexed_function_kernel(function)
    old_plan = plan(function, module)
    note_aarch64_reload_destinations(function, old_plan)
    allocate_aarch64_block_registers(function)
    layout = compact_aarch64_frame(function, old_plan)
    try:
        updated = plan(function, module)
        try:
            locations = {location.offset for record in updated.diagnostic_records()
                         for location in record.locations}
            base = -kernel.alloca_offset(kernel.value_id("roots"))
            assert locations == {base, base + 8}
        finally:
            updated.packed_records.close()
    finally:
        layout.restore(function)
        old_plan.packed_records.close()
    assert "_branch:" in backend.emit_aarch64_darwin_asm(source)


def test_emission_failure_restores_nominal_layout(monkeypatch):
    module = prepare(COLLECT.read_text())
    function, = module.functions
    kernel = get_indexed_function_kernel(function)
    old = function.frame_size, tuple(kernel.slot_scalars.get_unchecked(i) for i in range(len(kernel.slot_scalars)))
    real = backend._emit_function
    def fail(*args, **kwargs):
        raise BackendUnavailable("deliberate emission failure")
    monkeypatch.setattr(backend, "_emit_function", fail)
    with pytest.raises(BackendUnavailable, match="deliberate"):
        backend._emit_prepared_aarch64_darwin_module(module, optimize=True, close_native_tables=False)
    assert (function.frame_size, tuple(kernel.slot_scalars.get_unchecked(i) for i in range(len(kernel.slot_scalars)))) == old
    monkeypatch.setattr(backend, "_emit_function", real)
    assert backend._emit_prepared_aarch64_darwin_module(module, optimize=True, close_native_tables=False) == backend._emit_prepared_aarch64_darwin_module(module, optimize=True, close_native_tables=False)


@pytest.mark.pcc_gate(probe=lambda: sys.platform == "darwin" and platform.machine() == "arm64")
def test_owned_native_root_reload_survives_compaction(tmp_path):
    from pcc.backend.macho_exec import link_executable
    from pcc.backend.owned_object_emit import emit_owned_object
    source = _stale_managed_ssa_ir("arm64-apple-darwin")
    # Replace declarations with actual fixture bodies, so no runtime archive
    # or external assembler/linker participates in this codegen proof.
    source = "\n".join(line for line in source.splitlines() if not line.startswith("declare "))
    source += '''
@slot = global ptr null
@replacement = global ptr null
@failed = global i32 1
define void @pcc_gc_frame_enter(ptr %map, ptr %root) {
entry:
  store ptr %root, ptr @slot
  ret void
}
define void @pcc_gc_frame_leave(ptr %root) {
entry:
  ret void
}
define void @pcc_thread_safepoint() {
entry:
  %root = load ptr, ptr @slot
  %new = load ptr, ptr @replacement
  store ptr %new, ptr %root
  ret void
}
define void @opaque_call(ptr %obj) {
entry:
  %new = load ptr, ptr @replacement
  %expected = getelementptr i8, ptr %new, i64 24
  %wrong = icmp ne ptr %obj, %expected
  %code = zext i1 %wrong to i32
  store i32 %code, ptr @failed
  ret void
}
define i32 @main() {
entry:
  %old = alloca [64 x i8], align 8
  %new = alloca [64 x i8], align 8
  store ptr %new, ptr @replacement
  call void @refresh(ptr %old, ptr null, i1 false)
  %code = load i32, ptr @failed
  ret i32 %code
}
'''
    output = tmp_path / "frame-reload"
    output.write_bytes(link_executable([emit_owned_object(source, "arm64-apple-darwin")], entry="_main"))
    output.chmod(0o755)
    result = subprocess.run([str(output)], text=True, capture_output=True, timeout=10)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
