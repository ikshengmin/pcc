"""Source-platform CI wiring contracts; no compiler execution in these tests."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re

import pytest

from scripts import ci_validation_plan as plan
from scripts import ci_validation_tools as tools

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pcc1-package-parity.yml"


def job(name):
    text = WORKFLOW.read_text().split(f"\n  {name}:\n", 1)[1]
    return re.split(r"\n  [a-zA-Z0-9_-]+:\n", text, maxsplit=1)[0]


def steps(text):
    return re.split(r"\n      - ", text.split("    steps:\n", 1)[1])


def test_cpp_adds_exact_mac_owner_without_replacing_whole_linux_lanes():
    result = plan.select([("M", "pcc/frontends/cpp/parser.py")])
    assert result["matrix"]["include"] == [
        {"domain": "cpp", "lane": lane} for lane in plan.LANES]
    assert result["source_platform"]
    assert result["matrix_platform"]["include"] == [
        {"domain": "cpp", "lane": "reference", "platform": "macos-arm64", "runner": "macos-15"}]
    assert result["ownership_manifest_sha256"] == hashlib.sha256(
        (ROOT / "scripts/data/ci_validation_owners.json").read_bytes()).hexdigest()
    assert result["matrix_native"]["include"] == [{"domain": "cpp", "lane": "integration"}]
    assert result["native_stage1"] and not result["legacy"]
    assert result["matrix_native_stage1"]["include"] == list(plan.NATIVE_PLATFORMS[:1])


def test_go_only_has_no_invented_mac_obligation():
    result = plan.select([("M", "tests/go/test_math_bits.py")])
    assert not result["source_platform"]
    assert result["matrix_platform"] == {"include": []}
    assert result["matrix"]["include"] == [
        {"domain": "go", "lane": lane} for lane in plan.LANES]
    assert not result["native_stage1"]


def test_manifest_optional_input_and_shared_scope_preserve_full_producers():
    manifest = json.loads((ROOT / "scripts/data/ci_validation_owners.json").read_text())
    result = plan.select([("M", "pcc/backend/shared.py")], ownership_manifest=(manifest, "fixture-digest"))
    assert result["ownership_manifest_sha256"] == "fixture-digest"
    assert result["matrix_native_stage1"]["include"] == list(plan.NATIVE_PLATFORMS)
    expected = {(domain, lane) for domain in (*plan.LANGUAGES, "shared", "ci-contracts") for lane in plan.LANES}
    assert {(row["domain"], row["lane"]) for row in result["matrix"]["include"]} == expected
    assert result["legacy"] and result["source_platform"]


def test_plan_retains_exact_workflow_context_and_outputs():
    text = (ROOT / "scripts/ci_validation_plan.py").read_text()
    for variable in ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        assert f'os.environ["{variable}"]' in text
    assert 'plan["context"]' in text
    planner = job("validation-plan")
    for key in ("source_platform", "matrix_platform"):
        assert f"{key}: ${{{{ steps.select.outputs.{key} }}}}" in planner
    assert "validation-plan-${{ github.sha }}-${{ github.run_attempt }}" in planner


def test_all_source_producers_keep_original_guards_and_frozen_sparse_inputs():
    for name in ("source-validation", "shared-source-validation", "source-platform-validation"):
        text = job(name)
        assert "timeout-minutes: 15" in text and "fail-fast: false" in text
        assert "continue-on-error:" not in text
        assert "python-version-file: .python-version" in text
        assert "uv sync --frozen --dev --no-install-project" in text
        assert "ref: ${{ github.sha }}" in text
        assert "sparse-checkout-cone-mode: false" in text
        assert "            /*\n            !/projects/nginx-1.28.3/" in text
        assert 'PCC_NO_AUTO_PCC1: "1"' in text and 'PCC_TEST_NO_NATIVE_PROVISIONING: "1"' in text
        assert "--workers" not in text and "--timeout" not in text and "--rss" not in text
        assert text.index("scripts/ci_validation_tools.py") < text.index("scripts/ci_validation_run.py")
        platform = '${{ matrix.platform }}' if name == "source-platform-validation" else "linux-x86_64"
        assert f"source-${{{{ matrix.domain }}}}-${{{{ matrix.lane }}}}-{platform}-${{{{ github.sha }}}}-${{{{ github.run_attempt }}}}" in text
        assert f'--platform "{platform}"' in text or f"--platform {platform}" in text
    runner = (ROOT / "scripts/ci_validation_run.py").read_text()
    assert "TIMEOUT = 90" in runner and "RSS = 1073741824" in runner and "DISK_FLOOR = 1073741824" in runner
    assert '"6"' in runner and '"loadgroup"' in runner
    assert '"tools-before.json"' in runner and '"tools-after.json"' in runner
    assert '"source-before.json"' in runner and '"source-after.json"' in runner
    mac = job("source-platform-validation")
    assert "needs: validation-plan" in mac and "matrix_platform" in mac and "runs-on: ${{ matrix.runner }}" in mac
    assert "stage1" not in mac and "native-wheel" not in mac


def test_aggregate_is_required_even_for_failed_cancelled_or_missing_producer():
    text = job("source-platform-coverage")
    assert "needs: [validation-plan, source-validation, shared-source-validation, source-platform-validation]" in text
    assert "if: ${{ always() && (needs.validation-plan.result != 'success' || needs.validation-plan.outputs.source == 'true') }}" in text
    assert "!cancelled()" not in text and ".result == 'success'" not in text
    assert "validation-plan-${{ github.sha }}-${{ github.run_attempt }}" in text
    assert "pattern: coverage-*-${{ github.sha }}-${{ github.run_attempt }}" in text
    assert "merge-multiple: false" in text
    assert "aggregate-source-platform-coverage-${{ github.sha }}-${{ github.run_attempt }}" in text
    assert "if-no-files-found: error" in text
    downloads = []
    reconciles = []
    for step in steps(text):
        if "continue-on-error:" in step:
            assert "actions/download-artifact@v7" in step
            downloads.append(step)
        if "scripts/ci_validation_coverage.py" in step:
            reconciles.append(step)
            assert "if: always()" in step and "continue-on-error:" not in step
            for argument in ("--plan", "--artifacts", "--out"):
                assert argument in step
        if "actions/upload-artifact@v6" in step:
            assert "if: always()" in step
    assert len(downloads) == 2 and len(reconciles) == 1
    assert "other-platform-native-wheel" not in text


def test_cpp_tools_bind_direct_clang_path_and_preserve_gpp():
    tree = ast.parse((ROOT / "scripts/ci_validation_tools.py").read_text())
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "tools" for target in node.targets)]
    mapping = assignments[0].value.value
    assert ast.literal_eval(mapping)["cpp"] == ("g++", "clang")
    text = (ROOT / "scripts/ci_validation_tools.py").read_text()
    assert 'environment["PCC_CPP_ORACLE"] = receipt["g++"]["invocation"]' in text
    assert '"path_lookup": name == "clang"' in text
    assert 'shutil.which(name) != row["invocation"]' in text


def test_clang_receipt_rejects_changed_or_new_path_lookup(monkeypatch):
    monkeypatch.setattr(tools.shutil, "which", lambda name: "/other/clang")
    with pytest.raises(ValueError, match="PATH invocation changed"):
        tools.verify_tool_receipt({"clang": {"path_lookup": True, "invocation": "/original/clang"}})
    with pytest.raises(ValueError, match="appeared"):
        tools.verify_tool_receipt({"clang": {"path_lookup": True, "status": "UNRUN_PREREQUISITE"}})
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    assert tools.verify_tool_receipt({"clang": {"path_lookup": True, "status": "UNRUN_PREREQUISITE"}})["verified"] == []


def test_compact_join_allowlist_preserves_full_raw_diagnostics():
    suffixes = {
        "*.json", "collection-*/modules.json", "collection-*/result.json",
        "collection-*/live.jsonl", "collection-*/inventory/*.json",
        "batch-*/selection.json", "batch-*/result.json", "batch-*/live.jsonl",
        "batch-*/outcomes.json", "batch-*/inventory/*.json",
    }
    for name in ("source-validation", "shared-source-validation", "source-platform-validation"):
        uploads = [step for step in steps(job(name)) if "actions/upload-artifact@v6" in step]
        assert len(uploads) == 2
        raw = next(step for step in uploads if "name: source-" in step)
        compact = next(step for step in uploads if "name: coverage-" in step)
        assert "${{ runner.temp }}/source-validation\n" in raw
        assert "${{ runner.temp }}/reference-tools" in raw
        paths = compact.split("          path: |\n", 1)[1].splitlines()
        assert {line.strip().removeprefix("${{ runner.temp }}/source-validation/")
                for line in paths if line.strip()} == suffixes
        assert "if: always()" in compact and "if-no-files-found: error" in compact
