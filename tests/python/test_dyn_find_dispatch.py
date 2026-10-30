"""Real host frontend/IR regression only; never emit or execute native objects."""

import hashlib
import json
import os
from pathlib import Path
import re

import pytest

from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.py_ast import FuncDef, Return
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module
from tests.python.test_bound_find_protocol import PROGRAMS


def _strict_find_ir(source, module_name, tmp_path):
    """Compile the complete source through strict lowering, without emission."""
    from tests.owned_ir_validation import verify_ir_text

    module = infer_module(parse_and_lift(source, module_name + ".py", module_name))
    codegen = L1CodeGen(module, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    text = str(codegen.generate(module))
    (tmp_path / (module_name + ".py.txt")).write_text(source, encoding="utf-8")
    (tmp_path / (module_name + ".ll")).write_text(text, encoding="utf-8")
    verify_ir_text(text)
    function = codegen.functions["main"]
    definition = re.search(
        r'^define [^\n]*@"?' + re.escape(function.name) + r'"?\([^\n]*\{.*?^}',
        text, re.M | re.S,
    )
    assert definition is not None, function.name
    main = definition.group(0)
    assert "strict.nolib.stub" not in main, main
    assert not re.search(r'\bcall\b[^\n]*@"?py_cpy_', main), main
    assert codegen._strict_first_cpy_call(function) is None
    return main


@pytest.mark.parametrize("operands", ("value, needle", "value, needle, 2", "value, needle, 2, 5"))
def test_unbound_str_find_uses_native_descriptor(operands, tmp_path):
    source = (
        "from typing import Any\n"
        "def main(value: Any, needle: Any):\n"
        "    return str.find(" + operands + ")\n"
    )
    main = _strict_find_ir(source, "unbound_str_find", tmp_path)
    assert re.search(r'\bcall\b[^\n]*@"?py_obj_getattr\(', main), main
    assert re.search(r'\bcall\b[^\n]*@"?py_obj_call_slots\(', main), main


@pytest.mark.parametrize("case", PROGRAMS)
def test_complete_bound_find_native_corpus_has_no_main_stub(case, tmp_path):
    # Reuse every unchanged native fixture, including its module-level main()
    # call. This is host IR evidence only: it cannot replace native execution,
    # the aggregate entry, or exit/stdout/stderr checks under all five GCs.
    _strict_find_ir(PROGRAMS[case], "bound_find_" + case, tmp_path)


def test_find_dispatch_reachable_from_static_and_dynamic_receivers(tmp_path):
    output = Path(os.environ.get("PCC_FIND_EVIDENCE", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    cases = []
    for receiver_type in ("bytes", "str", "Any-bytes", "Any-str"):
        dynamic = receiver_type.startswith("Any-")
        actual_type = receiver_type.removeprefix("Any-")
        annotation = "Any" if dynamic else actual_type
        receiver = b"abcabc" if actual_type == "bytes" else "abcabc"
        needle = b"bc" if actual_type == "bytes" else "bc"
        for arity in (1, 2, 3):
            label = receiver_type.replace("-", "_") + "_" + str(arity)
            operands = ("needle", "needle, start", "needle, start, end")[arity - 1]
            source = (
                "from typing import Any\n"
                "import threading\n\n"
                f"def selected(receiver: {annotation}, needle: {annotation}, start: int, end: int) -> int:\n"
                f"    return receiver.find({operands})\n\n"
                "def reachable() -> int:\n"
                f"    return selected({receiver!r}, {needle!r}, 2, 6)\n"
            )
            module = infer_module(parse_and_lift(source, label + ".py", "find_repro"))
            function = next(node for node in module.body if isinstance(node, FuncDef) and node.name == "selected")
            expression = next(node.value for node in function.body if isinstance(node, Return))
            codegen = L1CodeGen(module, ir_scaffold_mode="on")
            codegen._strict_no_libpython = True
            codegen._prefer_native_callable_values = True
            text = codegen.generate(module)
            assert isinstance(text, str)
            calls = re.findall(r'\bcall\b[^\n]*@"?(py_(?:(?:str|bytes)_find(?:_range)?|obj_load_method|obj_call_(?:loaded_)?method))"?\(', text)
            expected = ("ordinary-method" if dynamic else
                        "py_bytes_find_range" if actual_type == "bytes" else
                        "py_str_find" if arity == 1 else "py_str_find_range")
            python_args = (needle,) if arity == 1 else ((needle, 2) if arity == 2 else (needle, 2, 6))
            case = {
                "case": label, "arity": arity, "actual_receiver": actual_type,
                "receiver_annotation": annotation,
                "inferred_receiver": type(expression.func.obj.ty).__name__,
                "inferred_needle": type(expression.args[0].ty).__name__,
                "inferred_bounds": [type(arg.ty).__name__ for arg in expression.args[1:]],
                "calls": calls, "expected_runtime": expected,
                "cpython_reference": receiver.find(*python_args),
                "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "ir_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "threads_enabled": codegen._runtime_threads_enabled,
            }
            (output / (label + ".py.txt")).write_text(source)
            (output / (label + ".ll")).write_text(text)
            cases.append(case)
    mismatches = []
    for case in cases:
        if case["expected_runtime"] == "ordinary-method":
            correct = case["calls"] in (
                ["py_obj_load_method", "py_obj_call_method"],
                ["py_obj_load_method", "py_obj_call_loaded_method"],
            )
        else:
            correct = case["calls"] == [case["expected_runtime"]]
        if not correct:
            mismatches.append(case)
    (output / "results.json").write_text(json.dumps({
        "scope": "Actual host parse/lift/infer/L1CodeGen.generate; IR only. No native executable, object, linker, runtime or collector run.",
        "cases": cases, "mismatches": mismatches,
    }, indent=2) + "\n")
    assert not mismatches, [(c["case"], c["inferred_receiver"], c["calls"], c["expected_runtime"]) for c in mismatches]
