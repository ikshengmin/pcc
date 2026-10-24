"""Bounded synchronous tuple operands reuse the retaining call-tuple path.

Default tests execute production host lowering, verify owned IR/stack maps,
and exercise its root protocol in a deterministic ownership model. They do
not execute emitted native programs or qualify any collector. The explicit
integration case remains the source-matched native qualification gate.
"""

from collections import Counter
from types import SimpleNamespace
import re
import subprocess
import sys

import pytest

from pcc.backend.self_backend_precise_stackmaps import build_stack_map_plans
from pcc.backend.self_backend_prepare import prepare_module_for_target
from pcc.backend.self_backend_x86_64_linux import _aggregate_returned_indirect
from pcc.frontends.python.codegen.call_object_lowering import CallObjectLoweringMixin
from pcc.frontends.python.py_ast import DynType, Name, SourceSpan, TupleExpr
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module
from tests.python.owned_regression_support import explicit_owned_runtime
from tests.python.test_slot_call_operand_roots import (
    SlotProbeCodegen,
    _calls,
    _emit,
    _probe_function,
)


@pytest.mark.parametrize("count", (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 32))
def test_tuple_operand_has_a_bounded_direct_shape(count):
    elements = ", ".join("value" for _ in range(count))
    if count == 1:
        elements += ","
    body = _probe_function(_emit(
        "def probe(value):\n    return slot_operand_probe((" + elements + "))\n"
    ))
    if 1 <= count <= 8:
        assert len(_calls(body, "py_tuple_new")) == 1
        assert len(_calls(body, "py_tuple_set_item")) == count
        assert not _calls(body, "py_list_new")
        assert not _calls(body, "py_list_append")
        assert not _calls(body, "py_tuple_from_list")
    else:
        assert len(_calls(body, "py_list_new")) == 1
        assert len(_calls(body, "py_list_append")) == count
        assert len(_calls(body, "py_tuple_from_list")) == 1


@pytest.mark.parametrize("expression", ("(*value,)", "(1, *value, 2)", "[value, value]"))
def test_splats_and_lists_keep_streaming_lowering(expression):
    body = _probe_function(_emit(
        "def probe(value):\n    return slot_operand_probe(" + expression + ")\n"
    ))
    assert _calls(body, "py_list_new")
    assert not _calls(body, "py_tuple_set_item")
    if expression.startswith("("):
        assert _calls(body, "py_list_extend")
        assert _calls(body, "py_tuple_from_list")
    else:
        assert len(_calls(body, "py_list_append")) == 2


class RoutingCodegen(SlotProbeCodegen):
    def __init__(self, *args, **kwargs):
        self.literal_routes = []
        super().__init__(*args, **kwargs)

    def _emit_slot_call_args_tuple(self, args, label="call.args"):
        if label == "probe.operand":
            self.literal_routes.append("fixed")
        return super()._emit_slot_call_args_tuple(args, label)

    def _emit_slot_call_sequence(self, elems, label, tuple_result):
        if label == "probe.operand":
            self.literal_routes.append("streaming")
        return super()._emit_slot_call_sequence(elems, label, tuple_result)


