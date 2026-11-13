"""WeakSet and the CPython 3.15 weak-reference/finalizer order, executed.

Specification:
- Python Library Reference, ``weakref``: references are hashable when their
  referent is, keep that hash after it dies, and compare by referent while
  both live (CPython v3.15.0 Objects/weakrefobject.c weakref_hash and
  weakref_richcompare); ``weakref.ref`` refuses objects without a weak
  reference list ("cannot create weak reference to 'int' object").
- ``WeakSet`` is CPython v3.15.0 Lib/_weakrefset.py, compiled from
  pcc/stdlib/_weakrefset.py over the native ``ref``.
- Cyclic garbage (CPython v3.15.0 Python/gc.c handle_weakrefs, then the clear
  after handle_resurrected_objects): a ref with a callback is cleared before
  finalizers and a live one runs its callback first; a ref without one keeps
  its target through finalizers and is cleared silently after them.
- A reference-count death (Objects/typeobject.c subtype_dealloc) runs the
  finalizer before PyObject_ClearWeakRefs.

The program covers eight cases. GC0 counts references, so its plain run
matches CPython's plain run. GC1-GC4 are count-free: every death is a
collection, so all their runs match CPython's run with each case-6 object
made cyclic (argument ``cyclic``), as does GC0's cyclic run. The c_gnu_attributes
checked-unit WeakSet shape is case 3. Python IR passes stay at their defaults.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROGRAM = '''import gc
import sys
import threading
import weakref

CYCLIC = len(sys.argv) > 1
log = []
keep = []


def run_worker(function):
    worker = threading.Thread(target=function)
    worker.start()
    worker.join()


def collect_until(predicate):
    for attempt in range(8):
        gc.collect()
        if predicate():
            return True
    return False


class Key:
    def __init__(self, name, group):
        self.name = name
        self.group = group

    def __eq__(self, other):
        return isinstance(other, Key) and self.group == other.group

    def __hash__(self):
        return hash(self.group)


class Node:
    def __init__(self, name):
        self.name = name
        self.other = None


class Fin:
    def __init__(self, name):
        self.name = name
        self.other = None
        self.refs = []

    def __del__(self):
        states = []
        for label, ref in self.refs:
            states.append(label + "=" + ("dead" if ref() is None else "live"))
        log.append("del " + self.name + " " + " ".join(states))


def callback_for(name):
    def callback(ref):
        log.append("cb " + name + " " + ("dead" if ref() is None else "live"))
    return callback


# 1. weakref.ref hash/eq follow the referent; a dead ref keeps its cached hash.
def case_ref_hash_eq():
    a = Key("a", 1)
    b = Key("b", 1)
    c = Key("c", 2)
    ra = weakref.ref(a)
    rb = weakref.ref(b, callback_for("rb"))
    rc = weakref.ref(c)
    keep.append((ra, rb, rc))
    hashed = hash(ra)
    print("1 alive", ra == rb, ra != rb, ra == rc, hashed == hash(a), hash(rb) == hash(b))
    a = None
    b = None
    c = None


def case_ref_hash_eq_dead():
    ra, rb, rc = keep.pop()
    hashed = hash(ra)
    ok = collect_until(lambda: ra() is None and rb() is None and rc() is None)
    try:
        hash(rc)
        dead_hash = "hash"
    except TypeError as error:
        dead_hash = "TypeError " + str(error)
    print("1 dead", ok, ra == rb, ra != rb, ra == ra, hash(ra) == hashed, dead_hash)


# 2. WeakSet membership uses referent equality, rejects non-weakrefable items.
def case_weakset_basic():
    s = weakref.WeakSet()
    a = Key("a", 1)
    b = Key("b", 1)
    c = Key("c", 2)
    s.add(a)
    s.add(b)
    s.add(c)
    print("2 len", len(s), a in s, Key("x", 2) in s, Key("y", 3) in s, 1 in s)
    try:
        s.add(1)
        print("2 add int ok")
    except TypeError as error:
        print("2 add int TypeError", str(error))
    s.discard(Key("z", 2))
    print("2 discard", len(s), c in s)
    try:
        s.remove(c)
        print("2 remove missing ok")
    except KeyError:
        print("2 remove missing KeyError")
    s.add(c)
    print("2 iter", sorted(item.name for item in s))
    s.remove(c)
    print("2 removed", len(s), sorted(item.name for item in s))
    keep.append((a, b, c))


# 3. Entries disappear when their referents die (Stage1 checked-unit shape).
CHECKED = weakref.WeakSet()


def check(node):
    try:
        if node in CHECKED:
            return "cached"
    except TypeError:
        pass
    try:
        CHECKED.add(node)
    except TypeError:
        pass
    return "checked"


def produce_checked():
    for index in range(50):
        node = Node(str(index))
        if CYCLIC:
            node.other = node
        check(node)


def case_weakset_death():
    stay = Node("stay")
    print("3 first", check(stay), check(stay), check(42), len(CHECKED))
    run_worker(produce_checked)
    ok = collect_until(lambda: len(CHECKED) == 1)
    print("3 after", ok, len(CHECKED), [n.name for n in CHECKED], check(stay))


# 4. Cyclic garbage: callback refs clear before finalizers, plain refs after.
def produce_cycle():
    b = Fin("b")
    c = Fin("c")
    p = Node("p")
    b.other = c
    c.other = b
    c.peer = p
    p.other = c
    b.refs.append(("self_cb", weakref.ref(b, callback_for("b"))))
    b.refs.append(("self_plain", weakref.ref(b)))
    b.refs.append(("peer_plain", weakref.ref(p)))
    c.refs.append(("peer_cb", weakref.ref(p, callback_for("p"))))
    c.refs.append(("self_plain", weakref.ref(c)))
    peer = weakref.ref(p)

    def b_ext(ref):
        # Callbacks run before finalizers, after refs with callbacks clear;
        # a ref without one still names its unreachable target.
        log.append("cb b_ext " + ("dead" if ref() is None else "live")
                   + " peer_plain=" + ("dead" if peer() is None else "live"))

    keep.append(weakref.ref(b, b_ext))
    keep.append(peer)
    keep.append(weakref.ref(c))


def case_cycle_order():
    log.clear()
    run_worker(produce_cycle)
    rc = keep.pop()
    rp = keep.pop()
    rb = keep.pop()
    ok = collect_until(lambda: rp() is None and rc() is None and rb() is None)
    callbacks = [entry for entry in log if entry.startswith("cb ")]
    finals = [entry for entry in log if entry.startswith("del ")]
    first_del = min(log.index(entry) for entry in finals) if finals else -1
    last_cb = max(log.index(entry) for entry in callbacks) if callbacks else -1
    print("4 cycle", ok, sorted(callbacks), last_cb < first_del)
    print("4 finals", sorted(finals))


# 5. A WeakSet whose entries are garbage loses them before finalizers run.
S = weakref.WeakSet()


class Watcher:
    def __init__(self, name):
        self.name = name
        self.other = None

    def __del__(self):
        log.append("watch " + self.name + " len=" + str(len(S)))


def produce_set_cycle():
    w = Watcher("w")
    n = Node("n")
    w.other = n
    n.other = w
    S.add(w)
    S.add(n)
    keep.append(weakref.ref(n))


def case_weakset_finalizer():
    log.clear()
    run_worker(produce_set_cycle)
    rn = keep.pop()
    ok = collect_until(lambda: rn() is None)
    print("5 set", ok, log, len(S))


# 6. Finalizer death of non-cyclic garbage (refcount order on GC0/CPython).
class Solo:
    def __init__(self):
        self.cycle = self if CYCLIC else None

    def __del__(self):
        log.append("solo del cb=" + ("dead" if R[0]() is None else "live")
                   + " plain=" + ("dead" if R[1]() is None else "live"))


R = []


def produce_solo():
    s = Solo()
    R.append(weakref.ref(s, lambda ref: log.append(
        "solo cb plain=" + ("dead" if R[1]() is None else "live"))))
    R.append(weakref.ref(s))


def case_solo():
    log.clear()
    run_worker(produce_solo)
    ok = collect_until(lambda: R[1]() is None and len(log) == 2)
    print("6 solo", ok, log)


# 7. Resurrection keeps plain refs; the next true death clears them.
SAVED = []


class Phoenix:
    def __init__(self):
        self.cycle = self
        self.lives = 1

    def __del__(self):
        if self.lives:
            self.lives = 0
            SAVED.append(self)


def produce_phoenix():
    p = Phoenix()
    R.append(weakref.ref(p))


def drop_saved():
    SAVED.clear()


def case_phoenix():
    R.clear()
    run_worker(produce_phoenix)
    ok = collect_until(lambda: len(SAVED) == 1)
    alive = R[0]() is SAVED[0] if SAVED else False
    run_worker(drop_saved)
    gone = collect_until(lambda: R[0]() is None)
    print("7 phoenix", ok, alive, gone)


# 8. WeakSet iteration tolerates removal and union-like operations.
def case_weakset_ops():
    a = Node("a")
    b = Node("b")
    c = Node("c")
    s = weakref.WeakSet([a, b])
    t = weakref.WeakSet([b, c])
    print("8 ops", sorted(n.name for n in s | t), sorted(n.name for n in s & t),
          sorted(n.name for n in s - t), s == weakref.WeakSet([b, a]), bool(weakref.WeakSet()))
    for item in s:
        s.discard(a)
    print("8 iter", sorted(n.name for n in s), len(s.copy()))
    keep.append((a, b, c))


def main():
    gc.disable()
    case_ref_hash_eq()
    case_ref_hash_eq_dead()
    case_weakset_basic()
    case_weakset_death()
    case_cycle_order()
    case_weakset_finalizer()
    case_solo()
    case_phoenix()
    case_weakset_ops()
    print("DONE")


main()
'''

EXPECTED_PLAIN = "1 alive True False False True True\n1 dead True False True True True TypeError weak object has gone away\n2 len 2 True True False False\n2 add int TypeError cannot create weak reference to 'int' object\n2 discard 1 False\n2 remove missing KeyError\n2 iter ['a', 'c']\n2 removed 1 ['a']\n3 first checked cached checked 1\n3 after True 1 ['stay'] cached\n4 cycle True ['cb b_ext dead peer_plain=live'] True\n4 finals ['del b self_cb=dead self_plain=live peer_plain=live', 'del c peer_cb=dead self_plain=live']\n5 set True ['watch w len=0'] 0\n6 solo True ['solo del cb=live plain=live', 'solo cb plain=dead']\n7 phoenix True True True\n8 ops ['a', 'b', 'c'] ['b'] ['a'] True False\n8 iter ['b'] 1\nDONE\n"
EXPECTED_CYCLIC = "1 alive True False False True True\n1 dead True False True True True TypeError weak object has gone away\n2 len 2 True True False False\n2 add int TypeError cannot create weak reference to 'int' object\n2 discard 1 False\n2 remove missing KeyError\n2 iter ['a', 'c']\n2 removed 1 ['a']\n3 first checked cached checked 1\n3 after True 1 ['stay'] cached\n4 cycle True ['cb b_ext dead peer_plain=live'] True\n4 finals ['del b self_cb=dead self_plain=live peer_plain=live', 'del c peer_cb=dead self_plain=live']\n5 set True ['watch w len=0'] 0\n6 solo True ['solo cb plain=live', 'solo del cb=dead plain=live']\n7 phoenix True True True\n8 ops ['a', 'b', 'c'] ['b'] ['a'] True False\n8 iter ['b'] 1\nDONE\n"


# Set methods reached through dynamic receivers (``self.data`` is untyped):
# names shared with list/dict guards bind the runtime's set methods, and the
# predicates join the guard's generic arm as bool objects.
DYNAMIC_SET_PROGRAM = '''
class Holder:
    def __init__(self):
        self.data = {1, 2, 3}


def run():
    out = []
    h = Holder()
    out.append(sorted(h.data.copy()))
    out.append(h.data.update([4], (5,)))
    out.append(sorted(h.data))
    out.append(sorted(h.data.union([9])))
    out.append(h.data.difference_update([1]))
    out.append(sorted(h.data))
    out.append(h.data.isdisjoint([7]))
    out.append(h.data.issubset(range(10)))
    out.append(h.data.issuperset([2]))
    out.append(h.data.discard(99))
    try:
        h.data.remove(99)
    except KeyError as error:
        out.append("KeyError " + repr(error.args))
    h.data.remove(2)
    try:
        h.data.add()
    except TypeError as error:
        out.append(str(error))
    bound = getattr(h.data, "add")
    bound(8)
    out.append(sorted(h.data))
    out.append(h.data.clear())
    out.append(len(h.data))
    print(out)


run()
'''
EXPECTED_DYNAMIC_SET = (
    "[[1, 2, 3], None, [1, 2, 3, 4, 5], [1, 2, 3, 4, 5, 9], None, [2, 3, 4, 5], "
    "True, True, True, None, 'KeyError (99,)', "
    "'set.add() takes exactly one argument (0 given)', [3, 4, 5, 8], None, 0]\n"
)


def _run(command, environment=None):
    return subprocess.run(command, capture_output=True, text=True, timeout=20,
                          env=environment)


def _expected(backend, mode):
    return EXPECTED_PLAIN if backend == 0 and mode == () else EXPECTED_CYCLIC


@pytest.mark.pcc_gate(env="PCC_RUN_CPYTHON_REFERENCE")
def test_cpython_reference_outputs(tmp_path):
    assert sys.implementation.name == "cpython"
    assert sys.version_info[:3] == (3, 15, 0), sys.version
    source = tmp_path / "weakset_order.py"
    source.write_text(PROGRAM)
    plain = _run([sys.executable, str(source)])
    cyclic = _run([sys.executable, str(source), "cyclic"])
    assert (plain.returncode, plain.stdout, plain.stderr) == (0, EXPECTED_PLAIN, "")
    assert (cyclic.returncode, cyclic.stdout, cyclic.stderr) == (0, EXPECTED_CYCLIC, "")
    dynamic = tmp_path / "dynamic_set.py"
    dynamic.write_text(DYNAMIC_SET_PROGRAM)
    result = _run([sys.executable, str(dynamic)])
    assert (result.returncode, result.stdout, result.stderr) == (0, EXPECTED_DYNAMIC_SET, "")


@pytest.mark.integration
def test_weakset_and_weakref_order_native_five_gc(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    source = tmp_path / "weakset_order.py"
    source.write_text(PROGRAM)
    binary = tmp_path / "weakset_order"
    python_program_compiler(str(source), str(binary), backend="self",
                            runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        for mode in ((), ("cyclic",)):
            result = _run([str(binary), *mode],
                          dict(os.environ, PCC_GC_BACKEND=str(backend)))
            label = f"GC{backend} {mode or 'plain'}"
            assert result.returncode == 0, (label, result.stdout, result.stderr)
            assert result.stderr == "", (label, result.stderr)
            assert result.stdout == _expected(backend, mode), (label, result.stdout)


@pytest.mark.integration
def test_dynamic_set_methods_native_five_gc(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    source = tmp_path / "dynamic_set.py"
    source.write_text(DYNAMIC_SET_PROGRAM)
    binary = tmp_path / "dynamic_set"
    python_program_compiler(str(source), str(binary), backend="self",
                            runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        result = _run([str(binary)], dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert (result.returncode, result.stdout, result.stderr) == (
            0, EXPECTED_DYNAMIC_SET, ""), f"GC{backend}"


def test_weakset_port_is_cpython_source_with_documented_changes():
    """The port differs from CPython's file only where its docstring says."""
    import _weakrefset as cpython_weakrefset
    import inspect

    upstream = inspect.getsource(cpython_weakrefset)
    port = Path(__file__).resolve().parents[2].joinpath(
        "pcc/stdlib/_weakrefset.py").read_text()
    port_body = port.split('"""', 2)[2]
    upstream_body = upstream.split("from _weakref import ref", 1)[1]
    expected = ("\n\nfrom weakref import ref" + upstream_body).replace(
        "set(map(ref, other))", "set(ref(item) for item in other)")
    assert port_body == expected


