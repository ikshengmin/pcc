"""Method locals use the same entry-slot contract as ordinary functions.

Host IR only: these tests do not build or execute native code.
"""

import re

import pytest

from pcc.frontends.python import type_infer
from pcc.frontends.python.py_ast import DynType, Name, NoneLit, NoneType
from pcc.frontends.python.py_lift import parse_and_lift
from tests.python.test_slot_call_operand_roots import SlotProbeCodegen, _emit


CASES = (
    ("conditional-tuple", "    if take:\n        value, other = replacement\n"),
    ("conditional-list", "    if take:\n        [value, other] = replacement\n"),
    ("conditional-nested", "    if take:\n        (value,), other = replacement\n"),
    ("conditional-literal", "    if take:\n        value, other = replacement, None\n"),
    ("unconditional", "    value, other = replacement\n"),
    ("skipped-loop", "    while take:\n        value, other = replacement\n        break\n"),
    ("early-return", "    if not take:\n        return slot_args_probe(value)\n    value, other = replacement\n"),
)


def _body(source, symbol):
    text = _emit(source)
    match = re.search(r"^define[^\n]*@" + symbol + r"\([^\n]*\{\n(.*?)^\}",
                      text, re.M | re.S)
    assert match is not None
    assert "strict.nolib.stub" not in match[1]
    return match[1]


@pytest.mark.parametrize("method", [False, True], ids=["function", "method"])
@pytest.mark.parametrize("case, statements", CASES, ids=[case[0] for case in CASES])
def test_initialized_local_keeps_one_slot_across_unpack_edges(case, statements, method):
    source = ("def probe(take, replacement):\n    value = None\n" + statements
              + "    return slot_args_probe(value)\n")
    symbol = "user_slot_operand_probe"
    if method:
        source = "class Holder:\n" + "\n".join(
            "    " + line.replace("def probe(take,", "def probe(self, take,")
            for line in source.splitlines()
        ) + "\n"
        symbol = "user_slot_operand_Holder_probe"
    body = _body(source, symbol)
    slots = re.findall(r"^  (%value(?:\.unpack)?\.addr[\w.]*) = alloca ptr$", body, re.M)
    assert len(slots) == 1, (case, slots)
    assert "value.for.obj.addr" not in body
    slot = re.escape(slots[0])
    # The None initialization, conditional transfer, and later operand copy
    # all refer to one owning frame slot, including the skipped edge.
    assert re.search(
        r"@pcc_gc_store_root_take\(ptr " + slot + r", ptr %none\.[\w.]+\)", body,
    )
    assert re.search(r"@pcc_gc_root_move\(ptr " + slot + r", ptr ", body)
    assert re.search(r"@pcc_gc_root_copy_(?:borrowed_)?lease\(ptr [^,]+, ptr " + slot + r"\)", body)
    assert re.search(r"@pcc_gc_frame_enter\(ptr @\.pcc\.gc\.frame\.map\.1, ptr " + slot + r"\)", body)
    assert not re.search(r"@pcc_gc_frame_enter\(ptr @\.pcc\.gc\.frame\.map\.borrowed\.1, ptr " + slot + r"\)", body)


def test_method_preparation_keeps_augmented_assignment_on_the_initial_slot():
    body = _body("""class Holder:
    def probe(self, take, replacement):
        value = []
        if take:
            value, other = replacement
        value += [1]
        return slot_args_probe(value)
""", "user_slot_operand_Holder_probe")
    slots = re.findall(r"^  (%value(?:\.unpack)?\.addr[\w.]*) = alloca ptr$", body, re.M)
    assert len(slots) == 1
    assert "value.for.obj.addr" not in body
    assert "@pcc_gc_root_move(ptr " + slots[0] + ", ptr " in body
    assert "value.owned" in body


