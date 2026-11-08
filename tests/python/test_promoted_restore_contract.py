"""Small structural mutations for the optimized consumer's ownership oracle.

These are parser/checker tests, not native execution or a collector model.
Invalid variants deliberately bypass the production verifier so the tested
contract itself must reject them. Production emitter tests remain strict.
"""
import pytest

from tests.python.test_generator_frame_take_group import assert_group_restore_contract
from tests.python.test_generator_operand_root_groups import _parsed_function


_MIXED = r'''
@own.map = constant i32 2
@borrowed.map = constant i32 -2
declare void @pcc_gc_frame_enter(ptr, ptr)
declare void @pcc_gc_frame_leave(ptr)
declare void @pcc_gc_store_root(ptr, ptr)
declare ptr @py_gen_frame_take(ptr, i64)
declare i64 @py_gen_frame_take_group(ptr, i64, i64, ptr)
define ptr @resume(ptr %frame, i64 %state) {
entry:
  %outputs = alloca [2 x ptr]
  %cell0 = getelementptr [2 x ptr], ptr %outputs, i32 0, i32 0
  %cell1 = getelementptr [2 x ptr], ptr %outputs, i32 0, i32 1
  store ptr null, ptr %cell0
  store ptr null, ptr %cell1
  call void @pcc_gc_frame_enter(ptr @own.map, ptr %outputs)
  %borrowed = alloca [2 x ptr]
  %frame.cell = getelementptr [2 x ptr], ptr %borrowed, i32 0, i32 1
  store ptr null, ptr %borrowed
  store ptr %frame, ptr %frame.cell
  call void @pcc_gc_frame_enter(ptr @borrowed.map, ptr %borrowed)
  %memory.flag = alloca i1
  store i1 0, ptr %memory.flag
  %empty = alloca ptr
  store ptr null, ptr %empty
  br label %gen.dispatch
gen.dispatch:
  %receiver = load ptr, ptr %frame.cell
  %taken = call i64 @py_gen_frame_take_group(ptr %receiver, i64 0, i64 2, ptr %cell0)
  %accepted = icmp ne i64 %taken, 0
  br i1 %accepted, label %gen.operand.group.owned.1, label %gen.operand.scalar.1
gen.operand.group.owned.1:
  store i1 1, ptr %memory.flag
  br label %gen.operand.restored.1
gen.operand.scalar.1:
  %frame.first = load ptr, ptr %frame.cell
  %gen.operand.restore.0 = call ptr @py_gen_frame_take(ptr %frame.first, i64 0)
  store ptr %gen.operand.restore.0, ptr %cell0
  %frame.second = load ptr, ptr %frame.cell
  %gen.operand.restore.1 = call ptr @py_gen_frame_take(ptr %frame.second, i64 1)
  store ptr %gen.operand.restore.1, ptr %cell1
  store i1 1, ptr %memory.flag
  br label %gen.operand.restored.1
gen.operand.restored.1:
  %promoted = phi i1 [ 1, %gen.operand.group.owned.1 ], [ 1, %gen.operand.scalar.1 ]
  br label %gen.select
gen.select:
  switch i64 %state, label %normal [ i64 1, label %error ]
normal:
  br i1 %promoted, label %release, label %continue
release:
  call void @pcc_gc_store_root(ptr %cell0, ptr null)
  br label %continue
continue:
  br label %finish
error:
  %forward = phi i1 [ %promoted, %gen.select ]
  %selected = select i1 %forward, ptr %cell0, ptr %empty
  call void @pcc_gc_store_root(ptr %selected, ptr null)
  br label %finish
finish:
  call void @pcc_gc_store_root(ptr %cell1, ptr null)
  call void @pcc_gc_frame_leave(ptr %borrowed)
  call void @pcc_gc_frame_leave(ptr %outputs)
  ret ptr null
}
'''


