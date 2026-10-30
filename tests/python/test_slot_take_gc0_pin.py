"""A call root's object is pinned for its take without the GC0 graph lock.

Before an owned result leaves its call root as a raw argument, compiled
code pinned it: graph lock, read-barrier load, pin, unlock. The lock keeps
a moving collector from relocating the object between the load and the
pin; GC0 never relocates and the root belongs to the activation, yet every
take on four workers queued on the global lock (14% of the lock wait in
the request handler). pcc_gc_pin_slot_for_take keeps the transaction for
the moving collectors and pins directly on GC0.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import subprocess
import sys
import types

import pytest

from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.pipeline import compile_python
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_obj.py"
PINNED = 64

PROGRAM = r'''
import gc


class Box:
    def __init__(self, value):
        self.value = value

    def keep(self, item):
        return [item, self.value]


def handoff(box, index):
    return box.keep(dict.fromkeys(["a", "b"], index)), box.keep(set([index, 2]))


def main():
    box = Box("v")
    seen = []
    index = 0
    while index < 300:
        first, second = handoff(box, index)
        if index % 50 == 0:
            gc.collect()
            seen.append((first[0], sorted(second[0])))
        index += 1
    print(seen[-1], len(seen))


main()
'''


def test_take_pins_through_the_runtime_without_a_graph_lock(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    typed = infer_module(parse_and_lift(PROGRAM, "takes.py", "takes"))
    generator = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
    generator._strict_no_libpython = True
    generator._prefer_native_callable_values = True
    text = str(generator.generate(typed))
    body = re.search(r"^define [^\n]*@user_takes_handoff\(.*?^}", text, re.M | re.S).group(0)
    assert body.count("@pcc_gc_pin_slot_for_take(") == 2
    assert "@pcc_py_gc_minor_graph_lock(" not in body


class Words:
    def __init__(self, backend, initialized=1):
        self.memory = {("pcc_gc_backend_selected", 0): backend,
                       ("pcc_gc_config_initialized", 0): initialized}
        self.events = []

    def namespace(self):
        tree = ast.parse(RUNTIME.read_text())
        node = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "pcc_gc_pin_slot_for_take")
        node.decorator_list = []
        namespace = {
            "PY_FLAG_GC_PINNED": PINNED, "PYOBJECTHEADER_FLAGS_OFFSET": 12,
            "load_ptr": lambda base, offset: self.memory[(base, offset)],
            "load_i32": lambda base, offset: self.memory[(base, offset)],
            "global_addr": lambda name: name,
            "null": lambda: None,
            "ptr_is_null": lambda value: int(value is None),
            "is_tagged_int": lambda value: int(isinstance(value, int)),
            "pcc_gc_pin": lambda value: self.events.append(("pin", value)),
            "pcc_gc_load_ptr": lambda owner, slot: self.events.append("load") or self.memory[(slot, 0)],
            "pcc_py_gc_minor_graph_lock": lambda: self.events.append("lock"),
            "pcc_py_gc_minor_graph_unlock": lambda: self.events.append("unlock"),
        }
        code = compile(ast.Module([node], type_ignores=[]), str(RUNTIME), "exec")
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                namespace[const.co_name] = types.FunctionType(const, namespace)
        return namespace["pcc_gc_pin_slot_for_take"]


@pytest.mark.parametrize("prior", (0, PINNED))
def test_gc0_pins_without_the_lock_and_reports_the_prior_pin(prior):
    words = Words(backend=0)
    words.memory[("slot", 0)] = "object"
    words.memory[("object", 12)] = prior | 8
    assert words.namespace()("slot", 0) == prior
    assert words.events == [("pin", "object")]


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
def test_other_collectors_pin_the_current_object_inside_the_transaction(backend):
    words = Words(backend=backend)
    words.memory[("slot", 0)] = "object"
    words.memory[("object", 12)] = PINNED
    assert words.namespace()("slot", 0) == PINNED
    assert words.events == ["lock", "load", ("pin", "object"), "unlock"]


def test_uninitialized_configuration_takes_the_transaction():
    words = Words(backend=0, initialized=0)
    words.memory[("slot", 0)] = "object"
    words.memory[("object", 12)] = 0
    assert words.namespace()("slot", 0) == 0
    assert words.events[0] == "lock" and words.events[-1] == "unlock"


@pytest.mark.parametrize("keep", (0, 1))
def test_immediates_and_kept_pins(keep):
    words = Words(backend=0)
    words.memory[("slot", 0)] = 85
    assert words.namespace()("slot", keep) == (PINNED if keep else 0)
    assert words.events == []
    words.memory[("slot", 0)] = "object"
    words.memory[("object", 12)] = 0
    assert words.namespace()("slot", keep) == (PINNED if keep else 0)


@pytest.mark.integration
def test_taken_arguments_match_cpython_on_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "takes.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=120)
    assert expected.returncode == 0, expected.stderr
    executable = tmp_path / "takes"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected.stdout, ""), backend
