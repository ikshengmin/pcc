"""Pure command/report controls; actual native/xdist gates run in CI."""

import ast
import hashlib
import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import weakref

import pytest

from scripts import ci_worker_size_prior_gate as gate
from tests.integration.test_worker_size_prior_closed_world import require_definition

ROOT = Path(__file__).resolve().parents[1]


def reports(directory, nodes):
    rows = [{"event": "start", "collect_only": False, "override_ini": []},
            {"event": "collected", "nodeids": nodes}]
    rows.extend({"event": "report", "nodeid": node, "when": when, "outcome": "passed"}
                for node in nodes for when in ("setup", "call", "teardown"))
    rows.append({"event": "finish", "exitstatus": 0, "testsfailed": 0, "testscollected": len(nodes)})
    noun = "item" if len(nodes) == 1 else "items"
    (directory / "target.stdout").write_text(
        "created: 6/6 workers\n6 workers [" + str(len(nodes)) + " " + noun
        + "]\nscheduling tests via LoadGroupScheduling\n")
    return rows


def put(directory, rows):
    (directory / "live.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_default_command_retains_project_xdist(tmp_path):
    assert gate.HOST_FILES[10:14] == (
        "tests/python/test_host_indexed_process_split.py",
        "tests/python/test_dynamic_handoff_slots.py",
        "tests/python/test_indexed_phase_timing.py",
        "tests/python/test_x86_64_windows_packed_stackmaps.py",
    )
    assert gate.HOST_FILES[4] == "tests/python/test_worker_guard_cadence.py"
    assert gate.HOST_FILES[5] == "tests/c/test_self_backend_function_body_lines.py"
    assert gate.HOST_FILES[6] == "tests/python/test_compiled_default_pass_tier.py"
    assert gate.HOST_FILES[7] == "tests/python/test_owned_mem2reg_frontiers.py"
    assert gate.HOST_FILES[8] == "tests/c/test_self_backend_text_index.py"
    assert gate.HOST_FILES[9] == "tests/c/test_self_backend_type_parser.py"
    assert gate.HOST_COUNTS == (80, 3, 253, 0, 6, 29, 25, 7, 11, 49, 20, 21, 413, 17, 4, 5, 60, 32, 24, 11, 89, 55, 16, 136, 19, 77, 23, 55, 84, 12, 51, 71, 34, 1, 17, 1, 8, 1, 9, 1, 8, 1, 95, 43, 2)
    assert gate.HOST_FILES[14:17] == (gate.X87_PREFIX_TEST, gate.X87_ENCODING_TEST, gate.ASSEMBLY_PROFILE_TEST)
    assert gate.HOST_FILES[17] == gate.REGALLOC_FACTS_TEST
    assert gate.HOST_FILES[18:20] == (gate.RSS_READER_TEST, gate.RSS_PUBLICATION_TEST)
    assert gate.HOST_FILES[20] == gate.SCALAR_PROJECTION_TEST
    assert gate.HOST_FILES[21] == gate.RSS_PUBLISHER_TEST
    assert gate.HOST_FILES[22] == gate.ELF_RELOCATION_CONTEXT_TEST
    assert gate.HOST_FILES[23:25] == (gate.STACKMAP_BUDGET_TEST, gate.STACKMAP_HOST_BUDGET_TEST)
    assert gate.HOST_FILES[25:27] == (gate.PARSE_BUFFER_TEST, gate.PARSE_BUFFER_OBJECT_TEST)
    assert gate.HOST_FILES[27:28] == (gate.ARM_CALL_VENEERS_TEST,)
    assert gate.HOST_FILES[28:30] == (gate.TREE_RECOVERY_TEST, gate.TREE_RECOVERY_GUARD_TEST)
    assert gate.HOST_FILES[30:32] == (gate.TAR_ATTRIBUTES_TEST, gate.TAR_STRICT_ORACLE_TEST)
    assert gate.HOST_FILES[32:34] == (gate.MANAGED_SCRATCH_TEST, gate.MANAGED_MEMORY_TEST)
    assert gate.HOST_FILES[34:36] == (gate.ELF_PACKED_TEST, gate.ELF_PACKED_SOURCE_TEST)
    assert gate.HOST_FILES[36:38] == (gate.ELF_NEGATIVE_TEST, gate.ELF_NEGATIVE_SOURCE_TEST)
    assert gate.HOST_FILES[38:40] == (gate.ELF_PROGRESS_TEST, gate.ELF_PROGRESS_SOURCE_TEST)
    assert gate.HOST_FILES[40:42] == (gate.ELF_APPLICATION_TEST, gate.ELF_APPLICATION_SOURCE_TEST)
    assert gate.HOST_FILES[42:43] == (gate.MANAGED_DIRTY_ROWS_TEST,)
    assert gate.HOST_FILES[43:] == (gate.FACTS_STRUCTURE_TEST, gate.FACTS_NATIVE_BOUNDARY_TEST)
    assert len(gate.HOST_FILES) == len(gate.HOST_COUNTS) == 45
    assert sum(gate.HOST_COUNTS) == 1979 and sum(gate.HOST_COUNTS[:43]) == 1934
    assert sum(gate.HOST_COUNTS[:42]) == 1839
    assert sum(gate.HOST_COUNTS[:40]) == 1830
    assert sum(gate.HOST_COUNTS[:38]) == 1820
    assert sum(gate.HOST_COUNTS[:36]) == 1811
    assert sum(gate.HOST_COUNTS[:34]) == 1793
    assert sum(gate.HOST_COUNTS[:32]) == 1758
    assert sum(gate.HOST_COUNTS[:30]) == 1636
    assert sum(gate.HOST_COUNTS[:28]) == 1540
    assert sum(gate.HOST_COUNTS[:27]) == 1485
    assert sum(gate.HOST_COUNTS[:25]) == 1385
    assert sum(gate.HOST_COUNTS[:23]) == 1230
    assert sum(gate.HOST_COUNTS[:22]) == 1214
    assert sum(gate.HOST_COUNTS[:21]) == 1159
    assert sum(gate.HOST_COUNTS[:20]) == 1070
    assert sum(gate.HOST_COUNTS[:18]) == 1035
    assert sum(gate.HOST_COUNTS[:17]) == 1003 and sum(gate.HOST_COUNTS[:16]) == 943
    assert sum(gate.HOST_COUNTS[:14]) == 934
    for filename, expected_count in ((gate.MANAGED_SCRATCH_TEST, 34), (gate.MANAGED_MEMORY_TEST, 1)):
        count = 0
        for node in ast.parse((ROOT / filename).read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                cases = 1
                for decorator in node.decorator_list:
                    assert isinstance(decorator, ast.Call)
                    assert ast.unparse(decorator.func) == "pytest.mark.parametrize"
                    cases *= len(ast.literal_eval(decorator.args[1]))
                count += cases
        assert count == expected_count
    dirty_tree = ast.parse((ROOT / gate.MANAGED_DIRTY_ROWS_TEST).read_text())
    dirty_sequences = {node.targets[0].id: node.value for node in dirty_tree.body
                       if isinstance(node, ast.Assign) and len(node.targets) == 1
                       and isinstance(node.targets[0], ast.Name)
                       and isinstance(node.value, (ast.List, ast.Tuple))}
    dirty_count = 0
    for node in dirty_tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        cases = 1
        for mark in node.decorator_list:
            assert isinstance(mark, ast.Call) and ast.unparse(mark.func) == "pytest.mark.parametrize"
            values = mark.args[1]
            if isinstance(values, ast.Name):
                values = dirty_sequences[values.id]
            assert isinstance(values, (ast.List, ast.Tuple))
            cases *= len(values.elts)
        dirty_count += cases
    assert dirty_count == 95
    facts_tree = ast.parse((ROOT / gate.REGALLOC_FACTS_TEST).read_text())
    facts_nodes = [node for node in facts_tree.body
                   if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
    assert tuple(len(node.decorator_list[0].args[1].elts) for node in facts_nodes) == (4, 8, 3, 5, 6, 6)
    assert "tests/aarch64_regalloc_facts_reference.py" in gate.REGALLOC_INPUTS
    assert gate.REGALLOC_FACTS_TEST in gate.REGALLOC_INPUTS
    for path, count in ((gate.X87_PREFIX_TEST, 4), (gate.X87_ENCODING_TEST, 5)):
        nodes = [node for node in ast.parse((ROOT / path).read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
        assert len(nodes) == count and all(not node.decorator_list for node in nodes)
    profiles = [node for node in ast.parse((ROOT / gate.ASSEMBLY_PROFILE_TEST).read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
    assert len(profiles) == 50 and all(not node.decorator_list for node in profiles[:48])
    assert [node.name for node in profiles[48:]] == [
        "test_arm_facts_hook_is_local_restored_and_business_exception_wins",
        "test_facts_pop_edge_requires_unique_raw_parent_and_child_identity",
    ]
    assert tuple(len(node.decorator_list[0].args[1].elts) for node in profiles[48:]) == (5, 7)
    for filename, expected_count in ((gate.FACTS_STRUCTURE_TEST, 43),
                                     (gate.FACTS_NATIVE_BOUNDARY_TEST, 2)):
        count = 0
        for node in ast.parse((ROOT / filename).read_text()).body:
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            cases = 1
            for mark in node.decorator_list:
                assert isinstance(mark, ast.Call) and ast.unparse(mark.func) == "pytest.mark.parametrize"
                assert isinstance(mark.args[1], (ast.List, ast.Tuple))
                cases *= len(mark.args[1].elts)
            count += cases
        assert count == expected_count
    assert {gate.FACTS_STRUCTURE_TEST, gate.FACTS_NATIVE_BOUNDARY_TEST} <= set(gate.REGALLOC_INPUTS)
    identity_tree = ast.parse((ROOT / "scripts/ci_worker_size_prior_gate.py").read_text())
    identity = next(node for node in identity_tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "source_identity")
    assert any(isinstance(node, ast.Starred) and isinstance(node.value, ast.Name)
               and node.value.id == "X87_INPUTS" for node in ast.walk(identity))
    exception_inputs = ("tests/strict_exception_oracle.py", "tests/fixtures/strict_stackmap_catch.ll")
    for path in exception_inputs:
        assert any(isinstance(node, ast.Constant) and node.value == path
                   for node in ast.walk(identity))
    workflow = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    push_filters, pull_request_filters = workflow.split('\n  pull_request:', 1)
    for path in exception_inputs:
        assert push_filters.count('      - "' + path + '"') == 1
        assert pull_request_filters.count('      - "' + path + '"') == 1
    assert gate.STACKMAP_BUDGET_INPUTS == (
        'tests/python/test_compact_stackmap_processing_budget.py',
        'tests/python/test_stackmap_host_budget.py',
        'pcc/backend/precise_stackmap.py',
        'pcc/backend/macho_link.py',
        'pcc/backend/macho_exec.py',
        'pcc/backend/native_object.py',
        'pcc/backend/macho_obj.py',
        'pcc/frontends/python/pipeline_self_backend_link.py',
        'pcc/backend/stackmap_host_budget.py',
    )
    assert any(isinstance(node, ast.Starred) and isinstance(node.value, ast.Name)
               and node.value.id == "STACKMAP_BUDGET_INPUTS" for node in ast.walk(identity))
    for path in (gate.STACKMAP_BUDGET_TEST, gate.STACKMAP_HOST_BUDGET_TEST):
        assert workflow.count('      - "' + path + '"') == 2
    for path, expected in ((gate.STACKMAP_BUDGET_TEST, 136), (gate.STACKMAP_HOST_BUDGET_TEST, 19)):
        count = 0
        module = ast.parse((ROOT / path).read_text())
        consumer_values = [node.value for node in module.body
                           if isinstance(node, ast.Assign) and len(node.targets) == 1
                           and isinstance(node.targets[0], ast.Name)
                           and node.targets[0].id == "_CONSUMERS"]
        for node in module.body:
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            cases = 1
            for mark in node.decorator_list:
                assert isinstance(mark, ast.Call) and isinstance(mark.func, ast.Attribute)
                assert mark.func.attr == "parametrize"
                values = mark.args[1]
                if isinstance(values, ast.Name):
                    assert values.id == "_CONSUMERS" and len(consumer_values) == 1
                    values = consumer_values[0]
                assert isinstance(values, (ast.List, ast.Tuple))
                cases *= len(values.elts)
            count += cases
        assert count == expected
    assert any(isinstance(node, ast.Starred) and isinstance(node.value, ast.Name)
               and node.value.id == "ASSEMBLY_PROFILE_INPUTS" for node in ast.walk(identity))
    for path in (*gate.X87_INPUTS[:6], *gate.ASSEMBLY_PROFILE_INPUTS):
        assert workflow.count('      - "' + path + '"') == 2
    assert any(isinstance(node, ast.Starred) and isinstance(node.value, ast.Name)
               and node.value.id == "RSS_READER_INPUTS" for node in ast.walk(identity))
    assert gate.RSS_READER_INPUTS == (
        gate.RSS_READER_TEST, gate.RSS_PUBLICATION_TEST, gate.RSS_PUBLISHER_TEST,
        "pcc/frontends/python/worker_resource_plan.py",
        "pcc/frontends/python/worker_resource_host_windows.py",
        "pcc/frontends/python/pipeline_import_scan.py",
    )
    for path in (gate.RSS_READER_TEST, gate.RSS_PUBLICATION_TEST, gate.RSS_PUBLISHER_TEST):
        assert workflow.count('      - "' + path + '"') == 2
    assert gate.PARSE_BUFFER_INPUTS == (
        'tests/python/test_x86_64_parse_buffer_publication.py',
        'tests/python/test_x86_64_parse_buffer_objects.py',
        'tests/python/test_x86_64_assembler_lifetime.py',
        'tests/python/test_x86_64_numeric_data_batching.py',
        'tests/python/data/x86_64_parse_buffer_before_81776.py.txt',
        'pcc/backend/x86_64_asm_driver.py',
    )
    assert any(isinstance(node, ast.Starred) and isinstance(node.value, ast.Name)
               and node.value.id == "PARSE_BUFFER_INPUTS" for node in ast.walk(identity))
    for path in gate.PARSE_BUFFER_INPUTS[:5]:
        assert workflow.count('      - "' + path + '"') == 2
    bundle = ast.parse((ROOT / gate.PARSE_BUFFER_OBJECT_TEST).read_text())
    imports = [node for node in bundle.body if isinstance(node, ast.ImportFrom)]
    expected = {'tests.python.test_x86_64_assembler_lifetime': ('test_packed_instruction_spans_bound_storage_and_preserve_entry_order', 'test_packed_span_object_matches_unpacked_parser_on_same_input', 'test_packed_span_runs_retire_during_final_encoding', 'test_packed_span_corruption_is_rejected', 'test_packed_span_signed_width_preserves_boundary', 'test_label_dense_span_storage_never_inflates_host_representation'), 'tests.python.test_x86_64_numeric_data_batching': ('test_small_or_disabled_bounds_preserve_mixed_width_complete_object', 'test_symbol_label_alignment_zero_size_and_section_barriers', 'test_full_coff_bytes_keep_symbol_relocation_stackmap_and_unwind_sections')}
    assert {node.module: tuple(alias.name for alias in node.names) for node in imports} == expected
    assert all(alias.asname is None for node in imports for alias in node.names)
    assert not any(isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import)) for node in bundle.body)
    count = 0
    for module, names in expected.items():
        parsed = ast.parse((ROOT / (module.replace(".", "/") + ".py")).read_text())
        selected = [node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name in names]
        assert len(selected) == len(names)
        for node in selected:
            assert all(arg.arg in ("monkeypatch", "text", "run_length", "limit") for arg in node.args.args)
            cases = 1
            for mark in node.decorator_list:
                assert isinstance(mark, ast.Call) and isinstance(mark.func, ast.Attribute)
                assert mark.func.attr == "parametrize" and isinstance(mark.args[1], (ast.List, ast.Tuple))
                cases *= len(mark.args[1].elts)
            count += cases
    assert count == 23
    assert gate.TREE_RECOVERY_INPUTS == (
        'tests/python/test_worker_current_rss.py',
        'tests/python/test_worker_tree_recovery_guard.py',
        'tests/python/test_process_tree_sample_tool.py',
        'tests/python/test_worker_guard_cadence.py',
        'pcc/frontends/python/worker_resource_plan.py',
        'pcc/frontends/python/worker_process_pool.py',
        'pcc/frontends/python/worker_resource_host_recovery.py',
        'scripts/run_process_tree_sample.py',
        'scripts/run_pcc_stage1_build.py',
    )
    assert any(isinstance(node, ast.Starred) and isinstance(node.value, ast.Name)
               and node.value.id == "TREE_RECOVERY_INPUTS" for node in ast.walk(identity))
    for path in (*gate.TREE_RECOVERY_INPUTS[:4], "scripts/run_pcc_stage1_build.py"):
        assert workflow.count('      - "' + path + '"') == 2
    bundle = ast.parse((ROOT / gate.TREE_RECOVERY_GUARD_TEST).read_text())
    imports = [node for node in bundle.body if isinstance(node, ast.ImportFrom)]
    assert len(imports) == 1 and imports[0].module == "tests.python.test_process_tree_sample_tool"
    expected = ('test_process_table_keeps_full_worker_command_and_manifest', 'test_safety_table_avoids_all_process_argv_and_queries_only_largest', 'test_safety_process_table_failure_uses_only_bounded_retries', 'test_mac_ci_guard_auto_selection_reaches_receipt_environment_and_state', 'test_mac_ci_guard_rejection_persists_observation_without_launch', 'test_guard_publishes_adopted_attempt_start_and_original_recovery_deadline', 'test_stage1_clean_environment_forwards_recovery_only_with_guard_state')
    assert tuple(alias.name for alias in imports[0].names) == expected
    assert all(alias.asname is None for alias in imports[0].names)
    assert not any(isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import))
                   for node in bundle.body)
    source = ast.parse((ROOT / "tests/python/test_process_tree_sample_tool.py").read_text())
    selected = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in expected]
    assert len(selected) == 7
    count = 0
    for node in selected:
        cases = 1
        for mark in node.decorator_list:
            assert isinstance(mark, ast.Call) and isinstance(mark.func, ast.Attribute)
            assert mark.func.attr == "parametrize" and isinstance(mark.args[1], (ast.List, ast.Tuple))
            cases *= len(mark.args[1].elts)
        count += cases
    assert count == 12
    command = gate.pytest_command(tmp_path, gate.HOST_FILES)
    assert command[:4] == [sys.executable, "-m", "pytest", "-x"]
    assert command[-len(gate.HOST_FILES):] == list(gate.HOST_FILES)
    assert not any(arg in ("-o", "-n", "-n0", "--dist", "--override-ini")
                   or arg.startswith(("addopts=", "--numprocesses", "--dist=")) for arg in command)
    assert command.count("-m") == 1


def test_integration_selects_one_owner_without_changing_workers(tmp_path):
    native_tree = ast.parse((ROOT / "scripts/ci_worker_size_prior_gate.py").read_text())
    function = next(node for node in native_tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "native_gate")
    # Execute only the selector assignments with fake names. Stop before the
    # first call, filesystem read, guard, import or receipt verification.
    setup = function.body[:next(index for index, node in enumerate(function.body)
                               if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call))]
    assert all(isinstance(node, ast.Assign) for node in setup)
    for owner in ("pcc0", "pcc1"):
        scope = {"PROCESS_TEST": gate.PROCESS_TEST, "X87_NATIVE_TEST": gate.X87_NATIVE_TEST, "owner": owner}
        exec(compile(ast.Module(body=setup, type_ignores=[]), "<native-node-selection-only>", "exec"), scope)
        worker = gate.PROCESS_TEST + "::test_size_prior_native_processes_five_collectors[" + owner + "]"
        expected = ([gate.X87_NATIVE_TEST + "::test_x87_tables_native_five_gc[pcc0]", worker]
                    if owner == "pcc0" else [worker])
        assert scope["nodes"] == expected
        command = gate.pytest_command(tmp_path, expected, integration=True)
        assert command[-len(expected) - 2:] == ["-m", "integration", *expected]
        assert "-n0" not in command and "-o" not in command


@pytest.mark.parametrize(("nodes", "summary"), [
    (["a.py::one"], "6 workers [1 item]"),
    (["a.py::one", "b.py::two"], "6 workers [2 items]"),
])
def test_exact_six_worker_reports_are_required(tmp_path, nodes, summary):
    put(tmp_path, reports(tmp_path, nodes))
    # Literal xdist output keeps the fixture from repeating a parser spelling bug.
    (tmp_path / "target.stdout").write_text(
        "created: 6/6 workers\n" + summary + "\nscheduling tests via LoadGroupScheduling\n")
    assert gate.verify_pytest(tmp_path, nodes) == nodes
    assert gate.verify_pytest(tmp_path, [node.split("::")[0] for node in nodes],
                              [1] * len(nodes)) == nodes


@pytest.mark.parametrize("summary", [
    "5 workers [1 item]", "16 workers [1 item]", "6 workers [0 items]",
    "6 workers [2 items]", "6 workers [1 items]", "6 workers [1 item] extra",
    "prefix 6 workers [1 item]", "",
])
def test_worker_summary_requires_exact_worker_count_item_count_and_line(tmp_path, summary):
    nodes = ["a.py::one"]
    put(tmp_path, reports(tmp_path, nodes))
    (tmp_path / "target.stdout").write_text(
        "created: 6/6 workers\n" + summary + "\nscheduling tests via LoadGroupScheduling\n")
    with pytest.raises(AssertionError):
        gate.verify_pytest(tmp_path, nodes)


@pytest.mark.parametrize("fault", ["collect-only", "override", "missing", "duplicate", "failed",
                                   "skip", "xfail", "no-call", "no-finish", "five-workers", "wrong-count",
                                   "wrong-scheduler", "exit-status", "failure-count"])
def test_no_partial_or_serial_run_can_pass(tmp_path, fault):
    nodes = ["a.py::one"]
    rows = reports(tmp_path, nodes)
    if fault == "collect-only": rows[0]["collect_only"] = True
    elif fault == "override": rows[0]["override_ini"] = ["addopts="]
    elif fault == "missing": rows[1]["nodeids"] = []
    elif fault == "duplicate": rows[1]["nodeids"] = nodes + nodes
    elif fault == "failed": rows[3]["outcome"] = "failed"
    elif fault == "skip": rows[3]["outcome"] = "skipped"
    elif fault == "xfail": rows[3]["wasxfail"] = "not allowed"
    elif fault == "no-call": rows.pop(3)
    elif fault == "no-finish": rows.pop()
    elif fault in ("five-workers", "wrong-scheduler"):
        path = tmp_path / "target.stdout"
        before, after = (("created: 6/6 workers", "created: 5/5 workers") if fault == "five-workers"
                         else ("LoadGroupScheduling", "LoadScheduling"))
        path.write_text(path.read_text().replace(before, after))
    elif fault == "exit-status": rows[-1]["exitstatus"] = 1
    elif fault == "failure-count": rows[-1]["testsfailed"] = 1
    else: rows[-1]["testscollected"] = 2
    put(tmp_path, rows)
    with pytest.raises(AssertionError):
        gate.verify_pytest(tmp_path, nodes)


@pytest.mark.parametrize("replacement", [
    "", "declare ptr @user_test()\n", "define ptr @user_test() {\nentry:\n ret ptr null\n}",
    "define ptr @user_test() {\nstrict.nolib.stub:\n call void @x()\n ret ptr null\n}",
    "define ptr @user_test() {\nentry:\n call ptr @py_cpy_import(ptr null)\n ret ptr null\n}",
    "define ptr @user_test() {\nentry:\n call ptr @py_exc_new(i64 11, ptr null)\n ret ptr null\n}",
])
def test_strict_body_rejects_fallback_or_missing_definition(replacement):
    following = "\ndefine ptr @user_next() {\nentry:\n call void @x()\n ret ptr null\n}\n"
    with pytest.raises(AssertionError):
        require_definition(replacement + following, "user_test")


_STRICT_CATCH_BODY = '''entry:
 %class.slot = alloca ptr
 %empty.slot = alloca ptr
 store ptr null, ptr %class.slot
 store ptr null, ptr %empty.slot
 %frame.slot = bitcast ptr %class.slot to ptr
 call void (ptr, ptr) @pcc_gc_frame_enter(ptr @frame_map, ptr %frame.slot)
 %exc.cls.NotImplementedError.1 = call ptr (i64) @py_exc_builtin_class(i64 11)
 %class.store = bitcast ptr %class.slot to ptr
 call void (ptr, ptr) @pcc_gc_store_root(ptr %class.store, ptr %exc.cls.NotImplementedError.1)
 %class.load = bitcast ptr %class.slot to ptr
 %class.live = call ptr (ptr, ptr) @pcc_gc_load_ptr(ptr null, ptr %class.load)
 %matched = call i64 (ptr, ptr) @py_exc_match_handler(ptr %exception, ptr %class.live)
 %cleanup.slot = select i1 %owned, ptr %class.slot, ptr %empty.slot
 call void (ptr, ptr) @pcc_gc_store_root(ptr %cleanup.slot, ptr null)
 call void (ptr) @pcc_gc_frame_leave(ptr %frame.slot)
 ret ptr null
'''


def _strict_catch_fixture_body(payload):
    # Git text checkouts may use CRLF. Normalize only that exact byte pair for
    # the fixture oracle; source_identity continues to hash raw checkout bytes.
    canonical = payload.replace(b'\r\n', b'\n')
    assert hashlib.sha256(canonical).hexdigest() == '1cddc4156315ca7a7411bac5ec27bd8d26dc42657812cc7a8a391bc697acd3a7'
    return canonical.decode()


@pytest.mark.parametrize('shape', ('lf', 'crlf', 'changed-body', 'lone-cr'))
def test_strict_catch_fixture_allows_only_checkout_newlines(shape):
    canonical = (ROOT / 'tests/fixtures/strict_stackmap_catch.ll').read_bytes().replace(b'\r\n', b'\n')
    payload = canonical
    if shape == 'crlf':
        payload = canonical.replace(b'\n', b'\r\n')
    elif shape == 'changed-body':
        payload = canonical.replace(b'@py_exc_builtin_class(i64 11)', b'@py_exc_builtin_class(i64 10)')
        assert payload != canonical
    elif shape == 'lone-cr':
        payload = canonical.replace(b'\n', b'\r', 1)
    if shape in ('lf', 'crlf'):
        assert _strict_catch_fixture_body(payload) == canonical.decode()
    else:
        with pytest.raises(AssertionError):
            _strict_catch_fixture_body(payload)


def _strict_check_body(body, indexed, *, actual=False):
    from tests.integration import test_worker_size_prior_closed_world as closure

    symbol = 'user_pcc_backend_precise_stackmap__native_record_reads_available' if actual else 'user_test'
    header = ('define i1 @' + symbol + '()' if actual
              else 'define ptr @' + symbol + '(ptr %exception, i1 %owned)')
    text = header + ' {\n' + body + '}\n'
    if actual:
        with pytest.raises(AssertionError):
            _original_require_definition(text, symbol)
    bodies = closure._index_definition_bodies(text) if indexed else None
    closure.require_definition(text, symbol, bodies=bodies)


@pytest.mark.parametrize('indexed', (False, True))
@pytest.mark.parametrize('shape', ('direct', 'rooted', 'quoted', 'anonymous', 'restore-current', 'actual-source-ir'))
def test_strict_body_accepts_only_catch_class_references(indexed, shape):
    body = _STRICT_CATCH_BODY
    if shape == 'direct':
        body = '''entry:
 %exc.cls.NotImplementedError.1 = call ptr @py_exc_builtin_class(i64 11)
 %matched = call i64 @py_exc_match_handler(ptr %exception, ptr %exc.cls.NotImplementedError.1)
 ret ptr null
'''
    elif shape == 'quoted':
        body = re.sub(r'([@%])([\w.$]+)', r'\1"\2"', body)
    elif shape == 'anonymous':
        body = body.replace('exc.cls.NotImplementedError.1', 'anonymous')
    elif shape == 'restore-current':
        body = body.replace(' ret ptr null', ''' %current = call ptr @py_current_exception()
 call void @py_raise(ptr %current)
 ret ptr null''')
    elif shape == 'actual-source-ir':
        # Exact emitted helper from the strict x86-64 CI failure. Original
        # failure-evidence SHA256:
        # e213e654e8e5b7196821593a96534f3c438c8f50e405ba16a4370b9685729582
        # The body includes owned class roots and a separate exception restore.
        payload = (ROOT / 'tests/fixtures/strict_stackmap_catch.ll').read_bytes()
        body = _strict_catch_fixture_body(payload)
        source = ast.parse((ROOT / 'pcc/backend/precise_stackmap.py').read_bytes())
        function = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                        and node.name == '_native_record_reads_available')
        expected = ast.parse('''def _native_record_reads_available() -> bool:
    try:
        return ptr_is_null(null()) != 0
    except NotImplementedError:
        return False
''').body[0]
        assert ast.dump(function, include_attributes=False) == ast.dump(expected, include_attributes=False)
    _strict_check_body(body, indexed, actual=shape == 'actual-source-ir')