def _unchecked(text):
    from pcc.backend.self_backend_kernel import get_indexed_function_kernel
    from pcc.backend.self_backend_parse import parse_self_backend_module
    module = parse_self_backend_module(text)
    function = next(function for function in module.functions if function.name == "resume")
    return module, get_indexed_function_kernel(function).materialize_legacy_blocks(function)


def test_mixed_memory_and_promoted_flags_preserve_the_same_restore_contract():
    module, blocks = _parsed_function(_MIXED, "resume")
    batches = assert_group_restore_contract(blocks, module.globals_, promoted_owners=True)
    assert list(batches.values()) == [((0, ("outputs", 0)), (1, ("outputs", 8)))]
    with pytest.raises(AssertionError, match="immediate ownership store"):
        assert_group_restore_contract(blocks, module.globals_)


@pytest.mark.parametrize("before, after, diagnostic", [
    ("  %promoted = phi i1 [ 1, %gen.operand.group.owned.1 ], [ 1, %gen.operand.scalar.1 ]\n",
     "", "each missing memory flag"),
    ("[ 1, %gen.operand.scalar.1 ]", "[ 0, %gen.operand.scalar.1 ]", "true on both"),
    ("[ 1, %gen.operand.scalar.1 ]", "[ 1, %entry ]", "true on both"),
    ("release:\n  call void @pcc_gc_store_root(ptr %cell0, ptr null)",
     "release:\n  call void @pcc_gc_store_root(ptr %cell1, ptr null)", "exactly one physical output"),
    ("select i1 %forward, ptr %cell0, ptr %empty",
     "select i1 %forward, ptr %cell1, ptr %empty", "exactly one physical output"),
    ("store ptr null, ptr %empty", "store ptr %frame, ptr %empty", "must be empty"),
    ("call void @pcc_gc_store_root(ptr %selected, ptr null)",
     "call void @pcc_gc_store_root(ptr %cell0, ptr null)", "projection is unused"),
    ("@own.map = constant i32 2", "@own.map = constant i32 1", "covering owning registration"),
    ("@py_gen_frame_take(ptr %frame.second, i64 1)",
     "@py_gen_frame_take(ptr %frame.first, i64 1)", "freshly reload"),
    ("icmp ne i64 %taken, 0", "icmp eq i64 %taken, 0", None),
    ("label %gen.operand.group.owned.1, label %gen.operand.scalar.1",
     "label %gen.operand.scalar.1, label %gen.operand.group.owned.1", None),
    ("gen.operand.group.owned.1:\n  store i1 1, ptr %memory.flag",
     "gen.operand.group.owned.1:\n  store i1 0, ptr %memory.flag", None),
    ("%forward = phi i1 [ %promoted, %gen.select ]",
     "%forward = phi i1 [ %promoted, %normal ]", "actual predecessors"),
    ("gen.select:\n  switch i64 %state, label %normal [ i64 1, label %error ]",
     "gen.select:\n  switch i64 %state, label %normal [ i64 1, label %error i64 2, label %release ]",
     "ownership guard"),
    ("continue:\n  br label %finish", "continue:\n  br label %gen.operand.restored.1",
     "unexpected predecessor"),
    ("continue:\n  br label %finish", "continue:\n  br label %gen.operand.group.owned.1",
     "sole admission guard"),
    ("br label %gen.dispatch", "br i1 %promoted, label %gen.dispatch, label %gen.dispatch",
     "does not dominate"),
])
def test_optimized_restore_checker_rejects_broken_ownership(before, after, diagnostic):
    module, blocks = _parsed_function(_MIXED, "resume")
    assert_group_restore_contract(blocks, module.globals_, promoted_owners=True)
    assert _MIXED.count(before) == 1, "mutation must change exactly its intended site"
    changed = _MIXED.replace(before, after)
    assert changed != _MIXED
    module, blocks = _unchecked(changed)
    with pytest.raises(AssertionError, match=diagnostic):
        assert_group_restore_contract(blocks, module.globals_, promoted_owners=True)
