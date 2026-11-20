"""Invariant PHI uses remain predecessor-specific in x86 liveness."""

import pytest

from pcc.backend.precise_stackmap import RECORD_HAS_EXCEPTION_EDGE, SAFEPOINT_EXCEPTION
from pcc.backend.self_backend_kernel import IndexedFunctionKernel, get_indexed_function_kernel
from pcc.backend.self_backend_precise_stackmaps import (
    _managed_live_after,
    build_function_stack_map_plan,
)
from pcc.backend.self_backend_prepare import prepare_module_for_target
from tests.python.test_managed_phi_liveness import _call_states, _phi_module


TARGET = "x86_64-unknown-linux-gnu"
ROOTED_LOOP = f"""
target triple = "{TARGET}"

@frame_map = internal constant i32 1
declare void @pcc_gc_frame_enter(ptr, ptr)
declare void @pcc_gc_frame_leave(ptr)
declare void @pcc_thread_safepoint()
declare i64 @py_err_occurred()
declare void @opaque_call(ptr)

define i64 @rooted_phi(ptr %obj, i1 %take_left, i1 %again, i32 %tag) {{
entry:
  %root = alloca ptr, align 8
  store ptr %obj, ptr %root, align 8
  call void @pcc_gc_frame_enter(ptr @frame_map, ptr %root)
  br i1 %take_left, label %left, label %right

left:
  %lp = load ptr, ptr %root, align 8
  %ld = getelementptr i8, ptr %lp, i64 24
  call void @pcc_thread_safepoint()
  switch i32 %tag, label %loop [
    i32 0, label %loop
    i32 1, label %failure
  ]

right:
  %rp = load ptr, ptr %root, align 8
  %rd = getelementptr i8, ptr %rp, i64 24
  call void @pcc_thread_safepoint()
  br label %loop

loop:
  %chosen = phi ptr [ %rd, %right ], [ %ld, %left ], [ %next, %latch ], [ %ld, %left ]
  %ordinal = phi i64 [ 1, %left ], [ 3, %latch ], [ 1, %left ], [ 2, %right ]
  %alias = getelementptr i8, ptr %chosen, i64 0
  call void @pcc_thread_safepoint()
  %err = call i64 @py_err_occurred()
  %failed = icmp ne i64 %err, 0
  br i1 %failed, label %failure, label %body

body:
  call void @opaque_call(ptr %alias)
  br i1 %again, label %latch, label %done

latch:
  %next = getelementptr i8, ptr %alias, i64 0
  call void @pcc_thread_safepoint()
  br label %loop

failure:
  call void @pcc_gc_frame_leave(ptr %root)
  ret i64 -1

done:
  call void @pcc_gc_frame_leave(ptr %root)
  ret i64 %ordinal
}}
""".strip()


def _prepare(source):
    return prepare_module_for_target(
        source, aggregate_returned_indirect=lambda _type: False,
    )


@pytest.mark.parametrize("selection", ["all", "alternating", "empty"])
def test_real_phi_rows_are_read_once_without_changing_call_liveness(monkeypatch, selection):
    width = 12
    source = _phi_module(width, True).replace("arm64-apple-darwin23.6.0", TARGET)
    prepared = _prepare(source)
    func = prepared.functions[0]
    kernel = get_indexed_function_kernel(func)
    observed = []
    phi_incoming = IndexedFunctionKernel.phi_incoming

    def counted(self, index):
        if self is kernel:
            observed.append(index)
        return phi_incoming(self, index)

    monkeypatch.setattr(IndexedFunctionKernel, "phi_incoming", counted)
    names = [f"p{i}" for i in range(width)] + ["chosen"]
    if selection == "alternating":
        names = names[1::2] + ["chosen"]
    elif selection == "empty":
        names = []
    tracked = frozenset(kernel.value_id(name) for name in names)
    assert all(value_id >= 0 for value_id in tracked)
    after = _managed_live_after(func, tracked)
    try:
        expected = []
        for i in range(width):
            named = f"p{i}"
            wanted = (named,) if named in names else ()
            expected.extend(((f"case{i}", 0, wanted), (f"case{i}", 1, wanted)))
        chosen = ("chosen",) if "chosen" in names else ()
        expected.extend((("join", 0, chosen), ("repeat", 0, chosen), ("exit", 0, chosen)))
        assert _call_states(kernel, after) == expected
        assert len(observed) == (width + 1 if tracked else 0)
    finally:
        after.close()
        kernel.close_native_tables()


def test_rooted_loop_keeps_duplicate_edges_aliases_and_exception_reloads():
    prepared = _prepare(ROOTED_LOOP)
    func = prepared.functions[0]
    try:
        plan = build_function_stack_map_plan(func, prepared.globals_, target="x86_64-linux")
        records = {record.label: record for record in plan.diagnostic_records()}
        by_site = {
            (block, instruction): records[label]
            for block, instruction, label in plan.instruction_suffix_labels
        }
        for site in (("left", 2), ("right", 2), ("loop", 1), ("latch", 1)):
            record = by_site[site]
            assert len(record.locations) == len(record.reloads) == 1
            assert record.reloads[0].source_offset == record.locations[0].offset
            assert record.reloads[0].derived_offset == 24
        exception = by_site[("loop", 2)]
        assert exception.kind == SAFEPOINT_EXCEPTION
        assert exception.exceptional_block == "failure"
        assert exception.flags == RECORD_HAS_EXCEPTION_EDGE
        assert [reload.derived_offset for reload in exception.reloads] == [24]
    finally:
        get_indexed_function_kernel(func).close_native_tables()
