"""Exhausted Lambda alternatives retain bounded, attempt-local evidence."""
from types import SimpleNamespace

import pytest

from pcc.ir.compat import ir
from pcc.frontends.python.codegen import expr_dispatch_lowering as dispatch
from pcc.frontends.python.codegen.lambda_helpers_lowering import (
    LambdaHelperLoweringMixin,
    _PER_FUNCTION_OWNERSHIP_DICTS,
    _PER_FUNCTION_OWNERSHIP_SETS,
    _record_native_lambda_rejection,
)
from pcc.frontends.python.py_ast import Arg, DynType, Expr, Lambda, Module, Name, SourceSpan


SPAN = SourceSpan("pkg/callbacks.py", 17, 9, 17, 30)
DYN = DynType(name="dyn")


def lambda_expr(names=(), *, span=SPAN):
    return Lambda(
        span=span, ty=DYN,
        params=tuple(Arg(name, None, None, "pos") for name in names),
        body=Name(span=SPAN, ty=DYN, ident="value"),
    )


class DispatchHost(dispatch.ExprDispatchLoweringMixin):
    def __init__(self, prefer_native, winner=None, failure=None):
        self._prefer_native_callable_values = prefer_native
        self.winner = winner
        self.failure = failure
        self.calls = []
        self.receipts = []
        self.result = object()

    def _maybe_emit_native_lambda_func(self, expr, rejection=None):
        self.calls.append("native")
        self.receipts.append(rejection)
        if self.failure is not None:
            raise self.failure
        if self.winner == "native":
            return self.result
        _record_native_lambda_rejection(rejection, "slot-body")
        return None

    def _maybe_emit_simple_lambda(self, expr):
        self.calls.append("simple")
        return self.result if self.winner == "simple" else None

    def _maybe_emit_lambda_wrap(self, expr):
        self.calls.append("wrap")
        return self.result if self.winner == "wrap" else None


@pytest.mark.parametrize("prefer_native", [False, True])
@pytest.mark.parametrize("winner", ["native", "simple", "wrap"])
def test_dispatch_preserves_alternative_order_and_success(prefer_native, winner, monkeypatch):
    def unexpected(*args):
        raise AssertionError("successful lowering must not format diagnostics")

    monkeypatch.setattr(dispatch, "_lambda_rejection_detail", unexpected)
    host = DispatchHost(prefer_native, winner)
    assert host._emit_expr_impl(lambda_expr()) is host.result
    order = ["native", "simple", "wrap"] if prefer_native else ["simple", "native", "wrap"]
    assert host.calls == order[:order.index(winner) + 1]


@pytest.mark.parametrize("prefer_native", [False, True])
def test_exhaustion_identifies_source_and_actual_native_stage(prefer_native):
    host = DispatchHost(prefer_native)
    with pytest.raises(NotImplementedError) as caught:
        host._emit_expr_impl(lambda_expr())
    message = str(caught.value)
    assert type(caught.value) is NotImplementedError
    assert message.startswith("Layer 1 does not handle expression Lambda")
    assert "source=pkg/callbacks.py:17:9" in message
    assert "native-stage=slot-body" in message
    assert "native-reason=unknown-body-rejection" in message
    assert "native-exception=NotImplementedError native-ir=partial" in message
    assert "fallbacks=exhausted" in message
    assert host.calls == (["native", "simple", "wrap"] if prefer_native else ["simple", "native", "wrap"])


@pytest.mark.parametrize("error", [ValueError("direct"), NotImplementedError("direct")])
def test_direct_native_exceptions_keep_identity_and_do_not_try_fallbacks(error):
    host = DispatchHost(True, failure=error)
    with pytest.raises(type(error)) as caught:
        host._emit_expr_impl(lambda_expr())
    assert caught.value is error
    assert host.calls == ["native"]


def test_non_lambda_failure_is_unchanged():
    with pytest.raises(NotImplementedError, match="^Layer 1 does not handle expression Expr$"):
        DispatchHost(True)._emit_expr_impl(Expr(span=SPAN, ty=DYN))


def test_diagnostic_failure_cannot_replace_original_failure(monkeypatch):
    def broken(*args):
        raise ValueError("diagnostic failure")

    monkeypatch.setattr(dispatch, "_lambda_rejection_detail", broken)
    with pytest.raises(NotImplementedError) as caught:
        DispatchHost(True)._emit_expr_impl(lambda_expr())
    assert str(caught.value) == "Layer 1 does not handle expression Lambda"


