"""``for k, v in x.items()`` follows CPython's for statement and assignment.

Python reference 8.3 (for) and 7.2 (assignment): items() is called on x and
its result iterated, each item unpacked into k and v. pcc walks the entries
of an exact dict, or of a dict subclass instance whose items() is dict's own,
without building pairs; any other x, including an overriding subclass and an
instance attribute named items, takes the call, lazy iteration, StopIteration
and unpack errors of CPython. Each program runs under all five collectors and
must print CPython 3.15's output.
"""

import os
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python


_ITEMS_LOOP = 'class Plain(dict):\n    pass\n\n\nclass Listed(dict):\n    def items(self):\n        return [("p", 1), ("q", 2)]\n\n\nclass Lazy(dict):\n    def items(self):\n        for key in sorted(self):\n            print("yield", key)\n            yield key, self[key] * 10\n\n\nclass Pairs:\n    def __init__(self, pairs):\n        self.pairs = pairs\n\n    def items(self):\n        return self.pairs\n\n\nclass Countdown:\n    def __init__(self, n):\n        self.n = n\n\n    def __iter__(self):\n        return self\n\n    def __next__(self):\n        if self.n == 0:\n            raise StopIteration\n        self.n -= 1\n        return ("c" + str(self.n), self.n)\n\n\nclass Iterating:\n    def items(self):\n        return Countdown(3)\n\n\nclass Broken:\n    def items(self):\n        raise KeyError("no items")\n\n\ndef walk(source):\n    out = []\n    for k, v in source.items():\n        if k == "skip":\n            continue\n        if k == "stop":\n            break\n        out.append((k, v))\n    return out\n\n\ndef attempt(label, fn):\n    try:\n        print(label, fn())\n    except Exception as exc:\n        print(label, "raised", type(exc).__name__, exc)\n\n\nattempt("dict", lambda: walk({"a": 1, "skip": 2, "b": 3, "stop": 4, "c": 5}))\nattempt("plain", lambda: walk(Plain(a=1, b=2)))\nattempt("listed", lambda: walk(Listed(z=0)))\nattempt("lazy", lambda: walk(Lazy(b=2, a=1)))\nattempt("pairs", lambda: walk(Pairs([("x", 1), ("skip", 0), ("y", 2)])))\nattempt("countdown", lambda: walk(Iterating()))\nattempt("short", lambda: walk(Pairs([(1,)])))\nattempt("long", lambda: walk(Pairs([(1, 2, 3)])))\nattempt("scalar", lambda: walk(Pairs([5])))\nattempt("string-pair", lambda: walk(Pairs(["ab", "cd"])))\nattempt("broken", lambda: walk(Broken()))\nshadowed = Plain(a=1)\nshadowed.items = lambda: [("shadow", 9)]\nattempt("shadowed", lambda: walk(shadowed))\nd = {"k": 1}\nfor key, value in d.items():\n    d2 = {key: value}\nattempt("after", lambda: (d2, key, value))\n\n\ndef body_raises():\n    total = 0\n    for k, v in {"a": 1, "b": 0}.items():\n        total += 10 // v\n    return total\n\n\nattempt("body-raises", body_raises)\n'

_ITEMS_LOOP_EXPECTED = "dict [('a', 1), ('b', 3)]\nplain [('a', 1), ('b', 2)]\nlisted [('p', 1), ('q', 2)]\nyield a\nyield b\nlazy [('a', 10), ('b', 20)]\npairs [('x', 1), ('y', 2)]\ncountdown [('c2', 2), ('c1', 1), ('c0', 0)]\nshort raised ValueError not enough values to unpack (expected 2, got 1)\nlong raised ValueError too many values to unpack (expected 2, got 3)\nscalar raised TypeError cannot unpack non-iterable int object\nstring-pair [('a', 'b'), ('c', 'd')]\nbroken raised KeyError 'no items'\nshadowed [('shadow', 9)]\nafter ({'k': 1}, 'k', 1)\nbody-raises raised ZeroDivisionError division by zero\n"


_PROGRAMS = {'items_loop': (_ITEMS_LOOP, _ITEMS_LOOP_EXPECTED)}


@pytest.fixture(scope="module")
def items_loop_programs(tmp_path_factory):
    work = tmp_path_factory.mktemp("items_loop")
    built = {}
    for name, (source, expected) in _PROGRAMS.items():
        path = work / (name + ".py")
        path.write_text(source, encoding="utf-8")
        executable = work / (name + ".out")
        compile_python(str(path), str(executable), backend="self",
                       ir_scaffold_mode="on")
        built[name] = (executable, expected)
    return built


@pytest.mark.parametrize("backend", range(5), ids=lambda backend: f"gc{backend}")
@pytest.mark.parametrize("program", sorted(_PROGRAMS))
def test_matches_cpython_output(items_loop_programs, program, backend):
    executable, expected = items_loop_programs[program]
    run = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=60,
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
    )
    assert (run.returncode, run.stderr) == (0, "")
    assert run.stdout == expected
