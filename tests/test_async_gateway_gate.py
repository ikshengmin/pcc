"""Mandatory gate evidence must not turn collection or partial runs into passes."""
import hashlib
from types import SimpleNamespace

import pytest

from scripts.qualification.async_gateway import (
    _account,
    _execution_environment,
    _verify_source,
)


def test_collection_and_first_failure_leave_later_original_nodes_unrun():
    events = [
        {"event": "collected", "nodeids": ["first", "second", "third"]},
        {"event": "report", "nodeid": "first", "when": "call", "outcome": "passed"},
        {"event": "report", "nodeid": "second", "when": "call", "outcome": "failed"},
    ]
    rows = _account(events, ["first", "second", "third"])
    assert {node: row["state"] for node, row in rows.items()} == {
        "first": "PASS", "second": "FAIL", "third": "UNRUN",
    }


def test_teardown_errors_and_platform_skips_are_not_successful_execution():
    rows = _account([
        {"event": "report", "nodeid": "native", "when": "call", "outcome": "passed"},
        {"event": "report", "nodeid": "native", "when": "teardown", "outcome": "failed"},
        {"event": "report", "nodeid": "platform", "when": "setup", "outcome": "skipped"},
    ], ["native", "platform"])
    assert rows["native"]["state"] == "SETUP_OR_TEARDOWN_ERROR"
    assert rows["platform"]["state"] == "SKIP"


def test_source_inventory_rejects_changed_bytes_before_running_commands(tmp_path):
    source = tmp_path / "input.py"
    source.write_bytes(b"original\n")
    records = {"input.py": {"sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                             "mode": 0o644, "kind": "file"}}
    _verify_source(tmp_path, records)
    source.write_bytes(b"changed\n")
    with pytest.raises(ValueError, match="source identity mismatch"):
        _verify_source(tmp_path, records)


def test_absent_compiler_selectors_do_not_leak_from_previous_snapshot(monkeypatch, tmp_path):
    for variable in ("PCC_TEST_PCC", "PCC_TEST_PCC1", "PCC_CURRENT_PCC1",
                     "PCC_TEST_COMPILER", "PCC_THREADED_RUNTIME_ARCHIVE"):
        monkeypatch.setenv(variable, str(tmp_path / "stale"))
    args = SimpleNamespace(core=tmp_path, gateway=tmp_path, runtime_archive=tmp_path / "runtime.a",
                           source_manifest="identity.json", threads="1", refcount="atomic",
                           pcc0=None, pcc1=None)
    environment = _execution_environment(args)
    assert "PCC_TEST_PCC" not in environment
    assert "PCC_TEST_PCC1" not in environment
    assert "PCC_CURRENT_PCC1" not in environment
    assert "PCC_TEST_COMPILER" not in environment
    assert "PCC_THREADED_RUNTIME_ARCHIVE" not in environment
    assert environment["PCC_RUNTIME_ARCHIVE"] == str(args.runtime_archive)


def test_explicit_native_compiler_reaches_both_existing_selector_contracts(tmp_path):
    compiler = tmp_path / "pcc1"
    compiler.write_bytes(b"verified by the caller")
    args = SimpleNamespace(core=tmp_path, gateway=tmp_path, runtime_archive=tmp_path / "runtime.a",
                           source_manifest="identity.json", threads="1", refcount="atomic",
                           pcc0=None, pcc1=compiler)
    environment = _execution_environment(args)
    assert environment["PCC_TEST_PCC1"] == str(compiler)
    assert environment["PCC_CURRENT_PCC1"] == str(compiler)


def test_gateway_file_continues_untouched_nodes_without_retrying_failure(monkeypatch):
    from scripts.qualification import async_gateway

    calls = []

    def run(args, name, repo, nodes, environment):
        calls.append(list(nodes))
        cases = {node: {"state": "UNRUN"} for node in nodes}
        if "failed" in nodes:
            cases["failed"] = {"state": "FAIL"}
        else:
            cases.update({node: {"state": "PASS"} for node in nodes})
        return {"cases": cases, "watchdog": {"status": "COMPLETE"}}

    monkeypatch.setattr(async_gateway, "_run", run)
    rows = list(async_gateway._execute_file_nodes(None, "file", None,
                                                  ["failed", "later_a", "later_b"], {}))
    assert calls == [["failed", "later_a", "later_b"], ["later_a", "later_b"]]
    assert rows[0]["cases"]["failed"]["state"] == "FAIL"
    assert rows[1]["cases"]["later_b"]["state"] == "PASS"


def test_gateway_resource_stop_does_not_retry_unreported_nodes(monkeypatch):
    from scripts.qualification import async_gateway

    calls = []

    def run(args, name, repo, nodes, environment):
        calls.append(list(nodes))
        return {"cases": {"first": {"state": "PASS"}, "later": {"state": "UNRUN"}},
                "watchdog": {"status": "MEMORY_LIMIT"}}

    monkeypatch.setattr(async_gateway, "_run", run)
    rows = list(async_gateway._execute_file_nodes(None, "file", None,
                                                  ["first", "later"], {}))
    assert len(rows) == 1
    assert calls == [["first", "later"]]
    assert rows[0]["cases"]["later"]["state"] == "UNRUN"
