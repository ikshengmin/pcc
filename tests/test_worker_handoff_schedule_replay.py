"""The research model preserves bounds; it does not assert a CI speedup."""

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "worker_handoff_schedule_replay",
    ROOT / "scripts/research/replay_worker_handoff_schedule.py",
)
model = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(model)


def fixture():
    return json.loads((ROOT / "scripts/research/fixtures/worker_handoff_head_of_line.json").read_text())


@pytest.mark.parametrize("policy", ["largest", "drain"])
def test_complete_graph_keeps_dependencies_slots_and_reservations(policy):
    source = fixture()
    result = model.replay(source, policy)
    rows = {row["task"]: row for row in result["trace"]}
    assert result["completed_tasks"] == len(source["tasks"])
    assert result["max_reserved_bytes"] <= source["available_bytes"]
    assert result["max_outstanding_slots"] <= source["slot_limit"]
    for task in source["tasks"]:
        dependency = task["depends_on"]
        if dependency >= 0:
            assert rows[task["id"]]["start_s"] >= rows[dependency]["end_s"]
    edges = sorted({value for row in rows.values() for value in (row["start_s"], row["end_s"])})
    tasks = {row["id"]: row for row in source["tasks"]}
    for time in edges:
        active = [identity for identity, row in rows.items() if row["start_s"] <= time < row["end_s"]]
        assert len(active) <= source["width"]
        assert sum(tasks[identity]["reservation_bytes"] for identity in active) <= source["available_bytes"]


def test_slot_retires_only_after_consumer_not_producer():
    source = fixture()
    source["slot_limit"] = 1
    result = model.replay(source, "drain")
    rows = {row["task"]: row for row in result["trace"]}
    assert rows[2]["start_s"] == rows[1]["end_s"]
    assert result["max_outstanding_slots"] == 1


def test_no_universal_speedup_and_large_producers_still_complete():
    source = fixture()
    assert model.compare(source)["model_delta_s"] == 0
    # A different bounded graph makes consumer preference WORSE. This guards
    # against turning the illustrative policy into an unconditional claim.
    producer_rows = [(4, 500), (3, 400), (3, 600), (7, 600), (5, 300)]
    consumer_rows = [(43, 700), (34, 200), (27, 700), (48, 300), (49, 300)]
    for index, (producer, consumer) in enumerate(zip(producer_rows, consumer_rows)):
        for offset, (duration, reservation) in enumerate((producer, consumer)):
            row = source["tasks"][2 * index + offset]
            row["duration_s"], row["reservation_bytes"] = duration, reservation
    result = model.compare(source)
    assert result["model_delta_s"] == -24
    assert [row["model_wall_s"] for row in result["policies"]] == [114, 138]
    assert all(row["completed_tasks"] == 10 for row in result["policies"])


@pytest.mark.parametrize("field,value", [
    ("duration_s", 0), ("duration_s", float("nan")),
    ("duration_s", float("inf")), ("reservation_bytes", True),
    ("reservation_bytes", 1001), ("depends_on", 1), ("slot", 20),
])
def test_invalid_or_over_budget_graph_is_rejected(field, value):
    source = fixture()
    source["tasks"][0][field] = value
    with pytest.raises(ValueError):
        model.replay(source, "largest")


def line(event, task, pid, phase, timestamp, *, peak=8, reservation=400000000):
    return (f"pcc frontend admission event={event} task={task} pid={pid} "
            f"phase='{phase}' indices=[7] mapping_offset=0 "
            f"reservation_bytes={reservation} available_bytes=1000000000 "
            f"peak_bytes={peak} monotonic_s={timestamp}")


def log_with_retry():
    return "\n".join([
        line("start", 0, 10, "indexed-frontend", 1),
        line("cancel", 0, 10, "indexed-frontend", 2),
        line("start", 0, 11, "indexed-frontend", 3),
        line("retire", 0, 11, "indexed-frontend", 5, peak=300000000),
        line("start", 1, 12, "indexed-backend", 5),
        line("retire", 1, 12, "indexed-backend", 9),
    ])


def test_retry_is_disclosed_not_silently_modeled_as_new_completed_work():
    source, evidence = model.model_from_log(
        log_with_retry(), width=3, slot_limit=3, reservation="admission",
    )
    assert len(source["tasks"]) == 2
    assert [row["duration_s"] for row in source["tasks"]] == [2, 4]
    assert evidence["cancelled_attempts_omitted"] == 1
    assert evidence["cancelled_worker_seconds_omitted"] == 1
    assert evidence["observed_successful_attempt_span_s"] == 6


def test_completed_peak_sensitivity_never_reduces_a_reservation():
    before, _ = model.model_from_log(log_with_retry(), width=3, slot_limit=3, reservation="admission")
    after, _ = model.model_from_log(log_with_retry(), width=3, slot_limit=3, reservation="completed-peak")
    assert after["tasks"][0]["reservation_bytes"] == 509217728
    for old, new in zip(before["tasks"], after["tasks"]):
        assert new["reservation_bytes"] >= old["reservation_bytes"]


@pytest.mark.parametrize("text", ["", log_with_retry().splitlines()[0],
    log_with_retry().replace("task=1", "task=3"),
    log_with_retry() + "\n" + line("retire", 1, 12, "indexed-backend", 10),
])
def test_incomplete_unpaired_and_duplicate_terminal_logs_fail_closed(text):
    with pytest.raises(ValueError):
        model.model_from_log(text, width=3, slot_limit=3, reservation="admission")


def test_cli_fixture_is_standalone_text_not_a_production_benchmark(capsys):
    model.main(["--fixture", str(ROOT / "scripts/research/fixtures/worker_handoff_head_of_line.json")])
    result = json.loads(capsys.readouterr().out)
    assert "not observed" in result["warning"]
    assert result["fixture"]["policies"][0]["completed_tasks"] == 10
