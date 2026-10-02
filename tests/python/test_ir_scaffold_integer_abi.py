"""Scaffold integer operands follow the defining Python helper's ABI."""
from pathlib import Path
import re

import pytest

from pcc.backend.owned_object_emit import emit_owned_object
from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.pipeline_context import build_closed_world_context
from pcc.frontends.python.type_infer import infer_module
from pcc.ir.compat import ir


CASES = [
    ("builder.load_atomic(operand, 'acquire', number)", "IRBuilder_load_atomic", 3),
    ("builder.store_atomic(operand, operand, 'release', number)", "IRBuilder_store_atomic", 4),
    ("builder.extract_value(operand, number)", "IRBuilder_extract_value", 2),
    ("builder.load(operand, align=number)", "IRBuilder_load", 3),
    ("builder.store(operand, operand, align=number)", "IRBuilder_store", 3),
    ("builder.call4_i32(operand, operand, operand, operand, number)", "IRBuilder_call4_i32", 5),
    ("ir.IntType(number)", "scaffold_IntType", 0),
    ("ir.ArrayType(operand, number)", "scaffold_ArrayType", 1),
    ("ir.Constant(operand, number)", "scaffold_Constant_i64", 1),
]


@pytest.mark.parametrize("expression,suffix,integer_index", CASES)
def test_scaffold_preserves_boxed_integer_operand(tmp_path, monkeypatch, expression, suffix, integer_index):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
    provider = Path(__file__).resolve().parents[2] / 'pcc/ir/ir.py'
    source = tmp_path / 'probe.py'
    source.write_text('from typing import Any\nfrom pcc.ir.compat import ir\n'
                      'def probe(operand: Any, number: int):\n'
                      '    builder = ir.IRBuilder()\n    return ' + expression + '\n')
    modules, exports, derived = build_closed_world_context([str(provider), str(source)], ['pcc.ir.ir', 'probe'])
    typed = infer_module(modules[1], external_exports=exports, derived_class_map=derived)
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode='on')
    codegen._native_module_exports = exports
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    text = str(codegen.generate(typed))
    (tmp_path / 'probe.ll').write_text(text)
    assert emit_owned_object(text, 'x86_64-unknown-linux-gnu')[:4] == b'\x7fELF'
    body = re.search(r'^define [^\n]*@user_probe_probe\([^\n]*\{\n([\s\S]*?)^\}', text, re.M).group(1)
    symbol = 'user_pcc_ir_ir_' + suffix
    call = re.search(r'\bcall [^\n]*@' + symbol + r'\(([^\n]*)\)', body)
    assert call is not None, body
    operands = call.group(1).split(',')
    assert operands[integer_index].strip().startswith('ptr '), call.group(0)
    assert '@py_int_to_i64_lane(' not in body, body
    assert not re.search(r'\bcall [^\n]*@py_cpy_', body)
    info = exports['pcc.ir.ir'].get(suffix)
    if info is None:
        info = next(method for method in exports['pcc.ir.ir']['IRBuilder']['methods']
                    if method['name'] == suffix[len('IRBuilder_'):])
    if info['param_types'][integer_index][0] == 'int':
        assert info['box_int_abi'] is True
    function = codegen.module.globals[symbol]
    assert isinstance(function.args[integer_index].type, ir.PointerType)


