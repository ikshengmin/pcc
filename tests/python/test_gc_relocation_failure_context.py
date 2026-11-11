"""Host contracts for failure-only GC4 probe diagnostics.

Owned C lowering checks the complete probe, but does not link or run a runtime.
Synthetic failing runner results test receipt plumbing, not relocation success.
The admitted-runtime integration probe remains the positive native oracle.
"""

import ast
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from tests.python import test_gc_relocation_sweep_verdict as probe


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime/py"


def _diagnostic():
    return probe.PROBE_SOURCE.split("static int fail_relocation_state(", 1)[1].split(
        "\nint main(void)", 1
    )[0]


def test_diagnostics_are_only_on_original_selection_and_copy_failures():
    main = probe.PROBE_SOURCE.split("int main(void)", 1)[1]
    assert main.count("fail_relocation_state(") == 2
    assert main.count("pcc_gc_select_relocation_set(") == 1
    assert main.count("instance_payload_size(") == 1
    assert main.count("pcc_gc_relocate_copy(") == 1
    assert '''    if (pcc_gc_select_relocation_set(1) != 1)
        /* Size was not requested on this path; -1 means not evaluated. */
        return fail_relocation_state(13, "select", source, 0, -1);
    int64_t requested_size = instance_payload_size(source);
    PyObject *copy = pcc_gc_relocate_copy(
        source, requested_size
    );''' in main
    assert '''    if (copy == 0 || copy == source)
        return fail_relocation_state(14, "relocate", source, copy, requested_size);
    watched_copy = copy;''' in main
    assert "pcc_gc_alloc_stage_pending" not in main
    assert "pcc_gc_object_index_find" not in main
    assert "pcc_py_gc_minor_graph_lock" not in main
    assert "pcc_gc_object_is_address_pinned" not in main
    assert "pcc_gc_relocation_set_add" not in probe.PROBE_SOURCE
    assert "pcc_gc_unpin" not in probe.PROBE_SOURCE


def test_original_movement_verdict_lifetime_and_exactly_once_checks_remain():
    main = probe.PROBE_SOURCE.split("int main(void)", 1)[1]
    expected = [
        "if (copy != 0)", "py_decref(copy);", "if (copy == 0 || copy == source)",
        "watched_copy = copy;",
        "if ((flags_of(copy) & PY_FLAG_GC_SWEEP_CANDIDATE) != 0)",
        'return fail_state(20, "destination-inherits-verdict", source, copy);',
        "&& (flags_of(source) & PY_FLAG_GC_SWEEP_CANDIDATE) != 0",
        'return fail_state(21, "shell-keeps-verdict", source, copy);',
        "(void)pcc_gc_collect(0);", "if (del_other != 0)",
        'return fail_state(27, "finalizer-ran-on-forwarding-shell", 0, 0);',
        "if ((flags_of(copy) & PY_FLAG_GC_SWEEP_CANDIDATE) != 0)",
        'return fail_state(22, "collect-recut-live-copy", source, copy);',
        "if (del_on_copy != 0)",
        'return fail_state(23, "finalizer-ran-on-live-copy", source, copy);',
        "if (pcc_gc_object_is_known_no_lock(copy) != 1)",
        'return fail_state(24, "live-copy-unknown-after-collect", source, copy);',
        "pcc_gc_scheduler_root_unregister_handle(root_handle);",
        "pcc_gc_store_root(&root_slot, 0);", "root_slot = 0;",
        "for (int i = 0; i < 16 && del_on_copy == 0; i++)",
        "if (backend == PCC_GC_KIND_COLORED_RELOCATING)",
        "if (del_on_copy != 1)",
        'return fail_state(25, "exactly-once-violated", source, copy);',
        "else if (pcc_gc_object_is_known_no_lock(copy) != 0)",
        'return fail_state(26, "dead-copy-still-known", source, copy);',
        "if (del_other != 0)",
        'return fail_state(27, "finalizer-ran-on-forwarding-shell", 0, 0);',
        '"ok backend=%d del_other=%ld\\n"', "return 0;",
    ]
    cursor = 0
    for fragment in expected:
        cursor = main.index(fragment, cursor) + len(fragment)


