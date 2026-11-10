"""Full-collection GC3 young liveness, not independent minor reclamation.

Production bodies run through load_functions; native rows use owned PCC.
Public dispatcher semantics are unchanged, including stopped full-heap work.
"""
from pathlib import Path
import os
import subprocess

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.runtime_body_model import load_functions
from tests.python.test_gc3_norc_collection_entry import PipelineGC3
from pcc.runtime.py import py_abi_constants as abi

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime/py"


def collect_model():
    model = PipelineGC3()
    load_functions(RUNTIME / "py_obj.py", {"_gc_tracing_collect_step", "_gc_tracing_automatic_collect"}, model.ns)
    return model


@pytest.mark.parametrize("backend", (0, 1, 2, 3, 4))
@pytest.mark.parametrize("count_free", (0, 1))
@pytest.mark.parametrize("phase", ("idle", "requested", "marking", "sweep"))
def test_full_owner_selects_no_bulk_only_for_count_free_gc3_mark(backend, count_free, phase):
    model = collect_model()
    model.store("pcc_gc_backend_selected", 0, backend)
    model.store("pcc_gc_backend4_no_refcounts", 0, count_free)
    model.store("pcc_gc_explicit_collect_active", 0, 1)
    model.store("pcc_gc_cycle_requested", 0, int(phase == "requested"))
    model.store("pcc_gc_mark_active", 0, int(phase == "marking"))
    if phase == "sweep":
        obj, _, _ = model.value()
        model.store(obj, 12, model.load(obj, 12) | 1024)
    calls = []
    model.ns["pcc_gc_step"] = lambda budget: calls.append(("public", budget)) or 7
    model.ns["pcc_gc_generational_step"] = lambda budget, bulk: calls.append(("generation", budget, bulk)) or 1
    model.ns["pcc_gc_tracing_step_cycle"] = lambda budget: calls.append(("trace", budget)) or 2
    admitted = backend == 3 and count_free and phase in ("requested", "marking")
    assert model.ns["_gc_tracing_collect_step"](16) == (3 if admitted else 7)
    assert calls == ([("generation", 16, 0), ("trace", 15)] if admitted else [("public", 16)])
    assert model.load("pcc_gc_metric_step", 0) == int(admitted)


@pytest.mark.parametrize("explicit,stopped", ((0, True), (1, False)))
def test_non_owner_falls_back_without_new_queries(explicit, stopped):
    model = collect_model()
    model.store("pcc_gc_explicit_collect_active", 0, explicit)
    model.store("pcc_gc_mark_active", 0, 1)
    model.stopped = stopped
    calls = []
    model.ns["pcc_gc_step"] = lambda budget: calls.append(budget) or 3
    assert model.ns["_gc_tracing_collect_step"](1) == 3
    assert calls == [1]


@pytest.mark.parametrize("budget", (0, 1, 16, 1024))
def test_active_major_does_not_turn_unrooted_white_young_into_gray_copies(budget):
    model = collect_model()
    objects = []
    for _ in range(20):
        obj, node, _ = model.value()
        model.ns["pcc_gc_backend3_young_link_head"](node)
        objects.append(obj)
    model.store("pcc_gc_explicit_collect_active", 0, 1)
    model.store("pcc_gc_mark_active", 0, 1)
    observed = []
    model.ns["pcc_gc_tracing_step_cycle"] = lambda remaining: observed.append((remaining, len(model.forwardings))) or 0
    assert model.ns["_gc_tracing_collect_step"](budget) == 0
    assert observed == ([(budget, 0)] if budget else [])
    assert not model.forwardings
    assert all(model.load(obj, 12) & 128 and model.load(obj, 12) & 8 for obj in objects)


@pytest.mark.parametrize("change", ("close_scope", "clear_mark", "clear_request", "request_to_mark"))
def test_trace_rechecks_callback_capable_promotion_state(change):
    model = collect_model()
    model.store("pcc_gc_explicit_collect_active", 0, 1)
    model.store("pcc_gc_cycle_requested", 0, int(change != "clear_mark"))
    model.store("pcc_gc_mark_active", 0, int(change == "clear_mark"))
    calls = []
    def promote(budget, bulk):
        assert bulk == 0
        if change == "close_scope":
            model.store("pcc_gc_explicit_collect_active", 0, 0)
        elif change == "clear_mark":
            model.store("pcc_gc_mark_active", 0, 0)
        elif change == "clear_request":
            model.store("pcc_gc_cycle_requested", 0, 0)
        else:
            model.store("pcc_gc_cycle_requested", 0, 0)
            model.store("pcc_gc_mark_active", 0, 1)
        return 1
    model.ns["pcc_gc_generational_step"] = promote
    model.ns["pcc_gc_tracing_step_cycle"] = lambda budget: calls.append(budget) or 0
    assert model.ns["_gc_tracing_collect_step"](16) == 1
    assert calls == ([15] if change == "request_to_mark" else [])


