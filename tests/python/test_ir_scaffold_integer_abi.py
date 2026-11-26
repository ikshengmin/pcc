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


@pytest.mark.parametrize("contextual", [False, True])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("caller_sink", [False, True])
def test_scaffold_constant_adapts_raw_value_with_imprecise_semantic_type_once(tmp_path, monkeypatch, contextual, nested, caller_sink):
    from pcc.frontends.python.py_ast import DynType, FuncDef, Return

    monkeypatch.setenv('PCC_PYTHON_IR_PASSES', 'off')
    monkeypatch.setenv('PCC_DIRECT_INDEXED_KERNEL_EMIT', '0')
    monkeypatch.setenv('PCC_DIRECT_INDEXED_KERNEL_CAPTURE', '0')
    provider = Path(__file__).resolve().parents[2] / 'pcc/ir/ir.py'
    source = tmp_path / 'probe.py'
    source.write_text('from typing import Any\nfrom pcc.unsafe import null\nfrom pcc.ir.compat import ir\n'
                      'def take(*, value, **extras):\n    return value\n'
                      'def probe(operand: Any, values: list[int]):\n'
                      '    return ' + ('take(value=' if caller_sink else '') + ('[' if nested else '')
                      + 'ir.Constant(operand, len(values))' + (']' if nested else '')
                      + (')' if caller_sink else '') + '\n')
    modules, exports, derived = build_closed_world_context([str(provider), str(source)], ['pcc.ir.ir', 'probe'])
    typed = infer_module(modules[1], external_exports=exports, derived_class_map=derived)
    function = next(node for node in typed.body if isinstance(node, FuncDef) and node.name == 'probe')
    returned = next(node for node in function.body if isinstance(node, Return))
    # Model the admitted imprecise expression type while retaining the real
    # len emitter and its machine-return ABI; never rewrite the replay input.
    result_expr = returned.value.kwargs[0][1] if caller_sink else returned.value
    constant = result_expr.elems[0] if nested else result_expr
    object.__setattr__(constant.args[1], 'ty', DynType(name='dyn'))
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode='on')
    codegen._native_module_exports = exports if contextual else None
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

    evidence = _assert_scaffold_constant_owner_protocol(text, nested or caller_sink)
    if not (contextual or nested or caller_sink):
        # Mutate the real successful lowering to prove the model detects
        # missing publication, double release and missing preserved cleanup.
        result, output, boxed, ready = evidence
        store = "  store ptr %" + result + ", ptr %" + output + "\n"
        assert body.count(store) == 1
        boxed_text = boxed if boxed.startswith("%") else "%" + boxed
        release = re.search(
            r"^" + re.escape(ready) + r":\n(  call[^\n]*@pcc_gc_release(?:_known)?\(ptr " + re.escape(boxed_text) + r"\)\n)",
            body, re.M,
        )
        assert release is not None
        cleanup = re.search(
            r"^  call[^\n]*@py_cleanup_one_root_preserving_exception\(ptr %" + re.escape(output) + r"\)\n",
            body, re.M,
        )
        assert cleanup is not None
        mutations = (
            body.replace(store, "", 1),
            body.replace(release.group(0), release.group(0) + release.group(1), 1),
            body.replace(cleanup.group(0), "", 1),
        )
        for mutant in mutations:
            assert mutant != body
            with pytest.raises(AssertionError):
                _assert_scaffold_constant_owner_protocol(text.replace(body, mutant, 1), False)
    assert not re.search(r"\bcall [^\n]*@py_cpy_", body)


