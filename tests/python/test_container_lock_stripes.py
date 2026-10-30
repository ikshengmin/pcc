"""Per-object container locks: striped 0/1 words with a fixed lock order.

Every GC0 container transaction took the one graph lock word, which four
carriers queued on. pcc_container_lock takes one 0/1 word chosen by the
object's address (64 stripes, one per 128-byte line), so all transactions
on one object exclude each other while those on unrelated objects rarely
share a word. The graph lock is taken outside a container lock, never
inside it, and a holder takes no second container lock: both abort, so a
wait for either can never close a cycle. Holder and waiter are no-park, and
the release polls a pending stop when it leaves the last no-park level.
A transaction over two containers (list.extend of a list) takes both stripes
in index order, a shared one once, so a waiter holds only lower stripes.
"""
from __future__ import annotations

import ast
from pathlib import Path
import types

import pytest


KERNEL = Path(__file__).resolve().parents[2] / "pcc/runtime/py/freestanding_thread_kernel_pthread.py"
NAMES = ("_container_stripe_index", "_container_stripe_word", "_container_stripe",
         "_container_enter", "_container_acquire", "_container_exit",
         "pcc_container_lock", "pcc_container_unlock", "pcc_container_lock_pair",
         "pcc_container_unlock_pair", "pcc_container_lock_held",
         "pcc_thread_graph_lock_acquire")
REGISTERED = 1 << 49
SCHEDULER = 1 << 48
HELD = 1 << 53
GRAPH_UNIT = 1 << 24


class Abort(Exception):
    pass


class Kernel:
    def __init__(self, state=REGISTERED, stop=0):
        self.memory = {("pcc_tls_thread_state_py", 0): state, ("pcc_thread_stop_requested", 0): stop}
        self.events = []
        self.on_spin = None
        tree = ast.parse(KERNEL.read_text())
        constants = {node.target.id: node.value.value for node in tree.body
                     if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                     and isinstance(node.value, ast.Constant)}
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in NAMES]
        assert {node.name for node in nodes} == set(NAMES)
        for node in nodes:
            node.decorator_list = []
        self.ns = dict(constants)
        self.ns.update({
            "i64": int,
            "global_addr": lambda name: (name, 0),
            "ptr_add": lambda base, offset: (base[0], base[1] + offset),
            "ptr_to_int": lambda value: value,
            "load_i64": lambda slot, offset: self.memory[(slot[0], slot[1] + offset)],
            "store_i64": self.store,
            "atomic_load_i32": self.atomic_load,
            "atomic_cas_i32": self.cas,
            "atomic_store_i32": self.atomic_store,
            "pcc_platform_abort": self.abort,
            "pcc_current_thread_id": self.register,
            "pcc_thread_safepoint": lambda: self.events.append("safepoint"),
            "_graph_wait_acquire": lambda word: pytest.fail("graph lock contended in the model"),
        })
        code = compile(ast.Module(nodes, type_ignores=[]), str(KERNEL), "exec")
        for const in code.co_consts:
            if isinstance(const, types.CodeType) and const.co_name in NAMES:
                self.ns[const.co_name] = types.FunctionType(const, self.ns)

    def store(self, slot, offset, value):
        self.memory[(slot[0], slot[1] + offset)] = value

    def atomic_load(self, slot, offset, order):
        key = (slot[0], slot[1] + offset)
        if key[0] == "pcc_container_lock_stripes" and self.memory.get(key, 0) and self.on_spin:
            hook, self.on_spin = self.on_spin, None
            hook(key)
        return self.memory.get(key, 0)

    def cas(self, slot, offset, expected, desired, success, failure):
        key = (slot[0], slot[1] + offset)
        observed = self.memory.get(key, 0)
        if observed == expected:
            self.memory[key] = desired
        self.events.append(("cas", key, observed == expected, success))
        return observed

    def atomic_store(self, slot, offset, value, order):
        self.memory[(slot[0], slot[1] + offset)] = value
        self.events.append(("release", (slot[0], slot[1] + offset), order))

    def abort(self):
        raise Abort

    def register(self):
        self.events.append("register")
        self.memory[("pcc_tls_thread_state_py", 0)] |= REGISTERED
        return 7

    @property
    def state(self):
        return self.memory[("pcc_tls_thread_state_py", 0)]

    def stripe(self, address):
        return self.ns["_container_stripe"](address)

    def lock(self, address):
        self.ns["pcc_container_lock"](address)

    def unlock(self, address):
        self.ns["pcc_container_unlock"](address)


def test_a_holder_is_no_park_and_marked_until_it_releases():
    kernel = Kernel()
    kernel.lock(0x10000)
    word = kernel.stripe(0x10000)
    assert kernel.memory[word] == 1
    assert kernel.state == REGISTERED | HELD | 1
    assert kernel.ns["pcc_container_lock_held"]() == 1
    kernel.unlock(0x10000)
    assert kernel.memory[word] == 0 and kernel.state == REGISTERED
    assert ("release", word, "release") in kernel.events
    assert kernel.ns["pcc_container_lock_held"]() == 0


def test_one_object_always_takes_one_stripe_and_stripes_are_128_bytes_apart():
    kernel = Kernel()
    seen = set()
    for address in range(0x10000, 0x10000 + 16 * 4096, 16):
        stripe = kernel.stripe(address)
        assert stripe == kernel.stripe(address)
        assert stripe[1] % 128 == 0 and 0 <= stripe[1] < 64 * 128
        seen.add(stripe)
    assert len(seen) == 64


