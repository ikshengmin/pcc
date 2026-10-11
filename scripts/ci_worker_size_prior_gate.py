"""Bounded premerge phases using the existing platform watchdogs and pytest."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PROCESS_TEST = "tests/python/test_worker_size_prior_processes.py"
CLOSURE_TEST = "tests/integration/test_worker_size_prior_closed_world.py"
EXIT_TEST = "tests/python/test_owned_windows_exit.py"
EXIT_INPUTS = (EXIT_TEST, "tests/c/test_owned_linux_c_exports.py",
               "tests/c/fixtures/owned_linux_c_exports/exit_lifecycle.c",
               "tests/c/fixtures/owned_linux_c_exports/immediate_bypass.c")
ARM_TRANSPORT_TEST = "tests/python/test_linux_aarch64_worker_transport.py"
ARM_TRANSPORT_NATIVE_TEST = "tests/python/test_linux_aarch64_transport_native.py"
ARM_TRANSPORT_INPUTS = (ARM_TRANSPORT_TEST, ARM_TRANSPORT_NATIVE_TEST)
ELF_FORMAT_TEST = "tests/python/test_elf_x86_64.py"
ELF_STAGING_TEST = "tests/python/test_owned_elf_staging.py"
ELF_OWNER_TEST = "tests/python/test_pipeline_self_backend_link_owner.py"
ELF_OWNER_NODE = ELF_OWNER_TEST + "::test_linux_pcc_link_route_uses_owned_elf_driver_and_internal_assembly"
ELF_INPUTS = (ELF_FORMAT_TEST, ELF_STAGING_TEST, ELF_OWNER_TEST,
              "pcc/backend/owned_elf_inputs.py", "pcc/backend/elf_x86_64.py")
REGALLOC_TEST = "tests/c/test_self_backend_aarch64_regalloc.py"
CALLEE_SAVED_TEST = "tests/c/test_self_backend_aarch64_callee_saved.py"
BRANCH_PARITY_TEST = "tests/python/test_aarch64_branch_layout_parity.py"
REGALLOC_INPUTS = (REGALLOC_TEST, CALLEE_SAVED_TEST, BRANCH_PARITY_TEST,
                  "tests/aarch64_regalloc_scan_reference.py",
                  "tests/python/test_precise_stackmap_abi.py",
                  "tests/python/test_unsafe_syscall6.py")
REGALLOC_MODES = ("default", "function", "local")
REGALLOC_SHAPES = ("safe", "call", "loop", "empty", "syscall", "inline-error",
                  "madd", "reload", "vararg", "invalid-low", "invalid-high")
REGALLOC_HOST_NODES = (
    *(REGALLOC_TEST + "::test_aarch64_regalloc_reuses_function_facts_without_changing_state["
      + calls + "-" + mode + "-" + shape + "]"
      for calls in ("0", "1") for mode in REGALLOC_MODES for shape in REGALLOC_SHAPES),
    *(REGALLOC_TEST + "::test_aarch64_regalloc_scan_reuse_preserves_syscall_failure[" + mode + "]"
      for mode in REGALLOC_MODES),
    *(CALLEE_SAVED_TEST + "::test_scan_reuse_preserves_callee_saved_assembly_and_frames[" + mode + "]"
      for mode in REGALLOC_MODES),
    *(BRANCH_PARITY_TEST + "::test_regalloc_scan_reuse_preserves_assembly_native_sections_and_stackmaps["
      + mode + "-" + shape + "]" for mode in REGALLOC_MODES for shape in ("branches", "root-reloads")),
    CALLEE_SAVED_TEST + "::test_callee_saved_registers_are_saved_and_restored_on_every_exit",
    CALLEE_SAVED_TEST + "::test_mode_off_emits_no_callee_saved_registers",
)
REGALLOC_NATIVE_NODES = tuple(
    REGALLOC_TEST + "::test_aarch64_call_result_executes_through_indexed_emission[" + mode + "]"
    for mode in REGALLOC_MODES
)
HOST_FILES = ("tests/python/test_worker_size_priors.py", PROCESS_TEST,
              "tests/test_ci_worker_size_prior_gate.py", CLOSURE_TEST,
              "tests/python/test_worker_guard_cadence.py",
              "tests/c/test_self_backend_function_body_lines.py",
              "tests/python/test_compiled_default_pass_tier.py",
              "tests/python/test_owned_mem2reg_frontiers.py",
              "tests/c/test_self_backend_text_index.py",
              "tests/python/test_host_indexed_process_split.py",
              "tests/python/test_dynamic_handoff_slots.py")
# Updated with the exact frozen pure-control inventory; real tests remain separate.
HOST_COUNTS = (80, 3, 145, 0, 4, 29, 25, 7, 11, 20, 21)
PHASES = ("preflight", "stage1", "pcc1")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def source_identity():
    from scripts.bootstrap_platform import source_identity as identity
    value = identity()
    expected_python = (3, 15, 0, "candidate", 1) if sys.platform == "darwin" else (3, 13)
    assert tuple(sys.version_info[:len(expected_python)]) == expected_python, "use the platform baseline Python"
    import pytest
    import xdist
    assert (pytest.__version__, xdist.__version__) == ("9.0.3", "3.8.0"), "use the frozen development lock"
    value["test_versions"] = {"pytest": pytest.__version__, "xdist": xdist.__version__}
    value["test_inputs"] = {name: sha(ROOT / name) for name in (*HOST_FILES, *EXIT_INPUTS, *ARM_TRANSPORT_INPUTS, *REGALLOC_INPUTS, *ELF_INPUTS,
                           "tests/owned_ir_validation.py",
                           "tests/fixtures/native/worker_size_priors.py", "conftest.py")}
    value["python"] = {"executable": sys.executable, "sha256": sha(sys.executable),
                       "version": sys.version}
    return value


def environment():
    assert not os.environ.get("PYTEST_ADDOPTS"), "default pytest configuration must be unmodified"
    assert os.environ.get("PCC_PYTEST_AUTO_CLEAN", "0").lower() in ("", "0", "false", "off"), "qualification cannot delete source outputs automatically"
    assert os.environ.get("PCC_WITH_THREADS", "0") in ("", "0"), "preserve the baseline single-thread runtime configuration"
    assert os.environ.get("PCC_REFCOUNT_KIND", "atomic") in ("", "atomic")
    assert not os.environ.get("PCC_TEST_COMPILER"), "use the explicit pcc0/pcc1 fixture parameter"
    assert not os.environ.get("PCC_BOOTSTRAP_EXTERNAL_MEMORY_GUARD"), "bootstrap owns its Stage1 guard"
    assert not os.environ.get("PCC_BOOTSTRAP_MAX_TREE_RSS_BYTES"), "explicit cap overrides the required automatic Mac budget"
    value = dict(os.environ)
    if sys.platform == "darwin":
        assert not value.get("UV_PROJECT_ENVIRONMENT"), "use the ordinary project environment"
        assert sha(ROOT / ".venv/bin/python") == sha(sys.executable)
        value.update(UV_PYTHON=sys.executable, UV_NO_SYNC="1")
    value.update(PCC_NO_AUTO_PCC1="1", PCC_TEST_COMPILER_STRICT="1",
                 PCC_PYTEST_AUTO_CLEAN="0", PCC_WITH_THREADS="0", PCC_REFCOUNT_KIND="atomic",
                 PCC_PY_FRONTEND_IR_CACHE="0", PCC_SELF_BACKEND_OBJECT_CACHE="0",
                 PCC_RUNTIME_CC="pcc", PCC_RUNTIME_HIGH="py", PCC_RUNTIME_BUILD="owned",
                 PCC_SELF_LINK="pcc", PCC_SELF_OBJ="pcc", PCC_IR_TO_OBJ_EMITTER="pcc",
                 PCC_PYTHON_IR_PASSES="off", PCC_GC_BACKEND="0")
    return value


def guarded(command, directory, timeout, env):
    """One existing guard owns the tree and performance lock for this command."""
    directory.mkdir(parents=True, exist_ok=False)
    save(directory / "command.json", {"argv": command, "timeout": timeout})
    if sys.platform == "win32":
        from scripts.file_lock import exclusive_file_lock
        from scripts.platform_process_watchdog import run
        child_env = dict(env, PCC_WORKER_TREE_BUDGET_BYTES="17179869184")
        with exclusive_file_lock(ROOT / "build/.pcc-performance.lock", blocking=False):
            result = run(command, cwd=ROOT, env=child_env, log_path=directory / "target.stdout",
                         timeout=timeout, rss_limit=17179869184)
        assert result["command"] == list(command) and 0 <= result["peak_tree_rss"] <= 17179869184
        # The existing watchdog returns only after exit 0 and finally wait();
        # it exposes no independent post-cleanup PID inventory. Keep that limit.
        save(directory / "result.json", {"guard": "platform_process_watchdog.run",
             "return_contract": "normal return after child exit 0 and finally wait",
             "requested_rss_limit": 17179869184, "requested_timeout": timeout, **result})
    else:
        options = (["--auto-tree-rss-ceiling-bytes", "4294967296", "--min-tree-rss-bytes", "2147483648",
                    "--darwin-preflight-reserve-bytes", "536870912"] if sys.platform == "darwin"
                   else ["--max-tree-rss-bytes", "17179869184"])
        argv = [sys.executable, str(ROOT / "scripts/run_process_tree_sample.py"),
                "--result", str(directory / "result.json"), "--samples", str(directory / "samples.tsv"),
                "--stdout", str(directory / "target.stdout"), "--stderr", str(directory / "target.stderr"),
                "--cwd", str(ROOT), "--timeout", str(timeout), *options, "--", *command]
        subprocess.run(argv, cwd=ROOT, env=env, check=True)
        verify_posix_guard(json.loads((directory / "result.json").read_text()), timeout)


def verify_posix_guard(result, timeout):
    assert result["status"] == "COMPLETE" and result["returncode"] == 0
    assert result["timeout_s"] == timeout
    cap = result["max_tree_rss_bytes"]
    if sys.platform == "darwin":
        assert 2147483648 <= cap <= 4294967296
        assert result["darwin_preflight_reserve_bytes"] == 536870912
    else:
        assert cap == 17179869184
    assert 0 <= result["peak_tree_rss_bytes"] <= cap
    assert not result.get("post_exit_cleanup_pids"), "worker exited before its descendants"
    # terminal_processes is the last live sample, not a post-cleanup inventory.
    return {"max_tree_rss_bytes": cap, "peak_tree_rss_bytes": result["peak_tree_rss_bytes"],
            "timeout_s": timeout, "status": result["status"], "returncode": result["returncode"]}


def pytest_command(directory, nodes, integration=False):
    # In particular, never clear addopts or override -n/--dist.
    command = [sys.executable, "-m", "pytest", "-x", "-vv", "--tb=short",
               "-p", "scripts.pytest_live_report", "--pcc-live-report", str(directory / "live.jsonl"),
               "--junitxml", str(directory / "junit.xml"), "--basetemp", str(directory / "tests")]
    return command + (["-m", "integration"] if integration else []) + list(nodes)


def verify_pytest(directory, expected, file_counts=None):
    rows = [json.loads(line) for line in (directory / "live.jsonl").read_text().splitlines()]
    starts = [r for r in rows if r["event"] == "start"]
    assert len(starts) == 1 and not starts[0]["collect_only"] and not starts[0]["override_ini"]
    collections = [r["nodeids"] for r in rows if r["event"] == "collected"]
    assert len(collections) == 1 and len(set(collections[0])) == len(collections[0])
    nodes = collections[0]
    if file_counts is None:
        assert nodes == list(expected), (nodes, expected)
    else:
        assert len(nodes) == sum(file_counts)
        assert all(sum(node.startswith(path + "::") for node in nodes) == count
                   for path, count in zip(expected, file_counts))
        assert all(any(node.startswith(path + "::") for path in expected) for node in nodes)
    reports = [r for r in rows if r["event"] == "report"]
    assert len(reports) == 3 * len(nodes)
    for node in nodes:
        actual = [r for r in reports if r["nodeid"] == node]
        assert [r["when"] for r in actual] == ["setup", "call", "teardown"]
        assert all(r["outcome"] == "passed" and "wasxfail" not in r for r in actual)
    finish = [r for r in rows if r["event"] == "finish"]
    assert len(finish) == 1 and finish[0]["exitstatus"] == 0
    assert finish[0]["testsfailed"] == 0 and finish[0]["testscollected"] == len(nodes)
    lines = (directory / "target.stdout").read_text().splitlines()
    assert "created: 6/6 workers" in lines
    assert "scheduling tests via LoadGroupScheduling" in lines
    noun = "item" if len(nodes) == 1 else "items"
    assert "6 workers [" + str(len(nodes)) + " " + noun + "]" in lines
    return nodes


def run_pytest(out, name, nodes, env, *, integration=False, counts=None):
    directory = out / name
    guarded(pytest_command(directory, nodes, integration), directory, 300, env)
    return verify_pytest(directory, nodes, counts)


def runtime_identity(archive):
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive
    checked, manifest = _verified_test_runtime_archive(archive, threads=False)
    assert checked == archive.resolve()
    return {"path": str(checked), "target": manifest["target_triple"],
            "config": {"threads": False, "refcount": "atomic"},
            "hashes": {suffix: sha(str(checked) + suffix)
                       for suffix in ("", ".provenance.json", ".capi_syms")}}


def native_gate(out, owner, env):
    node = PROCESS_TEST + "::test_size_prior_native_processes_five_collectors[" + owner + "]"
    name = "native-" + owner
    run_pytest(out, name, [node], env, integration=True)
    files = list((out / name).rglob("native-worker-size-priors.json"))
    assert len(files) == 1
    receipt = json.loads(files[0].read_text())
    assert receipt["status"] == "PASS" and receipt["compiler_parameter"] == owner
    assert receipt["source_sha256"] == sha(ROOT / "tests/fixtures/native/worker_size_priors.py")
    assert receipt["runtime_archive_sha256"] == sha(env["PCC_RUNTIME_ARCHIVE"])
    assert len(receipt["executions"]) == 10
    assert {(row["case"], row["requested_collector"]) for row in receipt["executions"]} == {
        (case, gc) for case in ("width", "growth") for gc in range(5)}
    from scripts.verify_nolibpython import linked_libraries
    binary = files[0].parent / ("prior-worker.exe" if sys.platform == "win32" else "prior-worker.out")
    assert sha(binary) == receipt["binary_sha256"]
    assert not any("python" in name.lower() or "llvm" in name.lower()
                   for name in linked_libraries(binary.read_bytes()))
    return receipt["binary_sha256"]


def admission_metrics(text, compile_wall):
    """Reduce original coordinator events; worker time is never added to wall."""
    assert compile_wall > 0
    active, cancelled, exports, units, completed = {}, set(), {}, {}, set()
    area, exclusive_intervals, seen, events = 0.0, [], set(), []
    counts = {"start": 0, "calibrate": 0, "cancel": 0, "retire": 0, "exclusive": 0}
    for line in text.splitlines():
        if not line.startswith("pcc frontend admission ") or line in seen:
            continue
        seen.add(line)
        row = {}
        for key, value in re.findall(r'(\w+)=(.*?)(?= \w+=|$)', line):
            try:
                row[key] = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                row[key] = value
        offset = row.get("mapping_offset", 0)
        if offset:
            assert events, "orphan mapping continuation"
            first = events[-1]
            repeated = set(row) - {"indices", "modules", "mapping_offset", "monotonic_s"}
            assert all(row[key] == first[key] for key in repeated), "mismatched mapping continuation"
            assert offset == max(len(first["indices"]), len(first.get("modules", []))), "missing or duplicated mapping page"
            first["indices"].extend(row["indices"])
            if "modules" in row:
                first["modules"].extend(row["modules"])
        else:
            events.append(row)
    for row in events:
        if "module_count" in row:
            assert len(row["modules"]) == row["module_count"]
            if row["phase"] == "preload-delta":
                assert row["indices"] == []  # Existing module-only full-graph diagnostics.
            else:
                assert len(row["indices"]) == row["module_count"]
        event, stamp = row["event"], row["monotonic_s"]
        key, task = (row["phase"], row["task"], row["pid"]), (row["phase"], row["task"])
        indices = tuple(row["indices"])
        if event in counts:
            counts[event] += 1
        if event in ("start", "calibrate"):
            assert key not in active
            active[key] = (stamp, stamp if event == "calibrate" or task in cancelled else None)
            if row["phase"] == "export":
                assert len(row["inputs"]) == 6
                exports[indices] = row["inputs"][0]
            if row["phase"] in ("codegen", "indexed-frontend"):
                assert len(row["inputs"]) == 6
                units[indices] = row["inputs"][0]
        elif event == "exclusive":
            start, exclusive = active[key]
            assert exclusive is None
            active[key] = (start, stamp)
        elif event in ("retire", "cancel", "failed", "unverified-retire"):
            start, exclusive = active.pop(key)
            assert stamp >= start
            area += stamp - start
            if exclusive is not None:
                exclusive_intervals.append((exclusive, stamp))
            if event == "cancel":
                cancelled.add(task)
            elif event == "retire" and row["phase"] in ("codegen", "indexed-backend"):
                completed.update(indices)
        else:
            raise AssertionError("unknown admission event: " + str(event))
    assert exports and units, "missing actual source/admission evidence"
    total_bytes = sum(exports.values())
    completed_bytes = sum(size for indices, size in units.items() if set(indices) <= completed)
    exclusive_wall, end = 0.0, -1.0
    for start, stop in sorted(exclusive_intervals):
        exclusive_wall += max(0.0, stop - max(start, end))
        end = max(end, stop)
    return {"compile_wall_s": compile_wall, "worker_seconds": area,
            "average_workers": area / compile_wall,
            "exclusive_wall_s": exclusive_wall, "events": counts,
            "source_bytes_total": total_bytes, "source_bytes_complete": completed_bytes,
            "source_byte_fraction": completed_bytes / total_bytes,
            "completed_modules": len(completed),
            "planned_modules": len({index for indices in exports for index in indices}),
            "open_attempts": len(active),
            "scope": "original event durations; incomplete attempts excluded from area until terminal; no CPU inference"}


def run(out, phase):
    assert phase in PHASES
    assert ((sys.platform == "darwin" and platform.machine().lower() == "arm64")
            or (sys.platform.startswith("linux") and platform.machine().lower() in ("x86_64", "aarch64", "arm64"))
            or (sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64")))
    out = out.resolve()
    if phase == "preflight":
        assert not out.exists(), "preflight requires a fresh output root"
    receipt_path = out / (phase + ".json")
    assert not receipt_path.exists(), "refuse stale phase receipt"
    source = source_identity()
    env = environment()
    archive = out / "runtime/libpy_runtime_pcc_py.a"
    receipt = {"status": "RUNNING", "phase": phase, "source": source}
    save(receipt_path, receipt)
    try:
        if phase == "preflight":
            run_pytest(out, "default-xdist", HOST_FILES, env, counts=HOST_COUNTS)
            build_env = dict(env)
            build_env.pop("PCC_TEST_NO_NATIVE_PROVISIONING", None)
            build_env.pop("PCC_RUNTIME_ARCHIVE", None)
            guarded([sys.executable, "-m", "pcc.frontends.python.owned_runtime_build", "--output", str(archive)],
                    out / "runtime-build", 1200, build_env)
            receipt["runtime"] = runtime_identity(archive)
            env.update(PCC_RUNTIME_ARCHIVE=str(archive), PCC_TEST_NO_NATIVE_PROVISIONING="1")
            if sys.platform == "darwin":
                # Each exact node retains the six-worker default and the 300s
                # tree watchdog. Owned executable probes also have 10s bounds.
                run_pytest(out, "aarch64-regalloc-host", REGALLOC_HOST_NODES, env)
                run_pytest(out, "aarch64-regalloc-native", REGALLOC_NATIVE_NODES, env)
            if sys.platform == "win32":
                run_pytest(out, "windows-exit-host", [EXIT_TEST], env, counts=(8,))
                run_pytest(out, "windows-exit-native", [EXIT_TEST], env, integration=True, counts=(6,))
            elif sys.platform.startswith("linux") and platform.machine().lower() in ("aarch64", "arm64"):
                run_pytest(out, "linux-arm-transport-host", [ARM_TRANSPORT_TEST], env, counts=(7,))
                run_pytest(out, "linux-arm-transport-routes", [ARM_TRANSPORT_TEST], env, integration=True, counts=(8,))
                run_pytest(out, "linux-arm-transport-native", [ARM_TRANSPORT_NATIVE_TEST
                           + "::test_linux_aarch64_transport_executes_tls_varargs_and_managed_reload"],
                           env, integration=True)
            if sys.platform.startswith("linux"):
                run_pytest(out, "linux-elf-format", [ELF_FORMAT_TEST], env, counts=(31,))
                # Match the staging test's actual pcc_gate predicate exactly.
                # ARM deselects two x86 executable probes at collection; skips
                # in any selected case still fail verify_pytest.
                staging_count = 69 if platform.machine() in ("x86_64", "amd64") else 67
                run_pytest(out, "linux-elf-staging", [ELF_STAGING_TEST], env,
                           counts=(staging_count,))
                # The whole owner file also contains unmarked Darwin native
                # execution. Select only the affected Linux dispatch contract.
                run_pytest(out, "linux-elf-owner", [ELF_OWNER_NODE], env)
            native_gate(out, "pcc0", env)
            run_pytest(out, "strict-closure", [CLOSURE_TEST + "::test_worker_size_prior_modules_strict_target_emission"],
                       env, integration=True)
        else:
            previous = json.loads((out / "preflight.json").read_text())
            assert previous["status"] == "PASS" and previous["source"] == source
            assert runtime_identity(archive) == previous["runtime"]
            receipt["runtime"] = previous["runtime"]
            env.update(PCC_RUNTIME_ARCHIVE=str(archive), PCC_TEST_NO_NATIVE_PROVISIONING="1",
                       PCC_PY_FRONTEND_WORKER_TIMING="1")
            if phase == "stage1":
                destination = ROOT / "build" / ("bootstrap" if sys.platform == "darwin" else "platform-qualification")
                assert not destination.exists()
                if sys.platform == "darwin":
                    env.update(PCC_HOST_INDEXED_PROCESS_SPLIT="1", PCC_BOOTSTRAP_STAGE_TIMEOUT="2400",
                               PCC_BOOTSTRAP_AUTO_TREE_RSS_CEILING_BYTES="4294967296",
                               PCC_BOOTSTRAP_MIN_TREE_RSS_BYTES="2147483648",
                               PCC_BOOTSTRAP_HOST_MEMORY_RESERVE_BYTES="536870912",
                               PCC_BOOTSTRAP_PROFILE_DIR=str(out / "profile"), PCC_BOOTSTRAP_KEEP_PROCESS_SAMPLES="1")
                    command = [sys.executable, "scripts/bootstrap.py", "--backend", "self", "--stage", "1", "--out-dir", str(destination)]
                    binary = destination / "pcc1"
                else:
                    env["PCC_HOST_INDEXED_PROCESS_SPLIT"] = "0"
                    # Preserve the existing five-GC Stage2/3 matrix after its one
                    # shared Stage1; do not launch a second Stage1 for this gate.
                    command = [sys.executable, "scripts/bootstrap_platform.py", "--gc", "all", "--stage", "3",
                               "--out-dir", str(destination), "--runtime-archive", str(archive),
                               "--timeout", "2400", "--rss-limit", "17179869184", "--cpu-budget", "4"]
                    binary = destination / "shared" / ("pcc1.exe" if sys.platform == "win32" else "pcc1")
                # The original bootstrap owns its own lock and tree watchdog.
                # Do not hold an outer copy of that same lock around this call.
                started = time.monotonic()
                with (out / "stage1.log").open("wb") as log:
                    result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                entry_wall = time.monotonic() - started
                receipt.update(command=command, returncode=result.returncode, entry_wall_s=entry_wall)
                if result.returncode:
                    originals = (list(destination.glob("stage1.process.*/target.stderr"))
                                 if sys.platform == "darwin" else [destination / "shared/stage1.log"])
                    try:
                        if len(originals) == 1 and originals[0].is_file():
                            receipt["partial_metrics"] = admission_metrics(originals[0].read_text(), entry_wall)
                            receipt["partial_metrics"]["scope"] = "closed-attempt lower bounds divided by bootstrap entry wall; includes validation overhead"
                    except (AssertionError, KeyError, ValueError, OSError) as error:
                        receipt["metrics_error"] = str(error)
                    raise subprocess.CalledProcessError(result.returncode, command)
                receipt["compiler"] = {"path": str(binary), "sha256": sha(binary)}
                if sys.platform == "darwin":
                    result = json.loads((out / "profile/stage1.result.json").read_text())
                    wall = result["compile_wall_ms"] / 1000
                    originals = list(destination.glob("stage1.process.*/target.stderr"))
                    assert len(originals) == 1, "use one original stderr, not replayed console tails"
                    text = originals[0].read_text()
                    guard = json.loads((originals[0].parent / "result.json").read_text())
                    receipt["stage_guard"] = verify_posix_guard(guard, 2400)
                    assert result["returncode"] == result["publish_barrier_returncode"] == 0
                else:
                    result = json.loads((destination / "shared/receipt.json").read_text())
                    wall = result["stages"][0]["seconds"]
                    text = (destination / "shared/stage1.log").read_text()
                receipt["metrics"] = admission_metrics(text, wall)
                save(receipt_path, receipt)
                assert receipt["metrics"]["open_attempts"] == 0
                assert receipt["metrics"]["source_bytes_complete"] == receipt["metrics"]["source_bytes_total"]
                assert receipt["metrics"]["completed_modules"] == receipt["metrics"]["planned_modules"]
                if sys.platform == "darwin":
                    assert wall < 1800 and receipt["metrics"]["average_workers"] >= 2, receipt["metrics"]
            else:
                stage = json.loads((out / "stage1.json").read_text())
                assert stage["status"] == "PASS" and stage["source"] == source and stage["runtime"] == previous["runtime"]
                assert sha(stage["compiler"]["path"]) == stage["compiler"]["sha256"]
                env["PCC_CURRENT_PCC1"] = stage["compiler"]["path"]
                receipt["compiler"] = stage["compiler"]
                native_gate(out, "pcc1", env)
                assert sha(stage["compiler"]["path"]) == stage["compiler"]["sha256"], "pcc1 changed during use"
        assert source_identity() == source
        assert runtime_identity(archive) == receipt["runtime"]
        receipt["status"] = "PASS"
    except BaseException as error:
        receipt.update(status="FAIL", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        save(receipt_path, receipt)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=PHASES, required=True)
    args = parser.parse_args(argv)
    run(args.out_dir, args.phase)


if __name__ == "__main__":
    main()