def _verified_ir(source):
    module = infer_module(parse_and_lift(source, "slot_operand.py", "slot_operand"))
    codegen = RoutingCodegen(module, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    codegen._target_triple = "x86_64-unknown-linux-gnu"
    text = 'target triple = "x86_64-unknown-linux-gnu"\n' + str(codegen.generate(module))
    prepared = prepare_module_for_target(
        text, aggregate_returned_indirect=_aggregate_returned_indirect,
    )
    plans = build_stack_map_plans(
        prepared.functions, prepared.globals_, target="x86_64-linux",
    )
    assert len(plans) == len(prepared.functions)
    return codegen, text


def test_ordinary_dynamic_call_uses_direct_literal_without_probe_interception():
    _, text = _verified_ir(
        "def probe(capture, value):\n    return capture((value, value))\n"
    )
    body = _probe_function(text)
    # One tuple holds the two literal elements; the other holds the single
    # positional argument. This is the unmodified public lowering dispatch.
    assert len(_calls(body, "py_tuple_new")) == 2
    assert len(_calls(body, "py_tuple_set_item")) == 3
    assert not _calls(body, "py_list_new")
    assert not _calls(body, "py_list_append")
    assert not _calls(body, "py_tuple_from_list")


def test_module_dynamic_tuple_literal_balances_root_frames():
    _, text = _verified_ir(
        "def capture(value):\n    return value\n"
        "value = []\nfn = capture\nresult = fn((value, value))\n"
    )
    assert not _calls(text, "py_tuple_from_list")


@pytest.mark.parametrize("statement,routes", (
    ("yield slot_operand_probe((value, value))", ["streaming"]),
    ("yield slot_operand_probe(((yield value), value))", []),
))
def test_generator_tuple_operand_keeps_existing_lowering(statement, routes):
    codegen, _ = _verified_ir("def probe(value):\n    " + statement + "\n")
    # An actual suspending operand is handled even earlier by generator
    # spilling; neither generator route enters the new fixed-literal branch.
    assert codegen.literal_routes == routes
    assert any(flag is not None and not lifo
               for _slot, flag, lifo in codegen._slot_call_root_records)


@pytest.mark.parametrize("body", (
    "return slot_operand_probe((first(), second()))",
    "try:\n        return slot_operand_probe((first(), second()))\n"
    "    except ValueError:\n        return None",
))
def test_tuple_evaluates_left_to_right_before_allocation_with_balanced_roots(body):
    codegen, text = _verified_ir(
        "def first():\n    return [1]\n"
        "def second():\n    raise ValueError('second')\n"
        "def probe():\n    " + body + "\n"
    )
    assert codegen.literal_routes == ["fixed"]
    body = _probe_function(text)
    first = re.search(r"call [^\n]*@user_[^\n(]*_first\(", body)
    second = re.search(r"call [^\n]*@user_[^\n(]*_second\(", body)
    allocation = _calls(body, "py_tuple_new")
    assert first is not None and second is not None and len(allocation) == 1
    assert first.start() < second.start() < body.index(allocation[0])
    assert not _calls(body, "py_list_new")
    assert not _calls(body, "py_tuple_from_list")


class ModelFailure(Exception):
    pass


class TupleModel(CallObjectLoweringMixin):
    """Run the real dispatcher, fixed/streaming emitters and root release.

    Runtime allocation, retaining stores and error-edge execution are modeled;
    real emitted error joins are independently checked by _verified_ir above.
    """
    def __init__(self, evaluators, fail_at=None):
        self.evaluators = evaluators
        self.fail_at = fail_at
        self.events = []
        self.live = []
        self.stack = []
        self._slot_call_root_records = []
        self._try_err_block = "outer"
        self._cpy_operand_cleanup_block = "outer-cpy"
        self.runtime = {name: name for name in (
            "py_tuple_new", "py_list_new", "pcc_gc_store_root",
        )}
        self.builder = SimpleNamespace(call=self.call)

    _expr_returns_unsafe_raw_pointer = lambda self, expr: False
    _emit_class_namespace_name_root = lambda self, expr, label: None
    _emit_lambda_adapter_name_root = lambda self, expr, label: None
    _slot_call_published_module_ref = lambda self, expr: None
    _emit_slot_call_valueclass_name = lambda self, expr, label: None
    _fresh = lambda self, label: label
    _as_gc_ptr = lambda self, slot: slot
    _current_try_err_block = lambda self: self._try_err_block

    def _new_slot_call_root(self, label):
        slot = [None]
        self.stack.append(slot)
        self._slot_call_root_records.append((slot, None, True))
        return slot

    def _slot_call_cleanup_block(self, roots, target):
        return tuple(roots), target

    def _emit_gc_frame_leave_lifo_for_slot(self, slot):
        assert self.stack[-1] is slot
        self.stack.pop()

    def node(self, label, children=()):
        value = SimpleNamespace(label=label, refs=1, children=list(children))
        self.live.append(value)
        return value

    def retain(self, value):
        assert value.refs > 0
        value.refs += 1
        return value

    def release(self, value):
        if value is None:
            return
        value.refs -= 1
        assert value.refs >= 0
        if value.refs == 0:
            self.events.append("drop:" + value.label)
            for child in reversed(value.children):
                self.release(child)

    def checkpoint(self, name):
        self.events.append(name)
        if self.fail_at != name:
            return
        cleanup = self._try_err_block
        while cleanup != "outer":
            roots, cleanup = cleanup
            self._release_slot_call_roots(roots)
        raise ModelFailure(name)

    def _emit_slot_call_operand(self, expr, label, borrow_stable=False):
        if isinstance(expr, TupleExpr):
            return super()._emit_slot_call_operand(expr, label, borrow_stable)
        slot = self._new_slot_call_root(label)
        previous = self._try_err_block
        self._try_err_block = self._slot_call_cleanup_block((slot,), previous)
        try:
            self.checkpoint("eval:" + expr.ident)
            self._publish_slot_call_owned(slot, self.evaluators[expr.ident](self))
        finally:
            self._try_err_block = previous
        return slot

    def call(self, runtime_name, arguments, **kwargs):
        if runtime_name == "pcc_gc_store_root":
            slot = arguments[0]
            value, slot[0] = slot[0], None
            self.release(value)
            return
        assert runtime_name in ("py_tuple_new", "py_list_new")
        self.checkpoint(runtime_name)
        return self.node(runtime_name, [None] * arguments[0].value)

    def _publish_slot_call_owned(self, slot, value, **kwargs):
        assert slot[0] is None
        slot[0] = value

    def _slot_call_runtime_call(self, name, roots, *, result_slot=None, suffix_args=(), **kwargs):
        self.checkpoint(name)
        if name == "py_tuple_set_item":
            index = suffix_args[0].value
            self.checkpoint("before-set:" + str(index))
            assert roots[0][0].children[index] is None
            roots[0][0].children[index] = self.retain(roots[1][0])
            self.checkpoint("after-set:" + str(index))
        elif name == "py_list_append":
            roots[0][0].children.append(self.retain(roots[1][0]))
        elif name == "py_tuple_from_list":
            self._publish_slot_call_owned(result_slot, self.node(
                "converted", [self.retain(child) for child in roots[0][0].children],
            ))
        else:
            raise AssertionError(name)


def _tuple(*names):
    span = SourceSpan(file="model.py", line=1, col=1, end_line=1, end_col=2)
    return TupleExpr(span=span, ty=DynType(name="dyn"), elems=tuple(
        Name(span=span, ty=DynType(name="dyn"), ident=name) for name in names
    ))


def test_literal_model_preserves_aliases_and_owners_across_later_evaluation():
    model = TupleModel({})
    shared = model.node("shared")
    binding = [shared]

    def later(current):
        assert shared.refs == 3  # External owner and two independent operands.
        binding[0] = current.node("replacement")
        current.release(shared)
        return current.retain(binding[0])

    model.evaluators = {
        "first": lambda current: current.retain(binding[0]),
        "alias": lambda current: current.retain(binding[0]),
        "later": later,
    }
    output = model._emit_slot_call_operand(_tuple("first", "alias", "later"), "literal")
    assert output[0].children == [shared, shared, binding[0]]
    assert output[0].children[0] is output[0].children[1]
    assert shared.refs == 2 and binding[0].refs == 2
    assert model.events[:4] == ["eval:first", "eval:alias", "eval:later", "py_tuple_new"]
    assert "py_list_new" not in model.events and "py_tuple_from_list" not in model.events
    assert len(model.stack) == 1 and model.stack[0] is output
    model._release_slot_call_roots((output,))
    model.release(binding[0])
    assert all(value.refs == 0 for value in model.live)
    assert not model.stack


@pytest.mark.parametrize("failure", (
    "eval:first", "eval:second", "py_tuple_new", "before-set:0", "after-set:0",
    "before-set:1", "after-set:1",
))
def test_literal_model_failure_releases_completed_and_partial_owners(failure):
    model = TupleModel({
        "first": lambda current: current.node("first"),
        "second": lambda current: current.node("second"),
    }, failure)
    with pytest.raises(ModelFailure, match=re.escape(failure)):
        model._emit_slot_call_operand(_tuple("first", "second"), "literal")
    assert all(value.refs == 0 for value in model.live)
    assert not model.stack
    assert model._try_err_block == "outer"
    assert model._cpy_operand_cleanup_block == "outer-cpy"
    dropped = Counter(event for event in model.events if event.startswith("drop:"))
    assert all(count == 1 for count in dropped.values())
    if failure == "eval:first":
        assert "eval:second" not in model.events and "py_tuple_new" not in model.events
    elif failure == "eval:second":
        assert "py_tuple_new" not in model.events


PROGRAM = '''import gc
import weakref
events = []
refs = []
class Item:
    def __init__(self, label):
        self.label = label
        refs.append(weakref.ref(self))
    def __del__(self):
        events.append("drop:" + self.label)
        gc.collect()
def make(label):
    events.append(label)
    gc.collect()
    return Item(label)
def capture(value):
    return value
def fail():
    events.append("fail")
    raise ValueError("selected")
def probe(capture):
    result = capture((make("first"), make("second")))
    assert events == ["first", "second"]
    assert result[0] is refs[0]() and result[1] is refs[1]()
    del result
    gc.collect()
    assert refs[0]() is None and refs[1]() is None
    assert events[-2:] == ["drop:second", "drop:first"]
    events.clear()
    try:
        capture((make("before-error"), fail(), make("unreached")))
    except ValueError as error:
        assert str(error) == "selected"
    else:
        raise AssertionError("missing error")
    gc.collect()
    assert events == ["before-error", "fail", "drop:before-error"]
    alias = []
    result = capture((alias, alias, None, True, "text", b"bytes", 1 << 100, -(1 << 100)))
    assert result[0] is alias and result[1] is alias
    alias.append(7)
    assert result[0] == [7] and result[2:] == (None, True, "text", b"bytes", 1 << 100, -(1 << 100))
    assert capture(()) == ()
    assert capture((0, 1, 2, 3, 4, 5, 6, 7, 8)) == tuple(range(9))
    assert capture((0, *[1, 2], 3)) == (0, 1, 2, 3)
probe(capture)
print("FIXED_TUPLE_LITERAL_OK")
'''


def test_fixed_tuple_literal_reference(tmp_path):
    source = tmp_path / "reference.py"
    source.write_text(PROGRAM)
    result = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=20)
    assert (result.returncode, result.stdout, result.stderr) == (0, "FIXED_TUPLE_LITERAL_OK\n", "")


@pytest.mark.integration
def test_fixed_tuple_literal_native(
    tmp_path, explicit_owned_runtime, python_program_compiler, request, capfd,
):
    from tests.python.owned_regression_support import assert_owned_program

    assert_owned_program(
        PROGRAM, "FIXED_TUPLE_LITERAL_OK\n", tmp_path, python_program_compiler,
        request.node.callspec.params["python_program_compiler"], explicit_owned_runtime, capfd,
    )
