"""The final count-free verdict includes roots acquired during concurrent mark."""

import ast
import os
from pathlib import Path
import subprocess

import pytest


SOURCE = (Path(__file__).resolve().parents[2] / "pcc/runtime/py"
          / "freestanding_gc_incremental_concurrent_scheduler.py")


class RemarkModel:
    def __init__(self, tracing, *, owned=False, stop_failure=False, invalidate=False,
                 chain_cut=False, seed_stop=None, held_stop=41):
        self.events = []
        self.owned = owned
        self.chain_cut = chain_cut
        # The stop this remark runs in, and the one the mark was seeded in
        # (by default an earlier stop: mutators ran in between).
        self.held_stop = held_stop
        self.depth = 0
        self.invalidate = invalidate
        self.cells = {
            "pcc_gc_backend4_no_refcounts": int(tracing),
            "pcc_gc_tracing_finish_claim_epoch": 7,
            "pcc_gc_tracing_finish_claim_backend": 4,
            "pcc_gc_tracing_cycle_epoch": 7,
            "pcc_gc_backend_selected": 4,
            "pcc_gc_mark_active": 1,
            "pcc_gc_tracing_seed_stop_epoch": 40 if seed_stop is None else seed_stop,
        }
        self.ns = dict(
            i64=int,
            pcc_thread_owns_stopped_world=lambda: int(self.owned),
            pcc_thread_world_stop_epoch_held=lambda: self.held_stop if self.owned else -1,
            pcc_stop_the_world=lambda: self.stop(stop_failure),
            pcc_resume_world=self.resume,
            pcc_py_gc_minor_graph_lock=lambda: self.lock(1),
            pcc_py_gc_minor_graph_unlock=lambda: self.lock(-1),
            global_addr=lambda name: name,
            global_load_ptr=lambda name: self.cells.get(name, 0),
            load_i32=lambda name, offset: self.cells.get(name, 0),
            load_i64=lambda name, offset: self.cells.get(name, 0),
            store_i32=lambda name, offset, value: self.cells.__setitem__(name, value),
            store_i64=lambda name, offset, value: self.cells.__setitem__(name, value),
            stack_alloc=lambda size: "context",
            function_addr=lambda name: name,
            ptr_is_null=lambda obj: obj == 0,
            pcc_gc_tracing_finish_claim_clear_unlocked=lambda *args: self.events.append("clear"),
            pcc_capi_visit_extension_module_state_roots=self.extensions,
            pcc_gc_gray_conservative_and_listed_pinned_roots=self.conservative,
            pcc_gc_gray_registered_roots=lambda: self.root("registered"),
            pcc_gc_gray_current_roots=lambda: self.root("listed+registered"),
            pcc_gc_trace_stack_begin=lambda: self.stack("stack_begin"),
            pcc_gc_trace_stack_drain=lambda budget: self.stack("stack_drain") or 0,
            pcc_gc_trace_stack_end=lambda: self.stack("stack_end"),
            _pcc_gc_drain_all_gray_stopped_world=lambda *args: self.events.append("drain"),
            pcc_gc_finish_tracing_cycle=self.finish,
        )
        node = next(node for node in ast.parse(SOURCE.read_text()).body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "pcc_gc_complete_claimed_tracing_cycle")
        node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), self.ns)

    def lock(self, delta):
        self.depth += delta
        assert self.depth in (0, 1)

    def stop(self, failure):
        self.events.append("stop")
        self.owned = not failure
        return int(failure)

    def resume(self):
        assert self.depth == 0
        self.owned = False
        self.events.append("resume")

    def extensions(self, callback, context):
        assert self.depth == 0 and self.owned
        self.events.append("extensions")
        if self.invalidate:
            self.cells["pcc_gc_tracing_cycle_epoch"] += 1

    def root(self, kind):
        assert self.depth == 1 and self.owned
        self.events.append(kind)

    def conservative(self):
        # The fused walk also shades listed pinned objects unless a
        # forwarding chain cut leaves them to the separate walk.
        self.root("raw" if self.chain_cut else "raw+listed")
        return 0 if self.chain_cut else 1

    def stack(self, kind):
        assert self.depth == 1 and self.owned
        self.events.append(kind)

    def finish(self, epoch, backend):
        assert self.depth == 1 and self.owned
        self.events.append("finish")
        return 1

    def complete(self):
        result = self.ns["pcc_gc_complete_claimed_tracing_cycle"](7, 4)
        assert self.depth == 0
        return result


