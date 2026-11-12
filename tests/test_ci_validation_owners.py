"""Authored platform coverage models. These never invoke PCC or pytest children."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import ci_validation_owners as owners
from scripts import ci_validation_run as run
from scripts import ci_validation_tools as tools

MODULE = "tests/cpp/test_headers.py"
LINUX = "linux-x86_64"
MAC = "macos-arm64"


def row(name, *, selected=True, lane="reference", groups=()):
    return {"file": MODULE, "function": name, "nodeid": MODULE + "::" + name,
            "canonical_nodeid": MODULE + "::" + name, "lane": lane,
            "selected_by_repository": selected, "groups": list(groups),
            "gates": [], "reference_activity": "external-reference"}


def manifest(platforms=None):
    platforms = platforms or [MAC]
    return {"schema": 1, "default_platform": LINUX, "overrides": [
        {"domain": "cpp", "lane": "reference", "nodeid": row("darwin")["nodeid"],
         "platforms": platforms, "tools": {platform: ["clang"] for platform in platforms}}]}


def test_mixed_module_partition_preserves_raw_gate_metadata():
    rows = [row("linux"), row("darwin", selected=False)]
    frozen = copy.deepcopy(rows)
    linux = owners.partition(rows, manifest(), "cpp", "reference", LINUX)
    assert linux["local_nodeids"] == [row("linux")["nodeid"]]
    assert linux["transfers"] == [dict(domain="cpp", lane="reference", nodeid=row("darwin")["nodeid"],
                                      platform=MAC, tools=["clang"])]
    mac = owners.partition(rows, manifest(), "cpp", "reference", MAC, complete=False)
    assert mac["local_nodeids"] == [row("darwin")["nodeid"]]
    assert rows == frozen
    assert not rows[1]["selected_by_repository"]  # A missing owner gate remains UNRUN.
    assert owners.owner_modules(manifest(), "cpp", "reference", MAC) == [MODULE]
    assert owners.additional_campaigns(["cpp"], manifest()) == [
        {"domain": "cpp", "lane": "reference", "platform": MAC, "runner": "macos-15"}]


def test_all_of_owners_never_becomes_fallback():
    result = owners.partition([row("darwin")], manifest([LINUX, MAC]), "cpp", "reference", LINUX)
    assert len(result["obligations"]) == 2
    assert result["local_nodeids"] == [row("darwin")["nodeid"]]
    assert result["transfers"][0]["platform"] == MAC


@pytest.mark.parametrize("change", [
    lambda m: m.update(schema=True),
    lambda m: m.update(extra="unknown"),
    lambda m: m["overrides"].append(copy.deepcopy(m["overrides"][0])),
    lambda m: m["overrides"][0].update(platforms=[MAC, MAC]),
    lambda m: m["overrides"][0].update(platforms=["windows-x86_64"]),
    lambda m: m["overrides"][0].update(lane="fallback"),
    lambda m: m["overrides"][0].update(nodeid="../test.py::test_bad"),
    lambda m: m["overrides"][0].update(tools={}),
    lambda m: m["overrides"][0].update(tools={MAC: ["clang", "clang"]}),
])
def test_malformed_or_duplicate_rules_fail_closed(change):
    value = manifest()
    change(value)
    with pytest.raises(ValueError):
        owners.validate_manifest(value)


@pytest.mark.parametrize("rows,match", [
    ([row("linux")], "stale"),
    ([row("darwin", lane="ordinary")], "lane mismatch"),
    ([row("darwin"), row("darwin")], "duplicate"),
    ([row("linux", groups=["dependency"]), row("darwin", groups=["dependency"])], "splits"),
])
def test_inventory_rules_fail_closed(rows, match):
    with pytest.raises(ValueError, match=match):
        owners.partition(rows, manifest(), "cpp", "reference", LINUX)


def test_stale_rules_checked_across_lanes_but_not_uncollected_mac_modules():
    value = manifest()
    other = copy.deepcopy(value["overrides"][0])
    other.update(nodeid="tests/cpp/test_other.py::test_other", lane="ordinary")
    value["overrides"].append(other)
    with pytest.raises(ValueError, match="stale"):
        owners.partition([row("darwin")], value, "cpp", "reference", LINUX)
    assert owners.partition([row("darwin")], value, "cpp", "reference", MAC,
                            complete=False)["local_nodeids"] == [row("darwin")["nodeid"]]


def test_parameterized_exact_ids_are_distinct():
    value = manifest()
    value["overrides"][0]["nodeid"] = row("darwin[a]")["nodeid"]
    result = owners.partition([row("darwin[a]"), row("darwin[b]")], value, "cpp", "reference", LINUX)
    assert result["local_nodeids"] == [row("darwin[b]")["nodeid"]]


def test_manifest_digest_is_exact_bytes_and_duplicate_keys_rejected(tmp_path):
    path = tmp_path / owners.MANIFEST
    path.parent.mkdir(parents=True)
    data = json.dumps(manifest()).encode() + b"\n"
    path.write_bytes(data)
    assert owners.read_manifest(tmp_path) == (manifest(), hashlib.sha256(data).hexdigest())
    path.write_text('{"schema":1,"schema":1}')
    with pytest.raises(ValueError, match="duplicate"):
        owners.read_manifest(tmp_path)


def context(monkeypatch):
    for name, value in {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_SHA": "a" * 40,
                        "GITHUB_RUN_ID": "12", "GITHUB_RUN_ATTEMPT": "2"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(owners.host_platform, "system", lambda: "Linux")
    monkeypatch.setattr(owners.host_platform, "machine", lambda: "x86_64")


@pytest.mark.parametrize("key,value", [("GITHUB_SHA", "main"), ("GITHUB_RUN_ID", "0"),
                                        ("GITHUB_RUN_ATTEMPT", "-1"), ("GITHUB_REPOSITORY", "repo")])
def test_invalid_context_rejected(monkeypatch, key, value):
    context(monkeypatch)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="context"):
        owners.host_context(LINUX)


def test_actual_host_cannot_be_relabelled(monkeypatch):
    context(monkeypatch)
    assert owners.host_context(LINUX)[1] == {"platform": LINUX, "system": "Linux", "machine": "x86_64"}
    with pytest.raises(ValueError, match="actual host"):
        owners.host_context(MAC)
    monkeypatch.setattr(owners.host_platform, "machine", lambda: "aarch64")
    with pytest.raises(ValueError, match="actual host"):
        owners.host_context(LINUX)


def test_required_tools_bind_actual_path_and_available_receipt(tmp_path, monkeypatch):
    path = tmp_path / "clang"
    path.write_bytes(b"audited fixture")
    monkeypatch.setattr(owners.shutil, "which", lambda _: str(path))
    obligation = {"platform": MAC, "tools": ["clang"]}
    receipt = {"clang": {"status": "AVAILABLE", "invocation": str(path), "resolved": str(path.resolve()),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
    before = owners.required_tool_bindings([obligation], MAC, receipt)
    assert before["bindings"][0]["tool"] == "clang"
    for key, value in [("status", "UNRUN_PREREQUISITE"), ("invocation", "/other/clang"),
                       ("resolved", "/other/clang"), ("sha256", "0" * 64)]:
        bad = copy.deepcopy(receipt)
        bad["clang"][key] = value
        with pytest.raises(ValueError):
            owners.required_tool_bindings([obligation], MAC, bad)
    path.write_bytes(b"changed")
    with pytest.raises(ValueError):
        owners.required_tool_bindings([obligation], MAC, receipt)


@pytest.mark.parametrize("platform", [LINUX, MAC])
@pytest.mark.parametrize("failure", [None, "missing_gate", "interrupt", "tool_closure", "manifest_closure", "path_closure"])
def test_mocked_campaign_transfers_never_pass_or_mask_local_failures(tmp_path, monkeypatch, failure, platform):
    root = tmp_path / "source"
    root.mkdir()
    (root / "pyproject.toml").write_text("# mocked configuration\n")
    # Use parsed configuration directly: this is a runner model, not a TOML test.
    monkeypatch.setattr(run.tomllib, "loads", lambda _: {"tool": {"pytest": {"ini_options": {
        "addopts": '-n 6 --dist=loadgroup -m "not integration"'}}}})
    path = root / owners.MANIFEST
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest()))
    frozen, digest = owners.read_manifest(root)
    identity = {"sha": "a" * 40, "files": {}, "sparse_omissions": [],
                "ownership_manifest": frozen, "ownership_manifest_sha256": digest}
    context(monkeypatch)
    if platform == MAC:
        monkeypatch.setattr(owners.host_platform, "system", lambda: "Darwin")
        monkeypatch.setattr(owners.host_platform, "machine", lambda: "arm64")
    monkeypatch.setattr(run, "ROOT", root)
    monkeypatch.setattr(run, "identity", lambda: identity)
    monkeypatch.setattr(run, "modules", lambda _: [MODULE])
    tool_path = tmp_path / "tools.json"
    executable = tmp_path / "clang"
    executable.write_bytes(b"audited tool fixture")
    monkeypatch.setattr(owners.shutil, "which", lambda _: str(executable))
    tool_path.write_text(json.dumps({"clang": {"status": "AVAILABLE", "invocation": str(executable),
        "resolved": str(executable.resolve()), "sha256": hashlib.sha256(executable.read_bytes()).hexdigest()}}))
    monkeypatch.setenv("PCC_CI_TOOL_RECEIPT", str(tool_path))
    monkeypatch.delenv("PCC_RUSTC_INSTALL_RECEIPT", raising=False)
    checks = []
    def verify(_):
        checks.append(True)
        if failure == "tool_closure" and len(checks) == 2:
            raise ValueError("changed tool")
        return {"verified": []}
    monkeypatch.setattr(tools, "verify_tool_receipt", verify)
    rows = [row("linux", selected=platform == LINUX and failure != "missing_gate"),
            row("darwin", selected=platform == MAC and failure != "missing_gate")]
    monkeypatch.setattr(run, "summarize_collection", lambda *_: {
        "modules": [MODULE], "rows": rows, "selected_count": 1, "status": "COLLECTED_NOT_EXECUTED"})
    selections = []
    def guard(directory, command, env, original):
        if directory.name.startswith("batch"):
            selections.append(json.loads((directory / "selection.json").read_text()))
            if failure == "interrupt":
                raise KeyboardInterrupt("fixture")
            if failure == "manifest_closure":
                path.write_text(json.dumps(manifest()) + "\n")
            if failure == "path_closure":
                monkeypatch.setattr(owners.shutil, "which", lambda _: "/changed/clang")
        return 0
    monkeypatch.setattr(run, "run_guard", guard)
    def summarize(directory, expected, raw):
        assert raw == rows  # Planner partition must not contaminate raw witnesses.
        return {"outcomes": [{"nodeid": node, "status": "PASS", "terminal_node": True} for node in expected],
                "pytest_exitstatus": 0, "pytest_testsfailed": 0, "session_events": [],
                "evidence_errors": [], "unattributed_reports": []}
    monkeypatch.setattr(run, "summarize_batch", summarize)
    out = tmp_path / "out"
    args = SimpleNamespace(domain="cpp", lane="reference", platform=platform, out=out)
    if failure == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            run.run(args)
    else:
        assert run.run(args) == (0 if failure is None or (failure == "path_closure" and platform == LINUX) else 1)
    receipt = json.loads((out / "receipt.json").read_text())
    assert receipt["status"] == {None: "PLATFORM_COVERAGE_PENDING", "missing_gate": "INCOMPLETE_UNRUN",
                                  "interrupt": "INTERRUPTED", "tool_closure": "INFRASTRUCTURE_OR_RESOURCE_FAILURE",
                                  "manifest_closure": "INFRASTRUCTURE_OR_RESOURCE_FAILURE",
                                  "path_closure": "PLATFORM_COVERAGE_PENDING" if platform == LINUX else
                                  "INFRASTRUCTURE_OR_RESOURCE_FAILURE"}[failure]
    assert receipt["transfers"][0]["status"] == "TRANSFER_PENDING"
    nonlocal_name = "darwin" if platform == LINUX else "linux"
    assert all(row(nonlocal_name)["nodeid"] not in selected for selected in selections)
    assert len(checks) == 2
