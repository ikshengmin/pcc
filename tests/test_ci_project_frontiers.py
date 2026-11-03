"""Unified CI wiring models; these do not execute either project frontier."""
from pathlib import Path
import re

import pytest

from scripts import ci_validation_plan as plan
from scripts import ci_validation_run as validation


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pcc1-package-parity.yml"
PROJECTS = (
    ("numpy", "scripts/numpy_source_frontier.py", "--host-pcc-development --output-dir"),
    ("orjson", "scripts/ci_orjson_source_frontier.py", "--output"),
)


def job(name):
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(?P<body>.*?)(?=^  [\w-]+:\n|\Z)",
        WORKFLOW.read_text(),
    )
    assert match is not None, name
    return match.group("body")


@pytest.mark.parametrize("project,script,arguments", PROJECTS)
def test_project_jobs_are_independent_host_lanes_in_the_unified_action(project, script, arguments):
    body = job(project + "-source-frontier")
    assert re.findall(r"(?m)^    needs: (.*)$", body) == ["validation-plan"]
    assert "if: ${{ needs.validation-plan.outputs.source == 'true' }}" in body
    assert "runs-on: ubuntu-24.04" in body
    assert "timeout-minutes: 5" in body
    assert "host-PCC source frontier (not native pcc1)" in body
    assert 'CI: "true"' in body
    assert 'PCC_NO_AUTO_PCC1: "1"' in body
    assert 'PCC_TEST_NO_NATIVE_PROVISIONING: "1"' in body
    assert 'PYTHONDONTWRITEBYTECODE: "1"' in body
    assert "ref: ${{ github.sha }}" in body
    assert "persist-credentials: false" in body
    assert 'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"' in body
    assert "uv sync --frozen --dev --no-install-project" in body
    assert "actions/download-artifact" not in body
    assert "--pcc1" not in body and "--bootstrap-receipt" not in body


@pytest.mark.parametrize("project,script,arguments", PROJECTS)
def test_runner_cli_uses_fresh_output_and_cannot_hide_nonzero_exit(project, script, arguments):
    body = job(project + "-source-frontier")
    run = re.search(r"(?ms)^        run: \|\n(.*?)(?=^      - name:|\Z)", body)
    assert run is not None
    command = run.group(1)
    assert "set -euo pipefail" in command
    assert f'OUT="$RUNNER_TEMP/{project}-source-frontier-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"' in command
    assert 'mkdir "$OUT"' in command
    assert 'mkdir "$OUT/frontier"' not in command
    assert f"uv run --frozen --no-sync python {script} \\\n" in command
    assert arguments + ' "$OUT/frontier"' in command
    assert command.rstrip().endswith('>"$OUT/runner.stdout" 2>"$OUT/runner.stderr"')
    for forbidden in ("continue-on-error:", "|| true", "set +e", "exit 0", "--timeout", "--rss"):
        assert forbidden not in body


@pytest.mark.parametrize("project,script,arguments", PROJECTS)
def test_failed_frontiers_always_upload_complete_reports_and_logs(project, script, arguments):
    body = job(project + "-source-frontier")
    upload = re.search(r"(?ms)^      - name: Retain [^\n]*\n(?P<body>.*)\Z", body)
    assert upload is not None
    evidence = upload.group("body")
    assert "if: always()" in evidence
    assert "uses: actions/upload-artifact@v6" in evidence
    assert "retention-days: 7" in evidence
    # Whole-directory retention includes result.json/frontier.json, partial
    # stages, original runner streams and every internal watchdog receipt.
    assert ("path: ${{ runner.temp }}/" + project
            + "-source-frontier-${{ github.run_id }}-${{ github.run_attempt }}/") in evidence
    assert ("name: " + project
            + "-source-frontier-${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}") in evidence


@pytest.mark.parametrize("path", [
    ".github/workflows/pcc1-package-parity.yml",
    "scripts/numpy_source_frontier.py",
    "scripts/ci_orjson_source_frontier.py",
    "scripts/ci_source_frontier_support.py",
    "tests/test_numpy_source_frontier.py",
    "tests/fixtures/numpy_source_frontier.json",
    "tests/rust/test_rust_orjson_source_frontier.py",
    "tests/test_ci_project_frontiers.py",
    "projects/numpy-2.4.4/numpy/_core/src/npymath/halffloat.cpp",
    "projects/orjson-3.13.0/src/lib.rs",
])
def test_project_inputs_select_source_obligations_without_new_dispatch_modes(path):
    selected = plan.select([("M", path)])
    assert selected["source"] is True
    assert selected["matrix"]["include"]


def test_wiring_models_are_discovered_by_the_existing_ci_contract_domain():
    assert "tests/test_ci_project_frontiers.py" in validation.modules("ci-contracts")


def test_frontier_children_keep_the_original_resource_limits(tmp_path):
    from scripts import numpy_source_frontier as numpy
    from scripts import ci_source_frontier_support as support

    assert numpy.TIMEOUT == validation.TIMEOUT == 90
    assert numpy.RSS_LIMIT == support.RSS_BYTES == validation.RSS == 1073741824
    command = validation.guarded_command(tmp_path, ["unexecuted-fixture-command"])
    assert command[command.index("--timeout") + 1] == "90"
    assert command[command.index("--auto-tree-rss-ceiling-bytes") + 1] == "1073741824"
    assert command[command.index("--min-tree-rss-bytes") + 1] == "1073741824"
