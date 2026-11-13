"""Refcount prepares decide PCC_DEBUG_RUNTIME from the cached word.

Every incref and decref prepare asks whether the debug runtime refuses a
value without managed provenance (py_obj._debug_refuses_unmanaged). With
the cache known to be off (0, filled by the first allocation) that answer
is one load: no call into freestanding_runtime_debug, whose cross-object
call ran on every refcount transaction. An unfilled (-1) or enabled (1)
cache keeps the original question, and an enabled debug runtime still
refuses an unmanaged value before its header is read.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PY_OBJ = ROOT / "pcc/runtime/py/py_obj.py"


class Model:
    def __init__(self, cache, enabled, managed):
        self.cache = cache
        self.enabled = enabled
        self.managed = managed
        self.calls = []
        self.words = {}

    def load_i32(self, address, offset):
        assert address == "pcc_debug_runtime_enabled_cache" and offset == 0
        return self.cache

    def enabled_query(self):
        self.calls.append("enabled")
        return self.enabled

    def can_have_header(self, value):
        self.calls.append("provenance")
        return self.managed

    def store_i64(self, address, offset, value):
        self.words[(address, offset)] = value


def _refuses(model):
    namespace = {
        "load_i32": model.load_i32,
        "global_addr": lambda name: name,
        "_pcc_debug_runtime_enabled": model.enabled_query,
        "_ptr_can_have_header": model.can_have_header,
        "store_i64": model.store_i64,
    }
    tree = ast.parse(PY_OBJ.read_text())
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name == "_debug_refuses_unmanaged"]
    assert len(nodes) == 1
    exec(compile(ast.Module(nodes, []), str(PY_OBJ), "exec"), namespace)
    return namespace["_debug_refuses_unmanaged"]


def test_cached_off_answers_without_a_call():
    model = Model(cache=0, enabled=0, managed=False)
    assert _refuses(model)(object(), "prepared") == 0
    assert model.calls == [] and model.words == {}


@pytest.mark.parametrize("cache", (-1, 1))
def test_unfilled_or_enabled_cache_keeps_the_query(cache):
    model = Model(cache=cache, enabled=1, managed=False)
    assert _refuses(model)(object(), "prepared") == 1
    assert model.calls == ["enabled", "provenance"]
    assert model.words == {("prepared", 8): -1, ("prepared", 48): 1}
    model = Model(cache=cache, enabled=1, managed=True)
    assert _refuses(model)(object(), "prepared") == 0
    model = Model(cache=cache, enabled=0, managed=False)
    assert _refuses(model)(object(), "prepared") == 0
    assert model.calls == ["enabled"]
