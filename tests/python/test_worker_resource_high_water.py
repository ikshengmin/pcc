"""Leaf-only host controls for attempt-local published RSS high water.

The publisher controls load one source leaf and inject RSS/platform operations.
Receipt controls reuse the existing synthetic evidence helpers. Neither group
compiles native code; the separate native file exercises the owned runtime.
Existing Windows publication tests own the real OS sharing/rename contract.
"""

import builtins
import ast
import errno
import importlib.util
import io
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "pcc/frontends/python/worker_resource_plan.py"
WORKERS_MODULE = "pcc.frontends.python.pipeline_frontend_workers"


@pytest.fixture
def publisher(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("_rss_high_water_control", POLICY_PATH)
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    state = SimpleNamespace(current=100, raw=120, pid=123, token="attempt", path=tmp_path / "a.rss")
    environment = {policy.RESOURCE_REPORT_ENV: str(state.path),
                   policy.RESOURCE_TOKEN_ENV: state.token}
    monkeypatch.setattr(policy, "os", SimpleNamespace(
        environ=environment, getpid=lambda: state.pid, unlink=os.unlink,
    ))
    monkeypatch.setattr(policy, "_open_worker_resource", lambda path: open(path, encoding="utf-8"))
    monkeypatch.setattr(policy, "_replace_worker_resource", os.replace)
    original_import = builtins.__import__
    workers = SimpleNamespace(_coordinator_rss_bytes=lambda: state.current,
                              _worker_peak_rss_bytes=lambda: state.raw)

    def importing(name, *args, **kwargs):
        if name == WORKERS_MODULE:
            return workers
        assert not name.startswith("pcc."), "unexpected PCC package dependency"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    return policy, state


def _rows(policy, *, pid=123, phase="started", current=100, peak=120, token="attempt"):
    return [policy.RESOURCE_REPORT_SCHEMA, str(pid), phase, str(current), str(peak), token]


def _write(path, rows):
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _report(path):
    return path.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("current,raw,expected", [(80, 90, 120), (130, 140, 140), (150, 90, 150)])
def test_published_high_water_survives_falling_samples(publisher, current, raw, expected):
    policy, state = publisher
    policy.publish_worker_resource("started")
    state.current, state.raw = current, raw
    policy.publish_worker_resource("complete")
    assert _report(state.path) == _rows(policy, phase="complete", current=current, peak=expected)
    assert not Path(str(state.path) + ".tmp").exists()


def test_interleaved_paths_preserve_each_committed_peak(publisher, tmp_path):
    policy, state = publisher
    first = state.path
    second = tmp_path / "b.rss"
    policy.publish_worker_resource("started")
    policy.os.environ[policy.RESOURCE_REPORT_ENV] = str(second)
    state.current, state.raw = 300, 320
    policy.publish_worker_resource("started")
    policy.os.environ[policy.RESOURCE_REPORT_ENV] = str(first)
    state.current, state.raw = 80, 90
    policy.publish_worker_resource("complete")
    assert _report(first)[3:5] == ["80", "120"]
    assert _report(second)[3:5] == ["300", "320"]


@pytest.mark.parametrize("change", ["pid", "token", "both"])
def test_fork_or_retry_identity_does_not_inherit_foreign_peak(publisher, change):
    policy, state = publisher
    policy.publish_worker_resource("started")
    if change in ("pid", "both"):
        state.pid = 456
    if change in ("token", "both"):
        policy.os.environ[policy.RESOURCE_TOKEN_ENV] = "retry"
    state.current, state.raw = 80, 90
    policy.publish_worker_resource("complete")
    token = policy.os.environ[policy.RESOURCE_TOKEN_ENV]
    assert _report(state.path) == _rows(policy, pid=state.pid, token=token, phase="complete", current=80, peak=90)


def test_valid_long_phase_and_token_are_not_diagnostic_size_limited(publisher):
    policy, state = publisher
    token, phase = "t" * 4097, "p" * 4097
    policy.os.environ[policy.RESOURCE_TOKEN_ENV] = token
    _write(state.path, _rows(policy, phase=phase, token=token, peak=300))
    policy.publish_worker_resource(phase)
    assert _report(state.path) == _rows(policy, phase=phase, token=token, peak=300)


@pytest.mark.parametrize("mutation", ["schema", "short", "extra", "pid-text", "pid-zero", "pid-negative",
                                     "current-text", "current-zero", "current-negative", "peak-text", "low-peak"])
@pytest.mark.parametrize("foreign", [False, True])
def test_invalid_previous_report_fails_before_replacement(publisher, mutation, foreign):
    policy, state = publisher
    rows = _rows(policy, token="foreign" if foreign else "attempt")
    changes = {"schema": (0, "unknown"), "pid-text": (1, "bad"), "pid-zero": (1, "0"),
               "pid-negative": (1, "-1"), "current-text": (3, "bad"), "current-zero": (3, "0"),
               "current-negative": (3, "-1"), "peak-text": (4, "bad"), "low-peak": (4, "99")}
    if mutation == "short":
        rows.pop()
    elif mutation == "extra":
        rows.append("unexpected")
    else:
        index, value = changes[mutation]
        rows[index] = value
    _write(state.path, rows)
    before = state.path.read_bytes()
    with pytest.raises(policy.WorkerMemoryError):
        policy.publish_worker_resource("complete")
    assert state.path.read_bytes() == before
    assert not Path(str(state.path) + ".tmp").exists()


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO, errno.EMFILE])
@pytest.mark.parametrize("when", ["open", "read"])
def test_prior_io_error_is_not_absence(publisher, monkeypatch, code, when):
    policy, state = publisher
    policy.publish_worker_resource("started")
    before = state.path.read_bytes()
    failure = OSError(code, "prior report unavailable")
    streams = []

    class BrokenRead(io.StringIO):
        def read(self, *args):
            raise failure

    def opening(_path):
        if when == "open":
            raise failure
        stream = BrokenRead()
        streams.append(stream)
        return stream

    monkeypatch.setattr(policy, "_open_worker_resource", opening)
    with pytest.raises(OSError) as caught:
        policy.publish_worker_resource("complete")
    assert caught.value is failure
    assert all(stream.closed for stream in streams)
    assert state.path.read_bytes() == before
    assert not Path(str(state.path) + ".tmp").exists()