def test_scaffold_preserves_declared_machine_argument_and_return(tmp_path, monkeypatch):
    """A real runtime-marked provider keeps its explicit machine contract."""
    from pcc.frontends.python.codegen.ir_scaffold_lowering import _scaffold_emit_declared_call
    from pcc.ir.compat import ir
    from pcc.frontends.python.py_ast import IntLit, IntType, SourceSpan

    # A function context with a declared i64 operand/result is sufficient to
    # exercise the same shared argument adapter without changing any provider.
    source = tmp_path / 'machine.py'
    source.write_text('__pcc_runtime_port__ = True\ndef machine(value: int) -> int:\n    return value\n')
    modules, exports, _ = build_closed_world_context([str(source)], ['machine'])
    typed = infer_module(modules[0])
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode='on')
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
    codegen.generate(typed)
    fn = codegen.functions["machine"]
    assert fn.args[0].type == ir.IntType(64)
    assert fn.function_type.return_type == ir.IntType(64)
    caller = ir.Function(codegen.module, ir.FunctionType(ir.IntType(64), []), 'machine_caller')
    codegen.current_function = caller
    codegen.builder = ir.IRBuilder(caller.append_basic_block('entry'))
    codegen._call_user = lambda function, args, name, **kwargs: codegen.builder.call(function, args, name=name)
    expr = IntLit(SourceSpan('machine.py', 1, 0, 1, 1), IntType(name='int'), 7)
    value = _scaffold_emit_declared_call(codegen, fn.name, ir.IntType(8).as_pointer(), (expr,), (0,))
    assert value.type == ir.IntType(64)
    codegen.builder.ret(value)
    text = str(codegen.module)
    assert 'call i64 (i64) @user_machine_machine(i64 7)' in text
    caller_body = re.search(r'^define [^\n]*@machine_caller\([^\n]*\{\n([\s\S]*?)^\}', text, re.M).group(1)
    assert not re.search(r'call [^\n]*@py_int_from_i64\(', caller_body)
    assert emit_owned_object(text, 'x86_64-unknown-linux-gnu')[:4] == b'\x7fELF'


def test_scaffold_constant_adapts_raw_value_with_imprecise_semantic_type_once(tmp_path, monkeypatch):
    from pcc.frontends.python.py_ast import DynType, FuncDef, Return

    monkeypatch.setenv('PCC_PYTHON_IR_PASSES', 'off')
    monkeypatch.setenv('PCC_DIRECT_INDEXED_KERNEL_EMIT', '0')
    monkeypatch.setenv('PCC_DIRECT_INDEXED_KERNEL_CAPTURE', '0')
    provider = Path(__file__).resolve().parents[2] / 'pcc/ir/ir.py'
    source = tmp_path / 'probe.py'
    source.write_text('from typing import Any\nfrom pcc.unsafe import null\nfrom pcc.ir.compat import ir\n'
                      'def probe(operand: Any, values: list[int]):\n'
                      '    return ir.Constant(operand, len(values))\n')
    modules, exports, derived = build_closed_world_context([str(provider), str(source)], ['pcc.ir.ir', 'probe'])
    typed = infer_module(modules[1], external_exports=exports, derived_class_map=derived)
    function = next(node for node in typed.body if isinstance(node, FuncDef) and node.name == 'probe')
    returned = next(node for node in function.body if isinstance(node, Return))
    # Model the admitted imprecise expression type while retaining the real
    # len emitter and its machine-return ABI; never rewrite the replay input.
    object.__setattr__(returned.value.args[1], 'ty', DynType(name='dyn'))
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode='on')
    codegen._native_module_exports = exports
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    text = str(codegen.generate(typed))
    (tmp_path / 'raw_dynamic_constant.ll').write_text(text)
    assert emit_owned_object(text, 'x86_64-unknown-linux-gnu')[:4] == b'\x7fELF'
    body = re.search(r'^define [^\n]*@user_probe_probe\([^\n]*\{\n([\s\S]*?)^\}', text, re.M).group(1)
    lengths = re.findall(r'(%[^\s=]+) = call [^\n]*@py_list_len\(', body)
    assert len(lengths) == 1, body
    boxes = re.findall(r'(%[^\s=]+) = call [^\n]*@py_int_from_i64\(i64 ' + re.escape(lengths[0]) + r'\)', body)
    assert len(boxes) == 1, body
    assert re.search(r'@user_pcc_ir_ir_scaffold_Constant_i64\(ptr [^,]+, ptr ' + re.escape(boxes[0]) + r'\)', body), body
