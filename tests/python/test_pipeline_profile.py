from __future__ import annotations

from pcc.diagnostics.profile_events import ProfileRecorder
from pcc.frontends.python import pipeline
from pcc.frontends.python import pipeline_profile
from pcc.frontends.python.pipeline_profile import (
    ROADMAP_PHASES,
    run_profiled_phase,
    seed_expected_phase_counters,
)


def test_pipeline_profile_records_named_phase():
    rec = ProfileRecorder()
    result = run_profiled_phase(rec, "parse", lambda x: x + 1, 41)
    assert result.name == "parse"
    assert result.value == 42
    data = rec.to_json()
    assert data["events"][0]["name"] == "parse"


def test_pipeline_profile_exposes_all_roadmap_phase_names():
    rec = ProfileRecorder()
    seed_expected_phase_counters(rec)
    for name in ROADMAP_PHASES:
        assert "phase.expected." + name in rec.counters


def test_dictionary_profile_accumulates_events_totals_and_counters(monkeypatch):
    ticks = iter((1.0, 2.0, 3.0, 5.0))
    monkeypatch.setattr(pipeline_profile.time, "monotonic", lambda: next(ticks))
    profile = {}

    first = pipeline_profile.profile_begin(profile)
    pipeline_profile.profile_end(profile, "parse", first, detail="first")
    second = pipeline_profile.profile_begin(profile)
    pipeline_profile.profile_end(profile, "parse", second)
    pipeline_profile.profile_counter(profile, "modules", 2)

    assert profile["events"] == [
        {"name": "parse", "ms": 1000, "detail": "first"},
        {"name": "parse", "ms": 2000},
    ]
    assert profile["phase_totals_ms"] == {"parse": 3000}
    assert profile["counters"] == {"modules": 2}


def test_pipeline_facade_reexports_dictionary_profile_helpers():
    assert pipeline._profile_begin is pipeline_profile.profile_begin
    assert pipeline._profile_end is pipeline_profile.profile_end
    assert pipeline._profile_counter is pipeline_profile.profile_counter
    assert pipeline._profiled_gc_collect is pipeline_profile.profiled_gc_collect


def test_unrequested_dictionary_free_timing_is_silent_and_clock_free(monkeypatch, capsys):
    monkeypatch.delenv("PCC_PY_FRONTEND_WORKER_TIMING", raising=False)

    def unexpected():
        raise AssertionError("disabled phase timing read the clock")

    monkeypatch.setattr(pipeline_profile, "profile_now_ms", unexpected)
    assert pipeline_profile.profile_begin(None) == 0
    pipeline_profile.profile_end(None, "disabled", 0)
    assert capsys.readouterr() == ("", "")


def test_dictionary_free_timing_streams_pid_and_inclusive_monotonic_bounds(monkeypatch, capsys):
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", " true ")
    ticks = iter((1200, 1234))
    monkeypatch.setattr(pipeline_profile, "profile_now_ms", lambda: next(ticks))
    monkeypatch.setattr(pipeline_profile.os, "getpid", lambda: 17)
    started = pipeline_profile.profile_begin(None)
    pipeline_profile.profile_end(None, "collect_relative_module_closure", started)
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == (
        "pcc pipeline phase pid=17 name=collect_relative_module_closure "
        "start_ms=1200 end_ms=1234 elapsed_ms=34\n"
    )


def test_stream_timing_preserves_dictionary_profile_payload(monkeypatch, capsys):
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "1")
    ticks = iter((1200, 1234))
    monkeypatch.setattr(pipeline_profile, "profile_now_ms", lambda: next(ticks))
    profile = {}
    started = pipeline_profile.profile_begin(profile)
    pipeline_profile.profile_end(profile, "parse", started, detail="private detail")
    assert profile == {
        "events": [{"name": "parse", "ms": 34, "detail": "private detail"}],
        "phase_totals_ms": {"parse": 34},
    }
    output = capsys.readouterr()
    assert output.out == ""
    assert "elapsed_ms=34" in output.err
    assert "private detail" not in output.err


def test_optional_stream_failure_does_not_replace_result(monkeypatch):
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "1")
    ticks = iter((1200, 1234))
    monkeypatch.setattr(pipeline_profile, "profile_now_ms", lambda: next(ticks))

    class BrokenStream:
        def write(self, text):
            raise OSError("closed diagnostic stream")
        def flush(self):
            raise OSError("closed diagnostic stream")

    monkeypatch.setattr(pipeline_profile.sys, "stderr", BrokenStream())
    profile = {}
    pipeline_profile.profile_end(profile, "parse", pipeline_profile.profile_begin(profile))
    assert profile["phase_totals_ms"] == {"parse": 34}


def test_optional_clock_failure_does_not_change_unprofiled_compilation(monkeypatch, capsys):
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "1")

    def unavailable():
        raise OSError("clock unavailable")

    monkeypatch.setattr(pipeline_profile, "profile_now_ms", unavailable)
    assert pipeline_profile.profile_begin(None) == 0
    pipeline_profile.profile_end(None, "failed_clock", 0)
    pipeline_profile.profile_end(None, "failed_end_clock", 1234)
    assert capsys.readouterr() == ("", "")
