"""CI owner contracts, never a substitute for the six native pressure cases."""

from contextlib import contextmanager
from copy import deepcopy
import ast
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import ci_bootstrap_partition as partition
from scripts import ci_pytest_markers as gate


def _runtime_inventories():
    bootstrap = list(partition.BOOTSTRAP_NODES)
    pressure = [node + "@gc_pressure_native" for node in partition.PRESSURE_NODES]
    ordinary = ["tests/test_ordinary.py::test_body"]
    original = bootstrap + pressure + ordinary
    rest, selected, transferred = partition.partition_inventory(original, original, "remainder")
    expected, selected, moved = partition.runtime_phase_inventory(rest, selected, "ordinary")
    return ({"partition": "remainder", "runtime_phase": "ordinary", "marker": "integration",
             "original_expected": original, "expected": expected, "selected": selected,
             "transferred": transferred, "pressure_transferred": moved},
            {"runtime_phase": "pressure", "marker": "integration",
             "original_expected": pressure, "expected": pressure, "selected": pressure,
             "pressure_transferred": []})


def test_pressure_partition_preserves_exact_six_and_bootstrap_union():
    ordinary, pressure = _runtime_inventories()
    combined = partition.combine_runtime_inventory(ordinary, pressure)
    partition.verify_union({"partition": "bootstrap", "selected": list(partition.BOOTSTRAP_NODES)}, combined)
    assert combined["selected"] == pressure["selected"] + ordinary["selected"]
    assert len(combined["selected"]) == 7
    gated = deepcopy(ordinary)
    gated["pressure_transferred"] = list(partition.PRESSURE_NODES)
    gated["original_expected"] = [partition.canonical_node(node) for node in ordinary["original_expected"]]
    normalized = partition.combine_runtime_inventory(gated, pressure)
    assert normalized["runtime_phase_nodes"]["pressure"] == pressure["selected"]
    partition.verify_union({"partition": "bootstrap", "selected": list(partition.BOOTSTRAP_NODES)}, normalized)
    regrouped = deepcopy(ordinary)
    regrouped["selected"][0] += "@changed_group"
    with pytest.raises(ValueError, match="scheduler group differs"):
        partition.combine_runtime_inventory(regrouped, pressure)
    for key in ("pressure_transferred", "original_expected"):
        regrouped = deepcopy(ordinary)
        index = next(i for i, node in enumerate(regrouped[key])
                     if partition.canonical_node(node) in partition.PRESSURE_NODES)
        regrouped[key][index] = partition.canonical_node(regrouped[key][index]) + "@other_group"
        with pytest.raises(ValueError, match="unknown pressure scheduler group"):
            partition.combine_runtime_inventory(regrouped, pressure)
    for bad in (pressure["expected"][:-1], pressure["expected"] + [pressure["expected"][0]],
                pressure["expected"] + ["tests/extra.py::test_extra"]):
        with pytest.raises(ValueError):
            partition.runtime_phase_inventory(bad, bad, "pressure")
    for target, key in ((ordinary, "pressure_transferred"), (pressure, "selected")):
        before = target[key]
        target[key] = before[:-1]
        with pytest.raises(ValueError):
            partition.combine_runtime_inventory(ordinary, pressure)
        target[key] = before
    ordinary["selected"] += pressure["selected"][:1]
    with pytest.raises(ValueError, match="overlap"):
        partition.combine_runtime_inventory(ordinary, pressure)


@pytest.mark.parametrize("platform", ["linux-x86_64", "linux-aarch64", "macos-arm64"])
def test_pressure_build_and_both_phases_share_original_residual_guard(tmp_path, platform):
    argv = gate.watchdog_command(tmp_path, "integration", platform, partition="remainder", timeout=317)
    assert argv[argv.index("--timeout") + 1] == "317"
    assert argv[argv.index("--") + 1:] == gate.execution_command(tmp_path, "integration", "remainder")
    assert argv[-2:] == ["--runtime-phases", str(tmp_path)]
    assert "--no-performance-lock" in argv
    if platform == "macos-arm64":
        assert argv[argv.index("--auto-tree-rss-ceiling-bytes") + 1] == "4294967296"
        assert argv[argv.index("--min-tree-rss-bytes") + 1] == "2147483648"
    else:
        assert argv[argv.index("--max-tree-rss-bytes") + 1] == "17179869184"
    with pytest.raises(ValueError, match="original envelope"):
        gate.watchdog_command(tmp_path, "integration", platform, partition="remainder", timeout=1801)