@pytest.mark.parametrize("prior", (0, 1))
@pytest.mark.parametrize("gated", (False, True))
def test_outer_oldify_suspension_spans_promotion_and_tracing(prior, gated):
    model = collect_model()
    model.ns["pcc_thread_live_count"] = lambda: 2
    model.gated = gated
    model.store("pcc_gc_explicit_collect_active", 0, 1)
    model.store("pcc_gc_cycle_requested", 0, 1)
    model.store("pcc_gc_backend3_oldify_suspended", 0, prior)
    calls = []
    def observe(*args):
        calls.append(model.load("pcc_gc_backend3_oldify_suspended", 0))
        return 1
    model.ns["pcc_gc_generational_step"] = observe
    model.ns["pcc_gc_tracing_step_cycle"] = observe
    pauses = []
    model.ns["pcc_gc_tracing_record_pause"] = lambda start, end: pauses.append((start, end))
    assert model.ns["_gc_tracing_collect_step"](16) == 2
    assert calls == [prior if gated else 1] * 2
    assert model.load("pcc_gc_backend3_oldify_suspended", 0) == prior
    assert pauses == [(0, 0)]
    assert model.load("pcc_gc_metric_step", 0) == 1


def test_remap_active_refuses_before_accounting_or_work():
    model = collect_model()
    model.store("pcc_gc_explicit_collect_active", 0, 1)
    model.store("pcc_gc_cycle_requested", 0, 1)
    model.store("pcc_gc_backend4_remap_active", 0, 1)
    model.ns["pcc_gc_generational_step"] = lambda *args: pytest.fail("promotion during remap")
    assert model.ns["_gc_tracing_collect_step"](1024) == 0
    assert model.load("pcc_gc_metric_step", 0) == 0


@pytest.mark.parametrize("budget", (1, 16, 1024))
def test_required_promotion_consumes_budget_without_new_candidate_query(budget):
    model = collect_model()
    model.store("pcc_gc_explicit_collect_active", 0, 1)
    model.store("pcc_gc_cycle_requested", 0, 1)
    model.ns["pcc_gc_generational_step"] = lambda left, bulk: left
    model.ns["pcc_gc_tracing_step_cycle"] = lambda *args: pytest.fail("exhausted trace")
    model.ns["pcc_gc_dispatch_tracing_work_pending"] = lambda: pytest.fail("added query")
    assert model.ns["_gc_tracing_collect_step"](budget) == budget


@pytest.mark.parametrize("pinned,gated", ((False, True), (True, True), (False, False)))
def test_full_owner_zero_work_finish_keeps_same_call_actual_alias_retirement(pinned, gated):
    model = collect_model()
    source, _, block = model.value(nursery=True)
    target = model.copy(source)
    model.keep_target(target)
    model.words = [source + 16] if pinned else []
    model.gated = gated
    # Enter a real final-remark boundary with no queued gray work. The real
    # complete_claimed_tracing_cycle performs remark/finish, and the actual
    # remap owner decides whether the source can retire.
    model.store("pcc_gc_mark_active", 0, 1)
    model.store("pcc_gc_tracing_cycle_epoch", 0, 1)
    model.store("pcc_gc_backend3_oldify_suspended", 0, 0)
    model.store(target, 12, model.load(target, 12) & ~56 | 32)
    model.store(source, 12, model.load(source, 12) & ~56 | 32)
    observed = []
    real_trace = model.ns["pcc_gc_tracing_step_cycle"]
    def trace(budget):
        result = real_trace(budget)
        observed.append(result)
        return result
    model.ns["pcc_gc_tracing_step_cycle"] = trace
    model.store("pcc_gc_explicit_collect_active", 0, 1)
    result = model.ns["_gc_tracing_collect_step"](1024)
    assert result >= 0
    assert observed == [0]
    assert model.load("pcc_gc_tracing_finish_commits", 0) == 1
    assert model.load("pcc_gc_mark_active", 0) == 0
    assert target in model.index
    retained = pinned or not gated
    assert (source in model.index) == retained
    assert model.load(block, 32) == int(retained)
    assert model.load("pcc_gc_explicit_collect_active", 0) == 1


