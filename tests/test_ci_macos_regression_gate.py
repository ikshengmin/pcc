from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ci_macos_regression_gate", ROOT / "scripts/ci_macos_regression_gate.py",
)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def _reports(nodes):
    rows = [
        {"event": "start", "collect_only": False},
        {"event": "collected", "nodeids": nodes},
    ]
    rows += [{"event": "report", "nodeid": node, "when": when, "outcome": "passed"}
             for node in nodes for when in ("setup", "call", "teardown")]
    rows.append({"event": "finish", "exitstatus": 0,
                 "testscollected": len(nodes), "testsfailed": 0})
    return rows


def _write_reports(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_exact_passed_nodes_are_execution_evidence(tmp_path):
    nodes = ["sample.py::test_one", "sample.py::test_two[pcc0]"]
    path = tmp_path / "live.jsonl"
    _write_reports(path, _reports(nodes))
    assert gate.verify_execution_report(path, nodes) == nodes


@pytest.mark.parametrize("mutation", [
    "collect-only", "deselected", "missing-node", "skipped", "xfail",
    "no-call", "no-teardown", "no-finish", "failed-exit", "duplicate-call",
])
def test_incomplete_or_nonexecuted_gate_cannot_pass(tmp_path, mutation):
    nodes = ["sample.py::test_one"]
    rows = _reports(nodes)
    if mutation == "collect-only":
        rows[0]["collect_only"] = True
    elif mutation == "deselected":
        rows.insert(1, {"event": "deselected", "nodeids": nodes})
    elif mutation == "missing-node":
        rows[1]["nodeids"] = []
    elif mutation == "skipped":
        rows[3]["outcome"] = "skipped"
    elif mutation == "xfail":
        rows[3]["wasxfail"] = "expected failure"
    elif mutation == "no-call":
        del rows[3]
    elif mutation == "no-teardown":
        del rows[4]
    elif mutation == "no-finish":
        rows.pop()
    elif mutation == "failed-exit":
        rows[-1]["exitstatus"] = 1
    else:
        rows.insert(4, dict(rows[3]))
    path = tmp_path / "live.jsonl"
    _write_reports(path, rows)
    with pytest.raises(AssertionError):
        gate.verify_execution_report(path, nodes)


def test_receipts_are_complete_compressed_and_exclude_native_artifacts(tmp_path):
    work = tmp_path / "work" / "root-joins"
    work.mkdir(parents=True)
    original = "an exact failure traceback\n" * 100
    (work / "target.stderr").write_text(original)
    (work / "junit.xml").write_text("<testsuites />")
    (work / "program.out").write_bytes(b"native binary")
    (work / "program.ll").write_text("compiler IR")
    (work / "program.codegen.json").write_text("large compiler intermediate")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "libpy_runtime_pcc_py.a").write_bytes(b"archive")
    gate.collect_evidence(tmp_path)
    evidence = tmp_path / "evidence"
    assert gzip.decompress((evidence / "root-joins/target.stderr.gz").read_bytes()).decode() == original
    index = json.loads((evidence / "index.json").read_text())
    assert index["status"] == "COMPLETE"
    assert {row["source"] for row in index["files"]} == {
        "root-joins/target.stderr", "root-joins/junit.xml",
    }


def test_evidence_limit_is_explicit_and_fails_closed(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    (work / "target.stderr").write_text("failure must not be silently truncated")
    monkeypatch.setattr(gate, "EVIDENCE_LIMIT", 1)
    with pytest.raises(AssertionError, match="Evidence exceeded"):
        gate.collect_evidence(tmp_path)
    index = json.loads((tmp_path / "evidence/index.json").read_text())
    assert index["status"] == "LIMIT_EXCEEDED"
    assert index["files"][0]["status"] == "OMITTED_EVIDENCE_LIMIT"
    assert index["files"][0]["sha256"] == gate._sha256(work / "target.stderr")
    assert not list((tmp_path / "evidence").rglob("*.gz"))


def test_pytest_command_clears_marker_defaults_and_disables_xdist(tmp_path, monkeypatch):
    seen = []

    def command(arguments, directory, environment):
        seen.append(arguments)
        directory.mkdir(parents=True)
        _write_reports(directory / "live.jsonl", _reports(["test.py::test_native[pcc0]"]))
        return 0

    monkeypatch.setattr(gate, "_command", command)
    assert gate._pytest_gate("smoke", ["test.py::test_native[pcc0]"], tmp_path, {})
    args = seen[0]
    assert args[args.index("-o") + 1] == "addopts="
    assert "-x" in args and "-n0" in args
    assert "--collect-only" not in args and "-m" not in args[3:]


def test_workflow_keeps_stage1_configuration_and_evidence_separate():
    workflow = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    smoke = workflow.split("  macos-pcc0-regressions:\n", 1)[1].split("  other-platform-native-wheel:\n", 1)[0]
    stage1 = workflow.split("  pcc1-package-parity:\n", 1)[1]
    assert "runs-on: macos-15" in smoke
    assert "timeout-minutes: 25" in smoke and "--timeout 1200" in smoke
    assert "--max-tree-rss-bytes 4294967296" in smoke
    assert "--darwin-preflight-reserve-bytes 536870912" in smoke
    assert "path: build/macos-regressions/evidence/" in smoke
    assert "actions/download-artifact" not in smoke + stage1
    assert "timeout-minutes: 45" in stage1
    assert 'PCC_BOOTSTRAP_STAGE_TIMEOUT: "2400"' in stage1
    assert "PCC_WITH_THREADS" not in stage1
    assert "ci_macos_regression_gate" not in stage1


def test_root_join_gate_names_the_five_original_pcc0_witnesses():
    nodes = dict(gate.GATES)["root-joins"]
    assert nodes == tuple(gate.OWNERSHIP_NODE + "[pcc0-" + name + "]" for name in (
        "typed-list-unbox", "typed-loop-unbox", "typed-comprehension-unbox",
        "original-direct-payload", "original-valuebox",
    ))


def test_failed_gate_stops_before_runtime_build_and_marks_remaining_unrun(tmp_path, monkeypatch):
    monkeypatch.setattr(gate.sys, "platform", "darwin")
    monkeypatch.setattr(gate.platform, "machine", lambda: "arm64")
    monkeypatch.delenv("PCC_TEST_COMPILER", raising=False)
    calls = []

    def fail(name, nodes, out, environment):
        calls.append(name)
        raise AssertionError("original clock failure")

    monkeypatch.setattr(gate, "_pytest_gate", fail)
    with pytest.raises(AssertionError, match="original clock failure"):
        gate.run(tmp_path)
    assert calls == ["clock"]
    receipt = json.loads((tmp_path / "work/qualification.json").read_text())
    assert receipt["status"] == "FAIL"
    assert receipt["gates"]["clock"]["status"] == "FAIL"
    assert all(row["status"] == "NOT_RUN" for name, row in receipt["gates"].items() if name != "clock")
    assert receipt["gates"]["root-joins"]["expected_nodes"] == list(dict(gate.GATES)["root-joins"])
