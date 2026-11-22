"""Focused baselines for the remaining direct boxed-int ownership paths (#7)."""
from __future__ import annotations

import json
import os
import re

import pytest

from pcc.frontends.python.codegen.freestanding_abi_constants import PY_TYPE_INT
from tests.python.owned_regression_support import (
    assert_owned_program,
    explicit_owned_runtime,
)
from pcc.backend.self_backend_ir import PARSED_INSTRUCTION_KINDS
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from tests.python.indexed_receipt_support import decoded_module
from tests.python.process_timeout import run_process_group_timeout
from tests.python.test_cpy_result_slot_handoff import _body

FIELD = """
class Holder:
    def __init__(self, n: int):
        self.value = n

def returned(owner: Holder) -> int:
    return owner.value
"""
ITEM = """
def returned(owner: list[int]) -> int:
    return owner[0]
"""
PARSE = """
def returned(text: str) -> int:
    return int(text)
"""
RUNTIME_INDEX = """
def returned(owner: list[int], index: int) -> int:
    return owner[index]
"""
CASES = {
    "field": (FIELD, "owner = Holder(n)\n    result: object = returned(owner)\n    assert result is n"),
    "subscript": (ITEM, "owner: list[int] = [n]\n    result: object = returned(owner)\n    assert result is n"),
    "field-detached": (FIELD, "owner = Holder(n)\n    n = 0\n    result: object = returned(owner)\n    owner.value = 0\n    gc.collect()\n    assert result == 9223372036854775808"),
    "subscript-detached": (ITEM, "owner: list[int] = [n]\n    n = 0\n    result: object = returned(owner)\n    owner[0] = 0\n    gc.collect()\n    assert result == 9223372036854775808"),
    "subscript-empty": (ITEM, 'owner: list[int] = []\n    try:\n        result: object = returned(owner)\n    except IndexError:\n        pass\n    else:\n        raise AssertionError("expected IndexError")'),
    "parse": (PARSE, 'result: object = returned("9223372036854775808")\n    assert result == n and result is not n'),
    "invalid": (PARSE, 'try:\n        result: object = returned("not-an-integer")\n    except ValueError:\n        pass\n    else:\n        raise AssertionError("expected ValueError")'),
}
for name, index in (("positive", 1), ("negative", -1)):
    declaration = ITEM.replace("owner[0]", f"owner[{index}]")
    for detached in (False, True):
        setup = "owner: list[int] = [0, n]\n    "
        use = "result: object = returned(owner)\n    "
        action = (setup + "n = 0\n    " + use + "owner[1] = 0\n    "
                  + "gc.collect()\n    assert result == 9223372036854775808"
                  if detached else setup + use + "assert result is n")
        suffix = "-detached" if detached else ""
        CASES[f"nonzero-{name}{suffix}"] = declaration, action

CASES.update({
    "runtime-index-positive": (RUNTIME_INDEX,
        "owner: list[int] = [0, n]\n    result: object = returned(owner, two - 1)\n    assert result is n"),
    "runtime-index-negative-detached": (RUNTIME_INDEX,
        "owner: list[int] = [0, n]\n    n = 0\n    result: object = returned(owner, 1 - two)\n    "
        "owner[1] = 0\n    gc.collect()\n    assert result == 9223372036854775808"),
    "runtime-index-computed": (RUNTIME_INDEX.replace("owner[index]", "owner[index - 1]"),
        "owner: list[int] = [0, n]\n    result: object = returned(owner, two)\n    assert result is n"),
    "runtime-index-error": (RUNTIME_INDEX,
        'owner: list[int] = [0, n]\n    try:\n        result: object = returned(owner, two)\n    '
        'except IndexError:\n        pass\n    else:\n        raise AssertionError("expected IndexError")'),
})

TUPLE_INDEX = """
def returned(owner: tuple[int, int], index: int) -> int:
    return owner[index]
"""
CASES.update({
    "tuple-index-runtime": (TUPLE_INDEX,
        "owner: tuple[int, int] = (0, n)\n    result: object = returned(owner, two - 1)\n    assert result is n"),
    "tuple-index-detached": (TUPLE_INDEX,
        "owner: tuple[int, int] = (0, n)\n    n = 0\n    result: object = returned(owner, 1 - two)\n    "
        "owner = (0, 0)\n    gc.collect()\n    assert result == 9223372036854775808"),
    "tuple-index-error": (TUPLE_INDEX,
        'owner: tuple[int, int] = (0, n)\n    try:\n        result: object = returned(owner, two)\n    '
        'except IndexError:\n        pass\n    else:\n        raise AssertionError("expected IndexError")'),
})