@pytest.mark.parametrize('indexed', (False, True))
@pytest.mark.parametrize('constructor', ('py_exc_new', 'py_exc_new_with_value'))
@pytest.mark.parametrize('quoted', (False, True))
@pytest.mark.parametrize('opcode', ('call', 'invoke'))
def test_strict_body_rejects_literal_exception_constructors(indexed, constructor, quoted, opcode):
    callee = '@"' + constructor + '"' if quoted else '@' + constructor
    body = 'entry:\n %anonymous = ' + opcode + ' ptr ' + callee + '(i64 11, ptr null)\n ret ptr null\n'
    with pytest.raises(AssertionError):
        _strict_check_body(body, indexed)


@pytest.mark.parametrize('indexed', (False, True))
@pytest.mark.parametrize('shape', (
    'with-class', 'object-call', 'slot-call', 'raise-class', 'raise-loaded',
    'return-class', 'root-escape', 'global-store', 'wrong-match-operand',
    'second-unused-class', 'second-class-constructor', 'no-match',
    'cache-slot', 'cache-get', 'cache-global', 'select-load',
    'unknown-transform', 'nonlocal-root', 'unproved-name',
    'catch-and-new-value', 'catch-and-stub', 'catch-and-cpy',
    'unparsed-match-operand', 'unparsed-frame-operand',
))
def test_strict_body_rejects_class_construction_or_escape(indexed, shape):
    body = _STRICT_CATCH_BODY
    additions = {
        'with-class': ' %bad = call ptr @py_exc_new_with_class(ptr %exc.cls.NotImplementedError.1, ptr null)\n',
        'object-call': ' %bad = call ptr @py_obj_call(ptr %exc.cls.NotImplementedError.1, ptr null, ptr null)\n',
        'slot-call': ' call i64 @py_obj_call_slots(ptr %class.slot, ptr null, ptr null, ptr null)\n',
        'raise-class': ' call void @py_raise(ptr %exc.cls.NotImplementedError.1)\n',
        'raise-loaded': ' call void @py_raise_owned(ptr %class.live)\n',
        'root-escape': ' call void @unowned_sink(ptr %class.load)\n',
        'global-store': ' store ptr %exc.cls.NotImplementedError.1, ptr @escaped\n',
        'second-unused-class': ' %other = call ptr @py_exc_builtin_class(i64 11)\n',
        'second-class-constructor': ' %other = call ptr @py_exc_builtin_class(i64 11)\n %bad = call ptr @py_exc_new_with_class(ptr %other, ptr null)\n',
        'cache-slot': ' %cache = call ptr @py_subs_exc_cache_slot(i64 11)\n call void @pcc_gc_root_copy_lease(ptr %class.slot, ptr %cache)\n',
        'cache-get': ' %cached = call ptr @py_subs_exc_cache_get(i64 11)\n',
        'cache-global': ' %cached = load ptr, ptr @py_exc_classes\n',
        'select-load': ' %selected = call ptr @pcc_gc_load_ptr(ptr null, ptr %cleanup.slot)\n %match2 = call i64 @py_exc_match_handler(ptr %exception, ptr %selected)\n',
        'unknown-transform': ' %bad = getelementptr i8, ptr %class.live, i64 0\n',
        'unproved-name': ' call void @NotImplementedError()\n',
        'catch-and-new-value': ' %anonymous = call ptr @py_exc_new_with_value(i64 11, ptr null)\n',
        'catch-and-stub': 'strict.nolib.stub:\n call void @real()\n',
        'catch-and-cpy': ' call void @"py_cpy_import"(ptr null)\n',
        'unparsed-match-operand': ' %match2 = call i64 @py_exc_match_handler(ptr noundef %class.live, ptr %class.live)\n',
        'unparsed-frame-operand': ' call void @pcc_gc_frame_enter(ptr noundef %class.live, ptr %class.slot)\n',
    }
    if shape == 'return-class':
        body = body.replace('ret ptr null', 'ret ptr %class.live')
    elif shape == 'wrong-match-operand':
        body = body.replace('ptr %exception, ptr %class.live', 'ptr %class.live, ptr %exception')
    elif shape == 'no-match':
        body = body.replace(' %matched = call i64 (ptr, ptr) @py_exc_match_handler(ptr %exception, ptr %class.live)\n', '')
    elif shape == 'nonlocal-root':
        body = body.replace(' %class.slot = alloca ptr\n', '')
    else:
        body = body.replace(' ret ptr null', additions[shape] + ' ret ptr null')
    with pytest.raises(AssertionError):
        _strict_check_body(body, indexed)


def load_file(name, path, monkeypatch):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("enabled", [False, True])
def test_windows_default_collection_never_imports_fcntl_and_autoclean_fails_closed(monkeypatch, enabled):
    import builtins
    original = builtins.__import__
    imports = []
    def importing(name, *args, **kwargs):
        if name == "fcntl":
            imports.append(name)
            raise AssertionError("Windows has no fcntl")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", importing)
    module = load_file("prior_root_conftest", ROOT / "conftest.py", monkeypatch)
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(module, "_auto_clean_enabled", lambda: enabled)
    config = SimpleNamespace(option=SimpleNamespace(numprocesses=0))
    if enabled:
        with pytest.raises(RuntimeError, match="POSIX shared file locks"):
            module.pytest_configure(config)
    else:
        module.pytest_configure(config)
    assert imports == []


def load_platform(monkeypatch):
    # This test verifies orchestration, not ctypes/Job Object behavior. The real
    # platform guard is executed by premerge CI; no FFI is imported in this unit.
    watchdog = ModuleType("scripts.platform_process_watchdog")
    watchdog.run = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "scripts.platform_process_watchdog", watchdog)
    return load_file("prior_platform_bootstrap", ROOT / "scripts/bootstrap_platform.py", monkeypatch)


@pytest.mark.parametrize("mismatch", ["source", "codegen", "missing"])
def test_explicit_runtime_rejection_never_builds_or_launches(tmp_path, monkeypatch, mismatch):
    module = load_platform(monkeypatch)
    from pcc.frontends.python import owned_runtime_build as owner, pipeline_targets
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "source_identity", lambda *_args: {"commit": "fixed"})
    monkeypatch.setattr(module, "build_configuration", lambda target: {"target": target})
    monkeypatch.setattr(pipeline_targets, "host_target_triple", lambda: "x86_64-unknown-linux-gnu")
    def reject(*args, **kwargs):
        assert kwargs == {"explicit_archive": str(tmp_path / "input.a")}
        raise ValueError(mismatch)
    monkeypatch.setattr(owner, "ensure_target_runtime", reject)
    calls = []
    monkeypatch.setattr(module, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match=mismatch):
        module.run_chain("0", tmp_path / "out", stage_limit=1, lock_held=True,
                         runtime_archive=tmp_path / "input.a")
    assert calls == []


def test_explicit_runtime_cli_reaches_existing_matrix_owner(tmp_path, monkeypatch):
    module = load_platform(monkeypatch)
    calls = []
    monkeypatch.setattr(module, "run_matrix", lambda *args, **kwargs: calls.append((args, kwargs)))
    module.main(["--gc", "0", "--stage", "1", "--out-dir", str(tmp_path),
                 "--runtime-archive", "source-matched.a", "--inside-matrix", "--lock-held"])
    assert calls[0][1]["runtime_archive"] == "source-matched.a"
    assert calls[0][1]["timeout"] == 2400 and calls[0][1]["rss_limit"] == 17179869184


