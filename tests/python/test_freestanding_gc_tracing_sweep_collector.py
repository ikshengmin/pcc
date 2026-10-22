from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

from pcc.frontends.python import pipeline
from pcc.frontends.python.codegen.runtime_abi import FREESTANDING_GC_RUNTIME_GLOBALS


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "runtime"
STRICT_SOURCE = (
    RUNTIME_DIR / "py" / "freestanding_gc_tracing_sweep_collector.py"
)
MANAGED_SOURCE = RUNTIME_DIR / "py" / "py_gc_backend.py"
MAKEFILE = RUNTIME_DIR / "Makefile"

OWNED_SYMBOLS = {
    "pcc_gc_tracing_finalize_unreachable",
    "pcc_gc_tracing_has_sweep_candidate",
    "pcc_gc_tracing_recheck_reachability_after_finalizers",
    "pcc_gc_tracing_sweep_unreachable",
}
RAW_FUNCTION_IMPORTS = {
    "pcc_gc_object_is_address_pinned",
    "pcc_capi_dealloc_cext_object",
    "pcc_capi_is_cext_type_tag",
    "pcc_gc_drain_all_gray_unlocked",
    "pcc_gc_forwarding_index_find",
    "pcc_gc_free_watch_begin",
    "pcc_gc_free_watch_end",
    "pcc_gc_note_object_freeing",
    "pcc_gc_object_node_is_active",
    "pcc_gc_seed_roots",
    "pcc_gc_tracing_clear_unreachable",
    "pcc_py_gc_minor_graph_lock",
    "pcc_py_gc_minor_graph_unlock",
    "pcc_refcount_forget",
    "pcc_thread_world_stop_epoch_held",
    "py_class_dealloc",
    "py_descriptor_dealloc",
    "py_dealloc_continuation",
    "py_dealloc_coroutine",
    "py_dealloc_dict",
    "py_dealloc_exc",
    "py_dealloc_file",
    "py_dealloc_float",
    "py_dealloc_func",
    "py_dealloc_gen",
    "py_dealloc_generic",
    "py_dealloc_int",
    "py_dealloc_iter",
    "py_dealloc_list",
    "py_dealloc_memoryview",
    "py_dealloc_set",
    "py_dealloc_str",
    "py_dealloc_task",
    "py_dealloc_thread_condition",
    "py_dealloc_thread_event",
    "py_dealloc_thread_lock",
    "py_dealloc_thread_rlock",
    "py_dealloc_thread_semaphore",
    "py_dealloc_thread_thread",
    "py_dealloc_tuple",
    "py_dealloc_virtual_thread",
    "py_dealloc_vthread_channel",
    "py_dealloc_weakref",
    "py_gc_untrack",
    "py_instance_dealloc",
    "py_user_del_dispatch",
}
RAW_GLOBAL_IMPORTS = {
    "pcc_gc_backend_selected",
    "pcc_gc_object_head",
    "pcc_gc_tracing_finish_commits",
    "pcc_gc_tracing_finish_stop_epoch",
    "pcc_gc_tracing_no_candidate_commit",
    "pcc_gc_user_finalizer_runs",
}


def _exported_symbols(source: str) -> set[str]:
    return set(re.findall(r'@c_abi_export\("([^"]+)"\)', source))