def test_missing_previous_report_starts_new_publication(publisher):
    policy, state = publisher
    assert policy._previous_worker_resource_peak(str(state.path), state.pid, state.token) == 0
    policy.publish_worker_resource("started")
    assert _report(state.path) == _rows(policy)


def test_read_time_enoent_is_not_an_absent_previous_publication(publisher, monkeypatch):
    policy, state = publisher
    policy.publish_worker_resource("started")
    before = state.path.read_bytes()
    failure = FileNotFoundError(errno.ENOENT, "read failed after open")

    class BrokenRead(io.StringIO):
        def read(self, *args):
            raise failure

    stream = BrokenRead()
    monkeypatch.setattr(policy, "_open_worker_resource", lambda _path: stream)
    with pytest.raises(FileNotFoundError) as caught:
        policy.publish_worker_resource("complete")
    assert caught.value is failure and stream.closed
    assert state.path.read_bytes() == before
    assert not Path(str(state.path) + ".tmp").exists()


@pytest.mark.parametrize("ending", ["", "\n", "\r\n"])
def test_prior_publication_requires_complete_last_line(publisher, ending):
    policy, state = publisher
    text = "\n".join(_rows(policy, peak=300)) + ending
    state.path.write_bytes(text.encode())
    if not ending:
        with pytest.raises(policy.WorkerMemoryError):
            policy.publish_worker_resource("complete")
        assert state.path.read_bytes() == text.encode()
    else:
        policy.publish_worker_resource("complete")
        assert _report(state.path)[4] == "300"