def test_initial_snapshot_is_locked_stable_and_printed_before_mutating_observation():
    body = _diagnostic()
    first, secondary = body.split("    /* This secondary predicate", 1)
    calls = re.findall(r"\b([a-z_]\w*)\s*\(", re.sub(r"/\*.*?\*/", "", first, flags=re.S))
    assert calls == [
        "fail_state", "pcc_py_gc_minor_graph_lock", "pcc_gc_alloc_stage_pending",
        "pcc_gc_relocation_set_contains", "pcc_gc_relocation_set_size",
        "pcc_gc_object_index_find_stable", "pcc_gc_object_node_freeing",
        "pcc_gc_object_node_size", "pcc_gc_forwarding_find",
        "pcc_gc_forwarding_target_exists", "flags_of", "py_header",
        "pcc_gc_alloc_stage_pending", "pcc_py_gc_minor_graph_unlock", "fprintf",
    ]
    assert "node ? pcc_gc_object_node_freeing(node) : -1" in first
    assert "node ? pcc_gc_object_node_size(node) : 0" in first
    assert '"relocation_context=post-failure-stable-index code=%d "' in first
    assert "NOT the first rejected copy phase" in first
    assert "requested_size=%lld pending_after=%lld" in first
    assert "pcc_gc_object_known_size" not in body
    assert "pcc_gc_object_index_find(" not in body
    assert "pcc_gc_relocate_copy(" not in body
    assert "pcc_gc_select_relocation_set(" not in body
    assert "pcc_gc_relocation_member_count" not in body
    assert secondary.index("pcc_py_gc_minor_graph_lock()") < secondary.index(
        "pin_pending_before = pcc_gc_alloc_stage_pending()"
    ) < secondary.index("pcc_gc_object_is_address_pinned(source)") < secondary.index(
        "pcc_gc_object_index_find_stable(source)"
    ) < secondary.index("pin_pending_after = pcc_gc_alloc_stage_pending()") < secondary.index(
        "pcc_py_gc_minor_graph_unlock()"
    ) < secondary.index("fprintf(")
    assert "post-failure-secondary-may-drain" in secondary
    assert secondary.rstrip().endswith("return result;\n}")


@pytest.mark.parametrize(("module", "symbol", "arguments", "returns"), [
    ("freestanding_gc_alloc_stage", "pcc_gc_alloc_stage_pending", [], "i64"),
    ("freestanding_gc_object_nodes", "pcc_gc_object_node_freeing", ["c_ptr"], "i64"),
    ("freestanding_gc_object_nodes", "pcc_gc_object_node_size", ["c_ptr"], "i64"),
    ("freestanding_gc_forwarding_identity", "pcc_gc_forwarding_find", ["c_ptr"], "c_ptr"),
    ("freestanding_gc_forwarding_identity", "pcc_gc_forwarding_target_exists", ["c_ptr"], "i64"),
    ("freestanding_gc_root_operations", "pcc_gc_object_is_address_pinned", [None], "i64"),
    ("freestanding_gc_index_table", "pcc_gc_object_index_find_stable", ["c_ptr"], "c_ptr"),
    ("freestanding_runtime_high_substrate", "pcc_py_gc_minor_graph_lock", [], "None"),
    ("freestanding_runtime_high_substrate", "pcc_py_gc_minor_graph_unlock", [], "None"),
    ("py_gc_backend", "pcc_gc_relocation_set_contains", [None], "int"),
    ("py_gc_backend", "pcc_gc_relocation_set_size", [], "int"),
])
def test_snapshot_calls_use_existing_production_c_abi(module, symbol, arguments, returns):
    tree = ast.parse((RUNTIME / (module + ".py")).read_text())
    exported = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                and any(isinstance(mark, ast.Call)
                        and isinstance(mark.func, ast.Name) and mark.func.id == "c_abi_export"
                        and mark.args and ast.literal_eval(mark.args[0]) == symbol
                        for mark in node.decorator_list)]
    assert len(exported) == 1
    function = exported[0]
    assert [ast.unparse(arg.annotation) if arg.annotation else None
            for arg in function.args.args] == arguments
    assert ast.unparse(function.returns) == returns
    internal = (ROOT / "pcc/runtime/src/py_internal.h").read_text()
    public = (ROOT / "pcc/runtime/include/py_runtime.h").read_text()
    declarations = {
        "pcc_gc_alloc_stage_pending": "int64_t pcc_gc_alloc_stage_pending(void);",
        "pcc_gc_object_node_freeing": "int64_t pcc_gc_object_node_freeing(void *);",
        "pcc_gc_object_node_size": "int64_t pcc_gc_object_node_size(void *);",
        "pcc_gc_forwarding_find": "void *pcc_gc_forwarding_find(PyObject *);",
        "pcc_gc_forwarding_target_exists": "int64_t pcc_gc_forwarding_target_exists(PyObject *);",
        "pcc_gc_object_is_address_pinned": "int64_t pcc_gc_object_is_address_pinned(PyObject *);",
        "pcc_gc_object_index_find_stable": "void *pcc_gc_object_index_find_stable(PyObject *obj);",
        "pcc_py_gc_minor_graph_lock": "void pcc_py_gc_minor_graph_lock(void);",
        "pcc_py_gc_minor_graph_unlock": "void pcc_py_gc_minor_graph_unlock(void);",
        "pcc_gc_relocation_set_contains": "int64_t pcc_gc_relocation_set_contains(PyObject *o);",
        "pcc_gc_relocation_set_size": "int64_t pcc_gc_relocation_set_size(void);",
    }
    assert declarations[symbol] in " ".join((probe.PROBE_SOURCE + internal + public).split())


