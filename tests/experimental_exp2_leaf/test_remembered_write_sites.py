"""Execute the actual write-site bodies with a small protected-slot oracle.

No allocator/page implementation is assumed. The fake note ABI observes
semantic values and commit ordering without consulting target memory. This
test doesn't prove full native coverage, absence of safepoints or a cut.
"""
import ast
from pathlib import Path
import unittest

from pcc.runtime.py import py_abi_constants as abi


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"
WINDOW = "pcc_exp2_window_state"
PYLIST = abi.PY_TYPE_LIST
CAP = abi.PYLISTOBJECT_CAPACITY_OFFSET
LEN = abi.PYLISTOBJECT_LENGTH_OFFSET
ITEMS = abi.PYLISTOBJECT_ITEMS_OFFSET
DCAP = abi.PYDICTOBJECT_CAPACITY_OFFSET
USED = abi.PYDICTOBJECT_ENTRIES_USED_OFFSET
COUNT = abi.PYDICTOBJECT_ITEM_COUNT_OFFSET
INDEX = abi.PYDICTOBJECT_INDICES_OFFSET
ENTRIES = abi.PYDICTOBJECT_ENTRIES_OFFSET
KEY = abi.DICTENTRY_KEY_OFFSET
VALUE = abi.DICTENTRY_VALUE_OFFSET


class Oracle:
    def __init__(self, active=True):
        self.words = {}
        self.globals = {WINDOW: 2 if active else 0,
                        "pcc_gc_config_initialized": 1,
                        "pcc_gc_backend_selected": 3,
                        "pcc_gc_nonmoving_count_free": 1}
        self.events = []
        self.next = 100_000
        self.depth = 0
        self.backend = 3
        self.try_inits = []
        self.probe_result = -1
        self.commit_check = lambda value, kind: None
        self.env = {
            **vars(abi),
            "i64": int,
            "global_addr": lambda s: s,
            "null": lambda: 0,
            "is_tagged_int": lambda p: p & 1,
            "ptr_is_null": lambda p: p == 0,
            "ptr_eq": lambda a, b: a == b,
            "ptr_add": lambda p, off: p + off,
            "pcc_gc_backend": lambda: self.backend,
            "_selected_backend": lambda: self.backend,
            "pcc_exp2_note_old_target": self.note,
            "pcc_container_lock_held": lambda: self.depth > 0,
            "pcc_gc_count_free_store_shade": lambda *_: None,
            "pcc_gc_note_store": lambda: None,
            "pcc_gc_metric_shard": lambda: 0,
            "atomic_rmw_i64": lambda *_: 0,
            "pcc_gc_try_init_ptr_locked": self.try_init,
            "_list_lock": self.lock,
            "_list_unlock": self.unlock,
            "_dict_table_lock": self.lock,
            "_dict_table_unlock": self.unlock,
            "pcc_container_lock": self.lock,
            "pcc_container_unlock": self.unlock,
            "_container_fast_admitted": lambda: 1,
            "_container_fast_still_admitted": lambda: 1,
            "_list_is_sane": lambda *_: True,
            "_dict_gc0_admitted_locked": lambda: 1,
            "_dict_set_needs_graph_lock": lambda *_: 0,
            "_dict_fast0_hash": lambda *args: 17,
            "_dict_probe_fast0": self.probe,
            "_frame_list_raw": lambda: 1,
            "py_list_set": lambda *_: None,
            "py_decref": lambda *_: None,
            "_maybe_grow": lambda *_: 0,
            "malloc": self.alloc,
            "stack_alloc": self.alloc,
            "free": lambda *_: None,
            "memmove": self.memmove,
            "memset": self.memset,
        }
        for suffix in ("ptr", "i32", "i64"):
            self.env["load_" + suffix] = self.load
            self.env["store_" + suffix] = self.store
        for suffix in ("i32", "i64"):
            self.env["atomic_load_" + suffix] = self.load

    def alloc(self, n):
        p = self.next
        self.next += (n + 63) & -64
        return p

    def load(self, p, off, order=None):
        if isinstance(p, str):
            return self.globals.get(p, 0)
        return self.words.get(p + off, 0)

    def store(self, p, off, v):
        self.words[p + off] = v

    def lock(self, *_):
        self.depth += 1

    def unlock(self, *_):
        assert self.depth > 0
        self.depth -= 1

    def memmove(self, dst, src, n):
        for off in range(0, n, 8):
            self.store(dst, off, self.load(src, off))

    def memset(self, dst, value, n):
        for off in range(0, n, 8):
            self.store(dst, off, value)

    def note(self, value, kind):
        assert self.globals[WINDOW] == 2
        if kind >= 3:
            assert self.depth > 0
        self.commit_check(value, kind)
        self.events.append((value, kind))
        return 0  # Observational only; not a store success value.

    def probe(self, d, key, kind, hash_val, output):
        self.store(output, 0, 1)
        return self.probe_result

    def try_init(self, dst, src):
        if self.try_inits and not self.try_inits.pop(0):
            return 0
        self.store(dst, 0, self.load(src, 0))
        return 1

    def source(self, file, *funcs):
        path = RUNTIME / file
        tree = ast.parse(path.read_text(), str(path))
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name in funcs]
        assert len(selected) == len(funcs)
        for node in selected:
            node.decorator_list = []
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), self.env)
        return self.env[funcs[0]]

    def list_with_capacity(self, values, capacity):
        ptr, items = self.alloc(64), self.alloc(capacity * 8)
        self.store(ptr, 8, PYLIST)
        self.store(ptr, CAP, capacity)
        self.store(ptr, LEN, len(values))
        self.store(ptr, ITEMS, items)
        for index, value in enumerate(values):
            self.store(items, index * 8, value)
        return ptr, items

    def empty_dict(self):
        d, table, indices = self.alloc(96), self.alloc(4 * abi.DICTENTRY_SIZE), self.alloc(32)
        self.store(d, DCAP, 4)
        self.store(d, ENTRIES, table)
        self.store(d, INDEX, indices)
        self.store(indices, 8, -1)
        return d, table, indices


