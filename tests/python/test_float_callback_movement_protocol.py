"""Pure-host contracts for the native float witness's stopped-world scope.

Read the integration program as data; never import its compiler fixtures or
execute its native entrypoint. These checks qualify only the test protocol.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


class ProtocolAbort(BaseException):
    pass


def _move_function(protocol, *, optimize=0):
    source = Path(__file__).with_name("test_native_float_callback_movement.py")
    module = ast.parse(source.read_text())
    program = next(
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "PROGRAM"
                for target in node.targets)
    )
    function = next(
        node for node in ast.parse(program).body
        if isinstance(node, ast.FunctionDef) and node.name == "move_live_objects"
    )
    namespace = {
        "metric": protocol.metric,
        "select_moving": protocol.select,
        "step": protocol.step,
        "stop_world": protocol.stop,
        "resume_world": protocol.resume,
        "stopped_at_gates": protocol.gates,
        "evidence": [],
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source),
                 "exec", optimize=optimize), namespace)
    return namespace["move_live_objects"], namespace["evidence"]


class _Protocol:
    def __init__(self, *, depth=0, stop_result=0, gate_result=1, fail_at="",
                 resume_result=0):
        self.depth = depth
        self.stop_result = stop_result
        self.gate_result = gate_result
        self.resume_result = resume_result
        self.fail_at = fail_at
        self.events = []
        self.metric_reads = 0

    def _event(self, name, *args):
        self.events.append((name, *args))
        if name == self.fail_at:
            raise ProtocolAbort(name)

    def stop(self):
        self._event("stop")
        if self.stop_result == 0:
            self.depth += 1
        return self.stop_result

    def resume(self):
        self._event("resume")
        assert self.depth > 0
        if self.resume_result == 0:
            self.depth -= 1
        return self.resume_result

    def gates(self):
        self._event("gates")
        assert self.depth > 0
        return self.gate_result

    def metric(self, number):
        if number == 42:
            self._event("pending")
            return 0
        assert number == 15
        name = "before" if self.metric_reads == 0 else "after"
        self._event(name)
        assert self.depth > 0
        self.metric_reads += 1
        return 10 + self.metric_reads

    def select(self, budget):
        self._event("select", budget)
        assert self.depth > 0
        return 79

    def step(self, budget):
        self._event("step", budget)
        assert self.depth > 0
        return 354


def test_float_movement_one_shot_order_and_nested_stop_balance():
    for optimize in (0, 1):
        for initial_depth in (0, 2):
            protocol = _Protocol(depth=initial_depth)
            move, evidence = _move_function(protocol, optimize=optimize)
            move("callback")
            assert protocol.depth == initial_depth
            # optimize=1 checks successful acquire/release effects only.
            # The native witness requires its normal gate/movement assertions;
            # this is not qualification for running that witness with -O.
            gate_events = [("gates",)] if optimize == 0 else []
            assert protocol.events == [
                ("pending",), ("stop",),
            ] + gate_events + [
                ("before",), ("select", 4096), ("step", 4096),
                ("after",), ("resume",),
            ]
            assert evidence == [("callback", 79, 354, 1, 0, 0, 0)]


def test_float_movement_failed_acquire_never_steps_or_resumes():
    for result in (-1, 1):
        protocol = _Protocol(stop_result=result)
        move, evidence = _move_function(protocol)
        with pytest.raises(AssertionError):
            move("callback")
        assert protocol.depth == 0
        assert protocol.events == [("pending",), ("stop",)]
        assert evidence == []
    protocol = _Protocol(fail_at="stop")
    move, evidence = _move_function(protocol)
    with pytest.raises(ProtocolAbort, match="stop"):
        move("callback")
    assert protocol.events == [("pending",), ("stop",)]
    assert protocol.depth == 0
    assert evidence == []


def test_float_movement_unsafe_gate_fails_without_retry_and_releases():
    for result in (0, -1):
        protocol = _Protocol(gate_result=result)
        move, evidence = _move_function(protocol)
        with pytest.raises(AssertionError):
            move("cleanup")
        assert protocol.depth == 0
        assert protocol.events == [("pending",), ("stop",), ("gates",), ("resume",)]
        assert evidence == []


def test_float_movement_failure_inside_scope_always_releases_once():
    sequence = [
        ("pending",), ("stop",), ("gates",), ("before",),
        ("select", 4096), ("step", 4096), ("after",),
    ]
    for fault in ("gates", "before", "select", "step", "after"):
        protocol = _Protocol(depth=2, fail_at=fault)
        move, evidence = _move_function(protocol)
        with pytest.raises(ProtocolAbort, match=fault):
            move("cleanup")
        assert protocol.depth == 2
        failed_index = next(index for index, event in enumerate(sequence)
                            if event[0] == fault)
        assert protocol.events == sequence[:failed_index + 1] + [("resume",)]
        assert evidence == []
    protocol = _Protocol(resume_result=-1)
    move, evidence = _move_function(protocol)
    with pytest.raises(AssertionError):
        move("cleanup")
    assert protocol.events == sequence + [("resume",)]
    assert evidence == []