def test_nested_and_repeated_attempts_do_not_share_receipts():
    class NestedHost(DispatchHost):
        def _maybe_emit_native_lambda_func(self, expr, rejection=None):
            self.receipts.append(rejection)
            if len(self.receipts) == 1:
                with pytest.raises(NotImplementedError) as inner:
                    self._emit_expr_impl(lambda_expr(span=SourceSpan("inner.py", 2, 3, 2, 4)))
                assert "native-stage=preflight" in str(inner.value)
                assert "source=inner.py:2:3" in str(inner.value)
                _record_native_lambda_rejection(rejection, "legacy-body")
            else:
                _record_native_lambda_rejection(rejection, "arity")
            return None

    host = NestedHost(True)
    with pytest.raises(NotImplementedError) as outer:
        host._emit_expr_impl(lambda_expr())
    assert "native-stage=legacy-body" in str(outer.value)
    assert "source=pkg/callbacks.py:17:9" in str(outer.value)
    with pytest.raises(NotImplementedError) as repeated:
        host._emit_expr_impl(lambda_expr())
    assert "native-reason=arity" in str(repeated.value)
    assert len({id(receipt) for receipt in host.receipts}) == 3
    assert host.receipts == [["legacy-body"], ["arity"], ["arity"]]


def test_bounded_source_coordinates_and_unknown_codes():
    span = SourceSpan("x" * 10000 + "\nsecret.py\t", 2147483647, 0, 1, 1)
    detail = dispatch._lambda_rejection_detail(lambda_expr(span=span), ["unrecognized-code"])
    assert "source=..." in detail and "?secret.py?" in detail
    assert "\n" not in detail and "\t" not in detail
    assert len(detail) < 450
    assert "native-stage=unknown native-reason=unknown" in detail
    assert "unrecognized-code" not in detail


def test_invalid_span_values_are_not_stringified():
    class NeverStringify:
        def __str__(self):
            raise AssertionError("arbitrary span value was stringified")

        __repr__ = __str__

    class StringSubclass(str):
        def __getitem__(self, key):
            raise AssertionError("span string subclass was sliced")

        def __len__(self):
            raise AssertionError("span string subclass length was read")

    class IntSubclass(int):
        def __format__(self, spec):
            raise AssertionError("span integer subclass was formatted")

    for span in (None, SimpleNamespace(file=NeverStringify(), line=1, col=2),
                 SourceSpan("invalid.py", -1, 2, 1, 2),
                 SourceSpan("invalid.py", 2147483648, 2, 1, 2),
                 SourceSpan("invalid.py", True, 2, 1, 2),
                 SourceSpan(StringSubclass("invalid.py"), 1, 2, 1, 2),
                 SourceSpan("invalid.py", IntSubclass(1), 2, 1, 2)):
        detail = dispatch._lambda_rejection_detail(lambda_expr(span=span), [])
        assert "source=unknown" in detail


def test_broken_span_or_poisoned_record_keeps_original_failure():
    class BrokenSpan:
        @property
        def file(self):
            raise ValueError("span unavailable")

    class PoisonedCode:
        def __eq__(self, other):
            raise ValueError("record unavailable")

    class SpoofedCode:
        def __eq__(self, other):
            return True

        def __format__(self, spec):
            raise AssertionError("arbitrary rejection code was formatted")

        def __str__(self):
            raise AssertionError("arbitrary rejection code was stringified")

    class PoisonedHost(DispatchHost):
        code_type = PoisonedCode

        def _maybe_emit_native_lambda_func(self, expr, rejection=None):
            rejection.append(self.code_type())
            return None

    spoofed = PoisonedHost(True)
    spoofed.code_type = SpoofedCode
    for host, span in ((DispatchHost(True), BrokenSpan()), (PoisonedHost(True), SPAN),
                       (spoofed, SPAN)):
        with pytest.raises(NotImplementedError) as caught:
            host._emit_expr_impl(lambda_expr(span=span))
        assert str(caught.value) == "Layer 1 does not handle expression Lambda"


class UnprintableBodyError(NotImplementedError):
    def __str__(self):
        raise AssertionError("body exception payload must not be stringified")

    __repr__ = __str__


