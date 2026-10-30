"""A str subclass hashes as str.__hash__ does, so dict and set keys agree.

str.__hash__ has no method entry in a subclass, so a str subclass instance
fell back to its identity hash while comparing equal to the str by payload:
`d[Name("a")]` missed the "a" entry, storing it added a second entry, and
deleting with it raised KeyError. A str subclass with neither __hash__ nor
__eq__ of its own now hashes its payload. A class that defines __eq__
without __hash__ is unhashable with CPython's message; it used to call the
None it stores as __hash__.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python


PROGRAM = r'''
class Name(str):
    pass


class Hashy(str):
    def __hash__(self):
        return 7


class EqOnly(str):
    def __eq__(self, other):
        return NotImplemented


def attempt(label, fn):
    try:
        print(label, fn())
    except Exception as error:
        print(label, type(error).__name__, error)


def attempt_kind(label, fn):
    # CPython 3.14 words an unhashable dict key differently from hash();
    # only the exception type is compared here.
    try:
        print(label, fn())
    except Exception as error:
        print(label, type(error).__name__)


def main():
    attempt("hash", lambda: hash(Name("a")) == hash("a"))
    d = {"a": 1}
    attempt("get", lambda: d[Name("a")])
    attempt("in", lambda: Name("a") in d)
    attempt("dict.get", lambda: d.get(Name("a"), "miss"))
    d[Name("a")] = 2
    attempt("store", lambda: (len(d), d["a"], [(type(k).__name__, v) for k, v in d.items()]))
    e = {Name("b"): 1}
    attempt("reverse", lambda: (e["b"], "b" in e))
    e["b"] = 2
    attempt("reverse store", lambda: (len(e), type(list(e)[0]).__name__, e[Name("b")]))
    s = {"k"}
    s.add(Name("k"))
    attempt("set", lambda: (len(s), Name("k") in s, type(list(s)[0]).__name__))
    attempt("fromkeys", lambda: len(dict.fromkeys(["x", Name("x"), "y"])))
    h = {}
    h.setdefault(Name("z"), 1)
    h.setdefault("z", 2)
    attempt("setdefault", lambda: (len(h), h["z"]))
    f = {"q": 1, "r": 2}
    attempt("pop", lambda: (f.pop(Name("q")), len(f)))
    del f[Name("r")]
    attempt("del", lambda: len(f))
    attempt("hashy", lambda: (hash(Hashy("w")), Hashy("w") == "w", {Hashy("w"): 1}.get("w", "miss")))
    attempt("eq only", lambda: hash(EqOnly("q")))
    attempt_kind("eq only key", lambda: {EqOnly("q"): 1})


main()
'''


@pytest.mark.integration
def test_str_subclass_hash_matches_cpython_on_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "str_subclass_hashing.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=120)
    assert expected.returncode == 0, expected.stderr
    executable = tmp_path / "str_subclass_hashing"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected.stdout, ""), backend
