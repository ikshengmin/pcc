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


def test_trait_bound_rustc_oracle_has_a_separate_reference_lane():
    module = "tests/rust/test_rust_trait_bound_arguments.py"
    registry = json.loads(run.REFERENCES.read_text())["functions"]
    reference = "test_rustc_gives_the_same_verdicts"
    assert set(registry[module]) == {reference}
    assert registry[module][reference]["activity"] == "external-reference"
    assert not registry[module][reference].get("conditional")
    tree = ast.parse((ROOT / module).read_text())
    functions = {node.name: node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
    assert reference in functions
    for name, function in functions.items():
        marks = {ast.unparse(mark.func if isinstance(mark, ast.Call) else mark)
                 for mark in function.decorator_list}
        assert "pytest.mark.integration" not in marks
        assert classify(module, name, False, registry) == (
            "reference" if name == reference else "ordinary")


def test_outlives_rustc_oracle_keeps_certified_owned_execution_in_its_lane():
    module = "tests/rust/test_rust_data_outlives_bounds.py"
    registry = json.loads(run.REFERENCES.read_text())["functions"]
    reference = "test_rustc_gives_the_same_verdicts_and_value"
    owned = "test_checked_program_runs_with_owned_compile_assemble_and_link"
    assert set(registry[module]) == {reference, owned}
    assert registry[module][reference]["activity"] == "external-reference"
    assert not registry[module][reference].get("conditional")
    assert registry[module][owned]["activity"] == "mandatory-same-input-certification-and-owned-contract"
    assert registry[module][owned]["conditional"] is True
    tree = ast.parse((ROOT / module).read_text())
    functions = {node.name: node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
    assert {reference, owned} <= functions.keys()
    for name, function in functions.items():
        marks = {ast.unparse(mark.func if isinstance(mark, ast.Call) else mark)
                 for mark in function.decorator_list}
        integration = "pytest.mark.integration" in marks
        assert classify(module, name, integration, registry) == (
            "reference" if name == reference else "integration" if integration else "ordinary")
    # A conditional record documents the mandatory certifier; it is not a waiver
    # and must never move this owned contract into the optional reference lane.
    assert classify(module, owned, False, registry) == "ordinary"
    assert classify(module, owned, True, registry) == "integration"
    calls = {name: {ast.unparse(node.func) for node in ast.walk(function)
                    if isinstance(node, ast.Call)} for name, function in functions.items()}
    assert {"run_rustc_str_function", "subprocess.run"} <= calls[reference]
    assert {"certify_source", "run_owned_u64_function"} <= calls[owned]
    assert "certify_source" not in calls[reference]
    assert "run_owned_u64_function" not in calls[reference]
    for name in (reference, owned):
        marks = {ast.unparse(mark.func if isinstance(mark, ast.Call) else mark)
                 for mark in functions[name].decorator_list}
        assert "pytest.mark.pcc_gate" in marks


def test_go_function_literal_reference_keeps_owned_checks_in_their_original_lane():
    module = "tests/go/test_function_literal_syntax.py"
    registry = json.loads(run.REFERENCES.read_text())["functions"]
    reference = "test_reference_accepts_the_same_sources"
    assert set(registry[module]) == {reference}
    assert registry[module][reference]["activity"] == "external-reference"
    assert not registry[module][reference].get("conditional")
    tree = ast.parse((ROOT / module).read_text())
    functions = {node.name: node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
    assert reference in functions
    for name, function in functions.items():
        marks = {ast.unparse(mark.func if isinstance(mark, ast.Call) else mark)
                 for mark in function.decorator_list}
        integration = "pytest.mark.integration" in marks
        assert classify(module, name, integration, registry) == (
            "reference" if name == reference else "integration" if integration else "ordinary")


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
    return {"file": module, "canonical_nodeid": f"{module}::test_{index}", "nodeid": f"{module}::test_{index}",
            "function": f"test_{index}", "groups": list(group), "gates": [], "reference_activity": "fixture",
            "selected_by_repository": selected, "lane": lane}


def _collection_reports(directory, files, rows):
    """Authored collection model only; no pytest/compiler process is started."""
    inventory = directory / "inventory"
    inventory.mkdir(exist_ok=True)
    selected = [entry["nodeid"] for entry in rows if entry["selected_by_repository"]]
    witness = {"collect_only": True, "workers": 0, "dist": "no", "marker": "",
               "local_schedule": {"workers": 0, "dist": "no"}, "requested_modules": files,
               "module_reports": [{"nodeid": path, "outcome": "passed"} for path in files],
               "collection_anomalies": [], "rows": rows, "raw_nodeids": [entry["canonical_nodeid"] for entry in rows],
               "selected": selected}
    code = 0 if selected else 5
    events = [{"event": "start", "collect_only": True, "maxfail": 0},
              {"event": "collected", "nodeids": selected},
              {"event": "finish", "exitstatus": code, "testsfailed": 0, "testscollected": len(selected)}]
    deselected = [entry["nodeid"] for entry in rows if not entry["selected_by_repository"]]
    if deselected:
        events.insert(1, {"event": "deselected", "nodeids": deselected})
    (inventory / "controller.json").write_text(json.dumps(witness))
    (directory / "live.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    return witness, events, code


def test_collection_plan_is_complete_disjoint_and_does_not_split_modules():
    files = [f"tests/test_{index:03}.py" for index in range(23)]
    plan = run.collection_plan(list(reversed(files)))
    assert [len(part) for part in plan] == [10, 10, 3]
    assert [path for part in plan for path in part] == files
    for invalid in ([], ["a.py", "a.py"], ["../a.py"], ["a.py::test_1"], [None]):
        with pytest.raises(ValueError):
            run.collection_plan(invalid)


def test_collection_merge_rejoins_cross_shard_groups_and_keeps_prerequisites(tmp_path):
    files = [f"tests/test_{index:03}.py" for index in range(12)]
    parts = []
    for index, paths in enumerate(run.collection_plan(files)):
        directory = tmp_path / str(index)
        directory.mkdir()
        rows = [row(path, 0, group=("shared",) if path in (files[0], files[-1]) else (),
                    selected=path != files[1], lane="integration" if path == files[2] else "ordinary")
                for path in paths if path != files[3]]  # A genuinely empty module is still witnessed.
        _witness, _events, code = _collection_reports(directory, paths, rows)
        parts.append(run.summarize_collection(directory, paths, code))
    merged = run.merge_collections(parts, files)
    assert len(merged) == 11 and len({entry["canonical_nodeid"] for entry in merged}) == 11
    assert next(entry for entry in merged if entry["file"] == files[1])["selected_by_repository"] is False
    plan = run.batches(merged, "ordinary")
    owners = [{entry["file"] for entry in batch} for batch in plan]
    assert any({files[0], files[-1]} <= owner for owner in owners)
    assert {entry["canonical_nodeid"] for batch in plan for entry in batch} == {
        entry["canonical_nodeid"] for entry in merged if entry["selected_by_repository"] and entry["lane"] == "ordinary"}
    for bad in (parts[:-1], list(reversed(parts)), parts + [parts[0]]):
        with pytest.raises(ValueError, match="exact disjoint"):
            run.merge_collections(bad, files)


@pytest.mark.parametrize("blocked", [False, True])
def test_zero_selected_collection_requires_exit_five_and_is_not_execution(tmp_path, blocked):
    rows = [row("a.py", 0, selected=False)] if blocked else []
    _witness, _events, code = _collection_reports(tmp_path, ["a.py"], rows)
    assert code == 5
    part = run.summarize_collection(tmp_path, ["a.py"], code)
    assert part["status"] == "COLLECTED_NOT_EXECUTED" and part["selected_count"] == 0
    with pytest.raises(ValueError, match="counts/exit"):
        run.summarize_collection(tmp_path, ["a.py"], 0)
    if blocked:
        assert run.merge_collections([part], ["a.py"]) == rows
    else:
        with pytest.raises(ValueError, match="empty raw inventory"):
            run.merge_collections([part], ["a.py"])


@pytest.mark.parametrize("fault", [None, "missing-module", "duplicate-module", "nested-skip"])
def test_collection_plugin_records_actual_collectors_including_empty_modules(tmp_path, monkeypatch, fault):
    from scripts import ci_validation_inventory as inventory

    files = ["a.py", "empty.py"]
    manifest = tmp_path / "modules.json"
    manifest.write_text(json.dumps(files))
    registry = tmp_path / "references.json"
    registry.write_text('{"functions":{}}')
    monkeypatch.setenv("PCC_VALIDATION_INVENTORY", str(tmp_path / "inventory"))
    monkeypatch.setenv("PCC_VALIDATION_COLLECTION", str(manifest))
    monkeypatch.setenv("PCC_VALIDATION_REFERENCES", str(registry))
    monkeypatch.delenv("PCC_VALIDATION_SELECTION", raising=False)
    registered = []
    options = {"collectonly": True, "numprocesses": 0, "dist": "no", "markexpr": "", "keyword": ""}
    config = SimpleNamespace(rootpath=tmp_path, getoption=options.__getitem__,
                             pluginmanager=SimpleNamespace(register=lambda plugin, name: registered.append(plugin)))
    inventory.pytest_configure(config)
    plugin, = registered
    module_reports = ["a.py"] if fault == "missing-module" else ["a.py", "empty.py"]
    if fault == "duplicate-module":
        module_reports.append("a.py")
    for nodeid in module_reports:
        plugin.pytest_collectreport(SimpleNamespace(nodeid=nodeid, outcome="passed"))
    if fault == "nested-skip":
        plugin.pytest_collectreport(SimpleNamespace(nodeid="a.py::TestSkipped", outcome="skipped", longreprtext="unavailable"))
    item = SimpleNamespace(path=tmp_path / "a.py", nodeid="a.py::test_0", name="test_0", originalname="test_0",
                           get_closest_marker=lambda name: None, iter_markers=lambda name: [])
    hook = plugin.pytest_collection_modifyitems([item])
    next(hook)
    with pytest.raises(StopIteration):
        next(hook)
    events = [{"event": "start", "collect_only": True, "maxfail": 0},
              {"event": "collected", "nodeids": [item.nodeid]},
              {"event": "finish", "exitstatus": 0, "testsfailed": 0, "testscollected": 1}]
    (tmp_path / "live.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    if fault is None:
        summary = run.summarize_collection(tmp_path, files, 0)
        assert summary["status"] == "COLLECTED_NOT_EXECUTED" and len(summary["rows"]) == 1
    else:
        with pytest.raises(ValueError, match="collection evidence"):
            run.summarize_collection(tmp_path, files, 0)


@pytest.mark.parametrize("fault", [
    "missing-inventory", "extra-worker", "missing-module", "duplicate-module", "unexpected-module",
    "module-skip", "nested-skip", "nested-failure", "requested-files", "duplicate-row", "foreign-row",
    "malformed-row", "duplicate-selected", "missing-selected", "workers", "missing-start", "missing-finish",
    "duplicate-finish", "failed-count", "collected-count", "collected-list", "exit", "executed", "truncated",
    "maxfail", "unknown-event", "canonical-decoration", "missing-unselected-row",
    "malformed-deselection", "contradictory-deselection",
])
def test_collection_missing_malformed_or_partial_evidence_fails_closed(tmp_path, fault):
    files = ["a.py", "empty.py"]
    witness, events, code = _collection_reports(tmp_path, files, [row("a.py", 0)])
    if fault == "missing-module":
        witness["module_reports"].pop()
    elif fault == "duplicate-module":
        witness["module_reports"][1] = dict(witness["module_reports"][0])
    elif fault == "unexpected-module":
        witness["module_reports"][1]["nodeid"] = "other.py"
    elif fault == "module-skip":
        witness["module_reports"][1]["outcome"] = "skipped"
    elif fault in ("nested-skip", "nested-failure"):
        witness["collection_anomalies"] = [{"nodeid": "a.py::SkippedClass",
                                             "outcome": "skipped" if fault == "nested-skip" else "failed"}]
    elif fault == "requested-files":
        witness["requested_modules"] = ["a.py"]
    elif fault == "duplicate-row":
        witness["rows"].append(dict(witness["rows"][0]))
    elif fault == "foreign-row":
        witness["rows"].append(row("other.py", 0))
    elif fault == "malformed-row":
        witness["rows"][0]["selected_by_repository"] = "true"
    elif fault == "missing-unselected-row":
        witness["raw_nodeids"].append("a.py::test_blocked")
    elif fault == "malformed-deselection":
        events.insert(1, {"event": "deselected", "nodeids": "a.py::test_0"})
    elif fault == "contradictory-deselection":
        events.insert(1, {"event": "deselected", "nodeids": ["a.py::test_0"]})
    elif fault == "canonical-decoration":
        witness["rows"][0]["nodeid"] += "@group"
    elif fault == "duplicate-selected":
        witness["selected"] *= 2
    elif fault == "missing-selected":
        witness["selected"] = []
    elif fault == "workers":
        witness["workers"] = 6
    elif fault == "missing-start":
        events.pop(0)
    elif fault == "missing-finish":
        events.pop()
    elif fault == "duplicate-finish":
        events.append(dict(events[-1]))
    elif fault == "failed-count":
        events[-1]["testsfailed"] = 1
    elif fault == "collected-count":
        events[-1]["testscollected"] = 2
    elif fault == "collected-list":
        events[1]["nodeids"] = ["unexpected.py::test_0"]
    elif fault == "exit":
        events[-1]["exitstatus"] = 5
        code = 5
    elif fault == "executed":
        events.insert(2, {"event": "report", "nodeid": "a.py::test_0", "when": "call", "outcome": "passed"})
    elif fault == "maxfail":
        events[0]["maxfail"] = 1
    elif fault == "unknown-event":
        events.insert(2, {"event": "unknown-collection-protocol"})
    inventory = tmp_path / "inventory/controller.json"
    inventory.write_text(json.dumps(witness))
    (tmp_path / "live.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    if fault == "missing-inventory":
        inventory.unlink()
    elif fault == "extra-worker":
        inventory.with_name("gw0.json").write_text(json.dumps(witness))
    elif fault == "truncated":
        with (tmp_path / "live.jsonl").open("a") as stream:
            stream.write('{"event":')
    with pytest.raises(ValueError):
        run.summarize_collection(tmp_path, files, code)


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


@pytest.mark.parametrize("members,expected_group", [
    ((
        ("tests.test_ci_bootstrap_owner", "test_actual_six_worker_partition_runs_five_bodies_once_then_remainder"),
        ("tests.python.test_worker_resource_plan", "test_live_peak_growth_backs_off_until_worker_retires"),
    ), "worker_resource_fixtures"),
    ((
        ("tests.test_ci_pytest_markers", "test_complete_marker_entry_real_six_worker_execution"),
        ("tests.test_ci_pytest_markers", "test_current_host_watchdog_executes_real_pytest_body"),
    ), "ci_marker_pytest_fixtures"),
    ((("tests.test_xdist_cpu_budget", "test_real_xdist_budget_contract"),), "pcc_xdist_budget_contract"),
], ids=["resource-pressure", "marker-watchdog", "cpu-budget"])
def test_nested_ci_process_trees_keep_resource_exclusion_groups(members, expected_group):
    """Concurrent real process trees need one loadgroup, not just one batch."""
    import importlib

    rows = []
    for module_name, function_name in members:
        module = importlib.import_module(module_name)
        function = getattr(module, function_name)
        module_marks = getattr(module, "pytestmark", [])
        if not isinstance(module_marks, list):
            module_marks = [module_marks]
        marks = [*module_marks, *getattr(function, "pytestmark", [])]
        groups = sorted(str(mark.kwargs.get("name", mark.args[0] if mark.args else ""))
                        for mark in marks if mark.name == "xdist_group")
        # Extra groups change xdist's combined group key and break exclusion.
        assert groups == [expected_group]
        path = Path(module.__file__).relative_to(ROOT).as_posix()
        rows.append(row(path, function_name, group=groups))
    rows.extend(row(rows[0]["file"], index) for index in range(250))
    assert run.batches(rows, "ordinary") == [rows]


def test_shared_scope_refreshes_new_elf_macho_odr_modules_without_old_counts():
    shared = json.loads((ROOT / "scripts/data/ci_validation_shared.json").read_text())
    assert set(shared["refresh_globs"]) == {"tests/**/test_elf_*.py", "tests/**/test_macho_*.py", "tests/**/test_*odr_definition*.py"}
    assert "tests/python/test_macho_collection_platform_gates.py" in run.modules("shared")
    assert "7056" not in json.dumps(shared)


def test_shared_scope_collects_import_scanner_semantics_outside_ci_contracts():
    modules = (
        "tests/python/test_pipeline_import_scan.py",
        "tests/python/test_attribute_error_import_filter_native.py",
    )
    scope = json.loads((ROOT / "scripts/data/ci_validation_shared.json").read_text())
    for module in modules:
        assert scope["extra_modules"].count(module) == 1
    files = run.modules("shared")
    assert set(modules) <= set(files)
    # Use the complete real inventory, not a fabricated module-only collection.
    assert [path for shard in run.collection_plan(files) for path in shard] == files
    assert set(modules).isdisjoint(run.CI_CONTRACT_MODULES)
    assert set(modules).isdisjoint(run.modules("ci-contracts"))
    for lane in ("ordinary", "integration", "reference"):
        assert plan.requires_native_inputs("shared", lane)


def test_import_scanner_functions_keep_ordinary_reference_and_native_lanes():
    scanner = "tests/python/test_pipeline_import_scan.py"
    native = "tests/python/test_attribute_error_import_filter_native.py"
    registry = json.loads(run.REFERENCES.read_text())["functions"]
    functions = {}
    for module in (scanner, native):
        tree = ast.parse((ROOT / module).read_text())
        # No module-wide mark may silently change these function lane owners.
        assert not any(
            isinstance(node, (ast.Assign, ast.AnnAssign)) and any(
                isinstance(target, ast.Name) and target.id == "pytestmark"
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            ) for node in tree.body
        )
        functions[module] = {
            node.name: node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        }

    def integration_mark(function):
        marks = {ast.unparse(mark.func if isinstance(mark, ast.Call) else mark)
                 for mark in function.decorator_list}
        assert marks <= {"pytest.mark.parametrize", "pytest.mark.integration"}
        return "pytest.mark.integration" in marks

    assert "test_pipeline_reexports_import_scanners_by_identity" in functions[scanner]
    assert "test_attribute_error_filter_preserves_real_discovery_targets" in functions[scanner]
    assert not registry.get(scanner)
    for name, function in functions[scanner].items():
        integration = integration_mark(function)
        assert not integration
        assert classify(scanner, name, integration, registry) == "ordinary"

    reference = "test_attribute_error_filter_cpython_reference"
    emitted = "test_attribute_error_filter_native"
    assert set(functions[native]) == {reference, emitted}
    assert set(registry[native]) == {reference}
    entry = registry[native][reference]
    assert entry["activity"] == "external-reference" and not entry.get("conditional")
    assert not integration_mark(functions[native][reference])
    assert classify(native, reference, integration_mark(functions[native][reference]), registry) == "reference"
    # Derive the marker from the actual native test; passing a literal True
    # would hide a removed integration decorator.
    integration = integration_mark(functions[native][emitted])
    assert integration
    assert classify(native, emitted, integration, registry) == "integration"
    assert {"pcc_runtime_archive", "python_program_compiler"} <= {
        argument.arg for argument in functions[native][emitted].args.args
    }


def test_guard_keeps_six_workers_90_seconds_1gib_without_injected_fail_fast(tmp_path):
    command = run.pytest_command(tmp_path, ["tests/example.py"])
    assert "-x" in command and "--maxfail=0" in command
    assert "-n0" not in command and "-n" not in command
    assert "--pcc-live-subtests" in command
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


@pytest.mark.parametrize("wrapper_code,target_code", [(0, 0), (1, 1), (2, 0), (0, 1)])
def test_guard_wrapper_exit_must_match_terminal_target_receipt(tmp_path, monkeypatch, wrapper_code, target_code):
    original = {"sha": "fixture"}
    monkeypatch.setattr(run, "identity", lambda: original)
    monkeypatch.setattr(run.shutil, "disk_usage", lambda path: SimpleNamespace(free=2 * run.DISK_FLOOR))
    monkeypatch.setattr(run.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=wrapper_code))
    (tmp_path / "result.json").write_text(json.dumps({
        "status": "COMPLETE", "timeout_s": run.TIMEOUT, "max_tree_rss_bytes": run.RSS,
        "peak_tree_rss_bytes": 0, "returncode": target_code,
    }))
    if wrapper_code == target_code:
        assert run.run_guard(tmp_path, ["unused"], {}, original) == target_code
    else:
        with pytest.raises(ValueError, match="guard exit differs"):
            run.run_guard(tmp_path, ["unused"], {}, original)


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
    events = [{'event': 'start', 'maxfail': 0},
              {'event': 'report', 'nodeid': 'a.py::test_1@group', 'when': 'setup', 'outcome': 'passed'},
              {'event': 'report', 'nodeid': 'a.py::test_1@group', 'when': 'call', 'outcome': 'failed'},
              {'event': 'report', 'nodeid': 'a.py::test_1@group', 'when': 'teardown', 'outcome': 'passed'},
              {'event': 'finish', 'exitstatus': 1, 'testsfailed': 1}]
    (tmp_path / 'live.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in events))
    result = run.summarize_batch(tmp_path, ['a.py::test_1', 'a.py::test_2'])
    assert [row['status'] for row in result['outcomes']] == ['FAIL', 'UNRUN_OR_SKIPPED']
    assert result['pytest_exitstatus'] == 1
    (inventory / 'gw5.json').unlink()
    missing = run.summarize_batch(tmp_path, ['a.py::test_1', 'a.py::test_2'])
    assert any('six identical' in error for error in missing['evidence_errors'])
    assert len(missing['unattributed_reports']) == 3
    assert all(row['status'] == 'UNRUN_OR_SKIPPED' for row in missing['outcomes'])


def _batch_reports(directory, expected, events):
    inventory = directory / "inventory"
    inventory.mkdir(exist_ok=True)
    witness = {"collect_only": False, "workers": 6, "dist": "loadgroup",
               "selected": [node + "@group" for node in expected],
               "raw_nodeids": expected,
               "rows": [{"nodeid": node + "@group", "canonical_nodeid": node,
                         "file": node.split("::")[0], "function": node.split("::")[-1].split("[")[0],
                         "groups": [], "gates": [], "reference_activity": "fixture",
                         "selected_by_repository": True, "lane": "ordinary"} for node in expected]}
    for index in range(6):
        (inventory / f"gw{index}.json").write_text(json.dumps(witness))
    (directory / "live.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))


def _node_reports(node, *, failed=False, phases=("setup", "call", "teardown")):
    return [{"event": "report", "nodeid": node + "@group", "when": phase,
             "outcome": "failed" if failed and phase == "call" else "passed"} for phase in phases]


@pytest.mark.parametrize("fault", [None, "lane", "groups", "gates", "reference", "selected", "function",
                                   "missing-unselected", "added-raw-node", "duplicate-raw-node"])
def test_body_inventory_matches_every_frozen_raw_row_not_only_selected_nodes(tmp_path, fault):
    frozen = [row("a.py", 0), row("a.py", 1, selected=False, lane="integration")]
    node = frozen[0]["canonical_nodeid"]
    events = [{"event": "start", "maxfail": 0}, *_node_reports(node),
              {"event": "finish", "exitstatus": 0, "testsfailed": 0}]
    _batch_reports(tmp_path, [node], events)
    actual = [dict(entry, nodeid=entry["nodeid"] + "@group") for entry in frozen]
    changes = {"lane": ("lane", "reference"), "groups": ("groups", ["changed"]),
               "gates": ("gates", [{"env": "CHANGED"}]), "reference": ("reference_activity", "changed"),
               "selected": ("selected_by_repository", True), "function": ("function", "different")}
    if fault in changes:
        key, value = changes[fault]
        actual[1][key] = value
    elif fault == "missing-unselected":
        actual.pop()
    elif fault == "added-raw-node":
        actual.append(row("a.py", 2, selected=False))
    elif fault == "duplicate-raw-node":
        actual.append(dict(actual[1]))
    for path in (tmp_path / "inventory").glob("gw*.json"):
        witness = json.loads(path.read_text())
        witness["rows"] = actual
        witness["raw_nodeids"] = [entry["canonical_nodeid"] for entry in actual]
        path.write_text(json.dumps(witness))
    result = run.summarize_batch(tmp_path, [node], frozen)
    if fault is None:
        assert result["outcomes"][0]["status"] == "PASS" and not result["evidence_errors"]
    else:
        assert result["evidence_errors"] and result["outcomes"][0]["status"] != "PASS"
        assert not run.batch_continuation(dict(result, returncode=0, guard_error=None))[0]


def _subtest_report(node, index, *, outcome="passed", **extra):
    return {"event": "subtest", "nodeid": node + "@group", "when": "call", "outcome": outcome,
            "report_type": "_pytest.subtests.SubtestReport", "subtest_index": index,
            "subtest_context": {"msg": None, "kwargs": {"case": repr(index)}}, **extra}


def test_pinned_pytest_subtest_transport_is_an_explicit_event_not_duplicate_parent_call():
    from _pytest.reports import TestReport
    from _pytest.subtests import SubtestContext, SubtestReport
    from scripts.pytest_live_report import LiveReport

    reporter = object.__new__(LiveReport)
    reporter.subtest_counts = {}
    reporter.separate_subtests = True
    events = []
    reporter.write = lambda event, **fields: events.append({"event": event, **fields})
    parent = TestReport(nodeid="case.py::test_parent", location=("case.py", 0, "test_parent"),
                        keywords={}, outcome="passed", longrepr=None, when="call")
    context = SubtestContext(msg="same original", kwargs={"expression": "1<2"})
    child = SubtestReport._new(parent, context, captured_output=None, captured_logs=None)
    transport = child._to_json()
    assert transport["_report_type"] == "SubTestReport"
    reporter.pytest_runtest_logreport(SubtestReport._from_json(transport))
    reporter.pytest_runtest_logreport(SubtestReport._from_json(transport))
    # An ordinary report with a context attribute is still an ordinary phase.
    parent.context = context
    reporter.pytest_runtest_logreport(parent)
    assert [event["event"] for event in events] == ["subtest", "subtest", "report"]
    assert [event["subtest_index"] for event in events[:2]] == [1, 2]
    assert events[0]["subtest_context"] == {"msg": "same original", "kwargs": {"expression": repr("1<2")}}


@pytest.mark.parametrize("consumer", ["worker-prior", "macos-regression", "complete-markers"])
@pytest.mark.parametrize("outcome,xfail", [
    ("passed", False), ("failed", False), ("skipped", False),
    ("passed", True), ("skipped", True),
])
def test_legacy_report_consumers_keep_subtests_visible_and_fail_closed(tmp_path, consumer, outcome, xfail):
    """Authored report-model regression; no pytest process or native qualification."""
    from _pytest.reports import TestReport
    from _pytest.subtests import SubtestContext, SubtestReport
    from scripts import ci_macos_regression_gate, ci_pytest_markers, ci_worker_size_prior_gate
    from scripts.pytest_live_report import LiveReport, pytest_addoption

    options = {}
    pytest_addoption(SimpleNamespace(addoption=lambda name, **kwargs: options.update({name: kwargs})))
    assert options["--pcc-live-subtests"]["default"] is False
    assert options["--pcc-live-subtests"]["action"] == "store_true"
    values = {name: option["default"] for name, option in options.items()}
    values.update(markexpr="not integration", maxfail=0, collectonly=False)
    config = SimpleNamespace(invocation_params=SimpleNamespace(args=[]), rootpath=tmp_path,
                             inipath=None, getoption=lambda name, default=None: values.get(name, default))
    path = tmp_path / "live.jsonl"
    reporter = LiveReport(path, config)
    node = "case.py::test_parent"
    reporter.record_collection([node])
    fields = dict(nodeid=node, location=("case.py", 0, "test_parent"), keywords={}, longrepr=None)
    reporter.pytest_runtest_logreport(TestReport(**fields, outcome="passed", when="setup"))
    subtest = SubtestReport._new(
        TestReport(**fields, outcome=outcome, when="call"),
        SubtestContext(msg="explicit child", kwargs={}), captured_output=None, captured_logs=None,
    )
    if xfail:
        subtest.wasxfail = "expected child failure"
    reporter.pytest_runtest_logreport(subtest)
    for phase in ("call", "teardown"):
        reporter.pytest_runtest_logreport(TestReport(**fields, outcome="passed", when=phase))
    code = int(outcome == "failed")
    reporter.pytest_sessionfinish(SimpleNamespace(testscollected=1, testsfailed=code,
                                                 shouldstop=False, shouldfail=False), code)
    reporter.pytest_unconfigure(config)
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert not events[0]["separate_subtests"]
    assert not any(event["event"] == "subtest" for event in events)
    reports = [event for event in events if event["event"] == "report"]
    assert len(reports) == 4 and reports[1]["outcome"] == outcome
    assert ("wasxfail" in reports[1]) is xfail
    # The unchanged consumers rejected subtest/duplicate raw phases before the
    # source-lane schema was added. An all-green parent cannot change that.
    if consumer == "worker-prior":
        (tmp_path / "target.stdout").write_text(
            "created: 6/6 workers\n6 workers [1 item]\nscheduling tests via LoadGroupScheduling\n")
        with pytest.raises(AssertionError):
            ci_worker_size_prior_gate.verify_pytest(tmp_path, [node])
    elif consumer == "macos-regression":
        with pytest.raises(AssertionError):
            ci_macos_regression_gate.verify_execution_report(path, [node])
    else:
        inventory = {"marker": "not integration", "expected": [node], "selected": [node],
                     "unexpected": [], "gated_out": []}
        for index in range(6):
            (tmp_path / f"inventory-gw{index}.json").write_text(json.dumps(inventory))
        (tmp_path / "configuration.json").write_text(json.dumps(
            {"marker": "not integration", "workers": 6, "dist": "loadgroup"}))
        with pytest.raises(ValueError):
            ci_pytest_markers.summarize(tmp_path, "not integration")


def test_ten_and_five_successful_unittest_subcases_remain_two_parent_nodes(tmp_path):
    expected = ["control.py::ScalarControlFlowTests::test_relational",
                "control.py::ScalarControlFlowTests::test_void"]
    events = [{"event": "start", "maxfail": 0}]
    for node, count in zip(expected, (10, 5)):
        events += _node_reports(node, phases=("setup",))
        events += [_subtest_report(node, index) for index in range(1, count + 1)]
        events += _node_reports(node, phases=("call", "teardown"))
    events.append({"event": "finish", "exitstatus": 0, "testsfailed": 0})
    _batch_reports(tmp_path, expected, events)
    summary = run.summarize_batch(tmp_path, expected)
    assert not summary["evidence_errors"] and len(summary["outcomes"]) == 2
    assert [len(row["subtests"]) for row in summary["outcomes"]] == [10, 5]
    assert all(row["status"] == "PASS" and row["full_phase"] for row in summary["outcomes"])
    batch = dict(summary, returncode=0, guard_error=None)
    assert run.batch_continuation(batch)[0]
    assert run.campaign_status({"batches": [batch], "unrun": [], "lane_count": 2}) == "PASS"


@pytest.mark.parametrize("outcome,xfail,expected_status", [
    ("failed", False, "FAIL"), ("skipped", False, "UNRUN_OR_SKIPPED"),
    ("passed", True, "UNRUN_OR_SKIPPED"), ("skipped", True, "UNRUN_OR_SKIPPED"),
])
def test_subcase_failure_skip_or_xfail_cannot_be_hidden_by_green_parent_phases(
    tmp_path, outcome, xfail, expected_status,
):
    node = "a.py::test_parent"
    subtest = _subtest_report(node, 1, outcome=outcome, **({"wasxfail": "expected"} if xfail else {}))
    code = int(outcome == "failed")
    events = [{"event": "start", "maxfail": 0}, *_node_reports(node, phases=("setup",)), subtest,
              *_node_reports(node, phases=("call", "teardown")),
              {"event": "finish", "exitstatus": code, "testsfailed": code}]
    _batch_reports(tmp_path, [node], events)
    summary = run.summarize_batch(tmp_path, [node])
    assert len(summary["outcomes"]) == 1
    assert summary["outcomes"][0]["status"] == expected_status
    assert summary["outcomes"][0]["full_phase"] and len(summary["outcomes"][0]["subtests"]) == 1
    batch = dict(summary, returncode=code, guard_error=None)
    assert run.campaign_status({"batches": [batch], "unrun": [], "lane_count": 1}) != "PASS"


@pytest.mark.parametrize("anomaly", ["duplicate-subtest", "missing-parent-call", "ordinary-duplicate"])
def test_subtest_support_never_waives_duplicate_or_missing_parent_phase(tmp_path, anomaly):
    node = "a.py::test_parent"
    phases = ("setup", "teardown") if anomaly == "missing-parent-call" else ("setup", "call", "teardown")
    events = [{"event": "start", "maxfail": 0}, *_node_reports(node, phases=phases), _subtest_report(node, 1)]
    if anomaly == "duplicate-subtest":
        events.append(_subtest_report(node, 1))
    elif anomaly == "ordinary-duplicate":
        events += _node_reports(node, phases=("call",))
    events.append({"event": "finish", "exitstatus": 0, "testsfailed": 0})
    _batch_reports(tmp_path, [node], events)
    summary = run.summarize_batch(tmp_path, [node])
    batch = dict(summary, returncode=0, guard_error=None)
    assert summary["outcomes"][0]["status"] != "PASS"
    assert not run.batch_continuation(batch)[0]
    assert run.campaign_status({"batches": [batch], "unrun": [], "lane_count": 1}) != "PASS"


@pytest.mark.parametrize("end", ["missing", "truncated", "interrupt", "internalerror", "worker_crash"])
def test_partial_reports_keep_full_phase_pass_distinct_from_call_only(tmp_path, end):
    expected = ["a.py::test_full", "a.py::test_partial"]
    events = [{"event": "start", "maxfail": 0}, *_node_reports(expected[0]),
              *_node_reports(expected[1], phases=("call",))]
    if end not in {"missing", "truncated"}:
        events += [{"event": end, "longrepr": "session stopped"},
                   {"event": "finish", "exitstatus": 2, "testsfailed": 0}]
    _batch_reports(tmp_path, expected, events)
    if end == "truncated":
        with (tmp_path / "live.jsonl").open("a") as stream:
            stream.write('{"event":')
    summary = run.summarize_batch(tmp_path, expected)
    full, partial = summary["outcomes"]
    assert full["status"] == "PASS" and full["full_phase"]
    assert partial["status"] == "UNRUN_OR_SKIPPED" and not partial["full_phase"]
    assert partial["call_outcome"] == "passed" and partial["observed_phases"] == ["call"]
    batch = dict(summary, returncode=2, guard_error=None)
    assert not run.batch_continuation(batch)[0]
    assert run.campaign_status({"batches": [batch], "unrun": [], "lane_count": 2}) == "FAIL"


@pytest.mark.parametrize("code,exitstatus,event,shouldstop", [
    (2, 2, None, ""), (1, 1, "interrupt", ""), (1, 1, "internalerror", ""),
    (1, 1, "worker_crash", ""), (1, 1, "collection", ""),
    (1, 1, None, "stopping after 1 failures"), (0, 1, None, ""),
])
def test_nonordinary_sessions_never_admit_later_batches_even_with_assertion_reports(
    tmp_path, code, exitstatus, event, shouldstop,
):
    node = "a.py::test_failed"
    events = [{"event": "start", "maxfail": 0}, *_node_reports(node, failed=True)]
    if event:
        events.append({"event": event, "outcome": "failed"})
    events.append({"event": "finish", "exitstatus": exitstatus, "testsfailed": 1, "shouldstop": shouldstop})
    _batch_reports(tmp_path, [node], events)
    summary = run.summarize_batch(tmp_path, [node])
    assert summary["outcomes"][0]["status"] == "FAIL"
    assert not run.batch_continuation(dict(summary, returncode=code, guard_error=None))[0]


@pytest.mark.parametrize("anomaly", ["duplicate-phase", "unexpected-node", "maxfail", "missing-finish"])
def test_report_policy_anomalies_keep_evidence_and_cannot_be_green(tmp_path, anomaly):
    node = "a.py::test_passed"
    events = [{"event": "start", "maxfail": 1 if anomaly == "maxfail" else 0}, *_node_reports(node)]
    if anomaly == "duplicate-phase":
        events += _node_reports(node, phases=("call",))
    elif anomaly == "unexpected-node":
        events += _node_reports("other.py::test_extra", phases=("call",))
    if anomaly != "missing-finish":
        events.append({"event": "finish", "exitstatus": 0, "testsfailed": 0})
    _batch_reports(tmp_path, [node], events)
    summary = run.summarize_batch(tmp_path, [node])
    assert summary["evidence_errors"]
    assert len(summary["outcomes"][0]["reports"]) >= 3
    batch = dict(summary, returncode=0, guard_error=None)
    assert not run.batch_continuation(batch)[0]
    assert run.campaign_status({"batches": [batch], "unrun": [], "lane_count": 1}) == "FAIL"


@pytest.mark.parametrize("first", ["assertion", "interrupt", "resource"])
def test_campaign_saves_failed_batch_before_deciding_independent_continuation(tmp_path, monkeypatch, first):
    """Model the public campaign orchestration; no pytest or compiler subprocess."""
    root = tmp_path / "source"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts="-n 6 --dist=loadgroup -m \'not integration\'"\n')
    setup = tmp_path / "tools.json"
    setup.write_text("{}")
    monkeypatch.setenv("PCC_CI_TOOL_RECEIPT", str(setup))
    monkeypatch.setenv("GITHUB_SHA", "fixture-source")
    monkeypatch.delenv("PCC_RUSTC_INSTALL_RECEIPT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(run, "ROOT", root)
    monkeypatch.setattr(run, "identity", lambda: {"sha": "fixture-source", "files": {}})
    monkeypatch.setattr(tools, "verify_tool_receipt", lambda receipt: {"verified": []})
    monkeypatch.setattr(run, "modules", lambda domain: ["a.py", "b.py"])
    rows = [row("a.py", 0), row("b.py", 0)]
    monkeypatch.setattr(run, "batches", lambda collected, lane, **policy: [[entry] for entry in collected])
    attempts = []

    def guarded(directory, command, environment, original):
        if directory.name.startswith("collection-"):
            files = json.loads((directory / "modules.json").read_text())
            return _collection_reports(directory, files, [entry for entry in rows if entry["file"] in files])[2]
        attempts.append(directory.name)
        expected = json.loads((directory / "selection.json").read_text())
        node = expected[0]
        failing = directory.name == "batch-000"
        events = [{"event": "start", "maxfail": 0}]
        if failing and first == "resource":
            events += _node_reports(node, phases=("call",))
            _batch_reports(directory, expected, events)
            raise ValueError("resource/guard anomaly; no later batch admitted")
        events += _node_reports(node, failed=failing)
        code = 1 if failing else 0
        if failing and first == "interrupt":
            events.append({"event": "interrupt", "exception_type": "Interrupted"})
            code = 2
        events.append({"event": "finish", "exitstatus": code, "testsfailed": int(failing)})
        _batch_reports(directory, expected, events)
        return code

    monkeypatch.setattr(run, "run_guard", guarded)
    out = tmp_path / "campaign"
    assert run.run(SimpleNamespace(domain="go", lane="ordinary", out=out)) == 1
    receipt = json.loads((out / "receipt.json").read_text())
    first_outcomes = json.loads((out / "batch-000/outcomes.json").read_text())
    assert first_outcomes["outcomes"][0]["call_outcome"] == ("passed" if first == "resource" else "failed")
    if first == "assertion":
        assert attempts == ["batch-000", "batch-001"]
        assert receipt["status"] == "FAIL" and receipt["unattempted"] == []
        assert [batch["continue_independent_batches"] for batch in receipt["batches"]] == [True, True]
    else:
        assert attempts == ["batch-000"]
        assert receipt["status"] == "INFRASTRUCTURE_OR_RESOURCE_FAILURE"
        assert receipt["unattempted"] == ["b.py::test_0"]
        assert not receipt["batches"][0]["continue_independent_batches"]


@pytest.mark.parametrize("domain,lane", [
    ("shared", "ordinary"), ("cpp", "integration"), ("ci-contracts", "integration"),
])
@pytest.mark.parametrize("fault", ["resource", "missing-finish", "missing-module"])
def test_collection_abort_keeps_completed_shards_and_never_admits_test_bodies(tmp_path, monkeypatch, fault, domain, lane):
    """Orchestration model only: guard and identities are not executed."""
    from scripts import ci_validation_native_inputs as native
    native_state = {"fixture": "transport admission is separately tested"}
    admissions, verified = [], []
    def admit(root, bundle, **options):
        admissions.append(options)
        return native_state, {"PCC_PLATFORM_TEST_COMPILER": "verified-stage1-fixture"}
    def verify_after(root, state):
        verified.append(state)
        return state
    monkeypatch.setattr(native, "admit", admit)
    monkeypatch.setattr(native, "verify_after", verify_after)
    monkeypatch.delenv("PCC_TEST_COMPILER", raising=False)
    root = tmp_path / "source"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts="-n 6 --dist=loadgroup -m \'not integration\'"\n')
    setup = tmp_path / "tools.json"
    setup.write_text("{}")
    monkeypatch.setenv("PCC_CI_TOOL_RECEIPT", str(setup))
    monkeypatch.setenv("GITHUB_SHA", "fixture-source")
    monkeypatch.setenv("PCC_VALIDATION_SELECTION", "must-not-be-inherited")
    monkeypatch.delenv("PCC_RUSTC_INSTALL_RECEIPT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(run, "ROOT", root)
    monkeypatch.setattr(run, "identity", lambda: {"sha": "fixture-source", "files": {}})
    monkeypatch.setattr(tools, "verify_tool_receipt", lambda receipt: {"verified": []})
    files = [f"test_{index:02}.py" for index in range(12)]
    monkeypatch.setattr(run, "modules", lambda domain: files)
    attempts = []

    def guarded(directory, command, environment, original):
        attempts.append(directory.name)
        assert directory.name.startswith("collection-")
        assert "PCC_VALIDATION_SELECTION" not in environment
        assert environment["PCC_PLATFORM_TEST_COMPILER"] == "verified-stage1-fixture"
        assert "PCC_TEST_COMPILER" not in environment
        assert "--collect-only" in command and "-n0" in command
        shard = json.loads(Path(environment["PCC_VALIDATION_COLLECTION"]).read_text())
        witness, events, code = _collection_reports(directory, shard, [row(path, 0) for path in shard])
        if directory.name == "collection-001":
            if fault == "resource":
                raise ValueError("resource/guard anomaly")
            if fault == "missing-finish":
                (directory / "live.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events[:-1]))
            else:
                witness["module_reports"].pop()
                (directory / "inventory/controller.json").write_text(json.dumps(witness))
        return code

    monkeypatch.setattr(run, "run_guard", guarded)
    out = tmp_path / "campaign"
    assert run.run(SimpleNamespace(domain=domain, lane=lane, out=out,
                                   stage1_bundle=tmp_path / "modeled-stage1-bundle")) == 1
    assert admissions == [{"domain": domain, "lane": lane}] and verified == [native_state]
    assert json.loads((out / "native-inputs-after.json").read_text()) == native_state
    receipt = json.loads((out / "receipt.json").read_text())
    assert attempts == ["collection-000", "collection-001"]
    assert receipt["status"] == "INFRASTRUCTURE_OR_RESOURCE_FAILURE" and receipt["batches"] == []
    assert [part["status"] for part in receipt["collections"]] == ["COLLECTED_NOT_EXECUTED", "COLLECTION_INCOMPLETE"]
    assert receipt["collection_pending_modules"] == files[10:]
    assert "lane_count" not in receipt and not (out / "raw-inventory.json").exists()


def test_live_report_records_actual_interrupt_internalerror_worker_and_stop_evidence():
    from scripts.pytest_live_report import LiveReport
    reporter = object.__new__(LiveReport)
    events = []
    reporter.write = lambda event, **fields: events.append({"event": event, **fields})
    exception = SimpleNamespace(typename="KeyboardInterrupt", value="actual interruption")
    reporter.pytest_keyboard_interrupt(exception)
    reporter.pytest_internalerror("actual internal error", exception)
    reporter.pytest_testnodedown(SimpleNamespace(gateway=SimpleNamespace(id="gw3")), "worker exited")
    reporter.pytest_sessionfinish(SimpleNamespace(testscollected=4, testsfailed=1,
                                                  shouldstop="stopped", shouldfail=False), 2)
    assert [event["event"] for event in events] == ["interrupt", "internalerror", "worker_crash", "finish"]
    assert events[-1]["exitstatus"] == 2 and events[-1]["shouldstop"] == "stopped"


def test_rust_ci_installs_real_pinned_host_and_cross_std_before_attestation():
    workflow = WORKFLOW.read_text()
    job = workflow.split('\n  source-validation:\n', 1)[1].split('\n  shared-source-validation:\n', 1)[0]
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
    import base64
    import hashlib
    for stream, data in (("stdout", stdout), ("stderr", stderr)):
        assert base64.b64decode(result[stream + "_base64"], validate=True) == data
        assert result[stream + "_sha256"] == hashlib.sha256(data).hexdigest()
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


@pytest.mark.parametrize("domain,lane", [("shared", lane) for lane in plan.LANES] + [("cpp", "integration")])
def test_native_source_lane_without_bundle_stops_before_tools_or_collection(tmp_path, monkeypatch, domain, lane):
    root = tmp_path / "source"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts="-n 6 --dist=loadgroup -m \'not integration\'"\n')
    monkeypatch.setattr(run, "ROOT", root)
    monkeypatch.setattr(run, "identity", lambda: {"sha": "fixture-source", "files": {}})
    monkeypatch.setenv("GITHUB_SHA", "fixture-source")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(run, "modules", lambda *a: pytest.fail("missing bundle reached collection"))
    out = tmp_path / "campaign"
    assert run.run(SimpleNamespace(domain=domain, lane=lane, out=out)) == 1
    receipt = json.loads((out / "receipt.json").read_text())
    assert receipt["status"] == "INFRASTRUCTURE_OR_RESOURCE_FAILURE"
    assert "requires an explicit same-run Stage1 bundle" in receipt["error"]
    assert receipt["collections"] == receipt["batches"] == []


def test_go_ci_installs_pinned_reference_and_binds_whole_package_before_attestation():
    job = WORKFLOW.read_text().split('\n  source-validation:\n', 1)[1].split(
        '\n  shared-source-validation:\n', 1)[0]
    install = job.split('      - name: Install the pinned official Go reference toolchain\n', 1)[1]
    install, bind = install.split('      - name: Bind the matching whole-package Go reference installation\n', 1)
    bind = bind.split('      - name: Bind actual available reference tools', 1)[0]
    for step in (install, bind):
        assert "if: ${{ matrix.domain == 'go' }}" in step
    assert 'uses: actions/setup-go@v6' in install
    assert "go-version: '1.27.3'" in install and 'cache: false' in install
    assert 'export GOTOOLCHAIN=local' in bind
    assert 'test "$(go env GOVERSION)" = \'go1.27.3\'' in bind
    assert 'goroot="$(go env GOROOT)"' in bind and 'test -x "$goroot/bin/go"' in bind
    assert 'PCC_GO_MATH_BITS_REFERENCE=$goroot/bin/go' in bind
    assert 'PCC_GO_MATH_BITS_GOROOT=$goroot' in bind
    assert job.index('actions/setup-go@v6') < job.index('scripts/ci_validation_tools.py') < job.index('scripts/ci_validation_run.py')
    assert 'continue-on-error:' not in job


def test_resource_groups_split_only_modules_and_preserve_exact_frozen_rows():
    rows = [row(path, index, group=("oracle",), lane="reference")
            for path in ("a.py", "b.py", "c.py") for index in range(6)]
    rows += [row("d.py", 0, group=("oracle",), selected=False, lane="reference")]
    assert run.batches(rows, "reference") == [rows[:-1]]
    batches = run.batches(rows, "reference", resource_groups=("oracle",))
    assert [len(batch) for batch in batches] == [6, 6, 6]
    assert [entry for batch in batches for entry in batch] == rows[:-1]
    assert all(len({entry["file"] for entry in batch}) == 1 for batch in batches)
    assert all(entry["groups"] == ["oracle"] for batch in batches for entry in batch)
    assert run.batches(rows, "ordinary", resource_groups=("oracle",)) == []


def test_resource_declaration_keeps_default_transitive_components_indivisible():
    rows = [row("a.py", i, group=("oracle",), lane="reference") for i in range(11)]
    rows += [row("a.py", 20, group=("first",), lane="reference"),
             row("b.py", 0, group=("first", "second"), lane="reference"),
             row("c.py", 0, group=("second",), lane="reference"),
             row("d.py", 0, group=("oracle",), lane="reference")]
    batches = run.batches(rows, "reference", resource_groups=("oracle",))
    assert batches == [rows[:-1], rows[-1:]]


@pytest.mark.parametrize("groups", [("oracle", "other"), ("oracle", "oracle"), ("oracle", "second")])
@pytest.mark.parametrize("selected,lane", [(True, "reference"), (False, "reference"), (True, "ordinary")])
def test_resource_groups_reject_combined_keys_in_all_raw_rows(groups, selected, lane):
    rows = [row("a.py", 0, group=("oracle",), lane="reference"),
            row("b.py", 0, group=groups, selected=selected, lane=lane)]
    with pytest.raises(ValueError, match="sole xdist group"):
        run.batches(rows, "reference", resource_groups=("oracle",))


@pytest.mark.parametrize("declarations", ["oracle", ("",), (" ",), (None,),
                                          ("oracle", "oracle"), ("unknown",)])
def test_resource_group_declarations_fail_closed(declarations):
    with pytest.raises(ValueError):
        run.batches([row("a.py", 0, group=("oracle",))], "ordinary", resource_groups=declarations)


@pytest.mark.parametrize("policy", [[], {"unknown": {"oracle": "resource-exclusion-only"}},
                                    {"go": []}, {"go": {}}, {"go": {"": "resource-exclusion-only"}},
                                    {"go": {"oracle": "parallel"}}])
def test_invalid_resource_policy_fails_closed(policy):
    with pytest.raises(ValueError, match="group policy"):
        run.resource_exclusion_groups({"tool": {"pcc-ci": {"group_policy": policy}}}, "go")


def test_resource_policy_is_explicit_and_domain_scoped():
    config = {"tool": {"pcc-ci": {"group_policy": {"go": {"oracle": "resource-exclusion-only"}}}}}
    assert run.resource_exclusion_groups({}, "go") == ()
    assert run.resource_exclusion_groups(config, "go") == ("oracle",)
    assert run.resource_exclusion_groups(config, "cpp") == ()
    actual = run.tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert run.resource_exclusion_groups(actual, "go") == ("go-reference",)


@pytest.mark.parametrize("stop", [False, True])
def test_resource_campaign_admits_batches_serially_with_exact_receipts(tmp_path, monkeypatch, stop):
    """Orchestration model only; real process-tree exclusion remains a CI gate."""
    root = tmp_path / "source"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts="-n 6 --dist=loadgroup -m \'not integration\'"\n'
        '[tool.pcc-ci.group_policy.go]\noracle="resource-exclusion-only"\n')
    setup = tmp_path / "tools.json"
    setup.write_text("{}")
    monkeypatch.setenv("PCC_CI_TOOL_RECEIPT", str(setup))
    monkeypatch.setenv("GITHUB_SHA", "fixture-source")
    monkeypatch.setenv("PCC_GO_REFERENCE_CACHE", str(tmp_path / "shared-cache"))
    monkeypatch.delenv("PCC_RUSTC_INSTALL_RECEIPT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(run, "ROOT", root)
    original = {"sha": "fixture-source", "files": {}}
    monkeypatch.setattr(run, "identity", lambda: original)
    monkeypatch.setattr(tools, "verify_tool_receipt", lambda receipt: {"verified": []})
    monkeypatch.setattr(run, "modules", lambda domain: ["a.py", "b.py"])
    rows = [row(path, index, group=("oracle",), lane="reference")
            for path in ("a.py", "b.py") for index in range(6)]
    boundaries = []
    real_run_guard = run.run_guard
    monkeypatch.setattr(run.shutil, "disk_usage", lambda path: SimpleNamespace(free=2 * run.DISK_FLOOR))
    monkeypatch.setattr(run.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))

    def guarded(directory, command, environment, frozen):
        assert frozen == original
        assert environment["PCC_GO_REFERENCE_CACHE"] == str(tmp_path / "shared-cache")
        assert run.TIMEOUT == 90 and run.RSS == run.DISK_FLOOR == 1073741824
        if directory.name.startswith("collection-"):
            files = json.loads((directory / "modules.json").read_text())
            return _collection_reports(directory, files, rows)[2]
        assert "-n0" not in command and "--collect-only" not in command
        boundaries.append((directory.name, "start"))
        expected = json.loads((directory / "selection.json").read_text())
        events = [{"event": "start", "maxfail": 0}]
        for node in expected:
            events += _node_reports(node)
        events.append({"event": "finish", "exitstatus": 0, "testsfailed": 0})
        _batch_reports(directory, expected, events)
        for path in (directory / "inventory").glob("gw*.json"):
            witness = json.loads(path.read_text())
            witness["rows"] = [dict(entry, nodeid=entry["nodeid"] + "@oracle")
                               for entry in rows if entry["canonical_nodeid"] in expected]
            witness["selected"] = [node + "@oracle" for node in expected]
            path.write_text(json.dumps(witness))
        live = directory / "live.jsonl"
        live.write_text(live.read_text().replace("@group", "@oracle"))
        boundaries.append((directory.name, "finish"))
        (directory / "result.json").write_text(json.dumps({
            "status": "COMPLETE", "timeout_s": run.TIMEOUT, "max_tree_rss_bytes": run.RSS,
            "peak_tree_rss_bytes": 0, "returncode": 0,
            "post_exit_cleanup_pids": [12345] if stop else [],
        }))
        return real_run_guard(directory, command, environment, frozen)

    monkeypatch.setattr(run, "run_guard", guarded)
    out = tmp_path / "campaign"
    assert run.run(SimpleNamespace(domain="go", lane="reference", out=out)) == int(stop)
    receipt = json.loads((out / "receipt.json").read_text())
    assert receipt["group_policy"] == {"oracle": "resource-exclusion-only"}
    assert receipt["workers"] == 6 and receipt["dist"] == "loadgroup"
    assert json.loads((out / "batch-plan.json").read_text()) == [
        [entry["canonical_nodeid"] for entry in rows[:6]],
        [entry["canonical_nodeid"] for entry in rows[6:]]]
    assert boundaries == [(f"batch-{index:03}", phase)
                          for index in range(1 if stop else 2) for phase in ("start", "finish")]
    assert receipt["unattempted"] == ([entry["canonical_nodeid"] for entry in rows[6:]] if stop else [])
    assert receipt["status"] == ("INFRASTRUCTURE_OR_RESOURCE_FAILURE" if stop else "PASS")
    assert all(not batch["evidence_errors"] for batch in receipt["batches"])


def test_combined_declared_resource_groups_are_not_an_exclusion_key():
    with pytest.raises(ValueError, match="sole xdist group"):
        run.batches([row("a.py", 0, group=("first", "second"))], "ordinary",
                    resource_groups=("first", "second"))


def test_duplicate_resource_policy_names_are_rejected_by_toml():
    with pytest.raises(run.tomllib.TOMLDecodeError):
        run.tomllib.loads('[tool.pcc-ci.group_policy.go]\n'
                          'oracle="resource-exclusion-only"\n'
                          'oracle="resource-exclusion-only"\n')


@pytest.mark.parametrize("cleanup", [None, [], [12345]])
def test_guard_rejects_uncertain_post_exit_descendant_cleanup(tmp_path, monkeypatch, cleanup):
    """Receipt model only: a successful root exit does not prove descendants exited."""
    original = {"sha": "fixture"}
    monkeypatch.setattr(run, "identity", lambda: original)
    monkeypatch.setattr(run.shutil, "disk_usage", lambda path: SimpleNamespace(free=2 * run.DISK_FLOOR))
    monkeypatch.setattr(run.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    result = {"status": "COMPLETE", "timeout_s": run.TIMEOUT, "max_tree_rss_bytes": run.RSS,
              "peak_tree_rss_bytes": 0, "returncode": 0}
    if cleanup is not None:
        result["post_exit_cleanup_pids"] = cleanup
    (tmp_path / "result.json").write_text(json.dumps(result))
    if cleanup:
        with pytest.raises(ValueError, match="resource/guard anomaly; no later batch admitted"):
            run.run_guard(tmp_path, ["unused"], {}, original)
    else:
        assert run.run_guard(tmp_path, ["unused"], {}, original) == 0


@pytest.mark.parametrize("interrupt_at", ["collection-001", "batch-001"])
def test_campaign_keyboard_interrupt_persists_terminal_status_and_propagates(
        tmp_path, monkeypatch, interrupt_at):
    """Orchestration model only, not OS signal or child-cleanup qualification."""
    root = tmp_path / "source"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts=\"-n 6 --dist=loadgroup -m 'not integration'\"\n")
    setup = tmp_path / "tools.json"
    setup.write_text("{}")
    monkeypatch.setenv("PCC_CI_TOOL_RECEIPT", str(setup))
    monkeypatch.setenv("GITHUB_SHA", "fixture-source")
    monkeypatch.delenv("PCC_RUSTC_INSTALL_RECEIPT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(run, "ROOT", root)
    monkeypatch.setattr(run, "identity", lambda: {"sha": "fixture-source", "files": {}})
    closure_checks = []

    def verify_tools(receipt):
        closure_checks.append(receipt)
        return {"verified": []}

    monkeypatch.setattr(tools, "verify_tool_receipt", verify_tools)
    files = [f"module_{index:02}.py" for index in range(11)]
    rows = [row(path, index, lane="reference") for path in files for index in range(6)]
    monkeypatch.setattr(run, "modules", lambda domain: files)
    interruption = KeyboardInterrupt("fixture interruption")
    admitted = []

    def guarded(directory, command, environment, frozen):
        admitted.append(directory.name)
        if directory.name == interrupt_at:
            raise interruption
        if directory.name.startswith("collection-"):
            shard = json.loads((directory / "modules.json").read_text())
            return _collection_reports(directory, shard, [r for r in rows if r["file"] in shard])[2]
        return 0

    # Model a previously completed batch; evidence validation has separate tests.
    def summarized(directory, expected, frozen_rows):
        return {"outcomes": [{"nodeid": node, "status": "PASS", "terminal_node": True}
                             for node in expected],
                "pytest_exitstatus": 0, "pytest_testsfailed": 0,
                "session_events": [], "evidence_errors": [], "unattributed_reports": []}

    monkeypatch.setattr(run, "run_guard", guarded)
    monkeypatch.setattr(run, "summarize_batch", summarized)
    out = tmp_path / "campaign"
    with pytest.raises(KeyboardInterrupt) as caught:
        run.run(SimpleNamespace(domain="go", lane="reference", out=out))
    assert caught.value is interruption
    receipt = json.loads((out / "receipt.json").read_text())
    assert receipt["status"] == "INTERRUPTED"
    assert receipt["error"] == "KeyboardInterrupt: fixture interruption"
    assert closure_checks == [{}, {}]
    assert json.loads((out / "tools-after.json").read_text()) == {"verified": []}
    assert admitted[-1] == interrupt_at
    if interrupt_at.startswith("collection-"):
        assert admitted == ["collection-000", "collection-001"]
        assert receipt["collections"][0]["status"] == "COLLECTED_NOT_EXECUTED"
        assert receipt["collections"][1]["status"] == "COLLECTION_INCOMPLETE"
        assert receipt["collection_pending_modules"] == files[10:]
        assert receipt["batches"] == []
    else:
        assert admitted == ["collection-000", "collection-001", "batch-000", "batch-001"]
        assert len(receipt["batches"]) == 1
        assert receipt["batches"][0]["directory"] == "batch-000"
        assert receipt["batches"][0]["outcomes"] == [
            {"nodeid": entry["canonical_nodeid"], "status": "PASS", "terminal_node": True}
            for entry in rows[:6]]
        assert receipt["unattempted"] == [entry["canonical_nodeid"] for entry in rows[6:]]
        assert not (out / "batch-001" / "outcomes.json").exists()
        assert not (out / "batch-002").exists()


def test_static_data_member_references_keep_owned_checks_in_their_original_lane():
    module = "tests/cpp/test_cpp_static_data_members.py"
    registry = json.loads(run.REFERENCES.read_text())["functions"]
    references = {
        "test_reference_compiler_returns_the_same_value",
        "test_reference_compiler_rejects_the_same_sources",
        "test_programs_execute_against_real_cpp17_reference_on_linux",
    }
    assert set(registry[module]) == references
    tree = ast.parse((ROOT / module).read_text())
    functions = {node.name: node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
    assert references <= functions.keys()
    for name, function in functions.items():
        marks = {ast.unparse(mark.func if isinstance(mark, ast.Call) else mark)
                 for mark in function.decorator_list}
        integration = "pytest.mark.integration" in marks
        if name in references:
            assert registry[module][name]["activity"] == "external-reference"
            assert not registry[module][name].get("conditional")
        assert classify(module, name, integration, registry) == (
            "reference" if name in references else "integration" if integration else "ordinary")


def test_protected_base_constructor_references_keep_owned_checks_in_their_lane():
    module = "tests/cpp/test_cpp_protected_base_constructors.py"
    registry = json.loads(run.REFERENCES.read_text())["functions"]
    reference_calls = {
        "test_reference_compiler_returns_the_same_value": "run_reference_cpp",
        "test_reference_compiler_rejects_the_same_sources": "reference_rejects",
        "test_programs_execute_against_real_cpp17_reference_on_linux": "execute_row_pair",
    }
    owned_calls = {
        "test_programs_compile_to_stable_linux_artifacts": "compile_host_cpp",
        "test_programs_run_natively_with_owned_compile_assemble_and_link": "run_owned_cpp",
        "test_inaccessible_constructors_are_ordinary_errors": "CppParser",
    }
    assert set(registry[module]) == set(reference_calls)
    tree = ast.parse((ROOT / module).read_text())
    functions = {node.name: node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
    assert reference_calls.keys() | owned_calls.keys() == functions.keys()
    for name, function in functions.items():
        calls = {node.func.id for node in ast.walk(function)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        assert (reference_calls | owned_calls)[name] in calls
        marks = {ast.unparse(mark.func if isinstance(mark, ast.Call) else mark)
                 for mark in function.decorator_list}
        integration = "pytest.mark.integration" in marks
        assert integration == (name == "test_programs_execute_against_real_cpp17_reference_on_linux")
        if name in reference_calls:
            assert registry[module][name]["activity"] == "external-reference"
            assert not registry[module][name].get("conditional")
        else:
            assert not calls & set(reference_calls.values())
        assert classify(module, name, integration, registry) == (
            "reference" if name in reference_calls else "ordinary")


def test_i64_min_max_oracles_keep_native_only_ownership_in_integration():
    module = "tests/python/test_native_min_max_i64_ownership.py"
    registry = json.loads(run.REFERENCES.read_text())["functions"]
    reference_calls = {
        "test_i64_min_max_reference": "assert_reference_program",
        "test_i64_min_max_five_gc": "assert_owned_program",
    }
    native_only = "test_i64_min_max_releases_boxed_items"
    ir_only = "test_i64_fold_publishes_each_item_before_cleanup"
    assert set(registry[module]) == set(reference_calls)
    tree = ast.parse((ROOT / module).read_text())
    functions = {node.name: node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
    assert functions.keys() == reference_calls.keys() | {native_only, ir_only}
    for name, function in functions.items():
        calls = {node.func.id for node in ast.walk(function)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        marks = {ast.unparse(mark.func if isinstance(mark, ast.Call) else mark)
                 for mark in function.decorator_list}
        integration = "pytest.mark.integration" in marks
        assert integration == (name in {"test_i64_min_max_five_gc", native_only})
        if name in reference_calls:
            assert reference_calls[name] in calls
            assert registry[module][name]["activity"] == "external-reference"
            assert not registry[module][name].get("conditional")
        else:
            assert not calls & set(reference_calls.values())
        if name == native_only:
            assert {"python_program_compiler", "run_process_group_timeout"} <= calls
        assert classify(module, name, integration, registry) == (
            "reference" if name in reference_calls
            else "integration" if name == native_only else "ordinary")


@pytest.mark.parametrize("module,reference,helper,reference_integration,siblings", [
    (
        'tests/java/test_source_static_initializers.py',
        'test_static_initializer_originals_against_selected_jdk_only',
        'check_originals_reference', True, {
            'test_a_block_without_fields_is_still_a_step',
            'test_blocks_and_initializers_are_steps_in_textual_order_and_blank_finals_are_slots',
            'test_former_empty_static_block_rows_are_checked_steps',
            'test_invalid_static_initializer_originals_fail_as_java_errors',
            'test_native_domain_still_rejects_static_initialization',
            'test_nested_blank_final_assignments_stay_explicit',
            'test_obligation_names_each_step_and_the_static_reference_root',
            'test_reference_typed_blank_finals_keep_the_static_reference_boundary',
            'test_static_initializer_originals_are_checked_in_the_class_model',
        },
    ),
    (
        'tests/go/test_defer_statements.py',
        'test_reference_accepts_and_rejects_the_same_sources',
        'reference_rejection', False, {
            'test_a_deferred_function_literal_reports_the_function_value_gap_first',
            'test_defer_keeps_its_call',
            'test_deferred_calls_are_an_explicit_checker_gap',
            'test_invalid_defer_statements_are_source_errors',
            'test_math_bits_external_test_package_now_stops_at_a_type_assertion',
        },
    ),
])
def test_static_initializer_and_defer_oracles_preserve_owned_sibling_lanes(
        module, reference, helper, reference_integration, siblings):
    registry = json.loads(run.REFERENCES.read_text())["functions"]
    assert set(registry[module]) == {reference}
    tree = ast.parse((ROOT / module).read_text())
    functions = {node.name: node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
    assert functions.keys() == siblings | {reference}
    for name, function in functions.items():
        calls = {node.func.id for node in ast.walk(function)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        marks = {ast.unparse(mark.func if isinstance(mark, ast.Call) else mark)
                 for mark in function.decorator_list}
        integration = "pytest.mark.integration" in marks
        assert integration == (name == reference and reference_integration)
        if name == reference:
            assert helper in calls
            assert registry[module][name]["activity"] == "external-reference"
            assert not registry[module][name].get("conditional")
        else:
            assert helper not in calls
        assert classify(module, name, integration, registry) == (
            "reference" if name == reference else "ordinary")