class NativeHost(LambdaHelperLoweringMixin, dispatch.ExprDispatchLoweringMixin):
    """Real native helper control flow/IR builder, inert ownership producers.

    This does not validate generated machine code or repair partial adapters.
    It reaches the production preflight and both existing body catch blocks.
    """
    def __init__(self, legacy=False, free=()):
        self.module = ir.Module(name="rejection")
        self.ast_module = SimpleNamespace(name="rejection")
        ptr, i64 = ir.IntType(8).as_pointer(), ir.IntType(64)
        self.runtime = {
            "py_tuple_get": ir.Function(self.module, ir.FunctionType(ptr, [ptr, i64]), name="get"),
            "py_tuple_len": ir.Function(self.module, ir.FunctionType(i64, [ptr]), name="len"),
        }
        self.current_function = ir.Function(self.module, ir.FunctionType(ptr, []), name="outer")
        self._current_entry_block = self.current_function.append_basic_block("entry")
        self.builder = ir.IRBuilder(self._current_entry_block)
        self.current_func_def = object()
        self.functions = {}
        self.env = {"kept": (object(), ptr, DYN)}
        self.env_class_hint = {"kept": "Outer"}
        self.env_class_object_hint = {"kept": "OuterClass"}
        self._cpy_env_flags = {"kept": legacy}
        self._cpy_values, self._owned_cpy_values = {object()}, {object()}
        self.loop_stack = [object()]
        self._try_err_block, self._cpy_operand_cleanup_block = object(), object()
        self._lambda_adapter_root_scope = (self.current_function, ())
        for field in _PER_FUNCTION_OWNERSHIP_DICTS:
            setattr(self, field, {"outer": object()})
        for field in _PER_FUNCTION_OWNERSHIP_SETS:
            setattr(self, field, {"outer"})
        self.free = {"kept"} if legacy else set(free)
        self.error = UnprintableBodyError("private body payload")
        self._prefer_native_callable_values = True
        self.winner = None
        self.fallback_calls = []
        self.fallback_result = object()

    def _maybe_emit_simple_lambda(self, expr):
        self.fallback_calls.append("simple")
        return self.fallback_result if self.winner == "simple" else None

    def _maybe_emit_lambda_wrap(self, expr):
        self.fallback_calls.append("wrap")
        return self.fallback_result if self.winner == "wrap" else None

    def _lambda_free_vars(self, expr, param_names):
        return self.free

    def _is_valueclass_payload_type(self, ty):
        return False

    def _module_symbol_suffix(self, name):
        return name

    def _fresh(self, name):
        return name

    def _new_slot_call_root(self, label):
        return object()

    def _ensure_fn_err_exit(self):
        return object()

    def _slot_call_cleanup_block(self, roots, target):
        return object()

    def _alloca_in_entry(self, *args, **kwargs):
        return object()

    def _as_gc_ptr(self, value):
        return value

    def _gc_one_slot_frame_map(self):
        return object()

    def _emit_current_gc_frame_enter_lifo(self, *args):
        pass

    def _enter_container_temp_root(self, *args):
        return object()

    def _make_cpy_operand_cleanup_block(self, *args, **kwargs):
        return object()

    def _emit_slot_call_operand(self, *args):
        raise self.error

    def _emit_expr(self, expr):
        raise self.error


@pytest.mark.parametrize("names,free,code", [
    (("a", "b", "c", "d"), (), "arity"),
    (("",), (), "parameter-name"),
    ((), ("missing",), "free-variable"),
])
def test_actual_native_preflight_rejects_without_mutating_host(names, free, code):
    host = NativeHost(free=free)
    before = dict(vars(host))
    globals_before = dict(host.module.globals)
    rejection = []
    assert host._maybe_emit_native_lambda_func(lambda_expr(names), rejection) is None
    assert rejection == [code]
    assert vars(host) == before
    assert dict(host.module.globals) == globals_before


