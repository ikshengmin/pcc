"""PEP 442: clear weak targets, run reachable weakrefs, then finalize."""
from pathlib import Path

import pytest

from tests.python.runtime_body_model import load_functions
from tests.python.test_gc0_weakref_batch import WeakBatch


SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_weakref.py"


def test_all_targets_are_cleared_before_callbacks_and_unreachable_refs_are_silent():
    words = {}
    leases = {}
    freed = []
    callbacks = []
    released_objects = []
    cleared_errors = []
    refs = (0x2000, 0x2100, 0x2200)
    targets = (0x4000, 0x4100, 0x4000)
    for index, (ref, target) in enumerate(zip(refs, targets)):
        words[ref + 12] = 1024 if index == 2 else 0
        words[ref + 16] = target
        words[ref + 24] = 0x8000 + index
        words[ref + 40] = refs[index + 1] if index < 2 else 0
        words[target + 8] = 104
        words[target + 12] = 0
    allocated = []
    tuples = {}

    def malloc(size):
        assert size == 24
        address = 0x10000 + len(allocated) * 32
        allocated.append(address)
        return address

    def acquire(slot):
        ref = words[slot]
        assert ref != refs[2]
        token = ref + 17
        leases[slot] = token
        return token

    def release(slot, token):
        assert leases.pop(slot) == token
        return 0

    def tuple_new(size):
        assert size == 1
        address = 0x20000 + len(tuples) * 16
        tuples[address] = None
        return address

    def call(callback, args, kwargs):
        assert all(words[ref + 16] == 0 for ref in refs)
        ref = tuples[args]
        assert any(words[slot] == ref for slot in leases)
        assert callback == words[ref + 24]
        callbacks.append(ref)
        # A callback may rewrite the global weakref chain; the frozen pending
        # queue must still deliver each reachable callback once.
        words[refs[0] + 40] = 0
        return 0x30000 + len(callbacks)

    def abort():
        raise AssertionError("unexpected fatal weakref batch failure")

    namespace = {
        # Every ref has a callback: none moves to the late list.
        "global_load_ptr": lambda name: refs[0] if name == "py_weakref_head" else 0,
        # These are distinct runtime globals, not aliases of the late
        # window count; the model exercises the legacy moving path.
        "global_addr": lambda name: {
            "pcc_gc_backend_selected": 0x50008,
            "pcc_gc_nonmoving_count_free": 0x50010,
        }.get(name, 0x50000),
        "store_i32": lambda base, offset, value: words.__setitem__(base + offset, value),
        "load_ptr": lambda base, offset: words[base + offset],
        "load_i64": lambda base, offset: words[base + offset],
        "load_i32": lambda base, offset: words.get(base + offset, 0),
        "store_ptr": lambda base, offset, value: words.__setitem__(base + offset, value),
        "store_i64": lambda base, offset, value: words.__setitem__(base + offset, value),
        "ptr_is_null": lambda ptr: ptr == 0, "is_tagged_int": lambda ptr: ptr & 1,
        "ptr_add": lambda base, offset: base + offset, "null": lambda: 0,
        "pcc_gc_note_relocation_read": lambda target: target,
        "pcc_gc_tracing_is_sweep_candidate": lambda target: int(target in targets),
        "pcc_capi_is_cext_type_tag": lambda tag: 0,
        "pcc_diagnostics_runtime_log_event_code": lambda *args: None,
        "malloc": malloc, "free": freed.append,
        "stack_alloc": lambda size: 0x40000,
        "py_current_exception": lambda: 0,
        "pcc_gc_foreign_lease_acquire": acquire, "pcc_gc_foreign_lease_release": release,
        "pcc_platform_abort": abort,
        "pcc_gc_load_ptr": lambda owner, slot: words[slot],
        "py_tuple_new": tuple_new,
        "py_tuple_set_item": lambda args, index, ref: tuples.__setitem__(args, ref),
        "py_obj_call": call, "_py_none": lambda: 0,
        "py_decref": released_objects.append, "py_clear_exception": lambda: cleared_errors.append(True),
    }
    load_functions(SOURCE, {
        "_weakref_run_callback", "py_weakref_invalidate_tracing_candidates",
    }, namespace)
    assert namespace["py_weakref_invalidate_tracing_candidates"]() == 2
    assert callbacks == list(refs[:2])
    assert leases == {}
    assert freed == allocated
    assert len(released_objects) == 4
    assert len(cleared_errors) == 2