def test_pressure_windows_reuses_one_job_object_and_residual_timeout(tmp_path, monkeypatch):
    from scripts import platform_process_watchdog

    calls = []
    def run(command, **options):
        calls.append((command, options))
        assert options["timeout"] == 317 and options["rss_limit"] == 17179869184
        assert options["env"]["PCC_WORKER_TREE_BUDGET_BYTES"] == "17179869184"
        return {"command": command, "peak_tree_rss": 100, "seconds": 0.2}
    monkeypatch.setattr(platform_process_watchdog, "run", run)
    gate.run_guarded(tmp_path, "integration", {}, "windows-x86_64", partition="remainder", timeout=317)
    assert len(calls) == 1 and calls[0][0][-2:] == ["--runtime-phases", str(tmp_path)]


def test_explicit_pressure_build_does_not_mutate_test_or_worker_environment():
    original = {"PCC_RUNTIME_ARCHIVE": "ordinary", "PCC_THREADED_RUNTIME_ARCHIVE": "ordinary",
                "PCC_TEST_COMPILER": "pcc1", "PCC_PLATFORM_TEST_COMPILER": "pcc1",
                "PCC_CURRENT_PCC1": "pcc1", "PCC_REQUIRE_CURRENT_PCC1": "1",
                "PCC_TEST_COMPILER_LOG": "native.jsonl", "PCC_TEST_COMPILER_STRICT": "1",
                "PCC_TEST_COMPILER_ROUTED": "1", "PCC_TEST_COMPILER_HASH_INPUTS": "1",
                "PCC_TEST_NO_NATIVE_PROVISIONING": "1",
                "PCC_WORKER_TREE_BUDGET_BYTES": "2147483648", "PCC_RUNTIME_BUILD_JOBS": "2",
                "PCC_WORKER_DEADLINE_MONOTONIC": "123.0"}
    before = dict(original)
    build = gate.pressure_build_environment(original)
    assert original == before
    assert build["PCC_TEST_NO_NATIVE_PROVISIONING"] == "0"
    assert build["PCC_RUNTIME_GC_STRESS_EVERY"] == "3" and build["PCC_NO_AUTO_PCC1"] == "1"
    assert not any(name in build for name in ("PCC_RUNTIME_ARCHIVE", "PCC_THREADED_RUNTIME_ARCHIVE",
                   "PCC_TEST_COMPILER", "PCC_CURRENT_PCC1", "PCC_PLATFORM_TEST_COMPILER",
                   "PCC_TEST_COMPILER_LOG", "PCC_TEST_COMPILER_STRICT", "PCC_TEST_COMPILER_ROUTED"))
    assert "PCC_TEST_COMPILER_HASH_INPUTS" not in build
    for name in ("PCC_RUNTIME_BUILD_JOBS", "PCC_WORKER_TREE_BUDGET_BYTES", "PCC_WORKER_DEADLINE_MONOTONIC"):
        assert build[name] == original[name]


