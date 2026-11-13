"""Inventory measurement contracts without importing their test/native owners."""
from pathlib import Path
import json
import shlex
import tomllib

import pytest

from scripts import ci_validation_owners as owners
from scripts import ci_validation_plan as plan
from scripts import ci_validation_run as run


ROOT = Path(__file__).resolve().parents[1]
MODULES = (
    "tests/python/test_compile_window_measurement.py",
    "tests/python/test_compile_window_wiring.py",
    "tests/python/test_pcc_compile_ab_tool.py",
)
DARWIN_NODES = (
    MODULES[2] + "::test_runner_writes_complete_manifest_for_four_matched_pairs",
    MODULES[2] + "::test_runner_writes_scanner_manifest_for_four_matched_pairs",
)


def _write(root, name, text="raise AssertionError('inventory must not import tests')\n"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_measurement_registration_keeps_every_original_discovery_route(tmp_path):
    for path in MODULES:
        _write(tmp_path, path)
    discovered = (
        "tests/test_ci_existing.py",
        "tests/test_xdist_existing.py",
        "tests/python/test_workflow_contract.py",
    )
    for path in discovered[:2]:
        _write(tmp_path, path)
    _write(tmp_path, discovered[2], "# .github/workflows/example.yml\nraise AssertionError\n")
    _write(tmp_path, "tests/python/test_unrelated.py")
    assert set(MODULES) <= set(run.CI_CONTRACT_MODULES)
    assert run.modules("ci-contracts", root=tmp_path) == sorted((*MODULES, *discovered))


@pytest.mark.parametrize("missing", MODULES)
@pytest.mark.parametrize("directory", [False, True])
def test_missing_named_measurement_module_fails_closed(tmp_path, missing, directory):
    for path in MODULES:
        if path != missing:
            _write(tmp_path, path)
    _write(tmp_path, "tests/test_ci_existing.py")
    if directory:
        (tmp_path / missing).mkdir(parents=True)
    with pytest.raises(ValueError, match="explicit CI contract scope disappeared") as error:
        run.modules("ci-contracts", root=tmp_path)
    assert missing in str(error.value)


def test_real_inventory_and_plan_keep_complete_measurement_modules():
    files = run.modules("ci-contracts")
    assert set(MODULES) <= set(files)
    assert [path for shard in run.collection_plan(files) for path in shard] == files
    for path in (*MODULES, "scripts/ci_validation_run.py"):
        selection = plan.select([("M", path)])
        assert selection["legacy"] and "ci-contracts" in selection["domains"]
    assert not plan.requires_native_inputs("ci-contracts", "ordinary")
    assert plan.requires_native_inputs("ci-contracts", "integration")


def test_only_actual_darwin_timing_runner_transfers_receipt_coverage():
    manifest, _digest = owners.read_manifest(ROOT)
    rules = [rule for rule in manifest["overrides"]
             if rule["domain"] == "ci-contracts" and rule["nodeid"].split("::")[0] in MODULES]
    assert rules == [{"domain": "ci-contracts", "lane": "ordinary", "nodeid": node,
                      "platforms": ["macos-arm64"], "tools": {"macos-arm64": []}}
                     for node in DARWIN_NODES]
    rows = []
    for node in (*[path + "::test_portable_receipt" for path in MODULES], *DARWIN_NODES):
        module, function = node.split("::")
        rows.append({"file": module, "canonical_nodeid": node, "nodeid": node,
                     "function": function, "groups": [], "gates": [],
                     "reference_activity": None, "selected_by_repository": True,
                     "lane": "ordinary"})
    partition = owners.partition(rows, manifest, "ci-contracts", "ordinary", "linux-x86_64")
    assert partition["local_nodeids"] == sorted(row["nodeid"] for row in rows if row["nodeid"] not in DARWIN_NODES)
    assert [row["nodeid"] for row in partition["transfers"]] == list(DARWIN_NODES)
    mac_rows = [row for row in rows if row["file"] == MODULES[2]]
    mac = owners.partition(mac_rows, manifest, "ci-contracts", "ordinary", "macos-arm64", complete=False)
    assert mac["local_nodeids"] == list(DARWIN_NODES)
    assert [row["nodeid"] for row in mac["transfers"]] == [MODULES[2] + "::test_portable_receipt"]
    assert partition["transfers"] == [row for row in mac["obligations"] if row["platform"] == "macos-arm64"]
    registry = json.loads(run.REFERENCES.read_text())["functions"]
    assert all(node.split("::")[1] not in registry.get(MODULES[2], {})
               for node in DARWIN_NODES)
    assert MODULES[2] in owners.owner_modules(manifest, "ci-contracts", "ordinary", "macos-arm64")
    campaigns = owners.additional_campaigns(["ci-contracts"], manifest)
    assert {"domain": "ci-contracts", "lane": "ordinary", "platform": "macos-arm64",
            "runner": "macos-15"} in campaigns


def test_registration_retains_original_execution_limits():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert shlex.split(config["tool"]["pytest"]["ini_options"]["addopts"]) == [
        "-n", "6", "--dist=loadgroup", "-m", "not integration",
    ]
    assert run.TIMEOUT == 90
    assert run.RSS == run.DISK_FLOOR == 1073741824