def test_live_exclusive_has_one_existing_format_diagnostic():
    path = ROOT / "pcc/frontends/python/worker_process_pool.py"
    tree = ast.parse(path.read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "_resource_diagnostic"
             and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
             and node.args[1].value == "exclusive"]
    assert len(calls) == 1 and len(calls[0].args) == 8


def test_verified_explicit_runtime_is_reused_without_build(tmp_path, monkeypatch):
    module = load_platform(monkeypatch)
    from pcc.frontends.python import owned_runtime_build as owner, pipeline_targets
    archive = tmp_path / "libpy_runtime_pcc_py.a"
    archive.write_bytes(b"frozen-runtime")
    Path(str(archive) + ".provenance.json").write_text("{}")
    Path(str(archive) + ".capi_syms").write_text("py_test\n")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "source_identity", lambda *_args: {"commit": "fixed"})
    monkeypatch.setattr(module, "build_configuration", lambda target: {"target": target})
    monkeypatch.setattr(pipeline_targets, "host_target_triple", lambda: "x86_64-unknown-linux-gnu")
    monkeypatch.setattr(module, "dependency_receipt", lambda path: {"format": "ELF"})
    verified, launched = [], []
    def verify(runtime_root, target, **kwargs):
        verified.append((runtime_root, target, kwargs))
        return str(archive)
    monkeypatch.setattr(owner, "ensure_target_runtime", verify)
    def run(command, *, log_path, **kwargs):
        launched.append(command)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if "-o" in command:
            Path(command[command.index("-o") + 1]).write_bytes(b"native")
        else:
            log_path.write_text("0\n" + str(2 ** 80 + 42) + "\n")
        return {"seconds": 1, "peak_tree_rss": 1, "command": command, "log": str(log_path)}
    monkeypatch.setattr(module, "run", run)
    receipt = module.run_chain("0", tmp_path / "out", stage_limit=1, lock_held=True, runtime_archive=archive)
    assert verified == [(str(tmp_path / "pcc/runtime"), "x86_64-unknown-linux-gnu", {"explicit_archive": str(archive)})]
    assert not any("pcc.frontends.python.owned_runtime_build" in command for command in launched)
    assert receipt["runtime"]["path"] == str(archive) and len(launched) == 3


def event(event, phase, task, pid, stamp, indices, size=10):
    return ("pcc frontend admission event=" + event + " task=" + str(task) + " pid=" + str(pid)
            + " phase=" + repr(phase) + " indices=" + repr(indices) + " inputs=" + repr([size, size, 1, 1, 1, len(indices)])
            + " monotonic_s=" + str(stamp) + " mapping_offset=0\n")


def test_metrics_keep_wall_worker_time_retry_and_bytes_separate():
    text = (event("start", "export", 0, 1, 0, [0, 1], 30)
            + event("retire", "export", 0, 1, 1, [0, 1], 30)
            + event("start", "codegen", 0, 2, 1, [0], 10)
            + event("start", "codegen", 1, 3, 1, [1], 20)
            + event("cancel", "codegen", 1, 3, 2, [1], 20)
            + event("exclusive", "codegen", 0, 2, 2, [0], 10)
            + event("retire", "codegen", 0, 2, 4, [0], 10)
            + event("start", "codegen", 1, 4, 4, [1], 20)
            + event("retire", "codegen", 1, 4, 6, [1], 20))
    result = gate.admission_metrics(text + text, 6)
    assert result["worker_seconds"] == 7 and result["average_workers"] == 7 / 6
    assert result["exclusive_wall_s"] == 4
    assert result["source_bytes_total"] == result["source_bytes_complete"] == 30
    assert result["completed_modules"] == result["planned_modules"] == 2
    assert result["events"]["cancel"] == 1 and result["open_attempts"] == 0


def test_partial_metrics_never_count_unfinished_worker_or_source_as_complete():
    text = (event("start", "export", 0, 1, 0, [0], 10)
            + event("retire", "export", 0, 1, 1, [0], 10)
            + event("calibrate", "indexed-frontend", 0, 2, 1, [0], 10))
    result = gate.admission_metrics(text, 6)
    assert result["worker_seconds"] == 1 and result["open_attempts"] == 1
    assert result["source_bytes_complete"] == 0 and result["source_bytes_total"] == 10


def test_explicit_ambient_cap_cannot_override_automatic_budget(monkeypatch):
    monkeypatch.setenv("PCC_BOOTSTRAP_MAX_TREE_RSS_BYTES", "17179869184")
    with pytest.raises(AssertionError, match="explicit cap overrides"):
        gate.environment()


@pytest.mark.parametrize("fault", [None, "status", "returncode", "timeout_s", "cap", "peak", "cleanup"])
def test_actual_posix_guard_contract_is_required(monkeypatch, fault):
    monkeypatch.setattr(gate, "sys", SimpleNamespace(platform="darwin"))
    result = {"status": "COMPLETE", "returncode": 0, "timeout_s": 300,
              "max_tree_rss_bytes": 3 * 1024**3, "peak_tree_rss_bytes": 1024**3,
              "darwin_preflight_reserve_bytes": 536870912,
              "terminal_processes": [{"pid": 123, "scope": "last sample"}]}
    if fault == "status": result["status"] = "TIMEOUT"
    elif fault == "returncode": result["returncode"] = 1
    elif fault == "timeout_s": result["timeout_s"] = 301
    elif fault == "cap": result["max_tree_rss_bytes"] = 16 * 1024**3
    elif fault == "peak": result["peak_tree_rss_bytes"] = 4 * 1024**3
    elif fault == "cleanup": result["post_exit_cleanup_pids"] = [123]
    if fault is None:
        assert gate.verify_posix_guard(result, 300)["max_tree_rss_bytes"] == 3 * 1024**3
    else:
        with pytest.raises(AssertionError):
            gate.verify_posix_guard(result, 300)


def test_pcc1_compiler_is_rechecked_after_native_use(tmp_path, monkeypatch):
    binary = tmp_path / "pcc1"
    binary.write_bytes(b"original compiler")
    source, runtime = {"source": "fixed"}, {"runtime": "fixed"}
    stage = {"status": "PASS", "source": source, "runtime": runtime,
             "compiler": {"path": str(binary), "sha256": gate.sha(binary)}}
    gate.save(tmp_path / "stage1.json", stage)
    monkeypatch.setattr(gate, "source_identity", lambda: source)
    monkeypatch.setattr(gate, "environment", lambda: {})
    monkeypatch.setattr(gate, "runtime_identity", lambda archive: runtime)
    monkeypatch.setattr(gate, "native_gate", lambda *args: binary.write_bytes(b"replaced compiler"))
    with pytest.raises(AssertionError, match="pcc1 changed during use"):
        gate.run(tmp_path, "pcc1")
    assert json.loads((tmp_path / "pcc1.json").read_text())["status"] == "FAIL"


@pytest.mark.parametrize("kind", ["pco", "elf", "coff"])
def test_object_census_requires_code_definitions(monkeypatch, kind):
    from tests.integration.test_worker_size_prior_closed_world import require_object_symbols
    module_name, reader, target = {
        "pco": ("native_object", "decode_native_object", "arm64-apple-darwin"),
        "elf": ("elf_x86_64", "parse_relocatable", "x86_64-unknown-linux-gnu"),
        "coff": ("coff_x86_64", "parse_object", "x86_64-pc-windows-msvc"),
    }[kind]
    module = ModuleType("pcc.backend." + module_name)
    symbol = SimpleNamespace(name=("_" if kind == "pco" else "") + "user_test",
                             section=1, section_index=1, offset=0, value=0,
                             external=True, function=True, binding=1, type=2)
    section = SimpleNamespace(data=b"code", flags=0x20000004, segname="__TEXT", sectname="__text")
    obj = SimpleNamespace(machine=62, symbols=[symbol], sections=[section])
    setattr(module, reader, lambda payload: obj)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    assert require_object_symbols(b"reader-owned", target, ["user_test"])["required_defined_symbols"] == ["user_test"]
    obj.symbols = []
    with pytest.raises(AssertionError, match="missing/duplicate object definition"):
        require_object_symbols(b"reader-owned", target, ["user_test"])
    if kind == "elf":
        obj.symbols, obj.machine = [symbol], 183
        with pytest.raises(AssertionError, match="wrong ELF machine"):
            require_object_symbols(b"reader-owned", target, ["user_test"])


def test_mapping_pages_preserve_all_modules_and_count_scalars_once():
    indices = list(range(20))
    def pages(action, phase, task, pid, stamp):
        text = ""
        for offset in (0, 16):
            row = event(action, phase, task, pid, stamp, indices, 200)
            row = row.replace("indices=" + repr(indices), "indices=" + repr(indices[offset:offset + 16]))
            row = row.replace("mapping_offset=0", "mapping_offset=" + str(offset))
            text += row.rstrip() + " modules=" + repr(["m" + str(i) for i in indices[offset:offset + 16]]) + " module_count=20\n"
        return text
    text = (pages("start", "export", 0, 1, 0) + pages("retire", "export", 0, 1, 1)
            + pages("start", "codegen", 0, 2, 1) + pages("retire", "codegen", 0, 2, 3))
    result = gate.admission_metrics(text, 3)
    assert result["planned_modules"] == result["completed_modules"] == 20
    assert result["source_bytes_total"] == result["source_bytes_complete"] == 200
    assert result["events"]["start"] == result["events"]["retire"] == 2
    assert result["worker_seconds"] == 3
    with pytest.raises(AssertionError):
        gate.admission_metrics(text.replace("mapping_offset=16", "mapping_offset=17", 1), 3)



def test_preload_module_only_pages_keep_duration_without_source_double_count():
    text = ""
    for action, stamp in (("start", 0), ("retire", 1)):
        for offset in (0, 16):
            modules = ["m" + str(i) for i in range(offset, min(offset + 16, 20))]
            row = event(action, "preload-delta", 0, 1, stamp, [])
            row = row.replace("mapping_offset=0", "mapping_offset=" + str(offset))
            text += row.rstrip() + " modules=" + repr(modules) + " module_count=20\n"
    text += (event("start", "export", 0, 2, 1, [0]) + event("retire", "export", 0, 2, 2, [0])
             + event("start", "codegen", 0, 3, 2, [0]) + event("retire", "codegen", 0, 3, 4, [0]))
    result = gate.admission_metrics(text, 4)
    assert result["worker_seconds"] == 4 and result["events"]["retire"] == 3
    assert result["planned_modules"] == result["completed_modules"] == 1
    assert result["source_bytes_total"] == result["source_bytes_complete"] == 10


def workflow_job(text, name):
    match = re.search(r"(?ms)^  " + name + r":\n(.*?)(?=^  [A-Za-z0-9_-]+:|\Z)", text)
    assert match is not None
    return match.group(1)


def test_workflow_starts_stage1_without_independent_validation_dependencies():
    text = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    for path in (*gate.HOST_FILES, "tests/fixtures/native/worker_size_priors.py",
                 "scripts/ci_worker_size_prior_gate.py", "conftest.py", "uv.lock", ".python-version",
                 "tests/python/test_worker_resource_publication.py"):
        assert text.count('      - "' + path + '"') == 2, path
    other = workflow_job(text, "other-platform-native-wheel")
    mac = workflow_job(text, "pcc1-package-parity")
    preflight = workflow_job(text, "worker-prior-preflight")
    publication = workflow_job(text, "windows-rss-publication")
    assert "runs-on: windows-2022" in publication
    assert "timeout-minutes: 10" in publication and "timeout-minutes: 2" in publication
    assert 'python -m pytest --noconftest -o "addopts=" -x -vv --tb=short' in publication
    for path in (gate.RSS_PUBLICATION_TEST, gate.RSS_READER_TEST, gate.RSS_PUBLISHER_TEST):
        assert path in publication
    assert "timeout-minutes: 360" in other and "timeout-minutes: 90" in mac
    assert other.count("- { platform:") == 3
    assert "uv sync --frozen --dev --python 3.13" in other
    assert "uv sync --frozen --dev\n" in mac
    for body in (other, mac):
        assert "--phase preflight" not in body
        assert "worker-prior-preflight" not in body
        assert "Verify Windows RSS publication" not in body
        assert body.index("--phase stage1") < body.index("--phase pcc1")
        assert body.count("--phase stage1") == body.count("--phase pcc1") == 1
        assert "build/worker-prior-gate/**/*.json" in body
        assert "needs:" not in body and "actions/download-artifact" not in body
    for body in (preflight, publication):
        assert "needs:" not in body and "actions/download-artifact" not in body
        assert "--phase stage1" not in body and "--phase pcc1" not in body
    assert "fail-fast: false" in preflight
    assert "timeout-minutes: 120" in preflight
    for platform, runner in (("macos-arm64", "macos-15"), ("linux-x86_64", "ubuntu-24.04"),
                             ("linux-aarch64", "ubuntu-24.04-arm"), ("windows-x86_64", "windows-2022")):
        assert "- { platform: " + platform + ", runner: " + runner + " }" in preflight
    assert preflight.count("--phase preflight --strict-closure-timeout 1800") == 2
    assert "if: matrix.platform == 'macos-arm64'" in preflight
    assert "if: matrix.platform != 'macos-arm64'" in preflight
    assert "uv sync --frozen --dev\n" in preflight
    assert "uv sync --frozen --dev --python 3.13\n" in preflight
    assert "uv run --frozen --no-sync python scripts/ci_worker_size_prior_gate.py" in preflight
    assert "uv run --frozen --no-sync --python 3.13 python scripts/ci_worker_size_prior_gate.py" in preflight
    assert "name: worker-prior-preflight-${{ matrix.platform }}" in preflight
    for suffix in ("json", "jsonl", "log", "stdout", "stderr", "tsv", "xml"):
        assert "build/worker-prior-preflight/**/*." + suffix in preflight
    assert "if: always()" in preflight
    assert "name: bootstrap-${{ matrix.platform }}" in other
    assert "name: bootstrap-macos-arm64" in mac
    assert "continue-on-error:" not in text and "concurrency:" not in text and "max-parallel:" not in text
    for name in ("macos-pcc0-regressions", "macos-pcc0-float-doc-regressions", "macos-pcc0-indexed-split"):
        body = workflow_job(text, name)
        assert "timeout-minutes: 25" in body and "scripts/ci_macos_regression_gate.py" in body
        assert "ci_worker_size_prior_gate.py" not in body and "needs:" not in body
    assert 'PCC_BOOTSTRAP_STAGE_TIMEOUT: "2400"' in mac
    assert 'PCC_BOOTSTRAP_AUTO_TREE_RSS_CEILING_BYTES: "4294967296"' in mac


def test_other_platform_entry_keeps_the_original_full_gc_matrix_once():
    tree = ast.parse((ROOT / "scripts/ci_worker_size_prior_gate.py").read_text())
    commands = [node for node in ast.walk(tree) if isinstance(node, ast.List)
                and any(isinstance(item, ast.Constant) and item.value == "scripts/bootstrap_platform.py" for item in node.elts)]
    assert len(commands) == 1
    constants = [item.value for item in commands[0].elts if isinstance(item, ast.Constant)]
    assert constants[:5] == ["scripts/bootstrap_platform.py", "--gc", "all", "--stage", "3"]
    assert "--runtime-archive" in constants and "2400" in constants and "17179869184" in constants