def test_marker_rejects_inherited_pressure_hashing_before_any_admission(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", {"PCC_TEST_COMPILER_HASH_INPUTS": "1"})
    args = SimpleNamespace(marker="integration", partition="bootstrap", in_place=True,
                           platform="linux-x86_64", out=tmp_path / "report", owner_report=None)
    with pytest.raises(ValueError, match="inherited pressure configuration"):
        gate.run(args)


@pytest.mark.parametrize("failure", [None, "pressure", "ordinary"])
def test_pressure_attempt_precedes_ordinary_without_environment_leak(tmp_path, monkeypatch, failure):
    env = {"PCC_RUNTIME_ARCHIVE": "ordinary.a", "PCC_THREADED_RUNTIME_ARCHIVE": "ordinary.a",
           "PCC_MARKER_PARTITION": "remainder", "PCC_CI_STAGE1_MANIFEST": "original.json",
           "PCC_CI_STAGE1_MANIFEST_SHA256": "original-hash", "PCC_TEST_COMPILER": "pcc1",
           "PCC_TEST_NO_NATIVE_PROVISIONING": "1"}
    monkeypatch.setattr(os, "environ", env)
    monkeypatch.setattr(gate, "admitted_stage1", lambda *args: "pcc1")
    monkeypatch.setattr(gate, "summarize_runtime_phases", lambda path: {"status": "PASS"})
    monkeypatch.setattr(gate, "runtime_proof_files", lambda path: {"contract-fixture": "only"})
    calls = []
    def run(command, **options):
        phase = options["env"]["PCC_MARKER_RUNTIME_PHASE"]
        calls.append((phase, command, options["env"]))
        assert options["env"]["PCC_TEST_COMPILER"] == "pcc1"
        assert options["env"]["PCC_CI_STAGE1_MANIFEST"] == "original.json"
        assert options["env"]["PCC_CI_STAGE1_MANIFEST_SHA256"] == "original-hash"
        assert options["env"]["PCC_TEST_NO_NATIVE_PROVISIONING"] == "1"
        assert "timeout" not in options, "the original outer guard owns the only deadline"
        if phase == failure:
            raise subprocess.CalledProcessError(1, command)
        return None
    monkeypatch.setattr(gate.subprocess, "run", run)
    if failure:
        with pytest.raises(subprocess.CalledProcessError):
            gate.execute_runtime_phases(tmp_path)
    else:
        gate.execute_runtime_phases(tmp_path)
    assert calls[0][0] == "pressure"
    pressure_env = calls[0][2]
    assert pressure_env["PCC_RUNTIME_GC_STRESS_EVERY"] == "3"
    assert pressure_env["PCC_GC_PRESSURE_NATIVE"] == "1"
    assert pressure_env["PCC_RUNTIME_ARCHIVE"] == str(tmp_path / "pressure/runtime/libpy_runtime_pcc_py.a")
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    if failure == "pressure":
        assert len(calls) == 1 and receipt["ordinary"] == "UNRUN"
    else:
        assert calls[1][0] == "ordinary" and len(calls) == 2
        ordinary_env = calls[1][2]
        assert ordinary_env["PCC_RUNTIME_ARCHIVE"] == ordinary_env["PCC_THREADED_RUNTIME_ARCHIVE"] == "ordinary.a"
        assert "PCC_RUNTIME_GC_STRESS_EVERY" not in ordinary_env and "PCC_GC_PRESSURE_NATIVE" not in ordinary_env
        assert "PCC_TEST_COMPILER_HASH_INPUTS" not in ordinary_env
        assert ordinary_env["PCC_MARKER_REPORT_DIR"] != pressure_env["PCC_MARKER_REPORT_DIR"]
        assert ordinary_env["PCC_TEST_COMPILER_LOG"] != pressure_env["PCC_TEST_COMPILER_LOG"]
        assert "-x" in calls[1][1] and calls[1][1][-1] == "tests"
    assert receipt["status"] == ("FAIL" if failure else "PASS")
    assert "PCC_MARKER_RUNTIME_PHASE" not in env


def _routing_rows(compiler, runtime):
    from tests import pcc1_route
    from tests.python.test_gc_pressure_native import PROGRAM

    compiler.write_bytes(b"inert compiler fixture, never executed")
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_bytes(b"inert runtime fixture, never executed")
    directory = compiler.parent / "tmp/fixture"
    directory.mkdir(parents=True, exist_ok=True)
    source, helper, output = (directory / name for name in ("pressure.py", "guards.o", "pressure"))
    source.write_text(PROGRAM)
    helper.write_bytes(b"inert guard object")
    output.write_bytes(b"inert output, never executed")
    identity = {"route": "native", "kind": "compile_python", "compiler": str(compiler),
                "runtime_archive": str(runtime), "invocation": "123:0",
                "test": partition.PRESSURE_NODES[0] + " (setup)",
                "inputs": pcc1_route._compile_inputs(compiler, runtime, source, [helper]),
                "output_path": str(output)}
    return [dict(identity, event="attempt"), dict(identity, event="complete", outcome="passed", returncode=0,
                inputs_after=deepcopy(identity["inputs"]), output=pcc1_route._file_identity(output))]


def test_pressure_routing_requires_exact_completed_native_compile(tmp_path):
    path, compiler, runtime = tmp_path / "pcc1-routing.jsonl", tmp_path / "pcc1", tmp_path / "pressure.a"
    rows = _routing_rows(compiler, runtime)
    def write(value):
        path.write_text("".join(json.dumps(row) + "\n" for row in value))
    write(rows)
    assert gate.verify_pressure_routing(path, compiler, runtime)["status"] == "PASS"
    corruptions = [rows[:1], rows + rows, [rows[0], dict(rows[1], outcome="failed")],
                   [rows[0], dict(rows[1], invocation="other")],
                   [rows[0], dict(rows[1], runtime_archive="ordinary.a")],
                   [rows[0], dict(rows[1], route="host")]]
    for bad in corruptions:
        write(bad)
        with pytest.raises(ValueError):
            gate.verify_pressure_routing(path, compiler, runtime)
    for key in ("compiler", "runtime", "source"):
        bad = deepcopy(rows)
        bad[1]["inputs_after"][key]["sha256"] = "0" * 64
        write(bad)
        with pytest.raises(ValueError, match="inputs changed"):
            gate.verify_pressure_routing(path, compiler, runtime)
    for item in (rows[0]["inputs"]["source"], rows[0]["inputs"]["objects"][0], rows[1]["output"]):
        write(rows)
        file = Path(item["path"])
        before = file.read_bytes()
        file.write_bytes(before + b"changed")
        with pytest.raises(ValueError, match="bytes changed"):
            gate.verify_pressure_routing(path, compiler, runtime)
        file.write_bytes(before)
    bad = deepcopy(rows)
    bad[1]["output"]["path"] = str(tmp_path / "outside")
    write(bad)
    with pytest.raises(ValueError, match="output identity"):
        gate.verify_pressure_routing(path, compiler, runtime)


def test_optional_native_input_identity_does_not_reject_missing_negative_input(tmp_path):
    from tests import pcc1_route

    missing = tmp_path / "missing"
    row = pcc1_route._compile_inputs(missing, missing, missing, [missing])
    assert row["source"] == {"path": str(missing), "sha256": None}
    assert row["objects"] == [row["source"]]


@pytest.mark.parametrize("mutation", [None, "source", "platform", "attempt", "archive-path", "archive-bytes",
                                    "provenance", "interval", "routing", "missing-receipt", "config"])
def test_final_pressure_summary_readmits_exact_artifacts_and_restores_ordinary_config(tmp_path, monkeypatch, mutation):
    """Receipt mutation controls; the provenance verifier is an explicit seam."""
    from scripts import bootstrap_platform
    from tests import runtime_fixture_provenance

    pressure_dir = tmp_path / "pressure"
    ordinary_dir = tmp_path / "ordinary"
    pressure_dir.mkdir()
    ordinary_dir.mkdir()
    compiler = pressure_dir / "pcc1"
    runtime = pressure_dir / "runtime/libpy_runtime_pcc_py.a"
    rows = _routing_rows(compiler, runtime)
    routing_path = pressure_dir / "pcc1-routing.jsonl"
    routing_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    provenance = {"target_triple": "host-target", "contract_fixture": True}
    manifest_path = tmp_path / "manifest.json"
    source = {"commit": "a" * 40}
    lease = {"run_id": 123, "run_attempt": 1}
    manifest = {"paths": {"compiler": "pcc1"}, "source": source, "producer": lease,
                "platform": "linux-x86_64", "target": "host-target"}
    manifest_path.write_text(json.dumps(manifest))
    paths = (runtime, Path(str(runtime) + ".provenance.json"), Path(str(runtime) + ".capi_syms"))
    paths[1].write_text(json.dumps(provenance))
    paths[2].write_text("symbols\n")
    summary = {"status": "PASS", "selected_count": 6}
    receipt = {"status": "PASS", "build": "PASS", "tests": "PASS", "summary": summary,
               "source": source, "producer": lease, "platform": manifest["platform"],
               "manifest_sha256": gate.digest(manifest_path), "interval": 3,
               "archive_files": {str(p): gate.digest(p) for p in paths}, "provenance": provenance,
               "routing": gate.verify_pressure_routing(routing_path, compiler, runtime)}
    ordinary, pressure = _runtime_inventories()
    (ordinary_dir / "inventory-gw0.json").write_text(json.dumps(ordinary))
    (pressure_dir / "inventory-gw0.json").write_text(json.dumps(pressure))
    monkeypatch.setattr(os, "environ", {"PCC_CI_STAGE1_MANIFEST": str(manifest_path),
                      "PCC_CI_STAGE1_MANIFEST_SHA256": gate.digest(manifest_path)})
    monkeypatch.setattr(gate, "ROOT", pressure_dir)
    monkeypatch.setattr(gate, "workflow_lease", lambda: lease)
    monkeypatch.setattr(gate, "host_platform", lambda: "linux-x86_64")
    monkeypatch.setattr(bootstrap_platform, "source_identity", lambda: source)
    monkeypatch.setattr(gate, "summarize", lambda *a, **kw: summary)
    checks = []
    def verify(path, *, threads):
        assert os.environ["PCC_RUNTIME_GC_STRESS_EVERY"] == "3" and threads is True and path == runtime
        checks.append(path)
        if mutation == "config":
            raise ValueError("ordinary archive cannot satisfy interval three")
        return runtime, provenance
    monkeypatch.setattr(runtime_fixture_provenance, "_verified_test_runtime_archive", verify)
    if mutation == "source":
        receipt["source"] = {"commit": "stale"}
    elif mutation == "platform":
        receipt["platform"] = "linux-aarch64"
    elif mutation == "attempt":
        receipt["producer"] = dict(lease, run_attempt=2)
    elif mutation == "archive-path":
        receipt["archive_files"] = {"wrong": "0" * 64}
    elif mutation == "archive-bytes":
        runtime.write_bytes(b"changed archive")
    elif mutation == "provenance":
        receipt["provenance"] = dict(provenance, target_triple="wrong")
    elif mutation == "interval":
        receipt["interval"] = 0
    elif mutation == "routing":
        rows[1]["outcome"] = "failed"
        routing_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    if mutation != "missing-receipt":
        (pressure_dir / "receipt.json").write_text(json.dumps(receipt))
    if mutation:
        with pytest.raises((ValueError, FileNotFoundError)):
            gate.summarize_runtime_phases(tmp_path)
        assert not (tmp_path / "inventory-combined.json").exists()
    else:
        assert gate.summarize_runtime_phases(tmp_path)["status"] == "PASS" and len(checks) == 1
        assert (tmp_path / "inventory-combined.json").is_file()
    assert "PCC_RUNTIME_GC_STRESS_EVERY" not in os.environ


@pytest.mark.parametrize("mutation", [None, "stdout-missing", "stdout-duplicate", "stdout-truncated",
                                    "stdout-trailer", "receipt", "proof-map", "report", "missing-report",
                                    "guard", "source", "producer", "platform", "manifest", "phase", "union",
                                    "symlink"])
def test_post_guard_consumes_bound_reports_without_deep_admission(tmp_path, monkeypatch, mutation):
    """Sealed-report controls only. No runtime/compiler/program payload exists."""
    from tests import runtime_fixture_provenance

    def forbidden(*args, **kwargs):
        raise AssertionError("deep pressure admission must stay inside the original guard")
    monkeypatch.setattr(runtime_fixture_provenance, "_verified_test_runtime_archive", forbidden)
    monkeypatch.setattr(gate, "verify_pressure_routing", forbidden)
    monkeypatch.setattr(gate, "summarize_runtime_phases", forbidden)
    ordinary, pressure = _runtime_inventories()
    combined = partition.combine_runtime_inventory(ordinary, pressure)
    for phase, inventory in (("ordinary", ordinary), ("pressure", pressure)):
        directory = tmp_path / phase
        directory.mkdir()
        for name in ("configuration.json", "live.jsonl"):
            (directory / name).write_text('{}\n')
        for worker in range(6):
            (directory / f"inventory-gw{worker}.json").write_text(json.dumps(inventory))
    (tmp_path / "pressure/command.json").write_text('{}\n')
    (tmp_path / "pressure/pcc1-routing.jsonl").write_text('{"fixture":"already deeply checked under guard"}\n')
    identity = {"source": {"commit": "a" * 40}, "producer": {"run_id": 123, "run_attempt": 1},
                "platform": "linux-x86_64", "manifest_sha256": "b" * 64}
    pressure_receipt = dict(identity, status="PASS", build="PASS", tests="PASS", interval=3,
                           routing={"log_sha256": gate.digest(tmp_path / "pressure/pcc1-routing.jsonl")})
    gate.save(tmp_path / "pressure/receipt.json", pressure_receipt)
    gate.save(tmp_path / "inventory-combined.json", combined)
    summaries = {"pressure": {"status": "PASS", "selected_count": 6},
                 "ordinary": {"status": "PASS", "selected_count": 1}}
    monkeypatch.setattr(gate, "summarize", lambda directory, *args, **kwargs: summaries[directory.name])
    receipt = {"status": "PASS", "pressure": "PASS", "ordinary": "PASS", "runtime_phases": deepcopy(summaries),
               "marker_count": 7, "selected_count": 7, "proof_files": gate.runtime_proof_files(tmp_path)}
    gate.save(tmp_path / "result.json", {"status": "COMPLETE", "returncode": 0})
    def seal():
        gate.save(tmp_path / "receipt.json", receipt)
        (tmp_path / "target.stdout").write_text("ordinary log line\n" + gate.RUNTIME_PROOF_PREFIX
                                                + gate.digest(tmp_path / "receipt.json") + "\n")
    seal()
    if mutation and mutation.startswith("stdout-"):
        output = tmp_path / "target.stdout"
        raw = output.read_text()
        if mutation == "stdout-missing":
            output.write_text("no completed proof\n")
        elif mutation == "stdout-duplicate":
            output.write_text(raw + raw.splitlines()[-1] + "\n")
        elif mutation == "stdout-truncated":
            output.write_text(raw[:-1])
        else:
            output.write_text(raw + "unexpected trailer\n")
    elif mutation == "receipt":
        with (tmp_path / "receipt.json").open("a") as stream:
            stream.write(" ")
    elif mutation == "proof-map":
        receipt["proof_files"]["../../unrelated"] = "0" * 64
        seal()
    elif mutation == "report":
        (tmp_path / "pressure/live.jsonl").write_text("mutated report\n")
    elif mutation == "missing-report":
        (tmp_path / "pressure/receipt.json").unlink()
    elif mutation == "guard":
        gate.save(tmp_path / "result.json", {"status": "TIMEOUT", "returncode": 124})
    elif mutation in ("source", "producer", "platform", "manifest"):
        pressure_receipt["manifest_sha256" if mutation == "manifest" else mutation] = "wrong identity"
        gate.save(tmp_path / "pressure/receipt.json", pressure_receipt)
        receipt["proof_files"] = gate.runtime_proof_files(tmp_path)
        seal()
    elif mutation == "phase":
        summaries["pressure"]["status"] = "PASS_WITH_GAPS"
    elif mutation == "union":
        ordinary["selected"] = []
        gate.save(tmp_path / "ordinary/inventory-gw0.json", ordinary)
        receipt["proof_files"] = gate.runtime_proof_files(tmp_path)
        seal()
    elif mutation == "symlink":
        original = Path.is_symlink
        target = tmp_path / "pressure/live.jsonl"
        monkeypatch.setattr(Path, "is_symlink", lambda path: path == target or original(path))
    if mutation:
        with pytest.raises((ValueError, FileNotFoundError)):
            gate.summarize_completed_runtime_phases(tmp_path, **identity)
    else:
        result = gate.summarize_completed_runtime_phases(tmp_path, **identity)
        assert result["status"] == "PASS" and result["selected_count"] == 7
        assert result["guarded_runtime_proof_sha256"] == gate.digest(tmp_path / "receipt.json")


def test_parent_remainder_uses_report_only_post_guard_consumer():
    unit = ast.parse(inspect.getsource(gate.run))
    calls = [node.func.id for node in ast.walk(unit) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)]
    assert "summarize_completed_runtime_phases" in calls
    assert "summarize_runtime_phases" not in calls and "verify_pressure_routing" not in calls
    # Existing ordinary Stage1 admission remains before run_guarded; the new
    # pressure archive verifier must not appear in the post-guard consumer.
    unit = ast.parse(inspect.getsource(gate.summarize_completed_runtime_phases))
    calls = [node.func.id for node in ast.walk(unit) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)]
    assert not {"_verified_test_runtime_archive", "verify_pressure_routing", "source_identity"} & set(calls)


