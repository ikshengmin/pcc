"""Finite configuration and gate admission for runtime-body consumer tests.

The real configuration, first-allocation freeze and thread-gate bodies run.
Environment parsing, locks and thread records are explicit finite providers.
This does not run a collector, scan machine registers, copy an object, remap
the heap, or establish native stop-the-world scheduling.
"""
from pathlib import Path

from tests.python.runtime_body_model import gc_state_initial_values, load_functions


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"


class CollectorConfiguration:
    def __init__(self, backend, requested_count_free=False, provenance=2):
        assert backend in range(5) and provenance in (0, 2)
        self.requested_count_free = requested_count_free
        self.cells = dict(gc_state_initial_values())
        self.cells["pcc_gc_backend4_no_refcounts"] = int(requested_count_free)
        self.events = []
        self.lock_depth = 0
        environment = {"PCC_GC_BACKEND": str(backend),
                       "PCC_GC_REFCOUNT_PROVENANCE_PROBE": str(provenance)}
        ns = {
            "i64": int, "global_addr": lambda name: name,
            "load_i32": self.load, "store_i32": self.store,
            "load_i64": self.load, "store_i64": self.store,
            "atomic_load_i32": lambda name, offset, order: self.load(name, offset),
            "atomic_store_i32": lambda name, offset, value, order: self.store(name, offset, value),
            "global_load_ptr": lambda name: self.cells.get(name, 0),
            "ptr_is_null": lambda value: int(value == 0),
            "cstr": lambda text: text,
            "pcc_platform_getenv": lambda name: environment.get(name),
            "_pcc_gc_config_parse_backend": lambda value, default: int(value),
            "pcc_gc_config_parse_env_i32": lambda value, default, low, high: default if value is None else int(value),
            "pcc_thread_enable_conservative_roots": lambda: self.events.append("conservative-roots"),
            "pcc_threads_enabled": lambda: 1,
            "pcc_gc_maybe_start_cms_worker": lambda: self.events.append("worker-request"),
            "pcc_py_gc_minor_graph_lock": self.lock,
            "pcc_py_gc_minor_graph_unlock": self.unlock,
            "_object_graph_lock": self.lock, "_object_graph_unlock": self.unlock,
            "pcc_gc_alloc_stage_enable": lambda enabled: self.events.append(("alloc-stage", enabled)),
            "pcc_gc_tracking_enter_flag_mode": lambda: self.events.append("flag-mode"),
        }
        load_functions(RUNTIME / "freestanding_gc_forwarding_identity.py",
                       {"pcc_gc_refresh_read_barrier"}, ns)
        load_functions(RUNTIME / "freestanding_gc_public_collection.py",
                       {"pcc_gc_config_ensure"}, ns)
        ns["_init_config"] = ns["pcc_gc_config_ensure"]
        load_functions(RUNTIME / "py_gc_backend.py",
                       {"_nonmoving_count_free_backend", "pcc_gc_fix_backend"}, ns)
        assert ns["pcc_gc_config_ensure"]() == backend
        ns["pcc_gc_fix_backend"]()
        self.assert_frozen(backend, provenance)

    def load(self, name, offset):
        assert offset == 0
        return self.cells.get(name, 0)

    def store(self, name, offset, value):
        assert offset == 0
        self.cells[name] = value

    def lock(self):
        self.lock_depth += 1

    def unlock(self):
        assert self.lock_depth > 0
        self.lock_depth -= 1

    def assert_frozen(self, backend, provenance):
        assert self.cells["pcc_gc_backend_selected"] == backend
        assert self.cells["pcc_gc_config_initialized"] == 1
        assert self.cells["pcc_gc_backend_frozen"] == 1
        assert self.cells["pcc_gc_backend4_no_refcounts"] == int(backend != 0), "collector ownership was not normalized"
        assert self.cells.get("pcc_gc_nonmoving_count_free", 0) == int(backend != 0)
        assert self.cells["pcc_gc_refcount_provenance_probe"] == provenance
        assert self.cells.get("pcc_gc_list_container_mode", 0) == int(provenance == 0)
        assert self.lock_depth == 0


