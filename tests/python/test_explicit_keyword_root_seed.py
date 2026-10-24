"""Explicit keyword groups already own the private dictionary a call needs."""

import os
import re
import subprocess
import sys

import pytest

from tests.python.test_shared_call_binding import _emit, _function
from tests.owned_ir_validation import verify_ir_text


@pytest.mark.parametrize("arguments, merges", (
    ("second=2, first=1", 0),
    ("first=1, **extra", 1),
    ("first=1, **extra, last=3", 2),
    ("**extra", 1),
    ("**extra, last=3", 2),
))
def test_first_explicit_keyword_group_needs_no_empty_merge(arguments, merges):
    text = _emit("def target(**values):\n    return values\n"
                 "def probe(extra):\n    return target(" + arguments + ")\n")
    verify_ir_text(text)
    body = _function(text)
    calls = re.findall(r"\bcall\b[^\n]*@py_call_merge_kwargs_for_call\(", body)
    assert len(calls) == merges
    assert re.search(r"\bcall\b[^\n]*@py_obj_call_slots\(", body)


PROGRAM = '''import gc
events = []
dead = []
class Value:
    def __init__(self, name):
        self.name = name
    def __del__(self):
        dead.append(self.name)
def capture(**values):
    return values
def mark(name, value):
    events.append(name)
    gc.collect()
    return value
def call_explicit(fn, value):
    return fn(second=mark("second", value), first=mark("first", value))
class Mapping:
    def keys(self):
        events.append("keys")
        return ["middle"]
    def __getitem__(self, key):
        events.append("getitem:" + key)
        return 20
def mixed(fn):
    return fn(first=mark("first", 10), **Mapping(), last=mark("last", 30))
def duplicate(fn):
    return fn(first=mark("first", 1), **{"first": mark("duplicate", 2)}, last=mark("late", 3))
def duplicate_after_mapping(fn):
    return fn(**{"first": 1}, first=mark("duplicate", 2), last=mark("last", 3))
def fail():
    events.append("failure")
    raise LookupError("argument failed")
def failed_explicit(fn):
    return fn(first=Value("temporary"), second=fail())
def mapping_copy(fn, source):
    return fn(first=1, **source)
def positional_only(value, /):
    return value
def main():
    value = Value("held")
    result = call_explicit(capture, value)
    assert list(result) == ["second", "first"]
    assert result["first"] is value and result["second"] is value
    assert events == ["second", "first"]
    del value
    gc.collect()
    assert dead == []
    del result
    gc.collect()
    assert dead == ["held"]
    events.clear()
    result = mixed(capture)
    assert result == {"first": 10, "middle": 20, "last": 30}
    assert list(result) == ["first", "middle", "last"]
    assert events == ["first", "keys", "getitem:middle", "last"]
    events.clear()
    try:
        duplicate(capture)
    except TypeError:
        pass
    else:
        assert False
    assert events == ["first", "duplicate"]
    events.clear()
    try:
        duplicate_after_mapping(capture)
    except TypeError:
        pass
    else:
        assert False
    assert events == ["duplicate", "last"]
    events.clear()
    try:
        failed_explicit(capture)
    except LookupError as error:
        assert str(error) == "argument failed"
    else:
        assert False
    gc.collect()
    assert dead == ["held", "temporary"]
    assert events == ["failure"]
    source = {"second": 2}
    result = mapping_copy(capture, source)
    assert result == {"first": 1, "second": 2}
    assert source == {"second": 2}
    source["second"] = 9
    assert result["second"] == 2
    try:
        positional_only(value=1)
    except TypeError:
        pass
    else:
        assert False
    print("KEYWORD_ROOT_SEED_OK")
main()
'''


def test_keyword_seed_reference(tmp_path):
    source = tmp_path / "reference.py"
    source.write_text(PROGRAM)
    result = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "KEYWORD_ROOT_SEED_OK\n"


@pytest.mark.integration
def test_explicit_keyword_seed_native_all_gc(tmp_path, monkeypatch, threaded_pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    monkeypatch.delenv("PCC_PYTHON_IR_PASSES", raising=False)
    source = tmp_path / "keyword_seed.py"
    source.write_text(PROGRAM)
    binary = source.with_suffix("")
    compile_python(str(source), str(binary), backend="self", libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for gc in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                                env=dict(os.environ, PCC_GC_BACKEND=str(gc), PATH="/nonexistent"))
        (tmp_path / ("gc" + str(gc) + ".stdout")).write_text(result.stdout)
        (tmp_path / ("gc" + str(gc) + ".stderr")).write_text(result.stderr)
        assert result.returncode == 0, (gc, result.stdout, result.stderr)
        assert result.stdout == "KEYWORD_ROOT_SEED_OK\n"


PARKING_PROGRAM = """import gc
import pcc.virtual_thread as vt
events = []
def pause(value):
    events.append("pause")
    vt.yield_now()
    gc.collect()
    return value
def capture(**values):
    return values
def worker():
    value = [7]
    result = capture(first=value, second=pause(value))
    assert result["first"] is value and result["second"] is value
    result["first"].append(9)
    assert result["second"] == [7, 9]
    gc.collect()
    assert events == ["pause"]
    return 1
def main():
    task = vt.spawn(worker)
    vt.run(1, 128)
    assert vt.result(task) == 1
    print("KEYWORD_PARKING_OK")
main()
"""


@pytest.mark.integration
def test_explicit_keyword_group_survives_parking_all_gc(tmp_path, threaded_pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "keyword_parking.py"
    source.write_text(PARKING_PROGRAM)
    binary = source.with_suffix("")
    compile_python(str(source), str(binary), backend="self", libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for gc in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                                env=dict(os.environ, PCC_GC_BACKEND=str(gc), PATH="/nonexistent"))
        (tmp_path / ("gc" + str(gc) + ".stdout")).write_text(result.stdout)
        (tmp_path / ("gc" + str(gc) + ".stderr")).write_text(result.stderr)
        assert result.returncode == 0, (gc, result.stdout, result.stderr)
        assert result.stdout == "KEYWORD_PARKING_OK\n"