def test_automatic_existing_mark_preserves_new_request_and_defers_alias_retirement():
    model = collect_model()
    source, _, block = model.value(nursery=True)
    target = model.copy(source)
    model.keep_target(target)
    model.store("pcc_gc_mark_active", 0, 1)
    model.store("pcc_gc_tracing_cycle_epoch", 0, 1)
    for obj in (source, target):
        model.store(obj, 12, model.load(obj, 12) & ~56 | 32)
    observed = []
    real_trace = model.ns["pcc_gc_tracing_step_cycle"]
    def trace(budget):
        result = real_trace(budget)
        observed.append(result)
        return result
    model.ns["pcc_gc_tracing_step_cycle"] = trace
    assert model.ns["_gc_tracing_automatic_collect"](1) == 0
    assert observed == [0]
    assert model.load("pcc_gc_tracing_finish_commits", 0) == 1
    # begin_explicit requests another cycle while the previous one is
    # active. Finish preserves that request, so neither old nor new owner
    # is allowed to retire aliases at this still-pending cut.
    assert model.load("pcc_gc_cycle_requested", 0) == 1
    assert source in model.index and target in model.index
    assert model.load(block, 32) == 1
    assert model.load("pcc_gc_explicit_collect_active", 0) == 0


@pytest.mark.parametrize("budget", (1, 16))
def test_idle_owner_preserves_public_budget_consuming_path_without_new_query(budget):
    model = collect_model()
    model.store("pcc_gc_explicit_collect_active", 0, 1)
    for _ in range(20):
        _, node, _ = model.value()
        model.ns["pcc_gc_backend3_young_link_head"](node)
    model.store("pcc_gc_tracing_no_candidate_commit", 0, -1)
    model.ns["pcc_gc_tracing_has_sweep_candidate"] = lambda: pytest.fail("new public query")
    assert model.ns["_gc_tracing_collect_step"](budget) >= budget


def test_automatic_mark2_stays_on_direct_tracing_path():
    model = collect_model()
    model.ns["_gc_tracing_collect_step"] = lambda budget: pytest.fail("mark2 owner helper")
    model.ns["pcc_gc_step"] = lambda budget: pytest.fail("mark2 public step")
    assert model.ns["_gc_tracing_automatic_collect"](2) == 0
    assert model.load("pcc_gc_tracing_finish_commits", 0) == 1


def test_explicit_owner_sweeps_pending_young_verdict_before_any_step():
    model = collect_model()
    obj, node, _ = model.value()
    model.ns["pcc_gc_backend3_young_link_head"](node)
    model.store(obj, 12, model.load(obj, 12) | 1024)
    calls = []
    # This test exercises the actual owner ordering, not a modeled sweep.
    # Its narrow sweep double only consumes the pending verdict. The native
    # string fixtures exercise retirement; callback gates are separate.
    def sweep():
        calls.append("sweep")
        assert obj not in model.forwardings
        assert model.load(obj, 12) & 1024
        model.store(obj, 12, model.load(obj, 12) & ~1024)
        model.store("pcc_gc_cycle_requested", 0, 0)
        return 1
    model.ns["pcc_gc_collect_tracing"] = sweep
    model.ns["_gc_tracing_collect_step"] = lambda budget: calls.append("step") or 0
    assert model.ns["pcc_gc_collect"](0) == 1
    assert calls == ["sweep"] + ["step"] * 4


def test_active_major_keeps_long_promotion_closure_and_unrelated_young_separate():
    model = collect_model()
    owner, _, _ = model.value(abi.PY_TYPE_LIST)
    model.store(owner, 12, 8 | 256 | 64 | 262144)
    length = 1025
    slots = model.malloc(length * 8)
    children = []
    for index in range(length):
        child, node, _ = model.value()
        model.ns["pcc_gc_backend3_young_link_head"](node)
        children.append(child)
        model.store(slots, index * 8, child)
    model.store(owner, 16, length)
    model.store(owner, 24, length)
    model.store(owner, 32, slots)
    sentinel, node, _ = model.value()
    model.ns["pcc_gc_backend3_young_link_head"](node)
    model.keep_target(owner)
    model.store("pcc_gc_explicit_collect_active", 0, 1)
    model.store("pcc_gc_mark_active", 0, 1)
    model.ns["pcc_gc_tracing_step_cycle"] = lambda budget: 0
    with model.locked():
        model.ns["pcc_gc_trace_referents_for_promotion"](owner)
    for _ in range(256):
        if not model.load("pcc_gc_backend3_promotion_head", 0):
            break
        assert 0 <= model.ns["_gc_tracing_collect_step"](16) <= 16
    assert model.load("pcc_gc_backend3_promotion_head", 0) == 0
    for index, child in enumerate(children):
        target = model.load(slots, index * 8)
        assert target != child and model.known(target)
        assert model.load(target, 12) & 256
    assert sentinel not in model.forwardings
    assert model.load(sentinel, 12) & 128