@pytest.mark.parametrize("current,raw", [(0, 200), (-1, 200), (0, 0), (-1, -1)])
def test_previous_peak_cannot_mask_unavailable_current(publisher, monkeypatch, current, raw):
    policy, state = publisher
    policy.publish_worker_resource("started")
    before = state.path.read_bytes()
    state.current, state.raw = current, raw
    monkeypatch.setattr(policy, "_previous_worker_resource_peak",
                        lambda *_args: pytest.fail("sampling validation must precede prior read"))
    with pytest.raises(policy.WorkerMemoryError):
        policy.publish_worker_resource("complete")
    assert state.path.read_bytes() == before


def test_original_current_fallback_for_unavailable_raw_peak_remains(publisher):
    policy, state = publisher
    state.raw = -1
    policy.publish_worker_resource("started")
    assert _report(state.path)[3:5] == ["100", "100"]


@pytest.mark.parametrize("committed", [False, True])
def test_replace_failure_preserves_committed_high_water_on_next_call(publisher, monkeypatch, committed):
    policy, state = publisher
    policy.publish_worker_resource("started")
    state.current, state.raw = 200, 220
    failure = OSError(errno.EIO, "replacement completion uncertain")

    def replacing(source, destination):
        if committed:
            os.replace(source, destination)
        raise failure

    monkeypatch.setattr(policy, "_replace_worker_resource", replacing)
    with pytest.raises(OSError) as caught:
        policy.publish_worker_resource("intermediate")
    assert caught.value is failure
    assert _report(state.path)[4] == ("220" if committed else "120")
    assert not Path(str(state.path) + ".tmp").exists()
    monkeypatch.setattr(policy, "_replace_worker_resource", os.replace)
    state.current, state.raw = 80, 90
    policy.publish_worker_resource("complete")
    assert _report(state.path)[3:5] == ["80", "220" if committed else "120"]


def test_single_writer_preserves_reader_snapshot_across_atomic_replace(publisher, monkeypatch):
    """An inert old-reader snapshot avoids claiming multiwriter/WinAPI coverage."""
    policy, state = publisher
    policy.publish_worker_resource("started")
    before = state.path.read_text(encoding="utf-8")
    reader = io.StringIO(before)
    entered, resume = threading.Event(), threading.Event()
    errors = []

    def replacing(source, destination):
        entered.set()
        if not resume.wait(5):
            raise AssertionError("reader did not release publisher")
        os.replace(source, destination)

    monkeypatch.setattr(policy, "_replace_worker_resource", replacing)
    state.current, state.raw = 80, 90

    def publishing():
        try:
            policy.publish_worker_resource("complete")
        except BaseException as error:
            errors.append(error)

    writer = threading.Thread(target=publishing)
    writer.start()
    try:
        assert entered.wait(5)
        assert state.path.read_text(encoding="utf-8") == before
        assert reader.read() == before
    finally:
        resume.set()
        writer.join(5)
        reader.close()
    assert not writer.is_alive()
    assert errors == []
    assert _report(state.path) == _rows(policy, phase="complete", current=80, peak=120)


def _receipt_controls():
    # These existing helpers construct synthetic evidence, not a native run.
    from tests.python.test_worker_resource_plan import (
        _complete_diagnostic_chain, _rewrite_diagnostic_journal,
    )
    from tests.python.test_worker_size_prior_processes import _fixture_resource_diagnostics
    return _complete_diagnostic_chain, _rewrite_diagnostic_journal, _fixture_resource_diagnostics


