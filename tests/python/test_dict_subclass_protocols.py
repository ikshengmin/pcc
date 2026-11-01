"""Instances of a ``dict`` subclass behave as dicts (Python data model 3.3).

CPython's ``dict`` subclasses inherit ``dict.__init__``, ``__iter__``,
``__repr__`` and ``__eq__`` and are instances of ``dict`` (built-in functions:
isinstance/issubclass). pcc keeps their items in a backing dict; ``dict`` is
not an entry of their MRO, so isinstance, issubclass, construction and
special-method lookup consult the class's dict-subclass flag. The program runs
under all five collectors and must print exactly CPython 3.15's output.
"""

import os
import subprocess
import textwrap

import pytest

from pcc.frontends.python.pipeline import compile_python


_PROGRAM = textwrap.dedent("""
    import json


    class MyDict(dict):
        pass


    class Child(MyDict):
        def total(self):
            return sum(self.values())


    def attempt(label, fn):
        try:
            print(label, fn())
        except Exception as exc:
            print(label, "raised", type(exc).__name__, exc)


    empty = MyDict()
    empty["a"] = 1
    attempt("setitem-get", lambda: (empty["a"], len(empty), "a" in empty))
    attempt("isinstance", lambda: (isinstance(empty, dict), isinstance(Child(), dict),
                                   isinstance(empty, list), isinstance({}, MyDict)))
    attempt("issubclass", lambda: (issubclass(MyDict, dict), issubclass(Child, dict),
                                   issubclass(dict, MyDict)))
    attempt("kwargs", lambda: (lambda m: (len(m), m.get("a")))(MyDict(a=1)))
    attempt("mapping", lambda: (lambda m: (len(m), m.get("a")))(MyDict({"a": 1})))
    attempt("pairs", lambda: dict(MyDict([("a", 1), ("b", 2)])))
    attempt("too-many", lambda: MyDict({}, {}))
    attempt("keys", lambda: list(empty.keys()))
    attempt("items", lambda: list(empty.items()))
    attempt("iter", lambda: list(empty))
    attempt("repr", lambda: repr(empty))
    attempt("str", lambda: str(MyDict(b=2)))
    attempt("eq", lambda: (empty == {"a": 1}, {"a": 1} == empty, empty != {"a": 2}))
    attempt("child", lambda: (Child(x=3, y=4).total(), sorted(Child(x=3))))
    attempt("json", lambda: json.dumps({"outer": MyDict(a=1, b=[1, 2])}, sort_keys=True))


    def typed(d: MyDict) -> bool:
        return isinstance(d, dict)


    attempt("typed", lambda: typed(MyDict()))
""")

_EXPECTED = textwrap.dedent("""\
    setitem-get (1, 1, True)
    isinstance (True, True, False, False)
    issubclass (True, True, False)
    kwargs (1, 1)
    mapping (1, 1)
    pairs {'a': 1, 'b': 2}
    too-many raised TypeError dict expected at most 1 argument, got 2
    keys ['a']
    items [('a', 1)]
    iter ['a']
    repr {'a': 1}
    str {'b': 2}
    eq (True, True, True)
    child (7, ['x'])
    json {"outer": {"a": 1, "b": [1, 2]}}
    typed True
""")


@pytest.fixture(scope="module")
def dict_subclass_program(tmp_path_factory):
    work = tmp_path_factory.mktemp("dict_subclass")
    source = work / "dict_subclass.py"
    source.write_text(_PROGRAM, encoding="utf-8")
    executable = work / "dict_subclass.out"
    compile_python(str(source), str(executable), backend="self",
                   ir_scaffold_mode="on")
    return executable


@pytest.mark.parametrize("backend", range(5), ids=lambda backend: f"gc{backend}")
def test_dict_subclass_protocols_match_cpython(dict_subclass_program, backend):
    run = subprocess.run(
        [str(dict_subclass_program)], capture_output=True, text=True, timeout=60,
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
    )
    assert (run.returncode, run.stderr) == (0, "")
    assert run.stdout == _EXPECTED