@pytest.mark.parametrize("target", ["x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu"])
def test_complete_probe_lowers_with_owned_headers_without_linking(tmp_path, monkeypatch, target):
    from pcc.backend.self_backend_parse import decode_llvm_c_string
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator, TranslationUnit

    def forbidden(*args, **kwargs):
        raise AssertionError("host diagnostic contract must not build/link/run a runtime or use a tool")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr(probe, "admitted_runtime", forbidden)
    monkeypatch.setattr(CEvaluator, "emit_executable", forbidden)
    source = probe.PROBE_SOURCE.replace("@BACKEND@", "4").replace(
        "@EXPECTED_THREADS@", "1"
    ).replace("@EXPECTED_REFCOUNT_STRATEGY@", "PCC_REFCOUNT_STRATEGY_ATOMIC")
    evaluator = CEvaluator(backend="self", target_triple=target)
    units = evaluator.compile_translation_units(
        [TranslationUnit("probe.c", str(tmp_path / "probe.c"), source)],
        use_system_cpp=False, use_compile_cache=False,
        include_dirs=[str(ROOT / "pcc/runtime/include"), str(ROOT / "pcc/runtime/src"),
                      str(ROOT / "utils/fake_libc_include")],
    )
    assert len(units) == 1
    ir = units[0][1]
    (tmp_path / "probe.ll").write_text(ir)
    for symbol in ("fail_relocation_state", "pcc_gc_alloc_stage_pending",
                   "pcc_gc_object_index_find_stable", "pcc_gc_object_is_address_pinned"):
        assert symbol in ir
    literals = [decode_llvm_c_string(token) for token in re.findall(r'c"[^"\n]*"', ir)]
    assert (
        b"relocation_context=post-failure-stable-index code=%d "
        b"pending_before=%lld selected=%lld selection_size=%lld "
        b"published_node=%d node_freeing=%lld published_node_size=%lld "
        b"forwarding_record=%p source_is_forwarding_target=%lld "
        b"src_flags=0x%x header_pinned=%d src_tag=%d "
        b"requested_size=%lld pending_after=%lld\n\0"
    ) in literals
    assert (
        b"relocation_context=post-failure-secondary-may-drain code=%d "
        b"pending_before=%lld address_pinned=%lld published_node=%d "
        b"pending_after=%lld\n\0"
    ) in literals


@pytest.mark.parametrize("code", [13, 14])
def test_failure_receipt_preserves_code_stderr_source_and_native_marker(tmp_path, monkeypatch, code):
    from scripts import ci_worker_size_prior_gate as gate

    archive = tmp_path / "host-fixture-not-a-runtime.a"
    archive.write_bytes(b"synthetic failed-run fixture, not native execution")
    Path(str(archive) + ".capi_syms").write_text("synthetic symbol inventory")
    Path(str(archive) + ".provenance.json").write_text(json.dumps({
        "target_triple": "host-fixture-only",
        "members": [{"runtime_build_config": {"threads": True, "refcount": "atomic"}}],
    }))
    monkeypatch.setattr(probe, "admitted_runtime", lambda: archive)
    linked = []

    def capture_link(source, executable, **kwargs):
        linked.append(source)
        executable.write_bytes(b"synthetic non-executable output")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    stderr = (f"code={code} what=synthetic-failure src_flags=0x40400\n"
              f"relocation_context=post-failure-stable-index code={code} pending_before=1\n"
              f"relocation_context=post-failure-secondary-may-drain code={code} pending_after=0\n")
    observed = []

    def failed_run(command, **kwargs):
        observed.append((command, kwargs))
        return SimpleNamespace(returncode=code, stdout="", stderr=stderr)

    monkeypatch.setattr(probe, "link_c_harness", capture_link)
    monkeypatch.setattr(probe, "run_process_group_timeout", failed_run)
    with pytest.raises(AssertionError) as raised:
        probe.test_relocation_copy_clears_stale_sweep_verdict_backend4_single_mutator(tmp_path)
    # Pytest's assertion rewriting indents message continuation lines and adds
    # an expression explanation. The machine-readable receipt stays exact.
    for line in stderr.splitlines():
        assert line in str(raised.value)
    assert f"probe returned {code}:" in str(raised.value)
    assert len(linked) == len(observed) == 1
    source = linked[0]
    receipt = json.loads(source.with_suffix(".run.json").read_text())
    assert receipt["returncode"] == code and receipt["stderr"] == stderr
    assert receipt["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert receipt["compiler"] == "host-pcc0" and receipt["backend"] == "self"
    assert receipt["runtime_provisioning"] is False
    assert observed[0][1]["timeout"] == 120
    assert "PCC_REFCOUNT_STRATEGY" not in observed[0][1]["env"]
    function = probe.test_relocation_copy_clears_stale_sweep_verdict_backend4_single_mutator
    assert [mark.name for mark in function.pytestmark] == ["integration"]
    node = "tests/python/test_gc_relocation_sweep_verdict.py::" + function.__name__
    command = gate.pytest_command(tmp_path, [node], integration=True)
    assert command[-3:] == ["-m", "integration", node]
    assert "-x" in command
    assert not any(arg in ("-n", "-n0", "--dist", "-o", "--override-ini") for arg in command)