def _assert_scaffold_constant_owner_protocol(text, caller_owned_sink):
    """Check the actual CFG and physical slot, not retired pin block names."""
    from tests.owned_ir_validation import verify_ir_text
    from tests.python.iterator_root_contract import _dominated, parsed_root_contract

    module = verify_ir_text(text)
    function = next(fn for fn in module.functions if fn.name == "user_probe_probe")
    roots = parsed_root_contract(module, function)
    result_calls = [row for row in roots.rows
                    if roots.called(row[2], "user_pcc_ir_ir_scaffold_Constant_i64")]
    assert len(result_calls) == 1
    call = result_calls[0]
    block, index, producer = call
    borrowed, boxed = roots.args(producer)
    result = producer.data[0]
    publication = block.instructions[index + 1]
    # Plain activation-local publication cannot fail or park: the NEW
    # provider result's very next instruction is a store to its live root.
    assert publication.kind == "store" and publication.data[0].is_ptr
    assert publication.data[1] == result
    output = roots.slot(publication.data[3])
    roots.require_owning(output, allow_lifo=True)
    entries = [row for row in roots.rows
               if roots.called(row[2], "pcc_gc_frame_enter_lifo")
               and roots.slot(roots.args(row[2])[1]) == output]
    assert len(entries) == 1 and _dominated(roots, entries[0], call)
    assert roots.frame_abi(entries[0][2])[1], "result root is activation-local"

    # The first parameter is borrowed, the single boxed len result is NEW.
    # No pin protocol is needed for either; only the boxed owner is released.
    borrowed_read = roots.definitions[borrowed]
    assert roots.called(borrowed_read, "pcc_gc_load_borrowed_ptr")
    borrowed_cell = roots.slot(roots.args(borrowed_read)[1])
    borrowed_entries = []
    for row in roots.rows:
        ins = row[2]
        if not roots.called(ins, "pcc_gc_frame_enter"):
            continue
        base, offset = roots.slot(roots.args(ins)[1])
        descriptor = roots.globals[roots.symbol(roots.args(ins)[0]).lstrip("@")]
        count = int(descriptor.initializer)
        if count < 0 and base == borrowed_cell[0] and offset <= borrowed_cell[1] < offset - count * roots.POINTER_BYTES:
            borrowed_entries.append(row)
    assert len(borrowed_entries) == 1 and _dominated(roots, borrowed_entries[0], call), (
        "borrowed parameter must be in its registered borrowed group"
    )
    assert roots.called(roots.definitions[boxed], "py_int_from_i64")
    assert not any(ins.kind == "call" and ins.data[2] in (
        "pcc_gc_pin", "pcc_gc_unpin", "pcc_gc_pin_slot_for_take", "pcc_gc_take_pinned_slot",
    ) for _, _, ins in roots.rows)

    # The real pending-error test after immediate publication determines the
    # two paths. A dead former publication-failure label proves nothing.
    status = block.instructions[index + 2]
    comparison = block.instructions[index + 3]
    assert roots.called(status, "py_err_occurred")
    assert comparison.kind == "icmp" and comparison.data[0] == "ne"
    assert tuple(comparison.data[3:]) == (status.data[0], "0")
    assert block.terminator.kind == "br_cond" and block.terminator.data[0] == comparison.data[1]
    error_start, ready_start = block.terminator.data[1:]
    blocks = {item.name: item for item in roots.blocks}

    def release_of(ins, value):
        return ins.kind == "call" and ins.data[2] in ("pcc_gc_release", "pcc_gc_release_known") and roots.args(ins) == [value]

    # Success reloads or takes the exact result cell after releasing the
    # boxed length; the nested/caller-owned sink must not take another owner.
    ready = blocks[ready_start]
    releases = [i for i, ins in enumerate(ready.instructions) if release_of(ins, boxed)]
    assert len(releases) == 1
    uses = []
    for i, ins in enumerate(ready.instructions):
        if caller_owned_sink and ins.kind == "load" and roots.slot(ins.data[3]) == output:
            uses.append(i)
        if not caller_owned_sink and roots.called(ins, "pcc_gc_take_slot") and roots.slot(roots.args(ins)[0]) == output:
            uses.append(i)
    assert uses and releases[0] < min(uses)
    if caller_owned_sink:
        assert not any(roots.called(ins, "pcc_gc_take_slot") and roots.slot(roots.args(ins)[0]) == output
                       for ins in ready.instructions)
    else:
        assert len(uses) == 1

    def prove_exits(start, pending_error):
        pending = [(start, 0, False, False)]
        visited = set()
        returned = 0
        while pending:
            state = pending.pop()
            if state in visited:
                continue
            visited.add(state)
            name, released, cleared, left = state
            current = blocks[name]
            for ins in current.instructions:
                if release_of(ins, boxed):
                    released += 1
                    assert released == 1, "boxed owner released more than once"
                assert not release_of(ins, borrowed), "borrowed argument must not be released"
                if (ins.kind == "call" and ins.data[2].startswith("py_cleanup_")
                        and ins.data[2].endswith("_preserving_exception")
                        and any(roots.slot(arg) == output for arg in roots.args(ins))):
                    assert not left and released == 1
                    cleared = True
                abi = roots.frame_abi(ins)
                if abi is not None and "frame_leave" in abi[0] and roots.slot(roots.args(ins)[0]) == output:
                    assert not left
                    if pending_error:
                        assert cleared, "pending error must clear exact result preserving exception"
                    left = True
            term = current.terminator
            if term.kind in ("ret", "ret_void"):
                assert released == 1 and left, "every provider exit retires the boxed and result owners"
                if pending_error:
                    assert cleared and term.kind == "ret" and term.data[-1] == "null"
                returned += 1
                continue
            successors = []
            if term.kind == "br":
                successors = [term.data[0]]
            elif term.kind == "br_cond":
                successors = list(term.data[1:])
            elif term.kind == "switch":
                successors = [term.data[2], *(target for _, target in term.data[3])]
            pending.extend((target, released, cleared, left) for target in successors)
        assert returned

    prove_exits(error_start, True)
    prove_exits(ready_start, False)
    roots.assert_frame_exits(exact_abi=True)
    return result, publication.data[3], boxed, ready_start


