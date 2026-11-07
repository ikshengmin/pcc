"""Actual-source host model for first-reservation zero-fill elision.

Unsafe memory and locks are deterministic shims, not native runtime evidence.
The reference restores unconditional object clearing; every other source line
is shared. Poisoned heap bytes and complete snapshots detect changed contents,
including unused span bytes and the selector trailer. Lock hooks model the
specific preparation race, not arbitrary concurrent execution.
"""

from __future__ import annotations
import ast
from pathlib import Path
import pytest

CORE = Path(__file__).resolve().parents[2] / "pcc" / "runtime" / "py"


class Model:

    def __init__(self, variant):
        self.mem = {}
        self.next = 4096
        self.globals = {}
        self.freed = []
        self.zeros = []
        self.locked = False
        self.locks = 0
        self.hook = None
        self.fail = 0
        self.allocs = 0
        self.ns = {"i64": int}
        for name, fn in {
            "null": lambda: 0,
            "ptr_is_null": lambda p: int(p == 0),
            "is_tagged_int": lambda p: 0,
            "ptr_eq": lambda a, b: int(a == b),
            "ptr_add": lambda p, n: p + n,
            "ptr_diff": lambda a, b: a - b,
            "malloc": self.malloc,
            "free": self.free,
            "memset": self.memset,
            "global_addr": self.global_addr,
            "global_load_ptr": lambda n: self.load(self.global_addr(n), 0, 8),
            "global_store_ptr": lambda n, p: self.store(self.global_addr(n), 0, p, 8),
            "load_i32": lambda p, o: self.load(p, o, 4),
            "load_i64": lambda p, o: self.load(p, o, 8),
            "load_ptr": lambda p, o: self.load(p, o, 8),
            "store_i32": lambda p, o, v: self.store(p, o, v, 4),
            "store_i64": lambda p, o, v: self.store(p, o, v, 8),
            "store_ptr": lambda p, o, v: self.store(p, o, v, 8),
            "pcc_py_gc_minor_graph_lock": self.lock,
            "pcc_py_gc_minor_graph_lock_for": self.lock_for,
            "pcc_py_gc_minor_graph_unlock": self.unlock,
            "pcc_gc_config_ensure": lambda: 4,
            "pcc_gc_backend4_evacuation_page_find": lambda p: 0,
            "pcc_gc_zpage_owner_index_insert": lambda *a: 1,
            "pcc_gc_zpage_owner_index_insert_preallocated": lambda *a: 1,
        }.items():
            self.ns[name] = fn
        for file in [
            CORE / "freestanding_gc_zpage_mechanics.py",
            CORE / "freestanding_gc_zpage_allocation.py",
        ]:
            source = file.read_text(encoding="utf-8")
            if (
                variant == "baseline"
                and file.name == "freestanding_gc_zpage_allocation.py"
            ):
                conditional = "            if published_reset_page == 0 or allocated != 0:\n                memset(obj, 0, alloc_size)"
                assert source.count(conditional) == 1
                source = source.replace(
                    conditional, "            memset(obj, 0, alloc_size)"
                )
            t = ast.parse(source)
            t.body = [n for n in t.body if isinstance(n, ast.FunctionDef)]
            for n in t.body:
                n.decorator_list = []
            exec(compile(t, str(file), "exec"), self.ns)
        self.ns["pcc_gc_backend4_zpage_link_node"] = lambda n: None
        self.store(self.global_addr("pcc_gc_config_initialized"), 0, 1, 4)
        self.store(self.global_addr("pcc_gc_backend_selected"), 0, 4, 4)

    def malloc(self, n):
        self.allocs += 1
        if self.fail == self.allocs:
            return 0
        p = self.next
        self.next += (n + 15) // 16 * 16 + 16
        self.mem[p] = bytearray([165]) * n
        return p

    def free(self, p):
        if not p:
            return
        assert p not in self.freed
        self.freed.append(p)

    def locate(self, p, n):
        for b, a in self.mem.items():
            if b <= p and p + n <= b + len(a):
                assert b not in self.freed, ("use-after-free", p)
                return (a, p - b)
        raise AssertionError(("out-of-bounds", p, n))

    def load(self, p, o, n):
        a, k = self.locate(p + o, n)
        return int.from_bytes(a[k : k + n], "little", signed=True)

    def store(self, p, o, v, n):
        a, k = self.locate(p + o, n)
        a[k : k + n] = int(v).to_bytes(n, "little", signed=True)

    def memset(self, p, v, n):
        a, k = self.locate(p, n)
        a[k : k + n] = bytes([v]) * n
        self.zeros.append((p, n))

    def global_addr(self, n):
        if n not in self.globals:
            p = self.next
            self.next += 32
            self.mem[p] = bytearray(8)
            self.globals[n] = p
        return self.globals[n]

    def lock_for(self, site):
        assert site == 2
        self.ns["pcc_py_gc_minor_graph_lock"]()

    def lock(self):
        assert not self.locked
        self.locks += 1
        if self.hook:
            self.hook(self)
        self.locked = True

    def unlock(self):
        assert self.locked
        self.locked = False

    def call(self, n, *a):
        return self.ns["pcc_gc_backend4_" + n](*a)

    def page(self, size, poison=True):
        p = self.malloc(376)
        self.memset(p, 0, 376)
        self.call("zpage_reset", p, 0, size)
        if poison:
            self.memset(self.load(p, 72, 8), 109, self.load(p, 80, 8))
        return p

    def state(self):
        return (
            {p: bytes(a).hex() for p, a in self.mem.items()},
            self.freed,
            self.locked,
        )

    def check_obj(self, p, n):
        assert p
        a, k = self.locate(p, n + 7 & -8)
        assert not any(a[k : k + (n + 7 & -8)])

    def poison(self, p, n):
        self.memset(p, 55, n)


