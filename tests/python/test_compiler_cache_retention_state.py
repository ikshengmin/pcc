"""Malformed persisted retention state is rebuilt from the live cache."""

import json
from pathlib import Path

import pytest

from pcc.frontends.python import pipeline_self_backend_cache as cache
from pcc.tools import compiler_cache_retention as retention


@pytest.fixture
def retained_cache(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    artifacts = []
    for index in range(2):
        digest = str(index) * 64
        artifact = root / digest[:2] / (digest + ".pco")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"owned-object-" + str(index).encode("ascii"))
        artifacts.append(artifact)
    state_path = root / ".pcc-cache-retention" / "state.json"
    state_path.parent.mkdir()
    monkeypatch.setenv(cache.OBJECT_CACHE_ENV, "1")
    monkeypatch.setenv(cache.OBJECT_CACHE_IDENTITY_ENV, "retention-state-test")
    monkeypatch.setenv(cache.OBJECT_CACHE_DIR_ENV, str(root))
    monkeypatch.delenv("PCC_CODE_PROFILE", raising=False)
    monkeypatch.setenv("PCC_COMPILER_CACHE_RETENTION", "1")
    monkeypatch.setenv("PCC_COMPILER_CACHE_AUTO_INTERVAL_SECONDS", "0")
    return root, state_path, artifacts


def _maintain_and_check_rebuilt_state(root, state_path, artifacts):
    original = [artifact.read_bytes() for artifact in artifacts]
    cache.maintain(
        [str(artifact) for artifact in artifacts],
        host_python_command=None, pcc_source_root="", retention_host_code="",
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["schema"] == retention.STATE_SCHEMA
    assert state["index_complete"] is True
    assert state["cursor"] == ""
    assert set(state["entries"]) == {
        artifact.relative_to(root).as_posix() for artifact in artifacts
    }
    assert all(isinstance(row, dict) for row in state["entries"].values())
    assert [artifact.read_bytes() for artifact in artifacts] == original
    assert not (state_path.parent / "prune.lock").exists()


@pytest.mark.parametrize("state", [[], None, "corrupt", 7, True])
def test_non_object_state_rebuilds_through_live_cache_maintenance(retained_cache, state):
    root, state_path, artifacts = retained_cache
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _maintain_and_check_rebuilt_state(root, state_path, artifacts)


@pytest.mark.parametrize("row", [[], None, "corrupt", 7, True])
def test_non_object_index_row_rebuilds_through_live_cache_maintenance(retained_cache, row):
    root, state_path, artifacts = retained_cache
    state = retention._empty_state()
    state["entries"]["orphaned-row"] = row
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _maintain_and_check_rebuilt_state(root, state_path, artifacts)


@pytest.mark.parametrize("field", ["scan_cycle", "last_auto_ns"])
@pytest.mark.parametrize("value", [[1], "not-a-number", float("inf")])
def test_invalid_scan_numbers_restart_from_empty_state(retained_cache, field, value):
    root, state_path, artifacts = retained_cache
    state = retention._empty_state()
    state[field] = value
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _maintain_and_check_rebuilt_state(root, state_path, artifacts)


@pytest.mark.parametrize("field", ["size_bytes", "last_used_ns", "seen_cycle"])
@pytest.mark.parametrize("value", [[1], "not-a-number", float("inf")])
def test_invalid_index_numbers_are_rejected_before_resuming_scan(retained_cache, field, value):
    root, state_path, _artifacts = retained_cache
    state = retention._empty_state()
    state["entries"]["orphaned-row"] = {
        "kind": "object", "size_bytes": 1, "last_used_ns": 1, "seen_cycle": 0,
    }
    state["entries"]["orphaned-row"][field] = value
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert retention._load_state(root) == retention._empty_state()


def test_valid_partial_index_and_compatible_integer_fields_are_preserved(retained_cache):
    root, state_path, _artifacts = retained_cache
    state = retention._empty_state()
    state.update(cursor="1\t00/" + "0" * 64 + ".pco", scan_cycle="4", last_auto_ns="5")
    state["entries"]["00/" + "0" * 64 + ".pco"] = {
        "kind": "object", "size_bytes": "6", "last_used_ns": "7", "seen_cycle": "4",
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert retention._load_state(root) == state
