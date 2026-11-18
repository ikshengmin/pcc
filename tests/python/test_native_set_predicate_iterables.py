"""Set predicates accept iterables and preserve iterator short-circuiting."""

import os
import subprocess
import sys

from pcc.frontends.python.pipeline import compile_python


def test_set_predicate_iterables(tmp_path, pcc_runtime_archive):
    source = tmp_path / "set_predicates.py"
    source.write_text('''
import gc
events = []
class Key:
    def __hash__(self):
        gc.collect()
        events.append("hash")
        return 42
def values():
    events.append(1)
    gc.collect()
    yield 1
    events.append(2)
    yield 2
    events.append(3)
    raise ValueError("late")
class Collision:
    def __init__(self, label):
        self.label = label
    def __hash__(self):
        return 321
    def __eq__(self, other):
        gc.collect()
        events.append(self.label)
        if self.label == "raise":
            raise ValueError("equality callback")
        return True
class BrokenHash:
    def __hash__(self):
        gc.collect()
        raise ValueError("hash callback")
def mutates_receiver(target):
    yield "missing"
    target.add("__FILE__")
    gc.collect()
    yield "__FILE__"
    raise ValueError("read past the first match")
def main():
    builtins = frozenset(("_Pragma", "__LINE__", "__FILE__"))
    print(builtins.isdisjoint(["unchanged"]), builtins.isdisjoint(["x", "__LINE__"]))
    saved_method = builtins.isdisjoint
    print(saved_method(("__FILE__",)), saved_method(["nothing"]))
    first = {1}
    empty = set()
    print(first.isdisjoint(first), empty.isdisjoint(empty))
    print({1}.isdisjoint({2, 3}), {1, 2}.isdisjoint({2}))
    print(first.isdisjoint(values()), events)
    events.clear()
    try:
        print(empty.isdisjoint(values()))
    except ValueError:
        print("disjoint late error", events)
    events.clear()
    for other in (None, [[1]]):
        try:
            print(first.isdisjoint(other))
        except TypeError:
            print("bad disjoint input")
    left_key = Collision("small")
    right_key = Collision("large")
    small = {left_key}
    large = {right_key, 99}
    events.clear()
    print(small.isdisjoint(large), events)  # equality belongs to the larger table
    events.clear()
    print(large.isdisjoint(small), events)
    events.clear()
    try:
        print({Collision("raise")}.isdisjoint([right_key]))
    except ValueError as error:
        print(str(error), events)
    try:
        print(first.isdisjoint([BrokenHash()]))
    except ValueError as error:
        print(str(error))
    events.clear()
    changed = {"spare"}
    print(changed.isdisjoint(mutates_receiver(changed)), "__FILE__" in changed)
    print({"a"}.issubset({"a": 1, "b": 2}))
    print(frozenset({"a", "b"}).issubset({"a": 1}))
    print({1, 2}.issubset([1, 1, 2]), {1, 2}.issuperset((1, 1)))
    print({1}.issubset(values()), events)
    events.clear()
    print({1}.issuperset(values()), events)
    events.clear()
    print(set().issuperset(values()), events)
    events.clear()
    try:
        print(set().issubset(values()))
    except ValueError:
        print("late error", events)
    for other in [42, [[1]]]:
        try:
            print({1}.issubset(other))
        except TypeError:
            print("bad subset input")
        try:
            print({1}.issuperset(other))
        except TypeError:
            print("bad superset input")
    print({1}.issubset({1, 2}), {1, 2}.issuperset({1}))
    key = Key()
    left = {key}
    right = {key}
    events.clear()
    print(left.isdisjoint(right), events)  # exact sets reuse stored hashes
    events.clear()
    print(left.isdisjoint([key]), events)   # a generic argument hashes the item
    events.clear()
    print(left.issubset([key]), events)
    events.clear()
    print(left.issubset(right), events)
main()
''', encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=10)
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / "set_predicates"
    compile_python(str(source), str(binary), backend="self",
                   runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert result.returncode == 0, f"GC{backend}: {result.stderr}"
        assert result.stdout == expected.stdout, f"GC{backend}: {result.stdout}"