@pytest.mark.parametrize("target,machine,failing", [
    ("win32", "x86_64", None), ("linux", "x86_64", None), ("darwin", "arm64", None),
    ("win32", "x86_64", "windows-exit-host"), ("win32", "x86_64", "windows-exit-native"),
    ("linux", "aarch64", None), ("linux", "arm64", None),
    ("linux", "aarch64", "linux-arm-transport-host"),
    ("linux", "aarch64", "linux-arm-transport-routes"),
    ("linux", "aarch64", "linux-arm-transport-native"),
    ("linux", "aarch64", "runtime-identity"),
    ("darwin", "arm64", "aarch64-regalloc-host"),
    ("darwin", "arm64", "aarch64-regalloc-native"),
    ("linux", "x86_64", "linux-elf-format"),
    ("linux", "x86_64", "linux-elf-staging"),
    ("linux", "x86_64", "linux-elf-owner"),
    ("linux", "aarch64", "linux-elf-format"),
    ("linux", "aarch64", "linux-elf-staging"),
    ("linux", "aarch64", "linux-elf-owner"),
])
def test_platform_gates_follow_verified_runtime_and_stop_on_failure(tmp_path, monkeypatch, target, machine, failing):
    out = tmp_path / "preflight"
    archive = out / "runtime/libpy_runtime_pcc_py.a"
    source, runtime = {"source": "fixed"}, {"runtime": "matched"}
    events = []
    monkeypatch.setattr(gate, "sys", SimpleNamespace(platform=target, executable="chosen-python"))
    monkeypatch.setattr(gate, "platform", SimpleNamespace(machine=lambda: machine))
    monkeypatch.setattr(gate, "source_identity", lambda: source)
    monkeypatch.setattr(gate, "environment", lambda: {"PCC_RUNTIME_ARCHIVE": "stale", "PCC_TEST_NO_NATIVE_PROVISIONING": "1"})

    def guarded(command, directory, timeout, env):
        assert command == ["chosen-python", "-m", "pcc.frontends.python.owned_runtime_build", "--output", str(archive)]
        assert directory == out / "runtime-build" and timeout == 1200
        assert "PCC_RUNTIME_ARCHIVE" not in env and "PCC_TEST_NO_NATIVE_PROVISIONING" not in env
        events.append("runtime-build")

    def identity(path):
        assert path == archive
        events.append("runtime-identity")
        if failing == "runtime-identity":
            raise RuntimeError("original platform gate failure")
        return runtime

    def pytest_gate(directory, name, nodes, env, *, integration=False, counts=None, timeout=300):
        assert directory == out and timeout == (1800 if name == "strict-closure" else 300)
        events.append(name)
        if name.startswith("windows-exit-"):
            assert target == "win32"
            assert nodes == ([gate.EXIT_TEST, gate.TAR_ATTRIBUTES_TEST] if integration else [gate.EXIT_TEST])
            assert env["PCC_RUNTIME_ARCHIVE"] == str(archive)
            assert env["PCC_TEST_NO_NATIVE_PROVISIONING"] == "1"
            assert integration == (name == "windows-exit-native")
            assert counts == ((6, 2) if integration else (8,))
            assert "runtime-identity" in events
        elif name.startswith("linux-arm-transport-"):
            assert target == "linux" and machine in ("aarch64", "arm64")
            assert env["PCC_RUNTIME_ARCHIVE"] == str(archive)
            assert env["PCC_TEST_NO_NATIVE_PROVISIONING"] == "1"
            assert "runtime-identity" in events
            assert integration == (name != "linux-arm-transport-host")
            if name == "linux-arm-transport-native":
                assert nodes == [gate.ARM_TRANSPORT_NATIVE_TEST
                                 + "::test_linux_aarch64_transport_executes_tls_varargs_and_managed_reload"]
                assert counts is None
            else:
                assert nodes == [gate.ARM_TRANSPORT_TEST]
                assert counts == ((8,) if integration else (7,))
        elif name.startswith("aarch64-regalloc-"):
            assert target == "darwin" and machine == "arm64"
            assert env["PCC_RUNTIME_ARCHIVE"] == str(archive)
            assert env["PCC_TEST_NO_NATIVE_PROVISIONING"] == "1"
            assert "runtime-identity" in events
            assert not integration and counts is None
            assert nodes == (gate.REGALLOC_HOST_NODES if name.endswith("-host")
                             else gate.REGALLOC_NATIVE_NODES)
        elif name.startswith("linux-elf-"):
            assert target == "linux" and not integration
            assert env["PCC_RUNTIME_ARCHIVE"] == str(archive)
            assert env["PCC_TEST_NO_NATIVE_PROVISIONING"] == "1"
            assert "runtime-identity" in events
            if name == "linux-elf-format":
                assert nodes == [gate.ELF_FORMAT_TEST] and counts == (67,)
            elif name == "linux-elf-staging":
                assert nodes == [gate.ELF_STAGING_TEST]
                assert counts == ((69,) if machine in ("x86_64", "amd64") else (67,))
            else:
                assert name == "linux-elf-owner"
                assert nodes == [gate.ELF_OWNER_NODE] and counts is None
        elif name == "default-xdist":
            assert nodes == gate.HOST_FILES and counts == (80, 3, 253, 0, 6, 29, 25, 7, 11, 49, 20, 21, 413, 17, 4, 5, 60, 32, 24, 11, 89, 55, 16, 136, 19, 77, 23, 55, 84, 12, 51, 71, 34, 1, 17, 1, 8, 1, 9, 1, 8, 1, 95, 43, 2) and not integration
        else:
            assert name == "strict-closure" and integration
        if name == failing:
            raise RuntimeError("original platform gate failure")

    def native(directory, owner, env):
        assert directory == out and owner == "pcc0"
        assert env["PCC_RUNTIME_ARCHIVE"] == str(archive)
        events.append("native-pcc0")

    monkeypatch.setattr(gate, "guarded", guarded)
    monkeypatch.setattr(gate, "runtime_identity", identity)
    monkeypatch.setattr(gate, "run_pytest", pytest_gate)
    monkeypatch.setattr(gate, "native_gate", native)
    expected = ["default-xdist", "runtime-build", "runtime-identity"]
    if target == "darwin":
        expected += ["aarch64-regalloc-host", "aarch64-regalloc-native"]
    if target == "win32":
        expected += ["windows-exit-host", "windows-exit-native"]
    elif target == "linux" and machine in ("aarch64", "arm64"):
        expected += ["linux-arm-transport-host", "linux-arm-transport-routes", "linux-arm-transport-native"]
    if target == "linux":
        expected += ["linux-elf-format", "linux-elf-staging", "linux-elf-owner"]
    expected += ["native-pcc0", "strict-closure", "runtime-identity"]
    if failing:
        with pytest.raises(RuntimeError, match="original platform gate failure"):
            gate.run(out, "preflight", strict_closure_timeout=1800)
        expected = expected[:expected.index(failing) + 1]
    else:
        gate.run(out, "preflight", strict_closure_timeout=1800)
    assert events == expected
    receipt = json.loads((out / "preflight.json").read_text())
    assert receipt["status"] == ("FAIL" if failing else "PASS")
    assert receipt["source"] == source
    if failing == "runtime-identity":
        assert "runtime" not in receipt
    else:
        assert receipt["runtime"] == runtime


def test_windows_exit_changed_shape_inputs_are_bound_and_trigger_ci():
    assert gate.EXIT_INPUTS == (
        "tests/python/test_owned_windows_exit.py",
        "tests/c/test_owned_linux_c_exports.py",
        "tests/c/fixtures/owned_linux_c_exports/exit_lifecycle.c",
        "tests/c/fixtures/owned_linux_c_exports/immediate_bypass.c",
    )
    tree = ast.parse((ROOT / "scripts/ci_worker_size_prior_gate.py").read_text())
    identity = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "source_identity")
    hashes = next(node.value for node in identity.body if isinstance(node, ast.Assign)
                  and isinstance(node.targets[0], ast.Subscript)
                  and isinstance(node.targets[0].slice, ast.Constant)
                  and node.targets[0].slice.value == "test_inputs")
    assert isinstance(hashes, ast.DictComp)
    assert any(isinstance(node, ast.Starred) and isinstance(node.value, ast.Name)
               and node.value.id == "EXIT_INPUTS" for node in hashes.generators[0].iter.elts)
    workflow = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    for name in gate.EXIT_INPUTS:
        assert workflow.count('      - "' + name + '"') == 2
    assert gate.TAR_ATTRIBUTES_INPUTS == (
        gate.TAR_ATTRIBUTES_TEST, gate.TAR_STRICT_ORACLE_TEST, "tests/tar_attribute_strict_oracle.py",
        "pcc/backend/pe_x86_64.py", "pcc/frontends/python/codegen/platform_machine_abis.py",
        "pcc/frontends/python/codegen/unsafe_lowering.py", "pcc/frontends/python/codegen/windows_platform.py",
        "pcc/frontends/python/pipeline_freestanding.py", "pcc/frontends/python/type_infer.py",
        "pcc/runtime/py/freestanding_windows.py", "pcc/stdlib/tarfile.py", "pcc/unsafe/__init__.py",
        "tests/python/owned_regression_support.py", "tests/python/process_timeout.py",
    )
    assert any(isinstance(node, ast.Starred) and isinstance(node.value, ast.Name)
               and node.value.id == "TAR_ATTRIBUTES_INPUTS" for node in hashes.generators[0].iter.elts)
    for name in gate.TAR_ATTRIBUTES_INPUTS:
        assert workflow.count('      - "' + name + '"') == 2
    # Appended coverage uses the existing native call and unchanged CI budget.
    assert workflow.count("--strict-closure-timeout 1800") == 2


def test_linux_arm_transport_inputs_markers_and_exact_inventory_are_bound():
    assert gate.ARM_TRANSPORT_INPUTS == (
        "tests/python/test_linux_aarch64_worker_transport.py",
        "tests/python/test_linux_aarch64_transport_native.py",
    )
    tree = ast.parse((ROOT / "scripts/ci_worker_size_prior_gate.py").read_text())
    identity = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "source_identity")
    hashes = next(node.value for node in identity.body if isinstance(node, ast.Assign)
                  and isinstance(node.targets[0], ast.Subscript)
                  and isinstance(node.targets[0].slice, ast.Constant)
                  and node.targets[0].slice.value == "test_inputs")
    assert isinstance(hashes, ast.DictComp)
    assert any(isinstance(node, ast.Starred) and isinstance(node.value, ast.Name)
               and node.value.id == "ARM_TRANSPORT_INPUTS" for node in hashes.generators[0].iter.elts)
    workflow = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    for name in gate.ARM_TRANSPORT_INPUTS:
        assert workflow.count('      - "' + name + '"') == 2

    # Read source only: this control must never import or emit the target IR.
    worker = ast.parse((ROOT / gate.ARM_TRANSPORT_TEST).read_text())
    counts = {False: 0, True: 0}
    for node in worker.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        integration = any(isinstance(mark, ast.Attribute) and mark.attr == "integration"
                          for mark in node.decorator_list)
        count = 1
        for mark in node.decorator_list:
            if isinstance(mark, ast.Call) and isinstance(mark.func, ast.Attribute) and mark.func.attr == "parametrize":
                assert isinstance(mark.args[1], (ast.List, ast.Tuple))
                count *= len(mark.args[1].elts)
        counts[integration] += count
    assert counts == {False: 7, True: 8}
    native = ast.parse((ROOT / gate.ARM_TRANSPORT_NATIVE_TEST).read_text())
    tests = [node for node in native.body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
    assert [node.name for node in tests] == ["test_linux_aarch64_transport_executes_tls_varargs_and_managed_reload"]
    assert any(isinstance(mark, ast.Attribute) and mark.attr == "integration"
               for mark in tests[0].decorator_list)
    assert any(isinstance(mark, ast.Call) and isinstance(mark.func, ast.Attribute) and mark.func.attr == "pcc_gate"
               for mark in tests[0].decorator_list)


def test_aarch64_regalloc_inputs_and_exact_nodes_are_bound(tmp_path):
    assert gate.REGALLOC_HOST_NODES[80:] == tuple(
        gate.MANAGED_PHI_TEST + "::test_phi_liveness_matches_set_oracle_without_repeated_fixpoint_scans["
        + selection + "-" + loop + "-" + fanin + "]"
        for selection in ("all", "alternating", "none")
        for loop in ("False", "True") for fanin in ("2", "9", "65")
    )
    assert {gate.FACTS_STRUCTURE_TEST, gate.FACTS_NATIVE_BOUNDARY_TEST,
            gate.MANAGED_SCRATCH_TEST, gate.MANAGED_MEMORY_TEST, gate.MANAGED_PHI_TEST,
            "tests/managed_scratch_retirement_reference.json"} <= set(gate.REGALLOC_INPUTS)
    assert gate.REGALLOC_INPUTS[-4:] == (
        gate.MANAGED_DIRTY_ROWS_TEST,
        "tests/managed_liveness_dirty_rows_reference.json",
        "tests/managed_liveness_dirty_rows_reference.py",
        "tests/managed_liveness_dirty_rows_ci_reference.py",
    )
    phi_tree = ast.parse((ROOT / gate.MANAGED_PHI_TEST).read_text())
    observation = [node for node in ast.walk(phi_tree) if isinstance(node, ast.ImportFrom)
                   and node.module == "tests.managed_liveness_dirty_rows_ci_reference"]
    assert len(observation) == 1
    assert [(name.name, name.asname) for name in observation[0].names] == [("record_real_kernel_overhead", None)]
    assert sum(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == "record_real_kernel_overhead" for node in ast.walk(phi_tree)) == 1
    assert len(gate.REGALLOC_HOST_NODES) == 98
    assert len(set(gate.REGALLOC_HOST_NODES)) == 98
    assert len(gate.REGALLOC_NATIVE_NODES) == 3
    assert gate.REGALLOC_NATIVE_NODES == tuple(
        "tests/c/test_self_backend_aarch64_regalloc.py::"
        "test_aarch64_call_result_executes_through_indexed_emission[" + mode + "]"
        for mode in ("default", "function", "local")
    )
    tree = ast.parse((ROOT / "scripts/ci_worker_size_prior_gate.py").read_text())
    identity = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "source_identity")
    hashes = next(node.value for node in identity.body if isinstance(node, ast.Assign)
                  and isinstance(node.targets[0], ast.Subscript)
                  and isinstance(node.targets[0].slice, ast.Constant)
                  and node.targets[0].slice.value == "test_inputs")
    assert any(isinstance(node, ast.Starred) and isinstance(node.value, ast.Name)
               and node.value.id == "REGALLOC_INPUTS" for node in hashes.generators[0].iter.elts)
    workflow = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    for path in gate.REGALLOC_INPUTS:
        assert workflow.count('      - "' + path + '"') == 2
    for nodes in (gate.REGALLOC_HOST_NODES, gate.REGALLOC_NATIVE_NODES):
        command = gate.pytest_command(tmp_path, nodes)
        assert command[-len(nodes):] == list(nodes)
        assert command.count("-m") == 1
        assert not any(arg in ("-o", "-n", "-n0", "--dist", "--override-ini")
                       or arg.startswith(("addopts=", "--numprocesses", "--dist=")) for arg in command)


@pytest.mark.parametrize("nodes", (gate.REGALLOC_HOST_NODES, gate.REGALLOC_NATIVE_NODES))
def test_aarch64_regalloc_gates_keep_tree_and_execution_bounds(tmp_path, monkeypatch, nodes):
    calls = []
    monkeypatch.setattr(gate, "guarded", lambda command, directory, timeout, env:
                        calls.append((command, directory, timeout, env)))
    monkeypatch.setattr(gate, "verify_pytest", lambda directory, expected, counts:
                        (directory, expected, counts))
    environment = {"unchanged": "yes"}
    actual = gate.run_pytest(tmp_path, "regalloc", nodes, environment)
    assert actual == (tmp_path / "regalloc", nodes, None)
    assert calls == [(gate.pytest_command(tmp_path / "regalloc", nodes),
                      tmp_path / "regalloc", 300, environment)]
    source = ast.parse((ROOT / gate.REGALLOC_TEST).read_text())
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                    and node.name == "test_aarch64_call_result_executes_through_indexed_emission")
    runs = [node for node in ast.walk(function) if isinstance(node, ast.Call)
            and ast.unparse(node.func) == "subprocess.run"]
    assert len(runs) == 1
    assert any(keyword.arg == "timeout" and isinstance(keyword.value, ast.Constant)
               and keyword.value.value == 10 for keyword in runs[0].keywords)



def test_strict_closure_explicitly_compiles_the_changed_regalloc_module():
    from tests.integration.test_worker_size_prior_closed_world import MODULES
    from tests.tar_attribute_strict_oracle import TAR_COMPILER_MODULES

    assert MODULES == tuple("pcc.frontends.python." + name for name in (
        "worker_resource_plan", "pipeline_frontend_workers",
        "pipeline_frontend_indexed_stage", "pipeline_indexed_handoff", "worker_process_pool",
    )) + ("pcc.backend.self_backend_aarch64_darwin_regalloc",
          "pcc.backend.self_backend_kernel",
          "pcc.backend.self_backend_precise_stackmaps",
          "pcc.backend.self_backend_verify",
          "pcc.backend.self_backend_aarch64_darwin_flow",
          "pcc.backend.self_backend_aarch64_darwin_memory",
          "pcc.backend.self_backend_aarch64_darwin_calls",
          "pcc.backend.self_backend_aarch64_darwin_ops",
          "pcc.backend.self_backend_aarch64_darwin_prologue",
          "pcc.backend.self_backend_aarch64_darwin_returns",
          'pcc.backend.precise_stackmap',
          'pcc.backend.macho_link',
          'pcc.backend.macho_exec',
          'pcc.backend.native_object',
          'pcc.backend.macho_obj',
          'pcc.frontends.python.pipeline_self_backend_link',
          "pcc.backend.owned_elf_inputs", "pcc.backend.elf_x86_64",
          "pcc.backend.elf_aarch64_relocations",
          "pcc.backend.x86_64_asm_driver",
          "pcc.backend.x86_64_encode") + TAR_COMPILER_MODULES + (
        "pcc.backend.owned_elf_link", "pcc.backend.indexed_phase_timing",
    )
    assert TAR_COMPILER_MODULES == (
        "pcc.backend.pe_x86_64", "pcc.frontends.python.codegen.platform_machine_abis",
        "pcc.frontends.python.codegen.unsafe_lowering", "pcc.frontends.python.codegen.windows_platform",
        "pcc.frontends.python.pipeline_freestanding", "pcc.frontends.python.type_infer",
    )
    source = ROOT / ("pcc.backend.self_backend_aarch64_darwin_regalloc".replace(".", "/") + ".py")
    definitions = [node.name for node in ast.parse(source.read_text()).body
                   if isinstance(node, ast.FunctionDef)]
    assert "allocate_aarch64_block_registers" in definitions
    reference = json.loads((ROOT / "tests/scalar_arena_projection_reference.json").read_text())
    changed_modules = {site["path"][:-3].replace("/", ".") for site in reference["sites"]}
    assert len(changed_modules) == 10 and changed_modules <= set(MODULES)
    assert len(MODULES) == 34
    assert "pcc.backend.stackmap_host_budget" not in MODULES
    assert "pcc.frontends.python.worker_resource_host_recovery" not in MODULES
    gate_tree = ast.parse((ROOT / gate.CLOSURE_TEST).read_text())
    closure = next(node for node in gate_tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == "test_worker_size_prior_modules_strict_target_emission")
    module_loop = next(node for node in ast.walk(closure) if isinstance(node, ast.For)
                       and ast.unparse(node.iter) == "zip(MODULES, sources)")
    calls = {ast.unparse(node) for node in ast.walk(module_loop) if isinstance(node, ast.Call)}
    assert "_index_definition_bodies(section)" in calls
    assert "require_definition(section, symbol, bodies=definition_bodies)" in calls
    assert "require_tar_attribute_module(name, source.read_text(), section, sections, require_definition, bodies=definition_bodies)" in calls
    assert "verify_ir_text(section)" in calls
    assert "require_object_symbols(obj.read_bytes(), target, required)" in calls
    assert "emit_indexed_module_file(str(sidecar), str(obj), 'PCO', optimize=False)" in calls
    assert MODULES[25] == "pcc.backend.x86_64_encode"
    assert MODULES[24] == "pcc.backend.x86_64_asm_driver"
    rss = next(node for node in module_loop.body if isinstance(node, ast.If)
               and ast.unparse(node.test) == "name == 'pcc.frontends.python.worker_resource_plan'")
    rss_strings = {node.value for node in ast.walk(rss) if isinstance(node, ast.Constant)
                   and isinstance(node.value, str)}
    assert {"_open_worker_resource", "read_worker_resource", "py_file_open_options",
            "_replace_worker_resource", "publish_worker_resource", "py_os_replace"} <= rss_strings
    assert "worker_resource_host_windows" in rss_strings
    assert "pcc.frontends.python.worker_resource_host_windows" in {
        node.value for node in ast.walk(closure) if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
    }
    assert "pcc.backend.stackmap_host_budget" in {
        node.value for node in ast.walk(closure) if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
    }
    assert "pcc.frontends.python.worker_resource_host_recovery" in {
        node.value for node in ast.walk(closure) if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
    }
    pool = next(node for node in module_loop.body if isinstance(node, ast.If)
                and ast.unparse(node.test) == "name == 'pcc.frontends.python.worker_process_pool'")
    assert "worker_resource_host_recovery" in {
        node.value for node in ast.walk(pool) if isinstance(node, ast.Constant)
    }
    changed = next(node for node in module_loop.body if isinstance(node, ast.If)
                   and ast.unparse(node.test) == "name == 'pcc.backend.x86_64_encode'")
    strings = [node.value for node in ast.walk(changed) if isinstance(node, ast.Constant)
               and isinstance(node.value, str)]
    assert "_pcc_py_module_top_pcc_backend_x86_64_encode" in strings
    assert "_X87_FIXED" in strings and "_X87_STACK" in strings
    assert any("py_dict_new" in value for value in strings)
    assert any("py_bytes_new" in value for value in strings)
    assert any("py_module_attr_set" in value for value in strings)
    assert "required.append(initializer)" in calls
    from tests.integration import test_worker_size_prior_closed_world as control
    initializer = "_pcc_py_module_top_pcc_backend_x86_64_encode"
    globals_text = "".join('@".modvar.pcc_backend_x86_64_encode.' + table + '" = global ptr null\n'
                           for table in ("_X87_FIXED", "_X87_STACK"))
    body = ("  call ptr @py_dict_new_presized(i64 10)\n" * 2
            + "  call ptr @py_bytes_new(ptr null, i64 2)\n" * 16
            + "  call void @py_module_attr_set(ptr null)\n"
            + '  store ptr null, ptr @".modvar.pcc_backend_x86_64_encode._X87_FIXED"\n'
            + '  store ptr null, ptr @".modvar.pcc_backend_x86_64_encode._X87_STACK"\n')
    valid = globals_text + "define void @" + initializer + "() {\n" + body + "  ret void\n}\n"
    # Dependency-replaced IR-text control only: no parser, compiler or emitter.
    variants = (valid,
                valid.replace("py_dict_new_presized", "other", 1),
                valid.replace("py_bytes_new", "other", 1),
                valid.replace("_X87_STACK", "wrong_table"),
                valid.replace("py_module_attr_set", "other"),
                valid.replace("py_module_attr_set", "py_cpy_unowned"),
                globals_text)
    for index, section in enumerate(variants):
        scope = {"name": "pcc.backend.x86_64_encode", "section": section,
                 "required": [], "definition_bodies": control._index_definition_bodies(section),
                 "require_definition": control.require_definition,
                 "_DEFINITION_BODY": control._DEFINITION_BODY, "re": re}
        code = compile(ast.Module(body=[changed], type_ignores=[]), "<fake-x87-module-init-text>", "exec")
        if index == 0:
            exec(code, scope)
            assert scope["required"] == [initializer]
        else:
            with pytest.raises(AssertionError):
                exec(code, scope)


