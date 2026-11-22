"""An isolated heap-goal policy witness; does not run or alter stock GC2."""
from dataclasses import dataclass

import pytest


MAX_BYTES = (1 << 63) - 1
MIN_GROWTH = 4 * 1024 * 1024
MAX_ASSIST = 512
MARK_BYTES_PER_UNIT = 64


@dataclass(frozen=True)
class Policy:
    trigger: int
    goal: int


def heap_goal(last_live, percent):
    assert 0 <= last_live <= MAX_BYTES and 0 <= percent <= 10_000
    requested = last_live * percent // 100
    growth = max(MIN_GROWTH, requested)
    goal = min(MAX_BYTES, last_live + growth)
    headroom = max(1, (goal - last_live) // 4)
    trigger = max(last_live, goal - headroom)
    return Policy(trigger, goal)


def assist_budget(policy, *, heap_bytes, allocated_batch, mark_active):
    if not mark_active or allocated_batch <= 0 or heap_bytes < policy.trigger:
        return 0
    allocation_units = (allocated_batch + MARK_BYTES_PER_UNIT - 1) // MARK_BYTES_PER_UNIT
    if heap_bytes >= policy.goal:
        return min(MAX_ASSIST, 2 * allocation_units)
    width = policy.goal - policy.trigger
    pressure = heap_bytes - policy.trigger
    desired = allocation_units + allocation_units * pressure // width
    return min(MAX_ASSIST, desired)


@pytest.mark.parametrize(
    "live,percent,goal",
    [
        (0, 100, MIN_GROWTH),
        (MIN_GROWTH, 0, 2 * MIN_GROWTH),
        (16 * MIN_GROWTH, 50, 24 * MIN_GROWTH),
        (16 * MIN_GROWTH, 100, 32 * MIN_GROWTH),
        (MAX_BYTES - 99, 10000, MAX_BYTES),
        (MAX_BYTES, 100, MAX_BYTES),
    ],
)
def test_trigger_headroom_and_saturating_absolute_goal(live, percent, goal):
    policy = heap_goal(live, percent)
    assert policy.goal == goal
    assert live <= policy.trigger <= policy.goal
    if live < goal:
        assert policy.trigger < policy.goal


def test_active_assist_scales_with_batch_and_pressure_but_is_capped():
    policy = heap_goal(16 * MIN_GROWTH, 100)
    near = policy.goal - 1
    assert assist_budget(policy, heap_bytes=near, allocated_batch=4096, mark_active=False) == 0
    assert assist_budget(policy, heap_bytes=policy.trigger - 1, allocated_batch=4096,
                         mark_active=True) == 0
    assert assist_budget(policy, heap_bytes=near, allocated_batch=0, mark_active=True) == 0
    assert assist_budget(policy, heap_bytes=policy.trigger, allocated_batch=4096,
                         mark_active=True) == 64
    assert assist_budget(policy, heap_bytes=near, allocated_batch=4096, mark_active=True) > 64
    assert assist_budget(policy, heap_bytes=near, allocated_batch=8192, mark_active=True) > 128
    assert assist_budget(policy, heap_bytes=policy.goal, allocated_batch=1,
                         mark_active=True) == 2
    assert assist_budget(policy, heap_bytes=MAX_BYTES, allocated_batch=MAX_BYTES,
                         mark_active=True) == MAX_ASSIST