def test_weakset_port_semantics_on_host():
    """On CPython the port runs over CPython's ref: same answers as weakref.WeakSet."""
    import gc
    import weakref

    from pcc.stdlib import _weakrefset

    class Key:
        def __init__(self, group):
            self.group = group

        def __eq__(self, other):
            return isinstance(other, Key) and self.group == other.group

        def __hash__(self):
            return hash(self.group)

    def observe(factory):
        a, b, c = Key(1), Key(1), Key(2)
        s = factory([a, b, c])
        t = factory([c])
        seen = [len(s), Key(2) in s, 1 in s, len(s - t), len(s & t), len(s | t),
                s == factory([a, c]), s.issuperset(t), bool(factory())]
        c = None
        t = None
        gc.collect()
        seen.append(len(s))
        return seen

    assert observe(_weakrefset.WeakSet) == observe(weakref.WeakSet)


def test_weakref_import_policy_keeps_native_ownership():
    from pcc.frontends.python.pipeline_import_policy import (
        NATIVE_BUILTIN_IMPORTS_WITH_COMPILED_PROVIDER,
        REQUIRED_COMPILED_STDLIB_PROVIDERS,
        native_builtin_dispatch_owns,
        native_builtin_import_from_owns,
    )

    assert "weakref" in NATIVE_BUILTIN_IMPORTS_WITH_COMPILED_PROVIDER
    assert "weakref" in REQUIRED_COMPILED_STDLIB_PROVIDERS
    for name in ("ref", "proxy", "WeakValueDictionary", "WeakKeyDictionary"):
        assert native_builtin_dispatch_owns("weakref", name)
        assert native_builtin_import_from_owns("weakref", name)
    assert not native_builtin_dispatch_owns("weakref", "WeakSet")
    assert not native_builtin_import_from_owns("weakref", "WeakSet")
    assert not native_builtin_import_from_owns("pickle", "dumps")