@pytest.mark.parametrize("method", [False, True], ids=["function", "method"])
@pytest.mark.parametrize("initialization", [
    "    value = initial\n",
    "    value = initial\n    value = initial\n",
    "    value = initial\n    if (value := initial):\n        pass\n",
], ids=["borrowed-parameter", "borrowed-rebind", "borrowed-walrus"])
def test_borrowed_initial_value_becomes_an_owner_before_conditional_unpack(initialization, method, tmp_path):
    source = ("def probe(take, initial, replacement):\n" + initialization
              + "    if take:\n        value, other = replacement\n"
              + "    return slot_args_probe(value)\n")
    symbol = "user_slot_operand_probe"
    if method:
        source = "class Holder:\n" + "\n".join(
            "    " + line.replace("def probe(take,", "def probe(self, take,")
            for line in source.splitlines()
        ) + "\n"
        symbol = "user_slot_operand_Holder_probe"
    body = _body(source, symbol)
    slots = re.findall(r"^  (%value(?:\.unpack)?\.addr[\w.]*) = alloca ptr$", body, re.M)
    assert len(slots) == 1
    assert "value.for.obj.addr" not in body
    slot = re.escape(slots[0])
    retained = re.findall(
        r"^  (%value\.local\.borrowed\.retain[\w.]*) = call ptr (?:\(ptr\) )?@pcc_gc_retain(?:_known)?\(ptr %[^)]+\)$",
        body, re.M,
    )
    (tmp_path / "borrowed.ll").write_text(body)
    expected = 2 if initialization.count("value = initial") == 2 else 1
    assert len(retained) == expected, [line for line in body.splitlines() if "retain" in line]
    for value in retained:
        assert re.search(r"@pcc_gc_store_root_take\(ptr " + slot + r", ptr "
                         + re.escape(value) + r"\)", body)
    first_move = body.index("@pcc_gc_root_move(ptr " + slots[0] + ", ptr ")
    assert body.index(retained[-1] + " =") < first_move
    assert re.search(r"store i1 1, ptr %value\.owned[\w.]*", body[:first_move])
    assert re.search(r"@pcc_gc_store_root\(ptr " + slot + r", ptr null\)", body)
    assert re.search(r"@pcc_gc_frame_enter\(ptr @\.pcc\.gc\.frame\.map\.1, ptr " + slot + r"\)", body)
    assert not re.search(r"@pcc_gc_frame_enter\(ptr @\.pcc\.gc\.frame\.map\.borrowed\.1, ptr " + slot + r"\)", body)


class PrecomputedStoreProbe(SlotProbeCodegen):
    def _emit_call(self, expr):
        if isinstance(expr.func, Name) and expr.func.ident == "store_precomputed":
            value = self._emit_expr(expr.args[0])
            target = Name(span=expr.span, ty=DynType(name="dyn"), ident="value")
            self._store_value_at_name(target, value, expr.args[0].ty, value_is_owned=False)
            return self._emit_expr(NoneLit(span=expr.span, ty=NoneType(name="None")))
        return super()._emit_call(expr)


def test_precomputed_borrowed_store_retains_before_replacing_planned_owner():
    source = """class Holder:
    def probe(self, take, initial, replacement):
        value = None
        if take:
            store_precomputed(initial)
        value, other = replacement
        return slot_args_probe(value)
"""
    module = type_infer.infer_module(parse_and_lift(source, "slot_operand.py", "slot_operand"))
    codegen = PrecomputedStoreProbe(module, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    text = str(codegen.generate(module))
    body = re.search(r"^define[^\n]*@user_slot_operand_Holder_probe\([^\n]*\{\n(.*?)^\}",
                     text, re.M | re.S)[1]
    slots = re.findall(r"^  (%value(?:\.unpack)?\.addr[\w.]*) = alloca ptr$", body, re.M)
    assert len(slots) == 1
    retained = re.search(r"^  (%value\.local\.borrowed\.retain[\w.]*) = call ptr (?:\(ptr\) )?@pcc_gc_retain\(ptr %[^)]+\)$", body, re.M)
    assert retained is not None
    publication = "@pcc_gc_store_root_take(ptr " + slots[0] + ", ptr " + retained[1] + ")"
    assert body.index(publication) > retained.start()
    assert body.index(publication) < body.index("@pcc_gc_root_move(ptr " + slots[0])
    assert re.search(r"store i1 1, ptr %value\.owned[\w.]*", body[body.index(publication):])


def test_planned_owner_marker_requires_exact_slot_identity():
    from pcc.frontends.python.codegen.local_bound_lowering import is_preplanned_owned_local
    from types import SimpleNamespace

    planned, shadow = object(), object()
    host = SimpleNamespace(_owned_local_flag_allocas={
        "value": shadow, ".planned.owner.value": planned,
    })
    assert is_preplanned_owned_local(host, "value", planned)
    assert not is_preplanned_owned_local(host, "value", shadow)
    assert not is_preplanned_owned_local(host, "missing", None)
