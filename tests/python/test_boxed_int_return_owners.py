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

def via_direct_object_local(n: int) -> None:
    result: object = fresh(n)
    assert result == 9223372036854775827

def add_two(n: int, m: int) -> int:
    return n + m

def subtract(n: int) -> int:
    return n - 19

def via_binary_direct_object_local(n: int, delta: int) -> None:
    box_add: object = add_two(n, delta)
    box_sub: object = subtract(n)
    assert box_add == 9223372036854775827
    assert box_sub == 9223372036854775789

def negate(n: int) -> int:
    return -n

def invert(n: int) -> int:
    return ~n

def via_unary_direct_object_local(n: int) -> None:
    negative: object = negate(n)
    inverse: object = invert(n)
    alias: object = by_plus(n)
    assert negative == -9223372036854775808
    assert inverse == -9223372036854775809
    assert alias is n

def parse_literal() -> int:
    return int("9223372036854775808")

def parse_invalid_literal() -> int:
    return int("not-an-integer")

def via_string_direct_object_local(n: int) -> None:
    parsed: object = parse_literal()
    assert parsed == n and parsed is not n
    try:
        invalid: object = parse_invalid_literal()
    except ValueError:
        pass
    else:
        raise AssertionError("missing int conversion error")

def mixed(n: int, make_fresh: bool) -> int:
    return n + 19 if make_fresh else n

def via_mixed_direct_object_local(n: int, make_fresh: bool) -> None:
    box: object = mixed(n, make_fresh)
    if make_fresh:
        assert box == 9223372036854775827 and box is not n
    else:
        assert box is n

def wrap_fresh(n: int) -> int:
    return fresh(n)

def wrap_alias(n: int) -> int:
    return by_name(n)

def valid_input(n: int) -> int:
    return int("9223372036854775808")

def wrap_parse(n: int) -> int:
    return valid_input(n)

def invalid_input(n: int) -> int:
    return int("not-an-integer")

def wrap_invalid(n: int) -> int:
    return invalid_input(n)

def via_wrapped_fresh(n: int) -> None:
    value: object = wrap_fresh(n)
    assert value == 9223372036854775827 and value is not n

def via_wrapped_alias(n: int) -> None:
    value: object = wrap_alias(n)
    assert value is n

def via_wrapped_parse(n: int) -> None:
    value: object = wrap_parse(n)
    assert value == n and value is not n

def via_wrapped_error(n: int) -> None:
    try:
        value: object = wrap_invalid(n)
    except ValueError:
        return
    raise AssertionError("missing exception in direct return chain")

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
    "fresh-direct-local": "via_direct_object_local(n)",
    "binary-direct-local": "via_binary_direct_object_local(n, 19)",
    "unary-direct-local": "via_unary_direct_object_local(n)",
    "string-direct-local": "via_string_direct_object_local(n)",
    "mixed-direct-fresh": "via_mixed_direct_object_local(n, True)",
    "mixed-direct-alias": "via_mixed_direct_object_local(n, False)",
    "wrapper-direct-fresh": "via_wrapped_fresh(n)",
    "wrapper-direct-alias": "via_wrapped_alias(n)",
    "wrapper-direct-parse": "via_wrapped_parse(n)",
    "wrapper-direct-error": "via_wrapped_error(n)",
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
    if shape in {
        "string-direct-local", "mixed-direct-fresh",
        "wrapper-direct-fresh", "wrapper-direct-parse",
    }:
        # Exercise a separate heap result in addition to the source and
        # replacement instead of passing with only tagged results.
        assert retired >= 3



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


@pytest.mark.parametrize(
    ("prefix", "formals", "value", "call", "proven"),
    (
        ("", "", '"9223372036854775808"', "parse()", True),
        ("", "int", '"9223372036854775808"', "parse(callback)", False),
        ("def int(text: str) -> int:\n    return 17\n", '',
         '"9223372036854775808"', "parse()", False),
        ("", "text: str", "text", "parse(text)", True),
        ("", "text: str, int", "text", "parse(text, callback)", False),
        ("", "text: str, unused: str", "text", "parse(text, text)", False),
    ),
    ids=("builtin-literal", "formal-shadow", "module-shadow", "typed-input",
         "typed-formal-shadow", "extra-formal"),
)
def test_direct_string_int_owner_requires_the_proven_unshadowed_builtin(
    prefix, formals, value, call, proven,
):
    text = _emit(prefix + f"""\
def parse({formals}) -> int:
    return int({value})
def probe(callback=None, text: str = "") -> bool:
    result: object = {call}
    return bool(result)
""", strict=True)
    caller = _body(text)
    assert "@user_bridge_handoff_parse(" in caller
    if proven:
        callee = _body(text, "parse")
        producer = re.search(
            r"(%[^ ]+) = call ptr [^\n]*@py_int_from_cstr_or_raise\(", callee,
        )
        assert producer is not None
        # The caller consumes exactly the owner returned by the parser, not
        # the pointer of a scalar fallback that unboxed and reboxed a result.
        assert re.search(
            r"@pcc_gc_store_return_root\(ptr [^,]+, ptr "
            + re.escape(producer[1]) + r"\)", callee,
        )
        assert "result.local.borrowed.retain" not in caller
    else:
        assert "result.local.borrowed.retain" in caller



