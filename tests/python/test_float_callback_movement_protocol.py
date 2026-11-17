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
        "producer_phase": protocol.phase,
        "guards": protocol.guards,
        "active": protocol.active,
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
        self.phase = ["start"]
        self.guards = []

    def active(self):
        return 0

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
            # optimize=1 still evaluates the read-only gate_status assignment.
            # Checks successful acquire/release effects only.
            # The native witness requires its normal gate/movement assertions;
            # this is not qualification for running that witness with -O.
            gate_events = [("gates",)]
            assert protocol.phase == ["callback"]
            assert protocol.guards == [("callback", 0)]
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


def _record_function():
    source = Path(__file__).with_name("test_native_float_callback_movement.py")
    module = ast.parse(source.read_text())
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_record_float_execution")
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    return namespace[function.name]


@pytest.mark.parametrize("failed_index", [None, 0, 1, 2])
def test_float_receipt_preserves_every_execution_and_failure(failed_index):
    record = _record_function()
    receipt = {"status": "RUNNING", "executions": [], "source_sha256": "source",
               "runtime_sha256": "runtime", "binary_sha256": "binary"}
    modes = ((0, "synchronous"), (4, "positive"), (4, "deferred"))
    for index, (backend, mode) in enumerate(modes):
        current = dict(gc_backend=backend, execution_mode=mode,
                       status="NATIVE_EXECUTION_FAILED" if failed_index == index else "PASS",
                       stdout=f"evidence-{index}", stderr="", returncode=1 if failed_index == index else 0)
        record(receipt, current)
        current["status"] = "OVERWRITTEN_LOCAL"
        assert len(receipt["executions"]) == index + 1
        assert receipt["executions"][-1]["stdout"] == f"evidence-{index}"
        assert receipt["executions"][-1]["status"] != "OVERWRITTEN_LOCAL"
        expected = ("NATIVE_EXECUTION_FAILED" if failed_index is not None and failed_index <= index
                    else "PASS" if index == 2 else "RUNNING")
        assert receipt["status"] == expected
        assert {key:receipt[key] for key in ("source_sha256", "runtime_sha256", "binary_sha256")} == {
            "source_sha256":"source", "runtime_sha256":"runtime", "binary_sha256":"binary"}
    assert [(row["gc_backend"], row["execution_mode"]) for row in receipt["executions"]] == list(modes)


def test_float_receipt_rejects_missing_or_reordered_modes():
    record = _record_function()
    receipt = {"status": "RUNNING", "executions": []}
    with pytest.raises(AssertionError):
        record(receipt, dict(gc_backend=4, execution_mode="deferred", status="PASS"))
    assert receipt == {"status":"RUNNING", "executions":[]}


def test_float_ci_allowlisted_receipt_writes_aggregate_not_last_mode():
    source = Path(__file__).with_name("test_native_float_callback_movement.py")
    module = ast.parse(source.read_text())
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                    and node.name == "test_canonical_float_callback_movement_gc4")
    loop = next(node for node in function.body if isinstance(node, ast.For))
    statements = [ast.unparse(node) for node in loop.body]
    record_at = statements.index("_record_float_execution(receipt, current)")
    writes = [(index,text) for index,text in enumerate(statements) if text.startswith("receipt_path.write_text(")]
    assert len(writes) == 1
    assert writes[0][0] > record_at
    assert "json.dumps(receipt, indent=2)" in writes[0][1]
    assert "json.dumps(current" not in writes[0][1]
