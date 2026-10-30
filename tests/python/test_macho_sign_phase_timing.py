"""Host-only control-flow contracts for opt-in Mach-O signing timing.

Execute selected source functions with stubs, never import the native linker
or construct a real Mach-O object, signature, runtime or executable.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


class DiagnosticAbort(BaseException):
    pass


class LinkFailure(Exception):
    pass


class MaterializationFailure(Exception):
    pass


class NoClock:
    def __getattr__(self, name):
        raise AssertionError("unexpected clock access: " + name)


def _source_function(name, namespace):
    path = Path(__file__).resolve().parents[2] / "pcc/backend/macho_exec.py"
    source = ast.parse(path.read_text())
    function = next(node for node in source.body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__",
                             names=[ast.alias(name="annotations")], level=0),
              function],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace["time"] = NoClock()
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


class Timer:
    def __init__(self, events):
        self.events = events
        self.tick = 100

    def begin(self, phase):
        self.tick += 1
        self.events.append(("begin", phase, self.tick))
        return self.tick

    def complete(self, phase, started):
        self.events.append(("complete", phase, started))


@pytest.mark.parametrize("budget_enabled", (False, True))
@pytest.mark.parametrize("timed", (False, True))
def test_prepared_link_only_propagates_opted_in_timing(budget_enabled, timed):
    merged, plan, callback, budget = object(), object(), object(), object()
    timer = object() if timed else None
    events = []

    def prepare(value, **kwargs):
        assert value is merged
        events.append(("prepare", kwargs))
        return plan

    def retained(value):
        assert value is merged
        events.append(("retained",))
        return 7

    def finish(*args, **kwargs):
        assert args == (plan, b"test-id", callback)
        events.append(("finish", kwargs))
        return b"unchanged-output"

    link = _source_function("link_prepared_executable", {
        "_prepare_executable_image": prepare,
        "_compact_source_raw_bytes": retained,
        "_finish_executable_image": finish,
    })
    kwargs = {"entry": "_entry", "minos": (13, 2), "identifier": b"test-id",
              "phase_callback": callback}
    if budget_enabled:
        kwargs["compact_budget"] = budget
    if timed:
        kwargs["coordinator_timing"] = timer
    assert link(merged, **kwargs) == b"unchanged-output"
    expected_prepare = {"entry": "_entry", "minos": (13, 2),
                        "identifier": b"test-id"}
    expected_finish = {}
    if budget_enabled:
        expected_prepare["compact_budget"] = budget
        expected_finish.update(compact_budget=budget, retained_source_bytes=7)
    if timed:
        expected_finish["coordinator_timing"] = timer
    expected = [("prepare", expected_prepare)]
    if budget_enabled:
        expected.append(("retained",))
    expected.append(("finish", expected_finish))
    assert events == expected


class ImageFixture:
    def __init__(self, *, timed, failure="", error=None, timer=None):
        self.events = timer.events if timer is not None else []
        self.timer = (timer or Timer(self.events)) if timed else None
        self.failure = failure
        self.error = error or DiagnosticAbort(failure)
        self.regions = [SimpleNamespace(data=b"input123")]
        self.image = bytearray(b"input123" + b"\0" * 4)
        self.signer_inputs = []
        self.finish = _source_function("_finish_executable_image", {
            "materialize_output_buffer": self.materialize,
            "build_signature": self.sign,
            "_check_compact_copy_peak": self.peak,
            "ParallelLinkError": MaterializationFailure,
            "LinkError": LinkFailure,
        })

    def materialize(self, size, regions, *, reserve):
        self.events.append(("materialize", size, reserve))
        assert regions is self.regions and regions[0].data == b"input123"
        if self.failure == "materialize":
            raise MaterializationFailure("failed output")
        return self.image

    def peak(self, budget, size, label):
        raise AssertionError("unexpected compact-budget accounting")

    def callback(self, event):
        self.events.append(("callback", event))
        if self.failure == event:
            raise self.error

    def sign(self, region, **kwargs):
        self.events.append(("sign",))
        assert isinstance(region, memoryview)
        assert region.obj is self.image
        assert self.regions == []
        self.signer_inputs.append((bytes(region), kwargs))
        if self.failure == "sign":
            raise self.error
        return b"bad" if self.failure == "size" else b"SIGN"

    def run(self):
        kwargs = {}
        if self.timer is not None:
            kwargs["coordinator_timing"] = self.timer
        return self.finish((8, self.regions, 4, 8), b"test-id",
                           self.callback, **kwargs)


def _sign_events(timed, *, complete=True, started=101):
    events = [("callback", "sign_begin")]
    if timed:
        events.append(("begin", "macho_sign", started))
    events.append(("sign",))
    if timed and complete:
        events.append(("complete", "macho_sign", started))
    events.append(("callback", "sign_end"))
    return events


@pytest.mark.parametrize("timed", (False, True))
def test_sign_timing_preserves_input_snapshot_materialization_and_output(timed):
    fixture = ImageFixture(timed=timed)
    result = fixture.run()
    assert type(result) is bytes and result == b"input123SIGN"
    assert fixture.image == bytearray(result)
    assert fixture.regions == []
    assert fixture.signer_inputs == [(b"input123", {
        "identifier": b"test-id", "exec_seg_base": 0,
        "exec_seg_limit": 8, "exec_seg_flags": 1,
    })]
    assert fixture.events == [("materialize", 8, 4)] + _sign_events(timed)


@pytest.mark.parametrize("timed", (False, True))
@pytest.mark.parametrize("error_type", (RuntimeError, DiagnosticAbort))
def test_failed_sign_keeps_original_error_and_sign_end_without_completion(timed, error_type):
    error = error_type("sign failure")
    fixture = ImageFixture(timed=timed, failure="sign", error=error)
    with pytest.raises(error_type) as caught:
        fixture.run()
    assert caught.value is error
    assert fixture.image == bytearray(b"input123" + b"\0" * 4)
    assert fixture.events == [("materialize", 8, 4)] + _sign_events(timed, complete=False)


@pytest.mark.parametrize("timed", (False, True))
@pytest.mark.parametrize("failure", ("materialize", "sign_begin", "sign_end", "size"))
def test_other_failures_keep_prior_materialization_and_callback_boundaries(timed, failure):
    fixture = ImageFixture(timed=timed, failure=failure)
    expected_error = LinkFailure if failure in ("materialize", "size") else DiagnosticAbort
    with pytest.raises(expected_error) as caught:
        fixture.run()
    expected = [("materialize", 8, 4)]
    if failure == "materialize":
        assert str(caught.value) == "parallel Mach-O output failed: failed output"
        assert isinstance(caught.value.__cause__, MaterializationFailure)
        assert len(fixture.regions) == 1
    elif failure == "sign_begin":
        expected.append(("callback", "sign_begin"))
        assert caught.value is fixture.error
    else:
        expected += _sign_events(timed)
        if failure == "size":
            assert str(caught.value) == "signature size mismatch: predicted 4, built 3"
        else:
            assert caught.value is fixture.error
    assert fixture.events == expected
    assert fixture.image == bytearray(b"input123" + b"\0" * 4)


def test_repeated_sign_calls_complete_only_their_own_begin_token():
    events = []
    timer = Timer(events)
    first = ImageFixture(timed=True, timer=timer)
    second = ImageFixture(timed=True, timer=timer)
    assert first.run() == second.run() == b"input123SIGN"
    assert events == ([("materialize", 8, 4)] + _sign_events(True, started=101)
                      + [("materialize", 8, 4)] + _sign_events(True, started=102))