def test_a_waiter_spins_reading_until_the_holder_releases():
    kernel = Kernel()
    word = kernel.stripe(0x20000)
    kernel.memory[word] = 1

    def holder_releases(key):
        assert key == word
        assert not [event for event in kernel.events if isinstance(event, tuple) and event[0] == "cas"]
        kernel.memory[key] = 0
    kernel.on_spin = holder_releases
    kernel.lock(0x20000)
    assert kernel.memory[word] == 1
    cas = [event for event in kernel.events if isinstance(event, tuple) and event[0] == "cas"]
    assert cas == [("cas", word, True, "acquire")]


def test_a_second_container_lock_aborts():
    kernel = Kernel()
    kernel.lock(0x10000)
    with pytest.raises(Abort):
        kernel.lock(0x30000)


def test_an_outer_graph_lock_inside_a_container_lock_aborts():
    kernel = Kernel()
    kernel.lock(0x10000)
    with pytest.raises(Abort):
        kernel.ns["pcc_thread_graph_lock_acquire"](("g_pcc_py_gc_minor_graph_lock", 0))


def test_a_container_lock_inside_the_graph_lock_is_the_allowed_order():
    kernel = Kernel()
    kernel.memory[("g_pcc_py_gc_minor_graph_lock", 0)] = 0
    kernel.ns["pcc_thread_graph_lock_acquire"](("g_pcc_py_gc_minor_graph_lock", 0))
    assert kernel.state == REGISTERED | GRAPH_UNIT | 1
    kernel.lock(0x10000)
    assert kernel.state == REGISTERED | GRAPH_UNIT | HELD | 2
    # Re-entering the held graph lock waits on nothing.
    kernel.ns["pcc_thread_graph_lock_acquire"](("g_pcc_py_gc_minor_graph_lock", 0))
    assert kernel.state == REGISTERED | 2 * GRAPH_UNIT | HELD | 2
    kernel.unlock(0x10000)
    assert kernel.state == REGISTERED | 2 * GRAPH_UNIT | 1


def test_an_unregistered_thread_registers_before_it_holds_anything():
    kernel = Kernel(state=0)
    kernel.lock(0x10000)
    assert kernel.events[0] == "register" and kernel.state == REGISTERED | HELD | 1
    kernel.unlock(0x10000)


@pytest.mark.parametrize("stop", (0, 1))
def test_leaving_the_last_no_park_level_polls_a_pending_stop(stop):
    kernel = Kernel(stop=stop)
    kernel.lock(0x10000)
    kernel.unlock(0x10000)
    assert ("safepoint" in kernel.events) == bool(stop)


def test_an_unpaired_release_aborts():
    kernel = Kernel()
    with pytest.raises(Abort):
        kernel.unlock(0x10000)


def test_a_scheduler_lock_owner_never_reaches_a_zero_level_release_holding_it():
    kernel = Kernel(state=REGISTERED | SCHEDULER)
    kernel.lock(0x10000)
    with pytest.raises(Abort):
        kernel.unlock(0x10000)


def _two_addresses(kernel, same):
    first = 0x10000
    for second in range(0x10010, 0x10010 + 16 * 65536, 16):
        if (kernel.stripe(second) == kernel.stripe(first)) == same:
            return first, second
    raise AssertionError("no address pair")


def _cas_words(kernel):
    return [event[1] for event in kernel.events if isinstance(event, tuple) and event[0] == "cas"]


@pytest.mark.parametrize("reverse", (False, True))
def test_a_pair_takes_its_stripes_in_index_order_and_releases_both(reverse):
    kernel = Kernel()
    first, second = _two_addresses(kernel, same=False)
    if (kernel.stripe(first)[1] > kernel.stripe(second)[1]) != reverse:
        first, second = second, first
    low, high = sorted((kernel.stripe(first), kernel.stripe(second)), key=lambda word: word[1])
    kernel.ns["pcc_container_lock_pair"](first, second)
    assert _cas_words(kernel) == [low, high]
    assert kernel.memory[low] == kernel.memory[high] == 1
    assert kernel.state == REGISTERED | HELD | 1
    kernel.ns["pcc_container_unlock_pair"](first, second)
    assert kernel.memory[low] == kernel.memory[high] == 0 and kernel.state == REGISTERED


def test_a_pair_sharing_a_stripe_takes_it_once():
    kernel = Kernel()
    first, second = _two_addresses(kernel, same=True)
    kernel.ns["pcc_container_lock_pair"](first, second)
    assert _cas_words(kernel) == [kernel.stripe(first)]
    kernel.ns["pcc_container_unlock_pair"](first, second)
    releases = [event for event in kernel.events if isinstance(event, tuple) and event[0] == "release"]
    assert releases == [("release", kernel.stripe(first), "release")]
    assert kernel.memory[kernel.stripe(first)] == 0 and kernel.state == REGISTERED


def test_a_pair_waiter_holds_only_the_lower_stripe():
    kernel = Kernel()
    first, second = _two_addresses(kernel, same=False)
    low, high = sorted((kernel.stripe(first), kernel.stripe(second)), key=lambda word: word[1])
    kernel.memory[high] = 1

    def holder_releases(key):
        assert key == high and kernel.memory[low] == 1
        kernel.memory[key] = 0
    kernel.on_spin = holder_releases
    kernel.ns["pcc_container_lock_pair"](first, second)
    assert kernel.memory[low] == kernel.memory[high] == 1


def test_a_pair_inside_a_container_lock_and_a_lock_inside_a_pair_abort():
    kernel = Kernel()
    first, second = _two_addresses(kernel, same=False)
    kernel.lock(0x50000)
    with pytest.raises(Abort):
        kernel.ns["pcc_container_lock_pair"](first, second)
    other = Kernel()
    other.ns["pcc_container_lock_pair"](first, second)
    with pytest.raises(Abort):
        other.lock(0x50000)
    with pytest.raises(Abort):
        other.ns["pcc_thread_graph_lock_acquire"](("g_pcc_py_gc_minor_graph_lock", 0))