@pytest.mark.parametrize("tracing", [False, True])
@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("chain_cut", [False, True])
def test_final_snapshot_roots_precede_drain_and_verdict_under_valid_world(tracing, owned, chain_cut):
    model = RemarkModel(tracing, owned=owned, chain_cut=chain_cut)
    assert model.complete() == 1
    if not tracing:
        roots = ["listed+registered"]
    elif chain_cut:
        roots = ["raw", "listed+registered"]
    else:
        # One walk shades conservative owners and listed pins; only the
        # registered roots remain.
        roots = ["raw+listed", "registered"]
    expected = (["extensions", "stack_begin"] + roots
                + ["stack_drain", "stack_end", "drain", "finish"])
    assert model.events == (expected if owned else ["stop"] + expected + ["resume"])


@pytest.mark.parametrize("chain_cut", [False, True])
def test_a_mark_seeded_in_this_same_stop_is_not_rescanned_for_roots(chain_cut):
    # A GC4 gate collection or an explicit collect seeds, traces and
    # remarks inside one stopped world: the parked stacks are the ones the
    # seed read, so the remark walks no object list for roots again.
    model = RemarkModel(True, owned=True, chain_cut=chain_cut, seed_stop=41, held_stop=41)
    assert model.complete() == 1
    assert model.events == [
        "extensions", "stack_begin", "stack_drain", "stack_end", "drain", "finish",
    ]


@pytest.mark.parametrize("seed_stop", [-1, 0, 40, 42])
def test_a_mark_seeded_in_another_stop_takes_the_final_root_snapshot(seed_stop):
    model = RemarkModel(True, owned=True, seed_stop=seed_stop, held_stop=41)
    assert model.complete() == 1
    assert model.events == [
        "extensions", "stack_begin", "raw+listed", "registered",
        "stack_drain", "stack_end", "drain", "finish",
    ]


def test_a_remark_that_stops_the_world_itself_always_takes_the_snapshot():
    # The stop it acquires is a new one: mutators ran since the seed, even
    # when the recorded epoch number happens to match the model's.
    model = RemarkModel(True, owned=False, seed_stop=40, held_stop=41)
    assert model.complete() == 1
    assert model.events == [
        "stop", "extensions", "stack_begin", "raw+listed", "registered",
        "stack_drain", "stack_end", "drain", "finish", "resume",
    ]


def test_a_counted_collector_keeps_its_listed_roots_in_the_same_stop():
    model = RemarkModel(False, owned=True, seed_stop=41, held_stop=41)
    assert model.complete() == 1
    assert model.events == [
        "extensions", "stack_begin", "listed+registered",
        "stack_drain", "stack_end", "drain", "finish",
    ]