@pytest.mark.parametrize("manual", [False, True])
def test_imprecise_constant_rejects_raw_or_manual_provider_result(tmp_path, monkeypatch, manual):
    from pcc.frontends.python.codegen.errors import L1CodegenError
    from pcc.frontends.python.py_ast import DynType, FuncDef, Return

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
    provider = tmp_path / "provider.py"
    provider.write_text("from pcc.extern import c_ptr\nfrom pcc.unsafe import int_to_ptr\n"
                        + ("__pcc_runtime_port__ = True\n" if manual else "")
                        + "def scaffold_Constant_i64(ty, value: int)"
                        + ("" if manual else " -> c_ptr")
                        + ":\n    return int_to_ptr(value)\n")
    entry = tmp_path / "probe.py"
    entry.write_text("from pcc.ir.compat import ir\n"
                     "def take(*, value, **extras):\n    return value\n"
                     "def probe(operand, values: list[int]):\n"
                     "    return take(value=ir.Constant(operand, len(values)))\n")
    modules, exports, derived = build_closed_world_context(
        [str(provider), str(entry)], ["pcc.ir.ir", "probe"])
    info = exports["pcc.ir.ir"]["scaffold_Constant_i64"]
    assert info["return_ty"] == (("dyn",) if manual else ("raw_pointer",))
    assert info["manual_pointer_abi"] is manual
    typed = infer_module(modules[1], external_exports=exports, derived_class_map=derived)
    function = next(node for node in typed.body if isinstance(node, FuncDef) and node.name == "probe")
    returned = next(node for node in function.body if isinstance(node, Return))
    constant = returned.value.kwargs[0][1]
    object.__setattr__(constant.args[1], "ty", DynType(name="dyn"))
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
    codegen._native_module_exports = exports
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    with pytest.raises(L1CodegenError, match="scaffold call has no managed result contract"):
        codegen.generate(typed)
    assert not re.search(r"\bcall [^\n]*@user_pcc_ir_ir_scaffold_Constant_i64\(", str(codegen.module))
