"""Source/receipt contracts only. No compiler, subprocess or network is allowed."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
import re
import tomllib
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
    monkeypatch.setattr(gate, "_LIVE_PROOFS", {})


def inputs():
    return dict(performance_ab="true", expected_sha="c" * 40, ab_baseline_sha="b" * 40,
                ab_baseline_run="123:2", ab_input_manifest="tests/data/ab.json",
                full_chain="false", pytest_selection="both")


def environment():
    return dict(GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF="refs/heads/master",
                GITHUB_SHA="c" * 40, GITHUB_RUN_ID="456", GITHUB_RUN_ATTEMPT="3",
                GITHUB_REPOSITORY=gate.REPOSITORY, GITHUB_WORKFLOW_SHA="c" * 40,
                GITHUB_WORKFLOW_REF=gate.REPOSITORY + "/" + gate.WORKFLOW + "@refs/heads/master")


def event(data=None, env=None):
    return dict(inputs=inputs() if data is None else data, ref=(env or environment())["GITHUB_REF"])


def candidate_selection():
    ref = "refs/heads/ci-ab/scanner/b" + "b" * 40 + "-r123-a2/c" + "c" * 40
    env = dict(environment(), GITHUB_REF=ref, GITHUB_WORKFLOW_REF=gate.REPOSITORY + "/" + gate.WORKFLOW + "@" + ref)
    data = dict(inputs(), scope="all", base_sha="b" * 40, ab_input_manifest=gate.SCANNER_CORPUS)
    return env, event(data, env)


def install_selection(tmp_path, monkeypatch, env=None, data=None):
    env = env or environment()
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    path = tmp_path / "event.json"
    path.write_text(json.dumps(data or event(env=env)))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    return path


def provider_context(ref=gate.MASTER_REF):
    return dict(candidate_ref=ref, workflow=dict(id=99))


def test_explicit_sources_are_not_the_coverage_base():
    data = inputs()
    data["base_sha"] = "d" * 40
    result = gate.selectors(environment(), event(data))
    assert result["baseline"] == dict(sha="b" * 40, run_id=123, run_attempt=2)
    assert result["candidate"] == dict(sha="c" * 40, run_id=456, run_attempt=3)


@pytest.mark.parametrize("key,value", [
    ("performance_ab", "false"), ("performance_ab", False), ("performance_ab", None),
    ("performance_ab", 1), ("performance_ab", 1.0), ("full_chain", 0), ("full_chain", 0.0),
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
        gate.selectors(environment(), event(data))


@pytest.mark.parametrize("event_name", ["push", "pull_request", "workflow_run", "schedule"])
def test_no_performance_on_ordinary_events_even_with_selector(event_name):
    env = dict(environment(), GITHUB_EVENT_NAME=event_name)
    with pytest.raises(ValueError, match="opt-in"):
        gate.selectors(env, event(env=env))


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
    expected = "  group: ${{ github.workflow }}-${{ github.event_name == 'workflow_dispatch' && inputs.performance_ab && 'push' || github.event_name }}-${{ github.event_name == 'workflow_dispatch' && inputs.performance_ab && 'refs/heads/master' || github.event_name == 'push' && github.run_attempt == 1 && github.ref || format('{0}-{1}', github.run_id, github.run_attempt) }}"
    assert expected in text
    assert "  cancel-in-progress: false\n  queue: max" in text
    def old(event, attempt, run, ref):
        return f"PCC validation-{event}-" + (ref if event == "push" and attempt == 1 else f"{run}-{attempt}")
    def new(event, attempt, run, ref, opt_in):
        ab = event == "workflow_dispatch" and opt_in
        return "PCC validation-" + ("push" if ab else event) + "-" + (gate.MASTER_REF if ab else ref if event == "push" and attempt == 1 else f"{run}-{attempt}")
    for event_name in ("push", "pull_request", "workflow_dispatch", "schedule"):
        for attempt in (1, 2, 3):
            for selected in (False, True):
                for ref in (gate.MASTER_REF, candidate_selection()[0]["GITHUB_REF"], "refs/heads/topic", "refs/tags/tag"):
                    actual = new(event_name, attempt, 100, ref, selected)
                    if event_name == "workflow_dispatch" and selected:
                        assert actual == old("push", 1, 999, gate.MASTER_REF)
                    else:
                        assert actual == old(event_name, attempt, 100, ref)


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
    assert "stage2_le_stage1': 'UNPROVEN" in job
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
    return dict(id=42, run_attempt=2, workflow_id=99, head_sha="a" * 40, path=gate.WORKFLOW,
                repository=dict(full_name=gate.REPOSITORY), head_repository=dict(full_name=gate.REPOSITORY),
                head_branch="master", event="workflow_dispatch" if candidate else "push",
                status="in_progress" if candidate else "completed", conclusion=None if candidate else "success")


def test_current_candidate_can_be_active_but_baseline_must_be_complete():
    gate.validate_run(run_row(True), arm(), candidate=True, context=provider_context())
    gate.validate_run(run_row(), arm(), candidate=False, context=provider_context())
    with pytest.raises(ValueError):
        gate.validate_run(run_row(True), arm(), candidate=False, context=provider_context())


@pytest.mark.parametrize("key,value", [
    ("id", 43), ("head_sha", "b" * 40), ("run_attempt", 1), ("workflow_id", 100), ("path", ".github/workflows/workflow.yml"),
    ("repository", {"full_name": "foreign/pcc"}), ("head_repository", {"full_name": "foreign/pcc"}),
    ("event", "pull_request"), ("head_branch", "other"), ("status", "queued"), ("conclusion", "failure"),
])
def test_provider_run_mismatch_never_qualifies(key, value):
    row = run_row()
    row[key] = value
    with pytest.raises(ValueError):
        gate.validate_run(row, arm(), candidate=False, context=provider_context())


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
    install_selection(tmp_path, monkeypatch)
    proof = registered_proof(tmp_path, monkeypatch, files={str(path): gate.digest(path)})
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
    corpus = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "corpus_inputs")
    assert 'row["expected_output"].endswith("\\n")' in ast.get_source_segment(source, corpus)
    assert 'selected["input_manifest"]' in text
    assert 'stage2_le_stage1="UNRUN' in text
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
    event.write_text(json.dumps(dict(inputs=inputs(), ref=gate.MASTER_REF)))
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


def test_candidate_dispatch_uses_one_adapter_and_all_nine_existing_inputs():
    env, data = candidate_selection()
    selected = gate.selected_dispatch(env, data)
    assert selected["qualification"] == gate.selectors(env, data)
    assert selected["source_selection"]["route"] == "scanner-candidate"
    assert selected["source_selection"]["candidate_ref"] == env["GITHUB_REF"]
    assert selected["input_manifest"] == gate.SCANNER_CORPUS
    source = (ROOT / "scripts/ci_pcc_ab_gate.py").read_text()
    tree = ast.parse(source)
    for name in ("write_request", "prepare_campaign", "verify_campaign", "finish_campaign"):
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
        body = ast.get_source_segment(source, node)
        assert "current_selection()" in body
        assert 'event["inputs"]' not in body
    workflow = WORKFLOW.read_text()
    assert "    branches: [master]" in workflow
    assert "ci-ab/scanner/**" not in workflow
    header = workflow.split("  push:", 1)[0]
    assert re.findall(r"^      ([a-z_]+):$", header, re.M) == [
        "scope", "expected_sha", "base_sha", "full_chain", "pytest_selection", "performance_ab",
        "ab_baseline_sha", "ab_baseline_run", "ab_input_manifest"]


@pytest.mark.parametrize("field", ["scope", "expected_sha", "base_sha", "full_chain", "pytest_selection",
                                    "performance_ab", "ab_baseline_sha", "ab_baseline_run", "ab_input_manifest"])
def test_candidate_dispatch_missing_any_selector_is_rejected(field):
    env, data = candidate_selection()
    del data["inputs"][field]
    with pytest.raises(ValueError):
        gate.selected_dispatch(env, data)


@pytest.mark.parametrize("key,value", [
    ("scope", "frontends"), ("base_sha", "d" * 40), ("expected_sha", "d" * 40),
    ("ab_baseline_sha", "d" * 40), ("ab_baseline_run", "124:2"), ("ab_baseline_run", "123:3"),
    ("ab_baseline_run", "0123:2"), ("ab_baseline_run", "1" * 21 + ":2"),
    ("ab_input_manifest", "tests/data/ab.json"), ("full_chain", "true"),
    ("pytest_selection", "integration"), ("performance_ab", "false"), ("extra", "unexpected"),
    ("performance_ab", 1), ("performance_ab", 1.0), ("full_chain", 0), ("full_chain", 0.0),
])
def test_candidate_namespace_and_explicit_selectors_cannot_disagree(key, value):
    env, data = candidate_selection()
    data["inputs"][key] = value
    with pytest.raises(ValueError):
        gate.selected_dispatch(env, data)


@pytest.mark.parametrize("key,value", [
    ("GITHUB_EVENT_NAME", "push"), ("GITHUB_REF", "refs/heads/topic"),
    ("GITHUB_REF", "refs/tags/ci-ab/scanner"), ("GITHUB_REF", "refs/heads/ci-ab/scanner/arbitrary"),
    ("GITHUB_SHA", "d" * 40), ("GITHUB_RUN_ID", "0"), ("GITHUB_RUN_ATTEMPT", "03"),
    ("GITHUB_RUN_ATTEMPT", "1" * 21), ("GITHUB_REPOSITORY", "foreign/pcc"),
    ("GITHUB_WORKFLOW_SHA", "b" * 40), ("GITHUB_WORKFLOW_REF", "foreign/workflow@refs/heads/master"),
])
def test_candidate_actual_event_ref_workflow_run_and_attempt_are_bound(key, value):
    env, data = candidate_selection()
    env[key] = value
    with pytest.raises(ValueError):
        gate.selected_dispatch(env, data)


@pytest.mark.parametrize("replacement", ["refs/heads/master", "refs/heads/topic", "", None])
def test_payload_ref_must_equal_actual_candidate_ref(replacement):
    env, data = candidate_selection()
    data["ref"] = replacement
    with pytest.raises(ValueError, match="event/ref"):
        gate.selected_dispatch(env, data)


def test_candidate_provider_must_match_branch_and_baseline_remains_master_only():
    ref = candidate_selection()[0]["GITHUB_REF"]
    context = provider_context(ref)
    row = dict(run_row(True), head_branch=ref[len("refs/heads/"):])
    gate.validate_run(row, arm(), candidate=True, context=context)
    for key, value in (("head_branch", "master"), ("id", 43), ("run_attempt", 3),
                       ("head_sha", "b" * 40), ("workflow_id", 100), ("path", "wrong"),
                       ("event", "push"), ("status", "completed"), ("conclusion", "success")):
        with pytest.raises(ValueError):
            gate.validate_run(dict(row, **{key: value}), arm(), candidate=True, context=context)
    with pytest.raises(ValueError):
        gate.validate_run(dict(row, status="completed", conclusion="success"), arm(), candidate=False, context=context)
    gate.validate_run(run_row(), arm(), candidate=False, context=context)


def source_pair():
    baseline = {gate.SOURCE: dict(sha256="a" * 64, size_bytes=10, executable=False),
                gate.WORKFLOW: dict(sha256="w" * 64, size_bytes=20, executable=False),
                gate.SCANNER_CORPUS: dict(sha256="f" * 64, size_bytes=30, executable=False)}
    candidate = copy.deepcopy(baseline)
    candidate[gate.SOURCE]["sha256"] = "b" * 64
    return baseline, candidate


class ContextAPI:
    def __init__(self, selection):
        self.selection = selection
        data = selection["qualification"]
        self.rows = {
            "/git/commits/" + data["candidate"]["sha"]: dict(sha=data["candidate"]["sha"],
                tree=dict(sha="e" * 40), parents=[dict(sha=data["baseline"]["sha"])]),
            "/actions/workflows/" + gate.WORKFLOW.rsplit("/", 1)[1]: dict(id=99, path=gate.WORKFLOW, state="active"),
            "/git/ref/" + selection["source_selection"]["candidate_ref"][len("refs/"):]: dict(
                ref=selection["source_selection"]["candidate_ref"], object=dict(type="commit", sha=data["candidate"]["sha"])),
            "/git/ref/heads/master": dict(ref=gate.MASTER_REF, object=dict(type="commit",
                sha=data["baseline"]["sha"] if selection["source_selection"]["route"] == "scanner-candidate" else data["candidate"]["sha"])),
        }
        arm = data["candidate"]
        self.rows[f"/actions/runs/{arm['run_id']}/attempts/{arm['run_attempt']}"] = dict(
            id=arm["run_id"], run_attempt=arm["run_attempt"], head_sha=arm["sha"], workflow_id=99,
            path=gate.WORKFLOW, repository=dict(full_name=gate.REPOSITORY), head_repository=dict(full_name=gate.REPOSITORY),
            head_branch=selection["source_selection"]["candidate_ref"][len("refs/heads/"):],
            event="workflow_dispatch", status="in_progress", conclusion=None)
    def get(self, path):
        return copy.deepcopy(self.rows[path])


def context_fixture():
    env, data = candidate_selection()
    selected = gate.selected_dispatch(env, data)
    return selected, ContextAPI(selected), source_pair()


@pytest.mark.parametrize("mutation", ["extra_parent", "wrong_parent", "missing_parent", "wrong_commit", "wrong_tree",
    "moved_candidate", "deleted_candidate", "ref_is_tag", "wrong_workflow_id", "wrong_workflow_path", "inactive_workflow"])
def test_provider_candidate_context_fails_closed(mutation):
    selected, api, files = context_fixture()
    commit = api.rows["/git/commits/" + "c" * 40]
    ref = api.rows["/git/ref/" + selected["source_selection"]["candidate_ref"][len("refs/"):]]
    workflow = api.rows["/actions/workflows/" + gate.WORKFLOW.rsplit("/", 1)[1]]
    if mutation == "extra_parent":
        commit["parents"].append(dict(sha="d" * 40))
    elif mutation == "wrong_parent":
        commit["parents"][0]["sha"] = "d" * 40
    elif mutation == "missing_parent":
        commit["parents"] = []
    elif mutation == "wrong_commit":
        commit["sha"] = "d" * 40
    elif mutation == "wrong_tree":
        commit["tree"]["sha"] = "short"
    elif mutation == "moved_candidate":
        ref["object"]["sha"] = "d" * 40
    elif mutation == "deleted_candidate":
        ref.clear()
    elif mutation == "ref_is_tag":
        ref["object"]["type"] = "tag"
    elif mutation == "wrong_workflow_id":
        workflow["id"] = 0
    elif mutation == "wrong_workflow_path":
        workflow["path"] = "wrong"
    elif mutation == "inactive_workflow":
        workflow["state"] = "disabled_manually"
    with pytest.raises(ValueError):
        gate.authenticate_context(api, selected, *files)


@pytest.mark.parametrize("mutation", ["same_tree", "scanner_mode", "extra_path", "missing_path", "workflow", "corpus", "other_mode"])
def test_one_scanner_delta_includes_modes_and_all_common_files(mutation):
    baseline, candidate = source_pair()
    if mutation == "same_tree":
        candidate = copy.deepcopy(baseline)
    elif mutation == "scanner_mode":
        candidate[gate.SOURCE]["executable"] = True
    elif mutation == "extra_path":
        candidate["extra.py"] = dict(sha256="d" * 64, size_bytes=1, executable=False)
    elif mutation == "missing_path":
        del candidate[gate.SCANNER_CORPUS]
    elif mutation == "workflow":
        candidate[gate.WORKFLOW]["sha256"] = "d" * 64
    elif mutation == "corpus":
        candidate[gate.SCANNER_CORPUS]["sha256"] = "d" * 64
    else:
        candidate[gate.WORKFLOW]["executable"] = True
    with pytest.raises(ValueError, match="same-mode"):
        gate.validate_source_pair(baseline, candidate)


def registered_proof(tmp_path, monkeypatch, files=None, context=None):
    # Unit fixture models the return of live verification; public JSON callers
    # cannot write this private same-process registration.
    selected = gate.current_selection()
    context = context or dict(qualification=selected["qualification"])
    proof = dict(schema="pcc.ci-ab-frozen-proof.v1", admission=dict(source_context=context),
                 files=files or {}, trees={})
    gate._LIVE_PROOFS[id(proof)] = dict(proof=proof, proof_sha256=gate.tree_digest(proof),
                                      selection=selected, cache=str(tmp_path / "cache"))
    return proof


def test_serialized_pass_cannot_become_same_process_proof(tmp_path, monkeypatch):
    install_selection(tmp_path, monkeypatch)
    monkeypatch.setattr(gate, "remaining", lambda: 10)
    proof = registered_proof(tmp_path, monkeypatch)
    assert gate.verify_frozen_campaign(proof) is None
    with pytest.raises(ValueError, match="same-process"):
        gate.verify_frozen_campaign(copy.deepcopy(proof))


@pytest.mark.parametrize("mutation", ["source_context", "drop_file", "rewrite_file", "drop_tree", "rewrite_tree"])
def test_frozen_proof_closure_and_context_cannot_be_rewritten(tmp_path, monkeypatch, mutation):
    install_selection(tmp_path, monkeypatch)
    monkeypatch.setattr(gate, "remaining", lambda: 10)
    proof = registered_proof(tmp_path, monkeypatch, files={"file": "d" * 64})
    proof["trees"] = {"tree": "e" * 64}
    gate._LIVE_PROOFS[id(proof)]["proof_sha256"] = gate.tree_digest(proof)
    if mutation == "source_context":
        proof["admission"]["source_context"]["status"] = "PASS"
    elif mutation == "drop_file":
        proof["files"].clear()
    elif mutation == "rewrite_file":
        proof["files"]["file"] = "f" * 64
    elif mutation == "drop_tree":
        proof["trees"].clear()
    else:
        proof["trees"]["tree"] = "f" * 64
    with pytest.raises(ValueError, match="same-process"):
        gate.verify_frozen_campaign(proof)


def test_live_finish_preserves_exact_pair_but_never_transfers_to_advanced_master(tmp_path, monkeypatch):
    env, data = candidate_selection()
    install_selection(tmp_path, monkeypatch, env, data)
    selected, api, files = context_fixture()
    context = gate.authenticate_context(api, selected, *files)
    proof = registered_proof(tmp_path, monkeypatch, context=context)
    monkeypatch.setattr(gate, "remaining", lambda: 10)
    monkeypatch.setattr(gate, "GitHub", lambda cache: api)
    result = gate.finish_campaign(proof)
    assert result["final_identity_status"] == "EXACT_PAIR_REAUTHENTICATED"
    assert result["master_status"] == "UNCHANGED"
    assert result["final_master_ancestry"] is None
    add_advanced_master(api)
    result = gate.finish_campaign(proof)
    assert result["final_master_ancestry"]["baseline_sha"] == "b" * 40
    assert result["final_master_ancestry"]["observed_master_sha"] == "d" * 40
    assert result["observed_master_ancestry"] is None
    assert result["master_status"] == "STALE_FOR_PROMOTION"
    assert result["acceptance_scope"] == "HISTORICAL_B_C_PAIR_ONLY"
    assert result["qualification"] == selected["qualification"]
    assert result["promotion"] == "NOT_AUTHORIZED" and result["transfer_to_new_master"] is False
    assert result["master_observations"][-1] == dict(phase="completed", sha="d" * 40)


@pytest.mark.parametrize("mutation", ["ref", "run", "attempt", "workflow", "selectors", "workflow_sha"])
def test_finish_reauthenticates_candidate_not_just_caller_pass(tmp_path, monkeypatch, mutation):
    env, data = candidate_selection()
    path = install_selection(tmp_path, monkeypatch, env, data)
    selected, api, files = context_fixture()
    context = gate.authenticate_context(api, selected, *files)
    proof = registered_proof(tmp_path, monkeypatch, context=context)
    monkeypatch.setattr(gate, "remaining", lambda: 10)
    monkeypatch.setattr(gate, "GitHub", lambda cache: api)
    if mutation == "ref":
        api.rows["/git/ref/" + env["GITHUB_REF"][len("refs/"):]]["object"]["sha"] = "d" * 40
    elif mutation in ("run", "attempt"):
        api.rows["/actions/runs/456/attempts/3"]["id" if mutation == "run" else "run_attempt"] = 5
    elif mutation == "workflow":
        api.rows["/actions/workflows/" + gate.WORKFLOW.rsplit("/", 1)[1]]["id"] = 100
    elif mutation == "selectors":
        data["inputs"]["ab_baseline_run"] = "123:3"
        path.write_text(json.dumps(data))
    else:
        monkeypatch.setenv("GITHUB_WORKFLOW_SHA", "d" * 40)
    with pytest.raises(ValueError):
        gate.finish_campaign(proof)


def test_dispatch_payload_accepts_only_exact_short_or_full_selected_branch():
    env, data = candidate_selection()
    expected = gate.selected_dispatch(env, data)
    data["ref"] = env["GITHUB_REF"][len("refs/heads/"):]
    assert gate.selected_dispatch(env, data) == expected
    for bad in ("heads/" + data["ref"], "refs/tags/" + data["ref"], data["ref"] + "/extra"):
        with pytest.raises(ValueError, match="event/ref"):
            gate.selected_dispatch(env, dict(data, ref=bad))


def add_advanced_master(api, master="d" * 40):
    baseline = api.selection["qualification"]["baseline"]["sha"]
    path = "/compare/" + baseline + "..." + master
    comparison = dict(url="https://api.github.com/repos/" + gate.REPOSITORY + path,
                      base_commit=dict(sha=baseline), merge_base_commit=dict(sha=baseline),
                      status="ahead", ahead_by=3, behind_by=0, total_commits=3)
    api.rows[path + "?per_page=1"] = comparison
    api.rows["/git/ref/heads/master"]["object"]["sha"] = master
    return comparison


def test_advanced_master_admits_only_authenticated_historical_pair(tmp_path, monkeypatch):
    env, data = candidate_selection()
    install_selection(tmp_path, monkeypatch, env, data)
    selected, api, files = context_fixture()
    add_advanced_master(api)
    context = gate.authenticate_context(api, selected, *files)
    assert context["master_status"] == "STALE_FOR_PROMOTION"
    assert context["acceptance_scope"] == "HISTORICAL_B_C_PAIR_ONLY"
    assert context["observed_master_ancestry"]["baseline_sha"] == "b" * 40
    assert context["observed_master_ancestry"]["observed_master_sha"] == "d" * 40
    assert context["promotion"] == "NOT_AUTHORIZED" and context["transfer_to_new_master"] is False
    proof = registered_proof(tmp_path, monkeypatch, context=context)
    monkeypatch.setattr(gate, "remaining", lambda: 10)
    monkeypatch.setattr(gate, "GitHub", lambda cache: api)
    # The same immutable admission comparison can be reused without another
    # provider request, but a newly observed final descendant needs its own.
    del api.rows["/compare/" + "b" * 40 + "..." + "d" * 40 + "?per_page=1"]
    add_advanced_master(api, "f" * 40)
    for later in ("d" * 40, "f" * 40, "b" * 40):
        api.rows["/git/ref/heads/master"]["object"]["sha"] = later
        result = gate.finish_campaign(proof)
        assert result["observed_master_ancestry"] == context["observed_master_ancestry"]
        if later == "b" * 40:
            assert result["final_master_ancestry"] is None
        else:
            assert result["final_master_ancestry"]["baseline_sha"] == "b" * 40
            assert result["final_master_ancestry"]["observed_master_sha"] == later
            if later == "d" * 40:
                assert result["final_master_ancestry"] is context["observed_master_ancestry"]
        assert result["master_status"] == "STALE_FOR_PROMOTION"
        assert result["acceptance_scope"] == "HISTORICAL_B_C_PAIR_ONLY"
        assert result["qualification"] == selected["qualification"]
        assert result["master_observations"][-1]["sha"] == later
        assert result["promotion"] == "NOT_AUTHORIZED" and result["transfer_to_new_master"] is False


@pytest.mark.parametrize("key,value", [
    ("url", "https://api.github.com/repos/foreign/pcc/compare/wrong"),
    ("base_commit", {"sha": "e" * 40}), ("merge_base_commit", {"sha": "e" * 40}),
    ("status", "behind"), ("status", "diverged"), ("status", "identical"),
    ("behind_by", 1), ("behind_by", False), ("ahead_by", 0), ("ahead_by", True),
    ("total_commits", 0), ("total_commits", 2), ("total_commits", None),
])
def test_historical_admission_rejects_unbound_diverged_or_incomplete_ancestry(key, value):
    selected, api, files = context_fixture()
    comparison = add_advanced_master(api)
    comparison[key] = value
    with pytest.raises(ValueError, match="ancestry"):
        gate.authenticate_context(api, selected, *files)


@pytest.mark.parametrize("key,value", [
    ("url", "https://api.github.com/repos/foreign/pcc/compare/wrong"),
    ("base_commit", {"sha": "e" * 40}), ("merge_base_commit", {"sha": "e" * 40}),
    ("status", "behind"), ("status", "diverged"), ("status", "identical"),
    ("behind_by", 1), ("behind_by", False), ("ahead_by", 0), ("ahead_by", True),
    ("total_commits", 0), ("total_commits", 2), ("total_commits", None),
])
def test_finish_rejects_invalid_new_master_after_valid_historical_admission(tmp_path, monkeypatch, key, value):
    env, data = candidate_selection()
    install_selection(tmp_path, monkeypatch, env, data)
    selected, api, files = context_fixture()
    add_advanced_master(api)
    context = gate.authenticate_context(api, selected, *files)
    assert context["observed_master_ancestry"]["observed_master_sha"] == "d" * 40
    proof = registered_proof(tmp_path, monkeypatch, context=context)
    monkeypatch.setattr(gate, "remaining", lambda: 10)
    monkeypatch.setattr(gate, "GitHub", lambda cache: api)
    comparison = add_advanced_master(api, "f" * 40)
    comparison[key] = value
    with pytest.raises(ValueError, match="ancestry"):
        gate.finish_campaign(proof)
    # The completed observation was never admitted and did not rewrite proof.
    assert context["master_observations"] == [dict(phase="admission", sha="d" * 40)]
    assert gate.verify_frozen_campaign(proof) is None


def test_legacy_master_dispatch_remains_source_bound_and_cannot_follow_new_master():
    selected = gate.selected_dispatch(environment(), event())
    api = ContextAPI(selected)
    context = gate.authenticate_context(api, selected, *source_pair())
    assert context["candidate_ref"] == gate.MASTER_REF
    assert context["expected_master"] == "c" * 40
    assert context["observed_master_ancestry"] is None
    api.rows["/git/ref/heads/master"]["object"]["sha"] = "d" * 40
    with pytest.raises(ValueError, match="candidate ref moved"):
        gate.authenticate_context(api, selected, *source_pair())


def test_request_serializes_actual_context_and_cannot_change_selectors(tmp_path, monkeypatch):
    env, data = candidate_selection()
    install_selection(tmp_path, monkeypatch, env, data)
    monkeypatch.setenv("PCC_AB_NEEDS", json.dumps({name: dict(result="success") for name in gate.ORIGINAL_JOBS}))
    request = tmp_path / "request.json"
    gate.write_request(request)
    assert json.loads(request.read_text()) == dict(schema="pcc.ci-ab-request.v1", **gate.selected_dispatch(env, data))
    request.chmod(0o444)
    monkeypatch.setattr(gate, "set_deadline", lambda deadline: None)
    # Reusing a previously selected request for another attempt is rejected
    # before snapshot(), Git, network, build or output creation.
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "4")
    with pytest.raises(ValueError, match="request differs"):
        gate.prepare_campaign(request, tmp_path / "output", 10)
    assert not (tmp_path / "output").exists()


def test_live_verification_recomputes_the_selected_common_corpus(tmp_path, monkeypatch):
    env, data = candidate_selection()
    install_selection(tmp_path, monkeypatch, env, data)
    selected = gate.selected_dispatch(env, data)
    descriptor = tmp_path / "selectors.json"
    descriptor.write_text(json.dumps(selected["qualification"]))
    baseline, candidate = source_pair()
    spec = dict(target_sha="c" * 40, allowed_changed_source=gate.SOURCE,
                qualification=dict(path=str(descriptor), sha256=gate.digest(descriptor)),
                baseline_source=dict(path="baseline", sha256=gate.tree_digest(baseline)),
                candidate_source=dict(path="candidate", sha256=gate.tree_digest(candidate)),
                warmup=dict(path="unselected.py", sha256="f" * 64), inputs=[])
    monkeypatch.setattr(gate, "set_deadline", lambda deadline: None)
    monkeypatch.setattr(gate, "tree_files", lambda path: baseline if path == "baseline" else candidate)
    seen = []
    def corpus(source, files, manifest):
        seen.append((source, files, manifest))
        return dict(path="selected.py", sha256="a" * 64), []
    monkeypatch.setattr(gate, "corpus_inputs", corpus)
    with pytest.raises(ValueError, match="tracked corpus"):
        gate.verify_campaign(spec, baseline_files=baseline, candidate_files=candidate)
    assert seen == [("candidate", candidate, gate.SCANNER_CORPUS)]


@pytest.mark.parametrize("mutation", ["none", "missing", "extra", "mode", "blob", "size", "symlink", "truncated", "tree_identity"])
def test_full_provider_tree_authentication_includes_paths_bytes_and_modes(tmp_path, mutation):
    source = tmp_path / "source"
    source.mkdir()
    file = source / "a.py"
    file.write_bytes(b"a = 1\n")
    file.chmod(0o444)
    data = file.read_bytes()
    files = {"a.py": dict(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), executable=False)}
    row = dict(path="a.py", type="blob", mode="100644", size=len(data),
               sha=hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest())
    tree = dict(sha="e" * 40, truncated=False, tree=[row])
    if mutation == "missing":
        tree["tree"] = []
    elif mutation == "extra":
        tree["tree"].append(dict(row, path="extra.py"))
    elif mutation == "mode":
        row["mode"] = "100755"
    elif mutation == "blob":
        row["sha"] = "f" * 40
    elif mutation == "size":
        row["size"] += 1
    elif mutation == "symlink":
        row["mode"] = "120000"
    elif mutation == "truncated":
        tree["truncated"] = True
    elif mutation == "tree_identity":
        tree["sha"] = "f" * 40
    class API:
        def get(self, path):
            if path == "/git/commits/" + "c" * 40:
                return dict(sha="c" * 40, tree=dict(sha="e" * 40))
            assert path == "/git/trees/" + "e" * 40 + "?recursive=1"
            return tree
    if mutation == "none":
        assert gate.bind_tree(API(), "c" * 40, files, source) == "e" * 40
    else:
        with pytest.raises(ValueError):
            gate.bind_tree(API(), "c" * 40, files, source)


def test_legacy_finish_rejects_master_moving_between_final_identity_reads(tmp_path, monkeypatch):
    install_selection(tmp_path, monkeypatch)
    selected = gate.selected_dispatch(environment(), event())
    api = ContextAPI(selected)
    context = gate.authenticate_context(api, selected, *source_pair())
    proof = registered_proof(tmp_path, monkeypatch, context=context)
    monkeypatch.setattr(gate, "remaining", lambda: 10)
    monkeypatch.setattr(gate, "GitHub", lambda cache: api)
    original_get = api.get
    observed = []
    values = iter(("c" * 40, "d" * 40))
    def changing_ref(path):
        if path == "/git/ref/heads/master":
            value = next(values)
            observed.append(value)
            return dict(ref=gate.MASTER_REF, object=dict(type="commit", sha=value))
        return original_get(path)
    monkeypatch.setattr(api, "get", changing_ref)
    with pytest.raises(ValueError, match="master candidate ref moved"):
        gate.finish_campaign(proof)
    assert observed == ["c" * 40, "d" * 40]


@pytest.mark.parametrize("opt_in,full_chain", [(True, False), ("true", "false")])
def test_dispatch_boolean_inputs_accept_only_bool_or_canonical_string_forms(opt_in, full_chain):
    env, data = candidate_selection()
    expected = gate.selected_dispatch(env, data)
    data["inputs"].update(performance_ab=opt_in, full_chain=full_chain)
    assert gate.selected_dispatch(env, data) == expected


def require_resolved_campaign_dependencies(workflow):
    job = re.split(r"\n  [A-Za-z0-9_-]+:\n", workflow.split("\n  pcc-compile-ab:\n", 1)[1], maxsplit=1)[0]
    name = "      - name: Install locked dev dependencies in the resolved campaign Python\n"
    assert job.count(name) == 1
    body = name + job.split(name, 1)[1].split("      - name:", 1)[0]
    expected = """      - name: Install locked dev dependencies in the resolved campaign Python
        shell: bash
        run: |
          set -euo pipefail
          SETUP_PYTHON="$(python -I -c 'from pathlib import Path; import sys; print(Path(sys.executable).resolve())')"
          CAMPAIGN_PYTHON="$(uv run --frozen --no-sync python -I -c 'from pathlib import Path; import sys; print(Path(sys.executable).resolve())')"
          test "$CAMPAIGN_PYTHON" = "$SETUP_PYTHON"
          REQUIREMENTS="$RUNNER_TEMP/pcc-ab-dev-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT.txt"
          uv export --frozen --only-group dev --no-emit-project --format requirements.txt --output-file "$REQUIREMENTS"
          uv pip install --python "$CAMPAIGN_PYTHON" --require-hashes --only-binary :all: --reinstall --no-deps --no-python-downloads --requirements "$REQUIREMENTS"
