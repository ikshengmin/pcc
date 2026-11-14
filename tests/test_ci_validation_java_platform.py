"""Java Darwin ownership and audited caller bindings, without native execution."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

from scripts import ci_validation_owners as owners
from scripts import ci_validation_plan as plan
from scripts import ci_validation_tools as tools

ROOT = Path(__file__).resolve().parents[1]
MODULE = "tests/java/test_source_floating_darwin_abi.py"
DARWIN = MODULE + "::test_real_clang_caller_observes_tenth_java_float_on_darwin_arm64"
DARWIN_CALLER = MODULE + "::test_owned_caller_passes_tenth_float_to_clang_on_darwin_arm64"
DARWIN_NODES = (DARWIN, DARWIN_CALLER)


def test_java_adds_exact_mac_integration_without_stage1_or_losing_linux_lanes():
    result = plan.select([("M", MODULE)])
    assert result["matrix"]["include"] == [
        {"domain": "java", "lane": lane} for lane in plan.LANES]
    assert result["matrix_platform"]["include"] == [
        {"domain": "java", "lane": "integration", "platform": "macos-arm64", "runner": "macos-15"}]
    assert result["matrix_independent"] == result["matrix"]
    assert result["matrix_native"] == {"include": []}
    assert not result["native_stage1"] and not result["legacy"]
    assert not plan.requires_native_inputs("java", "integration")
    manifest, _ = owners.read_manifest(ROOT)
    rules = [rule for rule in manifest["overrides"] if rule["domain"] == "java"]
    assert rules == [{"domain": "java", "lane": "integration", "nodeid": node,
                      "platforms": ["macos-arm64"], "tools": {"macos-arm64": ["clang"]}}
                     for node in DARWIN_NODES]
    assert owners.owner_modules(manifest, "java", "integration", "macos-arm64") == [MODULE]


def test_java_partition_keeps_ordinary_cross_objects_and_other_integration_on_linux():
    manifest, _ = owners.read_manifest(ROOT)
    rows = []
    for name, lane in (("test_float_stack_original_has_owned_darwin_cross_object_only", "ordinary"), ("linux_integration", "integration"),
                       *((node.split("::")[1], "integration") for node in DARWIN_NODES)):
        node = MODULE + "::" + name
        rows.append({"file": MODULE, "function": name, "nodeid": node,
                     "canonical_nodeid": node, "lane": lane, "groups": [], "gates": [],
                     "reference_activity": "external-reference", "selected_by_repository": node not in DARWIN_NODES})
    frozen = copy.deepcopy(rows)
    ordinary = owners.partition(rows, manifest, "java", "ordinary", "linux-x86_64")
    assert ordinary["local_nodeids"] == [MODULE + "::test_float_stack_original_has_owned_darwin_cross_object_only"]
    assert ordinary["transfers"] == []
    integration = owners.partition(rows, manifest, "java", "integration", "linux-x86_64")
    assert integration["local_nodeids"] == [MODULE + "::linux_integration"]
    assert integration["transfers"] == [{"domain": "java", "lane": "integration", "nodeid": node,
                                        "platform": "macos-arm64", "tools": ["clang"]}
                                       for node in sorted(DARWIN_NODES)]
    mac_rows = copy.deepcopy(rows)
    for row in mac_rows:
        row["selected_by_repository"] = True
    mac = owners.partition(mac_rows, manifest, "java", "integration", "macos-arm64", complete=False)
    assert mac["local_nodeids"] == sorted(DARWIN_NODES)
    assert mac["transfers"] == [{"domain": "java", "lane": "integration",
                                 "nodeid": MODULE + "::linux_integration",
                                 "platform": "linux-x86_64", "tools": []}]
    assert rows == frozen  # Ownership does not turn Linux's missing native gate into a pass.
    assert all(not row["selected_by_repository"] for row in rows if row["nodeid"] in DARWIN_NODES)

    # Reproduce the missing-owner defect: the original reverse node otherwise
    # defaults to Linux and is transferred away from its eligible Mac consumer.
    missing = copy.deepcopy(manifest)
    missing["overrides"] = [rule for rule in missing["overrides"] if rule["nodeid"] != DARWIN_CALLER]
    assert DARWIN_CALLER in owners.partition(rows, missing, "java", "integration",
                                            "linux-x86_64")["local_nodeids"]
    wrong_mac = owners.partition(mac_rows, missing, "java", "integration", "macos-arm64", complete=False)
    assert DARWIN_CALLER not in wrong_mac["local_nodeids"]
    assert {"domain": "java", "lane": "integration", "nodeid": DARWIN_CALLER,
            "platform": "linux-x86_64", "tools": []} in wrong_mac["transfers"]


def test_java_clang_overrides_inherited_tool_with_audited_invocation():
    environment = {"PCC_JAVA_DARWIN_CLANG": "/unverified/clang", "OTHER": "preserved"}
    tools.bind_java_darwin_clang(environment, {"clang": {"status": "AVAILABLE", "invocation": "/audited/clang"}})
    assert environment == {"PCC_JAVA_DARWIN_CLANG": "/audited/clang", "OTHER": "preserved"}


@pytest.mark.parametrize("receipt", [{}, {"clang": {"status": "UNRUN_PREREQUISITE"}},
                                     {"clang": {"status": "UNRUN_PREREQUISITE", "invocation": "/bad-version/clang"}}])
def test_unavailable_java_clang_cannot_use_inherited_override(receipt):
    with pytest.raises(ValueError, match="refuse inherited Java Darwin clang"):
        tools.bind_java_darwin_clang({"PCC_JAVA_DARWIN_CLANG": "/unverified/clang"}, receipt)
    empty = {"PCC_JAVA_DARWIN_CLANG": ""}
    tools.bind_java_darwin_clang(empty, receipt)
    assert empty == {}


def test_java_setup_attests_clang_and_exports_exact_invocation(tmp_path, monkeypatch):
    executable = tmp_path / "clang"
    executable.write_bytes(b"fixture, never executed")
    lookups = []
    def which(name):
        lookups.append(name)
        return str(executable) if name == "clang" else None
    monkeypatch.setattr(tools.shutil, "which", which)
    monkeypatch.setattr(tools, "version_observation", lambda *args: {"status": "COMPLETE", "exit_code": 0})
    monkeypatch.delenv("JAVA_HOME_21_X64", raising=False)
    environment = tmp_path / "github-env"
    monkeypatch.setenv("GITHUB_ENV", str(environment))
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["ci_validation_tools.py", "--domain", "java", "--out", str(out)])
    tools.main()
    assert lookups == ["java", "javac", "clang"]
    receipt = json.loads((out / "receipt.json").read_text())
    assert receipt["clang"]["status"] == "AVAILABLE"
    assert receipt["clang"]["path_lookup"] is True
    assert receipt["clang"]["sha256"] == tools.digest(executable)
    assert "PCC_JAVA_DARWIN_CLANG=" + str(executable) + "\n" in environment.read_text()


def test_runner_binds_java_child_before_any_collection():
    source = (ROOT / "scripts/ci_validation_run.py").read_text()
    binding = source.index('bind_java_darwin_clang(env, tool_receipt)')
    assert source.index('save(args.out / "tools-before.json", verify_tool_receipt(tool_receipt))') < binding
    assert source.index('env = dict(os.environ') < binding < source.index('shards = collection_plan(files)')
    assert 'if args.domain == "java":\n            bind_java_darwin_clang(env, tool_receipt)' in source