DYNAMIC = """
def returned(value: object) -> int:
    return int(value)
"""
CASES.update({
    "dynamic-string": (DYNAMIC, 'result: object = returned("9223372036854775808")\n    assert result == n and result is not n'),
    "dynamic-alias": (DYNAMIC, "result: object = returned(n)\n    assert result is n"),
    "dynamic-invalid": (DYNAMIC, 'try:\n        result: object = returned("not-an-integer")\n    except ValueError:\n        pass\n    else:\n        raise AssertionError("expected ValueError")'),
    "dynamic-type-error": (DYNAMIC, 'try:\n        result: object = returned(None)\n    except TypeError:\n        pass\n    else:\n        raise AssertionError("expected TypeError")'),
})

for method in ("__int__", "__index__"):
    provider = (
        "class Convertible:\n"
        f"    def {method}(self) -> int:\n"
        "        gc.collect()\n"
        "        return int(\"9223372036854775808\")\n"
    )
    CASES[f"dynamic-callback-{method}"] = (
        provider + DYNAMIC,
        "result: object = returned(Convertible())\n"
        "    assert result == n and result is not n",
    )

MARKER = "DIRECT_BOXED_INT_ACCESSOR_OK"


@pytest.mark.integration
@pytest.mark.xdist_group("direct_boxed_int_accessor_owner_audit")
@pytest.mark.parametrize("shape", CASES)
@pytest.mark.parametrize("python_program_compiler", ("pcc0",), indirect=True)
def test_native_accessor_baseline(
    shape, python_program_compiler, explicit_owned_runtime, tmp_path, capfd,
):
    declaration, action = CASES[shape]
    program = (
        "import gc\n" + declaration + "\ndef exercise(two: int) -> None:\n"
        "    n: int = two << 62\n    " + action + "\n"
        "\nexercise(2)\nprint(" + repr(MARKER) + ")\n"
    )
    def capture_ir(modules):
        matches = []
        call_paths = {}
        for index, module in enumerate(modules):
            if isinstance(module, bytes):
                with decoded_module(module) as indexed:
                    functions = {
                        function.name: function for function in indexed.functions
                    }
                    if "user_program_returned" not in functions:
                        continue
                    for subject in ("returned", "exercise"):
                        kernel = get_indexed_function_kernel(functions["user_program_" + subject])
                        call_paths[subject] = [
                            str(kernel.instruction_data(block, inst)[2])
                            for block in range(len(kernel.block_names))
                            for inst in range(kernel.instruction_count(block))
                            if PARSED_INSTRUCTION_KINDS[
                                kernel.instruction_kind_id(block, inst)
                            ] == "call"
                        ]
            else:
                if "@user_program_returned(" not in module:
                    continue
                for subject in ("returned", "exercise"):
                    call_paths[subject] = re.findall(
                        r"\bcall [^\n]*?@([-\w.$]+)\(",
                        _body(module, suffix=subject),
                    )
            matches.append(index)
        assert len(matches) == 1, matches
        assert any("user_program_returned" in name for name in call_paths["exercise"]), call_paths
        if shape.startswith("runtime-index-"):
            assert "py_list_getitem" in call_paths["returned"], call_paths
        if shape.startswith("tuple-index-"):
            assert "py_tuple_getitem" in call_paths["returned"], call_paths
        (tmp_path / "callee-calls.json").write_text(json.dumps(call_paths) + "\n")
        return tuple(matches)

    assert_owned_program(
        program, MARKER + "\n", tmp_path, python_program_compiler, "pcc0",
        explicit_owned_runtime, capfd, emitted_ir_check=capture_ir,
        retain_dump_evidence=True,
    )
    path = tmp_path / "refcount.jsonl"
    observed = run_process_group_timeout(
        [str(tmp_path / "program.out")],
        env={**os.environ, "PCC_GC_BACKEND": "0", "PCC_LOG": "refcount",
             "PCC_LOG_FORMAT": "json", "PCC_LOG_FILE": str(path)},
        timeout=10,
    )
    assert (observed.returncode, observed.stdout, observed.stderr) == (
        0, MARKER + "\n", "",
    )
    terminal = {}
    for line in path.read_text().splitlines():
        record = json.loads(line)
        if record["category"] == "refcount" and record["value1"] == PY_TYPE_INT:
            terminal[record["ptr"]] = (record["event"], record["value0"])
    assert terminal, "missing actual heap-int provenance"
    assert set(terminal.values()) == {("free", 0)}, terminal
    (tmp_path / "heap-int-terminal.json").write_text(json.dumps(terminal) + "\n")