class ThreadGate:
    """Two registered records; the caller owns a finite stopped-world epoch.

    The other record is the consumer. Its ordinary park must not become an
    application gate merely because the collector can see it stopped.
    """
    def __init__(self):
        self.collector, self.mutator = 1, 2
        self.words = {(1, 0): 2, (2, 0): 0, (2, 16): 0, (2, 40): 0}
        self.tls = self.mutator
        self.mutex_depth = 0
        self.stopped = False
        self.observations = []
        ns = {
            "global_load_ptr": self.global_pointer,
            "ptr_is_null": lambda value: int(value == 0),
            "ptr_to_int": int,
            "load_i64": lambda pointer, offset: self.words.get((pointer, offset), 0),
            "load_ptr": lambda pointer, offset: self.words.get((pointer, offset), 0),
            "store_i64": lambda pointer, offset, value: self.words.__setitem__((pointer, offset), value),
            "_world_init": lambda: 0,
            "pthread_mutex_lock": self.lock,
            "pthread_mutex_unlock": self.unlock,
            "pcc_thread_safepoint": self.poll,
        }
        load_functions(RUNTIME / "freestanding_thread_kernel_pthread.py",
                       {"pcc_thread_safepoint_gate", "pcc_thread_all_stopped_at_gates"}, ns)
        self.namespace = ns

    def global_pointer(self, name):
        return {"pcc_tls_thread_record_py": self.tls,
                "pcc_thread_records_head_py": self.collector,
                "pcc_world_lock_py": 3}[name]

    def lock(self, pointer):
        assert pointer == 3 and self.stopped
        self.mutex_depth += 1
        return 0

    def unlock(self, pointer):
        assert pointer == 3 and self.mutex_depth == 1
        self.mutex_depth -= 1
        return 0

    def poll(self):
        assert self.tls == self.mutator and not self.stopped
        self.words[self.mutator, 16] = 1
        self.tls, self.stopped = self.collector, True
        try:
            admitted = self.namespace["pcc_thread_all_stopped_at_gates"]()
            self.observations.append((self.words[self.mutator, 40], admitted))
        finally:
            self.tls, self.stopped = self.mutator, False
            self.words[self.mutator, 16] = 0

    def park(self, application_gate=False):
        assert self.words[self.mutator, 40] == 0
        before = len(self.observations)
        if application_gate:
            self.namespace["pcc_thread_safepoint_gate"]()
        else:
            self.poll()
        assert len(self.observations) == before + 1
        assert self.words[self.mutator, 40] == 0 and self.mutex_depth == 0
        return self.observations[-1][1]


