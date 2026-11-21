"""A boxed Python int return must give its caller exactly one owner."""
from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

from pcc.frontends.python.codegen.freestanding_abi_constants import PY_TYPE_INT
from pcc.frontends.python.pipeline import compile_python
from tests.python.owned_regression_support import (
    assert_owned_program,
    explicit_owned_runtime,
)
from tests.python.test_cpy_result_slot_handoff import _body, _bridge_publication, _emit


PROGRAM = """\
import gc

class Holder:
    def __init__(self, n: int):
        self.value = n

def fresh(n: int) -> int:
    return n + 19

def by_name(n: int) -> int:
    return n

def by_int(n: int) -> int:
    return int(n)

def by_plus(n: int) -> int:
    return +n

def by_choice(n: int, mode: int) -> int:
    return int(+n) if mode == 0 else (n if mode == 1 else n + 19)

def by_finally(n: int) -> int:
    try:
        return +n
    finally:
        n = 0

def pending(n: int, use_fresh: bool, cancel: bool) -> int:
    try:
        return n + 19 if use_fresh else +n
    finally:
        gc.collect()
        if cancel:
            raise ValueError("cancel")

def consume_pending(f, n: int, use_fresh: bool, cancel: bool) -> None:
    try:
        result: object = f(n, use_fresh, cancel)
    except ValueError as error:
        assert cancel and str(error) == "cancel"
        return
    assert not cancel
    if use_fresh:
        assert result == 9223372036854775827
    else:
        assert result is n

def by_field(box: Holder) -> int:
    return box.value

def by_item(seq: list[int]) -> int:
    return seq[0]

def by_opaque(n: object) -> int:
    return int(n)

def inspect_fresh(value: object) -> None:
    assert value == 9223372036854775827

def via_direct_operand(f, n: int) -> None:
    # A direct callee publishes to this dynamic call's managed argument slot.
    # This does not cover the separate scalar-to-object local assignment.
    f(fresh(n))

def consume(f, n, original, expected_alias: bool) -> None:
    # An unresolved native callable uses the real owning object dispatcher.
    result: object = f(n)
    if expected_alias:
        assert result is original
    else:
        assert result == 9223372036854775827

def consume_choice(f, n: int, mode: int) -> None:
    result: object = f(n, mode)
    if mode != 2:
        assert result is n
    else:
        assert result == 9223372036854775827

def main(two: int) -> None:
    n: int = two << 62
    CASE_BODY
    replacement: int = (two + 1) << 62
    assert n is not replacement
    assert n == 9223372036854775808
    print("BOXED_INT_RETURN_OWNER_OK")

main(2)
"""

CASES = {
    "fresh": "via_direct_operand(inspect_fresh, n)\n    consume(fresh, n, n, False)",
    "name": "consume(by_name, n, n, True)",
    "int": "consume(by_int, n, n, True)",
    "plus": "consume(by_plus, n, n, True)",
    "choice": """consume_choice(by_choice, n, 0)
    consume_choice(by_choice, n, 1)
    consume_choice(by_choice, n, 2)
    consume(by_finally, n, n, True)""",
    "finally-cancel": """consume_pending(pending, n, True, True)
    consume_pending(pending, n, False, True)
    consume_pending(pending, n, True, False)
    consume_pending(pending, n, False, False)""",
    "field-item-opaque": """consume(by_field, Holder(n), n, True)
    consume(by_item, [n], n, True)
    consume(by_opaque, n, n, True)""",
}


@pytest.mark.integration
@pytest.mark.xdist_group("boxed_int_return_owners")
@pytest.mark.parametrize("shape", CASES)
@pytest.mark.parametrize("python_program_compiler", ("pcc0", "pcc1"), indirect=True)
def test_boxed_int_return_owner_native(
    shape, python_program_compiler, request, explicit_owned_runtime, tmp_path, capfd,
):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(
        PROGRAM.replace("CASE_BODY", CASES[shape]), "BOXED_INT_RETURN_OWNER_OK\n",
        tmp_path, python_program_compiler, mode, explicit_owned_runtime, capfd,
    )
    path = tmp_path / "int-return-owner.jsonl"
    program = subprocess.run(
        [str(tmp_path / "program.out")], capture_output=True, text=True, timeout=10,
        env={**os.environ, "PCC_GC_BACKEND": "0", "PCC_LOG": "refcount",
             "PCC_LOG_FORMAT": "json", "PCC_LOG_FILE": str(path)},
    )
    assert (program.returncode, program.stdout, program.stderr) == (
        0, "BOXED_INT_RETURN_OWNER_OK\n", "",
    )
    retired = 0
    terminal = {}
    for line in path.read_text().splitlines():
        event = json.loads(line)
        if event["category"] == "refcount" and event["value1"] == PY_TYPE_INT:
            terminal[event["ptr"]] = (event["event"], event["value0"])
            if event["event"] == "free":
                retired += 1
    assert all(last == ("free", 0) for last in terminal.values()), terminal
    assert retired >= (4 if shape in {"fresh", "finally-cancel"} else 2)



def test_fresh_direct_return_enters_an_already_owned_argument_slot():
    text = _emit("""def fresh(k: int) -> int:
    return k + 19
def probe(fn, n: int) -> None:
    fn(fresh(n))
""", strict=True)
    body = _body(text)
    handoff = re.search(
        r"(%[^ ]+) = call ptr [^\n]*@user_bridge_handoff_fresh\([^\n]+\)\n"
        r"\s*store ptr \1, ptr (%[^ ,\n]+)",
        body,
    )
    assert handoff is not None
    assert "@py_obj_call_slots(" in body[handoff.end():]


@pytest.mark.parametrize("freestanding", (False, True))
def test_machine_int_return_remains_raw(freestanding, tmp_path):
    setting = "__pcc_freestanding__ = True\n" if freestanding else ""
    source = tmp_path / "raw.py"
    output = tmp_path / "raw.ll"
    source.write_text("from pcc.extern import c_abi_export\n" + setting + """@c_abi_export("raw_probe")
def raw_probe(n: i64) -> i64:
    return +n
""")
    compile_python(str(source), str(output), emit_llvm_only=True, backend="self",
                   python_library=freestanding)
    body = re.search(r"^define external i64 @raw_probe\(i64 %n\)[^\n]*\{\n(.*?)^\}",
                     output.read_text(), re.M | re.S)
    assert body is not None
    assert "ret i64 " in body[1]
    assert not re.search(r"@(pcc_gc_(?:retain|release|frame_enter|store_return_root)"
                         r"|py_int_from_i64|py_obj_pos)\w*\(", body[1])


def test_mixed_boxed_return_bridges_the_foreign_branch_into_a_pcc_owner():
    text = _emit("""from compatibility_provider import make
def probe(n: int, flag: bool) -> int:
    return int(n) if flag else make(n)
""")
    body = _body(text)
    bridge, output = _bridge_publication(body)
    conversion, foreign = bridge.groups()
    assert "@py_obj_as_int_object_args(" in body
    assert "@py_cpy_call1(" in body[:bridge.start()]
    assert re.search(r"@py_cpy_decref\(ptr " + re.escape(foreign) + r"\)", body)
    assert not re.search(r"@py_cpy_decref\(ptr " + re.escape(conversion) + r"\)", body)
    assert re.search(r"@pcc_gc_root_move\(ptr [^,]+, ptr " + re.escape(output) + r"\)",
                     body[bridge.end():])
