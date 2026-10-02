"""Unary and residual call helper lowering for L1CodeGen."""

from __future__ import annotations

from typing import Optional

from pcc.ir.compat import ir

from pcc.frontends.python.py_ast import RawPointerType, Attr, BoolLit, BoolType, Call, ClassType, ComplexType, DynType, Expr, FloatType, IntLit, IntType, Name, SetType, SourceSpan, StrLit, StrType, Subscript, Type, UnaryOp
from pcc.frontends.python.codegen import marshal

_DOUBLE = ir.DoubleType()
_CPY_BUILTIN_TYPE_NAMES = frozenset(
    {
        "BaseException",
        "Exception",
        "bool",
        "bytearray",
        "bytes",
        "complex",
        "dict",
        "float",
        "frozenset",
        "int",
        "list",
        "object",
        "set",
        "str",
        "tuple",
        "type",
    }
)


def folded_int_literal(expr: Expr) -> object:
    if isinstance(expr, IntLit) and isinstance(expr.value, int) and not isinstance(expr.value, bool):
        return expr.value
    if isinstance(expr, UnaryOp) and expr.op in ("-", "~", "+"):
        value: object = folded_int_literal(expr.operand)
        if value is not None:
            if expr.op == "-":
                return -value
            if expr.op == "~":
                return ~value
            return value
    return None


def is_i64_int_literal(expr: Expr) -> bool:
    value = folded_int_literal(expr)
    return value is not None and -(1 << 63) <= value <= (1 << 63) - 1