def _compile_object(tmp_path: Path, emitter: str) -> Path:
    llvm_ir = tmp_path / ("freestanding_gc_tracing_sweep_" + emitter + ".ll")
    pipeline.compile_python(
        str(STRICT_SOURCE),
        str(llvm_ir),
        emit_llvm_only=True,
        libpython_mode="off",
        python_library=True,
    )
    source = llvm_ir
    if emitter == "self":
        from pcc.backend.self_backend_dispatch import emit_self_asm

        source = tmp_path / "freestanding_gc_tracing_sweep.s"
        source.write_text(
            emit_self_asm(llvm_ir.read_text(encoding="utf-8")),
            encoding="utf-8",
        )
    obj = tmp_path / ("freestanding_gc_tracing_sweep_" + emitter + ".o")
    result = subprocess.run(
        ["clang", "-c", str(source), "-o", str(obj)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return obj


def test_tracing_sweep_collector_has_one_strict_source_owner():
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    managed = MANAGED_SOURCE.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "__pcc_freestanding__ = True" in strict
    assert _exported_symbols(strict) == OWNED_SYMBOLS
    assert _exported_symbols(managed).isdisjoint(OWNED_SYMBOLS)
    assert "freestanding_gc_tracing_sweep_collector" in makefile

    for name in ("_has_sweep_candidate", "_sweep_unreachable"):
        assert f"{name} = extern(" in managed
        assert f"def {name}(" not in managed
    for name in (
        "_finish_delayed_zpage_freeing_note",
        "_finalize_unreachable",
        "_recheck_reachability_after_finalizers",
    ):
        assert f"def {name}(" not in managed


def test_tracing_sweep_preserves_pep442_and_two_pass_order():
    strict = STRICT_SOURCE.read_text(encoding="utf-8")
    sweep = strict.split(
        '@c_abi_export("pcc_gc_tracing_sweep_unreachable")', 1
    )[1]

    recheck = "pcc_gc_tracing_recheck_reachability_after_finalizers()"
    assert sweep.count(recheck) == 2
    assert sweep.index("if budget <= 0:") < sweep.index(recheck)
    assert sweep.index(recheck) < sweep.index("py_user_del_dispatch(obj)")
    assert sweep.index("py_user_del_dispatch(obj)") < sweep.rindex(recheck)
    assert sweep.rindex(recheck) < sweep.index(
        "pcc_gc_tracing_clear_unreachable(obj)"
    )
    assert sweep.index("pcc_gc_tracing_clear_unreachable(obj)") < sweep.index(
        "pcc_gc_tracing_finalize_unreachable(obj)"
    )
    assert "(flags & 16384) == 0 and pcc_gc_object_is_address_pinned(obj) == 0" in sweep
    assert "pcc_capi_is_cext_type_tag(tag) == 0" in sweep

    finalize = strict.split(
        '@c_abi_export("pcc_gc_tracing_finalize_unreachable")', 1
    )[1].split("\n@c_abi_export", 1)[0]
    assert finalize.index("store_i32(obj, 12, flags | 524288)") < finalize.index(
        "pcc_gc_note_object_freeing(obj)"
    )
    assert "backend == 4 and (flags & 65536) != 0" in finalize
    assert finalize.index("pcc_capi_dealloc_cext_object(obj, tag) == 0") < (
        finalize.index(
            'if tag >= abi_constant("object.type.user_class_start"):'
        )
    )


@pytest.mark.parametrize("emitter", ["llvm", "self"])
def test_tracing_sweep_object_has_exact_raw_closure(tmp_path: Path, emitter: str):
    obj = _compile_object(tmp_path, emitter)
    assert RAW_GLOBAL_IMPORTS <= FREESTANDING_GC_RUNTIME_GLOBALS
    undefined_result = subprocess.run(
        ["nm", "-u", str(obj)], capture_output=True, text=True, timeout=30
    )
    assert undefined_result.returncode == 0, (
        undefined_result.stdout + undefined_result.stderr
    )
    undefined = {
        line.split()[-1].lstrip("_")
        for line in undefined_result.stdout.splitlines()
        if line.strip()
    }
    assert undefined == RAW_FUNCTION_IMPORTS | RAW_GLOBAL_IMPORTS

    symbols_result = subprocess.run(
        ["nm", "-g", str(obj)], capture_output=True, text=True, timeout=30
    )
    assert symbols_result.returncode == 0, symbols_result.stdout + symbols_result.stderr
    defined = {
        line.split()[-1].lstrip("_")
        for line in symbols_result.stdout.splitlines()
        if line.strip() and " U " not in line
    }
    assert defined == OWNED_SYMBOLS


def test_production_archive_has_one_tracing_sweep_owner(
    pcc_runtime_archive: Path,
):
    symbols_result = subprocess.run(
        ["nm", "-A", "-g", str(pcc_runtime_archive)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert symbols_result.returncode == 0, symbols_result.stdout + symbols_result.stderr
    for symbol in OWNED_SYMBOLS:
        owners = [
            line
            for line in symbols_result.stdout.splitlines()
            if line.strip()
            and line.split()[-1].lstrip("_") == symbol
            and " U " not in line
        ]
        assert len(owners) == 1, (symbol, owners)
        assert ":freestanding_gc_tracing_sweep_collector.o:" in owners[0]
        assert ":py_gc_backend.o:" not in owners[0]



class _CandidateModel:
    """The tracked-object list as nodes {active, obj}; obj is its flags."""

    def __init__(self, flags):
        self.nodes = [{"active": 1, "flags": value} for value in flags]
        self.words = {"pcc_gc_tracing_finish_commits": 0,
                      "pcc_gc_tracing_no_candidate_commit": -1}
        self.visits = 0
        self.locked = 0

    def environment(self):
        model = self

        def node_at(index):
            return None if index >= len(model.nodes) else ("node", index)

        def load_ptr(base, offset):
            kind, index = base
            if offset == 16:
                return node_at(index + 1)
            assert offset == 0
            return ("obj", index)

        def load_i32(base, offset):
            assert base[0] == "obj" and offset == 12
            return model.nodes[base[1]]["flags"]

        def is_active(node):
            model.visits += 1
            return model.nodes[node[1]]["active"]

        def lock():
            model.locked += 1

        def unlock():
            assert model.locked > 0
            model.locked -= 1

        return {
            "i64": int,
            "global_load_ptr": lambda name: node_at(0),
            "global_addr": lambda name: name,
            "load_i64": lambda name, offset: model.words[name],
            "store_i64": lambda name, offset, value: model.words.__setitem__(name, value),
            "load_ptr": load_ptr,
            "load_i32": load_i32,
            "ptr_is_null": lambda value: 1 if value is None else 0,
            "pcc_gc_object_node_is_active": is_active,
            "pcc_py_gc_minor_graph_lock": lock,
            "pcc_py_gc_minor_graph_unlock": unlock,
        }


def _has_sweep_candidate(model):
    tree = ast.parse(STRICT_SOURCE.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "pcc_gc_tracing_has_sweep_candidate"
    )
    function.decorator_list = []
    namespace = model.environment()
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(STRICT_SOURCE), "exec"), namespace)
    return namespace["pcc_gc_tracing_has_sweep_candidate"]


def test_sweep_candidate_walk_is_skipped_until_the_next_finished_mark():
    # Only a finished mark sets SWEEP_CANDIDATE (1024). Schedulers ask once
    # per step, so a walk of every tracked object per question made a GC3
    # automatic collection of a 600k-object heap quadratic (12 s per
    # collection in benchmarks/python/gc_advantage_matrix.py).
    model = _CandidateModel([0] * 1000)
    has_candidate = _has_sweep_candidate(model)
    assert has_candidate() == 0
    assert model.visits == 1000
    assert has_candidate() == 0
    assert model.visits == 1000
    # The next finished mark flags a candidate and then commits.
    model.nodes[700]["flags"] = 1024
    model.words["pcc_gc_tracing_finish_commits"] = 1
    assert has_candidate() == 1
    assert model.locked == 0
    # Its sweep clears the flag; one walk re-proves the list empty.
    model.nodes[700]["flags"] = 0
    visits = model.visits
    assert has_candidate() == 0
    assert model.visits == visits + 1000
    assert has_candidate() == 0
    assert model.visits == visits + 1000 and model.locked == 0


def test_sweep_candidate_walk_racing_a_finish_does_not_record_an_empty_list():
    # A finish that commits during the walk may have flagged nodes the walk
    # already passed: the walk must not mark the new commit empty.
    model = _CandidateModel([0] * 10)
    has_candidate = _has_sweep_candidate(model)
    env_active = model.environment()["pcc_gc_object_node_is_active"]

    def finishing_walk(node):
        if node[1] == 9:
            model.nodes[0]["flags"] = 1024
            model.words["pcc_gc_tracing_finish_commits"] = 1
        return env_active(node)

    namespace_function = has_candidate.__globals__
    namespace_function["pcc_gc_object_node_is_active"] = finishing_walk
    assert has_candidate() == 0
    assert model.words["pcc_gc_tracing_no_candidate_commit"] == -1
    namespace_function["pcc_gc_object_node_is_active"] = env_active
    assert has_candidate() == 1


class _SweepModel(_CandidateModel):
    """The sweep's three passes over {active, flags} nodes; finalizer and
    recheck calls are recorded, not executed."""

    def __init__(self, flags, *, finish_epoch, held_epoch, finalizer_runs):
        super().__init__(flags)
        self.words["pcc_gc_tracing_finish_stop_epoch"] = finish_epoch
        self.words["pcc_gc_user_finalizer_runs"] = 0
        self.held_epoch = held_epoch
        self.finalizer_runs = finalizer_runs
        self.rechecks = []
        self.events = []

    def environment(self):
        env = super().environment()
        model = self

        def store_i32(base, offset, value):
            assert base[0] == "obj" and offset == 12
            model.nodes[base[1]]["flags"] = value

        def finalizer(obj):
            model.events.append(("del", obj[1]))
            if model.finalizer_runs:
                model.words["pcc_gc_user_finalizer_runs"] += 1

        def recheck():
            model.rechecks.append(len(model.events))

        env.update(
            store_i32=store_i32,
            abi_constant=lambda name: 6,
            pcc_gc_object_is_address_pinned=lambda obj: 0,
            pcc_capi_is_cext_type_tag=lambda tag: 0,
            py_user_del_dispatch=finalizer,
            pcc_gc_tracing_recheck_reachability_after_finalizers=recheck,
            pcc_gc_tracing_clear_unreachable=lambda obj: model.events.append(("clear", obj[1])),
            pcc_gc_tracing_finalize_unreachable=lambda obj: model.events.append(("free", obj[1])),
            pcc_thread_world_stop_epoch_held=lambda: model.held_epoch,
        )
        load_i32 = env["load_i32"]

        def load_i32_tag(base, offset):
            if offset == 8:
                return 11
            return load_i32(base, offset)

        env["load_i32"] = load_i32_tag
        return env


def _sweep(model):
    tree = ast.parse(STRICT_SOURCE.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "pcc_gc_tracing_sweep_unreachable"
    )
    function.decorator_list = []
    namespace = model.environment()
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(STRICT_SOURCE), "exec"), namespace)
    return namespace["pcc_gc_tracing_sweep_unreachable"]


