"""``literal in parameter`` reads the parameter slot in the runtime.

A membership test retained and pinned its container around
py_obj_contains, then unpinned and released it: four calls and six atomics
per test, twice per string in json's _encode_str. When the needle is a
literal (permanent image storage) and the container an unchanged borrowed
parameter, py_obj_contains_borrowed reads the parameter slot itself and
protects the container only when the search can run user code: a str
searched for a str runs no gate or callback.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.pipeline import compile_python
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module


PROGRAM = r'''
import gc


class Bag:
    def __init__(self, words):
        self.words = words

    def __contains__(self, item):
        churn = [[index] * 3 for index in range(200)]
        gc.collect()
        return item in self.words and len(churn) == 200


class Broken:
    def __contains__(self, item):
        raise ValueError("no membership for " + item)


def probe(container):
    return ('"' in container, "b" in container, "zz" not in container)


def main():
    print(probe('say "hi"'), probe("abc"), probe(""))
    print(probe(["b", '"']), probe(("x", "zz")), probe({"b": 1}), probe({'"'}))
    print(probe(Bag(["b", "zz"])), probe(Bag([])))
    try:
        probe(Broken())
    except ValueError as error:
        print("ValueError", error)


main()
'''


def test_literal_in_parameter_reads_the_slot_in_the_runtime(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    typed = infer_module(parse_and_lift(PROGRAM, "membership.py", "membership"))
    generator = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
    generator._strict_no_libpython = True
    generator._prefer_native_callable_values = True
    text = str(generator.generate(typed))
    body = re.search(r"^define [^\n]*@user_membership_probe\(.*?^}", text, re.M | re.S).group(0)
    assert body.count("@py_obj_contains_borrowed(") == 3
    assert "@py_obj_contains(" not in body
    assert "membership.rhs.retain" not in body


@pytest.mark.integration
def test_literal_membership_matches_cpython_on_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "membership.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=120)
    assert expected.returncode == 0, expected.stderr
    executable = tmp_path / "membership"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected.stdout, ""), backend