def test_mixed_boxed_int_direct_return_selects_a_proven_owner_per_branch():
    text = _emit("""def mix(n: int, flag: bool) -> int:
    return n + 19 if flag else n
def probe(n: int, flag: bool) -> bool:
    box: object = mix(n, flag)
    return box is n
""", strict=True)
    callee, caller = _body(text, "mix"), _body(text)
    addition = re.search(
        r"(%[^ ]+) = call ptr [^\n]*@py_obj_add\([^\n]+\)\n"
        r"\s*store ptr \1, ptr (%[^ ,\n]+)",
        callee,
    )
    alias_copy = re.search(
        r"@pcc_gc_root_copy_local\(ptr (%[^ ,]+), ptr %n\.addr[^ ,]+, i64 1\)", callee,
    )
    assert addition is not None
    assert alias_copy is not None
    joined = []
    for source in (addition[2], alias_copy[1]):
        move = re.search(r"@pcc_gc_root_move\(ptr (%[^ ,]+), ptr "
                         + re.escape(source) + r"\)", callee)
        assert move is not None
        joined.append(move[1])
    assert joined[0] == joined[1]
    assert re.search(r"@pcc_gc_root_move\(ptr %return\.cleanup\.slot[^ ,]+, ptr "
                     + re.escape(joined[0]) + r"\)", callee)
    assert "ret ptr null" in callee
    result = re.search(
        r"(%[^ ]+) = call ptr [^\n]*@user_bridge_handoff_mix\(", caller,
    )
    assert result is not None
    assert re.search(r"icmp eq ptr " + re.escape(result[1]) + r", null", caller)
    assert "box.local.borrowed.retain" not in caller


def test_mixed_direct_return_with_an_unproven_alias_keeps_existing_boundary():
    text = _emit("""def mix(n: int, other: int, flag: bool) -> int:
    return n + 19 if flag else other
def probe(n: int, other: int, flag: bool) -> bool:
    box: object = mix(n, other, flag)
    return box is other
""", strict=True)
    callee, caller = _body(text, "mix"), _body(text)
    assert "return.int.mixed" not in callee
    assert "@user_bridge_handoff_mix(" in caller
    assert "box.local.borrowed.retain" in caller


def test_exported_mixed_int_return_keeps_the_machine_abi():
    text = _emit("""\
from pcc.extern import c_abi_export
@c_abi_export("raw_mix")
def mix(n: int, flag: bool) -> int:
    return n + 19 if flag else n
""", strict=True)
    body = re.search(r"^define external i64 @raw_mix\(i64 %n, i1 %flag\) \{\n(.*?)^\}",
                     text, re.M | re.S)
    assert body is not None
    assert "ret i64 " in body[1]
    assert "return.int.mixed" not in body[1]
    assert "@py_obj_add(" not in body[1]
    assert not re.search(r"@pcc_gc_(?:retain|release|frame_enter|root_move)\w*\(", body[1])