def test_guarded_stdout_proof_streams_and_requires_one_complete_terminal_marker(tmp_path, monkeypatch):
    path = tmp_path / "target.stdout"
    expected = "a" * 64
    path.write_text("diagnostic\n" * 1000 + gate.RUNTIME_PROOF_PREFIX + expected + "\n")
    def forbidden(*args, **kwargs):
        raise AssertionError("do not materialize the complete target log")
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    gate.require_runtime_stdout_proof(path, expected)


@pytest.mark.parametrize("failure", [None, "lock", "build", "provenance", "tests", "mutate"])
def test_pressure_owner_keeps_build_and_tests_inside_existing_lock(tmp_path, monkeypatch, failure):
    """Only inert files and subprocess seams; no PCC compilation in this contract."""
    from scripts import bootstrap_platform, file_lock
    from tests import runtime_fixture_provenance

    root, directory = tmp_path / "repo", tmp_path / "report"
    root.mkdir()
    directory.mkdir()
    compiler, original_runtime = root / "pcc1", root / "ordinary.a"
    compiler.write_bytes(b"inert compiler")
    original_runtime.write_bytes(b"ordinary runtime must survive")
    original_bytes = original_runtime.read_bytes()
    source = {"commit": "a" * 40, "source_sha256": "b" * 64, "clean_commit": True}
    lease = {"repository": "ikshengmin/pcc", "run_id": 123, "run_attempt": 1}
    manifest = {"source": source, "producer": lease, "platform": "linux-x86_64", "target": "host-target",
                "paths": {"compiler": "pcc1", "runtime": "ordinary.a"},
                "files": {p.name: {"sha256": gate.digest(p)} for p in (compiler, original_runtime)}}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    runtime = directory / "runtime/libpy_runtime_pcc_py.a"
    env = {"PCC_CI_STAGE1_MANIFEST": str(path), "PCC_CI_STAGE1_MANIFEST_SHA256": gate.digest(path),
           "PCC_TEST_COMPILER": str(compiler), "PCC_RUNTIME_ARCHIVE": str(runtime),
           "PCC_THREADED_RUNTIME_ARCHIVE": str(runtime), "PCC_RUNTIME_GC_STRESS_EVERY": "3",
           "PCC_GC_PRESSURE_NATIVE": "1", "PCC_TEST_NO_NATIVE_PROVISIONING": "1",
           "PCC_RUNTIME_BUILD_JOBS": "2", "PCC_WORKER_TREE_BUDGET_BYTES": "2147483648"}
    monkeypatch.setattr(os, "environ", env)
    monkeypatch.setattr(gate, "ROOT", root)
    monkeypatch.setattr(bootstrap_platform, "source_identity", lambda: source)
    monkeypatch.setattr(gate, "host_platform", lambda: "linux-x86_64")
    monkeypatch.setattr(gate, "workflow_lease", lambda: lease)
    held, calls = [], []
    @contextmanager
    def lock(lock_path, *, blocking):
        assert lock_path == root / "build/.pcc-performance.lock" and blocking is False
        if failure == "lock":
            raise OSError("lock busy")
        held.append(True)
        try:
            yield
        finally:
            held.pop()
    monkeypatch.setattr(file_lock, "exclusive_file_lock", lock)
    def run(command, **options):
        assert held and "timeout" not in options
        calls.append(command)
        if "pcc.frontends.python.owned_runtime_build" in command:
            assert command[command.index("--target") + 1] == manifest["target"]
            assert command[command.index("--runtime-dir") + 1] == str(root / "pcc/runtime")
            assert options["env"]["PCC_TEST_NO_NATIVE_PROVISIONING"] == "0"
            assert options["env"]["PCC_RUNTIME_BUILD_JOBS"] == "2"
            assert options["env"]["PCC_WORKER_TREE_BUDGET_BYTES"] == "2147483648"
            if failure == "build":
                raise subprocess.CalledProcessError(1, command)
            runtime.parent.mkdir()
            runtime.write_bytes(b"inert pressure archive")
            Path(str(runtime) + ".provenance.json").write_text('{"contract_fixture": true}')
            Path(str(runtime) + ".capi_syms").write_text("symbols\n")
        else:
            assert command[-1] == partition.PRESSURE_MODULE and "-x" in command
            assert options["env"] == env and env["PCC_TEST_NO_NATIVE_PROVISIONING"] == "1"
            if failure == "tests":
                raise subprocess.CalledProcessError(1, command)
            if failure == "mutate":
                runtime.write_bytes(b"changed archive")
    monkeypatch.setattr(gate.subprocess, "run", run)
    checks = []
    def verify(archive, *, threads):
        assert held and threads is True and archive == runtime
        checks.append(archive)
        if failure == "provenance":
            raise ValueError("wrong pressure build configuration")
        return runtime, {"contract_fixture": True}
    monkeypatch.setattr(runtime_fixture_provenance, "_verified_test_runtime_archive", verify)
    monkeypatch.setattr(gate, "summarize", lambda *a, **kw: {"status": "PASS", "selected_count": 6})
    monkeypatch.setattr(gate, "verify_pressure_routing", lambda *a: {"status": "PASS"})
    if failure:
        with pytest.raises((OSError, ValueError, subprocess.CalledProcessError)):
            gate.execute_pressure_phase(directory)
    else:
        gate.execute_pressure_phase(directory)
    receipt = json.loads((directory / "receipt.json").read_text())
    assert receipt["status"] == ("FAIL" if failure else "PASS")
    assert original_runtime.read_bytes() == original_bytes and not held
    if failure in ("lock", "build", "provenance"):
        assert receipt["tests"] == "UNRUN" and len(calls) <= 1
    if failure is None:
        assert len(calls) == len(checks) == 2
        assert receipt["build"] == receipt["tests"] == "PASS"
        assert receipt["provenance"] == {"contract_fixture": True}


