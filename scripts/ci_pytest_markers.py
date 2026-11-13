"""Run the complete tests tree with one explicit marker and an existing Stage1.

Both marker groups run on all four native CI platforms in the package workflow.
This is an execution entry, not evidence that the whole suite already passes.
The remainder owns one explicit pressure runtime build inside its existing
guard. The ordinary Stage1 bundle is never rebuilt or relabeled as pressure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ci_stage1_bundle import (
    PLATFORMS, host_platform, import_bundle, admit_in_place, workflow_lease,
)

from scripts.ci_bootstrap_partition import (
    BOOTSTRAP_NODES, PARTITIONS, partition_inventory, remaining_seconds, verify_union,
    PRESSURE_MODULE, RUNTIME_PHASES, runtime_phase_inventory, combine_runtime_inventory,
)

TIMEOUTS = {"not integration": 2400, "integration": 1800}
FROZEN_SOURCE = "pcc/ir/optimization/integer_fold_contract.py"
FROZEN_SHA256 = "8591c99dbd7eb97ae73b6151ecc1bab7def1d995a64c0b983e2200ba52df302a"
RUNTIME_PROOF_PREFIX = "PCC_RUNTIME_PHASE_PROOF_SHA256="


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def marker_command(directory, marker, partition="all", runtime_phase="all"):
    require(marker in TIMEOUTS, "unsupported marker expression")
    require(partition in PARTITIONS and (partition == "all" or marker == "integration"), "invalid marker partition")
    require(runtime_phase in RUNTIME_PHASES
            and (runtime_phase == "all" or marker == "integration"), "invalid runtime phase")
    paths = list(BOOTSTRAP_NODES) if partition == "bootstrap" else ["tests"]
    if runtime_phase == "pressure":
        require(partition == "all", "pressure phase must not collect bootstrap owners")
        paths = [PRESSURE_MODULE]
    return [sys.executable, "-m", "pytest", "-x", "-vv", "--tb=short",
            "-p", "scripts.pytest_live_report", "--pcc-live-report", str(directory / "live.jsonl"),
            "-p", "scripts.ci_pytest_markers", "--junitxml", str(directory / "junit.xml"),
            "--basetemp", str(directory / "tmp"), "-m", marker, *paths]


def execution_command(directory, marker, partition="all"):
    if partition == "remainder":
        require(marker == "integration", "runtime phases require integration")
        return [sys.executable, str(ROOT / "scripts/ci_pytest_markers.py"),
                "--runtime-phases", str(directory)]
    return marker_command(directory, marker, partition)


def watchdog_command(directory, marker, platform_id=None, *, partition="all", timeout=None):
    """Keep original POSIX budgets; selected tests retain their own build locks."""
    require(marker in TIMEOUTS, "unsupported marker expression")
    timeout = TIMEOUTS[marker] if timeout is None else timeout
    require(0 < timeout <= TIMEOUTS[marker], "marker budget exceeds original envelope")
    platform_id = platform_id or host_platform()
    require(platform_id != "windows-x86_64", "Windows requires the Job Object guard")
    options = (["--auto-tree-rss-ceiling-bytes", "4294967296", "--min-tree-rss-bytes", "2147483648",
                "--darwin-preflight-reserve-bytes", "536870912"] if platform_id == "macos-arm64"
               else ["--max-tree-rss-bytes", "17179869184"])
    return [sys.executable, str(ROOT / "scripts/run_process_tree_sample.py"),
            "--result", str(directory / "result.json"), "--samples", str(directory / "samples.tsv"),
            "--stdout", str(directory / "target.stdout"), "--stderr", str(directory / "target.stderr"),
            "--cwd", str(ROOT), "--timeout", str(timeout), *options, "--no-performance-lock",
            "--", *execution_command(directory, marker, partition)]


def run_guarded(directory, marker, env, platform_id, *, partition="all", timeout=None):
    """Use the existing native Windows Job Object or POSIX tree watchdog."""
    timeout = TIMEOUTS[marker] if timeout is None else timeout
    require(0 < timeout <= TIMEOUTS[marker], "marker budget exceeds original envelope")
    command = execution_command(directory, marker, partition)
    if platform_id == "windows-x86_64":
        from scripts.platform_process_watchdog import run
        record = {"guard": "platform_process_watchdog.run", "status": "RUNNING",
                  "requested_rss_limit": 17179869184, "requested_timeout": timeout,
                  "outer_performance_lock": False}
        save(directory / "result.json", record)
        started = time.monotonic()
        try:
            result = run(command, cwd=ROOT,
                         env=dict(env, PCC_WORKER_TREE_BUDGET_BYTES="17179869184"),
                         log_path=directory / "target.stdout", timeout=timeout,
                         rss_limit=17179869184)
            require(result["command"] == command
                    and 0 <= result["peak_tree_rss"] <= 17179869184,
                    "Windows guard command or tree budget differs")
            record.update(result, status="COMPLETE", returncode=0,
                          return_contract="normal return after child exit 0 and Job cleanup/final wait")
        except BaseException as error:
            # A raising guard returns no result; include its cleanup/final wait.
            record.update(status="FAIL", error=type(error).__name__ + ": " + str(error),
                          seconds=time.monotonic() - started)
            raise
        finally:
            save(directory / "result.json", record)
    else:
        from scripts.ci_worker_size_prior_gate import verify_posix_guard
        subprocess.run(watchdog_command(directory, marker, platform_id, partition=partition, timeout=timeout),
                       cwd=ROOT, env=env, check=True)
        verify_posix_guard(json.loads((directory / "result.json").read_text()), timeout)


def prepare_shared_input(root, manifest):
    """Copy only the original receipt; no symlink privileges or new Stage1."""
    relative = manifest["paths"].get("shared_receipt")
    if relative is None:
        return  # Darwin's integration fixture consumes the admitted compiler directly.
    common = root / "build/platform-bootstrap/shared"
    require(not common.exists() and not common.is_symlink(), "refuse an existing bootstrap input")
    common.mkdir(parents=True)
    shutil.copyfile(root / relative, common / "receipt.json")
    require(digest(common / "receipt.json") == manifest["files"][relative]["sha256"],
            "shared receipt copy differs")


def admitted_stage1(root, manifest_path, runtime_archive):
    """Recheck the explicit CI lease before a bootstrap fixture reuses Stage1.

    No receipt-only fixed-point claim: selected tests still execute Stage2/3.
    A missing/stale input is a hard failure, never permission to rebuild Stage1.
    """
    from scripts.bootstrap_platform import source_identity
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive

    root = Path(root).resolve(strict=True)
    require(digest(manifest_path) == os.environ.get("PCC_CI_STAGE1_MANIFEST_SHA256"),
            "Stage1 admission manifest changed")
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    require(manifest["producer"] == workflow_lease(), "Stage1 workflow lease differs")
    require(manifest["platform"] == host_platform(), "Stage1 host platform differs")
    source = source_identity()
    require(all(source.get(key) == manifest["source"].get(key)
                for key in ("commit", "source_sha256", "clean_commit"))
            and source["clean_commit"] is True, "Stage1 source differs")
    compiler = root / manifest["paths"]["compiler"]
    runtime = root / manifest["paths"]["runtime"]
    require(compiler.resolve().is_relative_to(root) and runtime.resolve().is_relative_to(root),
            "Stage1 fixture paths escape checkout")
    require(Path(runtime_archive).resolve(strict=True) == runtime.resolve(strict=True),
            "Stage1 fixture runtime differs")
    for path in (compiler, runtime):
        relative = path.relative_to(root).as_posix()
        require(digest(path) == manifest["files"][relative]["sha256"], "Stage1 fixture bytes differ")
    _verified_test_runtime_archive(runtime, threads=True)
    return compiler


def pytest_configure(config):
    """A small plugin records full marker inventory before repository gates."""
    directory = os.environ.get("PCC_MARKER_REPORT_DIR")
    if not directory:
        return
    import pytest
    partition = os.environ.get("PCC_MARKER_PARTITION", "all")
    phase = os.environ.get("PCC_MARKER_RUNTIME_PHASE", "all")
    require(partition in PARTITIONS and (partition == "all" or config.getoption("markexpr") == "integration"),
            "invalid marker partition")
    require(phase in RUNTIME_PHASES and (phase == "all" or config.getoption("markexpr") == "integration"),
            "invalid runtime phase")
    if not hasattr(config, "workerinput"):
        save(Path(directory) / "configuration.json", {
            "marker": config.getoption("markexpr"), "workers": config.getoption("numprocesses"),
            "dist": config.getoption("dist"),
            **({"partition": partition} if partition != "all" else {}),
            **({"runtime_phase": phase} if phase != "all" else {}),
        })

    class Inventory:
        @pytest.hookimpl(wrapper=True, tryfirst=True)
        def pytest_collection_modifyitems(self, items):
            marker = config.getoption("markexpr")
            require(marker in TIMEOUTS, "unexpected effective marker")
            expected_items = [item for item in items
                              if (item.get_closest_marker("integration") is not None) == (marker == "integration")]
            result = yield
            # xdist's loadgroup hook adds @group to nodeids. Compare the final
            # ids of the same original items, not pre-hook strings.
            expected = [item.nodeid for item in expected_items]
            original_expected = list(expected)
            expected, selected, transferred = partition_inventory(expected, [item.nodeid for item in items], partition)
            expected, selected, pressure_transferred = runtime_phase_inventory(expected, selected, phase)
            if partition == "remainder" or phase == "ordinary":
                selected_set = set(selected)
                moved = [item for item in items if item.nodeid not in selected_set]
                config.hook.pytest_deselected(items=moved)
                items[:] = [item for item in items if item.nodeid in selected_set]
            worker = getattr(config, "workerinput", {}).get("workerid", "controller")
            save(Path(directory) / ("inventory-" + worker + ".json"), {
                "marker": marker, "expected": expected, "selected": selected,
                "gated_out": sorted(set(expected) - set(selected)),
                "unexpected": sorted(set(selected) - set(expected)),
                **({"partition": partition, "original_expected": original_expected, "transferred": transferred}
                   if partition != "all" else {}),
                **({"runtime_phase": phase, "original_expected": original_expected,
                    "pressure_transferred": pressure_transferred} if phase != "all" else {}),
            })
            return result

    config.pluginmanager.register(Inventory(), "pcc-complete-marker-inventory")


def summarize(directory, marker, partition="all", runtime_phase="all"):
    inventories = [json.loads(path.read_text()) for path in sorted(directory.glob("inventory-gw*.json"))]
    require(len(inventories) == 6, "all six worker inventories are required")
    first = inventories[0]
    require(all(row == first for row in inventories), "worker selection differs")
    if partition != "all" or runtime_phase != "all":
        require(first.get("partition", "all") == partition, "inventory partition differs")
        require(first.get("runtime_phase", "all") == runtime_phase, "inventory runtime phase differs")
        expected, selected, transferred = partition_inventory(
            first["original_expected"], first["selected"] + first.get("transferred", [])
            + first.get("pressure_transferred", []), partition)
        expected, selected, pressure_transferred = runtime_phase_inventory(expected, selected, runtime_phase)
        require(expected == first["expected"] and selected == first["selected"]
                and transferred == first.get("transferred", [])
                and pressure_transferred == first.get("pressure_transferred", []), "partition inventory changed")
    configuration = json.loads((directory / "configuration.json").read_text())
    require(configuration == {"marker": marker, "workers": 6, "dist": "loadgroup",
                              **({"partition": partition} if partition != "all" else {}),
                              **({"runtime_phase": runtime_phase} if runtime_phase != "all" else {})}
            and first["marker"] == marker and not first["unexpected"], "marker/default scheduling changed")
    events = [json.loads(line) for line in (directory / "live.jsonl").read_text().splitlines()]
    start = next(row for row in events if row["event"] == "start")
    require(start["markexpr"] == marker and not any(start[key] for key in
            ("keyword", "ignore", "ignore_glob", "deselect", "collect_only", "override_ini", "noconftest", "confcutdir", "lf", "stepwise", "stepwise_skip")),
            "pytest selection was narrowed or collection substituted")
    require(events[-1]["event"] == "finish" and events[-1]["exitstatus"] == 0, "pytest did not complete successfully")
    reports = [row for row in events if row["event"] == "report"]
    selected = set(first["selected"])
    require(selected, "empty marker selection")
    require({row["nodeid"] for row in reports} == selected, "selected tests did not all execute")
    incomplete = []
    by_node = {}
    for row in reports:
        phases = by_node.setdefault(row["nodeid"], {})
        require(row["when"] not in phases, "duplicate phase report")
        phases[row["when"]] = row
    for node in sorted(selected):
        phases = by_node[node]
        if set(phases) != {"setup", "call", "teardown"} or any(
                row["outcome"] != "passed" or "wasxfail" in row for row in phases.values()):
            incomplete.append({"nodeid": node, "reports": list(phases.values())})
    return {"marker": marker, "status": "PASS_WITH_GAPS" if first["gated_out"] or incomplete else "PASS",
            "marker_count": len(first["expected"]), "selected_count": len(selected),
            "gated_out": first["gated_out"], "skipped_or_xfailed": incomplete,
            "claim": ("complete marker selection on this native platform, not all hardware gates or a whole-suite qualification"
                      if partition == "all" else "exact bootstrap owner component only; not complete integration coverage"
                      if partition == "bootstrap" else "integration remainder component; combined coverage needs the owner union proof")}


def pressure_build_environment(env):
    """Only the explicit owned build may provision; test routing stays native."""
    value = dict(env)
    for name in ("PCC_RUNTIME_ARCHIVE", "PCC_THREADED_RUNTIME_ARCHIVE",
                 "PCC_TEST_COMPILER", "PCC_PLATFORM_TEST_COMPILER", "PCC_CURRENT_PCC1",
                 "PCC_REQUIRE_CURRENT_PCC1", "PCC_TEST_COMPILER_ROUTED",
                 "PCC_TEST_COMPILER_LOG", "PCC_TEST_COMPILER_STRICT", "PCC_TEST_COMPILER_HASH_INPUTS"):
        value.pop(name, None)
    value.update(PCC_TEST_NO_NATIVE_PROVISIONING="0", PCC_NO_AUTO_PCC1="1",
                 PCC_RUNTIME_GC_STRESS_EVERY="3")
    # Worker widths, tree budget and deadline are inherited without alteration.
    return value


def verify_pressure_routing(path, compiler, runtime):
    """Require the fixture's one real native compile, not merely an attempt."""
    from tests.python.test_gc_pressure_native import PROGRAM

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    require(len(rows) == 2, "pressure compiler routing must contain one attempt and completion")
    attempt, complete = rows
    require(attempt.get("event") == "attempt" and complete.get("event") == "complete"
            and attempt.get("invocation") and attempt["invocation"] == complete.get("invocation")
            and complete.get("outcome") == "passed" and complete.get("returncode") == 0,
            "pressure native compile did not complete successfully")
    require(all(row.get("route") == "native" and row.get("kind") == "compile_python"
                and row.get("compiler") == str(compiler)
                and row.get("runtime_archive") == str(runtime)
                and row.get("test", "").startswith(PRESSURE_MODULE + "::") for row in rows),
            "pressure compiler/runtime/execution owner differs")
    inputs = attempt["inputs"]
    require(inputs == complete["inputs"] == complete["inputs_after"],
            "pressure compile inputs changed")
    require(inputs["compiler"] == {"path": str(compiler.resolve(strict=True)), "sha256": digest(compiler)}
            and inputs["runtime"] == {"path": str(runtime.resolve(strict=True)), "sha256": digest(runtime)},
            "pressure compiler or archive bytes differ")
    root = (path.parent / "tmp").resolve(strict=True)
    output = complete["output"]
    require(attempt["output_path"] == complete["output_path"] == output["path"],
            "pressure output identity differs")
    require(len(inputs["objects"]) == 1, "pressure guard object is missing or duplicated")
    records = [inputs["source"], inputs["objects"][0], output]
    paths = [Path(row["path"]).resolve(strict=True) for row in records]
    require(all(item.is_relative_to(root) for item in paths)
            and paths[0].parent == paths[1].parent == paths[2].parent
            and [item.name for item in paths] == ["pressure.py", "guards.o", "pressure"],
            "pressure source/object/output escapes its fixture directory")
    require(all(row["sha256"] == digest(item) for row, item in zip(records, paths)),
            "pressure source/object/output bytes changed")
    require(paths[0].read_text() == PROGRAM, "pressure program differs from the original fixture")
    return {"status": "PASS", "log_sha256": digest(path), "native_compiles": 1}


