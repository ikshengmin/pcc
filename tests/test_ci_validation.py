"""Unified source CI policy. These are CI tests, not compiler qualification."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import ci_validation_plan as plan
from scripts import ci_validation_run as run
from scripts.ci_validation_inventory import classify
from scripts import ci_validation_tools as tools
from scripts import run_process_tree_sample as guard

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pcc1-package-parity.yml"


def test_frontend_only_batch_avoids_legacy_but_runs_complete_three_lanes():
    result = plan.select([("M", "pcc/frontends/cpp/parser.py"), ("A", "tests/rust/test_new.py")])
    assert result["domains"] == ["cpp", "rust"]
    assert not result["legacy"] and result["cpp_platform"]
    assert {(row["domain"], row["lane"]) for row in result["matrix"]["include"]} == {
        (domain, lane) for domain in ("cpp", "rust") for lane in plan.LANES}


@pytest.mark.parametrize("status,path", [
    ("M", "pcc/backend/self_obj.py"), ("M", "pcc/runtime/py/owner.py"),
    ("M", "pcc/support/instantiation.py"), ("M", "pcc/unknown/new_owner.py"),
    ("D", "tests/cpp/test_retired.py"), ("R100", "tests/rust/renamed.py"),
    ("UNKNOWN", "__unavailable_exact_base__"), ("M", "pyproject.toml"),
])
def test_shared_unknown_deleted_paths_fail_closed(status, path):
    result = plan.select([(status, path)], scope="cpp")
    assert result["legacy"]
    assert set(result["domains"]) == {*plan.LANGUAGES, "shared", "ci-contracts"}


def test_workflow_edit_runs_all_source_and_existing_ci_contracts():
    result = plan.select([("M", ".github/workflows/pcc1-package-parity.yml")])
    assert result["legacy"]
    assert set(result["domains"]) == {*plan.LANGUAGES, "shared", "ci-contracts"}
    assert "tests/test_ci_validation.py" in run.modules("ci-contracts")
    assert "tests/test_ci_pytest_markers.py" in run.modules("ci-contracts")
    assert "tests/python/test_worker_resource_plan.py" in run.modules("ci-contracts")


def test_last_push_before_is_not_a_qualification_baseline():
    source = (ROOT / "scripts/ci_validation_plan.py").read_text()
    assert 'before = event.get("before")' not in source
    assert 'event_before_not_coverage_base' in source
    assert 'successful_validation_base(after)' in source
    assert 'fetch-depth: 0' in WORKFLOW.read_text()


def test_skipped_shared_commit_before_validation_only_tip_is_in_full_batch(monkeypatch):
    base, skipped, tip = "1" * 40, "2" * 40, "3" * 40
    observed = []
    def git(*args):
        observed.append(args)
        if args[0] == "rev-parse":
            return args[-1].removesuffix("^{commit}")
        assert args == ("diff", "--name-status", "--no-renames", "-z", base, tip, "--")
        return "M\0pcc/backend/self_obj.py\0M\0.github/workflows/pcc1-package-parity.yml\0"
    monkeypatch.setattr(plan, "git", git)
    monkeypatch.setattr(plan, "is_ancestor", lambda before, after: (before, after) == (base, tip))
    changes = plan.changed_paths(base, tip)
    result = plan.select(changes, before=base, after=tip)
    assert result["legacy"] and result["before"] == base and result["before"] != skipped
    assert "shared" in result["domains"]


def test_successful_base_requires_same_workflow_master_success_and_ancestry():
    valid = {"event": "push", "head_branch": "master", "status": "completed", "conclusion": "success",
             "path": ".github/workflows/pcc1-package-parity.yml", "head_sha": "1" * 40, "id": 42}
    for change in ({"conclusion": "failure"}, {"status": "in_progress"}, {"event": "pull_request"},
                   {"head_branch": "other"}, {"path": ".github/workflows/workflow.yml"}):
        assert plan.qualified_base([dict(valid, **change)], "2" * 40, lambda *_: True) is None
    assert plan.qualified_base([valid], "2" * 40, lambda *_: False) is None
    assert plan.qualified_base([valid], "2" * 40, lambda *_: True)["id"] == 42


def test_registry_never_deselects_unknown_or_moves_conditional_certification():
    registry = {"test.py": {"reference": {"activity": "external-reference"},
                            "mixed": {"conditional": True}}}
    assert classify("test.py", "reference", False, registry) == "reference"
    assert classify("test.py", "mixed", False, registry) == "ordinary"
    assert classify("test.py", "mixed", True, registry) == "integration"
    assert classify("test.py", "unknown", False, registry) == "ordinary"
    assert classify("new.py", "unknown", True, registry) == "integration"


def test_reviewed_reference_functions_exist_and_rust_negative_controls_stay():
    data = json.loads(run.REFERENCES.read_text())["functions"]
    for module, functions in data.items():
        definitions = {node.name for node in ast.walk(ast.parse((ROOT / module).read_text()))
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert set(functions) <= definitions, (module, set(functions) - definitions)
    rust = data.get("tests/rust/test_rust_certified_references.py", {})
    assert "test_forged_provenance_record_is_not_an_admission_token" not in rust
    assert not any(path.startswith("tests/java/test_classfile") for path in data)


def row(module, index, *, group=(), selected=True, lane="ordinary"):
    return {"file": module, "canonical_nodeid": f"{module}::test_{index}", "groups": list(group),
            "selected_by_repository": selected, "lane": lane}


def test_batches_preserve_exact_union_modules_and_transitive_xdist_groups():
    rows = [row("a.py", i, group=("shared",)) for i in range(260)]
    rows += [row("b.py", 0, group=("shared",)), row("c.py", 0)]
    rows += [row("blocked.py", 0, selected=False), row("integration.py", 0, lane="integration")]
    batches = run.batches(rows, "ordinary")
    assert len(batches) == 2 and len(batches[0]) == 261
    assert {entry["file"] for entry in batches[0]} == {"a.py", "b.py"}
    actual = [entry["canonical_nodeid"] for batch in batches for entry in batch]
    assert len(actual) == len(set(actual)) == 262
    assert set(actual) == {entry["canonical_nodeid"] for entry in rows if entry["selected_by_repository"] and entry["lane"] == "ordinary"}


def test_shared_scope_refreshes_new_elf_macho_odr_modules_without_old_counts():
    shared = json.loads((ROOT / "scripts/data/ci_validation_shared.json").read_text())
    assert set(shared["refresh_globs"]) == {"tests/**/test_elf_*.py", "tests/**/test_macho_*.py", "tests/**/test_*odr_definition*.py"}
    assert "tests/python/test_macho_collection_platform_gates.py" in run.modules("shared")
    assert "7056" not in json.dumps(shared)


def test_guard_keeps_six_workers_90_seconds_1gib_and_fail_fast(tmp_path):
    command = run.pytest_command(tmp_path, ["tests/example.py"])
    assert "-x" in command and "--maxfail=0" not in command and "-n0" not in command
    collection = run.pytest_command(tmp_path, ["tests/example.py"], collect=True)
    assert "--collect-only" in collection and "-n0" in collection
    guarded = run.guarded_command(tmp_path, command)
    assert guarded[guarded.index("--timeout") + 1] == "90"
    assert "--max-tree-rss-bytes" not in guarded
    assert guarded[guarded.index("--auto-tree-rss-ceiling-bytes") + 1] == "1073741824"
    assert guarded[guarded.index("--min-tree-rss-bytes") + 1] == "1073741824"
    assert guarded[guarded.index("--darwin-preflight-reserve-bytes") + 1] == "536870912"
    assert "--performance-lock" in guarded and "--process-rss-history" in guarded
    assert run.DISK_FLOOR == 1073741824


def _ci_admission_without_launch(tmp_path, monkeypatch, *, rejection=None):
    """Exercise the real guard dispatch, stopping after admission, before lock/work."""
    class AdmissionComplete(Exception):
        pass

    def boundary():
        raise AdmissionComplete

    def forbidden(*args, **kwargs):
        pytest.fail("admission policy test must not launch or sample a target")

    monkeypatch.setattr(guard, "compile_ab", SimpleNamespace(_performance_lock=boundary))
    monkeypatch.setattr(guard.subprocess, "Popen", forbidden)
    monkeypatch.setattr(guard, "_process_table", forbidden)
    monkeypatch.setattr(guard, "_timed_process_table", forbidden)
    args = guard._parser().parse_args(run.guarded_command(tmp_path, ["unused-target"])[2:])
    if rejection:
        with pytest.raises(guard.ProcessTreeSampleError, match=rejection):
            guard._run(args)
    else:
        with pytest.raises(AdmissionComplete):
            guard._run(args)
    receipt = json.loads((tmp_path / "result.json").read_text())
    assert receipt["timeout_s"] == 90
    assert receipt["status"] == ("PREFLIGHT_REJECTED" if rejection else "RUNNING")
    if not rejection:
        assert args.max_tree_rss_bytes == receipt["max_tree_rss_bytes"] == run.RSS
        assert receipt["environment"]["PCC_WORKER_TREE_BUDGET_BYTES"] == str(run.RSS)
        selection = receipt["memory_budget_selection"]
        assert selection["default_ceiling_bytes"] == selection["minimum_tree_rss_bytes"] == run.RSS
    return receipt


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("fault,rejection", [
    (None, None),
    ("host-low", "minimum tree budget"),
    ("ancestor-low", "minimum tree budget"),
    ("unmapped", "cgroup memory limits cannot be verified"),
    ("memavailable-missing", "Linux MemAvailable is unavailable"),
])
def test_ci_linux_admission_reads_memavailable_and_ancestor_cgroups_fail_closed(
    tmp_path, monkeypatch, version, fault, rejection,
):
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    mount = tmp_path / "cgroup"
    child = mount / "worker"
    child.mkdir(parents=True)
    available = 3 * run.RSS // 2 if fault == "host-low" else 100 * run.RSS
    (proc / "meminfo").write_text(
        "MemTotal: 268435456 kB\n" +
        ("" if fault == "memavailable-missing" else f"MemAvailable: {available // 1024} kB\n")
    )
    if version == 2:
        membership, kind, options = "0::/worker\n", "cgroup2", "rw"
        limit_name, usage_name = "memory.max", "memory.current"
    else:
        membership, kind, options = "5:memory:/worker\n", "cgroup", "rw,memory"
        limit_name, usage_name = "memory.limit_in_bytes", "memory.usage_in_bytes"
    (proc / "self/cgroup").write_text(membership)
    (proc / "self/mountinfo").write_text(
        "" if fault == "unmapped" else f"20 1 0:2 / {mount} rw - {kind} cgroup {options}\n"
    )
    (mount / limit_name).write_text(str(8 * run.RSS))
    usage = 13 * run.RSS // 2 if fault == "ancestor-low" else 6 * run.RSS
    (mount / usage_name).write_text(str(usage))
    (child / limit_name).write_text(str(16 * run.RSS))
    (child / usage_name).write_text(str(run.RSS))
    observe = guard._linux_memory_observation
    monkeypatch.setattr(guard.sys, "platform", "linux")
    monkeypatch.setattr(guard, "_linux_memory_observation", lambda: observe(proc))
    receipt = _ci_admission_without_launch(tmp_path, monkeypatch, rejection=rejection)
    if fault is None:
        observation = receipt["memory_budget_selection"]["observation"]
        assert observation["platform"] == "linux" and observation["cgroup_limits_verified"]
        assert observation["mem_available_bytes"] == 100 * run.RSS
        assert observation["available_bytes"] == 2 * run.RSS
        assert {row["path"] for row in observation["cgroup_limits"]} == {str(mount), str(child)}


@pytest.mark.parametrize("fault,rejection", [
    (None, None), ("reclaimable-low", "minimum tree budget"),
    ("disk-low", "minimum tree budget"), ("swap-pressure", "minimum tree budget"),
])
def test_ci_darwin_admission_retains_original_reserve_and_pressure_policy(
    tmp_path, monkeypatch, fault, rejection,
):
    observation = {"platform": "darwin", "reclaimable_bytes": 4 * run.RSS,
                   "disk_free_bytes": 4 * run.RSS, "swap_total_bytes": 0,
                   "swap_used_bytes": 0, "swap_free_bytes": 0}
    if fault == "reclaimable-low":
        observation["reclaimable_bytes"] = run.RSS
    elif fault == "disk-low":
        observation["disk_free_bytes"] = run.RSS
    elif fault == "swap-pressure":
        observation.update(reclaimable_bytes=2 * run.RSS, swap_total_bytes=2 * run.RSS,
                           swap_used_bytes=3 * run.RSS // 2, swap_free_bytes=run.RSS // 2)
    monkeypatch.setattr(guard.sys, "platform", "darwin")
    monkeypatch.setattr(guard, "_darwin_resource_observation", lambda: observation)
    receipt = _ci_admission_without_launch(tmp_path, monkeypatch, rejection=rejection)
    assert receipt["memory_budget_selection"]["observation"] == observation
    if fault is None:
        assert receipt["resource_preflight"]["reserve_bytes"] == 536870912
        assert receipt["resource_preflight"]["required_reclaimable_and_disk_free_bytes"] == 3 * run.RSS // 2


def test_missing_rust_target_is_unrun_and_receipt_is_not_a_source_token(tmp_path, monkeypatch):
    root = tmp_path / "toolchain"
    compiler = root / "bin/rustc"
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(b"actual-tool-fixture")
    lib = root / "lib/rustlib/x86_64-unknown-linux-gnu/lib"
    lib.mkdir(parents=True)
    (root / "lib/librustc_driver-fixture.so").write_bytes(b"driver")
    (lib / "libcore-fixture.rlib").write_bytes(b"core")
    (lib / "libstd-fixture.rlib").write_bytes(b"std")
    def execute(command):
        return str(root) + "\n" if command[-1] == "--print=sysroot" else "rustc fixture\nhost: x86_64-unknown-linux-gnu\n"
    monkeypatch.setattr(tools, "execute", execute)
    receipt = tools.rust_installation(compiler)
    assert receipt["targets"]["x86_64-unknown-linux-gnu"]["status"] == "AVAILABLE"
    assert receipt["targets"]["aarch64-apple-darwin"]["status"] == "UNRUN_PREREQUISITE"
    assert receipt["installed_files"][0]["sha256"] == hashlib.sha256(compiler.read_bytes()).hexdigest()
    assert "no Rust source was certified" in receipt["claim"]


def test_one_validation_entrypoint_retains_release_separation_and_platform_guards():
    workflow = WORKFLOW.read_text()
    assert not (ROOT / ".github/workflows/cpp17-validation.yml").exists()
    assert "name: PCC validation" in workflow
    assert "  cpp17:" in workflow and "  source-validation:" in workflow
    assert "cancel-in-progress: false" in workflow
    assert 'test "$(uname -m)" = "$EXPECTED_MACHINE"' in workflow
    assert "runner: macos-15" in workflow and "machine: arm64" in workflow
    assert "id-token:" not in workflow and "pypi-publish:" not in workflow
    release = (ROOT / ".github/workflows/workflow.yml").read_text()
    assert "github.event_name == 'release'" in release and "id-token: write" in release
    assert "  push:" not in release and "  pull_request:" not in release
    assert "timeout-minutes: 15" in workflow
    platform = workflow.split('\n  cpp17:\n', 1)[1]
    assert platform.count("--timeout 90 --auto-tree-rss-ceiling-bytes 1073741824 --min-tree-rss-bytes 1073741824") == 4
    assert platform.count("--darwin-preflight-reserve-bytes 536870912") == 4
    assert platform.count("--performance-lock --process-rss-history") == 4
    assert "--max-tree-rss-bytes" not in platform


def test_planner_dependency_does_not_turn_one_producer_into_another_gate():
    from tests.ci_validation_support import has_only_validation_plan_dependency
    assert has_only_validation_plan_dependency("    needs: validation-plan\n")
    assert not has_only_validation_plan_dependency("    needs: other-platform-native-wheel\n")
    assert not has_only_validation_plan_dependency("    needs: [validation-plan, other-platform-native-wheel]\n")
    assert not has_only_validation_plan_dependency("    needs: validation-plan\n    needs: pcc1-package-parity\n")


def test_execution_inventory_uses_controller_policy_not_worker_local_n0():
    text = (ROOT / "scripts/ci_validation_inventory.py").read_text()
    assert 'node.workerinput["pcc_validation_schedule"] = schedule' in text
    assert 'schedule = config.workerinput.get("pcc_validation_schedule")' in text
    assert 'schedule != {"workers": 6, "dist": "loadgroup"}' in text
    assert '"local_schedule": local_schedule' in text


def test_every_path_reaches_planner_and_source_lane_avoids_unrelated_nginx_warmup():
    workflow = WORKFLOW.read_text()
    assert workflow.count('      - "**"  # The planner') == 2
    source = workflow.split('\n  source-validation:\n', 1)[1].split('\n  ci-plan:\n', 1)[0]
    assert 'sparse-checkout-cone-mode: false' in source
    assert '            /*\n            !/projects/nginx-1.28.3/' in source
    legacy = workflow.split('\n  other-platform-native-wheel:\n', 1)[1].split('\n  pcc1-package-parity:\n', 1)[0]
    assert 'sparse-checkout' not in legacy


def test_terminal_receipts_keep_failure_and_unfinished_nodes_not_false_pass(tmp_path):
    inventory = tmp_path / 'inventory'
    inventory.mkdir()
    witness = {'collect_only': False, 'workers': 6, 'dist': 'loadgroup',
               'selected': ['a.py::test_1@group', 'a.py::test_2@group'],
               'rows': [{'nodeid': f'a.py::test_{index}@group', 'canonical_nodeid': f'a.py::test_{index}'}
                        for index in (1, 2)]}
    for index in range(6):
        (inventory / f'gw{index}.json').write_text(json.dumps(witness))
    events = [{'event': 'report', 'nodeid': 'a.py::test_1@group', 'when': 'setup', 'outcome': 'passed'},
              {'event': 'report', 'nodeid': 'a.py::test_1@group', 'when': 'call', 'outcome': 'failed'},
              {'event': 'report', 'nodeid': 'a.py::test_1@group', 'when': 'teardown', 'outcome': 'passed'},
              {'event': 'finish', 'exitstatus': 1}]
    (tmp_path / 'live.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in events))
    result = run.summarize_batch(tmp_path, ['a.py::test_1', 'a.py::test_2'])
    assert [row['status'] for row in result['outcomes']] == ['FAIL', 'UNRUN_OR_SKIPPED']
    assert result['pytest_exitstatus'] == 1
    (inventory / 'gw5.json').unlink()
    with pytest.raises(ValueError, match='six identical'):
        run.summarize_batch(tmp_path, ['a.py::test_1', 'a.py::test_2'])


def test_rust_ci_installs_real_pinned_host_and_cross_std_before_attestation():
    workflow = WORKFLOW.read_text()
    job = workflow.split('\n  source-validation:\n', 1)[1].split('\n  ci-plan:\n', 1)[0]
    assert 'rustup toolchain install 1.91.1 --profile minimal' in job
    assert '--target x86_64-unknown-linux-gnu --target aarch64-apple-darwin' in job
    assert job.index('rustup toolchain install') < job.index('scripts/ci_validation_tools.py') < job.index('scripts/ci_validation_run.py')
    assert 'continue-on-error:' not in job
    assert 'PCC_CI_RUST_TOOLCHAIN=1.91.1' in job


@pytest.mark.parametrize("returncode,exitstatus,testsfailed", [(1, 0, 0), (0, 1, 0), (0, 0, 1)])
def test_nonzero_session_result_cannot_become_pass_from_green_node_phases(returncode, exitstatus, testsfailed):
    receipt = {"batches": [{"returncode": returncode, "pytest_exitstatus": exitstatus,
                            "pytest_testsfailed": testsfailed,
                            "outcomes": [{"nodeid": "a.py::test_ok", "status": "PASS"}]}],
               "unrun": [], "lane_count": 1}
    assert run.campaign_status(receipt) == "FAIL"


@pytest.mark.parametrize("path", [".github/workflows/pcc1-package-parity.yml", "scripts/ci_validation_plan.py",
                                  "scripts/ci_validation_run.py", "scripts/data/ci_validation_shared.json",
                                  "tests/test_ci_validation.py"])
def test_ci_contract_only_change_retains_original_legacy_execution(path):
    result = plan.select([("M", path)])
    assert result["legacy"] and result["cpp_platform"]
    assert set(result["domains"]) == {*plan.LANGUAGES, "shared", "ci-contracts"}


@pytest.mark.parametrize("path", ["docs/research/example.py", "docs/research/evidence.json", "docs/fixtures/source.c", "docs/results.json"])
def test_docs_fixtures_remain_validation_inputs(path):
    result = plan.select([("M", path)])
    assert result["legacy"] and "shared" in result["domains"]


def test_known_prose_only_path_does_not_start_compiler_gates():
    result = plan.select([("M", "docs/status.md")])
    assert not result["legacy"] and result["domains"] == []


@pytest.mark.parametrize("name,arguments,stdout,stderr", [
    ("go", ["version"], b"go version go1.26 linux/amd64\n", b""),
    ("java", ["-version"], b"", b'openjdk version "21"\n'),
    ("g++", ["--version"], b"g++ fixture\n", b""),
    ("as", ["--version"], b"GNU assembler fixture\n", b""),
    ("ld", ["--version"], b"GNU ld fixture\n", b""),
])
def test_tool_version_observation_retains_both_streams_and_actual_exit(tmp_path, monkeypatch, name, arguments, stdout, stderr):
    from types import SimpleNamespace
    calls = []
    def invoke(command, **options):
        calls.append((command, options))
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=0)
    monkeypatch.setattr(tools.subprocess, "run", invoke)
    result = tools.version_observation(name, "/tools/" + name, tmp_path / "version")
    assert result["command"] == ["/tools/" + name, *arguments]
    assert result["status"] == "COMPLETE" and result["exit_code"] == 0
    assert result["stdout"] == stdout.decode() and result["stderr"] == stderr.decode()
    assert (tmp_path / "version/version.stdout.bin").read_bytes() == stdout
    assert (tmp_path / "version/version.stderr.bin").read_bytes() == stderr
    assert calls[0][1] == {"capture_output": True, "timeout": 15, "check": False}


def test_rust_closure_consumer_checks_driver_and_target_libraries_not_just_map(tmp_path):
    root = tmp_path / "rust"
    paths = [root / "bin/rustc", root / "lib/librustc_driver-fixture.so",
             root / "lib/libLLVM-fixture.so", root / "lib/rustlib/x86_64-unknown-linux-gnu/lib/libcore-fixture.rlib",
             root / "lib/rustlib/aarch64-apple-darwin/lib/libstd-fixture.rlib"]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
    receipt = {"new_prefix": str(root), "installed_files": [
        {"path": str(path), "sha256": tools.digest(path)} for path in paths], "targets": {}}
    assert tools.verify_rust_installation(receipt)["verified_files"] == len(paths)
    for path in paths[1:]:
        original = path.read_bytes()
        path.write_bytes(b"changed")
        with pytest.raises(ValueError, match="changed"):
            tools.verify_rust_installation(receipt)
        path.write_bytes(original)
    receipt["installed_files"].pop()
    with pytest.raises(ValueError, match="inventory differs"):
        tools.verify_rust_installation(receipt)


def test_generic_tool_closure_recheck_rejects_changed_declared_bytes(tmp_path):
    tool = tmp_path / "compiler"
    tool.write_bytes(b"before")
    receipt = {"g++": {"status": "AVAILABLE", "invocation": str(tool),
                        "resolved": str(tool.resolve()), "sha256": tools.digest(tool)}}
    assert tools.verify_tool_receipt(receipt)["verified"][0]["tool"] == "g++"
    tool.write_bytes(b"after")
    with pytest.raises(ValueError, match="tool identity changed"):
        tools.verify_tool_receipt(receipt)


def test_runner_binds_setup_receipt_and_checks_tools_before_and_after():
    text = (ROOT / "scripts/ci_validation_run.py").read_text()
    assert 'Path(os.environ["PCC_CI_TOOL_RECEIPT"])' in text
    assert 'save(args.out / "tools-before.json", verify_tool_receipt(tool_receipt))' in text
    assert 'save(args.out / "tools-after.json", verify_tool_receipt(tool_receipt))' in text
    assert 'setup tool receipt changed during source campaign' in text
    assert 'tool_closure_error=' in text


def test_actual_consumed_rust_receipt_is_bound_to_setup_copy_and_bytes(tmp_path):
    installation = {"new_prefix": "/actual/rust", "installed_files": [], "version": "rustc fixture"}
    receipt = {"rust_installation": installation}
    path = tmp_path / "rust-installation.json"
    path.write_text(json.dumps(installation))
    before = tools.consumed_rust_receipt_identity(receipt, path)
    assert before["path"] == str(path.resolve())
    path.write_text(json.dumps(installation, indent=2))
    assert tools.consumed_rust_receipt_identity(receipt, path) != before
    path.write_text(json.dumps(dict(installation, new_prefix="/other/rust")))
    with pytest.raises(ValueError, match="consumed Rust receipt differs"):
        tools.consumed_rust_receipt_identity(receipt, path)


def test_pinned_rust_selection_precedes_any_default_proxy_observation(monkeypatch):
    monkeypatch.setenv("PCC_CI_RUST_TOOLCHAIN", "1.91.1")
    calls = []
    def which(name):
        assert name == "rustup", "must not choose default rustc proxy"
        return "/tools/rustup"
    def execute(command):
        calls.append(command)
        return "/toolchains/1.91.1/bin/rustc\n"
    monkeypatch.setattr(tools.shutil, "which", which)
    monkeypatch.setattr(tools, "execute", execute)
    assert tools.selected_rustc_invocation() == "/toolchains/1.91.1/bin/rustc"
    assert calls == [["rustup", "which", "--toolchain", "1.91.1", "rustc"]]