def test_elf_input_sources_strict_modules_and_workflow_are_bound():
    from tests.integration.test_worker_size_prior_closed_world import MODULES

    assert gate.ELF_INPUTS == (
        "tests/python/test_elf_x86_64.py",
        "tests/python/test_owned_elf_staging.py",
        "tests/python/test_pipeline_self_backend_link_owner.py",
        "pcc/backend/owned_elf_inputs.py", "pcc/backend/elf_x86_64.py",
        "pcc/backend/elf_aarch64_relocations.py", "tests/elf_validate_object_preimage.py",
        "tests/python/test_aarch64_relocation_error_context.py",
        "tests/python/test_aarch64_elf_call_veneers.py",
        "tests/python/test_elf_packed_link_classification.py",
        "tests/test_elf_packed_classification_source_controls.py",
        "tests/elf_link_image_preimage.py",
        "pcc/backend/owned_elf_link.py", "pcc/backend/indexed_phase_timing.py",
        "tests/python/test_elf_negative_classification_summary.py",
        "tests/test_elf_negative_classification_source_controls.py",
        "tests/elf_negative_classification_preimage.py",
        "scripts/pcc_record_inventory.py",
        "tests/python/test_elf_relocation_progress.py",
        "tests/test_elf_relocation_progress_source_controls.py",
        "tests/elf_relocation_progress_preimage.py",
        "tests/python/test_elf_packed_application.py",
        "tests/test_elf_packed_application_source_controls.py",
        "tests/elf_packed_application_preimage.py",
    )
    assert gate.ELF_OWNER_NODE == (
        "tests/python/test_pipeline_self_backend_link_owner.py::"
        "test_linux_pcc_link_route_uses_owned_elf_driver_and_internal_assembly"
    )
    elf_modules = (
        "pcc.backend.owned_elf_inputs",
        "pcc.backend.elf_x86_64",
        "pcc.backend.elf_aarch64_relocations",
    )
    elf_start = MODULES.index(elf_modules[0])
    assert MODULES[elf_start:elf_start + len(elf_modules)] == elf_modules
    timing_modules = ("pcc.backend.owned_elf_link", "pcc.backend.indexed_phase_timing")
    assert tuple(name for name in MODULES if name in timing_modules) == timing_modules
    assert gate.ELF_RELOCATION_CONTEXT_TEST in gate.HOST_FILES
    assert gate.ARM_CALL_VENEERS_TEST in gate.HOST_FILES
    assert gate.ELF_PACKED_TEST in gate.HOST_FILES
    assert gate.ELF_PACKED_SOURCE_TEST in gate.HOST_FILES
    assert gate.ELF_NEGATIVE_TEST in gate.HOST_FILES
    assert gate.ELF_NEGATIVE_SOURCE_TEST in gate.HOST_FILES
    assert gate.ELF_PROGRESS_TEST in gate.HOST_FILES
    assert gate.ELF_PROGRESS_SOURCE_TEST in gate.HOST_FILES
    assert gate.ELF_APPLICATION_TEST in gate.HOST_FILES
    assert gate.ELF_APPLICATION_SOURCE_TEST in gate.HOST_FILES
    tree = ast.parse((ROOT / "scripts/ci_worker_size_prior_gate.py").read_text())
    identity = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "source_identity")
    hashes = next(node.value for node in identity.body if isinstance(node, ast.Assign)
                  and isinstance(node.targets[0], ast.Subscript)
                  and isinstance(node.targets[0].slice, ast.Constant)
                  and node.targets[0].slice.value == "test_inputs")
    assert any(isinstance(node, ast.Starred) and isinstance(node.value, ast.Name)
               and node.value.id == "ELF_INPUTS" for node in hashes.generators[0].iter.elts)
    workflow = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    for path in gate.ELF_INPUTS:
        assert workflow.count('      - "' + path + '"') == 2, path
    # This control reads the final test source; it neither imports the ELF
    # test modules nor calls their object emitters or native probes.
    for path, expected, native_count in ((gate.ELF_FORMAT_TEST, 67, 0),
                                          (gate.ELF_STAGING_TEST, 69, 2),
                                          (gate.ELF_RELOCATION_CONTEXT_TEST, 16, 0),
                                          (gate.ARM_CALL_VENEERS_TEST, 55, 0),
                                          (gate.ELF_PACKED_TEST, 17, 0),
                                          (gate.ELF_PACKED_SOURCE_TEST, 1, 0),
                                          (gate.ELF_NEGATIVE_TEST, 8, 0),
                                          (gate.ELF_NEGATIVE_SOURCE_TEST, 1, 0),
                                          (gate.ELF_PROGRESS_TEST, 9, 0),
                                          (gate.ELF_PROGRESS_SOURCE_TEST, 1, 0),
                                          (gate.ELF_APPLICATION_TEST, 8, 0),
                                          (gate.ELF_APPLICATION_SOURCE_TEST, 1, 0)):
        total = gated = 0
        for node in ast.parse((ROOT / path).read_text()).body:
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            count = 1
            is_gated = False
            for mark in node.decorator_list:
                assert not (isinstance(mark, ast.Attribute) and mark.attr == "integration")
                if isinstance(mark, ast.Call) and isinstance(mark.func, ast.Attribute):
                    if mark.func.attr == "parametrize":
                        assert isinstance(mark.args[1], (ast.List, ast.Tuple))
                        count *= len(mark.args[1].elts)
                    elif mark.func.attr == "pcc_gate":
                        is_gated = True
                        assert node.name == "test_staged_metadata_image_executes_data_and_bss_relocations"
            total += count
            if is_gated:
                gated += count
        assert (total, gated) == (expected, native_count)


@pytest.mark.parametrize("name,nodes,counts", [
    ("linux-elf-format", [gate.ELF_FORMAT_TEST], (67,)),
    ("linux-elf-staging", [gate.ELF_STAGING_TEST], (69,)),
    ("linux-elf-staging", [gate.ELF_STAGING_TEST], (67,)),
    ("linux-elf-owner", [gate.ELF_OWNER_NODE], None),
])
def test_elf_runtime_slices_keep_original_workers_and_bounds(tmp_path, monkeypatch, name, nodes, counts):
    calls = []
    monkeypatch.setattr(gate, "guarded", lambda command, directory, timeout, env:
                        calls.append((command, directory, timeout, env)))
    monkeypatch.setattr(gate, "verify_pytest", lambda directory, expected, file_counts:
                        (directory, expected, file_counts))
    environment = {"PCC_RUNTIME_ARCHIVE": "verified.a", "PCC_TEST_NO_NATIVE_PROVISIONING": "1"}
    actual = gate.run_pytest(tmp_path, name, nodes, environment, counts=counts)
    assert actual == (tmp_path / name, nodes, counts)
    command = gate.pytest_command(tmp_path / name, nodes)
    assert command[-len(nodes):] == nodes and command.count("-m") == 1
    assert not any(arg in ("-o", "-n", "-n0", "--dist", "--override-ini")
                   or arg.startswith(("addopts=", "--numprocesses", "--dist=")) for arg in command)
    assert calls == [(command, tmp_path / name, 300, environment)]


@pytest.mark.parametrize("machine,expected", [("x86_64", 69), ("aarch64", 67)])
def test_elf_staging_collection_deselects_only_unavailable_native_cases(tmp_path, machine, expected):
    from tests import conftest as test_gates

    tree = ast.parse((ROOT / gate.ELF_STAGING_TEST).read_text())
    native = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "test_staged_metadata_image_executes_data_and_bss_relocations")
    marker = next(mark for mark in native.decorator_list if isinstance(mark, ast.Call)
                  and isinstance(mark.func, ast.Attribute) and mark.func.attr == "pcc_gate")
    predicate = next(keyword.value for keyword in marker.keywords if keyword.arg == "unavailable")
    assert isinstance(predicate, ast.IfExp)
    assert isinstance(predicate.body, ast.Constant) and predicate.body.value is None
    assert isinstance(predicate.orelse, ast.Constant) and predicate.orelse.value
    condition = ast.parse("sys.platform.startswith('linux') and platform.machine() in ('x86_64', 'amd64')",
                          mode="eval").body
    assert ast.dump(predicate.test) == ast.dump(condition)
    native_ids = [gate.ELF_STAGING_TEST + "::" + native.name + "[" + value + "]"
                  for value in ("False", "True")]
    ordinary_ids = [gate.ELF_STAGING_TEST + "::ordinary_" + str(index) for index in range(67)]
    reason = None if machine in ("x86_64", "amd64") else predicate.orelse.value
    items = []
    for node in ordinary_ids + native_ids:
        marks = (SimpleNamespace(kwargs={"unavailable": reason}),) if node in native_ids else ()
        items.append(SimpleNamespace(nodeid=node,
                                     iter_markers=lambda _name, marks=marks: iter(marks)))
    deselected = []
    config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items)))
    # Exercise the real hook and report verifier with synthetic reports only.
    # No native test module, compiler, emitter, runtime or child is invoked.
    test_gates.pytest_collection_modifyitems(config, items)
    assert [item.nodeid for item in deselected] == (native_ids if expected == 67 else [])
    nodes = [item.nodeid for item in items]
    assert nodes == ordinary_ids + (native_ids if expected == 69 else [])
    rows = reports(tmp_path, nodes)
    put(tmp_path, rows)
    assert gate.verify_pytest(tmp_path, [gate.ELF_STAGING_TEST], (expected,)) == nodes
    rows[3]["outcome"] = "skipped"
    put(tmp_path, rows)
    with pytest.raises(AssertionError):
        gate.verify_pytest(tmp_path, [gate.ELF_STAGING_TEST], (expected,))
    if expected == 67:
        put(tmp_path, reports(tmp_path, ordinary_ids + native_ids))
        with pytest.raises(AssertionError):
            gate.verify_pytest(tmp_path, [gate.ELF_STAGING_TEST], (expected,))


# Frozen test-only controls for the original repeated scans.
def _original_require_definition(text, symbol):
    pattern = (r'^define[^\n]*@"?' + re.escape(symbol)
               + r'"?\([^\n]*\)[^\n]*\{\n(?P<body>.*?)^\}')
    matches = list(re.finditer(pattern, text, re.M | re.S))
    assert len(matches) == 1, (symbol, "missing/duplicate definition")
    body = matches[0].group("body")
    assert not re.search(r"^define\b", body, re.M), (symbol, "cross-function match")
    assert "strict.nolib" not in body and "@py_cpy_" not in body and '@"py_cpy_' not in body
    assert not re.search(r'@"?py_exc_new"?\(\s*i64\s+11\s*,', body), symbol
    assert "NotImplementedError" not in body, symbol
    assert re.search(r"\bcall\b", body), (symbol, "empty/return-only replacement")

def _original_require_object_symbols(payload, target, required):
    """Owned readers validate the emitted container and every required code symbol."""
    darwin, windows = "-apple-" in target, "-windows-" in target
    if darwin:
        from pcc.backend.native_object import decode_native_object
        obj = decode_native_object(payload)
        machine = "PCO has no machine field; verified PIDX target and Darwin emitter route"
    elif windows:
        from pcc.backend.coff_x86_64 import parse_object
        obj = parse_object(payload)  # The reader requires AMD64 machine 0x8664.
        machine = 0x8664
    else:
        from pcc.backend.elf_x86_64 import parse_relocatable
        obj = parse_relocatable(payload)
        machine = 62 if target.startswith("x86_64-") else 183
        assert obj.machine == machine, "wrong ELF machine"
    for name in required:
        symbols = [symbol for symbol in obj.symbols if symbol.name == ("_" if darwin else "") + name]
        assert len(symbols) == 1, (name, "missing/duplicate object definition")
        symbol = symbols[0]
        index = symbol.section if windows else symbol.section_index
        assert 1 <= index <= len(obj.sections), (name, "undefined object symbol")
        section = obj.sections[index - 1]
        offset = symbol.offset if darwin else symbol.value
        assert 0 <= offset < len(section.data), (name, "symbol outside code")
        if darwin:
            assert symbol.external and (section.segname, section.sectname) == ("__TEXT", "__text")
        elif windows:
            assert symbol.external and symbol.function and section.flags & 0x20000000
        else:
            assert symbol.binding == 1 and symbol.type == 2 and section.flags & 4
    return {"machine": machine, "required_defined_symbols": list(required)}


def _plain_assertion_oracle(function):
    # Compare the unchanged explicit assertion payloads, not pytest's injected
    # presentation. Compile both source ASTs normally: the new exception helper
    # is not assertion-rewritten, while both test modules ordinarily are.
    path = Path(function.__code__.co_filename)
    tree = ast.parse(path.read_bytes())
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == function.__name__)
    assert not node.decorator_list
    namespace = dict(function.__globals__)
    code = compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec',
                   dont_inherit=True, optimize=0)
    exec(code, namespace)
    plain = namespace[function.__name__]
    # Bind the materialized function to the exact source AST and dependencies;
    # no assertion/message edits or substitute implementation are permitted.
    bound = next(item for item in ast.parse(Path(plain.__code__.co_filename).read_bytes()).body
                 if isinstance(item, ast.FunctionDef) and item.name == plain.__name__
                 and item.lineno == plain.__code__.co_firstlineno)
    assert ast.dump(bound, include_attributes=False) == ast.dump(node, include_attributes=False)
    assert all(namespace[name] is value for name, value in function.__globals__.items()
               if name != function.__name__)
    return plain


def _strict_check_result(check):
    try:
        return "return", check()
    except Exception as error:
        return "raise", type(error), str(error)


@pytest.mark.parametrize("shape", (
    "plain", "quoted", "duplicate", "mixed-duplicate", "missing", "empty",
    "stub", "cpy-bare", "cpy-quoted", "not-implemented", "exc11", "nested",
    "nested-and-later", "noncanonical-name", "multi-symbol-header", "multi-symbol-duplicate",
))
def test_strict_definition_index_matches_original_failures(shape):
    from tests.integration import test_worker_size_prior_closed_world as closure

    current = _plain_assertion_oracle(closure.require_definition)
    original = _plain_assertion_oracle(_original_require_definition)
    body = " call ptr @real()\n ret ptr null\n"
    text = "define ptr @user_test() {\n" + body + "}\n"
    symbol = "user_test"
    if shape == "quoted":
        text = text.replace("@user_test(", '@"user_test"(')
    elif shape == "duplicate":
        text *= 2
    elif shape == "mixed-duplicate":
        text += text.replace("@user_test(", '@"user_test"(')
    elif shape == "missing":
        symbol = "user_absent"
    elif shape == "empty":
        text = text.replace(" call ptr @real()\n", "")
    elif shape == "stub":
        text = text.replace(" call ptr", " strict.nolib.stub:\n call ptr")
    elif shape == "cpy-bare":
        text = text.replace("@real", "@py_cpy_import")
    elif shape == "cpy-quoted":
        text = text.replace("@real", '@"py_cpy_import"')
    elif shape == "not-implemented":
        text = text.replace("@real", "@NotImplementedError")
    elif shape == "exc11":
        text = text.replace("@real()", "@py_exc_new(i64 11, ptr null)")
    elif shape in ("nested", "nested-and-later"):
        text = "define ptr @outer() {\n" + text
        if shape == "nested-and-later":
            text += "define ptr @user_test() {\n" + body + "}\n"
        else:
            symbol = "outer"
    elif shape == "noncanonical-name":
        text = text.replace("user_test", "user-test")
        symbol = "user-test"
    elif shape in ("multi-symbol-header", "multi-symbol-duplicate"):
        ambiguous = text.replace("@user_test()", "@user_test(ptr @other())")
        text = ambiguous if shape == "multi-symbol-header" else text + ambiguous
    indexed = closure._index_definition_bodies(text)
    assert _strict_check_result(lambda: current(text, symbol, bodies=indexed)) == (
        _strict_check_result(lambda: original(text, symbol))
    )


