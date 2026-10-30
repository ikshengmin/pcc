"""Lifetime regressions and IR sizing for an inactive statement-root trial."""
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from pcc.frontends.python import type_infer
from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.codegen import generator_lowering, layer1_entrypoints
from pcc.frontends.python.py_lift import parse_and_lift
from tests.owned_ir_validation import verify_ir_text


PROGRAM = '''import gc
import weakref
events = []
class Item:
    def __init__(self, label):
        self.label = label
    def __del__(self):
        events.append(self.label)
        gc.collect()
def keep(value):
    gc.collect()
    return value
def fail(value):
    gc.collect()
    raise ValueError("selected")
def produce(value):
    first = keep((value, "tag"))
    second = {"value": first, "number": len(first)}
    gc.collect()
    yield second
    try:
        failed = fail(Item("temporary"))
    except ValueError as error:
        assert str(error) == "selected"
    third = keep(first)
    yield third
def main():
    value = Item("kept")
    reference = weakref.ref(value)
    stream = produce(value)
    first = next(stream)
    del value
    gc.collect()
    assert first["value"][0] is reference()
    second = next(stream)
    assert second is first["value"]
    assert events == ["temporary"]
    stream.close()
    del stream
    del first
    del second
    gc.collect()
    assert reference() is None
    assert events == ["temporary", "kept"]
    print("STATEMENT_ROOTS_OK")
main()
'''


VTHREAD_PROGRAM = '''import gc
import weakref
import pcc.virtual_thread as vt
events = []
class Item:
    def __init__(self, label):
        self.label = label
    def __del__(self):
        events.append(self.label)
        gc.collect()
def fail():
    gc.collect()
    raise ValueError("selected")
def child(value):
    vt.yield_now()
    gc.collect()
    return [value]
def parent(value):
    try:
        failed = vt.spawn(child, [Item("temporary"), fail()])
    except ValueError as error:
        assert str(error) == "selected"
    assert events == ["temporary"]
    child_thread = vt.spawn(child, value)
    data = {"child": child_thread, "value": [value]}
    try:
        vt.join(child_thread)
        gc.collect()
        assert vt.result(child_thread)[0] is value
        return data
    finally:
        vt.yield_now()
        gc.collect()
def main():
    value = Item("kept")
    reference = weakref.ref(value)
    thread = vt.spawn(parent, value)
    vt.run(1, 128)
    data = vt.result(thread)
    assert data["value"][0] is value
    assert vt.result(data["child"])[0] is value
    del value
    del data
    del thread
    gc.collect()
    assert reference() is None
    assert events == ["temporary", "kept"]
    print("VTHREAD_STATEMENT_ROOTS_OK")
main()
'''


def _emit(source, *, diagnostic=False):
    module = type_infer.infer_module(parse_and_lift(source, "statement_roots.py", "statement_roots"))
    codegen = L1CodeGen(module, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    with pytest.MonkeyPatch.context() as context:
        if diagnostic:
            context.setattr(
                layer1_entrypoints, "synchronous_generator_statement_context",
                generator_lowering.diagnostic_synchronous_generator_statement_context,
            )
        text = 'target triple = "arm64-apple-darwin"\n' + str(codegen.generate(module))
    verify_ir_text(text)
    return text


def _frame_sizes(text):
    return [int(value) for value in re.findall(r'\bcall [^\n]*@py_gen_(?:inline_)?frame_new\(i64 (\d+)\)', text)]


def test_diagnostic_statement_policy_reduces_actual_heap_frame():
    after = _emit(VTHREAD_PROGRAM, diagnostic=True)
    before = _emit(VTHREAD_PROGRAM)
    old, new = _frame_sizes(before), _frame_sizes(after)
    assert old and len(old) == len(new)
    assert sum(new) < sum(old), (old, new)
    # The named state still has a continuation frame and actual yield/resume.
    assert "@py_gen_frame_set(" in after and "@py_gen_frame_take(" in after
    assert "@pcc_gc_frame_enter_lifo" in after


def test_default_statement_policy_matches_prior_emitted_ir(monkeypatch):
    actual = _emit(VTHREAD_PROGRAM)
    monkeypatch.setattr(layer1_entrypoints, "synchronous_generator_statement_context", lambda *args: None)
    assert actual == _emit(VTHREAD_PROGRAM)


def test_wrong_suspension_proof_fails_closed(monkeypatch):
    from pcc.frontends.python.codegen import generator_lowering
    from pcc.frontends.python.codegen.errors import L1CodegenError

    def unsafe_context(host, stmt):
        if generator_lowering._spill_function(host) is not None:
            return host._generator_ctx_stack[-1]
        return None
    monkeypatch.setattr(layer1_entrypoints, "synchronous_generator_statement_context", unsafe_context)
    with pytest.raises(L1CodegenError, match="synchronous operand-root statement emitted a suspension"):
        _emit(VTHREAD_PROGRAM)


def test_suspending_operands_keep_their_persistent_owner():
    source = '''def take(first, second):
    return first, second
def produce(value):
    result = take(value, (yield "ready"))
    yield result
'''
    after = _emit(source, diagnostic=True)
    before = _emit(source)
    assert _frame_sizes(after) == _frame_sizes(before)


def test_native_generator_statement_lifetimes_all_collectors(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    source = tmp_path / "statement_roots.py"
    binary = tmp_path / "statement_roots"
    source.write_text(PROGRAM)
    expected = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=15)
    assert (expected.returncode, expected.stdout, expected.stderr) == (0, "STATEMENT_ROOTS_OK\n", "")
    python_program_compiler(str(source), str(binary), backend="self", libpython_mode="off",
                            ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (result.returncode, result.stdout, result.stderr) == (
            0, expected.stdout, "",
        ), backend


def test_native_vthread_statement_lifetimes_all_collectors(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    source = tmp_path / "vthread_statement_roots.py"
    binary = tmp_path / "vthread_statement_roots"
    source.write_text(VTHREAD_PROGRAM)
    python_program_compiler(str(source), str(binary), backend="self", libpython_mode="off",
                            ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (result.returncode, result.stdout, result.stderr) == (
            0, "VTHREAD_STATEMENT_ROOTS_OK\n", "",
        ), backend