def _seed(*, owned, requested=1):
    """Run the production mark seed over a dict of cells; return them."""
    from tests.python.runtime_body_model import load_functions

    cells = {
        "pcc_gc_trace_extension_roots_pending": 4,
        "pcc_gc_trace_extension_roots_epoch": 7,
        "pcc_gc_trace_extension_roots_backend": 4,
        "pcc_gc_tracing_cycle_epoch": 7,
        "pcc_gc_backend_selected": 4,
        "pcc_gc_mark_active": 0,
        "pcc_gc_cycle_requested": requested,
        "pcc_gc_tracing_seed_stop_epoch": -1,
        "pcc_gc_object_head": 0x5000,
        "pcc_gc_trace_cursor": 0,
    }
    world = {"owned": owned, "epoch": 41 if owned else 0, "events": []}

    def stop():
        world["owned"] = True
        world["epoch"] = 42
        world["events"].append("stop")
        return 0

    def resume():
        world["owned"] = False
        world["events"].append("resume")

    namespace = dict(
        i64=int,
        global_addr=lambda name: name,
        global_load_ptr=lambda name: cells[name],
        global_store_ptr=lambda name, value: cells.__setitem__(name, value),
        load_i32=lambda name, offset: cells[name],
        load_i64=lambda name, offset: cells[name],
        store_i32=lambda name, offset, value: cells.__setitem__(name, value),
        store_i64=lambda name, offset, value: cells.__setitem__(name, value),
        atomic_store_i32=lambda name, offset, value, order: cells.__setitem__(name, value),
        null=lambda: 0,
        pcc_thread_owns_stopped_world=lambda: int(world["owned"]),
        pcc_thread_world_stop_epoch_held=lambda: world["epoch"] if world["owned"] else -1,
        pcc_stop_the_world=stop,
        pcc_resume_world=resume,
        pcc_py_gc_minor_graph_lock=lambda: None,
        pcc_py_gc_minor_graph_unlock=lambda: None,
        pcc_gc_seed_roots=lambda: world["events"].append("seed"),
        pcc_gc_gray_count_load_acquire=lambda: 1,
    )
    load_functions(SOURCE, {"_pcc_gc_complete_mark_cycle_seed"}, namespace)
    result = namespace["_pcc_gc_complete_mark_cycle_seed"](7, 4)
    return result, cells, world["events"]


def test_a_seed_records_the_stopped_world_it_read_its_roots_in():
    # The caller's stop (a GC4 gate collection): the remark that follows in
    # it may skip its own root snapshot.
    result, cells, events = _seed(owned=True)
    assert result == 1 and events == ["seed"]
    assert cells["pcc_gc_mark_active"] == 1
    assert cells["pcc_gc_tracing_seed_stop_epoch"] == 41
    # A stop the seed takes and releases itself (the collector thread): any
    # later remark runs in another stop and never matches this epoch.
    result, cells, events = _seed(owned=False)
    assert result == 1 and events == ["stop", "seed", "resume"]
    assert cells["pcc_gc_tracing_seed_stop_epoch"] == 42


def test_a_seed_that_does_not_start_the_mark_records_no_stop():
    result, cells, events = _seed(owned=True, requested=0)
    assert result == 0 and events == []
    assert cells["pcc_gc_mark_active"] == 0
    assert cells["pcc_gc_tracing_seed_stop_epoch"] == -1


def test_failed_world_stop_never_scans_or_publishes_a_verdict():
    model = RemarkModel(True, stop_failure=True)
    assert model.complete() == 0
    assert model.events == ["stop", "clear"]


def test_reentrant_extension_traversal_cannot_commit_a_stale_mark():
    model = RemarkModel(True, invalidate=True)
    assert model.complete() == 0
    assert model.events == ["stop", "extensions", "clear", "resume"]


@pytest.mark.integration
def test_final_remark_rescues_a_raw_root_not_seen_by_initial_seed(tmp_path, pcc_runtime_archive):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.python.test_owned_external_tls import _c_ir

    fixture = Path(__file__).with_name("fixtures") / "gc_final_remark_raw_root.c"
    target = host_target_triple()
    helper = tmp_path / "remark.o"
    helper.write_bytes(emit_owned_object(_c_ir(fixture.read_text(), target), target))
    source = tmp_path / "remark.py"
    source.write_text('''import gc
from pcc.extern import c_int64, extern
probe = extern("remark_raw_root_probe", (), c_int64)
def main():
    gc.disable()
    print(probe())
main()
''')
    binary = tmp_path / "remark"
    compile_python(str(source), str(binary), backend="self",
                   runtime_archive=str(pcc_runtime_archive), object_inputs=(str(helper),))
    result = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=30,
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="4", PCC_GC4_TRACING_ONLY="1"),
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "0\n", "")