@pytest.mark.parametrize("kind", ("pco", "elf", "coff"))
def test_strict_symbol_index_matches_original_failures(monkeypatch, kind):
    from tests.integration.test_worker_size_prior_closed_world import require_object_symbols

    current = _plain_assertion_oracle(require_object_symbols)
    original = _plain_assertion_oracle(_original_require_object_symbols)
    module_name, reader, target = {
        "pco": ("native_object", "decode_native_object", "arm64-apple-darwin"),
        "elf": ("elf_x86_64", "parse_relocatable", "x86_64-unknown-linux-gnu"),
        "coff": ("coff_x86_64", "parse_object", "x86_64-pc-windows-msvc"),
    }[kind]
    module = ModuleType("pcc.backend." + module_name)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    for fault in ("none", "missing", "duplicate", "undefined", "negative-offset",
                  "past-code", "private", "wrong-type", "wrong-section", "wrong-machine"):
        symbol = SimpleNamespace(name=("_" if kind == "pco" else "") + "user_test",
                                 section=1, section_index=1, offset=0, value=0,
                                 external=True, function=True, binding=1, type=2)
        section = SimpleNamespace(data=b"code", flags=0x20000004, segname="__TEXT", sectname="__text")
        obj = SimpleNamespace(machine=62, symbols=[symbol], sections=[section])
        if fault == "missing": obj.symbols = []
        elif fault == "duplicate": obj.symbols.append(symbol)
        elif fault == "undefined": symbol.section = symbol.section_index = 0
        elif fault == "negative-offset": symbol.offset = symbol.value = -1
        elif fault == "past-code": symbol.offset = symbol.value = len(section.data)
        elif fault == "private": symbol.external = False; symbol.binding = 0
        elif fault == "wrong-type": symbol.function = False; symbol.type = 0
        elif fault == "wrong-section": section.sectname = "__data"; section.flags = 0
        elif fault == "wrong-machine": obj.machine = 183
        setattr(module, reader, lambda payload: obj)
        actual = _strict_check_result(lambda: current(b"reader-owned", target, ["user_test"]))
        expected = _strict_check_result(lambda: original(b"reader-owned", target, ["user_test"]))
        assert actual == expected, (kind, fault)


def test_strict_lookup_indexes_scan_each_input_once(monkeypatch):
    from tests.integration import test_worker_size_prior_closed_world as closure

    source = "".join("define ptr @user_" + name + "() {\n call ptr @real()\n ret ptr null\n}\n"
                     for name in ("first", "second"))
    pattern = closure._DEFINITION_BODY
    scans = []

    def finditer(text):
        scans.append(text)
        return pattern.finditer(text)

    monkeypatch.setattr(closure, "_DEFINITION_BODY", SimpleNamespace(finditer=finditer))
    bodies = closure._index_definition_bodies(source)

    def unexpected(*args, **kwargs):
        raise AssertionError("indexed definition performed another section scan")

    monkeypatch.setattr(closure.re, "finditer", unexpected)
    for name in ("first", "second"):
        closure.require_definition(source, "user_" + name, bodies=bodies)
    assert scans == [source]

    class CountedSymbols(list):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    symbols = CountedSymbols(SimpleNamespace(name="_user_" + name, section_index=1,
                                             offset=0, external=True)
                             for name in ("first", "second"))
    obj = SimpleNamespace(symbols=symbols, sections=[SimpleNamespace(
        data=b"code", segname="__TEXT", sectname="__text",
    )])
    reader = ModuleType("pcc.backend.native_object")
    reader.decode_native_object = lambda payload: obj
    monkeypatch.setitem(sys.modules, reader.__name__, reader)
    closure.require_object_symbols(b"reader-owned", "arm64-apple-darwin", ["user_first", "user_second"])
    assert symbols.iterations == 1


def test_strict_progress_is_atomic_bounded_and_keeps_identity(tmp_path, monkeypatch):
    from tests.integration import test_worker_size_prior_closed_world as closure

    path = tmp_path / "worker-size-prior-closed-world.json"
    receipt = {"status": "RUNNING", "target": "chosen-target", "sources": {"original": "sha"},
               "codegen_sha256": "original-codegen", "modules": {"done": {"object_sha256": "bytes"}}}
    replace = closure.os.replace
    publications = []

    def atomic_replace(temporary, destination):
        assert destination == path and temporary == path.with_name(path.name + ".tmp")
        payload = json.loads(temporary.read_text())
        if path.exists():
            assert json.loads(path.read_text()) == publications[-1]
        publications.append(payload)
        replace(temporary, destination)

    monkeypatch.setattr(closure.os, "replace", atomic_replace)
    monkeypatch.setattr(closure, "time", SimpleNamespace(monotonic=lambda: 101.25))
    for phase, module in (("compile", ""), ("whole-verify", ""),
                          ("whole-parse", ""), ("whole-verify-parsed", ""),
                          *((phase, name) for name in closure.MODULES
                            for phase in ("module-verify", "module-emit"))):
        for state in ("started", "complete"):
            closure._record_closed_world_progress(path, receipt, 100.0, phase, state, module)
            assert json.loads(path.read_text()) == receipt
            assert receipt["progress"][-1] == {"phase": phase, "state": state,
                                                "module": module, "elapsed_s": 1.25}
    assert len(publications) == 8 + 4 * len(closure.MODULES) == 144
    assert receipt["status"] == "RUNNING" and receipt["sources"] == {"original": "sha"}
    assert receipt["codegen_sha256"] == "original-codegen"
    assert receipt["modules"] == {"done": {"object_sha256": "bytes"}}
    assert not path.with_name(path.name + ".tmp").exists()
    previous = path.read_bytes()
    with pytest.raises(AssertionError, match="phase bound"):
        closure._record_closed_world_progress(path, receipt, 100.0, "compile", "started")
    assert path.read_bytes() == previous


@pytest.mark.parametrize("fault", ("oversize", "replace-failed"))
def test_strict_progress_preserves_last_complete_json_on_write_failure(tmp_path, monkeypatch, fault):
    from tests.integration import test_worker_size_prior_closed_world as closure

    path = tmp_path / "worker-size-prior-closed-world.json"
    previous = b'{"status": "RUNNING", "phase": "compile"}\n'
    path.write_bytes(previous)
    if fault == "oversize":
        monkeypatch.setattr(closure, "_RECEIPT_MAX_BYTES", 8)
        error = AssertionError
    else:
        def failed_replace(*args):
            raise OSError("synthetic replace failure")
        monkeypatch.setattr(closure.os, "replace", failed_replace)
        error = OSError
    with pytest.raises(error):
        closure._publish_closed_world_receipt(path, {"status": "PASS"})
    assert path.read_bytes() == previous
    assert not path.with_name(path.name + ".tmp").exists()


def test_strict_progress_surrounds_real_phases_and_uses_collected_receipt():
    source = (ROOT / gate.CLOSURE_TEST).read_text()
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "test_worker_size_prior_modules_strict_target_emission")
    events = []

    class Calls(ast.NodeVisitor):
        def visit_Lambda(self, node):
            # This callback is executed by the helper, not at construction.
            pass

        def visit_Call(self, node):
            name = ast.unparse(node.func)
            if name == "_record_closed_world_progress":
                events.append((ast.literal_eval(node.args[3]), ast.literal_eval(node.args[4])))
            elif name in ("compile_python_multi", "verify_ir_text", "encode_indexed_module_file",
                          "emit_indexed_module_file", "require_object_symbols"):
                events.append(name)
            self.generic_visit(node)

    Calls().visit(function)
    verify_calls = [node for node in ast.walk(function)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "verify_ir_text"]
    whole_call = next(node for node in verify_calls if ast.unparse(node.args[0]) == "text")
    section_call = next(node for node in verify_calls if ast.unparse(node.args[0]) == "section")
    assert not section_call.keywords
    assert [keyword.arg for keyword in whole_call.keywords] == ["progress"]
    callback = whole_call.keywords[0].value
    assert isinstance(callback, ast.Lambda)
    assert ast.unparse(callback.body) == (
        "_record_closed_world_progress(receipt_path, receipt, started, "
        "{'parse': 'whole-parse', 'verify': 'whole-verify-parsed'}[phase], state)"
    )
    assert events == [
        ("compile", "started"), "compile_python_multi", ("compile", "complete"),
        ("whole-verify", "started"), "verify_ir_text", ("whole-verify", "complete"),
        ("module-verify", "started"), "verify_ir_text", ("module-verify", "complete"),
        ("module-emit", "started"), "encode_indexed_module_file", "emit_indexed_module_file",
        "require_object_symbols", ("module-emit", "complete"),
    ]
    assert 'receipt_path = tmp_path / "worker-size-prior-closed-world.json"' in source
    finalizer = next(node for node in function.body if isinstance(node, ast.Try)).finalbody
    assert isinstance(finalizer[-1], ast.Try)
    assert ast.unparse(finalizer[-1].body[-1]) == "_publish_closed_world_receipt(receipt_path, receipt)"
    assert ast.unparse(finalizer[-1].handlers[0].body[0]) == "if not primary_failed:\n    raise"
    workflow = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    assert workflow.count("build/worker-prior-gate/**/*.json\n") == 2
    helper_path = "tests/owned_ir_validation.py"
    assert workflow.count('      - "' + helper_path + '"') == 2
    gate_tree = ast.parse((ROOT / "scripts/ci_worker_size_prior_gate.py").read_text())
    identity = next(node for node in gate_tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "source_identity")
    hashes = next(node.value for node in ast.walk(identity) if isinstance(node, ast.Assign)
                  and isinstance(node.targets[0], ast.Subscript)
                  and isinstance(node.targets[0].slice, ast.Constant)
                  and node.targets[0].slice.value == "test_inputs")
    assert any(isinstance(node, ast.Constant) and node.value == helper_path
               for node in hashes.generators[0].iter.elts)
    command = gate.pytest_command(Path("build/worker-prior-gate/strict-closure"),
                                  [gate.CLOSURE_TEST + "::" + function.name], integration=True)
    assert command[command.index("--basetemp") + 1] == str(Path("build/worker-prior-gate/strict-closure") / "tests")
    assert "-n0" not in command and "-o" not in command


@pytest.mark.parametrize("failure", ("none", "compile", "compile-summary", "compile-publish", "parse", "verify",
                                     "parse-publish", "verify-publish", "encode", "emit", "publish"))
def test_strict_closure_releases_verified_inputs_before_next_consumer(tmp_path, monkeypatch, failure):
    import hashlib
    import weakref
    from tests.integration import test_worker_size_prior_closed_world as closure

    root, output = tmp_path / "source", tmp_path / "output"
    output.mkdir()
    names = ("pcc.frontends.python.mock_alpha", "pcc.frontends.python.mock_beta")
    dependency = "pcc.frontends.python.mock_dependency"
    for name in names:
        source = root / (name.replace(".", "/") + ".py")
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("def entry():\n    return 1\n")
    monkeypatch.setattr(closure, "MODULES", names)
    monkeypatch.setattr(closure, "__file__", str(root / "tests/integration/closure.py"))
    target = "x86_64-unknown-linux-gnu"
    error_type = OSError if failure == "publish" else RuntimeError
    original_error = error_type("synthetic " + failure + " failure")
    events, whole_refs, section_refs, module_refs, index_refs = [], [], [], [], []
    symbols = {name: "user_" + name.replace(".", "_") + "_entry"
               for name in (*names, dependency)}
    text = "".join('; ---- module: ' + name + ' ----\n'
                   + 'define ptr @' + symbols[name] + '() {\n call ptr @real()\n ret ptr null\n}\n'
                   for name in (*names, dependency))

    class TrackedSection(str):
        pass

    class TrackedIR(str):
        def __getitem__(self, key):
            assert isinstance(key, slice)
            assert events == ["compile", "whole-verify"] or events[-1] == "slice"
            assert module_refs[0]() is None, "whole parsed module still owns its arenas while sections are copied"
            value = TrackedSection(super().__getitem__(key))
            section_refs.append(weakref.ref(value))
            events.append("slice")
            return value

    class Parsed:
        pass

    class DefinitionIndex(dict):
        pass

    read_text = Path.read_text

    def tracked_read(path, *args, **kwargs):
        if path == output / "closed-world.ll":
            value = TrackedIR(read_text(path, *args, **kwargs))
            whole_refs.append(weakref.ref(value))
            return value
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked_read)
    original_index = closure._index_definition_bodies

    def indexed(section):
        value = DefinitionIndex(original_index(section))
        index_refs.append(weakref.ref(value))
        return value

    monkeypatch.setattr(closure, "_index_definition_bodies", indexed)

    def compile_multi(paths, destination, **kwargs):
        assert paths == [str(root / (name.replace(".", "/") + ".py")) for name in names]
        assert kwargs["module_names"] == list(names) and kwargs["libpython_mode"] == "off"
        assert kwargs["emit_llvm_only"] and kwargs["backend"] == "self"
        events.append("compile")
        profile = kwargs["profile"]
        assert profile == {}
        profile.update(phase_totals_ms={"multi_type_infer": 20, "python_ir_pass_pipeline_many": 30},
                       counters={"multi_files": 3},
                       events=[{"name": "multi_type_infer", "detail": names[0], "ms": 20}])
        # Existing timers update memory only. The pre-call receipt remains a
        # valid pending snapshot until normal return or an ordinary exception.
        pending = json.loads((output / "worker-size-prior-closed-world.json").read_text())
        assert pending["progress"][-1]["state"] == "started"
        assert pending["compile_profile"]["complete"] is False
        assert pending["compile_profile"]["phase_totals_ms"] == {}
        if failure in ("compile-summary", "compile-publish"):
            def fail_reporting(*args, **kwargs):
                raise OSError("secondary reporting failure")
            monkeypatch.setattr(closure, "_bounded_compile_profile" if failure == "compile-summary"
                                else "_publish_closed_world_receipt", fail_reporting)
        if failure.startswith("compile"):
            raise original_error
        if failure == "publish":
            publish = closure._publish_closed_world_receipt

            def fail_final_publication(path, receipt):
                if receipt["status"] == "PASS":
                    raise original_error
                publish(path, receipt)

            monkeypatch.setattr(closure, "_publish_closed_world_receipt", fail_final_publication)
        Path(destination).write_text(text)

    def verify(value, *, progress=None):
        is_whole = isinstance(value, TrackedIR)
        if is_whole:
            assert not section_refs, "section copies overlap whole-module parsing"
            assert callable(progress)
            events.append("whole-verify")
            for phase in ("parse", "verify"):
                progress(phase, "started")
                pending = json.loads((output / "worker-size-prior-closed-world.json").read_text())
                assert pending["status"] == "RUNNING"
                assert pending["modules"] == {}
                assert pending["progress"][-1]["phase"] == (
                    "whole-parse" if phase == "parse" else "whole-verify-parsed"
                )
                assert pending["progress"][-1]["state"] == "started"
                if failure in (phase, phase + "-publish"):
                    if failure.endswith("-publish"):
                        def failed_final_report(*args, **kwargs):
                            raise OSError("secondary phase reporting failure")
                        monkeypatch.setattr(closure, "_publish_closed_world_receipt", failed_final_report)
                    raise original_error
                progress(phase, "complete")
        else:
            assert progress is None, "section validation unexpectedly publishes whole phases"
            assert len(section_refs) == len(names), "unselected closure sections were copied"
            assert whole_refs[0]() is None, "whole text or regex markers are still retained"
            assert index_refs[-1]() is None, "definition Match index survived its last use"
            if len(module_refs) > 1:
                assert section_refs[len(module_refs) - 2]() is None, "previous section remains retained"
            events.append("section-verify")
        module = Parsed()
        module.triple = target
        module.functions = [SimpleNamespace(name=name, indexed_kernel=object())
                            for name in closure.DEFINITION.findall(value)]
        module_refs.append(weakref.ref(module))
        return module

    def encode(path, module):
        assert module_refs[-1]() is module
        events.append("encode")
        if failure == "encode":
            raise original_error
        Path(path).write_bytes(b"encoded-sidecar")

    def emit(path, destination, kind, *, optimize):
        assert Path(path).read_bytes() == b"encoded-sidecar" and kind == "PCO" and optimize is False
        assert module_refs[-1]() is None, "original parsed module overlaps decoded emitter module"
        events.append("emit")
        if failure == "emit":
            raise original_error
        Path(destination).write_bytes(b"owned-object")

    def require_symbols(payload, triple, required):
        assert payload == b"owned-object" and triple == target
        assert required == [symbols[names[len([event for event in events if event == "object-check"])]]]
        events.append("object-check")
        return {"required_defined_symbols": list(required)}

    monkeypatch.setattr(closure, "require_object_symbols", require_symbols)
    for module_name, exports in {
        "pcc.backend.self_backend_indexed_codec": {"encode_indexed_module_file": encode},
        "pcc.backend.self_backend_indexed_emit": {"emit_indexed_module_file": emit},
        "pcc.frontends.python.pipeline": {"compile_python_multi": compile_multi},
        "pcc.frontends.python.pipeline_targets": {"host_target_triple": lambda: target},
        "pcc.tools.runtime_archive_provenance": {"codegen_checksum": lambda: "fixed-codegen"},
        "tests.owned_ir_validation": {"verify_ir_text": verify},
    }.items():
        module = ModuleType(module_name)
        for key, value in exports.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, module_name, module)
    if failure == "none":
        closure.test_worker_size_prior_modules_strict_target_emission(output, monkeypatch)
    else:
        with pytest.raises(error_type, match="synthetic " + failure + " failure") as caught:
            closure.test_worker_size_prior_modules_strict_target_emission(output, monkeypatch)
        assert caught.value is original_error
    receipt = json.loads((output / "worker-size-prior-closed-world.json").read_text())
    assert receipt["target"] == target and receipt["codegen_sha256"] == "fixed-codegen"
    assert receipt["sources"] == {name: hashlib.sha256(
        (root / (name.replace(".", "/") + ".py")).read_bytes()).hexdigest() for name in names}
    if failure in ("compile-summary", "compile-publish"):
        assert receipt["status"] == "RUNNING" and receipt["modules"] == {}
        assert receipt["compile_profile"]["complete"] is False
        assert receipt["compile_profile"]["phase_totals_ms"] == {}
        assert receipt["progress"][-1]["phase"] == "compile" and receipt["progress"][-1]["state"] == "started"
        assert "closure_modules" not in receipt and events == ["compile"]
        return
    assert receipt["compile_profile"]["complete"] == (failure != "compile")
    assert receipt["compile_profile"]["phase_totals_ms"] == {
        "multi_type_infer": 20, "python_ir_pass_pipeline_many": 30,
    }
    assert receipt["compile_profile"]["module_events"]["multi_type_infer"] == [
        {"module": names[0], "ms": 20},
    ]
    if failure == "compile":
        assert receipt["status"] == "FAIL" and receipt["modules"] == {}
        assert receipt["error"] == "RuntimeError: synthetic compile failure"
        assert "closure_modules" not in receipt and events == ["compile"]
        return
    if failure in ("parse", "verify", "parse-publish", "verify-publish"):
        phase = "whole-parse" if failure.startswith("parse") else "whole-verify-parsed"
        assert receipt["progress"][-1]["phase"] == phase
        assert receipt["progress"][-1]["state"] == "started"
        assert receipt["status"] == ("RUNNING" if failure.endswith("-publish") else "FAIL")
        if not failure.endswith("-publish"):
            assert receipt["error"] == "RuntimeError: synthetic " + failure + " failure"
        assert "closure_modules" not in receipt and receipt["modules"] == {}
        assert events == ["compile", "whole-verify"]
        return
    assert receipt["closure_modules"] == [*names, dependency]
    assert receipt["closure_definitions"] == 3
    if failure == "publish":
        assert receipt["status"] == "RUNNING" and list(receipt["modules"]) == list(names)
        assert receipt["progress"][-1]["phase"] == "module-emit" and receipt["progress"][-1]["state"] == "complete"
        return
    if failure == "none":
        assert receipt["status"] == "PASS" and list(receipt["modules"]) == list(names)
        for name in names:
            section = '\ndefine ptr @' + symbols[name] + '() {\n call ptr @real()\n ret ptr null\n}\n'
            assert receipt["modules"][name] == {
                "object_contract": {"required_defined_symbols": [symbols[name]]},
                "functions": ["entry"], "ir_sha256": hashlib.sha256(section.encode()).hexdigest(),
                "object_sha256": hashlib.sha256(b"owned-object").hexdigest(), "object_bytes": 12,
            }
        assert all(reference() is None for reference in whole_refs + section_refs + module_refs + index_refs)
        assert events == ["compile", "whole-verify", "slice", "slice",
                          *(event for _ in names for event in ("section-verify", "encode", "emit", "object-check"))]
    else:
        assert receipt["status"] == "FAIL" and receipt["modules"] == {}
        assert receipt["error"] == "RuntimeError: synthetic " + failure + " failure"
        assert events == ["compile", "whole-verify", "slice", "slice", "section-verify", "encode"] + (
            ["emit"] if failure == "emit" else []
        )