@pytest.mark.xdist_group(name="ci_marker_pytest_fixtures")
def test_real_six_worker_runtime_phase_inventory_and_strict_union(tmp_path):
    """Execute miniature pytest bodies, not compiler/runtime/native proof."""
    corpus = tmp_path / "corpus"
    pressure = corpus / partition.PRESSURE_MODULE
    pressure.parent.mkdir(parents=True)
    (corpus / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "-n 6 --dist=loadgroup"\n'
        'markers = ["integration: native group"]\n')
    (corpus / "conftest.py").write_text(
        'import os\ndef pytest_collection_modifyitems(config, items):\n'
        '    if not os.environ.get("PCC_GC_PRESSURE_NATIVE"):\n'
        '        dropped = [i for i in items if "test_gc_pressure_native.py" in i.nodeid]\n'
        '        config.hook.pytest_deselected(items=dropped)\n'
        '        items[:] = [i for i in items if i not in dropped]\n')
    pressure.write_text(
        'import pytest\n@pytest.mark.integration\n@pytest.mark.xdist_group("gc_pressure_native")\n'
        '@pytest.mark.parametrize("backend,tracing", [(0,0),(1,0),(2,0),(3,0),(4,0),(4,1)])\n'
        'def test_pressure_executes_at_legal_gates_on_every_collector(backend, tracing):\n'
        '    assert (backend, tracing) in [(0,0),(1,0),(2,0),(3,0),(4,0),(4,1)]\n')
    for node in partition.BOOTSTRAP_NODES:
        file, name = node.split("::")
        path = corpus / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('import pytest\n@pytest.mark.integration\ndef ' + name + '():\n'
                        '    raise AssertionError("bootstrap owner must not execute here")\n')
    (corpus / "tests/test_ordinary.py").write_text(
        'import os, pytest\n@pytest.mark.integration\ndef test_body():\n'
        '    assert os.environ["PCC_RUNTIME_ARCHIVE"] == "ordinary.a"\n'
        '    assert not os.environ.get("PCC_RUNTIME_GC_STRESS_EVERY")\n')
    inventories = {}
    for phase in ("pressure", "ordinary"):
        out = tmp_path / phase
        out.mkdir()
        owner = "all" if phase == "pressure" else "remainder"
        env = dict(os.environ, PYTHONPATH=str(gate.ROOT), PCC_MARKER_REPORT_DIR=str(out),
                   PCC_MARKER_PARTITION=owner, PCC_MARKER_RUNTIME_PHASE=phase, PCC_RUNTIME_ARCHIVE="ordinary.a")
        for key in ("PYTEST_ADDOPTS", "PCC_GC_PRESSURE_NATIVE", "PCC_RUNTIME_GC_STRESS_EVERY"):
            env.pop(key, None)
        if phase == "pressure":
            env["PCC_GC_PRESSURE_NATIVE"] = "1"
        result = subprocess.run(gate.marker_command(out, "integration", owner, phase),
                                cwd=corpus, env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "created: 6/6 workers" in result.stdout and "LoadGroupScheduling" in result.stdout
        summary = gate.summarize(out, "integration", owner, phase)
        assert summary["status"] == "PASS" and summary["selected_count"] == (6 if phase == "pressure" else 1)
        inventories[phase] = json.loads((out / "inventory-gw0.json").read_text())
    combined = partition.combine_runtime_inventory(inventories["ordinary"], inventories["pressure"])
    partition.verify_union({"partition": "bootstrap", "selected": list(partition.BOOTSTRAP_NODES)}, combined)
    live = tmp_path / "pressure/live.jsonl"
    events = [json.loads(line) for line in live.read_text().splitlines()]
    index = next(index for index, row in enumerate(events) if row["event"] == "report" and row["when"] == "call")
    def write_events(rows):
        live.write_text("".join(json.dumps(row) + "\n" for row in rows))
    for changes in ({"outcome": "skipped"}, {"wasxfail": "expected failure"}):
        rows = deepcopy(events)
        rows[index].update(changes)
        write_events(rows)
        assert gate.summarize(tmp_path / "pressure", "integration", runtime_phase="pressure")["status"] == "PASS_WITH_GAPS"
    write_events(events[:index] + events[index + 1:])
    assert gate.summarize(tmp_path / "pressure", "integration", runtime_phase="pressure")["status"] == "PASS_WITH_GAPS"
    for rows in (events[:-1] + [events[index], events[-1]],
                 [row for row in events if row["event"] != "report"]):
        write_events(rows)
        with pytest.raises(ValueError):
            gate.summarize(tmp_path / "pressure", "integration", runtime_phase="pressure")
    write_events(events)
    inventory = tmp_path / "pressure/inventory-gw0.json"
    original = inventory.read_text()
    inventory.write_text("{}")
    with pytest.raises(ValueError, match="worker selection differs"):
        gate.summarize(tmp_path / "pressure", "integration", runtime_phase="pressure")
    inventory.write_text(original)
    inventory.unlink()
    with pytest.raises(ValueError, match="all six worker inventories"):
        gate.summarize(tmp_path / "pressure", "integration", runtime_phase="pressure")
