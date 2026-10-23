"""Host-only checks for parameter-target rebinding and entry storage.

These tests parse or emit IR. They never build or execute a native program.
"""

import re
from types import SimpleNamespace

import pytest

from pcc.frontends.python import parser
from pcc.frontends.python.codegen import cpy_call_lowering
from pcc.frontends.python.codegen.cpy_call_lowering import (
    CpyCallLoweringMixin,
    _name_is_stored_in,
)
from pcc.frontends.python.py_ast import FuncDef
from tests.python.test_slot_call_operand_roots import _emit


@pytest.mark.parametrize(
    "statement, expected",
    [
        ("value = other", True),
        ("value += other", True),
        ("value, other = replacement", True),
        ("(value,), other = replacement", True),
        ("[value, other] = replacement", True),
        ("[other, [value]] = replacement", True),
        ("(other, [value, tail]) = replacement", True),
        ("for (value,), other in replacement:\n    pass", True),
        ("for [other, [value]] in replacement:\n    pass", True),
        ("value.field, other = replacement", False),
        ("value[0], other = replacement", False),
        ("other[value] = replacement", False),
        # Keep the enclosing scan conservative: nested bodies may contain
        # nonlocal writes, and the same scan guards the own-kwargs shortcut.
        ("def inner():\n    value = other", True),
        ("def inner():\n    nonlocal value\n    value = other", True),
        ("class Inner:\n    value = other", True),
        ("fn = lambda value: value", False),
    ],
)
def test_store_scan_preserves_target_and_scope_policy(statement, expected):
    source = "def probe(value, other, replacement):\n" + "\n".join(
        "    " + line for line in statement.splitlines()
    ) + "\n"
    module = parser.parse(source, "parameter_scan.py")
    function = module.body[0]
    assert isinstance(function, FuncDef)
    assert _name_is_stored_in(function.body, "value") is expected


def test_target_walker_does_not_enter_a_nested_scope():
    module = parser.parse("def inner():\n    value = other\n", "parameter_scan.py")
    assert not cpy_call_lowering._target_stores_name(module.body[0], "value")


@pytest.mark.parametrize(
    "statement, expected",
    [
        ("(kwargs,), other = replacement", False),
        ("[other, [kwargs]] = replacement", False),
        ("kwargs.field = replacement", True),
        ("kwargs['key'] = replacement", True),
    ],
)
def test_own_kwargs_shortcut_rejects_recursive_name_rebinding(statement, expected):
    module = parser.parse(
        "def probe(replacement, **kwargs):\n    " + statement +
        "\n    return kwargs\n", "parameter_scan.py",
    )
    function = module.body[0]
    host = SimpleNamespace(current_func_def=function)
    kwargs_expr = function.body[-1].value
    accepted = CpyCallLoweringMixin._cpy_kw_mapping_is_own_kwargs_param(host, kwargs_expr)
    assert accepted is expected


@pytest.mark.parametrize("method", [False, True], ids=["function", "method"])
@pytest.mark.parametrize(
    "target",
    ["(value,), other", "[value, other]", "[other, [value]]"],
    ids=["nested-tuple", "list", "nested-list"],
)
def test_conditional_parameter_unpack_uses_entry_owned_storage(target, method, tmp_path):
    source = ("def probe(value, take, replacement):\n"
              "    if take:\n"
              "        " + target + " = replacement\n"
              "    return slot_args_probe(value)\n")
    symbol = "user_slot_operand_probe"
    if method:
        source = "class Holder:\n" + "\n".join(
            "    " + line.replace("def probe(value,", "def probe(self, value,")
            for line in source.splitlines()
        ) + "\n"
        symbol = "user_slot_operand_Holder_probe"
    emitted = _emit(source)
    match = re.search(r"^define[^\n]*@" + symbol + r"\([^\n]*\{\n(.*?)^\}",
                      emitted, re.M | re.S)
    assert match is not None
    body = match[1]
    (tmp_path / "parameter-unpack.ll").write_text(body)
    assert "strict.nolib.stub" not in body
    split_slots = re.findall(r"^  (%value\.unpack\.addr[\w.]*) = alloca ptr$", body, re.M)
    assert not split_slots
    slots = re.findall(r"^  (%value\.for\.obj\.addr[\w.]*) = alloca ptr$", body, re.M)
    assert len(slots) == 1
    slot = re.escape(slots[0])
    assert re.search(r"@pcc_gc_frame_enter\(ptr @\.pcc\.gc\.frame\.map\.1, ptr " + slot + r"\)", body)
    assert re.search(r"@pcc_gc_store_root_take\(ptr " + slot + r", ptr %value\.for\.pre\.retain", body)
    assert re.search(r"@pcc_gc_root_move\(ptr " + slot + r", ptr ", body)
    assert re.search(r"@pcc_gc_root_copy_lease\(ptr [^,]+, ptr " + slot + r"\)", body)
    assert re.search(r"@pcc_gc_store_root\(ptr " + slot + r", ptr null\)", body)
    # The synthetic recursion-activation preamble can branch before the
    # user's entry block. The owner must precede the user's condition, not
    # that activation failure edge which never enters the function body.
    entry_start = body.index("\nentry:\n")
    first_body_branch = body.index("br i1 ", entry_start)
    initialized_at = body.index("@pcc_gc_store_root_take(ptr " + slots[0])
    assert entry_start < initialized_at < first_body_branch