class ConfiguredConsumer:
    """Mixin preserving the existing operation and stale/owner oracles.

    Existing _Object.references is a logical ownership ledger, not the
    count-free production header's refcount. This first batch retains that
    oracle and proves configuration/admission only, not tracing reclamation.
    """
    def configure_consumer(self):
        self.configuration = CollectorConfiguration(
            self.backend, requested_count_free=self.count_free, provenance=2)
        for name, value in self.configuration.cells.items():
            self.global_cell(name).fields[0] = value
        self.count_free = bool(self.configuration.cells["pcc_gc_backend4_no_refcounts"])
        self.thread_gate = ThreadGate()
        # Exercise both sides with the real gate body; denial cannot pass
        # because a predicate double always returns false.
        assert self.thread_gate.park(application_gate=False) == 0
        assert self.thread_gate.park(application_gate=True) == 1
        self.ordinary_parks = []
        self.load_private_field_initialization()

    def load_private_field_initialization(self):
        # nonmoving_count_free also admits _list_get_gc0. Provenance=2
        # selects its graph lock, but does not disable this first-store path.
        self.init_copy_events, self.gray_worklist = [], []
        pointer_word = self.namespace["atomic_load_i64"]

        def integer_or_pointer_word(base, offset, order):
            value = self.read(base, offset)
            # The original provider represents object/payload pointers by
            # _FieldWord. GC counters and headers are actual integer words.
            return value if isinstance(value, int) else pointer_word(base, offset, order)

        def store_integer(base, offset, value):
            owner, start = self.pointer(base)
            if (any(owner is obj for obj in self.objects)
                    and start + offset == self.namespace["PYOBJECTHEADER_REFCOUNT_OFFSET"]):
                assert owner.alive
                owner.references = value
            else:
                self.write(base, offset, value)

        def compare_exchange(base, offset, expected, desired, success, failure):
            old = self.read(base, offset)
            assert isinstance(old, int)
            if old == expected:
                store_integer(base, offset, desired)
            return old

        def add_word(operation, base, offset, increment, order):
            assert operation == "add"
            old = self.read(base, offset)
            assert isinstance(old, int)
            store_integer(base, offset, old + increment)
            return old

        self.namespace.update({
            "i64": int,
            "atomic_load_i64": integer_or_pointer_word,
            "atomic_load_i32": lambda base, offset, order: self.read(base, offset),
            "atomic_cas_i64": compare_exchange,
            "atomic_cas_i32": compare_exchange,
            "atomic_rmw_i32": add_word,
            "ptr_eq": lambda first, second: int(self.pointer(first) == self.pointer(second)),
            # Finite exact membership and pending gray work, not a GC body.
            "pcc_gc_object_index_seen_stable": lambda value: int(
                any(value is obj and obj.alive for obj in self.objects)),
            "pcc_gc_gray_worklist_note": self.gray_worklist.append,
        })
        load_functions(RUNTIME / "freestanding_gc_root_operations.py",
                       {"pcc_gc_try_init_ptr_locked", "pcc_gc_shade_gray_atomic"},
                       self.namespace)
        load_functions(RUNTIME / "freestanding_gc_barrier_dispatcher.py",
                       {"pcc_gc_count_free_store_shade"}, self.namespace)
        self.init_ptr_body = self.namespace["pcc_gc_try_init_ptr_locked"]
        self.init_ptr_source = RUNTIME / "freestanding_gc_root_operations.py"

        def observed_init(destination, source):
            assert self.depth > 0, "private field initialization requires the caller's lock"
            old_source = None if source is None else self.read(source, 0)
            old_destination = None if destination is None else self.read(destination, 0)
            status = self.init_ptr_body(destination, source)
            new_source = None if source is None else self.read(source, 0)
            new_destination = None if destination is None else self.read(destination, 0)
            assert new_source is old_source, "initialization consumed its source"
            if status != 0:
                assert old_destination is None, "destination was not a private new field"
                assert new_destination is new_source, "successful initialization did not copy the authoritative slot"
                if self.count_free:
                    # Reachability established a logical NEW owner. Account
                    # for it only AFTER the real body wrote the right slot.
                    # This is not a production GC1-4 physical RC update, and
                    # the actual counted-GC0 CAS must not be counted twice.
                    self.incref(new_destination)
            else:
                assert new_destination is old_destination, "initialization miss changed its destination"
            self.init_copy_events.append((status, source, destination))
            return status

        self.namespace["pcc_gc_try_init_ptr_locked"] = observed_init

    def gc(self, phase):
        if not hasattr(self, "thread_gate"):
            return
        if self.depth or self.no_park_depth:
            return
        # Actual callbacks already issued by these source-body providers.
        # No callback is inserted at the retired unregister ordinals.
        if phase not in ("graph_lock", "graph_unlock", "retain_finish", "no_park_exit"):
            return
        admitted = self.thread_gate.park(application_gate=False)
        assert admitted == 0, "ordinary runtime park admitted relocation"
        self.ordinary_parks.append(phase)
