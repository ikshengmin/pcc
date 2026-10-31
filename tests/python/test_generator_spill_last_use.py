"""Completed hoist operands die before a later suspension, not at statement end.

These are host frontend/owned-IR checks. Native finalizers, weakrefs and all
five collectors remain covered by the independent repository fixture gate.
"""
import pytest

from pcc.frontends.python.codegen import generator_lowering
from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.py_ast import (
    Attr,
    Call,
    DynType,
    Name,
    SourceSpan,
)
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module
from tests.owned_ir_validation import verify_ir_text


SPAN = SourceSpan("spill_last_use.py", 1, 0, 1, 1)
DYN = DynType(name="dyn")


def _name(ident):
    return Name(span=SPAN, ty=DYN, ident=ident)


def _call(*args, kwargs=()):
    return Call(span=SPAN, ty=DYN, func=_name("consume"), args=args, kwargs=kwargs)


def test_last_use_inventory_includes_later_hoists_and_keyword_consumers():
    hoists = [
        ("first", _name("source")),
        ("second", _call(_name("first"))),
        ("third", _call(_name("second"), kwargs=(("again", _name("first")),))),
    ]
    assert generator_lowering._generator_spill_last_uses(hoists, _name("third")) == {
        "first": 2, "second": 2, "third": 3,
    }
    # A final consumer can reuse a root whose earlier consumer completed.
    assert generator_lowering._generator_spill_last_uses(
        hoists, _call(_name("first"), _name("third")),
    ) == {"first": 3, "second": 2, "third": 3}


def test_inplace_keeps_string_operands_and_reused_target_until_store():
    target = Attr(span=SPAN, ty=DYN, obj=_name("receiver"), name="value")
    hoists = [
        ("receiver", _name("source")),
        ("current", target),
        ("value", _call(_name("current"))),
    ]
    inplace = (0, "current", "value", target, SPAN)
    assert generator_lowering._generator_spill_last_uses(hoists, None, inplace) == {
        "receiver": 3, "current": 3, "value": 3,
    }


class SpillCodegen(L1CodeGen):
    """Observe production completion boundaries without substituting emitters."""
    def __init__(self, module):
        super().__init__(module, ir_scaffold_mode="on")
        self.completed = []
        self.consumer_inputs = []
        self.suspensions = []
        self._strict_no_libpython = True
        self._prefer_native_callable_values = True

    def _emit_slot_call_operand(self, expr, label, borrow_stable=False):
        root = super()._emit_slot_call_operand(expr, label, borrow_stable)
        if label == "spill" and self.current_function.name.endswith("produce__gen_resume"):
            self.completed.append((expr, root, self.builder._block, len(self.builder._block._instrs)))
            if isinstance(expr, Call) and isinstance(expr.func, Name) and expr.func.ident == "consume":
                self.consumer_inputs.append(tuple(self.env[arg.ident][0] for arg in expr.args))
        return root

    def _emit_generator_yield_value(self, value, **kwargs):
        if self.current_function.name.endswith("produce__gen_resume"):
            self.suspensions.append((self.builder._block, len(self.builder._block._instrs)))
        return super()._emit_generator_yield_value(value, **kwargs)


def _emit(alias, monkeypatch=None, *, conditional=False):
    source = '''def consume(value, acknowledgement):
    return ALIAS
def produce(flag):
    yield consume(INPUT, (yield "ready"))
'''.replace("ALIAS", "value" if alias else "None").replace(
        "INPUT", "([1] if flag else [2])" if conditional else "[1]",
    )
    module = infer_module(parse_and_lift(source, "spill_last_use.py", "spill_last_use"))
    codegen = SpillCodegen(module)
    if monkeypatch is not None:
        # Deliberately restore the prior statement-wide lifetime. The same
        # assertion below must reject it, rather than counting error clears.
        monkeypatch.setattr(
            generator_lowering, "_generator_spill_last_uses",
            lambda hoists, consumer, inplace=None: {
                ident: len(hoists) for ident, _operand in hoists
            },
        )
    text = str(codegen.generate(module))
    verify_ir_text(text)
    return codegen, text


def _assert_consumed_root_retired(codegen, text):
    completed = next(row for row in codegen.completed if (
        isinstance(row[0], Call) and isinstance(row[0].func, Name)
        and row[0].func.ident == "consume"
    ))
    input_root = codegen.consumer_inputs[0][0]
    _, result_root, completion_block, completion_index = completed
    assert input_root is not result_root, "aliased values still need independent owners"
    assert len(codegen.suspensions) == 2
    yield_block, yield_index = codegen.suspensions[-1]
    assert yield_block is completion_block
    before_yield = "\n".join(str(row) for row in yield_block._instrs[completion_index:yield_index])
    clear = "@pcc_gc_store_root(ptr " + str(input_root) + ", ptr null)"
    assert clear in before_yield, "consumed hoist still owns its input at the outer yield"
    result_clear = "@pcc_gc_store_root(ptr " + str(result_root) + ", ptr null)"
    assert result_clear not in before_yield, "outer yield still consumes the result root"
    # The first suspension precedes the completed call: no blanket yield clear.
    first_block, first_index = codegen.suspensions[0]
    assert clear not in "\n".join(str(row) for row in first_block._instrs[:first_index])
    # Error cleanup remains independent from the normal-path retirement.
    assert text.count(clear) >= 2


@pytest.mark.parametrize("alias", [False, True])
def test_completed_hoist_retires_consumed_input_before_outer_yield(alias):
    codegen, text = _emit(alias)
    _assert_consumed_root_retired(codegen, text)


def test_conditional_hoist_retires_selected_input_before_outer_yield():
    codegen, text = _emit(True, conditional=True)
    assert "spill.conditional" in str(codegen.consumer_inputs[0][0])
    _assert_consumed_root_retired(codegen, text)


def test_nested_await_consumption_retires_input_before_later_await():
    source = '''async def child(value):
    return value
def consume(value, acknowledgement):
    return value
def combine(first, second):
    return first
async def produce():
    return combine(consume([1], await child(None)), await child(None))
'''
    module = infer_module(parse_and_lift(source, "spill_last_use.py", "spill_last_use"))
    codegen = SpillCodegen(module)
    text = str(codegen.generate(module))
    verify_ir_text(text)
    completed = next(row for row in codegen.completed if (
        isinstance(row[0], Call) and isinstance(row[0].func, Name)
        and row[0].func.ident == "consume"
    ))
    _, result_root, block, index = completed
    assert len(codegen.suspensions) >= 2, "both real await paths must be lowered"
    inputs = codegen.consumer_inputs[0]
    after = "\n".join(str(row) for row in block._instrs[index:])
    clears = ["@pcc_gc_store_root(ptr " + str(root) + ", ptr null)" for root in inputs]
    assert all(clear in after for clear in clears)
    assert after.index(clears[1]) < after.index(clears[0]), "retire in reverse evaluation order"
    assert "@pcc_gc_store_root(ptr " + str(result_root) + ", ptr null)" not in after
    # The next hoist is the later await, not the statement's final consumer.
    position = codegen.completed.index(completed)
    assert any(isinstance(row[0], Call) and isinstance(row[0].func, Name)
               and row[0].func.ident == "__await__" for row in codegen.completed[position + 1:])


def test_ir_regression_rejects_statement_wide_lifetime(monkeypatch):
    codegen, text = _emit(False, monkeypatch)
    with pytest.raises(AssertionError, match="consumed hoist still owns"):
        _assert_consumed_root_retired(codegen, text)