def execute_pressure_phase(directory):
    """Called only as a child of the original integration process-tree guard."""
    from scripts.bootstrap_platform import source_identity
    from scripts.file_lock import exclusive_file_lock
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive

    manifest_path = Path(os.environ["PCC_CI_STAGE1_MANIFEST"])
    manifest = json.loads(manifest_path.read_text())
    source = source_identity()
    runtime = directory / "runtime/libpy_runtime_pcc_py.a"
    compiler = ROOT / manifest["paths"]["compiler"]
    receipt = {"status": "RUNNING", "source": source, "producer": manifest["producer"],
               "platform": manifest["platform"], "manifest_sha256": digest(manifest_path),
               "runtime_build_owner": "host CPython pcc0, owned frontend/emitter/archive builder",
               "guard_helper_owner": "host CPython pcc0 C frontend and owned object emitter",
               "program_compile_owner": "admitted native pcc1, strict route",
               "program_execution_owner": "emitted native program, six original collector cases",
               "claim": "pressure runtime execution only; not Stage2/3 or fixed-point acceptance",
               "interval": 3, "build": "UNRUN", "tests": "UNRUN"}
    save(directory / "receipt.json", receipt)
    try:
        require(source == manifest["source"] and manifest["producer"] == workflow_lease()
                and manifest["platform"] == host_platform(), "pressure lease/source/platform differs")
        require(receipt["manifest_sha256"] == os.environ["PCC_CI_STAGE1_MANIFEST_SHA256"],
                "pressure admission manifest changed")
        require(os.environ["PCC_TEST_COMPILER"] == str(compiler)
                and os.environ["PCC_RUNTIME_ARCHIVE"] == str(runtime)
                and os.environ["PCC_THREADED_RUNTIME_ARCHIVE"] == str(runtime)
                and os.environ["PCC_RUNTIME_GC_STRESS_EVERY"] == "3"
                and os.environ["PCC_GC_PRESSURE_NATIVE"] == "1"
                and os.environ["PCC_TEST_NO_NATIVE_PROVISIONING"] == "1",
                "pressure test environment differs")
        require(not runtime.parent.exists(), "refuse an existing pressure archive directory")
        # This owner runs before any ordinary test. The pressure fixture has no
        # performance lock of its own; the builder retains its runtime lock.
        # Lock admission, build, pytest and cleanup share the one outer deadline.
        with exclusive_file_lock(ROOT / "build/.pcc-performance.lock", blocking=False):
            command = [sys.executable, "-m", "pcc.frontends.python.owned_runtime_build",
                       "--runtime-dir", str(ROOT / "pcc/runtime"), "--target", manifest["target"],
                       "--output", str(runtime)]
            save(directory / "command.json", {"build": command,
                 "tests": marker_command(directory, "integration", runtime_phase="pressure"),
                 "budget": "inside the original integration guard; no fresh allowance"})
            receipt["build"] = "RUNNING"
            save(directory / "receipt.json", receipt)
            subprocess.run(command, cwd=ROOT, env=pressure_build_environment(os.environ), check=True)
            archive, provenance = _verified_test_runtime_archive(runtime, threads=True)
            require(archive == runtime.resolve(), "pressure archive path differs")
            files = {str(path): digest(path) for path in (
                runtime, Path(str(runtime) + ".provenance.json"), Path(str(runtime) + ".capi_syms"))}
            receipt.update(build="PASS", archive_files=files, provenance=provenance, tests="RUNNING")
            save(directory / "receipt.json", receipt)
            subprocess.run(marker_command(directory, "integration", runtime_phase="pressure"),
                           cwd=ROOT, env=dict(os.environ), check=True)
            summary = summarize(directory, "integration", runtime_phase="pressure")
            require(summary["status"] == "PASS" and summary["selected_count"] == 6,
                    "pressure cases missing, gated, skipped or xfailed")
            routing = verify_pressure_routing(directory / "pcc1-routing.jsonl", compiler, runtime)
            _verified_test_runtime_archive(runtime, threads=True)
            require(all(digest(path) == value for path, value in files.items()),
                    "pressure archive bytes changed during tests")
            require(source_identity() == source and digest(manifest_path) == receipt["manifest_sha256"]
                    and all(digest(ROOT / name) == row["sha256"] for name, row in manifest["files"].items()),
                    "source or admitted ordinary Stage1 artifacts changed during pressure tests")
            receipt.update(status="PASS", tests="PASS", summary=summary, routing=routing)
    except BaseException as error:
        for phase in ("build", "tests"):
            if receipt[phase] == "RUNNING":
                receipt[phase] = "FAIL"
        receipt.update(status="FAIL", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        save(directory / "receipt.json", receipt)


def summarize_runtime_phases(directory):
    from scripts.bootstrap_platform import source_identity
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive

    pressure = summarize(directory / "pressure", "integration", runtime_phase="pressure")
    ordinary = summarize(directory / "ordinary", "integration", "remainder", "ordinary")
    require(pressure["status"] == ordinary["status"] == "PASS", "runtime phases have gaps")
    receipt = json.loads((directory / "pressure/receipt.json").read_text())
    manifest = json.loads(Path(os.environ["PCC_CI_STAGE1_MANIFEST"]).read_text())
    runtime = directory / "pressure/runtime/libpy_runtime_pcc_py.a"
    compiler = ROOT / manifest["paths"]["compiler"]
    require(receipt["status"] == receipt["build"] == receipt["tests"] == "PASS"
            and receipt["summary"] == pressure, "pressure prerequisite receipt did not pass")
    require(receipt["manifest_sha256"] == os.environ["PCC_CI_STAGE1_MANIFEST_SHA256"]
            and receipt["producer"] == workflow_lease() == manifest["producer"]
            and receipt["source"] == source_identity() == manifest["source"]
            and receipt["platform"] == host_platform() == manifest["platform"],
            "pressure receipt belongs to another input/run")
    paths = (runtime, Path(str(runtime) + ".provenance.json"), Path(str(runtime) + ".capi_syms"))
    require(receipt["archive_files"] == {str(path): digest(path) for path in paths},
            "pressure archive evidence changed")
    previous = os.environ.get("PCC_RUNTIME_GC_STRESS_EVERY")
    try:
        os.environ["PCC_RUNTIME_GC_STRESS_EVERY"] = "3"
        archive, provenance = _verified_test_runtime_archive(runtime, threads=True)
    finally:
        if previous is None:
            os.environ.pop("PCC_RUNTIME_GC_STRESS_EVERY", None)
        else:
            os.environ["PCC_RUNTIME_GC_STRESS_EVERY"] = previous
    require(archive == runtime.resolve(strict=True) and receipt["provenance"] == provenance
            and provenance == json.loads(paths[1].read_text())
            and receipt["provenance"]["target_triple"] == manifest["target"]
            and receipt["interval"] == 3
            and receipt["routing"] == verify_pressure_routing(
                directory / "pressure/pcc1-routing.jsonl", compiler, runtime),
            "pressure provenance or native routing evidence changed")
    combined = combine_runtime_inventory(
        json.loads((directory / "ordinary/inventory-gw0.json").read_text()),
        json.loads((directory / "pressure/inventory-gw0.json").read_text()))
    save(directory / "inventory-combined.json", combined)
    return {"status": "PASS", "marker": "integration", "runtime_phases": {
                "pressure": pressure, "ordinary": ordinary},
            "marker_count": len(combined["expected"]), "selected_count": len(combined["selected"]),
            "gated_out": [], "skipped_or_xfailed": [],
            "claim": "integration remainder union; bootstrap owner proof still required"}


def runtime_report_path(directory, name):
    path = directory / name
    require(path.is_file() and not path.is_symlink() and not path.parent.is_symlink()
            and path.resolve(strict=True).is_relative_to(directory.resolve(strict=True)),
            "runtime proof path escapes its report directory")
    return path


def runtime_proof_files(directory):
    """Retained report bytes only, never runtime/program payloads or extraction."""
    names = ["pressure/receipt.json", "pressure/command.json", "pressure/pcc1-routing.jsonl",
             "inventory-combined.json"]
    for phase in ("pressure", "ordinary"):
        names.extend(phase + "/" + name for name in ("configuration.json", "live.jsonl"))
        names.extend(phase + f"/inventory-gw{worker}.json" for worker in range(6))
    return {name: digest(runtime_report_path(directory, name)) for name in names}


def require_runtime_stdout_proof(path, expected):
    """The single final marker is small; never materialize the build/test log."""
    count, last = 0, ""
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.startswith(RUNTIME_PROOF_PREFIX):
                count += 1
            last = line
    require(count == 1 and last == RUNTIME_PROOF_PREFIX + expected + "\n",
            "runtime proof differs from the completed guarded output")


def summarize_completed_runtime_phases(directory, *, source, producer, platform, manifest_sha256):
    """Read the completed guarded proof without repeating deep artifact admission.

    The controller's final stdout line binds its receipt to the guarded command.
    The receipt binds the deep pressure result and all raw phase reports. The
    parent recomputes their strict summaries/union, not the archive extraction.
    """
    guard = json.loads(runtime_report_path(directory, "result.json").read_text())
    require(guard.get("status") == "COMPLETE" and guard.get("returncode") == 0,
            "runtime proof has no successful outer guard")
    raw = runtime_report_path(directory, "receipt.json").read_bytes()
    proof_sha256 = hashlib.sha256(raw).hexdigest()
    require_runtime_stdout_proof(runtime_report_path(directory, "target.stdout"), proof_sha256)
    receipt = json.loads(raw)
    require(receipt.get("status") == receipt.get("pressure") == receipt.get("ordinary") == "PASS",
            "runtime proof contains incomplete phases")
    require(receipt["proof_files"] == runtime_proof_files(directory), "runtime proof report bytes changed")
    pressure_receipt = json.loads((directory / "pressure/receipt.json").read_text())
    require(pressure_receipt["source"] == source and pressure_receipt["producer"] == producer
            and pressure_receipt["platform"] == platform
            and pressure_receipt["manifest_sha256"] == manifest_sha256
            and pressure_receipt["status"] == pressure_receipt["build"] == pressure_receipt["tests"] == "PASS"
            and pressure_receipt["interval"] == 3
            and pressure_receipt["routing"]["log_sha256"] == receipt["proof_files"]["pressure/pcc1-routing.jsonl"],
            "guarded pressure identity or completion differs")
    pressure = summarize(directory / "pressure", "integration", runtime_phase="pressure")
    ordinary = summarize(directory / "ordinary", "integration", "remainder", "ordinary")
    require(pressure["status"] == ordinary["status"] == "PASS"
            and receipt["runtime_phases"] == {"pressure": pressure, "ordinary": ordinary},
            "runtime proof phase reports differ")
    combined = combine_runtime_inventory(
        json.loads((directory / "ordinary/inventory-gw0.json").read_text()),
        json.loads((directory / "pressure/inventory-gw0.json").read_text()))
    require(combined == json.loads((directory / "inventory-combined.json").read_text())
            and receipt["marker_count"] == receipt["selected_count"] == len(combined["selected"]),
            "runtime proof inventory union differs")
    return {"status": "PASS", "marker": "integration", "runtime_phases": receipt["runtime_phases"],
            "marker_count": len(combined["expected"]), "selected_count": len(combined["selected"]),
            "gated_out": [], "skipped_or_xfailed": [], "guarded_runtime_proof_sha256": proof_sha256,
            "claim": "integration remainder union; bootstrap owner proof still required"}


def execute_runtime_phases(directory):
    """Pressure first; unrelated ordinary gates cannot prevent its attempt."""
    ordinary_env = dict(os.environ)
    admitted_stage1(ROOT, ordinary_env["PCC_CI_STAGE1_MANIFEST"], ordinary_env["PCC_RUNTIME_ARCHIVE"])
    require(ordinary_env.get("PCC_MARKER_PARTITION") == "remainder"
            and not ordinary_env.get("PCC_GC_PRESSURE_NATIVE")
            and ordinary_env.get("PCC_RUNTIME_GC_STRESS_EVERY", "0") in ("", "0"),
            "ordinary remainder must keep its original runtime configuration")
    receipt = {"status": "RUNNING", "pressure": "UNRUN", "ordinary": "UNRUN"}
    save(directory / "receipt.json", receipt)
    try:
        pressure_dir, ordinary_dir = directory / "pressure", directory / "ordinary"
        pressure_dir.mkdir()
        ordinary_dir.mkdir()
        runtime = pressure_dir / "runtime/libpy_runtime_pcc_py.a"
        pressure_env = dict(ordinary_env, PCC_RUNTIME_ARCHIVE=str(runtime),
                            PCC_THREADED_RUNTIME_ARCHIVE=str(runtime), PCC_RUNTIME_GC_STRESS_EVERY="3",
                            PCC_GC_PRESSURE_NATIVE="1", PCC_MARKER_PARTITION="all",
                            PCC_MARKER_RUNTIME_PHASE="pressure", PCC_MARKER_REPORT_DIR=str(pressure_dir),
                            PCC_TEST_COMPILER_HASH_INPUTS="1",
                            PCC_TEST_COMPILER_LOG=str(pressure_dir / "pcc1-routing.jsonl"))
        receipt["pressure"] = "RUNNING"
        save(directory / "receipt.json", receipt)
        subprocess.run([sys.executable, str(ROOT / "scripts/ci_pytest_markers.py"),
                        "--pressure-phase", str(pressure_dir)], cwd=ROOT, env=pressure_env, check=True)
        receipt.update(pressure="PASS", ordinary="RUNNING")
        save(directory / "receipt.json", receipt)
        ordinary_env.update(PCC_MARKER_RUNTIME_PHASE="ordinary", PCC_MARKER_REPORT_DIR=str(ordinary_dir),
                            PCC_TEST_COMPILER_LOG=str(ordinary_dir / "pcc1-routing.jsonl"))
        subprocess.run(marker_command(ordinary_dir, "integration", "remainder", "ordinary"),
                       cwd=ROOT, env=ordinary_env, check=True)
        receipt.update(summarize_runtime_phases(directory), ordinary="PASS")
        receipt["proof_files"] = runtime_proof_files(directory)
    except BaseException as error:
        for phase in ("pressure", "ordinary"):
            if receipt[phase] == "RUNNING":
                receipt[phase] = "FAIL"
        receipt.update(status="FAIL", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        save(directory / "receipt.json", receipt)
    # Only a fully completed, deeply validated controller reaches this line.
    # The existing guard captures it after all phase children have returned.
    print(RUNTIME_PROOF_PREFIX + digest(directory / "receipt.json"), flush=True)


def run(args):
    from scripts.ci_worker_size_prior_gate import environment
    from scripts.bootstrap_platform import source_identity, build_configuration
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive

    partition = getattr(args, "partition", "all")
    require(partition in PARTITIONS and (partition == "all" or args.marker == "integration"), "invalid marker partition")
    require(bool(getattr(args, "in_place", False)) == (partition == "bootstrap"), "only bootstrap owner admits in place")
    require(bool(getattr(args, "owner_report", None)) == (partition == "remainder"), "remainder requires its owner report")
    out = args.out.resolve()
    require(not out.exists(), "refuse stale marker receipts")
    out.mkdir(parents=True)
    receipt = {"status": "RUNNING", "marker": args.marker, "platform": args.platform,
               "partition": partition,
               "timeout_s": TIMEOUTS[args.marker], "outer_performance_lock": False,
               "memory_policy": "Darwin: auto 2-4 GiB, 512 MiB reserve; Linux/Windows: 16 GiB",
               "lock_owner": "selected tests retain their existing bootstrap/build locks"}
    try:
        require(not os.environ.get("PCC_PLATFORM_TEST_COMPILER"),
                "refuse inherited platform compiler override")
        require(not os.environ.get("PCC_GC_PRESSURE_NATIVE")
                and os.environ.get("PCC_RUNTIME_GC_STRESS_EVERY", "0") in ("", "0")
                and not os.environ.get("PCC_MARKER_RUNTIME_PHASE")
                and not os.environ.get("PCC_TEST_COMPILER_HASH_INPUTS"),
                "refuse inherited pressure configuration for the ordinary Stage1 runtime")
        require(args.platform == host_platform(), "runner platform differs from matrix")
        source = source_identity()
        admit = admit_in_place if partition == "bootstrap" else import_bundle
        manifest = admit(ROOT, args.bundle, source["commit"], args.platform, workflow_lease())
        require(source["source_sha256"] == manifest["source"]["source_sha256"], "checkout source digest differs")
        if partition != "bootstrap":
            require(args.research_source is not None and digest(args.research_source / FROZEN_SOURCE) == FROZEN_SHA256,
                    "required frozen 85b0 research source is missing or differs")
        runtime = ROOT / manifest["paths"]["runtime"]
        compiler = ROOT / manifest["paths"]["compiler"]
        env = environment()
        # environment() first rejects an inherited compiler override. Only the
        # already admitted same-run/source executable may then own API/CLI
        # compilation. Pure internal unit calls are still host-unit evidence;
        # direct C APIs need their own native artifact boundary.
        env.update(PCC_RUNTIME_ARCHIVE=str(runtime), PCC_THREADED_RUNTIME_ARCHIVE=str(runtime),
                   PCC_CURRENT_PCC1=str(compiler), PCC_REQUIRE_CURRENT_PCC1="1", PCC_NO_AUTO_PCC1="1",
                   PCC_TEST_COMPILER=str(compiler), PCC_TEST_COMPILER_STRICT="1",
                   PCC_PLATFORM_TEST_COMPILER=str(compiler),
                   PCC_TEST_COMPILER_LOG=str(out / "pytest" / "pcc1-routing.jsonl"),
                   PCC_TEST_NO_NATIVE_PROVISIONING="1", UV_PYTHON_DOWNLOADS="never",
                   PCC_RUN_CPYTHON_REFERENCE="1",
                   PCC_CI_STAGE1_MANIFEST=str((args.bundle / "manifest.json").resolve()),
                   PCC_CI_STAGE1_MANIFEST_SHA256=digest(args.bundle / "manifest.json"),
                   PCC_MARKER_REPORT_DIR=str(out / "pytest"), PCC_MARKER_PARTITION=partition)
        if args.research_source is not None:
            env.update(PCC_RESEARCH_SOURCE=str(args.research_source.resolve()), PCC_RESEARCH_BASELINE_SOURCE=str(ROOT))
        os.environ.update(env)
        _verified_test_runtime_archive(runtime, threads=True)
        shared_path = manifest["paths"].get("shared_receipt")
        if shared_path is not None:
            shared = json.loads((ROOT / shared_path).read_text())
            require(shared["source"] == source and shared["configuration"] == build_configuration(manifest["target"]),
                    "shared bootstrap inputs differ")
        prepare_shared_input(ROOT, manifest)
        receipt.update(source=source, producer=manifest["producer"], bundle=manifest,
                       manifest_sha256=digest(args.bundle / "manifest.json"))
        timeout = TIMEOUTS[args.marker]
        owner = None
        if partition == "remainder":
            owner = json.loads((args.owner_report / "receipt.json").read_text())
            require(digest(args.owner_report / "pytest/result.json") == owner.get("guard_result_sha256"),
                    "owner guard receipt bytes changed")
            timeout = remaining_seconds(owner, source=source, producer=manifest["producer"],
                                        platform=args.platform, manifest_sha256=receipt["manifest_sha256"],
                                        guard=json.loads((args.owner_report / "pytest/result.json").read_text()))
            receipt.update(timeout_s=timeout, owner_status=owner["status"],
                           owner_guard_elapsed_s=owner["guard_elapsed_s"])
        save(out / "receipt.json", receipt)
        directory = out / "pytest"
        directory.mkdir()
        save(directory / "command.json", {"argv": execution_command(directory, args.marker, partition),
                                          "timeout": timeout, "outer_performance_lock": False})
        started = time.monotonic()
        try:
            run_guarded(directory, args.marker, env, args.platform, partition=partition, timeout=timeout)
        finally:
            receipt["guard_elapsed_s"] = time.monotonic() - started
            if (directory / "result.json").is_file():
                receipt["guard_result_sha256"] = digest(directory / "result.json")
        receipt.update(summarize_completed_runtime_phases(
                           directory, source=source, producer=manifest["producer"], platform=args.platform,
                           manifest_sha256=receipt["manifest_sha256"]) if partition == "remainder"
                       else summarize(directory, args.marker, partition))
        require(receipt["status"] == "PASS", "strict selection has missing, gated, skipped or xfailed cases")
        if partition == "remainder":
            # Recompute strict summaries from immutable same-run artifacts; do
            # not trust a PASS label or a count in place of actual phase reports.
            require(summarize(args.owner_report / "pytest", "integration", "bootstrap")["status"] == "PASS"
                    and owner["status"] == "PASS", "bootstrap owner did not pass")
            owner_inventory = json.loads((args.owner_report / "pytest/inventory-gw0.json").read_text())
            remainder_inventory = json.loads((directory / "inventory-combined.json").read_text())
            verify_union(owner_inventory, remainder_inventory)
            receipt.update(combined_integration_status="PASS",
                           original_integration_count=len(remainder_inventory["original_expected"]))
        require(source_identity() == source, "source changed during tests")
        require(digest(args.bundle / "manifest.json") == env["PCC_CI_STAGE1_MANIFEST_SHA256"],
                "admission manifest changed during tests")
        require(all(digest(ROOT / name) == row["sha256"] for name, row in manifest["files"].items()),
                "admitted artifacts changed during tests")
    except BaseException as error:
        receipt.update(status="FAIL", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        save(out / "receipt.json", receipt)
    return receipt


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--runtime-phases", "--pressure-phase"):
        require(len(argv) == 2, "internal runtime owner requires exactly one report directory")
        directory = Path(argv[1]).resolve(strict=True)
        if argv[0] == "--runtime-phases":
            execute_runtime_phases(directory)
        else:
            execute_pressure_phase(directory)
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--marker", choices=tuple(TIMEOUTS), required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--platform", choices=tuple(PLATFORMS), required=True)
    parser.add_argument("--research-source", type=Path)
    parser.add_argument("--partition", choices=PARTITIONS, default="all")
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--owner-report", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    run(parser.parse_args(argv))


if __name__ == "__main__":
    main()
