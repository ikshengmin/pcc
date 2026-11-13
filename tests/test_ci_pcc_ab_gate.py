"""Source/receipt contracts only. No compiler, subprocess or network is allowed."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import zipfile

import pytest

from scripts import ci_pcc_ab_gate as gate

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pcc1-package-parity.yml"


@pytest.fixture(autouse=True)
def no_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("contract tests must not run processes or access the network")
    monkeypatch.setattr(gate.subprocess, "run", forbidden)
    monkeypatch.setattr(gate.urllib.request, "build_opener", forbidden)
    monkeypatch.setattr(gate, "_DEADLINE_MONOTONIC", None)


def inputs():
    return dict(performance_ab="true", expected_sha="c" * 40, ab_baseline_sha="b" * 40,
                ab_baseline_run="123:2", ab_input_manifest="tests/data/ab.json",
                full_chain="false", pytest_selection="both")


def environment():
    return dict(GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF="refs/heads/master",
                GITHUB_SHA="c" * 40, GITHUB_RUN_ID="456", GITHUB_RUN_ATTEMPT="3")


def test_explicit_sources_are_not_the_coverage_base():
    data = inputs()
    data["base_sha"] = "d" * 40
    result = gate.selectors(environment(), dict(inputs=data))
    assert result["baseline"] == dict(sha="b" * 40, run_id=123, run_attempt=2)
    assert result["candidate"] == dict(sha="c" * 40, run_id=456, run_attempt=3)


@pytest.mark.parametrize("key,value", [
    ("performance_ab", "false"), ("performance_ab", False), ("performance_ab", None),
    ("expected_sha", ""), ("expected_sha", "d" * 40), ("expected_sha", "HEAD"),
    ("ab_baseline_sha", "master"), ("ab_baseline_sha", "c" * 40),
    ("ab_baseline_run", "123"), ("ab_baseline_run", "123:0"),
    ("ab_input_manifest", "../arbitrary.sh"), ("ab_input_manifest", "/tmp/claim.json"),
    ("full_chain", "true"), ("pytest_selection", "not integration"),
])
def test_request_rejects_implicit_stale_or_incomplete_selection(key, value):
    data = inputs()
    data[key] = value
    with pytest.raises(ValueError):
        gate.selectors(environment(), dict(inputs=data))


@pytest.mark.parametrize("event", ["push", "pull_request", "workflow_run", "schedule"])
def test_no_performance_on_ordinary_events_even_with_selector(event):
    env = dict(environment(), GITHUB_EVENT_NAME=event)
    with pytest.raises(ValueError, match="opt-in"):
        gate.selectors(env, dict(inputs=inputs()))


def workflow_job(name):
    block = WORKFLOW.read_text().split("\n  " + name + ":\n", 1)[1]
    return re.split(r"\n  [A-Za-z0-9_-]+:\n", block, maxsplit=1)[0]


def test_workflow_opt_in_is_false_and_all_original_dependencies_are_preserved():
    text = WORKFLOW.read_text()
    block = text.split("      performance_ab:\n", 1)[1].split("      ab_baseline_sha:", 1)[0]
    assert "type: boolean" in block and "default: false" in block
    for name, extra in (("pcc-ab-qualification", set()), ("pcc-compile-ab", {"pcc-ab-qualification"})):
        job = workflow_job(name)
        needs = re.search(r"^    needs: \[(.*?)\]$", job, re.M)
        assert needs and set(needs.group(1).split(", ")) == set(gate.ORIGINAL_JOBS) | extra
        assert "github.event_name == 'workflow_dispatch' && inputs.performance_ab" in job
        assert "continue-on-error" not in job
    assert "needs.pcc-ab-qualification.result == 'success'" in workflow_job("pcc-compile-ab")


def test_concurrency_routes_only_explicit_ab_to_the_existing_initial_push_group():
    text = WORKFLOW.read_text()
    expected = "  group: ${{ github.workflow }}-${{ github.event_name == 'workflow_dispatch' && inputs.performance_ab && 'push' || github.event_name }}-${{ (github.event_name == 'push' && github.run_attempt == 1 || github.event_name == 'workflow_dispatch' && inputs.performance_ab) && github.ref || format('{0}-{1}', github.run_id, github.run_attempt) }}"
    assert expected in text
    assert "  cancel-in-progress: false\n  queue: max" in text
    def old(event, attempt, run, ref):
        return f"PCC validation-{event}-" + (ref if event == "push" and attempt == 1 else f"{run}-{attempt}")
    def new(event, attempt, run, ref, opt_in):
        ab = event == "workflow_dispatch" and opt_in
        return "PCC validation-" + ("push" if ab else event) + "-" + (ref if (event == "push" and attempt == 1) or ab else f"{run}-{attempt}")
    for event in ("push", "pull_request", "workflow_dispatch"):
        for attempt in (1, 2):
            for selected in (False, True):
                actual = new(event, attempt, 100, "refs/heads/master", selected)
                if event == "workflow_dispatch" and selected:
                    assert actual == old("push", 1, 999, "refs/heads/master")
                else:
                    assert actual == old(event, attempt, 100, "refs/heads/master")


def test_limits_are_not_widened_and_partial_evidence_is_retained():
    job = workflow_job("pcc-compile-ab")
    assert "timeout-minutes: 200" in job and "runs-on: macos-15" in job
    assert "int(time.time()) + 11700" in job
    assert job.index("Fix the campaign deadline") < job.index("actions/checkout@v5")
    assert "--job-deadline-epoch \"$PCC_CI_AB_DEADLINE_EPOCH\"" in job
    assert "--campaign-request" in job and "run_pcc_stage_ab.py" not in job
    assert "actions: read" in job and "contents: read" in job and "write" not in re.search(r"    permissions:\n(.*?)    env:", job, re.S).group(1)
    assert "Record the job outcome even when qualification or setup failed\n        if: always()" in job
    assert "Preserve all partial campaign receipts and failure evidence\n        if: always()" in job
    assert "stage2_le_stage1': 'UNAVAILABLE" in job
    original = workflow_job("pcc1-package-parity")
    assert "timeout-minutes: 200" in original
    assert 'PCC_BOOTSTRAP_STAGE_TIMEOUT: "7200"' in original
    assert 'PCC_BOOTSTRAP_AUTO_TREE_RSS_CEILING_BYTES: "4294967296"' in original
    assert 'PCC_BOOTSTRAP_MIN_TREE_RSS_BYTES: "2147483648"' in original
    assert 'PCC_BOOTSTRAP_HOST_MEMORY_RESERVE_BYTES: "536870912"' in original
    assert "--partition bootstrap --in-place" in original
    assert "--partition remainder --owner-report" in workflow_job("pytest-markers")


def arm():
    return dict(sha="a" * 40, run_id=42, run_attempt=2)


def run_row(candidate=False):
    return dict(id=42, run_attempt=2, head_sha="a" * 40, path=gate.WORKFLOW,
                repository=dict(full_name=gate.REPOSITORY), head_repository=dict(full_name=gate.REPOSITORY),
                head_branch="master", event="workflow_dispatch" if candidate else "push",
                status="in_progress" if candidate else "completed", conclusion=None if candidate else "success")


def test_current_candidate_can_be_active_but_baseline_must_be_complete():
    gate.validate_run(run_row(True), arm(), candidate=True)
    gate.validate_run(run_row(), arm(), candidate=False)
    with pytest.raises(ValueError):
        gate.validate_run(run_row(True), arm(), candidate=False)


@pytest.mark.parametrize("key,value", [
    ("head_sha", "b" * 40), ("run_attempt", 1), ("path", ".github/workflows/workflow.yml"),
    ("repository", {"full_name": "foreign/pcc"}), ("head_repository", {"full_name": "foreign/pcc"}),
    ("event", "pull_request"), ("head_branch", "other"), ("status", "queued"), ("conclusion", "failure"),
])
def test_provider_run_mismatch_never_qualifies(key, value):
    row = run_row()
    row[key] = value
    with pytest.raises(ValueError):
        gate.validate_run(row, arm(), candidate=False)


def test_original_jobs_require_unique_successful_exact_attempt_rows():
    row = dict(id=1, name="owner", run_id=42, run_attempt=2, head_sha="a" * 40,
               status="completed", conclusion="success")
    assert gate.validate_jobs([row], {"owner"}, arm()) == [1]
    for rows in ([], [row, row], [dict(row, conclusion="skipped")], [dict(row, run_attempt=1)]):
        with pytest.raises(ValueError):
            gate.validate_jobs(rows, {"owner"}, arm())


class Marker:
    def __init__(self):
        self.rows = {}
    def name(self, name):
        assert name in self.rows
        return name
    def bytes(self, name):
        return self.rows[name]
    def json(self, name):
        return json.loads(self.bytes(name))
    def put(self, name, value):
        self.rows[name] = json.dumps(value).encode()


def marker_fixture():
    artifact = Marker()
    selection = sorted(gate.BOOTSTRAP_NODES)
    inventory = dict(marker="integration", partition="bootstrap", expected=selection,
                     selected=selection, gated_out=[], unexpected=[])
    for i in range(6):
        artifact.put(f"pytest/inventory-gw{i}.json", inventory)
    artifact.put("pytest/configuration.json", dict(marker="integration", partition="bootstrap", workers=6, dist="loadgroup"))
    events = [dict(event="start", markexpr="integration")]
    events.extend(dict(event="report", nodeid=node, when=phase, outcome="passed")
                  for node in selection for phase in ("setup", "call", "teardown"))
    events.append(dict(event="finish", exitstatus=0))
    artifact.rows["pytest/live.jsonl"] = b"\n".join(json.dumps(row).encode() for row in events)
    artifact.put("pytest/result.json", dict(status="COMPLETE", returncode=0, timeout_s=1800,
                 max_tree_rss_bytes=3 * 1024**3, peak_tree_rss_bytes=1024, post_exit_cleanup_pids=[],
                 darwin_preflight_reserve_bytes=536870912,
                 memory_budget_selection=dict(selection_kind="automatic", default_ceiling_bytes=4294967296,
                 minimum_tree_rss_bytes=2147483648, configured_host_memory_reserve_bytes=536870912,
                 max_tree_rss_bytes=3 * 1024**3)))
    source = dict(commit="a" * 40, clean_commit=True, source_sha256="d" * 64)
    artifact.put("receipt.json", dict(status="PASS", platform="macos-arm64", marker="integration", partition="bootstrap",
                 producer=gate.lease(arm()), source=source, bundle=dict(source=source, producer=gate.lease(arm())), timeout_s=1800,
                 guard_result_sha256=hashlib.sha256(artifact.bytes("pytest/result.json")).hexdigest()))
    return artifact


def validate_marker(artifact):
    return gate.validate_marker(artifact, arm(), "macos-arm64", "integration", "bootstrap", "d" * 64)


def test_actual_original_five_gc_node_phase_proof_is_required():
    artifact = marker_fixture()
    receipt, inventory = validate_marker(artifact)
    assert receipt["status"] == "PASS" and len(inventory["selected"]) == 5
    events = artifact.bytes("pytest/live.jsonl").splitlines()
    artifact.rows["pytest/live.jsonl"] = b"\n".join(events[:-2] + events[-1:])
    with pytest.raises(ValueError, match="actually executed"):
        validate_marker(artifact)


@pytest.mark.parametrize("mutation", ["skip", "xfail", "duplicate", "narrow", "missing_gc", "stale_source", "timeout", "rss", "reset_floor", "cleanup"])
def test_forged_pass_cannot_hide_incomplete_or_weakened_original_owner(mutation):
    artifact = marker_fixture()
    if mutation in ("skip", "xfail", "duplicate", "narrow"):
        events = [json.loads(line) for line in artifact.bytes("pytest/live.jsonl").splitlines()]
        if mutation == "skip":
            events[1]["outcome"] = "skipped"
        elif mutation == "xfail":
            events[1]["wasxfail"] = "claimed expected failure"
        elif mutation == "duplicate":
            events.insert(2, events[1])
        else:
            events[0]["keyword"] = "only_this"
        artifact.rows["pytest/live.jsonl"] = b"\n".join(json.dumps(row).encode() for row in events)
    elif mutation == "missing_gc":
        for i in range(6):
            value = artifact.json(f"pytest/inventory-gw{i}.json")
            value["selected"] = value["selected"][:-1]
            artifact.put(f"pytest/inventory-gw{i}.json", value)
    elif mutation == "stale_source":
        value = artifact.json("receipt.json")
        value["source"]["commit"] = "b" * 40
        artifact.put("receipt.json", value)
    else:
        value = artifact.json("pytest/result.json")
        if mutation == "timeout":
            value["timeout_s"] = 7200
        elif mutation == "rss":
            value["max_tree_rss_bytes"] = 8 * 1024**3
        elif mutation == "reset_floor":
            value["memory_budget_selection"]["minimum_tree_rss_bytes"] = 1
        else:
            value["post_exit_cleanup_pids"] = [123]
        artifact.put("pytest/result.json", value)
        receipt = artifact.json("receipt.json")
        receipt["guard_result_sha256"] = hashlib.sha256(artifact.bytes("pytest/result.json")).hexdigest()
        artifact.put("receipt.json", receipt)
    with pytest.raises(ValueError):
        validate_marker(artifact)


def test_whole_source_plan_cannot_be_a_scope_label_only():
    plan = dict(legacy=True, full_chain=False, domains=sorted(gate.DOMAINS),
                matrix=dict(include=[dict(domain=d, lane=l) for d in sorted(gate.DOMAINS) for l in sorted(gate.LANES)]),
                matrix_platform=dict(include=[]))
    assert "Complete pytest / macos-arm64 / integration" in gate.expected_jobs(plan)
    plan["matrix"]["include"].pop()
    with pytest.raises(ValueError, match="incomplete"):
        gate.expected_jobs(plan)


@pytest.mark.parametrize("name", ["../escape.json", "/absolute.json", "a/../bad.json", "a\\b.json"])
def test_artifact_traversal_is_rejected_without_extraction(tmp_path, name):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, "{}")
    with pytest.raises(ValueError):
        gate.Archive(path)


def test_provider_digest_and_fresh_attempt_are_not_optional(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", gate.REPOSITORY)
    monkeypatch.setenv("GH_TOKEN", "test-token-never-transmitted")
    api = gate.GitHub(tmp_path / "cache")
    with pytest.raises(ValueError, match="digest unavailable"):
        api.artifact(dict(id=1, expired=False, size_in_bytes=1))
    with pytest.raises(ValueError, match="expired"):
        api.artifact(dict(id=1, digest="sha256:" + "f" * 64, expired=True, size_in_bytes=1))
    source = (ROOT / "scripts/ci_pcc_ab_gate.py").read_text()
    assert 'created >= started' in source
    assert 'workflow_run.get("id") == arm["run_id"]' in source
    assert 'workflow_run.get("head_sha") == arm["sha"]' in source
    assert "urllib.request.Request(location), timeout=min(20, remaining())" in source
    assert 'urllib.request.build_opener(NoRedirect)' in source


def test_offline_recheck_cannot_reset_deadline_or_ignore_file_drift(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_CI_AB_DEADLINE_EPOCH", "1000")
    monkeypatch.setattr(gate.time, "time", lambda: 100)
    monkeypatch.setattr(gate.time, "monotonic", lambda: 50)
    gate.set_deadline(200)
    gate.set_deadline(300)
    assert gate.remaining() == 150
    path = tmp_path / "receipt.json"
    path.write_text("first")
    proof = dict(schema="pcc.ci-ab-frozen-proof.v1", files={str(path): gate.digest(path)}, trees={})
    assert gate.verify_frozen_campaign(proof) is None
    path.write_text("changed")
    with pytest.raises(ValueError, match="bytes changed"):
        gate.verify_frozen_campaign(proof)


def test_request_route_prepares_only_inside_guarded_entry_and_declares_stage_gap():
    source = (ROOT / "scripts/ci_pcc_ab_gate.py").read_text()
    tree = ast.parse(source)
    prepare = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "prepare_campaign")
    assert [arg.arg for arg in prepare.args.args] == ["request_path", "output_dir", "deadline_monotonic"]
    text = ast.get_source_segment(source, prepare)
    assert 'spec_request = decode(request_path.read_bytes())' in text
    assert 'output = Path(output_dir) / "prepared"' in text
    assert 'set_deadline(deadline_monotonic)' in text
    assert text.index('verify_arm(api, descriptor["candidate"]') < text.index('runtime_files(archive')
    assert '"PREPARED_NOT_MEASURED"' in text
    assert 'row["expected_output"].endswith("\\n")' in text
    assert 'stage2_le_stage1="UNAVAILABLE' in text
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    assert 'prepare_campaign(' not in ast.get_source_segment(source, main)


def test_original_source_lane_resource_defaults_remain_owned_by_existing_runner():
    source = (ROOT / "scripts/ci_validation_run.py").read_text()
    assert "1073741824" in source or "1024**3" in source or "1024 ** 3" in source
    assert "90" in source
    markers = (ROOT / "scripts/ci_pytest_markers.py").read_text()
    assert 'TIMEOUTS = {"not integration": 2400, "integration": 1800}' in markers
    assert 'len(inventories) == 6' in markers


def test_failure_before_request_creation_retains_error_metadata(tmp_path, monkeypatch):
    env = environment()
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    event = tmp_path / "event.json"
    event.write_text(json.dumps(dict(inputs=inputs())))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("PCC_AB_NEEDS", json.dumps({name: dict(result="success") for name in gate.ORIGINAL_JOBS[:-1]}))
    output = tmp_path / "request.json"
    with pytest.raises(ValueError, match="dependency failed or skipped"):
        gate.main(["--write-request", str(output)])
    assert not output.exists()
    failure = json.loads(output.with_suffix(".failure.json").read_text())
    assert failure["status"] == "FAIL"
    assert "dependency failed or skipped" in failure["error"]


def test_missing_original_receipts_cannot_be_replaced_by_caller_pass(tmp_path, monkeypatch):
    env = dict(environment(), GITHUB_REPOSITORY=gate.REPOSITORY,
               GITHUB_WORKFLOW_REF=gate.REPOSITORY + "/" + gate.WORKFLOW + "@refs/heads/master")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(gate, "set_deadline", lambda value: None)
    fake = tmp_path / "claimed-pass.json"
    fake.write_text(json.dumps(dict(status="PASS", five_gc=True, fixed_point=True)))
    spec = dict(target_sha="c" * 40, qualification=dict(path=str(fake), sha256=gate.digest(fake)))
    with pytest.raises(ValueError, match="qualification selectors"):
        gate.verify_campaign(spec, baseline_files={}, candidate_files={})


def test_artifact_redirect_does_not_receive_github_token(tmp_path, monkeypatch):
    import io
    monkeypatch.setenv("GITHUB_REPOSITORY", gate.REPOSITORY)
    monkeypatch.setenv("GH_TOKEN", "test-secret")
    monkeypatch.setattr(gate, "remaining", lambda: 10)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("receipt.json", "{}")
    data = payload.getvalue()
    requested = []
    class FakeOpener:
        def open(self, request, timeout):
            requested.append(request)
            if len(requested) == 1:
                raise gate.urllib.error.HTTPError(request.full_url, 302, "redirect",
                    {"Location": "https://test.blob.core.windows.net/artifact?temporary=opaque"}, None)
            return io.BytesIO(data)
    monkeypatch.setattr(gate.urllib.request, "build_opener", lambda *args: FakeOpener())
    archive = gate.GitHub(tmp_path / "cache").artifact(dict(id=7, expired=False, size_in_bytes=len(data),
                                                         digest="sha256:" + hashlib.sha256(data).hexdigest()))
    assert archive.json("receipt.json") == {}
    assert requested[0].get_header("Authorization") == "Bearer test-secret"
    assert requested[1].get_header("Authorization") is None
    assert requested[0].full_url == "https://api.github.com/repos/ikshengmin/pcc/actions/artifacts/7/zip"


def test_artifact_redirect_to_unrelated_host_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", gate.REPOSITORY)
    monkeypatch.setenv("GH_TOKEN", "test-secret")
    monkeypatch.setattr(gate, "remaining", lambda: 10)
    calls = []
    class FakeOpener:
        def open(self, request, timeout):
            calls.append(request)
            raise gate.urllib.error.HTTPError(request.full_url, 302, "redirect",
                                              {"Location": "https://attacker.example/artifact"}, None)
    monkeypatch.setattr(gate.urllib.request, "build_opener", lambda *args: FakeOpener())
    with pytest.raises(ValueError, match="not GitHub artifact storage"):
        gate.GitHub(tmp_path / "cache").artifact(dict(id=7, expired=False, size_in_bytes=1,
                                                    digest="sha256:" + "f" * 64))
    assert len(calls) == 1