@pytest.mark.parametrize("legacy", [False, True])
def test_actual_native_body_rejection_preserves_existing_restoration(legacy):
    host = NativeHost(legacy=legacy)
    saved = {field: getattr(host, field) for field in (
        "builder", "env", "env_class_hint", "env_class_object_hint",
        "current_function", "current_func_def", "loop_stack", "_current_entry_block",
        "_try_err_block", "_cpy_operand_cleanup_block", "_lambda_adapter_root_scope",
        *_PER_FUNCTION_OWNERSHIP_DICTS, *_PER_FUNCTION_OWNERSHIP_SETS,
    )}
    copied = {field: getattr(host, field).copy() for field in (
        "_cpy_env_flags", "_cpy_values", "_owned_cpy_values",
    )}
    rejection = []
    assert host._maybe_emit_native_lambda_func(lambda_expr(), rejection) is None
    assert rejection == ["legacy-body" if legacy else "slot-body"]
    assert all(getattr(host, field) is value for field, value in saved.items())
    assert all(getattr(host, field) == value for field, value in copied.items())
    # Existing behavior: the abandoned adapter and counter are NOT rolled back.
    # The diagnostic labels partial IR; it never turns this into a success.
    assert host._native_lambda_func_counter == 1
    adapter = host.module.globals["user_rejection__native_lambda_0"]
    assert adapter.blocks and adapter.blocks[0].terminator is None
    assert "native-ir=partial" in dispatch._lambda_rejection_detail(lambda_expr(), rejection)


def test_optional_or_broken_receipt_does_not_change_native_rejection():
    class BrokenReceipt(list):
        def append(self, value):
            raise RuntimeError("receipt unavailable")

    assert NativeHost()._maybe_emit_native_lambda_func(lambda_expr()) is None
    for receipt in (None, BrokenReceipt()):
        host = NativeHost()
        assert host._maybe_emit_native_lambda_func(lambda_expr(), receipt) is None
    receipt = []
    _record_native_lambda_rejection(receipt, "arity")
    _record_native_lambda_rejection(receipt, "legacy-body")
    assert receipt == ["arity"]


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("winner", ["simple", "wrap", None])
def test_public_dispatch_consumes_actual_native_body_rejection(legacy, winner):
    host = NativeHost(legacy=legacy)
    builder, environment = host.builder, host.env
    host.winner = winner
    if winner is None:
        with pytest.raises(NotImplementedError) as caught:
            host._emit_expr_impl(lambda_expr())
        detail = str(caught.value)
        assert "native-stage=" + ("legacy-body" if legacy else "slot-body") in detail
        assert "source=pkg/callbacks.py:17:9" in detail
        assert "private body payload" not in detail
    else:
        assert host._emit_expr_impl(lambda_expr()) is host.fallback_result
    assert host.fallback_calls == (["simple"] if winner == "simple" else ["simple", "wrap"])
    assert host.builder is builder and host.env is environment


def test_actual_body_non_notimplemented_exception_is_not_swallowed():
    host = NativeHost()
    error = ValueError("unexpected body failure")
    host.error = error
    with pytest.raises(ValueError) as caught:
        host._emit_expr_impl(lambda_expr())
    assert caught.value is error
    assert host.fallback_calls == []


@pytest.mark.parametrize("prefer_native", [False, True])
def test_real_codegen_exhaustion_keeps_native_preflight_receipt(prefer_native):
    from pcc.frontends.python.codegen.layer1 import L1CodeGen

    codegen = L1CodeGen(Module(name="lambda_rejected", body=()), ir_scaffold_mode="on")
    codegen._prefer_native_callable_values = prefer_native
    codegen._strict_no_libpython = True
    with pytest.raises(NotImplementedError) as caught:
        codegen._emit_expr_impl(lambda_expr(("a", "b", "c", "d")))
    message = str(caught.value)
    assert type(caught.value) is NotImplementedError
    assert "source=pkg/callbacks.py:17:9" in message
    assert "native-stage=preflight native-reason=arity" in message
    assert "native-exception=none native-ir=none" in message
    assert "fallbacks=exhausted" in message


@pytest.mark.parametrize("source", [
    "def probe(kept):\n    return lambda: kept\n",
    "def probe(kept):\n    return lambda left, right: (kept, left, right)\n",
])
def test_supported_captured_native_lambda_remains_native(source):
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.type_infer import infer_module

    module = infer_module(parse_and_lift(source, "lambda_supported.py", "lambda_supported"))
    codegen = L1CodeGen(module, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    text = str(codegen.generate(module))
    assert "__native_lambda_" in text
    assert not any("call " in line and "@py_cpy_" in line for line in text.splitlines())