def _load_owned_ir_validation_control(monkeypatch, parse, verify, target):
    # Install all PCC imports before executing this small test helper module.
    # This control never imports the real parser, verifier or frontend.
    for name, exports in {
        "pcc.backend.self_backend_parse": {"parse_self_backend_module": parse},
        "pcc.backend.self_backend_verify": {"verify_parsed_module": verify},
        "pcc.frontends.python.pipeline_targets": {"host_target_triple": target},
    }.items():
        module = ModuleType(name)
        for key, value in exports.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location(
        "_owned_ir_validation_control", ROOT / "tests/owned_ir_validation.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("text", (
    'target triple = "chosen-target"\ndefine void @f() {}\n',
    '  target \ttriple = "chosen-target"\ndefine void @f() {}\n',
    'define void @f() {}\n',
))
@pytest.mark.parametrize("with_progress", (False, True))
def test_owned_ir_validation_progress_preserves_normalization_and_identity(monkeypatch, text, with_progress):
    events = []
    parsed = object()
    needs_target = not text.lstrip().startswith("target")
    normalized = ('target triple = "host-target"\n' if needs_target else '') + text

    class Text:
        def __str__(self):
            events.append("normalize")
            return text

    def target():
        events.append("target")
        return "host-target"

    def parse(value):
        assert type(value) is str and value == normalized
        events.append("parse")
        return parsed

    def verify(value):
        assert value is parsed
        events.append("verify")

    helper = _load_owned_ir_validation_control(monkeypatch, parse, verify, target)
    def progress(phase, state):
        events.append((phase, state))

    if with_progress:
        result = helper.verify_ir_text(Text(), progress=progress)
    else:
        result = helper.verify_ir_text(Text())
    assert result is parsed
    expected = ["normalize"] + (["target"] if needs_target else [])
    if with_progress:
        expected += [("parse", "started"), "parse", ("parse", "complete"),
                     ("verify", "started"), "verify", ("verify", "complete")]
    else:
        expected += ["parse", "verify"]
    assert events == expected


@pytest.mark.parametrize("failure", (
    "normalize", "target", "parse", "verify",
    "parse-started", "parse-complete", "verify-started", "verify-complete",
))
def test_owned_ir_validation_progress_preserves_first_failure(monkeypatch, failure):
    events = []
    original_error = RuntimeError("original " + failure)
    parsed = object()

    def event(name):
        events.append(name)
        if name == failure:
            raise original_error

    class Text:
        def __str__(self):
            event("normalize")
            return "define void @f() {}\n"

    def target():
        event("target")
        return "host-target"

    def parse(value):
        assert value == 'target triple = "host-target"\ndefine void @f() {}\n'
        event("parse")
        return parsed

    def verify(value):
        assert value is parsed
        event("verify")

    helper = _load_owned_ir_validation_control(monkeypatch, parse, verify, target)
    def progress(phase, state):
        event(phase + "-" + state)

    with pytest.raises(RuntimeError) as caught:
        helper.verify_ir_text(Text(), progress=progress)
    assert caught.value is original_error
    full_order = ["normalize", "target", "parse-started", "parse", "parse-complete",
                  "verify-started", "verify", "verify-complete"]
    assert events == full_order[:full_order.index(failure) + 1]


def test_strict_compile_profile_keeps_only_bounded_phase_data():
    from tests.integration import test_worker_size_prior_closed_world as closure

    profile = {
        "phase_totals_ms": {name: 123 for name in closure._COMPILE_PROFILE_PHASES},
        "counters": {name: 456 for name in closure._COMPILE_PROFILE_COUNTERS},
        "events": [], "secret": "/private/source/path",
    }
    module_names = {index: ("pcc.module_" + str(index) + "_").ljust(160, "x") for index in range(20)}
    for phase in closure._COMPILE_PROFILE_MODULE_PHASES:
        profile["events"].extend({"name": phase, "detail": module_names[index], "ms": index}
                                 for index in range(20))
        for detail, duration in (("/private/source/path", 1), ("pcc." + "x" * 160, 1),
                                 ("pcc.hidden", True), ("pcc.hidden", -1),
                                 ("pcc.hidden", 9223372036854775808), ("pcc.hidden", float("inf"))):
            profile["events"].append({"name": phase, "detail": detail, "ms": duration})
    profile["events"] += [None, {"name": [], "detail": "pcc.hidden", "ms": 1},
                          {"name": "unapproved", "detail": "/private/path", "ms": 1}]
    profile["phase_totals_ms"].update(unapproved="/private/path", multi_type_infer=True)
    profile["counters"].update(unapproved="/private/path", multi_files=-1)
    snapshot = closure._bounded_compile_profile(profile, complete=True)
    assert snapshot["complete"] is True and snapshot["module_events_truncated"] is True
    assert set(snapshot["phase_totals_ms"]) == set(closure._COMPILE_PROFILE_PHASES) - {"multi_type_infer"}
    assert set(snapshot["counters"]) == set(closure._COMPILE_PROFILE_COUNTERS) - {"multi_files"}
    for phase in closure._COMPILE_PROFILE_MODULE_PHASES:
        assert snapshot["module_event_counts"][phase] == 20
        assert snapshot["module_events"][phase] == [
            {"module": module_names[index], "ms": index} for index in range(19, 11, -1)
        ]
    encoded = json.dumps(snapshot, allow_nan=False)
    assert "/private" not in encoded and "hidden" not in encoded and len(encoded.encode()) < 16384


def test_strict_compile_profile_snapshot_is_detached_and_handles_empty_fields():
    from tests.integration import test_worker_size_prior_closed_world as closure

    profile = {"phase_totals_ms": {"multi_codegen_layer1": 5}, "counters": {"multi_files": 2},
               "events": [{"name": "multi_codegen_layer1", "detail": "pcc.z", "ms": 5},
                          {"name": "multi_codegen_layer1", "detail": "pcc.a", "ms": 5}]}
    snapshot = closure._bounded_compile_profile(profile, complete=False)
    saved = json.dumps(snapshot, sort_keys=True)
    assert snapshot["capture"] == "return-or-python-exception" and snapshot["complete"] is False
    assert snapshot["module_events"]["multi_codegen_layer1"] == [
        {"module": "pcc.a", "ms": 5}, {"module": "pcc.z", "ms": 5},
    ]
    profile["phase_totals_ms"].clear()
    profile["counters"].clear()
    profile["events"][0]["detail"] = "/private/changed"
    profile.clear()
    assert json.dumps(snapshot, sort_keys=True) == saved
    empty = closure._bounded_compile_profile(
        {"phase_totals_ms": [], "counters": None, "events": "not-events"}, complete=False,
    )
    assert empty["phase_totals_ms"] == {} and empty["counters"] == {}
    assert not any(empty["module_event_counts"].values())
    assert not any(empty["module_events"].values()) and not empty["module_events_truncated"]


@pytest.mark.parametrize("retaining", [False, True], ids=["retired", "original-reference"])
@pytest.mark.parametrize("fail_freeze", [False, True], ids=["success", "freeze-failure"])
def test_parser_input_lifetimes_release_only_after_successful_freeze(retaining, fail_freeze):
    # Execute the actual traversal with synthetic owners, not the PCC parser,
    # unsafe arenas, codec, emitter, or native code. The old-loop control must
    # exhibit the opposite lifetime at the second function.
    path = ROOT / "pcc/backend/self_backend_parse.py"
    source = ast.parse(path.read_text())
    function, = [node for node in source.body
                 if isinstance(node, ast.FunctionDef) and node.name == "_parse_functions"]
    tree = ast.Module(body=[function], type_ignores=[])
    if retaining:
        reference = ast.parse((ROOT / "tests/c/test_self_backend_function_body_lines.py").read_text())
        restore, = [node for node in reference.body
                    if isinstance(node, ast.FunctionDef) and node.name == "_restore_retained_parse_inputs"]
        scope = {"ast": ast}
        exec(compile(ast.Module(body=[restore], type_ignores=[]), "<pure-parser-reference>", "exec"), scope)
        tree = scope["_restore_retained_parse_inputs"](tree)

    class Owner:
        def __init__(self, name):
            self.name = name
        def close(self):
            raise AssertionError("shared parser owners must not be closed")

    class Definitions(list):
        pass

    class Function:
        def __init__(self, **values):
            self.__dict__.update(values)

    header_refs, body_refs, seed_refs, arenas, functions, visits = [], [], [], [], [], []
    scan_refs = []
    failure = RuntimeError("original freeze failure")

    def scan(text):
        assert text == "all-input" and visits == []
        definitions = Definitions()
        for name in ("first", "second"):
            header, body = Owner(name), Owner(name)
            header_refs.append(weakref.ref(header))
            body_refs.append(weakref.ref(body))
            definitions.append((header, body))
        scan_refs.append(weakref.ref(definitions))
        return definitions

    def parse_blocks(name, body, args, **kwargs):
        assert body.name == name and args == []
        if name == "second":
            assert (header_refs[0]() is not None) is retaining
            assert (body_refs[0]() is not None) is retaining
            assert (seed_refs[0]() is not None) is retaining
            assert functions[0].indexed_kernel.arena is arenas[0]
        visits.append(name)
        assert len(scan_refs[0]()) == (2 if retaining else 2 - len(visits))
        seed = Owner(name)
        seed.construction = Owner("temporary columns")
        seed.arena = Owner("shared final arena")
        seed_refs.append(weakref.ref(seed))
        arenas.append(seed.arena)
        return [], seed

    def freeze(function):
        assert header_refs[-1 if function.name == "second" else 0]() is not None
        assert body_refs[-1 if function.name == "second" else 0]() is not None
        if fail_freeze and function.name == "second":
            raise failure
        function.indexed_kernel = SimpleNamespace(arena=function.indexed_seed.arena)
        function.indexed_seed = None
        functions.append(function)
        return function.indexed_kernel

    scope = {
        "_iter_function_defs": scan,
        "_parse_function_header": lambda header, **kwargs: ("", "i64", header.name, ""),
        "decode_global_name": lambda name: name,
        "check_simple_symbol_name": lambda name: None,
        "_parse_type": lambda text, **kwargs: "i64",
        "_parse_arg_infos": lambda name, text, **kwargs: [],
        "_parse_blocks": parse_blocks,
        "ParsedFunction": Function,
        "_arg_list_is_vararg": lambda text: False,
        "get_indexed_function_kernel": freeze,
    }
    tree.body[:0] = ast.parse("from __future__ import annotations").body
    exec(compile(ast.fix_missing_locations(tree), "<pure-parser-input-lifetimes>", "exec"), scope)
    parse = scope["_parse_functions"]
    if fail_freeze:
        with pytest.raises(RuntimeError) as caught:
            parse("all-input", type_context=object())
        assert caught.value is failure
        # The failed frame still owns the unadopted seed and current texts.
        assert seed_refs[1]() is not None
        assert header_refs[1]() is not None and body_refs[1]() is not None
        assert (seed_refs[0]() is not None) is False
    else:
        result = parse("all-input", type_context=object())
        assert result == functions and [item.name for item in result] == ["first", "second"]
        assert all(ref() is None for ref in (*header_refs, *body_refs, *seed_refs))
        assert scan_refs[0]() is None
        assert all(item.indexed_kernel.arena is arena for item, arena in zip(result, arenas))
    assert visits == ["first", "second"]


@pytest.mark.parametrize("timeout", (300, 1800))
def test_strict_closure_budget_reaches_existing_guard_without_changing_xdist(tmp_path, monkeypatch, timeout):
    nodes = [gate.CLOSURE_TEST + "::test_worker_size_prior_modules_strict_target_emission"]
    calls = []
    env = {"PCC_RUNTIME_ARCHIVE": "verified.a"}
    monkeypatch.setattr(gate, "guarded", lambda *args: calls.append(args))
    monkeypatch.setattr(gate, "verify_pytest", lambda directory, actual, counts: actual)
    assert gate.run_pytest(tmp_path, "strict-closure", nodes, env,
                           integration=True, timeout=timeout) == nodes
    command, directory, actual_timeout, actual_env = calls[0]
    assert actual_timeout == timeout and actual_env is env
    assert directory == tmp_path / "strict-closure"
    assert command == gate.pytest_command(directory, nodes, integration=True)
    assert "-n0" not in command and "-o" not in command