@pytest.mark.parametrize("terminal", ("n + 19", "n", 'int("not-an-integer")'))
def test_one_direct_wrapper_publishes_its_leaf_owner_before_cleanup(terminal):
    # Definitions below their callers also use the resolved declarations;
    # recognition must not depend on which function's body was emitted first.
    text = _emit(f"""\
def probe(n: int) -> bool:
    value: object = wrap(n)
    return value is n
def wrap(n: int) -> int:
    return leaf(n)
def leaf(n: int) -> int:
    return {terminal}
""", strict=True)
    wrapper, caller = _body(text, "wrap"), _body(text)
    native_call = re.search(
        r"(%[^ ]+) = call ptr [^\n]*@user_bridge_handoff_leaf\([^\n]+\)\n"
        r"\s*store ptr \1, ptr (%return\.int\.forwarded[^ ,\n]+)",
        wrapper,
    )
    assert native_call is not None
    assert re.search(r"@pcc_gc_root_move\(ptr %return\.cleanup\.slot[^ ,]+, ptr "
                     + re.escape(native_call[2]) + r"\)", wrapper)
    result = re.search(r"(%[^ ]+) = call ptr [^\n]*@user_bridge_handoff_wrap\(", caller)
    assert result is not None
    assert re.search(r"icmp eq ptr " + re.escape(result[1]) + r", null", caller)
    assert "value.local.borrowed.retain" not in caller


def test_direct_wrapper_does_not_prove_a_second_layer():
    text = _emit("""\
def terminal(n: int) -> int:
    return n
def leaf(n: int) -> int:
    return terminal(n)
def wrap(n: int) -> int:
    return leaf(n)
def probe(n: int) -> bool:
    value: object = wrap(n)
    return value is n
""", strict=True)
    assert "return.int.forwarded" in _body(text, "leaf")
    assert "return.int.forwarded" not in _body(text, "wrap")
    assert "value.local.borrowed.retain" in _body(text)


def test_wrapper_with_foreign_leaf_has_no_native_owner_proof():
    text = _emit("""\
from compatibility_provider import leaf
def wrap(n: int) -> int:
    return leaf(n)
def probe(n: int) -> bool:
    value: object = wrap(n)
    return bool(value)
""")
    wrapper = _body(text, "wrap")
    assert "@py_cpy_call1(" in wrapper
    assert "return.int.forwarded" not in wrapper


def test_wrapper_of_c_export_has_no_python_boxed_owner_proof():
    text = _emit("""\
from pcc.extern import c_abi_export
@c_abi_export("raw_leaf")
def leaf(n: int) -> int:
    return n
def wrap(n: int) -> int:
    return leaf(n)
""", strict=True)
    assert "@raw_leaf(" in _body(text, "wrap")
    assert "return.int.forwarded" not in _body(text, "wrap")


@pytest.mark.parametrize("name", ("slice", "frozenset"))
def test_wrapper_does_not_infer_the_provider_from_a_builtin_spelling(name):
    text = _emit(f"""\
def {name}(n: int) -> int:
    return n
def wrap(n: int) -> int:
    return {name}(n)
def probe(n: int) -> bool:
    value: object = wrap(n)
    return bool(value)
""", strict=True)
    assert "return.int.forwarded" not in _body(text, "wrap")
    assert "value.local.borrowed.retain" in _body(text)


def test_exact_field_return_consumes_the_getter_owner_and_checks_null():
    text = _emit("""
class Holder:
    def __init__(self, n: int):
        self.value = n

def read(owner: Holder) -> int:
    return owner.value

def probe(owner: Holder) -> bool:
    result: object = read(owner)
    return bool(result)
""", strict=True)
    callee = _body(text, "read")
    producer = re.search(r"(%[^ ]+) = call ptr [^\n]*@py_instance_get_field\(", callee)
    assert producer is not None
    assert re.search(
        r"@pcc_gc_store_return_root\(ptr [^,]+, ptr " + re.escape(producer[1]) + r"\)",
        callee,
    )
    assert "@py_err_occurred(" in callee
    caller = _body(text)
    assert "@user_bridge_handoff_read(" in caller
    assert "result.local.borrowed.retain" not in caller


@pytest.mark.parametrize("index,proven", [(0, True), (1, False)])
def test_exact_list_subscript_return_handoff_is_limited_to_proved_index(index, proven):
    text = _emit(f"""
def read(owner: list[int]) -> int:
    return owner[{index}]

def probe(owner: list[int]) -> bool:
    result: object = read(owner)
    return bool(result)
""", strict=True)
    callee = _body(text, "read")
    producer = re.search(r"(%[^ ]+) = call ptr [^\n]*@py_list_getitem\(", callee)
    assert producer is not None
    assert re.search(
        r"@pcc_gc_store_return_root\(ptr [^,]+, ptr " + re.escape(producer[1]) + r"\)",
        callee,
    )
    assert "@py_err_occurred(" in callee
    caller = _body(text)
    assert "@user_bridge_handoff_read(" in caller
    assert ("result.local.borrowed.retain" not in caller) == proven
