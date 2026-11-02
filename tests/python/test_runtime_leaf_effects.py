"""A runtime leaf is a checked call/CFG property, not a readonly hint."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.codegen.errors import L1CodegenError
from pcc.frontends.python.codegen.runtime_call_effects import (
    verify_no_safepoint_runtime_function,
)
from pcc.frontends.python.pipeline import compile_python
from pcc.ir.compat import ir


def _function(text, name):
    match = re.search(r"define [^@]*@" + name + r"\(.*?\n}\n", text, re.S)
    assert match, name
    return match.group(0)


def _leaf_ir(tmp_path, body):
    source = tmp_path / "leaf.py"
    source.write_text('''__pcc_runtime_port__ = True
from pcc.extern import c_abi_export, c_void, extern
from pcc.unsafe import ptr_is_null, load_i64
poll = extern("pcc_thread_safepoint", (), c_void)
@c_abi_export("py_tuple_len")
def bounded(value) -> int:
''' + body, encoding="utf-8")
    output = tmp_path / "leaf.ll"
    compile_python(str(source), str(output), emit_llvm_only=True, ir_scaffold_mode="on")
    return output.read_text(encoding="utf-8")


def test_only_admitted_bounded_leaf_omits_implicit_polls(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_IMPLICIT_THREAD_SAFEPOINTS", "1")
    text = _leaf_ir(tmp_path, '''    if ptr_is_null(value):
        return 0
    return load_i64(value, 16)

@c_abi_export("ordinary_tuple_length")
def ordinary(value) -> int:
    return load_i64(value, 16)
''')
    leaf = _function(text, "py_tuple_len")
    assert "call " not in leaf
    assert "pcc_thread_stop_requested" not in leaf
    assert "load i64" in leaf
    assert "@pcc_thread_safepoint(" in _function(text, "ordinary_tuple_length")


@pytest.mark.parametrize("body,reason", (
    ("    poll()\n    return 0\n", "contains a call"),
    ("    count: int = load_i64(value, 16)\n    while count > 0:\n        count -= 1\n    return count\n", "contains a call|cycle"),
))
def test_leaf_definition_rejects_explicit_poll_or_loop(tmp_path, body, reason):
    with pytest.raises(L1CodegenError, match=reason):
        _leaf_ir(tmp_path, body)


def test_leaf_verifier_rejects_unproved_backend_operations():
    module = ir.Module(name="leaf_test")
    i64 = ir.IntType(64)
    function = ir.Function(module, ir.FunctionType(i64, [i64, i64]), name="py_tuple_len")
    builder = ir.IRBuilder(function.append_basic_block("entry"))
    builder.ret(builder.sdiv(function.args[0], function.args[1]))
    with pytest.raises(L1CodegenError, match="unproved instruction"):
        verify_no_safepoint_runtime_function(function)


def test_leaf_verifier_rejects_call_free_cycles():
    module = ir.Module(name="cyclic_leaf")
    i64 = ir.IntType(64)
    function = ir.Function(module, ir.FunctionType(i64, []), name="py_tuple_len")
    entry = function.append_basic_block("entry")
    loop = function.append_basic_block("loop")
    builder = ir.IRBuilder(entry)
    builder.branch(loop)
    builder.position_at_end(loop)
    builder.branch(loop)
    with pytest.raises(L1CodegenError, match="cycle"):
        verify_no_safepoint_runtime_function(function)


def test_leaf_verifier_rejects_dynamic_stack_allocation():
    module = ir.Module(name="dynamic_leaf")
    i64 = ir.IntType(64)
    function = ir.Function(module, ir.FunctionType(i64, [i64]), name="py_tuple_len")
    builder = ir.IRBuilder(function.append_basic_block("entry"))
    builder.alloca(i64, size=function.args[0])
    builder.ret(ir.Constant(i64, 0))
    with pytest.raises(L1CodegenError, match="unbounded alloca"):
        verify_no_safepoint_runtime_function(function)


PROGRAM = '''from pcc import valueclass

@valueclass
class Pair:
    left: int
    right: int

def total(items: tuple[Pair, Pair]):
    result = 0
    for item in items:
        result += item.left + item.right
    return result

def main():
    print(total((Pair(1, 2), Pair(3, 4))))

main()
'''


def test_tuple_payload_iteration_uses_healed_leaf_inputs(tmp_path):
    source = tmp_path / "tuple_leaf.py"
    source.write_text(PROGRAM, encoding="utf-8")
    output = tmp_path / "tuple_leaf.ll"
    compile_python(str(source), str(output), emit_llvm_only=True)
    body = _function(output.read_text(encoding="utf-8"), "user_tuple_leaf_total")
    assert "@py_tuple_len(" in body
    assert "call.slot.leaf.argument" in body
    assert re.search(r"call.slot.leaf.argument[^\n]*@pcc_gc_load_borrowed_ptr", body)


@pytest.mark.integration
def test_leaf_tuple_iteration_matches_cpython_on_five_collectors(tmp_path, pcc_runtime_archive):
    source = tmp_path / "tuple_leaf.py"
    source.write_text(PROGRAM, encoding="utf-8")
    reference = subprocess.run([sys.executable, str(source)], capture_output=True,
                               text=True, timeout=30)
    assert reference.returncode == 0, reference.stderr
    binary = tmp_path / "tuple_leaf"
    compile_python(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                   runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (result.returncode, result.stdout, result.stderr) == (
            0, reference.stdout, reference.stderr,
        ), (backend, result.stdout, result.stderr)