class SilentTracingClear(WeakBatch):
    """Production clear/predicate over memory, index, pin and barrier doubles."""

    def __init__(self, backend=4):
        super().__init__(backend)
        self.known = set()
        self.pinned = set()
        self.forwarded = {}
        self.ns.update(
            i64=int,
            pcc_gc_object_is_known_no_lock=lambda obj: int(obj in self.known),
            pcc_gc_object_is_address_pinned=lambda obj: int(obj in self.pinned),
            pcc_gc_note_relocation_read=lambda obj: self.forwarded.get(obj, obj),
            pcc_capi_is_cext_type_tag=lambda tag: int(tag == 900),
        )
        load_functions(SOURCE.with_name("freestanding_gc_sweep_slots.py"), {
            "pcc_gc_tracing_is_sweep_candidate",
        }, self.ns)
        load_functions(SOURCE, {"py_weakref_clear_tracing_candidates"}, self.ns)

    def target(self, flags=1024, tag=104):
        obj = self.allocate_object(64, tag, flags)
        self.known.add(obj)
        return obj

    def clear(self):
        return self.ns["py_weakref_clear_tracing_candidates"]()

    def late_ref(self, target, callback):
        """A ref an open weak window holds: the late clear's only input."""
        self.words[0xE000] = 1
        return self.ref(target, callback, late=True)

    def forbid_callback_work(self):
        def unexpected(*args):
            pytest.fail("silent clear allocated, retained, leased or called user code")
        for name in (
            "malloc", "free", "py_incref", "py_decref", "py_obj_call",
            "pcc_gc_foreign_lease_acquire", "pcc_gc_foreign_lease_release",
            "_weakref_run_callback",
        ):
            self.ns[name] = unexpected


def test_gc4_sweep_keeps_pending_exception_separate_from_queued_weakref():
    """One count-free sweep, from candidate selection through the next reseed.

    Execute the sweep coordinator, weakref queue, TLS exchange and error clear.
    Root and lease providers are modeled; the unrelated sweep passes and
    object walk are empty. This checks ownership, not native stack discovery.
    """
    from tests.python.test_exception_tls_swap_container_mode import Thread

    batch = SilentTracingClear(backend=4)
    thread = Thread(container_mode=1, exit_unregisters=0)
    thread.roots = batch.words  # Both runtime modules operate on the same cells.
    tls = thread.namespace()
    load_functions(SOURCE.with_name("py_substrate.py"),
                   {"py_tls_exc_set", "py_tls_exc_get"}, tls)
    outer = ValueError("original", 41)
    inner = LookupError("discard callback error")
    covered, leased, disposed = set(), {}, []

    def covers(slot):
        covered.add(slot)
        return 1  # This case assumes the existing full-stack provider covers it.

    def acquire(slot):
        leased[slot] = batch.load(slot)
        return 1

    def release(slot, token):
        assert token == 1 and leased.pop(slot) == wr
        return 0

    def clear_owner(slot, empty):
        assert empty == 0
        disposed.append(batch.load(slot))
        batch.store(slot, 0, empty)

    ns = batch.ns
    ns.update(py_tls_exc_get=tls["py_tls_exc_get"],
              py_tls_exc_swap_slot=tls["py_tls_exc_swap_slot"],
              pcc_gc_stack_slot_root_is_conservative=covers,
              pcc_gc_store_root=clear_owner,
              pcc_gc_foreign_lease_acquire=acquire, pcc_gc_foreign_lease_release=release,
              _type_of=type)
    load_functions(SOURCE.with_name("py_exc_tls.py"),
                   {"py_current_exception", "py_clear_exception"}, ns)
    load_functions(SOURCE, {"py_weakref_queue_tracing_candidates",
                            "py_weakref_run_queued_callbacks"}, ns)

    def callback(ref):
        assert ref == wr and ns["py_weakref_call"](ref) == batch.none
        assert ns["py_current_exception"]() == 0 and thread.pending == 0
        assert list(leased.values()) == [wr]  # The queue does not own the exception.
        assert any(batch.load(slot) is outer for slot in covered)
        # The exception is private now, but neither guarded reentry starts a
        # nested collection. The actual refresh is after this callback.
        assert entry["pcc_gc_collect"](0) == 0
        assert entry["_gc_tracing_automatic_collect"](0) == 0
        raise inner

    def call(func, args, kwargs):
        try:
            return batch.call(func, args, kwargs)
        except LookupError as error:
            tls["py_tls_exc_set"](error)
            return 0  # A raised callback returns NULL with its own pending TLS.

    wr = batch.ref(batch.target(), callback)
    batch.callbacks[callback] = callback
    ns["py_obj_call"] = call
    tls["py_tls_exc_set"](outer)
    # The mark finished in this same stop. There is no pre-sweep recheck; the
    # one reseed below is requested by the coordinator because a weakref ran.
    state = dict(pcc_gc_backend_selected=4, pcc_gc_explicit_collect_active=1,
                 pcc_gc_backend4_no_refcounts=1, pcc_gc_tracing_finish_stop_epoch=7,
                 pcc_gc_user_finalizer_runs=0)
    reseeds = []

    def reseed():
        assert batch.calls == [wr] and not leased
        assert all(batch.load(slot) == 0 for slot in covered)
        reseeds.append([thread.tls[slot] for slot in thread.registered])

    def word(cell, offset):
        return state[cell] if isinstance(cell, str) else batch.load(cell, offset)

    def release_world():
        previous, batch.stopped = batch.stopped, False
        return int(previous)

    def reacquire_world(depth):
        batch.stopped = batch.stopped or bool(depth)

    entry = dict(global_addr=lambda name: name, load_i32=word, _gc_backend_fast=lambda: 4)
    load_functions(SOURCE.with_name("py_obj.py"),
                   {"pcc_gc_collect", "_gc_tracing_automatic_collect"}, entry)
    sweep = dict(ns, global_addr=lambda name: name, load_i32=word, load_i64=word,
                 store_i32=lambda name, offset, value: state.__setitem__(name, value),
                 pcc_thread_world_stop_epoch_held=lambda: 7,
                 pcc_thread_world_release_held=release_world,
                 pcc_thread_world_reacquire_held=reacquire_world,
                 pcc_gc_seed_roots=reseed, pcc_gc_object_walk_first=lambda: 0,
                 pcc_gc_drain_all_gray_unlocked=lambda: 0, pcc_platform_monotonic_us=lambda: 0,
                 _record_sweep_phase=lambda *args: None, _sweep_finalizers=lambda *args: None,
                 _sweep_clear_and_reclaim=lambda *args: 0)
    load_functions(SOURCE.with_name("freestanding_gc_tracing_sweep_collector.py"),
                   {"pcc_gc_tracing_sweep_unreachable"}, sweep)
    assert sweep["pcc_gc_tracing_sweep_unreachable"](1) == 0
    assert reseeds == [[outer]] and disposed == [inner] and batch.windows == 0
    restored = ns["py_current_exception"]()
    assert restored is outer and restored.args == ("original", 41)
    assert thread.pending == 1


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
def test_post_finalizer_clear_never_queues_callbacks_even_for_live_refs(backend):
    model = SilentTracingClear(backend)
    target = model.target()
    refs = [model.late_ref(target, callback) for callback in (0, 0x8000, 0x8100, 0x8200)]
    model.store(refs[2], 12, 1024)
    model.store(refs[3], 12, 524288)
    before = dict(model.refcounts)
    model.forbid_callback_work()
    assert model.clear() == 4
    assert all(model.load(ref, 16) == 0 for ref in refs)
    assert model.late == 0 and model.windows == 0
    assert model.clear() == 0
    assert model.calls == [] and model.refcounts == before