class WriteSiteContract(unittest.TestCase):
    def test_owned_ptr_store_vs_root_or_disabled(self):
        for owner, active, expected in ((64, True, [(320, 1)]),
                                        (0, True, []), (31, True, []), (64, False, [])):
            with self.subTest(owner=owner, active=active):
                vm = Oracle(active)
                f = vm.source("py_obj.py", "pcc_gc_store_ptr", "_store_root_nonmoving_tracing")
                vm.commit_check = lambda v, k: self.assertEqual(vm.load(128, 0), v)
                f(owner, 128, 320)
                f(owner, 0, 640)  # A refused store is not an edge.
                self.assertEqual(vm.load(128, 0), 320)
                self.assertEqual(vm.events, expected)

    def test_owner_aware_barrier_does_not_depend_on_major_mark(self):
        vm = Oracle()
        note = vm.source("freestanding_gc_barrier_dispatcher.py", "pcc_gc_note_slot_write_barrier")
        note(0, 128, 320)
        note(33, 128, 320)
        note(64, 128, 0)
        note(64, 128, 41)
        self.assertEqual(vm.events, [])
        note(64, 128, 320)
        self.assertEqual(vm.events, [(320, 2)])
        vm.globals[WINDOW] = 1
        note(64, 128, 640)
        self.assertEqual(vm.events, [(320, 2)])

    def test_append_commits_or_declines_and_growth_remembers_copies(self):
        for kind, want, capacity, admitted, enabled, expected in (
            ("fast", (640,), 1, True, True, [(640, 3)]),
            ("refused", (), 1, False, True, []),
            ("grow", (320, 640), 1, True, True, [(320, 4), (640, 3)]),
            ("grow-refused-append", (320,), 1, False, True, [(320, 4)]),
            ("grow-disabled", (320, 640), 1, True, False, []),
        ):
            with self.subTest(path=kind):
                vm = Oracle(enabled)
                f = vm.source("py_list.py", "_append_existing_capacity_gc0", "_append_grow_gc0")
                lst, _ = vm.list_with_capacity([320] if kind.startswith("grow") else [], capacity)
                list_slot, item_slot = vm.alloc(8), vm.alloc(8)
                vm.store(list_slot, 0, lst)
                vm.store(item_slot, 0, 640)
                vm.try_inits = [admitted]
                self.assertEqual(f(list_slot, item_slot), int(admitted))
                self.assertEqual(vm.depth, 0)
                self.assertEqual(vm.events, expected)
                items = vm.load(lst, ITEMS)
                self.assertEqual(tuple(vm.load(items, i * 8) for i in range(vm.load(lst, LEN))), want)

    def test_dict_records_only_indexed_insertion_and_value_replacement(self):
        vm = Oracle()
        set_value = vm.source("py_dict.py", "_dict_set_gc0_locked")
        d, table, indices = vm.empty_dict()
        def committed(val, kind):
            self.assertEqual(vm.load(d, COUNT), 1)
            self.assertEqual(vm.load(indices, 8), 0)
            self.assertEqual(vm.load(table, KEY if kind == 5 else VALUE), val)
        vm.commit_check = committed
        vm.try_inits = [True, False]
        self.assertEqual(set_value(d, 320, 640, 2), 0)
        self.assertEqual((vm.load(d, COUNT), vm.load(indices, 8), vm.load(table, KEY)), (0, -1, 0))
        self.assertEqual(vm.events, [])
        self.assertEqual(set_value(d, 320, 640, 2), 1)
        self.assertEqual(vm.events, [(320, 5), (640, 6)])
        vm.probe_result = 0
        self.assertEqual(set_value(d, 320, 640, 2), 1)
        self.assertEqual(vm.events, [(320, 5), (640, 6)])  # no mutation
        self.assertEqual(set_value(d, 320, 960, 2), 1)
        self.assertEqual(vm.events, [(320, 5), (640, 6), (960, 6)])
        vm.globals[WINDOW] = 0
        self.assertEqual(set_value(d, 320, 1280, 2), 1)
        self.assertEqual(vm.load(table, VALUE), 1280)
        self.assertEqual(vm.events, [(320, 5), (640, 6), (960, 6)])

    def test_managed_list_frame_cell_only_while_protected(self):
        vm = Oracle()
        frame, items = vm.list_with_capacity([0], 1)
        put = vm.source("py_gen.py", "py_gen_frame_set")
        vm.commit_check = lambda v, k: self.assertEqual(vm.load(items, 0), v)
        put(frame, -1, 320)
        self.assertEqual(vm.events, [])
        put(frame, 0, 320)
        self.assertEqual(vm.events, [(320, 7)])
        vm.globals[WINDOW] = 3
        put(frame, 0, 640)
        self.assertEqual(vm.load(items, 0), 640)
        self.assertEqual(vm.events, [(320, 7)])
