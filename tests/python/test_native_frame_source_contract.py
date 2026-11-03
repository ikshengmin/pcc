"""Source guards for native-root publication; not execution qualification.

Contract: docs/project-intent.md, shared five-GC slot/root contract and native
runtime ownership. These guards supplement the emitted native caller regressions.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def parsed(name):
    return ast.parse((ROOT / "pcc/runtime/py" / name).read_text())


def functions(tree):
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def named_calls(node):
    return [n.func.id for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]


def test_list_prepare_has_no_polling_shim_or_implicit_role():
    tree = parsed("py_list.py")
    assert "_prepare_moving_root" not in functions(tree)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_prepare_moving_root"]
    assert calls and all(len(n.args) == 3 for n in calls)
    entry = functions(parsed("py_list_native_entry.py"))["pcc_list_native_prepare_root"]
    order = sorted((n.lineno, n.func.id) for n in ast.walk(entry) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name))
    bind = next(line for line, name in order if name == "pcc_gc_native_root_bind_range")
    barrier = next(line for line, name in order if name == "pcc_gc_load_ptr")
    assert bind < barrier
    assert not ({"malloc", "pcc_gc_backend", "pcc_thread_safepoint"} & set(named_calls(entry)))


def test_public_native_entries_use_local_literal_layouts_and_keep_gc0_bypass():
    fs = functions(parsed("py_list_native_entry.py"))
    wrappers = {name: fn for name, fn in fs.items() if name.startswith("py_list_") or name == "pcc_list_grow_for_mutation"}
    assert len(wrappers) == 31
    required_gc0 = {"py_list_contains", "py_list_index", "py_list_index_range", "py_list_count", "py_list_remove", "py_list_pop"}
    for name, fn in wrappers.items():
        literals = {n.target.id: n.value.value for n in fn.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and isinstance(n.value, ast.Constant)}
        assert literals["native_frame_bytes"] == 40
        assert literals["native_range_capacity"] == 8
        assert literals["native_ranges_bytes"] == 128
        if name not in required_gc0:
            assert isinstance(fn.body[0], ast.If)
            calls = set(named_calls(fn.body[0]))
            assert not ({"pcc_gc_native_frame_enter", "pcc_thread_no_park_enter", "pcc_gc_native_root_bind_range"} & calls)
            assert any(c.startswith("pcc_list_body_") for c in calls)


def test_native_mutations_use_no_global_registry_or_collection_request():
    fs = functions(parsed("freestanding_thread_kernel_pthread.py"))
    mutations = ["pcc_gc_native_frame_enter", "pcc_gc_native_frame_leave", "pcc_gc_native_root_bind_range", "pcc_gc_native_root_unbind", "pcc_gc_native_root_unbind_slots"]
    prohibited = {"malloc", "free", "pcc_thread_safepoint", "pcc_py_gc_minor_graph_lock", "pcc_gc_scheduler_root_list_acquire", "pcc_gc_cycle_requested_store_release", "pcc_gc_root_registry_note_mutation_locked"}
    for name in mutations:
        assert not prohibited.intersection(named_calls(fs[name])), name
    for filename in ("py_list.py", "py_list_native_entry.py"):
        calls = set(named_calls(parsed(filename)))
        assert not {"pcc_gc_scheduler_root_register_handle", "pcc_gc_scheduler_root_unregister_handle", "pcc_gc_frame_enter", "pcc_gc_frame_leave"}.intersection(calls)


def test_generational_admission_covers_every_moving_source():
    fn = functions(parsed("freestanding_gc_generational_scheduler.py"))["pcc_gc_generational_step"]
    calls = [(n.lineno, n.func.id) for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    stop = min(line for line, name in calls if name == "pcc_stop_the_world")
    resume = max(line for line, name in calls if name == "pcc_resume_world")
    sources = {"pcc_gc_generational_promote_frame_roots", "pcc_gc_generational_promote_scheduler_roots", "pcc_gc_generational_promote_tls_exception_root", "pcc_gc_backend3_drain_remembered_owners", "pcc_gc_backend3_drain_promotion_worklist", "pcc_capi_visit_extension_module_state_roots"}
    for name in sources:
        assert any(stop < line < resume for line, candidate in calls if candidate == name), name