@pytest.mark.parametrize("arguments,expected", (([], 300), (["--strict-closure-timeout", "1800"], 1800)))
def test_cli_keeps_local_strict_budget_and_accepts_explicit_ci_budget(tmp_path, monkeypatch, arguments, expected):
    calls = []
    monkeypatch.setattr(gate, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    gate.main(["--out-dir", str(tmp_path), "--phase", "preflight", *arguments])
    assert calls == [((tmp_path, "preflight"), {"strict_closure_timeout": expected})]


@pytest.mark.parametrize("timeout", ("0", "-1"))
def test_cli_rejects_unbounded_or_negative_strict_budget(tmp_path, monkeypatch, timeout):
    monkeypatch.setattr(gate, "run", lambda *args, **kwargs: pytest.fail("invalid budget launched a gate"))
    with pytest.raises(SystemExit) as caught:
        gate.main(["--out-dir", str(tmp_path), "--phase", "preflight", "--strict-closure-timeout", timeout])
    assert caught.value.code == 2


@pytest.mark.parametrize("target,machine", (("darwin", "arm64"), ("linux", "x86_64"),
                                             ("linux", "aarch64"), ("win32", "amd64")))
@pytest.mark.parametrize("independent_preflight", ("missing", "failed"))
def test_stage1_builds_once_without_preflight_success(tmp_path, monkeypatch, target, machine, independent_preflight):
    root, out = tmp_path / "repo", tmp_path / "stage1"
    archive = out / "runtime/libpy_runtime_pcc_py.a"
    root.mkdir()
    independent = tmp_path / "worker-prior-preflight"
    if independent_preflight == "failed":
        gate.save(independent / "preflight.json", {"status": "FAIL", "error": "native node or strict timeout"})
    source, runtime = {"source": "fixed"}, {"runtime": "verified"}
    calls = []
    monkeypatch.setattr(gate, "ROOT", root)
    monkeypatch.setattr(gate, "sys", SimpleNamespace(platform=target, executable="chosen-python"))
    monkeypatch.setattr(gate, "platform", SimpleNamespace(machine=lambda: machine))
    monkeypatch.setattr(gate, "source_identity", lambda: source)
    monkeypatch.setattr(gate, "environment", lambda: {"PCC_RUNTIME_ARCHIVE": "stale", "PCC_TEST_NO_NATIVE_PROVISIONING": "1"})
    monkeypatch.setattr(gate, "run_pytest", lambda *args, **kwargs: pytest.fail("Stage1 ran a preflight test"))
    monkeypatch.setattr(gate, "native_gate", lambda *args: pytest.fail("Stage1 ran a prior-worker gate"))

    def guarded(command, directory, timeout, env):
        assert command == ["chosen-python", "-m", "pcc.frontends.python.owned_runtime_build", "--output", str(archive)]
        assert directory == out / "runtime-build" and timeout == 1200
        assert "PCC_RUNTIME_ARCHIVE" not in env and "PCC_TEST_NO_NATIVE_PROVISIONING" not in env
        calls.append("runtime-build")

    def identity(path):
        assert path == archive
        calls.append("runtime-identity")
        return runtime

    def bootstrap(command, *, cwd, env, stdout, stderr):
        assert calls == ["runtime-build", "runtime-identity"]
        assert cwd == root and env["PCC_RUNTIME_ARCHIVE"] == str(archive)
        assert env["PCC_TEST_NO_NATIVE_PROVISIONING"] == "1"
        assert env["PCC_PY_FRONTEND_WORKER_TIMING"] == "1"
        assert not (out / "preflight.json").exists()
        destination = root / "build" / ("bootstrap" if target == "darwin" else "platform-qualification")
        if target == "darwin":
            assert command == ["chosen-python", "scripts/bootstrap.py", "--backend", "self", "--stage", "1", "--out-dir", str(destination)]
            assert env["PCC_HOST_INDEXED_PROCESS_SPLIT"] == "1"
            assert env["PCC_BOOTSTRAP_STAGE_TIMEOUT"] == "2400"
            assert env["PCC_BOOTSTRAP_AUTO_TREE_RSS_CEILING_BYTES"] == "4294967296"
            assert env["PCC_BOOTSTRAP_MIN_TREE_RSS_BYTES"] == "2147483648"
            assert env["PCC_BOOTSTRAP_HOST_MEMORY_RESERVE_BYTES"] == "536870912"
            gate.save(out / "profile/stage1.result.json", {"compile_wall_ms": 1000, "returncode": 0,
                                                           "publish_barrier_returncode": 0})
            gate.save(destination / "stage1.process.test/result.json", {
                "status": "COMPLETE", "returncode": 0, "timeout_s": 2400,
                "max_tree_rss_bytes": 3221225472, "peak_tree_rss_bytes": 2147483648,
                "darwin_preflight_reserve_bytes": 536870912,
            })
            (destination / "stage1.process.test/target.stderr").write_text("original diagnostics")
            binary = destination / "pcc1"
        else:
            assert command == ["chosen-python", "scripts/bootstrap_platform.py", "--gc", "all", "--stage", "3",
                               "--out-dir", str(destination), "--runtime-archive", str(archive),
                               "--timeout", "2400", "--rss-limit", "17179869184", "--cpu-budget", "4"]
            assert env["PCC_HOST_INDEXED_PROCESS_SPLIT"] == "0"
            gate.save(destination / "shared/receipt.json", {"stages": [{"seconds": 1}]})
            (destination / "shared/stage1.log").write_text("original diagnostics")
            binary = destination / "shared" / ("pcc1.exe" if target == "win32" else "pcc1")
        binary.write_bytes(b"new compiler")
        calls.append("bootstrap")
        return SimpleNamespace(returncode=0)

    def metrics(text, wall):
        assert text == "original diagnostics" and wall == 1
        return {"open_attempts": 0, "source_bytes_complete": 10, "source_bytes_total": 10,
                "completed_modules": 1, "planned_modules": 1, "average_workers": 2}

    monkeypatch.setattr(gate, "guarded", guarded)
    monkeypatch.setattr(gate, "runtime_identity", identity)
    monkeypatch.setattr(gate.subprocess, "run", bootstrap)
    monkeypatch.setattr(gate, "admission_metrics", metrics)
    gate.run(out, "stage1")
    assert calls == ["runtime-build", "runtime-identity", "bootstrap", "runtime-identity"]
    receipt = json.loads((out / "stage1.json").read_text())
    assert receipt["status"] == "PASS" and receipt["source"] == source and receipt["runtime"] == runtime
    assert receipt["compiler"]["sha256"] == gate.sha(receipt["compiler"]["path"])
    assert not (out / "preflight.json").exists()


@pytest.mark.parametrize("fault", ("runtime-build", "source", "codegen", "target", "configuration", "bootstrap"))
def test_stage1_stops_at_runtime_or_bootstrap_failure_without_preflight(tmp_path, monkeypatch, fault):
    from tests import runtime_fixture_provenance
    out = tmp_path / "stage1"
    calls = []
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "sys", SimpleNamespace(platform="linux", executable="chosen-python"))
    monkeypatch.setattr(gate, "platform", SimpleNamespace(machine=lambda: "x86_64"))
    monkeypatch.setattr(gate, "source_identity", lambda: {"source": "fixed"})
    monkeypatch.setattr(gate, "environment", lambda: {})
    def build(*args):
        calls.append("runtime-build")
        if fault == "runtime-build":
            raise RuntimeError("runtime-build rejected")
    def reject(archive, *, threads):
        assert threads is False
        calls.append("runtime-identity")
        raise ValueError(fault + " rejected")
    def bootstrap(*args, **kwargs):
        calls.append("bootstrap")
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(gate, "guarded", build)
    monkeypatch.setattr(runtime_fixture_provenance, "_verified_test_runtime_archive", reject)
    if fault == "bootstrap":
        monkeypatch.setattr(gate, "runtime_identity", lambda archive: {"runtime": "verified"})
    monkeypatch.setattr(gate.subprocess, "run", bootstrap)
    with pytest.raises((RuntimeError, ValueError, gate.subprocess.CalledProcessError)):
        gate.run(out, "stage1")
    assert calls == (["runtime-build"] if fault == "runtime-build" else
                     ["runtime-build", "bootstrap"] if fault == "bootstrap" else
                     ["runtime-build", "runtime-identity"])
    receipt = json.loads((out / "stage1.json").read_text())
    assert receipt["status"] == "FAIL"
    if fault == "bootstrap":
        assert receipt["returncode"] == 7
    assert not (out / "preflight.json").exists()


def test_stage1_requires_fresh_outputs_before_any_build(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "source_identity", lambda: pytest.fail("stale outputs admitted"))
    with pytest.raises(AssertionError, match="stage1 requires a fresh output root"):
        gate.run(tmp_path, "stage1")


@pytest.mark.parametrize("fault", (None, "missing", "status", "source", "runtime", "compiler"))
def test_pcc1_requires_only_successful_matching_stage1_and_never_builds(tmp_path, monkeypatch, fault):
    binary = tmp_path / "pcc1"
    binary.write_bytes(b"compiler")
    source, runtime = {"source": "fixed"}, {"runtime": "matched"}
    stage = {"status": "PASS", "source": source, "runtime": runtime,
             "compiler": {"path": str(binary), "sha256": gate.sha(binary)}}
    if fault in ("status", "source", "runtime"):
        stage[fault] = "wrong"
    elif fault == "compiler":
        stage["compiler"]["sha256"] = "wrong"
    if fault != "missing":
        gate.save(tmp_path / "stage1.json", stage)
    calls = []
    monkeypatch.setattr(gate, "source_identity", lambda: source)
    monkeypatch.setattr(gate, "environment", lambda: {})
    monkeypatch.setattr(gate, "runtime_identity", lambda archive: runtime)
    monkeypatch.setattr(gate, "prepare_runtime", lambda *args: pytest.fail("pcc1 reprovisioned runtime"))
    def native(directory, owner, env):
        assert directory == tmp_path and owner == "pcc1"
        assert env["PCC_CURRENT_PCC1"] == str(binary)
        assert env["PCC_RUNTIME_ARCHIVE"] == str(tmp_path / "runtime/libpy_runtime_pcc_py.a")
        assert env["PCC_TEST_NO_NATIVE_PROVISIONING"] == "1"
        calls.append("native-pcc1")
    monkeypatch.setattr(gate, "native_gate", native)
    if fault:
        with pytest.raises((AssertionError, FileNotFoundError)):
            gate.run(tmp_path, "pcc1")
    else:
        gate.run(tmp_path, "pcc1")
    assert calls == ([] if fault else ["native-pcc1"])
    assert json.loads((tmp_path / "pcc1.json").read_text())["status"] == ("FAIL" if fault else "PASS")
    assert not (tmp_path / "preflight.json").exists()


def test_arm_packed_width_cache_source_and_observation_contract():
    # Source-only control: no import or execution of either ELF validator.
    import copy
    import hashlib

    oracle_tree = ast.parse((ROOT / "tests/elf_validate_object_preimage.py").read_text())
    original = ast.literal_eval(next(node.value for node in oracle_tree.body
                                    if isinstance(node, ast.Assign)
                                    and len(node.targets) == 1
                                    and isinstance(next(iter(node.targets)), ast.Name)
                                    and next(iter(node.targets)).id == "ORIGINAL_VALIDATE_OBJECT_SOURCE"))
    assert hashlib.sha256(original.encode()).hexdigest() == (
        "77c35a2eca9f99889622a6423b294885c0df22feeabce0694b61499b9d9533ae")
    source = (ROOT / "pcc/backend/elf_x86_64.py").read_text()
    tree = ast.parse(source)
    candidate = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == "_validate_object")
    def one(nodes):
        matches = list(nodes)
        assert len(matches) == 1
        return next(iter(matches))

    def named(node, name):
        return isinstance(node, ast.Name) and node.id == name

    def assigned(node, name):
        return (isinstance(node, ast.Assign) and len(node.targets) == 1
                and named(one(node.targets), name))

    def dump(node):
        return ast.dump(node, include_attributes=False)

    def statements(nodes):
        return dump(ast.Module(body=list(nodes), type_ignores=[]))

    def section_loop(function):
        return one(node for node in function.body if isinstance(node, ast.For)
                   and isinstance(node.target, ast.Tuple)
                   and all(isinstance(item, ast.Name) for item in node.target.elts)
                   and tuple(item.id for item in node.target.elts) == ("index", "section"))

    def relocation_loop(sections):
        return one(node for node in sections.body if isinstance(node, ast.For)
                   and named(node.target, "relocation")
                   and dump(node.iter) == dump(ast.parse("section.relocations", mode="eval").body))

    original_function = one(ast.parse(original).body)
    original_sections = section_loop(original_function)
    original_relocations = relocation_loop(original_sections)
    original_width = one(node for node in original_relocations.body if assigned(node, "width"))
    expected_guard = ast.parse(
        "type(obj) is ElfObject and type(obj.sections) is tuple and type(obj.symbols) is tuple "
        "and type(section) is ElfSection and type(section.data) is bytes "
        "and type(section.relocations) is _PackedElfRelocations "
        "and type(section.relocations.payload) is bytes "
        "and type(section.relocations.offset) is int and type(section.relocations.size) is int "
        "and type(obj.machine) is int and obj.machine == EM_AARCH64", mode="eval").body
    expected_branch = one(ast.parse("""if cache_widths:
    width = relocation_widths.get(relocation.type)
    if width is None:
        width = _relocation_width(relocation.type, obj.machine)
        if len(relocation_widths) < 21:
            relocation_widths[relocation.type] = width
else:
    width = _relocation_width(relocation.type, obj.machine)
""").body)
    # Admit exactly the separately checked negative-proof additions. Do not
    # skip arbitrary statements while looking for the mandatory row traversal.
    expected_initial = one(ast.parse("classification_candidate = None").body)
    expected_summary_gate = one(ast.parse("""if (type(obj) is ElfObject and type(obj.sections) is tuple
        and type(obj.symbols) is tuple and type(section) is ElfSection
        and type(section.relocations) is _PackedElfRelocations):
    candidate = section.relocations._classification_candidate
    if (type(candidate) is list and len(candidate) == 1
            and (candidate[0] is None or type(candidate[0]) is bool)):
        candidate[0] = None
        if cache_widths and type(_RELA) is struct.Struct and _RELA.format == "<QQq":
            classification_candidate = candidate
            classification_candidate[0] = True
""").body)
    expected_summary_tail = one(ast.parse("""if classification_candidate is not None and classification_candidate[0] is True:
    if type(relocation) is not ElfRelocation or type(relocation.type) is not int:
        classification_candidate[0] = None
    elif relocation.type in (283, 311, 312, 541, 542):
        classification_candidate[0] = False
""").body)

    def restore_declared_optimizations(function):
        restored = copy.deepcopy(function)
        cache = one(node for node in restored.body if isinstance(node, ast.AnnAssign)
                    and named(node.target, "relocation_widths"))
        assert dump(cache) == dump(one(ast.parse("relocation_widths: dict[int, int] = {}").body))
        sections = section_loop(restored)
        # The successful-width cache is initialized immediately before the
        # original section walk, never before the original object checks.
        assert restored.body == [part for node in restored.body if node is not cache
                                 for part in ([cache, node] if node is sections else [node])]
        guard = one(node for node in sections.body if assigned(node, "cache_widths"))
        assert dump(guard.value) == dump(expected_guard)
        initial = one(node for node in sections.body if assigned(node, "classification_candidate"))
        assert dump(initial) == dump(expected_initial)
        summary_gate = one(node for node in sections.body if dump(node) == dump(expected_summary_gate))
        relocations = relocation_loop(sections)
        branch = one(node for node in relocations.body if isinstance(node, ast.If)
                     and named(node.test, "cache_widths"))
        assert dump(branch) == dump(expected_branch)
        summary_tail = one(node for node in relocations.body if dump(node) == dump(expected_summary_tail))
        # Exact complete lists bind all first-error/read order, not just loop
        # existence or relative locations. Every original check stays in place.
        assert statements(sections.body) == statements(
            [node for node in original_sections.body if node is not original_relocations]
            + [guard, initial, summary_gate, relocations])
        assert statements(relocations.body) == statements(
            [branch if node is original_width else node for node in original_relocations.body]
            + [summary_tail])
        restored.body.remove(cache)
        sections.body = [node for node in sections.body
                         if node is not guard and node is not initial and node is not summary_gate]
        relocations.body = [part for node in relocations.body if node is not summary_tail
                            for part in (branch.orelse if node is branch else [node])]
        # Removing only the declared optimizations restores the entire frozen
        # validator, including symbol and stack-map checks and traversal order.
        assert dump(restored) == dump(original_function)

    restore_declared_optimizations(candidate)
    # Four bounded AST-only negative controls. Never execute either validator.
    for fault in ("extra-statement", "earlier-width", "weakened-type", "cache-cap"):
        changed = copy.deepcopy(candidate)
        sections = section_loop(changed)
        relocations = relocation_loop(sections)
        branch = one(node for node in relocations.body if isinstance(node, ast.If)
                     and named(node.test, "cache_widths"))
        if fault == "extra-statement":
            extra = one(ast.parse("unexpected_observation = None").body)
            sections.body = [part for node in sections.body
                             for part in ([extra, node] if node is relocations else [node])]
        elif fault == "earlier-width":
            relocations.body = [branch] + [node for node in relocations.body if node is not branch]
        elif fault == "weakened-type":
            guard = one(node for node in sections.body if assigned(node, "cache_widths"))
            guard.value = ast.Constant(value=True)
        else:
            cap = one(node for node in ast.walk(branch) if isinstance(node, ast.Constant)
                      and node.value == 21)
            cap.value = 22
        with pytest.raises(AssertionError):
            restore_declared_optimizations(changed)
    # Load only the literal classification and two source-discovery/report
    # functions. The inventory tool's PCC/parser/emitter imports never run.
    from collections import Counter

    inventory_tree = ast.parse((ROOT / "scripts/pcc_record_inventory.py").read_text())
    contract = ast.literal_eval(one(node.value for node in inventory_tree.body
                                   if assigned(node, "DATA_PLANE_CLASS_CONTRACT")))
    categories = one(node.value for node in inventory_tree.body
                     if assigned(node, "_DATA_PLANE_CLASSIFICATIONS"))
    assert isinstance(categories, ast.Call) and named(categories.func, "frozenset")
    assert not categories.keywords
    categories = frozenset(ast.literal_eval(one(categories.args)))
    key = "owned_elf_inputs.py:_ClassificationProof"
    assert contract[key] == "semantic_record"
    input_tree = ast.parse((ROOT / "pcc/backend/owned_elf_inputs.py").read_text())
    proof = one(node for node in input_tree.body if isinstance(node, ast.ClassDef)
                and node.name == "_ClassificationProof")
    expected_proof = one(ast.parse("""@dataclass(frozen=True)
class _ClassificationProof:
    sha256: str
    size: int
    section_ranges: tuple
    section_bits: tuple
""").body)
    assert dump(proof) == dump(expected_proof)
    function_names = ("_discover_self_backend_classes", "data_plane_class_contract_report")
    functions = [one(node for node in inventory_tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == name) for name in function_names]
    names = {"sorted", "set", "str", "len", "isinstance", "dict", "Counter",
             "_discover_self_backend_classes"}
    attributes = {"glob", "parse", "read_text", "add", "items", "values"}
    for function in functions:
        for node in ast.walk(function):
            assert not isinstance(node, (ast.Import, ast.ImportFrom))
            if isinstance(node, ast.Call):
                assert ((isinstance(node.func, ast.Name) and node.func.id in names)
                        or (isinstance(node.func, ast.Attribute) and node.func.attr in attributes))
    namespace = {"ast": ast, "Path": Path, "Counter": Counter,
                 "_SCRIPT_DIR": ROOT / "scripts", "DATA_PLANE_CLASS_CONTRACT": contract,
                 "_DATA_PLANE_CLASSIFICATIONS": categories}
    code = compile(ast.Module(body=functions, type_ignores=[]),
                   "<source-only record classification contract>", "exec", dont_inherit=True)
    exec(code, namespace)
    report = namespace["data_plane_class_contract_report"](ROOT / "pcc/backend")
    assert report["unclassified"] == report["stale_classifications"] == report["invalid_classifications"] == []
    assert report["discovered_class_count"] == report["classified_class_count"]
    missing = dict(contract)
    del missing[key]
    namespace["DATA_PLANE_CLASS_CONTRACT"] = missing
    missing_report = namespace["data_plane_class_contract_report"](ROOT / "pcc/backend")
    assert missing_report["unclassified"] == [key]
    assert missing_report["stale_classifications"] == missing_report["invalid_classifications"] == []
    assert missing_report["discovered_class_count"] == missing_report["classified_class_count"] + 1

    elf_tests = ast.parse((ROOT / gate.ELF_FORMAT_TEST).read_text())
    observation = next(node for node in elf_tests.body if isinstance(node, ast.FunctionDef)
                       and node.name == "test_arm_packed_width_cache_same_input_alternating_receipt")
    order = next(node for node in ast.walk(observation) if isinstance(node, ast.For)
                 and isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Name)
                 and node.iter.func.id == "enumerate")
    assert ast.literal_eval(one(order.iter.args)) == (
        "original candidate", "candidate original", "original candidate", "candidate original")
    text = ast.get_source_segment((ROOT / gate.ELF_FORMAT_TEST).read_text(), observation)
    assert 'assert output.stat().st_size <= 4096' in text
    assert '"timing_threshold": None' in text and 'kinds * 128' in text
    assert all(not isinstance(node, (ast.Import, ast.ImportFrom))
               or not any(alias.name in ("cProfile", "profile", "subprocess", "ctypes")
                          for alias in node.names) for node in ast.walk(observation))
