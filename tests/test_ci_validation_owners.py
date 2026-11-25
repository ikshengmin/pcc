"""Authored platform coverage models. These never invoke PCC or pytest children."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import ci_validation_owners as owners
from scripts import ci_validation_run as run
from scripts import ci_validation_tools as tools
from scripts.ci_validation_inventory import classify

MODULE = "tests/cpp/test_headers.py"
LINUX = "linux-x86_64"
MAC = "macos-arm64"
ROOT = Path(__file__).resolve().parents[1]


def cpp_darwin_rows():
    """Read source declarations, never import the native fixtures or run PCC."""
    registry = json.loads(run.REFERENCES.read_text())["functions"]
    rows = []
    counts = {"deferred_construction_proofs": 3, "mixed_static_overloads": 4,
              "protected_base_constructors": 5, "static_data_members": 10, "static_member_arrays": 7}
    native_name = "test_programs_run_natively_with_owned_compile_assemble_and_link"
    assert {path.relative_to(ROOT).as_posix() for path in (ROOT / "tests/cpp").glob("test_*.py")
            if "cpp-darwin-native" in path.read_text()} == {
                f"tests/cpp/test_cpp_{stem}.py" for stem in counts}
    for stem, count in counts.items():
        module = f"tests/cpp/test_cpp_{stem}.py"
        tree = ast.parse((ROOT / module).read_text())
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        programs = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "PROGRAMS" for target in node.targets))
        assert len(programs) == count
        grouped = set()
        for function in functions.values():
            marks = {ast.unparse(mark.func): mark for mark in function.decorator_list if isinstance(mark, ast.Call)}
            if function.name == native_name:
                declaration = marks["pytest.mark.pcc_source_group"]
                assert not declaration.args
                assert {keyword.arg: ast.literal_eval(keyword.value) for keyword in declaration.keywords} == {
                    "legacy": "cpp-darwin-native", "name": "cpp-darwin-owned"}
                assert ast.unparse(marks["pytest.mark.pcc_gate"].keywords[0].value) == "UNAVAILABLE"
            else:
                assert "pytest.mark.pcc_source_group" not in marks
            if "pytest.mark.xdist_group" in marks:
                grouped.add(function.name)
                group = marks["pytest.mark.xdist_group"]
                assert not group.args
                assert {keyword.arg: ast.literal_eval(keyword.value) for keyword in group.keywords} == {
                    "name": "cpp-darwin-native"}
        assert grouped == {native_name, "test_reference_compiler_returns_the_same_value"} | (
            {"test_reference_compiler_rejects_the_same_sources"}
            if "test_reference_compiler_rejects_the_same_sources" in functions else set())
        for name in (native_name, "test_reference_compiler_returns_the_same_value",
                     "test_programs_compile_to_stable_linux_artifacts", "test_reference_compiler_rejects_the_same_sources"):
            if name not in functions:
                continue
            assert not any(ast.unparse(mark) == "pytest.mark.integration" for mark in functions[name].decorator_list)
            suffixes = [f"[{program[0]}]" for program in programs] if name != "test_reference_compiler_rejects_the_same_sources" else [""]
            for suffix in suffixes:
                node = module + "::" + name + suffix
                is_native = name == native_name
                rows.append({"file": module, "function": name, "nodeid": node, "canonical_nodeid": node,
                             "lane": classify(module, name, False, registry),
                             "groups": ["cpp-darwin-owned"] if is_native else
                                       ["cpp-darwin-native"] if name.startswith("test_reference") else [],
                             "gates": [{"unavailable": "Darwin required"}] if is_native else [],
                             "selected_by_repository": not is_native,
                             "reference_activity": registry.get(module, {}).get(name, "unknown")})
    return sorted(rows, key=lambda entry: entry["canonical_nodeid"])


def go_darwin_rows():
    """Exact shared Go group from source literals; no Go test module import."""
    registry = json.loads(run.REFERENCES.read_text())["functions"]
    native_functions = {
        "tests/go/test_elided_composite_literals.py":
            "test_fixture_runs_natively_with_owned_compile_assemble_and_link",
        "tests/go/test_floating_types.py": "test_program_runs_with_owned_compile_assemble_and_link",
    }
    grouped_functions = {
        "test_defer_statements": {"test_reference_accepts_and_rejects_the_same_sources"},
        "test_elided_composite_literals": {
            "test_fixture_runs_natively_with_owned_compile_assemble_and_link",
            "test_reference_toolchain_computes_the_same_values",
            "test_reference_rejects_the_same_sources_at_the_same_positions"},
        "test_floating_types": {"test_program_runs_with_owned_compile_assemble_and_link",
            "test_reference_returns_the_same_value", "test_reference_accepts_and_rejects_the_same_sources"},
        "test_function_literal_syntax": {"test_reference_accepts_the_same_sources"},
        "test_owned_runtime_package": {"test_reference_gives_the_same_verdicts"},
        "test_type_assertions": {"test_reference_accepts_and_rejects_the_same_sources"},
    }
    modules = {f"tests/go/{stem}.py" for stem in grouped_functions}
    assert {path.relative_to(ROOT).as_posix() for path in (ROOT / "tests/go").glob("test_*.py")
            if "go-darwin-native" in path.read_text()} == modules
    rows = []
    for module in sorted(modules):
        tree = ast.parse((ROOT / module).read_text())
        expected = next((ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                         and any(isinstance(target, ast.Name) and target.id == "EXPECTED"
                                 for target in node.targets)), None)
        found = set()
        for function in (node for node in tree.body if isinstance(node, ast.FunctionDef)):
            marks = {ast.unparse(mark.func): mark for mark in function.decorator_list if isinstance(mark, ast.Call)}
            is_native = function.name == native_functions.get(module)
            if is_native:
                declaration = marks["pytest.mark.pcc_source_group"]
                assert not declaration.args
                assert {keyword.arg: ast.literal_eval(keyword.value) for keyword in declaration.keywords} == {
                    "legacy": "go-darwin-native", "name": "go-darwin-owned"}
            else:
                assert "pytest.mark.pcc_source_group" not in marks
            if "pytest.mark.xdist_group" not in marks:
                continue
            found.add(function.name)
            group = marks["pytest.mark.xdist_group"]
            assert not group.args and {keyword.arg: ast.literal_eval(keyword.value)
                for keyword in group.keywords} == {"name": "go-darwin-native"}
            gate = marks["pytest.mark.pcc_gate"]
            assert not gate.args and len(gate.keywords) == 1 and gate.keywords[0].arg == "unavailable"
            assert ast.unparse(gate.keywords[0].value) == ("UNAVAILABLE" if is_native else "REFERENCE_UNAVAILABLE")
            assert not any(ast.unparse(mark) == "pytest.mark.integration" for mark in function.decorator_list)
            suffixes = [""]
            if "pytest.mark.parametrize" in marks:
                param = marks["pytest.mark.parametrize"]
                assert not param.keywords and len(param.args) == 2
                assert ast.literal_eval(param.args[0]) == "function,expected" and ast.unparse(param.args[1]) == "EXPECTED"
                assert expected == (("Rows", 254), ("Anonymous", 270480), ("Indexed", 9478), ("Nested", 123546))
                suffixes = [f"[{name}-{value}]" for name, value in expected]
            for suffix in suffixes:
                node = module + "::" + function.name + suffix
                rows.append({"file": module, "function": function.name, "nodeid": node, "canonical_nodeid": node,
                    "lane": classify(module, function.name, False, registry),
                    "groups": ["go-darwin-owned" if is_native else "go-darwin-native"],
                    "gates": [{"unavailable": "Darwin required" if is_native else None}],
                    "selected_by_repository": not is_native,
                    "reference_activity": registry.get(module, {}).get(function.name, "unknown")})
        assert found == grouped_functions[Path(module).stem]
    assert len(rows) == 16
    assert sum(row["groups"] == ["go-darwin-owned"] for row in rows) == 5
    assert sum(row["groups"] == ["go-darwin-native"] and row["lane"] == "ordinary" for row in rows) == 9
    assert sum(row["lane"] == "reference" for row in rows) == 2
    return sorted(rows, key=lambda row: row["canonical_nodeid"])


def row(name, *, selected=True, lane="reference", groups=()):
    return {"file": MODULE, "function": name, "nodeid": MODULE + "::" + name,
            "canonical_nodeid": MODULE + "::" + name, "lane": lane,
            "selected_by_repository": selected, "groups": list(groups),
            "gates": [], "reference_activity": "external-reference"}


def manifest(platforms=None):
    platforms = platforms or [MAC]
    return {"schema": 1, "default_platform": LINUX, "overrides": [
        {"domain": "cpp", "lane": "reference", "nodeid": row("darwin")["nodeid"],
         "platforms": platforms, "tools": {platform: ["clang"] for platform in platforms}}]}


def test_mixed_module_partition_preserves_raw_gate_metadata():
    rows = [row("linux"), row("darwin", selected=False)]
    frozen = copy.deepcopy(rows)
    linux = owners.partition(rows, manifest(), "cpp", "reference", LINUX)
    assert linux["local_nodeids"] == [row("linux")["nodeid"]]
    assert linux["transfers"] == [dict(domain="cpp", lane="reference", nodeid=row("darwin")["nodeid"],
                                      platform=MAC, tools=["clang"])]
    mac = owners.partition(rows, manifest(), "cpp", "reference", MAC, complete=False)
    assert mac["local_nodeids"] == [row("darwin")["nodeid"]]
    assert rows == frozen
    assert not rows[1]["selected_by_repository"]  # A missing owner gate remains UNRUN.
    assert owners.owner_modules(manifest(), "cpp", "reference", MAC) == [MODULE]
    assert owners.additional_campaigns(["cpp"], manifest()) == [
        {"domain": "cpp", "lane": "reference", "platform": MAC, "runner": "macos-15"}]

    actual, _ = owners.read_manifest(ROOT)
    cpp_rows = cpp_darwin_rows()
    native = [entry for entry in cpp_rows if entry["groups"] == ["cpp-darwin-owned"]]
    reference = [entry for entry in cpp_rows if entry["groups"] == ["cpp-darwin-native"]]
    assert len(native) == 29 and all(entry["lane"] == "ordinary" for entry in native)
    assert sum(entry["lane"] == "ordinary" for entry in reference) == 16
    assert sum(entry["lane"] == "reference" for entry in reference) == 17
    native_ids = [entry["canonical_nodeid"] for entry in native]
    assert [rule for rule in actual["overrides"] if rule["domain"] == "cpp" and rule["lane"] == "ordinary"] == [
        {"domain": "cpp", "lane": "ordinary", "nodeid": node, "platforms": [MAC], "tools": {MAC: []}}
        for node in native_ids]
    # Full-domain partition also checks the existing, independent SDK rule.
    sdk, = [rule for rule in actual["overrides"] if rule["domain"] == "cpp" and rule["lane"] == "reference"]
    sdk_file, sdk_function = sdk["nodeid"].split("::")
    all_rows = [*cpp_rows, {"file": sdk_file, "function": sdk_function, "nodeid": sdk["nodeid"],
                          "canonical_nodeid": sdk["nodeid"], "lane": "reference", "groups": [], "gates": [],
                          "selected_by_repository": False, "reference_activity": "external-reference"}]
    frozen = copy.deepcopy(all_rows)
    linux = owners.partition(all_rows, actual, "cpp", "ordinary", LINUX)
    assert linux["local_nodeids"] == sorted(entry["canonical_nodeid"] for entry in cpp_rows
                                           if entry["lane"] == "ordinary" and entry not in native)
    assert linux["transfers"] == [dict(domain="cpp", lane="ordinary", nodeid=node, platform=MAC, tools=[])
                                    for node in native_ids]
    assert all_rows == frozen
    mac_rows = copy.deepcopy(cpp_rows)
    for entry in mac_rows:
        if entry["canonical_nodeid"] in native_ids:
            entry.update(selected_by_repository=True, gates=[{"unavailable": None}])
    mac = owners.partition(mac_rows, actual, "cpp", "ordinary", MAC, complete=False)
    assert mac["local_nodeids"] == native_ids
    assert {entry["nodeid"] for entry in mac["transfers"]} == set(linux["local_nodeids"])
    assert owners.owner_modules(actual, "cpp", "ordinary", MAC) == sorted({entry["file"] for entry in native})

    # The routing declaration never satisfies an unavailable prerequisite.
    unavailable = owners.partition(cpp_rows, actual, "cpp", "ordinary", MAC, complete=False)
    unrun = [dict(entry, status="UNRUN_PREREQUISITE") for entry in cpp_rows
             if entry["canonical_nodeid"] in unavailable["local_nodeids"] and not entry["selected_by_repository"]]
    assert len(unrun) == 29 and all(entry["groups"] == ["cpp-darwin-owned"] for entry in unrun)
    assert run.campaign_status({"batches": [], "unrun": unrun, "lane_count": 29,
                                "coverage": unavailable}) == "INCOMPLETE_UNRUN"

    # Neither an omitted/wrong owner nor bypassing the explicit source-only
    # declaration can silently split the serial native group.
    for wrong in ("missing", "linux"):
        bad = copy.deepcopy(actual)
        target = next(rule for rule in bad["overrides"] if rule["nodeid"] == native_ids[0])
        if wrong == "missing":
            bad["overrides"].remove(target)
        else:
            target.update(platforms=[LINUX], tools={LINUX: []})
        with pytest.raises(ValueError, match="splits xdist group"):
            owners.partition(all_rows, bad, "cpp", "ordinary", LINUX)
    legacy_rows = copy.deepcopy(all_rows)
    for entry in legacy_rows:
        if entry["canonical_nodeid"] in native_ids:
            entry["groups"] = ["cpp-darwin-native"]
    with pytest.raises(ValueError, match="splits xdist group"):
        owners.partition(legacy_rows, actual, "cpp", "ordinary", LINUX)


    # Go has the same mixed legacy mutex, with five owned nodes and eleven
    # untouched host-native Go oracles across the original two lanes.
    go_rows = go_darwin_rows()
    frozen_go = copy.deepcopy(go_rows)
    native_ids = [entry["canonical_nodeid"] for entry in go_rows if entry["groups"] == ["go-darwin-owned"]]
    expected_rules = [{"domain": "go", "lane": "ordinary", "nodeid": node,
                       "platforms": [MAC], "tools": {MAC: []}} for node in native_ids]
    assert [rule for rule in actual["overrides"] if rule["domain"] == "go"] == expected_rules
    linux = owners.partition(go_rows, actual, "go", "ordinary", LINUX)
    assert len(linux["local_nodeids"]) == 9
    assert linux["transfers"] == [dict(domain="go", lane="ordinary", nodeid=node, platform=MAC, tools=[])
                                  for node in native_ids]
    reference = owners.partition(go_rows, actual, "go", "reference", LINUX)
    assert len(reference["local_nodeids"]) == 2 and reference["transfers"] == []
    assert owners.additional_campaigns(["go"], actual) == [
        dict(domain="go", lane="ordinary", platform=MAC, runner="macos-15")]
    modules = owners.owner_modules(actual, "go", "ordinary", MAC)
    assert modules == ["tests/go/test_elided_composite_literals.py", "tests/go/test_floating_types.py"]
    mac_rows = [copy.deepcopy(entry) for entry in go_rows if entry["file"] in modules]
    unavailable = owners.partition(mac_rows, actual, "go", "ordinary", MAC, complete=False)
    assert unavailable["local_nodeids"] == native_ids
    unrun = [dict(entry, status="UNRUN_PREREQUISITE") for entry in mac_rows
             if entry["canonical_nodeid"] in native_ids and not entry["selected_by_repository"]]
    assert len(unrun) == 5
    assert run.campaign_status({"batches": [], "unrun": unrun, "lane_count": 5,
                               "coverage": unavailable}) == "INCOMPLETE_UNRUN"
    for entry in mac_rows:
        if entry["canonical_nodeid"] in native_ids:
            entry.update(selected_by_repository=True, gates=[{"unavailable": None}])
    mac = owners.partition(mac_rows, actual, "go", "ordinary", MAC, complete=False)
    assert mac["local_nodeids"] == native_ids and len(mac["transfers"]) == 7
    assert owners.required_tool_bindings(mac["obligations"], MAC,
        {"go": {"status": "UNRUN_PREREQUISITE"}}) == {"platform": MAC, "bindings": []}
    assert go_rows == frozen_go
    for wrong in ("missing", "linux", "both"):
        bad = copy.deepcopy(actual)
        target = next(rule for rule in bad["overrides"] if rule["nodeid"] == native_ids[0])
        if wrong == "missing":
            bad["overrides"].remove(target)
        else:
            platforms = [LINUX] if wrong == "linux" else [LINUX, MAC]
            target.update(platforms=platforms, tools={platform: [] for platform in platforms})
        with pytest.raises(ValueError, match="splits xdist group"):
            owners.partition(go_rows, bad, "go", "ordinary", LINUX)
    legacy_rows = copy.deepcopy(go_rows)
    for entry in legacy_rows:
        entry["groups"] = ["go-darwin-native"]
    with pytest.raises(ValueError, match="splits xdist group"):
        owners.partition(legacy_rows, actual, "go", "ordinary", LINUX)


def test_all_of_owners_never_becomes_fallback():
    result = owners.partition([row("darwin")], manifest([LINUX, MAC]), "cpp", "reference", LINUX)
    assert len(result["obligations"]) == 2
    assert result["local_nodeids"] == [row("darwin")["nodeid"]]
    assert result["transfers"][0]["platform"] == MAC


@pytest.mark.parametrize("change", [
    lambda m: m.update(schema=True),
    lambda m: m.update(extra="unknown"),
    lambda m: m["overrides"].append(copy.deepcopy(m["overrides"][0])),
    lambda m: m["overrides"][0].update(platforms=[MAC, MAC]),
    lambda m: m["overrides"][0].update(platforms=["windows-x86_64"]),
    lambda m: m["overrides"][0].update(lane="fallback"),
    lambda m: m["overrides"][0].update(nodeid="../test.py::test_bad"),
    lambda m: m["overrides"][0].update(tools={}),
    lambda m: m["overrides"][0].update(tools={MAC: ["clang", "clang"]}),
])
def test_malformed_or_duplicate_rules_fail_closed(change):
    value = manifest()
    change(value)
    with pytest.raises(ValueError):
        owners.validate_manifest(value)


@pytest.mark.parametrize("rows,match", [
    ([row("linux")], "stale"),
    ([row("darwin", lane="ordinary")], "lane mismatch"),
    ([row("darwin"), row("darwin")], "duplicate"),
    ([row("linux", groups=["dependency"]), row("darwin", groups=["dependency"])], "splits"),
])
def test_inventory_rules_fail_closed(rows, match):
    with pytest.raises(ValueError, match=match):
        owners.partition(rows, manifest(), "cpp", "reference", LINUX)


def test_stale_rules_checked_across_lanes_but_not_uncollected_mac_modules():
    value = manifest()
    other = copy.deepcopy(value["overrides"][0])
    other.update(nodeid="tests/cpp/test_other.py::test_other", lane="ordinary")
    value["overrides"].append(other)
    with pytest.raises(ValueError, match="stale"):
        owners.partition([row("darwin")], value, "cpp", "reference", LINUX)
    assert owners.partition([row("darwin")], value, "cpp", "reference", MAC,
                            complete=False)["local_nodeids"] == [row("darwin")["nodeid"]]


def test_parameterized_exact_ids_are_distinct():
    value = manifest()
    value["overrides"][0]["nodeid"] = row("darwin[a]")["nodeid"]
    result = owners.partition([row("darwin[a]"), row("darwin[b]")], value, "cpp", "reference", LINUX)
    assert result["local_nodeids"] == [row("darwin[b]")["nodeid"]]


def test_manifest_digest_is_exact_bytes_and_duplicate_keys_rejected(tmp_path):
    path = tmp_path / owners.MANIFEST
    path.parent.mkdir(parents=True)
    data = json.dumps(manifest()).encode() + b"\n"
    path.write_bytes(data)
    assert owners.read_manifest(tmp_path) == (manifest(), hashlib.sha256(data).hexdigest())
    path.write_text('{"schema":1,"schema":1}')
    with pytest.raises(ValueError, match="duplicate"):
        owners.read_manifest(tmp_path)


def context(monkeypatch):
    for name, value in {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_SHA": "a" * 40,
                        "GITHUB_RUN_ID": "12", "GITHUB_RUN_ATTEMPT": "2"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(owners.host_platform, "system", lambda: "Linux")
    monkeypatch.setattr(owners.host_platform, "machine", lambda: "x86_64")


@pytest.mark.parametrize("key,value", [("GITHUB_SHA", "main"), ("GITHUB_RUN_ID", "0"),
                                        ("GITHUB_RUN_ATTEMPT", "-1"), ("GITHUB_REPOSITORY", "repo")])
def test_invalid_context_rejected(monkeypatch, key, value):
    context(monkeypatch)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="context"):
        owners.host_context(LINUX)


def test_actual_host_cannot_be_relabelled(monkeypatch):
    context(monkeypatch)
    assert owners.host_context(LINUX)[1] == {"platform": LINUX, "system": "Linux", "machine": "x86_64"}
    with pytest.raises(ValueError, match="actual host"):
        owners.host_context(MAC)
    monkeypatch.setattr(owners.host_platform, "machine", lambda: "aarch64")
    with pytest.raises(ValueError, match="actual host"):
        owners.host_context(LINUX)


def test_required_tools_bind_actual_path_and_available_receipt(tmp_path, monkeypatch):
    path = tmp_path / "clang"
    path.write_bytes(b"audited fixture")
    monkeypatch.setattr(owners.shutil, "which", lambda _: str(path))
    obligation = {"platform": MAC, "tools": ["clang"]}
    receipt = {"clang": {"status": "AVAILABLE", "invocation": str(path), "resolved": str(path.resolve()),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
    before = owners.required_tool_bindings([obligation], MAC, receipt)
    assert before["bindings"][0]["tool"] == "clang"
    for key, value in [("status", "UNRUN_PREREQUISITE"), ("invocation", "/other/clang"),
                       ("resolved", "/other/clang"), ("sha256", "0" * 64)]:
        bad = copy.deepcopy(receipt)
        bad["clang"][key] = value
        with pytest.raises(ValueError):
            owners.required_tool_bindings([obligation], MAC, bad)
    path.write_bytes(b"changed")
    with pytest.raises(ValueError):
        owners.required_tool_bindings([obligation], MAC, receipt)


@pytest.mark.parametrize("platform", [LINUX, MAC])
@pytest.mark.parametrize("failure", [None, "missing_gate", "interrupt", "tool_closure", "manifest_closure", "path_closure"])
def test_mocked_campaign_transfers_never_pass_or_mask_local_failures(tmp_path, monkeypatch, failure, platform):
    root = tmp_path / "source"
    root.mkdir()
    (root / "pyproject.toml").write_text("# mocked configuration\n")
    # Use parsed configuration directly: this is a runner model, not a TOML test.
    monkeypatch.setattr(run.tomllib, "loads", lambda _: {"tool": {"pytest": {"ini_options": {
        "addopts": '-n 6 --dist=loadgroup -m "not integration"'}}}})
    path = root / owners.MANIFEST
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest()))
    frozen, digest = owners.read_manifest(root)
    identity = {"sha": "a" * 40, "files": {}, "sparse_omissions": [],
                "ownership_manifest": frozen, "ownership_manifest_sha256": digest}
    context(monkeypatch)
    if platform == MAC:
        monkeypatch.setattr(owners.host_platform, "system", lambda: "Darwin")
        monkeypatch.setattr(owners.host_platform, "machine", lambda: "arm64")
    monkeypatch.setattr(run, "ROOT", root)
    monkeypatch.setattr(run, "identity", lambda: identity)
    monkeypatch.setattr(run, "modules", lambda _: [MODULE])
    tool_path = tmp_path / "tools.json"
    executable = tmp_path / "clang"
    executable.write_bytes(b"audited tool fixture")
    monkeypatch.setattr(owners.shutil, "which", lambda _: str(executable))
    tool_path.write_text(json.dumps({"clang": {"status": "AVAILABLE", "invocation": str(executable),
        "resolved": str(executable.resolve()), "sha256": hashlib.sha256(executable.read_bytes()).hexdigest()}}))
    monkeypatch.setenv("PCC_CI_TOOL_RECEIPT", str(tool_path))
    monkeypatch.delenv("PCC_RUSTC_INSTALL_RECEIPT", raising=False)
    checks = []
    def verify(_):
        checks.append(True)
        if failure == "tool_closure" and len(checks) == 2:
            raise ValueError("changed tool")
        return {"verified": []}
    monkeypatch.setattr(tools, "verify_tool_receipt", verify)
    rows = [row("linux", selected=platform == LINUX and failure != "missing_gate"),
            row("darwin", selected=platform == MAC and failure != "missing_gate")]
    monkeypatch.setattr(run, "summarize_collection", lambda *_: {
        "modules": [MODULE], "rows": rows, "selected_count": 1, "status": "COLLECTED_NOT_EXECUTED"})
    selections = []
    def guard(directory, command, env, original):
        if directory.name.startswith("batch"):
            selections.append(json.loads((directory / "selection.json").read_text()))
            if failure == "interrupt":
                raise KeyboardInterrupt("fixture")
            if failure == "manifest_closure":
                path.write_text(json.dumps(manifest()) + "\n")
            if failure == "path_closure":
                monkeypatch.setattr(owners.shutil, "which", lambda _: "/changed/clang")
        return 0
    monkeypatch.setattr(run, "run_guard", guard)
    def summarize(directory, expected, raw):
        assert raw == rows  # Planner partition must not contaminate raw witnesses.
        return {"outcomes": [{"nodeid": node, "status": "PASS", "terminal_node": True} for node in expected],
                "pytest_exitstatus": 0, "pytest_testsfailed": 0, "session_events": [],
                "evidence_errors": [], "unattributed_reports": []}
    monkeypatch.setattr(run, "summarize_batch", summarize)
    out = tmp_path / "out"
    args = SimpleNamespace(domain="cpp", lane="reference", platform=platform, out=out)
    if failure == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            run.run(args)
    else:
        assert run.run(args) == (0 if failure is None or (failure == "path_closure" and platform == LINUX) else 1)
    receipt = json.loads((out / "receipt.json").read_text())
    assert receipt["status"] == {None: "PLATFORM_COVERAGE_PENDING", "missing_gate": "INCOMPLETE_UNRUN",
                                  "interrupt": "INTERRUPTED", "tool_closure": "INFRASTRUCTURE_OR_RESOURCE_FAILURE",
                                  "manifest_closure": "INFRASTRUCTURE_OR_RESOURCE_FAILURE",
                                  "path_closure": "PLATFORM_COVERAGE_PENDING" if platform == LINUX else
                                  "INFRASTRUCTURE_OR_RESOURCE_FAILURE"}[failure]
    assert receipt["transfers"][0]["status"] == "TRANSFER_PENDING"
    nonlocal_name = "darwin" if platform == LINUX else "linux"
    assert all(row(nonlocal_name)["nodeid"] not in selected for selected in selections)
    assert len(checks) == 2


def test_rust_transfers_only_the_two_certified_darwin_executions():
    modules = ["tests/rust/test_rust_data_outlives_bounds.py", "tests/rust/test_rust_match_guards.py"]
    owned = "test_checked_program_runs_with_owned_compile_assemble_and_link"
    native_ids = [f"{module}::{owned}" for module in modules]
    registry = json.loads(run.REFERENCES.read_text())["functions"]
    manifest, _ = owners.read_manifest(ROOT)
    rows = []
    for module in modules:
        for func in ast.parse((ROOT / module).read_text()).body:
            if not isinstance(func, ast.FunctionDef) or not func.name.startswith("test_"):
                continue
            marks = {ast.unparse(mark.func): mark for mark in func.decorator_list if isinstance(mark, ast.Call)}
            if func.name != owned:
                assert "pytest.mark.pcc_source_group" not in marks
                if func.name != "test_program_runs_without_an_actual_rustc_check":
                    continue
            legacy = marks["pytest.mark.xdist_group"]
            assert not legacy.args and {kw.arg: ast.literal_eval(kw.value) for kw in legacy.keywords} == {
                "name": "rust-native-reference"}
            group = "rust-native-reference"
            if func.name == owned:
                override = marks["pytest.mark.pcc_source_group"]
                assert not override.args
                assert {kw.arg: ast.literal_eval(kw.value) for kw in override.keywords} == {
                    "legacy": group, "name": "rust-darwin-owned"}
                group = "rust-darwin-owned"
            assert "pytest.mark.pcc_gate" in marks
            assert classify(module, func.name, False, registry) == "ordinary"
            node = f"{module}::{func.name}"
            rows.append(dict(file=module, function=func.name, canonical_nodeid=node, nodeid=node,
                             lane="ordinary", groups=[group], gates=[{"unavailable": "Darwin" if func.name == owned else None}],
                             selected_by_repository=func.name != owned, reference_activity=registry.get(module, {}).get(func.name, "unknown")))
    original = copy.deepcopy(rows)
    assert [rule for rule in manifest["overrides"] if rule["domain"] == "rust"] == [
        dict(domain="rust", lane="ordinary", nodeid=node, platforms=[MAC], tools={MAC: ["rustc"]})
        for node in native_ids]
    assert owners.owner_modules(manifest, "rust", "ordinary", MAC) == modules
    linux = owners.partition(rows, manifest, "rust", "ordinary", LINUX)
    assert linux["local_nodeids"] == [f"{modules[1]}::test_program_runs_without_an_actual_rustc_check"]
    assert linux["transfers"] == [dict(domain="rust", lane="ordinary", nodeid=node, platform=MAC, tools=["rustc"])
                                  for node in native_ids]
    mac = owners.partition(rows, manifest, "rust", "ordinary", MAC, complete=False)
    assert mac["local_nodeids"] == native_ids
    unrun = [dict(item, status="UNRUN_PREREQUISITE") for item in rows
             if item["canonical_nodeid"] in mac["local_nodeids"] and not item["selected_by_repository"]]
    assert len(unrun) == 2
    assert run.campaign_status(dict(batches=[], unrun=unrun, lane_count=2, coverage=mac)) == "INCOMPLETE_UNRUN"
    with pytest.raises(ValueError, match="required owner tool unavailable: rustc"):
        owners.required_tool_bindings(mac["obligations"], MAC, {})
    assert rows == original
    for item in rows:
        item["groups"] = ["rust-native-reference"]
    with pytest.raises(ValueError, match="splits xdist group"):
        owners.partition(rows, manifest, "rust", "ordinary", LINUX)


def test_darwin_backend_execution_has_exact_host_ownership_not_skip():
    """Darwin executables require the actual Darwin assembler and ABI.
    Cross-target pure IR tests stay in the default Linux campaign.
    """
    actual, _ = owners.read_manifest(ROOT)
    files = ("tests/c/test_self_backend.py", "tests/c/test_self_backend_aarch64_callee_saved.py")
    nodes = []
    portable = []
    required_tools = {}
    for file in files:
        tree = ast.parse((ROOT / file).read_text())
        for function in tree.body:
            if not isinstance(function, ast.FunctionDef) or not function.name.startswith("test_"):
                continue
            calls = {node.func.id for node in ast.walk(function)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
            native = bool(calls & {"_assemble_and_run", "_compile_native_helper", "_build_and_run"})
            native |= function.name in {
                "test_self_backend_small_zero_memset_preserves_every_byte_and_guards",
                "test_self_backend_aarch64_float_cycle_after_ready_copies_keeps_direct_records",
            }
            name = file + "::" + function.name
            if not native:
                if "_link_object_and_run" in calls:
                    portable.append(name)
                continue
            gates = [mark for mark in function.decorator_list
                     if isinstance(mark, ast.Call) and ast.unparse(mark.func) == "pytest.mark.pcc_gate"]
            assert len(gates) == 1 and {kw.arg: ast.literal_eval(kw.value) for kw in gates[0].keywords} == {
                "platforms": ("darwin",), "machines": ("arm64",)}
            suffixes = [""]
            for mark in function.decorator_list:
                if isinstance(mark, ast.Call) and ast.unparse(mark.func) == "pytest.mark.parametrize":
                    suffixes = [f"[{value}]" for value in ast.literal_eval(mark.args[1])]
            nodes.extend(name + suffix for suffix in suffixes)
            required_tools.update({name + suffix: ["cc"] if calls & {"_assemble_and_run", "_compile_native_helper", "_build_and_run"} else [] for suffix in suffixes})
    assert len(nodes) == 54 and len(portable) == 3
    expected = [{"domain": "shared", "lane": "ordinary", "nodeid": node,
                 "platforms": [MAC], "tools": {MAC: required_tools[node]}} for node in sorted(nodes)]
    rules = [r for r in actual["overrides"] if r["domain"] == "shared"
             and r["nodeid"].split("::")[0] in files]
    assert rules == expected
    rows = []
    for node in sorted(nodes + portable):
        file, function = node.split("::", 1)
        rows.append({**row(function.split("[", 1)[0], selected=node in portable, lane="ordinary"),
                     "file": file, "function": function.split("[", 1)[0],
                     "nodeid": node, "canonical_nodeid": node})
    # Restrict this model to the two tested files: the full campaign checks
    # all other exact manifest nodes as well.
    scoped = {**actual, "overrides": rules}
    linux = owners.partition(rows, scoped, "shared", "ordinary", LINUX)
    assert linux["local_nodeids"] == sorted(portable)
    assert [r["nodeid"] for r in linux["transfers"]] == sorted(nodes)
    for entry in rows:
        entry["selected_by_repository"] = True
    mac = owners.partition(rows, scoped, "shared", "ordinary", MAC, complete=False)
    assert mac["local_nodeids"] == sorted(nodes)
    assert owners.additional_campaigns(["shared"], scoped) == [
        dict(domain="shared", lane="ordinary", platform=MAC, runner="macos-15")]


def test_native_host_backend_builds_share_one_worker_without_changing_darwin_owner():
    """Each compiler subprocess must not create an independent cold build tree."""
    source = (ROOT / "tests/c/test_self_backend.py").read_text()
    grouped = 0
    for function in ast.parse(source).body:
        if not isinstance(function, ast.FunctionDef) or not function.name.startswith("test_"):
            continue
        marks = {ast.unparse(mark.func): mark for mark in function.decorator_list if isinstance(mark, ast.Call)}
        calls = {ast.unparse(node.func) for node in ast.walk(function) if isinstance(node, ast.Call)}
        native = bool(calls & {"_compile_units", "_compile_multi_units", "_link_object_and_run", "CEvaluator"})
        native |= any(call.endswith(".evaluate") or call.endswith(".emit_compiled_units") for call in calls)
        if native and "pytest.mark.pcc_gate" not in marks:
            mark = marks["pytest.mark.xdist_group"]
            assert {kw.arg: ast.literal_eval(kw.value) for kw in mark.keywords} == {
                "name": "self-backend-host-native"}
            grouped += 1
        else:
            assert "pytest.mark.xdist_group" not in marks or (
                {kw.arg: ast.literal_eval(kw.value) for kw in marks["pytest.mark.xdist_group"].keywords}
                != {"name": "self-backend-host-native"})
    assert grouped == 29
