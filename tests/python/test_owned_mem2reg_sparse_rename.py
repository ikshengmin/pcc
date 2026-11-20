"""The dominator walk restores only slots a block wrote."""

from pcc.ir.optimization import mem2reg as owned
from pcc.ir.optimization.ir_mutator import MutableModule
from tests.owned_ir_validation import verify_ir_text


NESTED = """define i64 @nested(i1 %choose, i1 %again, i64 %initial) {
entry:
  %slot = alloca i64
  store i64 %initial, ptr %slot
  br i1 %choose, label %left, label %right
left:
  store i64 11, ptr %slot
  store i64 12, ptr %slot
  br label %join
right:
  %sibling = load i64, ptr %slot
  %inc = add i64 %sibling, 3
  store i64 %inc, ptr %slot
  br label %join
dead:
  br label %join
join:
  br label %loop
loop:
  %value = load i64, ptr %slot
  %first = add i64 %value, 10
  store i64 %first, ptr %slot
  %second = add i64 %first, 1
  store i64 %second, ptr %slot
  br i1 %again, label %latch, label %exit
latch:
  %from_loop = load i64, ptr %slot
  %carried = add i64 %from_loop, 7
  store i64 %carried, ptr %slot
  br label %loop
exit:
  %answer = load i64, ptr %slot
  ret i64 %answer
}
"""

ESCAPING = """declare void @opaque(ptr)

define void @escape() {
entry:
  %slot = alloca i64
  store i64 2, ptr %slot
  call void @opaque(ptr %slot)
  ret void
}
"""


def _sparse_chain(width, depth):
    lines = ["define i64 @chain() {", "entry:"]
    for i in range(width):
        lines.extend((f"  %slot{i} = alloca i64", f"  store i64 {i}, ptr %slot{i}"))
    lines.append("  br label %step0")
    for i in range(depth):
        lines.extend((f"step{i}:", f"  br label %{'exit' if i == depth - 1 else 'step' + str(i + 1)}"))
    lines.append("exit:")
    for i in range(width):
        lines.append(f"  %load{i} = load i64, ptr %slot{i}")
        left = "0" if i == 0 else f"%sum{i - 1}"
        lines.append(f"  %sum{i} = add i64 {left}, %load{i}")
    lines.extend((f"  ret i64 %sum{width - 1}", "}", ""))
    return "\n".join(lines)


def _count_slot_visits(depth):
    width = 8
    source = _sparse_chain(width, depth)
    function = MutableModule.parse(source).functions[0]
    successors, predecessors = owned._build_cfg(function)
    entry = function.blocks[0].name
    order = owned._reverse_postorder(entry, successors)
    reachable = set(order)
    idom = owned._immediate_dominators(order, predecessors, reachable)
    frontiers = owned._dominance_frontiers(order, predecessors, idom, reachable)
    once = owned._single_execution_prologue(entry, successors, predecessors)
    scalars = owned._entry_block_candidates(function, once)
    uses = owned._collect_uses(function, scalars, reachable)
    children = owned._dominator_children(order, idom, entry)
    assert len(uses) == width
    visits = 0

    class CountedName(str):
        def __hash__(self):
            nonlocal visits
            visits += 1
            return str.__hash__(self)

    observed_uses = {CountedName(name): item for name, item in uses.items()}
    started = visits
    plan = owned._rename(
        function, observed_uses, successors, predecessors, idom, frontiers,
        children, entry, reachable,
    )
    observed_visits = visits - started
    assert plan is not None and plan["phi_lines"] == {}
    assert plan["replacements"] == {f"load{i}": str(i) for i in range(width)}
    return observed_visits


def test_dominator_blocks_without_writes_do_not_rescan_slots():
    assert _count_slot_visits(128) == _count_slot_visits(8)


def test_repeated_writes_do_not_leak_across_siblings_or_loop_exits():
    result, changed = owned.mem2reg_text(NESTED)
    assert changed
    verify_ir_text(result)
    assert "  %inc = add i64 %initial, 3" in result
    assert "[ 12, %left ], [ %inc, %right ], [ undef, %dead ]" in result
    assert "[ %slot.phi.1, %join ], [ %carried, %latch ]" in result
    assert "  %carried = add i64 %second, 7" in result
    assert "  ret i64 %second" in result


def test_an_escaping_slot_still_preserves_every_byte():
    assert owned.mem2reg_text(ESCAPING) == (ESCAPING, False)