class UnaryCallLoweringMixin:
    def _emit_unary(self, expr: UnaryOp) -> ir.Value:
        if expr.op == "not":
            truth = self._emit_condition_value(expr.operand)
            return self.builder.not_(truth, name=self._fresh("not"))
        ty = expr.operand.ty
        folded = folded_int_literal(expr)
        if folded is not None:
            # A signed or inverted integer literal is a compile-time constant
            # (``return 0 if ok else -1``); fold it instead of emitting int
            # arithmetic that freestanding code would have to annotate.
            return self._emit_expr(IntLit(span=expr.span, ty=expr.ty, value=folded))
        if (
            getattr(self, "_freestanding_module", False)
            and isinstance(ty, IntType)
            and ty.name == "int"
            and expr.op in ("+", "-", "~")
        ):
            raise RuntimeError(
                "freestanding ordinary Python int unary arithmetic cannot "
                "preserve arbitrary precision; annotate the machine boundary "
                "with pcc.i64 or pcc.u64"
            )
        if expr.op in ("-", "~") and isinstance(ty, ClassType):
            # CPython dispatches __neg__/__invert__; the numeric coercion
            # below crashed with "cannot coerce ClassType to int" (found on
            # numpy _pep440's `-Infinity`). Dispatch BEFORE emitting the
            # operand: the dunder helper emits the receiver itself.
            dunder_name = "__neg__" if expr.op == "-" else "__invert__"
            dunder = self._try_dispatch_dunder_unary(
                expr.operand,
                dunder_name,
                (),
            )
            if dunder is not None:
                return dunder
            raise NotImplementedError(
                f"Layer 1 cannot resolve {dunder_name} for unary "
                f"{expr.op!r} on a class instance"
            )
        operand = self._emit_expr(expr.operand)
        operand_is_cpy = operand in getattr(self, "_cpy_values", ())
        if operand_is_cpy and expr.op in ("+", "-", "~"):
            # Preserve CPython numeric protocol semantics (including custom
            # dunders and arbitrary-precision ints) without a new runtime ABI:
            # acquire the unary method and reuse the existing guarded no-arg
            # callable contract.  _emit_cpy_attr consumes only a fresh
            # receiver; borrowed CPython locals stay borrowed.
            dunder_name = {
                "+": "__pos__",
                "-": "__neg__",
                "~": "__invert__",
            }[expr.op]
            fn_val = self._emit_cpy_attr(operand, dunder_name)
            return self._emit_cpy_func_call(fn_val, dunder_name, ())
        if (
            expr.op in ("+", "-", "~")
            and isinstance(operand.type, ir.PointerType)
            and not isinstance(ty, (IntType, BoolType, FloatType, ComplexType))
        ):
            # A dynamically-typed operand can be an int, a float, a complex or
            # an instance with __neg__: dispatch at run time.  The numeric
            # fallthrough below reads the operand through the i64 lane, which
            # returned the int 0 for ``-f`` on a dyn float -- pcc1's
            # ``_bits_to_float64`` produced 0.0 for every negative double.
            return self._emit_dynamic_unary(expr, operand)
        if expr.op == "+":
            return operand
        if expr.op == "-":
            if isinstance(ty, ComplexType):
                obj = marshal.marshal_to_object(
                    self.builder, self.module, self.runtime, operand, ty
                )
                return self.builder.call(
                    self.runtime["py_complex_neg"],
                    [obj],
                    name=self._fresh("complex.neg"),
                )
            if isinstance(ty, FloatType):
                return self.builder.fneg(operand, name=self._fresh("fneg"))
            if (self._int_exprs_are_boxed() and isinstance(ty, (IntType, BoolType))
                    and not (isinstance(ty, IntType) and ty.name in ("pcc.i64", "pcc.u64"))):
                operand_obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    operand,
                    ty,
                )
                operand_owned = (
                    isinstance(operand.type, ir.PointerType)
                    and operand not in getattr(self, "_cpy_values", ())
                    and self._pcc_pointer_source_is_owned(expr.operand)
                )
                operand_pinned = (
                    isinstance(operand.type, ir.PointerType)
                    and operand not in getattr(self, "_cpy_values", ())
                )
                operand_cleanup = ()
                if operand_pinned:
                    self._gc_pin(operand)
                    operand_cleanup = ((operand, operand_owned),)
                result = self.builder.call(
                    self.runtime["py_int_neg"],
                    [operand_obj],
                    name=self._fresh("int.obj.neg"),
                )
                self._emit_post_call_err_check(
                    None,
                    pinned_release_on_error=operand_cleanup,
                )
                self._guard_cpy_value_not_null(
                    result,
                    pinned_pcc_on_error=operand_cleanup,
                )
                if operand_pinned:
                    self._gc_unpin(operand)
                if operand_owned:
                    self._gc_release(operand)
                return result
            ival = self._to_int64(operand, ty)
            return self.builder.neg(ival, name=self._fresh("neg"))
        if expr.op == "~":
            if (self._int_exprs_are_boxed() and isinstance(ty, (IntType, BoolType))
                    and not (isinstance(ty, IntType) and ty.name in ("pcc.i64", "pcc.u64"))):
                operand_obj = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    operand,
                    ty,
                )
                minus_one = self._emit_int_literal_object(-1)
                operand_owned = (
                    isinstance(operand.type, ir.PointerType)
                    and operand not in getattr(self, "_cpy_values", ())
                    and self._pcc_pointer_source_is_owned(expr.operand)
                )
                operand_pinned = (
                    isinstance(operand.type, ir.PointerType)
                    and operand not in getattr(self, "_cpy_values", ())
                )
                operand_cleanup = ()
                if operand_pinned:
                    self._gc_pin(operand)
                    operand_cleanup = ((operand, operand_owned),)
                result = self.builder.call(
                    self.runtime["py_int_xor"],
                    [operand_obj, minus_one],
                    name=self._fresh("int.obj.invert"),
                )
                self._emit_post_call_err_check(
                    None,
                    pinned_release_on_error=operand_cleanup,
                )
                self._guard_cpy_value_not_null(
                    result,
                    pinned_pcc_on_error=operand_cleanup,
                )
                if operand_pinned:
                    self._gc_unpin(operand)
                if operand_owned:
                    self._gc_release(operand)
                return result
            ival = self._to_int64(operand, ty)
            return self.builder.not_(ival, name=self._fresh("bnot"))

        raise NotImplementedError(f"Layer 1 unary {expr.op!r} not supported")

    def _emit_dynamic_unary(self, expr: UnaryOp, operand: ir.Value) -> ir.Value:
        runtime_name = {
            "+": "py_obj_pos",
            "-": "py_obj_neg",
            "~": "py_obj_invert",
        }[expr.op]
        operand_owned = (
            operand not in getattr(self, "_cpy_values", ())
            and self._pcc_pointer_source_is_owned(expr.operand)
        )
        operand_pinned = operand not in getattr(self, "_cpy_values", ())
        operand_cleanup = ()
        if operand_pinned:
            self._gc_pin(operand)
            operand_cleanup = ((operand, operand_owned),)
        result = self.builder.call(
            self.runtime[runtime_name],
            [operand],
            name=self._fresh("dyn.unary"),
        )
        self._emit_post_call_err_check(
            self._expr_span_or_none(expr),
            pinned_release_on_error=operand_cleanup,
        )
        self._guard_cpy_value_not_null(
            result,
            pinned_pcc_on_error=operand_cleanup,
        )
        if operand_pinned:
            self._gc_unpin(operand)
        if operand_owned:
            self._gc_release(operand)
        return result

    # -- Compare -------------------------------------------------------

    # -- BoolExpr ------------------------------------------------------

    # -- Call ----------------------------------------------------------

    def _call_user(
        self,
        fn: ir.Function,
        args_ir: list[ir.Value],
        call_name: str,
        span: Optional[SourceSpan] = None,
        root_result: bool = False,
        pinned_arg_temps: tuple[tuple[ir.Value, bool], ...] = (),
        result_slot=None,
    ) -> ir.Value:
        """Call a user function; after the call, check py_err_occurred()
        and branch to the active error-propagation block if a Python
        exception is pending. When we're inside a try block that block
        is the except-dispatch (self._try_err_block); otherwise it is
        the enclosing function's err-exit epilogue.

        This replaces an earlier Itanium-ABI design that used `invoke`
        + landingpad to route exceptions via libc++abi. Return-code
        style (CPython ceval.c) is portable, debuggable, and keeps
        libc++abi out of the runtime link.
        """
        # An operand computed before a park boundary may have been defined in
        # a block that does not dominate this one (may_park splits a function
        # at every park).  Global-backed operands are re-derived here; every
        # other value passes through unchanged.
        args_ir = [self._value_available_at_insertion_point(a) for a in args_ir]
        result = self.builder.call(fn, args_ir, name=call_name)
        root_slot = None
        root_ptr = None
        if result_slot is not None and isinstance(result.type, ir.PointerType):
            # The expression consumer registered this empty owning output
            # before any operand evaluation. Publish at the actual return
            # instruction, ahead of error checks and argument cleanup.
            self._publish_slot_call_owned(result_slot, result, label="user call")
        if (
            root_result
            and result_slot is None
            and isinstance(result.type, ir.PointerType)
            and not getattr(self, "_suppress_implicit_gc_roots", False)
            and not (self.ast_module.name or "").startswith("pcc.runtime.py.")
        ):
            # Entry-block alloca, NOT builder.alloca at the call site: a
            # call inside a loop body re-executes a body-positioned alloca
            # every iteration and the stack is only reclaimed at function
            # exit, so hot loops overflow the 8MB stack after ~500K
            # iterations (shared-refcount contention benchmark, SIGSEGV in
            # py_incref prologue). The null re-store below stays at the
            # call site: the slot is nulled again after every use, so the
            # store is a no-op on re-entry but keeps the first-use
            # store_root from decref'ing garbage.
            root_slot = self._alloca_in_entry(
                result.type,
                self._fresh("call.ret.root"),
            )
            self.builder.store(ir.Constant(result.type, None), root_slot)
            root_ptr = self._as_gc_ptr(
                root_slot,
                name=self._fresh("call.ret.root.ptr"),
            )
            self.builder.call(
                self.runtime["pcc_gc_store_root"],
                [root_ptr, result],
            )
            self._emit_current_gc_frame_enter_lifo(
                self._gc_one_slot_frame_map(),
                root_slot,
            )
        self._emit_post_call_err_check(
            span,
            pinned_release_on_error=pinned_arg_temps,
            lifo_owned_root_slots_on_error=(
                (root_slot,) if root_slot is not None else ()
            ),
        )
        if root_slot is not None and root_ptr is not None:
            result = self.builder.call(
                self.runtime["pcc_gc_load_ptr"],
                [ir.Constant(result.type, None), root_ptr],
                name=self._fresh("call.ret.current"),
            )
            self.builder.call(
                self.runtime["pcc_gc_store_root"],
                [root_ptr, ir.Constant(result.type, None)],
            )
            self._emit_gc_frame_leave_lifo_for_slot(root_slot)
        if (
            root_result
            and isinstance(result.type, ir.PointerType)
            and not getattr(self, "_suppress_borrowed_return_retain", False)
        ):
            # The declared user-function ABI returns one object owner. The
            # expression can still have an imprecise Dyn type, and a GC-root
            # reload creates a different SSA value. Preserve the producer's
            # ownership proof so assignments and borrowing consumers release
            # that owner instead of treating this result as a raw pointer.
            # Runtime-library helpers suppress automatic return retains and
            # manage raw/borrowed results explicitly. Their pointer ABI alone
            # does not prove an owner that the caller may consume.
            self._note_owned_object_value(result)
        return result

    def _emit_arg_for_abi_param(
        self,
        ast_arg: Expr,
        target_ty: Type,
        param_ir_ty: ir.Type,
    ) -> ir.Value:
        self._last_call_arg_owned_temp = False
        if isinstance(target_ty, IntType):
            if isinstance(param_ir_ty, ir.PointerType):
                raw = None
                if isinstance(ast_arg.ty, IntType) and ast_arg.ty.name == "int":
                    # The callee requires the object projection, regardless
                    # of the caller's local scalar optimization policy.
                    # Field/subscript reads already own exact Python ints;
                    # scalar lowering followed by reboxing would first narrow
                    # their values through a checked i64 conversion.
                    raw = self._maybe_emit_exact_int_object(ast_arg)
                elif self._int_expr_needs_exact_object_boundary(ast_arg):
                    raw = self._emit_exact_int_operand_object(ast_arg)
                if raw is None:
                    raw = self._emit_expr(ast_arg)
                if raw in getattr(self, "_cpy_values", ()):
                    self._last_call_arg_owned_temp = True
                    return self._emit_value_as_pcc_object_or_bridge(
                        raw,
                        ast_arg.ty,
                        "cpy.arg.to_pcc",
                    )
                if isinstance(raw.type, ir.PointerType):
                    self._last_call_arg_owned_temp = (
                        self._owned_release_needed(raw, ast_arg)
                        or self._pcc_pointer_source_is_owned(ast_arg)
                    )
                    return raw
                self._last_call_arg_owned_temp = True
                return self.builder.call(
                    self.runtime["py_int_from_i64"],
                    [self._to_int64(raw, ast_arg.ty)],
                    name=self._fresh("exact.int.arg.box"),
                )
            if isinstance(param_ir_ty, ir.IntType):
                if param_ir_ty.width == 1:
                    raw = self._emit_expr(ast_arg)
                    return self._truthy(raw, ast_arg.ty)
                value = self._emit_expr_as_i64(ast_arg)
                if param_ir_ty.width < 64:
                    return self.builder.trunc(
                        value,
                        param_ir_ty,
                        name=self._fresh("abi.arg.trunc"),
                    )
                if param_ir_ty.width > 64:
                    return self.builder.sext(
                        value,
                        param_ir_ty,
                        name=self._fresh("abi.arg.extend"),
                    )
                return value
        if isinstance(target_ty, BoolType):
            if isinstance(param_ir_ty, ir.IntType) and param_ir_ty.width == 1:
                raw = self._emit_expr(ast_arg)
                return self._truthy(raw, ast_arg.ty)
        if self._is_object(target_ty) and isinstance(ast_arg, Call):
            payload = self._maybe_emit_valueclass_constructor_payload(
                ast_arg.ty,
                ast_arg,
            )
            if payload is not None:
                boxed_valueclass = self._emit_valueclass_payload_to_object(
                    payload,
                    ast_arg.ty,
                    consume_fields=True,
                )
                if boxed_valueclass is not None:
                    self._last_call_arg_owned_temp = True
                    return boxed_valueclass
                coerced_payload = self._coerce(payload, ast_arg.ty, target_ty, ast_arg)
                if (
                    isinstance(param_ir_ty, ir.PointerType)
                    and isinstance(coerced_payload.type, ir.PointerType)
                    and not isinstance(payload.type, ir.PointerType)
                ):
                    self._last_call_arg_owned_temp = True
                return coerced_payload
        if (
            isinstance(param_ir_ty, ir.PointerType)
            and isinstance(ast_arg, Name)
            and (
                self._name_returns_owned_function_value(ast_arg.ident)
                or self._name_returns_native_builtin_callable_value(ast_arg.ident)
            )
        ):
            v = self._emit_expr_with_native_callable_values(ast_arg)
            return self._coerce(v, ast_arg.ty, target_ty, ast_arg)
        v = self._emit_expr(ast_arg)
        if isinstance(param_ir_ty, ir.PointerType) and self._is_object(target_ty):
            source_ty = ast_arg.ty
            if isinstance(ast_arg, Name):
                env_entry = self.env.get(ast_arg.ident)
                if env_entry is not None and len(env_entry) >= 3:
                    source_ty = env_entry[2]
            boxed_valueclass = self._emit_valueclass_payload_to_object(v, source_ty)
            if boxed_valueclass is not None:
                self._last_call_arg_owned_temp = True
                return boxed_valueclass
        if v in getattr(self, "_cpy_values", ()) and isinstance(
            param_ir_ty,
            ir.PointerType,
        ):
            if isinstance(target_ty, RawPointerType):
                raise NotImplementedError("CPython object cannot implicitly become a raw pointer")
            self._last_call_arg_owned_temp = True
            return self.builder.call(
                self.runtime["py_cpy_to_pcc_obj"],
                [v],
                name=self._fresh("cpy.arg.to_pcc"),
            )
        coerced = self._coerce(v, ast_arg.ty, target_ty, ast_arg)
        if isinstance(target_ty, RawPointerType):
            # A numeric address converted to ptr is not a new Python box.
            self._last_call_arg_owned_temp = False
            return coerced
        if (
            isinstance(param_ir_ty, ir.PointerType)
            and isinstance(coerced.type, ir.PointerType)
        ):
            if not isinstance(v.type, ir.PointerType):
                # Native -> object coercion creates a call-owned temporary.
                # The method/function lowering path pins it across later
                # operands and must release it on both the success and
                # exception edges.
                self._last_call_arg_owned_temp = True
            elif (
                self._owned_release_needed(v, ast_arg)
                or self._pcc_pointer_source_is_owned(ast_arg)
            ):
                # The argument expression itself produced a fresh reference --
                # ``f(T())``, ``f([T()])``.  Nothing else owns it, so the call
                # boundary consumes it.  A borrowed argument such as a plain
                # name is left alone by the same classifier the IntType branch
                # above already relies on.
                self._last_call_arg_owned_temp = True
        return coerced

    def _emit_arg_for_abi_param_with_cleanup(
        self,
        ast_arg: Expr,
        target_ty: Type,
        param_ir_ty: ir.Type,
        pinned_pcc: tuple[tuple[ir.Value, bool], ...],
    ) -> ir.Value:
        """Evaluate one ABI argument while unwinding earlier pointer args."""
        if not pinned_pcc:
            return self._emit_arg_for_abi_param(ast_arg, target_ty, param_ir_ty)
        previous_cpy_cleanup = getattr(
            self,
            "_cpy_operand_cleanup_block",
            None,
        )
        previous_pcc_target = self._current_try_err_block()
        pcc_target = previous_pcc_target
        if pcc_target is None:
            pcc_target = self._ensure_fn_err_exit()
        pcc_cleanup = self._make_cpy_operand_cleanup_block(
            (),
            (),
            pcc_target,
            "abi.arg.pcc.cleanup",
            pinned_pcc,
        )
        cpy_target = previous_cpy_cleanup
        if cpy_target is None:
            cpy_target = pcc_target
        if cpy_target is pcc_target:
            cpy_cleanup = pcc_cleanup
        else:
            cpy_cleanup = self._make_cpy_operand_cleanup_block(
                (),
                (),
                cpy_target,
                "abi.arg.cpy.cleanup",
                pinned_pcc,
            )
        self._try_err_block = pcc_cleanup
        self._cpy_operand_cleanup_block = cpy_cleanup
        try:
            return self._emit_arg_for_abi_param(
                ast_arg,
                target_ty,
                param_ir_ty,
            )
        finally:
            self._try_err_block = previous_pcc_target
            self._cpy_operand_cleanup_block = previous_cpy_cleanup

    def _is_native_set_dyn(self, ty: Type) -> bool:
        return isinstance(ty, SetType)

    _DYN_LIST_METHOD_NATIVE = frozenset(
        {
            "append",
            "extend",
            "insert",
            "pop",
            "remove",
            "index",
            "sort",
            "clear",
        }
    )

    _DYN_DICT_METHOD_NATIVE = frozenset(
        {
            "get",
            "keys",
            "values",
            "items",
            "setdefault",
            "pop",
        }
    )

    _DYN_SET_METHOD_NATIVE = frozenset(
        {
            "add",
            "remove",
            "discard",
            "update",
        }
    )

    def _maybe_emit_builtin_type_method(
        self,
        expr: Call,
    ) -> Optional[ir.Value]:
        """Handle ``int.__new__(cls, x)``-style builtin type dispatch.

        Without this guard, the generic "any class declaring the
        method" fallback can accidentally resolve ``int.__new__`` to a
        user class's own ``__new__`` in the same module.
        """
        attr = expr.func
        assert isinstance(attr, Attr)
        if not isinstance(attr.obj, Name):
            return None
        builtin_name = attr.obj.ident
        if builtin_name not in _CPY_BUILTIN_TYPE_NAMES:
            return None
        if attr.name == "__new__":
            ctor_args = expr.args
            if (
                ctor_args
                and isinstance(ctor_args[0], Name)
                and ctor_args[0].ident == "cls"
                and builtin_name == "object"
                and self.current_class is not None
            ):
                return self.class_lowering.emit_instantiate(
                    self.current_class.name,
                    (),
                    self,
                )
            if (
                ctor_args
                and isinstance(ctor_args[0], Name)
                and ctor_args[0].ident in ("cls", "self")
            ):
                ctor_args = ctor_args[1:]
            return self._emit_call(
                Call(
                    span=getattr(expr, "span", None),
                    ty=expr.ty,
                    func=Name(
                        span=getattr(attr.obj, "span", None),
                        ty=attr.obj.ty,
                        ident=builtin_name,
                    ),
                    args=ctor_args,
                    kwargs=expr.kwargs,
                )
            )
        if (
            builtin_name == "dict"
            and attr.name == "fromkeys"
            and 1 <= len(expr.args) <= 2
            and not expr.kwargs
        ):
            # Native dict.fromkeys(iterable[, value]) — avoid the libpython
            # fallback so it works under --python-libpython=off.
            iter_obj = self._emit_as_object(expr.args[0])
            if len(expr.args) == 2:
                val_obj = self._emit_as_object(expr.args[1])
            else:
                val_obj = self._emit_none_literal()
            result = self.builder.call(
                self.runtime["py_dict_fromkeys"],
                [iter_obj, val_obj],
                name=self._fresh("dict.fromkeys"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if builtin_name == "int" and attr.name == "from_bytes":
            if self._has_starred_unpack(expr.args):
                return None
            for keyword, _operand in expr.kwargs:
                if keyword == "**":
                    return None
            # Bind the Python signature without changing source evaluation
            # order: bytes and byteorder are positional-or-keyword, signed
            # is keyword-only. Keep every evaluated owner pinned until both
            # binding and conversion finish (including a signed.__bool__ call).
            operands = list(expr.args)
            bytes_index = 0 if operands else -1
            order_index = 1 if len(operands) >= 2 else -1
            signed_index = -1
            binding_error = ""
            if len(operands) > 2:
                binding_error = "from_bytes() takes at most 2 positional arguments"
            for keyword, operand in expr.kwargs:
                index = len(operands)
                operands.append(operand)
                if keyword == "bytes":
                    if bytes_index >= 0:
                        binding_error = "from_bytes() got multiple values for argument 'bytes'"
                    bytes_index = index
                elif keyword == "byteorder":
                    if order_index >= 0:
                        binding_error = "from_bytes() got multiple values for argument 'byteorder'"
                    order_index = index
                elif keyword == "signed":
                    if signed_index >= 0:
                        binding_error = "from_bytes() got multiple values for argument 'signed'"
                    signed_index = index
                else:
                    binding_error = "from_bytes() got an unexpected keyword argument '" + keyword + "'"
            if bytes_index < 0 and not binding_error:
                binding_error = "from_bytes() missing required argument 'bytes'"
            if order_index < 0:
                order_index = len(operands)
                operands.append(StrLit(span=expr.span, ty=StrType(name="str"), value="big"))

            roots = []
            previous = self._current_try_err_block()
            previous_cpy = getattr(self, "_cpy_operand_cleanup_block", None)
            target = previous if previous is not None else self._ensure_fn_err_exit()
            leases = []
            try:
                for operand in operands:
                    cleanup = self._extern_cleanup_block(tuple(roots), target)
                    self._try_err_block = cleanup
                    self._cpy_operand_cleanup_block = cleanup
                    value = self._emit_expr_with_cpy_operand_cleanup(
                        operand, (), as_pcc_object=True,
                    )
                    roots.append(self._extern_enter_root(
                        value, self._owned_release_needed(value, operand), "from_bytes.argument.root",
                    ))
                cleanup = self._extern_cleanup_block(tuple(roots), target)
                self._try_err_block = cleanup
                self._cpy_operand_cleanup_block = cleanup
                if binding_error:
                    message = self._pooled_cstr_ptr(binding_error, ".from_bytes.binding_error")
                    exc = self.builder.call(
                        self.runtime["py_exc_new"], [ir.Constant(ir.IntType(64), 3), message],
                        name=self._fresh("from_bytes.binding.exc"),
                    )
                    self.builder.call(self.runtime["py_raise"], [exc])
                    self._gc_release(exc)
                    self._emit_post_call_err_check(expr.span)
                    self._extern_release_roots(tuple(roots))
                    return self._emit_none_literal()
                # A traced slot follows moves while evaluating later operands.
                # Counted leases additionally protect raw ABI arguments across
                # callbacks that clear an aliased object's Boolean pin flag.
                for root in roots:
                    acquired = self.builder.call(
                        self.runtime["pcc_gc_foreign_lease_acquire"],
                        [self._as_gc_ptr(root[0])], name=self._fresh("from_bytes.lease.acquire"),
                    )
                    failed = self.builder.icmp_signed("<", acquired, ir.Constant(ir.IntType(64), 0))
                    error = self.current_function.append_basic_block(self._fresh("from_bytes.lease.error"))
                    ready = self.current_function.append_basic_block(self._fresh("from_bytes.lease.ready"))
                    self.builder.cbranch(failed, error, ready)
                    self.builder.position_at_end(error)
                    cleanup = self._extern_cleanup_block(tuple(roots), target, tuple(leases))
                    self._try_err_block = cleanup
                    self._cpy_operand_cleanup_block = cleanup
                    overflow = self.current_function.append_basic_block(self._fresh("bytes.lease.overflow"))
                    invalid = self.current_function.append_basic_block(self._fresh("bytes.lease.invalid"))
                    self.builder.cbranch(
                        self.builder.icmp_signed("==", acquired, ir.Constant(ir.IntType(64), -2)), overflow, invalid,
                    )
                    self.builder.position_at_end(overflow)
                    self._emit_builtin_exception_and_branch(
                        "OverflowError", "integer byte conversion address lease overflow", expr.span,
                    )
                    self.builder.position_at_end(invalid)
                    self._emit_builtin_exception_and_branch(
                        "RuntimeError", "integer byte conversion requires a stable managed owner", expr.span,
                    )
                    self.builder.position_at_end(ready)
                    leases.append((root, acquired))
                cleanup = self._extern_cleanup_block(tuple(roots), target, tuple(leases))
                self._try_err_block = cleanup
                self._cpy_operand_cleanup_block = cleanup
                signed_value = None
                if signed_index >= 0:
                    signed_expr = operands[signed_index]
                    if isinstance(signed_expr, BoolLit):
                        if signed_expr.value:
                            signed_value = ir.Constant(ir.IntType(64), 1)
                    else:
                        signed_value = self.builder.call(
                            self.runtime["py_obj_truthy"], [self._extern_load_root(roots[signed_index])],
                            name=self._fresh("from_bytes.signed.truth"),
                        )
                        self._emit_post_call_err_check(expr.span)
                call_args = [self._extern_load_root(roots[bytes_index]), self._extern_load_root(roots[order_index])]
                helper = "py_int_from_bytes"
                if signed_value is not None:
                    helper = "py_int_from_bytes_signed"
                    call_args.append(signed_value)
                result = self.builder.call(self.runtime[helper], call_args, name=self._fresh("int.from_bytes"))
                self._emit_post_call_err_check(expr.span)
                result_root = self._extern_enter_root(result, True, "from_bytes.result.root")
                # Root teardown may call finalizers and move the result. The
                # shared take helper consumes the source owner exactly once,
                # then transfers the root owner after restoring prior pin state.
                prior = result_root[2]
                for root in reversed(roots):
                    alias = self.builder.icmp_unsigned("==", self._extern_load_root(result_root), self._extern_load_root(root))
                    prior = self.builder.select(alias, root[2], prior)
                result_root = (result_root[0], True, prior)
            finally:
                self._try_err_block = previous
                self._cpy_operand_cleanup_block = previous_cpy
            release_failed = self._extern_release_foreign_leases(tuple(leases))
            self._extern_check_lease_cleanup(release_failed, tuple(roots) + (result_root,))
            self._extern_release_roots(tuple(roots))
            result = self._extern_take_root(result_root)
            self._note_owned_object_value(result)
            return result
        if (
            builtin_name == "str"
            and attr.name == "maketrans"
            and len(expr.args) == 2
            and not expr.kwargs
        ):
            # Native str.maketrans(x, y) -> {ord(x[i]):ord(y[i])} (2-arg form);
            # avoids the libpython fallback. Raises ValueError on length
            # mismatch, so emit the post-call err check.
            x_obj = self._emit_as_object(expr.args[0])
            y_obj = self._emit_as_object(expr.args[1])
            result = self.builder.call(
                self.runtime["py_str_maketrans"],
                [x_obj, y_obj],
                name=self._fresh("str.maketrans"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if (
            builtin_name == "bytes"
            and attr.name == "maketrans"
            and len(expr.args) == 2
            and not expr.kwargs
        ):
            x_obj = self._emit_as_object(expr.args[0])
            y_obj = self._emit_as_object(expr.args[1])
            result = self.builder.call(
                self.runtime["py_bytes_maketrans"],
                [x_obj, y_obj],
                name=self._fresh("bytes.maketrans"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if (
            builtin_name == "bytes"
            and attr.name == "fromhex"
            and len(expr.args) == 1
            and not expr.kwargs
        ):
            text_obj = self._emit_as_object(expr.args[0])
            result = self.builder.call(
                self.runtime["py_bytes_fromhex"],
                [text_obj],
                name=self._fresh("bytes.fromhex"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if (
            builtin_name == "float"
            and attr.name == "fromhex"
            and len(expr.args) == 1
            and not expr.kwargs
        ):
            # Native float.fromhex(str) — parses a hexadecimal floating-point
            # string exactly like CPython (see py_float_fromhex.c). Avoids the
            # libpython fallback so it works under --python-libpython=off.
            # Raises ValueError / OverflowError, so emit the post-call err
            # check and branch to the error path.
            text_obj = self._emit_as_object(expr.args[0])
            result = self.builder.call(
                self.runtime["py_float_fromhex"],
                [text_obj],
                name=self._fresh("float.fromhex"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if (
            builtin_name == "bytes"
            and attr.name == "translate"
            and len(expr.args) == 2
            and not expr.kwargs
        ):
            src_obj = self._emit_as_object(expr.args[0])
            table_obj = self._emit_as_object(expr.args[1])
            result = self.builder.call(
                self.runtime["py_bytes_translate"],
                [src_obj, table_obj],
                name=self._fresh("bytes.translate"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        fn_val = self._load_cpython_builtin(builtin_name)
        return self._emit_cpy_method_call_src(
            fn_val,
            attr.name,
            expr.args,
            kwargs=expr.kwargs,
        )

    def _try_dispatch_dunder_unary(
        self,
        host_expr: "Expr",
        dunder_name: str,
        arg_exprs: tuple["Expr", ...],
    ) -> Optional[ir.Value]:
        """If ``host_expr`` is a Name bound to a hinted class that
        defines ``dunder_name`` (via MRO), emit the direct method call
        with ``arg_exprs`` and return the result. Otherwise return None.
        """
        if isinstance(host_expr, Subscript):
            host = host_expr.obj
        else:
            host = host_expr
        hint = None
        receiver_expr = host
        if isinstance(host, Name):
            hint = self.env_class_hint.get(host.ident)
            if hint is None and host.ident in ("self", "cls"):
                current_class = getattr(self, "current_class", None)
                if current_class is not None:
                    hint = current_class.name
        if hint is None:
            hint = self._class_hint_for_expr(host_expr)
            receiver_expr = host_expr
        if hint is None:
            return None
        info = self._resolve_method_mro(hint, dunder_name)
        if info is None:
            return None
        # The hint names a compatible class, not the receiver's runtime class.
        # A subclass that overrides the dunder must win -- `value.type ==
        # _CSTR` on an ``ir.Type`` hint statically called ``Type.__eq__``
        # (type and text only) for a ``PointerType`` receiver, so pcc1 took
        # every pointer type as ``i8*``.  Leave such receivers, and hints the
        # class graph cannot vouch for, to the dynamic protocol.
        hint_info = self.class_lowering.classes.get(hint)
        if hint_info is None or self.class_lowering.method_overridden_by_subclass(
            hint_info, dunder_name
        ):
            return None
        obj_val = self._emit_expr(receiver_expr)
        method_fn = info.methods[dunder_name]
        return self._emit_direct_method_call(
            method_fn,
            obj_val,
            info,
            dunder_name,
            arg_exprs,
        )