@pytest.mark.parametrize(
    "finish_epoch, held_epoch, finalizer_runs, rechecks",
    [
        # Marked and swept in one stopped world, no finalizer ran: the
        # verdict is the mark's; no recheck walks the heap.
        (7, 7, False, 0),
        # A finalizer ran: PEP 442 recheck after PASS 0 only.
        (7, 7, True, 1),
        # The world resumed since the mark (or never stopped for it): the
        # mutator may have made a candidate reachable before PASS 0.
        (7, 8, False, 1),
        (-1, 7, False, 1),
        (7, -1, False, 1),
        (7, 8, True, 2),
    ],
)
def test_sweep_rechecks_reachability_only_when_something_could_change_it(
    finish_epoch, held_epoch, finalizer_runs, rechecks,
):
    model = _SweepModel([1024, 0, 1024], finish_epoch=finish_epoch,
                        held_epoch=held_epoch, finalizer_runs=finalizer_runs)
    assert _sweep(model)(1 << 62) == 2
    assert len(model.rechecks) == rechecks
    dels = [event for event in model.events if event[0] == "del"]
    assert dels == [("del", 0), ("del", 2)]
    if finalizer_runs and rechecks:
        # The post-finalizer recheck follows every PASS 0 finalizer.
        assert model.rechecks[-1] == 2
    assert [event for event in model.events if event[0] == "free"] == [("free", 0), ("free", 2)]