"""
    assert body == expected
    assert job.index("Fix the campaign deadline") < job.index(name)
    assert job.index("run: uv sync --frozen --dev --no-install-project") < job.index(name)
    assert job.index(name) < job.index("Qualify both real sources and run bounded compile and full-stage pairs")
    assert "int(time.time()) + 11700" in job and "timeout-minutes: 200" in job
    assert "uv run --frozen --no-sync python scripts/run_pcc_ci_ab.py" in job
    assert "PYTHONPATH" not in body and "--system" not in body
    return body


def test_campaign_dependency_install_binds_lock_hashes_and_actual_resolved_python():
    require_resolved_campaign_dependencies(WORKFLOW.read_text())
    source = (ROOT / "scripts/run_pcc_ci_ab.py").read_text()
    tree = ast.parse(source)
    child = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_child_command")
    assert "str(Path(sys.executable).resolve())" in ast.get_source_segment(source, child)


@pytest.mark.parametrize("before,after", [
    ("uv export --frozen", "uv export"), ("--only-group dev", "--all-extras"),
    ("--no-emit-project", ""), ("--format requirements.txt", "--no-hashes --format requirements.txt"),
    ('test "$CAMPAIGN_PYTHON" = "$SETUP_PYTHON"', 'test "$CAMPAIGN_PYTHON" = "$CAMPAIGN_PYTHON"'),
    ('--python "$CAMPAIGN_PYTHON"', "--python python"),
    ("Path(sys.executable).resolve()", "Path(sys.executable)"),
    ("--require-hashes", "--no-verify-hashes"), ("--only-binary :all:", ""),
    ("--reinstall", ""), ("--no-deps", ""), ("--no-python-downloads", ""),
    ('--requirements "$REQUIREMENTS"', "--requirements pyproject.toml"),
    ("set -euo pipefail", "set +e"), ("uv pip install", "PYTHONPATH=.venv/lib uv pip install"),
])
def test_campaign_dependency_setup_rejects_policy_mutations(before, after):
    workflow = WORKFLOW.read_text()
    step = require_resolved_campaign_dependencies(workflow)
    assert before in step
    changed = workflow.replace(step, step.replace(before, after), 1)
    with pytest.raises(AssertionError):
        require_resolved_campaign_dependencies(changed)


def require_locked_dev_wheels(lock):
    rows = {row["name"]: row for row in lock["package"]}
    assert len(rows) == len(lock["package"])
    pending = [row["name"] for row in rows["python-cc"]["dev-dependencies"]["dev"]]
    assert {"pytest", "pytest-xdist"}.issubset(pending)
    selected = set()
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected.add(name)
        row = rows[name]
        assert row["source"] == {"registry": "https://pypi.org/simple"}
        assert re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", row["version"])
        wheels = row.get("wheels", [])
        assert any(wheel["url"].endswith("-none-any.whl") for wheel in wheels)
        assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", wheel.get("hash", "")) for wheel in wheels)
        pending.extend(dependency["name"] for dependency in row.get("dependencies", []))
    assert "python-cc" not in selected and not {"mlx", "mlx-metal"} & selected
    return selected


def test_current_dev_lock_has_complete_hashed_wheels_without_project_or_optional_extras():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    selected = require_locked_dev_wheels(lock)
    assert {"pytest", "pytest-xdist", "execnet", "iniconfig", "packaging", "pluggy", "pygments"} <= selected


@pytest.mark.parametrize("mutation", ["local_project", "missing_hash", "source_only", "unpinned", "missing_xdist", "optional_extra"])
def test_locked_campaign_dependency_policy_rejects_builds_or_unbound_inputs(mutation):
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    rows = {row["name"]: row for row in lock["package"]}
    if mutation == "local_project":
        rows["pytest"]["source"] = {"editable": "."}
    elif mutation == "missing_hash":
        rows["pytest-xdist"]["wheels"][0].pop("hash")
    elif mutation == "source_only":
        rows["pytest"]["wheels"] = []
    elif mutation == "unpinned":
        rows["pytest"]["version"] = ">=9"
    elif mutation == "missing_xdist":
        rows["python-cc"]["dev-dependencies"]["dev"] = [row for row in rows["python-cc"]["dev-dependencies"]["dev"]
                                                        if row["name"] != "pytest-xdist"]
    else:
        rows["python-cc"]["dev-dependencies"]["dev"].append({"name": "mlx"})
    with pytest.raises(AssertionError):
        require_locked_dev_wheels(lock)


def test_full_stage_is_mandatory_inside_existing_campaign_without_new_selector():
    source = (ROOT / "scripts/run_pcc_ci_ab.py").read_text()
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    guarded = ast.get_source_segment(source, functions["_guarded"])
    assert guarded.index('ab.run_guarded(') < guarded.index('_run_full_stage(') < guarded.index('_finish_guarded(')
    assert 'performance_lock_token=token' in guarded
    stage = ast.get_source_segment(source, functions["_run_full_stage"])
    assert stage.count('stage.run_guarded(') == 2
    assert stage.count('**_guard_kwargs(args, output)') >= 3
    assert 'FULL_STAGE_PAIRS = 5' in source and 'FULL_STAGE_CONTROL_PAIRS = 5' in source
    finish = ast.get_source_segment(source, functions["_finish_guarded"])
    assert finish.index('_verify_full_stage(') < finish.index('finish_campaign(')
    terminal = ast.get_source_segment(source, functions["_verify_completion"])
    assert '_validate_full_stage_metadata(full_stage, output)' in terminal
    assert '_verify_full_stage(' not in terminal
    assert '"full_stage_sha256"' in terminal
    for path in ('scripts/run_pcc_stage_ab.py', 'scripts/bootstrap.py',
                 'scripts/run_pcc_native_deferred.py', 'scripts/pcc_stage_ab_acceptance.py'):
        assert '"' + path + '"' in source
