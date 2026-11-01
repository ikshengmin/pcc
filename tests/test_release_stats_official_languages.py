"""Synthetic receipt fixtures test accounting, never certify native programs."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

STATS = Path(__file__).resolve().parents[1] / "scripts/release_stats"
sys.path.insert(0, str(STATS))
import official_languages as official  # noqa: E402
import languages_stage  # noqa: E402
import report  # noqa: E402

spec = importlib.util.spec_from_file_location("release_stats_cli", STATS / "run.py")
release_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_cli)


REVISION = "1" * 40
IDENTITY = {"pcc": {"revision": REVISION}}


def write(path, value):
    path.write_text(json.dumps(value))


def phase(directory, prefix, outcome, stdout=b"", stderr=b"", exit_code=0):
    (directory / (prefix + ".stdout")).write_bytes(stdout)
    (directory / (prefix + ".stderr")).write_bytes(stderr)
    write(directory / (prefix + ".receipt.json"), {
        "command": ["synthetic-unit-fixture-not-an-execution"],
        "returncode": exit_code, "timed_out": False})
    return {"outcome": outcome, "exit_code": exit_code,
            "stdout_sha256": official.go.sha(stdout), "stderr_sha256": official.go.sha(stderr)}


@pytest.fixture
def observations(tmp_path, monkeypatch):
    suite = official.read_json(official.GO_MANIFEST)
    # Replace only the test inputs with synthetic fixtures, retaining the schema.
    # This temporary manifest is not a real official-source result or baseline.
    bodies = [b"// run\npackage main\nfunc main() {}\n",
              b"// compile\npackage main\nfunc main() {}\n",
              b'// errorcheck\npackage main\nfunc main() {} // ERROR "bool"\n',
              b"// run\npackage main\nfunc main() {}\n",
              b"// run\npackage main\nfunc main() {}\n"]
    for test, body in zip(suite["tests"], bodies):
        test.update(sha256=official.go.sha(body), bytes=len(body), directive=official.go.parse_directive(body),
                    expected_output={"path": None, "sha256": official.go.sha(b"")})
    manifest = tmp_path / "synthetic-manifest.json"
    write(manifest, suite)
    monkeypatch.setattr(official, "GO_MANIFEST", manifest)
    run_dir = tmp_path / "retained"
    run_dir.mkdir()
    run = {"schema_version": 1, "suite_id": suite["id"], "suite_version": suite["version"],
           "suite_source_sha256": suite["source_sha256"], "compiler_revision": REVISION,
           "mode": "pcc0", "backend": "self", "platform": "linux-x86_64", "gc": "not_exercised", "results": []}
    hashes = {"pcc/unit-fixture.py": "f" * 64}
    write(run_dir / "compiler-identity.json", {"revision": REVISION, "source_hashes": hashes,
          "closure_sha256": official.go.sha(json.dumps(hashes, sort_keys=True).encode())})
    write(run_dir / "adapter-identity.json", {"manifest_sha256": official.sha256_file(manifest)})
    version = phase(run_dir, "reference-version", "completed", b"go version go1.27.3 linux/amd64\n")
    write(run_dir / "reference-identity.json", {"source_archive_sha256": suite["archive_sha256"],
                                               "version_process": version})
    for index, (test, body) in enumerate(zip(suite["tests"], bodies)):
        directory = run_dir / f"case-{index:02d}"
        directory.mkdir()
        (directory / Path(test["path"]).name).write_bytes(body)
        expectation = test["expectation"]
        rejected = expectation == "reject"
        stderr = (Path(test["path"]).name + ":3: bool\n").encode() if rejected else b""
        row = {"test_id": test["id"], "input_sha256": test["sha256"],
               "compile": phase(directory, "pcc-compile", "rejected" if rejected else "accepted",
                                stderr=stderr, exit_code=int(rejected)),
               "run": {"outcome": "not_run"}, "checks": {official.go.CHECK: "passed"}}
        row["reference"] = {"name": "Go", "version": suite["version"], "role": "external_reference",
                            "platform": run["platform"], "input_sha256": test["sha256"],
                            "kind": {"run": "native_execution", "accept": "metadata_typecheck",
                                     "reject": "expected_rejection"}[expectation],
                            "compile": phase(directory, "go-compile", "rejected" if rejected else "accepted",
                                             stderr=stderr, exit_code=int(rejected)),
                            "run": {"outcome": "not_run"}}
        if expectation == "run":
            row["run"] = phase(directory, "pcc-run", "completed")
            row["reference"]["run"] = phase(directory, "go-run", "completed")
        for filename in ("owned", "reference"):
            (directory / filename).write_bytes(b"synthetic fixture, not a native binary")
        write(directory / "diagnostic.json", {"kind": "GoDiagnostic" if rejected else "accepted",
                                             "implementation_unsupported": False})
        run["results"].append(row)
    write(run_dir / "run.json", run)
    return suite, run, run_dir


def test_counts_are_raw_programs_not_compile_reject_or_pytest(observations):
    _, _, directory = observations
    result = official.summarize_go(directory, IDENTITY)
    assert result["selected_denominator"] == 5
    assert result["selected_by_kind"] == {"run_pass": 3, "compile_only": 1, "expected_reject": 1}
    assert result["executed_programs"] == result["reference_matched_programs"] == 3
    assert result["outcomes"]["compile_only_pass"] == result["outcomes"]["expected_reject_pass"] == 1
    assert result["inventory"]["denominator"] is None
    assert str(directory) not in json.dumps(result)
    text = report._official_language(result)
    assert "3/3 run-pass" in text and "5/5" in text and "tiny representative shard" in text
    assert "1/1 compile-only" in text and "1/1 expected-reject" in text
    assert "denominator and unrun remainder unknown" in text


def test_historical_run_keeps_observed_revision(observations):
    result = official.summarize_go(observations[2], {"pcc": {"revision": "2" * 40}})
    assert result["observed_compiler_revision"] == REVISION
    assert not result["current_revision_match"]
    assert "historical observation, not the current report revision" in report._official_language(result)


@pytest.mark.parametrize("mutation", ["stdout", "exit", "input", "missing"])
def test_tampered_or_missing_receipts_cannot_count_program(observations, mutation):
    _, _, directory = observations
    case = directory / "case-00"
    if mutation == "stdout":
        (case / "pcc-run.stdout").write_bytes(b"tampered")
    elif mutation == "exit":
        write(case / "pcc-run.receipt.json", {"returncode": 7, "timed_out": False, "command": ["fixture"]})
    elif mutation == "input":
        (case / "bug065.go").write_bytes(b"changed")
    else:
        (case / "pcc-run.stderr").unlink()
    result = official.summarize_go(directory, IDENTITY)
    assert result["reference_matched_programs"] == 2
    assert result["outcomes"]["missing_evidence"] == 1
    assert result["selected_denominator"] == 5


def test_byte_mismatch_is_failed_even_when_adapter_flag_says_passed(observations):
    _, run, directory = observations
    run["results"][0]["run"] = phase(directory / "case-00", "pcc-run", "completed", b"different")
    write(directory / "run.json", run)
    result = official.summarize_go(directory, IDENTITY)
    assert result["executed_programs"] == 3 and result["reference_matched_programs"] == 2
    assert result["outcomes"]["failed"] == 1


def test_unsupported_missing_tools_unrun_and_failed_are_separate(observations):
    _, run, directory = observations
    for index, row in enumerate(run["results"][:3]):
        row["compile"] = {"outcome": "not_run"}
        row["run"] = {"outcome": "not_run"}
        row["checks"] = {official.go.CHECK: "failed"}
        if index == 0:
            row["diagnostic_category"] = "frontend_unsupported"
        elif index == 1:
            row["compile"] = {"outcome": "blocked", "reason": "missing tool"}
        else:
            row["compile"] = {"outcome": "blocked", "reason": "timeout"}
    run["results"].pop()
    write(directory / "run.json", run)
    result = official.summarize_go(directory, IDENTITY)
    for key in ("unsupported", "missing_prerequisite", "failed", "unrun", "run_pass"):
        assert result["outcomes"][key] == 1


def test_rejection_replays_annotations_and_rejects_capability_diagnostic(observations):
    _, _, directory = observations
    write(directory / "case-02/diagnostic.json", {"kind": "GoDiagnostic", "implementation_unsupported": True})
    result = official.summarize_go(directory, IDENTITY)
    assert result["outcomes"]["expected_reject_pass"] == 0
    assert result["outcomes"]["failed"] == 1


def test_inventory_includes_unobserved_remainder_not_just_shard(observations):
    suite, run, directory = observations
    inventory = {key: suite[key] for key in ("version", "source_revision", "archive_sha256", "license_sha256")}
    inventory.update(inventory_complete=True, scope="synthetic official-driver inventory",
                     missing_inputs=[], extra_inputs=[], changed_inputs=[],
                     tests=[{"path": test["path"], "sha256": test["sha256"],
                             **test["directive"]} for test in suite["tests"]])
    inventory["tests"].append({"path": "test/extra.go", "sha256": "e" * 64,
                               "action": "run", "recipe": "run -race", "supported": False})
    inventory["driver_test_count"] = 6
    inventory["recipes"] = {"run": 4, "compile": 1, "errorcheck": 1}
    inventory["adapter_supported_count"] = 5
    run["results"].pop()
    write(directory / "run.json", run)
    result = official.summarize_go(directory, IDENTITY, inventory)
    assert result["inventory"]["denominator"] == 6
    assert result["inventory"]["unobserved"] == 2
    assert result["inventory"]["recipe_counts"] == {"run": 4, "compile": 1, "errorcheck": 1}
    assert result["inventory"]["unobserved_by_recipe"]["run"] == 2
    assert result["inventory"]["literal_plain_run_count"] == 3
    assert result["inventory"]["adapter_admitted_not_passed"] == 5
    assert "inventory 6, 2 unobserved" in report._official_language(result)
    for key in ("version", "driver_test_count", "inventory_complete", "recipes", "adapter_supported_count"):
        broken = deepcopy(inventory)
        broken[key] = "wrong"
        with pytest.raises(ValueError):
            official.summarize_go(directory, IDENTITY, broken)


@pytest.mark.parametrize("target", ["compiler", "adapter", "reference"])
def test_provenance_mismatch_is_not_relabelled(observations, target):
    _, _, directory = observations
    write(directory / (target + "-identity.json"), {})
    with pytest.raises(ValueError):
        official.summarize_go(directory, IDENTITY)


def options(tmp_path, **overrides):
    values = dict(official_selection="fast", official_go_run=None, official_go_execute=False,
                  official_go_inventory=None, official_go_src=tmp_path, official_go_tool=tmp_path / "go",
                  official_go_reference_cache=tmp_path, official_pcc_source=None)
    return SimpleNamespace(**(values | overrides))


def test_real_adapter_command_and_immutable_guards(tmp_path, monkeypatch):
    calls = []
    def guarded(command, **kwargs):
        calls.append((command, kwargs))
        out = Path(command[command.index("--out") + 1])
        out.mkdir()
        write(out / "run.json", {})
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(official.watchdog, "run", guarded)
    directory, execution = official.execute_go(options(tmp_path), tmp_path, IDENTITY)
    command, kwargs = calls[0]
    assert command[:3] == [sys.executable, str(official.REPO / "scripts/go_conformance.py"), "run"]
    assert kwargs["timeout"] == 90 and kwargs["rss_limit"] == 1 << 30
    assert command[-2:] == ["--timeout", "6"]
    assert directory == tmp_path / "official-go" and execution["status"] == "failed"
    assert execution["returncode"] == 1


def test_full_selection_and_missing_inputs_never_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(official.watchdog, "run", lambda *a, **k: pytest.fail("unexpected launch"))
    assert official.execute_go(options(tmp_path, official_selection="full"), tmp_path, IDENTITY)[1]["status"] == "unsupported"
    assert official.execute_go(options(tmp_path, official_go_src=None), tmp_path, IDENTITY)[1]["status"] == "missing_prerequisite"


def test_invalid_inventory_fails_before_any_execution(tmp_path, monkeypatch):
    inventory = tmp_path / "invalid.json"
    write(inventory, {})
    monkeypatch.setattr(official, "execute_go", lambda *a: pytest.fail("unexpected launch"))
    with pytest.raises(ValueError, match="inventory identity mismatch"):
        official.collect(options(tmp_path, official_go_execute=True, official_go_inventory=inventory), tmp_path, IDENTITY)


def test_official_only_preserves_unrun_and_never_runs_c_or_pytest(tmp_path, monkeypatch):
    for name in ("run_c_suites", "run_language_suites"):
        monkeypatch.setattr(languages_stage, name, lambda *a: pytest.fail("unexpected legacy suite execution"))
    result = languages_stage.run(options(tmp_path, official_only=True), tmp_path, deepcopy(IDENTITY))
    assert result["c_suites"] == result["language_suites"] == {}
    assert result["official_suites"]["go"]["outcomes"]["unrun"] == 5
    for language in ("cpp", "rust", "java"):
        assert result["official_suites"][language]["status"] == "missing_adapter"
        assert result["official_suites"][language]["selected_denominator"] is None
    assert report.compatibility_rows({"languages": result}) == []
    result["identity"].update(reference_python={"implementation": "CPython", "version": "3.15.0"},
                               machine={"machine": "x86_64", "platform": "test"},
                               started_utc="2026-11-01T00:00:00+00:00")
    assert "compatibility.svg" not in report.section({"languages": result}, "images")


@pytest.mark.parametrize("selection,chart", [("empty", False), ("go", False), ("c", True)])
def test_compatibility_image_only_when_a_chart_is_written(tmp_path, selection, chart):
    identity = {**deepcopy(IDENTITY), "reference_python": {"implementation": "CPython", "version": "3.15.0"},
                "machine": {"machine": "x86_64", "platform": "test"}, "started_utc": "2026-11-01"}
    entry = {"identity": identity, "c_suites": {}, "language_suites": {}}
    if selection == "go":
        entry["official_suites"] = {"go": official.summarize_go(None, identity)}
    if selection == "c":
        entry["c_suites"] = {"c-testsuite": {"passed": 1, "cases": 2}}
    results = {"languages": entry}
    text = report.section(results, "images")
    report.write_charts(results, tmp_path)
    assert ("compatibility.svg" in text) == chart
    assert (tmp_path / "compatibility.svg").is_file() == chart


def test_cli_official_modes_are_explicit_and_mutually_exclusive(tmp_path):
    args = ["languages", "--out", str(tmp_path), "--official-only"]
    parsed = release_cli.parser().parse_args(args)
    assert parsed.official_only and parsed.official_selection == "fast"
    assert not parsed.official_go_execute
    with pytest.raises(SystemExit):
        release_cli.parser().parse_args(args + ["--official-go-execute", "--official-go-run", "prior"])