def scenario(variant, kind, size, flags=0):
    m = Model(variant)
    if kind in ("cached", "cached_backend_changed"):
        p = m.page(size)
        m.store(p, 28, 2 if flags else 1, 4)
        m.ns["global_store_ptr"]("pcc_gc_backend4_free_page_head", p)
        m.store(p, 56, 0, 8)
    if kind in ("active", "external"):
        p = m.page(size)
        m.store(p, 28, 2 if flags else 1, 4)
        m.ns["global_store_ptr"]("pcc_gc_backend4_page_head", p)
        m.store(p, 56, 0, 8)
        if kind == "external":
            owner = m.malloc(32)
            m.memset(owner, 0, 32)
            m.store(owner, 12, flags, 4)
            m.call("zpage_track_alloc", owner, 32)
        else:
            m.call("zpage_set_active_page", p)
    if kind in ("race_fresh", "race_cached"):
        competing = m.page(size)
        m.store(competing, 28, 2 if flags else 1, 4)
        if kind == "race_cached":
            cached = m.page(size)
            m.ns["global_store_ptr"]("pcc_gc_backend4_free_page_head", cached)
            m.store(cached, 56, 0, 8)

        def hook(x):
            if x.locks == 2:
                x.ns["global_store_ptr"]("pcc_gc_backend4_page_head", competing)
                x.store(competing, 56, 0, 8)
                x.call("zpage_set_active_page", competing)

        m.hook = hook
    if kind in ("fail_page", "fail_span"):
        m.fail = m.allocs + (1 if kind == "fail_page" else 2)
    if kind in ("backend_changed", "cached_backend_changed"):

        def hook(x):
            if x.locks == 2:
                x.store(x.global_addr("pcc_gc_backend_selected"), 0, 0, 4)

        m.hook = hook
    if kind in ("prepared_nonzero", "prepared_bad_span"):

        def hook(x):
            if x.locks == 2:
                pages = [p for p, a in x.mem.items() if len(a) == 376]
                page = pages[-1]
                if kind == "prepared_nonzero":
                    x.store(page, 64, 8, 8)
                    x.poison(x.load(page, 72, 8), x.load(page, 80, 8))
                else:
                    x.store(page, 80, 0, 8)

        m.hook = hook
    p = m.call("try_zpage_alloc", size, flags)
    if kind in (
        "fail_page",
        "fail_span",
        "backend_changed",
        "cached_backend_changed",
        "prepared_bad_span",
    ) or (kind == "prepared_nonzero" and size in (4096, 65536)):
        assert p == 0
    else:
        m.check_obj(p, size)
        if kind in ("second", "rewind"):
            m.poison(p, size + 7 & -8)
            if kind == "rewind":
                page = m.ns["global_load_ptr"]("pcc_gc_backend4_page_head")
                m.store(page, 64, 0, 8)
                m.store(page, 88, 0, 8)
            q = m.call("try_zpage_alloc", size, flags)
            m.check_obj(q, size)
    return (m.state(), sum((n for _, n in m.zeros)), len(m.zeros))


KINDS = (
    "fresh",
    "cached",
    "active",
    "external",
    "race_fresh",
    "race_cached",
    "second",
    "rewind",
    "fail_page",
    "fail_span",
    "backend_changed",
    "cached_backend_changed",
    "prepared_nonzero",
    "prepared_bad_span",
)
CASES = [
    (kind, size, flags)
    for flags in (0, 256)
    for kind in KINDS
    for size in (16, 17, 128, 4096, 4097, 65536, 65537)
    if not (
        kind in ("cached", "race_cached", "cached_backend_changed") and size > 65536
    )
]


@pytest.mark.parametrize("kind,size,flags", CASES)
def test_first_reservation_preserves_memory_and_cleanup(kind, size, flags):
    reference, reference_bytes, reference_calls = scenario(
        "baseline", kind, size, flags
    )
    actual, actual_bytes, actual_calls = scenario("candidate", kind, size, flags)
    assert actual == reference
    assert actual_bytes <= reference_bytes
    if kind in ("fresh", "cached"):
        assert reference_bytes - actual_bytes == size + 7 & -8
        assert reference_calls - actual_calls == 1
    if (
        kind
        in (
            "fail_page",
            "fail_span",
            "backend_changed",
            "cached_backend_changed",
            "prepared_nonzero",
            "prepared_bad_span",
        )
        or (kind in ("active", "race_fresh", "race_cached") and size <= 65536)
        or (kind == "external" and size < 4096)
    ):
        assert actual_bytes == reference_bytes
        assert actual_calls == reference_calls
