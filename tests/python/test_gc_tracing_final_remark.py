"""The final count-free verdict includes roots acquired during concurrent mark."""

import ast
import os
from pathlib import Path
import subprocess

import pytest


SOURCE = (Path(__file__).resolve().parents[2] / "pcc/runtime/py"
          / "freestanding_gc_incremental_concurrent_scheduler.py")


class RemarkModel:
    def __init__(self, tracing, *, owned=False, stop_failure=False, invalidate=False):
        self.events = []
        self.owned = owned
        self.depth = 0
        self.invalidate = invalidate
        self.cells = {
            "pcc_gc_backend4_no_refcounts": int(tracing),
            "pcc_gc_tracing_finish_claim_epoch": 7,
            "pcc_gc_tracing_finish_claim_backend": 4,
            "pcc_gc_tracing_cycle_epoch": 7,
            "pcc_gc_backend_selected": 4,
            "pcc_gc_mark_active": 1,
        }
        self.ns = dict(
            i64=int,
            pcc_thread_owns_stopped_world=lambda: int(self.owned),
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
            pcc_gc_gray_conservative_thread_roots=lambda: self.root("raw"),
            pcc_gc_gray_current_roots=lambda: self.root("registered"),
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
def test_final_snapshot_roots_precede_drain_and_verdict_under_valid_world(tracing, owned):
    model = RemarkModel(tracing, owned=owned)
    assert model.complete() == 1
    expected = ["extensions"] + (["raw"] if tracing else []) + ["registered", "drain", "finish"]
    assert model.events == (expected if owned else ["stop"] + expected + ["resume"])


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