@pytest.mark.parametrize("backend", (3, 4))
@pytest.mark.parametrize("old_count", (64, 256, 1024))
def test_native_explicit_major_reclaims_white_young_in_first_cycle(tmp_path, pcc_runtime_archive,
                                                                 backend, old_count):
    source = ROOT / "tests/python/fixtures/gc3_explicit_white_young.c"
    executable = tmp_path / "young-major"
    compile_owned_c_with_runtime(source, executable, runtime_archive=pcc_runtime_archive)
    result = subprocess.run([str(executable), str(backend), str(old_count)],
                            capture_output=True, text=True, timeout=20,
                            env=dict(os.environ, PATH="/nonexistent",
                                     PCC_GC_MINOR_HEAP_SIZE="256", PCC_GC_MINOR_ALLOC_MAX="128"))
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == ""
    assert '"rooted_known_after_major":17' in result.stdout
    assert result.stdout.count("IDENTITIES retained=0") == 2




def test_automatic_new_all_live_mark_has_positive_work_and_keeps_live_forward_target():
    model = collect_model()
    source, _, block = model.value(nursery=True)
    target = model.copy(source)
    slot = model.keep_target(target)
    observed = []
    actual_trace = model.ns["pcc_gc_tracing_step_cycle"]

    def trace(budget):
        result = actual_trace(budget)
        observed.append(result)
        return result

    model.ns["pcc_gc_tracing_step_cycle"] = trace
    assert model.ns["_gc_tracing_automatic_collect"](1) == 0
    assert observed and sum(observed) > 0
    assert model.load("pcc_gc_tracing_finish_commits", 0) == 1
    assert model.load("pcc_gc_mark_active", 0) == 0
    assert model.load("pcc_gc_cycle_requested", 0) == 0
    assert model.load(slot, 0) == target and target in model.index
    # Positive-work completion never promised same-call alias retirement.
    # The unchanged next public idle step must still retire the old shell.
    assert source in model.index and model.load(block, 32) == 1
    assert model.ns["pcc_gc_step"](1024) > 0
    assert source not in model.index and target in model.index
    assert model.load(block, 32) == 0


@pytest.mark.parametrize("old_count", (64, 256, 1024))
def test_native_injected_pending_runs_actual_automatic_mark1(tmp_path, pcc_runtime_archive, old_count):
    """Controlled scheduler-state injection, not organic workload activation."""
    source = ROOT / "tests/python/fixtures/gc3_explicit_white_young.c"
    executable = tmp_path / "automatic-young-major"
    compile_owned_c_with_runtime(source, executable, runtime_archive=pcc_runtime_archive)
    result = subprocess.run([str(executable), "3", str(old_count), "injected-mark1"],
                            capture_output=True, text=True, timeout=20,
                            env=dict(os.environ, PATH="/nonexistent",
                                     PCC_GC_MINOR_HEAP_SIZE="256", PCC_GC_MINOR_ALLOC_MAX="128"))
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == ""
    assert "AUTO_MARK1 injected_pending=1 requested_before=1" in result.stdout
    assert "gates=1 commits=1 deaths=48" in result.stdout
    assert "active=0 requested=0 explicit=0 pending=0" in result.stdout
    assert result.stdout.count("IDENTITIES retained=0") == 2


def test_native_injected_automatic_all_live_preserves_forwarding_and_idle_cleanup(tmp_path, pcc_runtime_archive):
    """Actual refill/promotions, then a real positive-work automatic owner."""
    source = ROOT / "tests/python/fixtures/gc3_explicit_white_young.c"
    executable = tmp_path / "automatic-all-live"
    compile_owned_c_with_runtime(source, executable, runtime_archive=pcc_runtime_archive)
    result = subprocess.run([str(executable), "all-live"], capture_output=True, text=True, timeout=20,
                            env=dict(os.environ, PATH="/nonexistent",
                                     PCC_GC_MINOR_HEAP_SIZE="256", PCC_GC_MINOR_ALLOC_MAX="128"))
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == ""
    assert "ALL_LIVE_SETUP young=49 old=16 forwarding=16 minor_entries=1" in result.stdout
    assert "ALL_LIVE_AFTER deaths=0 young=33 old=32 forwarding=32" in result.stdout
    assert "ALL_LIVE_IDLE rounds=3 young=0 old=65 forwarding=0 stable_ids=65" in result.stdout