@pytest.mark.parametrize("retained", [False, True])
def test_extended_receipt_binds_prior_peak_to_same_child(tmp_path, retained):
    build, rewrite, parse = _receipt_controls()
    directory = tmp_path / "width"
    _driver, children = build(directory)
    for child in children:
        path = Path(child["path"])
        rows = [ast.literal_eval(line) for line in path.read_text().splitlines()]
        rows[0] = ("published", rows[0][1] + (0,))
        final = rows[1][1]
        if retained:
            # Previous 120 is lower than final 125 in this existing chain;
            # retaining it remains a distinct, explicitly checked input.
            rows[1] = ("published", final + (120,))
        else:
            # Historical four-field reports remain readable without claiming
            # they implement the newly strengthened publication contract.
            rows[1] = ("published", final)
        rewrite(path, rows)
    result = parse(directory)
    assert result["complete"] and len(result["verified_observations"]) == 3
    assert all(row["state"] == "matched" for row in result["accepted"])


@pytest.mark.parametrize("corruption", ["foreign-prior", "negative-prior", "bool-prior", "extra", "missing-prior", "inflated-peak"])
def test_extended_receipt_rejects_unproven_retained_peak(tmp_path, corruption):
    build, rewrite, parse = _receipt_controls()
    directory = tmp_path / "width"
    _driver, children = build(directory)
    path = Path(children[0]["path"])
    rows = [ast.literal_eval(line) for line in path.read_text().splitlines()]
    rows[0] = ("published", rows[0][1] + (0,))
    payload = list(rows[1][1] + (120,))
    if corruption == "foreign-prior":
        payload[4] = 121
    elif corruption == "negative-prior":
        payload[4] = -1
    elif corruption == "bool-prior":
        payload[4] = True
    elif corruption == "extra":
        payload.append(120)
    elif corruption == "missing-prior":
        payload[4] = 0
    else:
        payload[0] = payload[0].replace("\n120\n125\n", "\n120\n130\n")
        payload[3] = 130
    rows[1] = ("published", tuple(payload))
    rewrite(path, rows)
    result = parse(directory)
    assert not result["complete"]
    assert result["journals"][path.name]["reason"] == "missing-or-invalid-journal"


def test_new_diagnostic_payload_records_real_previous_publication(publisher, tmp_path):
    policy, state = publisher
    diagnostic = policy.worker_resource_diagnostics(str(tmp_path / "diagnostic"))
    policy.publish_worker_resource("started", diagnostic)
    state.current, state.raw = 80, 90
    policy.publish_worker_resource("complete", diagnostic)
    rows = [ast.literal_eval(line) for line in Path(diagnostic["path"]).read_text().splitlines()]
    assert [row[1][1:] for row in rows] == [(100, 120, 120, 0), (80, 90, 120, 120)]
    assert [row[1][0].splitlines()[4] for row in rows] == ["120", "120"]
    assert diagnostic["incomplete"] == "" and diagnostic["records"] == 2


def test_receipt_accepts_lower_raw_sample_only_with_bound_prior_peak(tmp_path):
    build, rewrite, parse = _receipt_controls()
    directory = tmp_path / "width"
    driver, children = build(directory)
    child_path = Path(children[0]["path"])
    rows = [ast.literal_eval(line) for line in child_path.read_text().splitlines()]
    first_text = rows[0][1][0].replace("\n120\n120\n", "\n120\n125\n")
    final_text = rows[1][1][0].replace("\n120\n125\n", "\n100\n125\n")
    rows[0] = ("published", (first_text, 120, 125, 125, 0))
    rows[1] = ("published", (final_text, 100, 110, 125, 125))
    rewrite(child_path, rows)
    driver_path = Path(driver["path"])
    records = [ast.literal_eval(line) for line in driver_path.read_text().splitlines()]
    first = list(records[0][1])
    first[4], first[5] = 125, first_text
    records[0] = ("accepted", tuple(first))
    final = list(records[1][1])
    final[3], final[5] = 100, final_text
    records[1] = ("accepted", tuple(final))
    rewrite(driver_path, records)
    result = parse(directory)
    assert result["complete"]
    assert result["accepted"][1]["raw_candidates"] == [(100, 110, 125, 125)]
    assert result["verified_observations"][0]["accepted_maximum"] == 125