@pytest.mark.parametrize("exclusion", (
    "reachable", "pinned", "fresh", "cext", "unknown", "tagged", "null",
))
def test_silent_clear_preserves_existing_target_qualification(exclusion):
    model = SilentTracingClear()
    target = model.target()
    if exclusion == "reachable":
        model.store(target, 12, 0)
    elif exclusion == "pinned":
        model.pinned.add(target)
    elif exclusion == "fresh":
        # The fresh-allocation publication bit is not a cleared-object bit.
        model.store(target, 12, 1024 | 16384)
    elif exclusion == "cext":
        model.store(target, 8, 900)
    elif exclusion == "unknown":
        model.known.remove(target)
    elif exclusion == "tagged":
        target = 3
    else:
        assert exclusion == "null"
        target = 0
    ref = model.late_ref(target, 0x8000)
    model.forbid_callback_work()
    assert model.clear() == 0
    assert model.load(ref, 16) == target


@pytest.mark.parametrize("candidate", (False, True))
def test_silent_clear_qualifies_the_relocation_resolved_target(candidate):
    model = SilentTracingClear()
    source, target = model.target(), model.target(1024 if candidate else 0)
    model.forwarded[source] = target
    ref = model.late_ref(source, 0x8000)
    model.forbid_callback_work()
    assert model.clear() == int(candidate)
    assert model.load(ref, 16) == (0 if candidate else source)


def test_silent_tracing_clear_rejects_running_world_before_mutation():
    model = SilentTracingClear()
    target = model.target()
    ref = model.late_ref(target, 0x8000)
    model.stopped = False
    with pytest.raises(RuntimeError, match="abort"):
        model.clear()
    assert model.load(ref, 16) == target


def test_silent_tracing_clear_does_not_handle_gc0():
    model = SilentTracingClear(0)
    target = model.target()
    ref = model.late_ref(target, 0x8000)
    model.stopped = False
    assert model.clear() == 0
    assert model.load(ref, 16) == target
