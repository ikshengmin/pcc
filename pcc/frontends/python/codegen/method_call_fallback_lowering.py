"""Terminal builtin and dynamic method dispatch after known-class resolution."""

from __future__ import annotations

from pcc.ir.compat import ir
from pcc.frontends.python.py_ast import (
    Attr,
    BoolLit,
    BoolType,
    ByteArrayType,
    BytesType,
    Call,
    ClassType,
    DictType,
    DynType,
    FloatType,
    IntLit,
    IntType,
    ListType,
    Name,
    NoneType,
    StrLit,
    StrType,
    TupleType,
)

_I32 = ir.IntType(32)
_I64 = ir.IntType(64)


class MethodCallFallbackLoweringMixin:
    def _emit_method_call_fallback(self, expr: Call, attr: Attr) -> ir.Value:
        """Finish dispatch without repeating earlier receiver/operand checks."""
        # Last-resort CPython fallback: when the receiver is a DynType
        # value (typical for foreign / imported-module classes whose
        # annotations such as ``llvm.ModuleRef`` resolve to DynType at
        # type-inference time), dispatch the method via
        # ``PyObject_CallMethod``. This unlocks ``module.verify()``
        # and similar idioms without requiring a CPython-class registry
        # on the pcc side.
        #
        # Typed containers (list / dict / tuple / str) deliberately do
        # *not* fall through here: pcc's pure-self-host story requires
        # that typed-collection methods stay on pcc-native runtime paths
        # so the produced binary has no libpython dependency. Missing
        # methods there surface as NotImplementedError so we can add a
        # dedicated fast path rather than silently pulling libpython in.
        obj_ty = attr.obj.ty
        expanded_int_bytes_call = False
        if attr.name == "to_bytes":
            expanded_int_bytes_call = self._has_unpacked_operands(expr.args, expr.kwargs)
        if (
            attr.name == "to_bytes"
            and isinstance(obj_ty, (IntType, DynType, BoolType))
            and not expanded_int_bytes_call
        ):
            # One binder for inferred ints and dynamic integer results. Keep
            # the receiver and all source-order operands owned and pinned while
            # the runtime performs __index__ and signed.__bool__ conversion.
            operands = [attr.obj] + list(expr.args)
            length_index = 1 if expr.args else -1
            order_index = 2 if len(expr.args) >= 2 else -1
            signed_index = -1
            binding_error = ""
            if len(expr.args) > 2:
                binding_error = "to_bytes() takes at most 2 positional arguments"
            for keyword, operand in expr.kwargs:
                index = len(operands)
                operands.append(operand)
                if keyword == "length":
                    if length_index >= 0:
                        binding_error = "to_bytes() got multiple values for argument 'length'"
                    length_index = index
                elif keyword == "byteorder":
                    if order_index >= 0:
                        binding_error = "to_bytes() got multiple values for argument 'byteorder'"
                    order_index = index
                elif keyword == "signed":
                    if signed_index >= 0:
                        binding_error = "to_bytes() got multiple values for argument 'signed'"
                    signed_index = index
                else:
                    binding_error = "to_bytes() got an unexpected keyword argument '" + keyword + "'"
            if length_index < 0:
                length_index = len(operands)
                operands.append(IntLit(span=expr.span, ty=IntType(name="int"), value=1))
            if order_index < 0:
                order_index = len(operands)
                operands.append(StrLit(span=expr.span, ty=StrType(name="str"), value="big"))
            if signed_index < 0:
                signed_index = len(operands)
                operands.append(BoolLit(span=expr.span, ty=BoolType(name="bool"), value=False))
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
                        value, self._owned_release_needed(value, operand), "to_bytes.argument.root",
                    ))
                cleanup = self._extern_cleanup_block(tuple(roots), target)
                self._try_err_block = cleanup
                self._cpy_operand_cleanup_block = cleanup
                if binding_error:
                    message = self._pooled_cstr_ptr(binding_error, ".to_bytes.binding_error")
                    exc = self.builder.call(
                        self.runtime["py_exc_new"], [ir.Constant(ir.IntType(64), 3), message],
                        name=self._fresh("to_bytes.binding.exc"),
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
                        [self._as_gc_ptr(root[0])], name=self._fresh("to_bytes.lease.acquire"),
                    )
                    failed = self.builder.icmp_signed("<", acquired, ir.Constant(ir.IntType(64), 0))
                    error = self.current_function.append_basic_block(self._fresh("to_bytes.lease.error"))
                    ready = self.current_function.append_basic_block(self._fresh("to_bytes.lease.ready"))
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
                result = self.builder.call(
                    self.runtime["py_int_to_bytes_args"],
                    [self._extern_load_root(roots[0]), self._extern_load_root(roots[length_index]),
                     self._extern_load_root(roots[order_index]), self._extern_load_root(roots[signed_index])],
                    name=self._fresh("int.to_bytes"),
                )
                self._emit_post_call_err_check(expr.span)
                result_root = self._extern_enter_root(result, True, "to_bytes.result.root")
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
        if isinstance(obj_ty, DynType):
            if attr.name == "clear" and not expr.args and not expr.kwargs:
                self.builder.call(
                    self.runtime["py_obj_clear"],
                    [self._emit_as_object(attr.obj)],
                    name=self._fresh("obj.clear"),
                )
                # Any receiver other than list/dict/set/bytearray takes the
                # ordinary method call inside py_obj_clear and may raise.
                self._emit_post_call_err_check(expr.span)
                return self._emit_none_literal()
            native = self._maybe_emit_dict_method_via_dyn(expr)
            if native is not None:
                return native
            native = self._maybe_emit_list_method_via_dyn(expr)
            if native is not None:
                return native
            native = self._maybe_emit_set_method_via_dyn(expr)
            if native is not None:
                return native
            native = self._maybe_emit_bytes_method_via_dyn(expr)
            if native is not None:
                return native
            # DynType receiver: when the method is a known pcc-native
            # str helper, dispatch through the runtime (assumes the
            # value really is a str at runtime — matches CPython
            # behaviour which would raise AttributeError on type
            # mismatch; we emit a probable crash). Keeps the binary
            # libpython-free for the common ``DynType str result``
            # idiom (function return + splitlines / rstrip / …).
            native = self._maybe_emit_str_method_via_dyn(expr)
            if native is not None:
                return native
            # DynType numeric methods on a boxed value (e.g. a float from
            # py_obj_truediv, or a boxed int): handle .is_integer()/.bit_length()
            # natively before the getattr-based dynamic dispatch (which would
            # AttributeError, since boxed float/int have no method table).
            # py_float_is_integer / py_int_bit_length read the object by tag.
            # (Same "assume the runtime type matches the method" pragmatism as
            # the str_method_via_dyn path above.)
            if attr.name == "is_integer" and not expr.args and not expr.kwargs:
                obj = self._emit_as_object(attr.obj)
                i64v = self.builder.call(
                    self.runtime["py_float_is_integer"],
                    [obj],
                    name=self._fresh("dyn.is_integer"),
                )
                return self.builder.icmp_signed(
                    "!=",
                    i64v,
                    ir.Constant(_I64, 0),
                    name=self._fresh("dyn.is_integer.i1"),
                )
            if attr.name == "bit_length" and not expr.args and not expr.kwargs:
                obj = self._emit_as_object(attr.obj)
                return self.builder.call(
                    self.runtime["py_int_bit_length"],
                    [obj],
                    name=self._fresh("dyn.bit_length"),
                )
            if attr.name == "bit_count" and not expr.args and not expr.kwargs:
                obj = self._emit_as_object(attr.obj)
                return self.builder.call(
                    self.runtime["py_int_bit_count"],
                    [obj],
                    name=self._fresh("dyn.bit_count"),
                )
            if self._slot_call_result_sink(expr) is not None:
                return self._emit_slot_method_call(expr, "method." + attr.name)
            recv_obj = self._emit_as_object(attr.obj)
            release_receiver = self._method_receiver_is_owned(attr.obj, recv_obj)
            if not expr.kwargs and self._split_starstar_kwargs_unpack(expr.args) is None:
                return self._emit_loaded_method_call(
                    recv_obj,
                    self._attr_name_ptr(attr.name),
                    attr.name,
                    expr.args,
                    expr.span,
                    attribute_error_span=attr.span,
                    dynamic_result=True,
                    release_receiver=release_receiver,
                )
            method_obj = self.builder.call(
                self.runtime["py_obj_getattr"],
                [recv_obj, self._attr_name_ptr(attr.name)],
                name=self._fresh(f"dyn.attr.{attr.name}"),
            )
            # The bound method holds its own reference to the receiver; a
            # receiver this expression created is consumed here.
            if release_receiver:
                self._gc_release(recv_obj)
            self._emit_attribute_error_if_null(method_obj, attr.name, attr.span)
            kwdict_unpack = self._split_starstar_kwargs_unpack(expr.args)
            arg_exprs = expr.args
            kwargs_expr = None
            if kwdict_unpack is not None:
                arg_exprs, kwargs_expr = kwdict_unpack
            args_tuple = self._emit_dynamic_call_args_tuple(arg_exprs)
            kwargs_obj = self._emit_dynamic_call_kwargs_object(
                expr.kwargs,
                kwargs_expr,
                expr.span,
            )
            result = self.builder.call(
                self.runtime["py_obj_call"],
                [method_obj, args_tuple, kwargs_obj],
                name=self._fresh(f"dyn.method.{attr.name}"),
            )
            self._gc_release(args_tuple)
            if expr.kwargs or kwargs_expr is not None:
                self._gc_release(kwargs_obj)
            # `py_obj_getattr` returns a NEW reference on every path: fields and
            # `__dict__`/`__class__` incref, the dynamic-attr path goes through
            # `py_dict_get` (which increfs), a descriptor `__get__` yields an
            # owned result, and a plain method builds a fresh bound object via
            # `py_instance_bind_method`.  `py_obj_call` only borrows the
            # callable, so this frame owns `method_obj` and must consume it.
            # Without this release EVERY dynamic method call leaked its bound
            # method object: two million `obj.method()` calls whose method
            # returns None (so the result cannot be the leak) grew RSS to
            # 2.8 GB.  Release before the error check so the unwind path does
            # not leak it either.
            self._gc_release(method_obj)
            self._emit_post_call_err_check(expr.span)
            self._note_owned_dynamic_call_value(result)
            return result
        if (
            isinstance(obj_ty, ByteArrayType)
            and attr.name == "clear"
            and not expr.args
            and not expr.kwargs
        ):
            recv = self._emit_expr(attr.obj)
            self.builder.call(self.runtime["py_bytearray_clear"], [recv])
            self._gc_release_if_owned(recv, attr.obj)
            self._emit_post_call_err_check(expr.span)
            return self._emit_none_literal()
        if (
            isinstance(obj_ty, ByteArrayType)
            and attr.name == "pop"
            and len(expr.args) <= 1
            and not expr.kwargs
        ):
            # bytearray.pop([index]) removes and returns the byte at index
            # (default last) as an int, shrinking the receiver in place. No-arg
            # form passes py_None; the runtime treats a None/non-int index as
            # "last element". Raises IndexError on empty / out-of-range.
            recv = self._emit_expr(attr.obj)
            if expr.args:
                index_obj = self._emit_as_object(expr.args[0])
            else:
                index_obj = self._emit_slice_bound_object(None)
            result = self.builder.call(
                self.runtime["py_bytearray_pop"],
                [recv, index_obj],
                name=self._fresh("bytearray.pop"),
            )
            self._emit_post_call_err_check(expr.span)
            if expr.args:
                self._gc_release_if_owned(index_obj, expr.args[0])
            return result
        if (
            isinstance(obj_ty, (BytesType, ByteArrayType))
            and attr.name == "rfind"
            and len(expr.args) == 1
            and not expr.kwargs
        ):
            recv = self._emit_expr(attr.obj)
            needle = self._emit_as_object(expr.args[0])
            fn = "py_bytes_find" if attr.name == "find" else "py_bytes_rfind"
            return self.builder.call(
                self.runtime[fn],
                [recv, needle],
                name=self._fresh(f"bytes.{attr.name}"),
            )
        if (
            isinstance(obj_ty, (BytesType, ByteArrayType))
            and attr.name == "find"
            and 1 <= len(expr.args) <= 3
            and not expr.kwargs
        ):
            recv = self._emit_expr(attr.obj)
            arguments = [self._emit_as_object(arg) for arg in expr.args]
            live = [(recv, attr.obj)] + list(zip(arguments, expr.args))
            owned = tuple(value for value, source in live if self._owned_release_needed(value, source))
            begin = ir.Constant(_I64, 0)
            end = ir.Constant(_I64, 9223372036854775807)
            # Evaluate every argument before invoking __index__, matching
            # ordinary call evaluation even when a bound has side effects.
            if len(arguments) >= 2:
                begin = self.builder.call(self.runtime["py_slice_index_i64"],
                                          [arguments[1], begin], name=self._fresh("bytes.find.start"))
                self._emit_post_call_err_check(expr.span, release_on_error=owned)
            if len(arguments) == 3:
                end = self.builder.call(self.runtime["py_slice_index_i64"],
                                        [arguments[2], end], name=self._fresh("bytes.find.end"))
                self._emit_post_call_err_check(expr.span, release_on_error=owned)
            result = self.builder.call(
                self.runtime["py_bytes_find_range"],
                [recv, arguments[0], begin, end],
                name=self._fresh("bytes.find.range"),
            )
            for value, source in live:
                self._gc_release_if_owned(value, source)
            self._emit_post_call_err_check(expr.span)
            return result
        if (
            isinstance(obj_ty, (BytesType, ByteArrayType))
            and attr.name == "count"
            and len(expr.args) == 1
            and not expr.kwargs
        ):
            # bytes/bytearray .count(sub): number of non-overlapping matches of
            # a sub-bytes or single byte value. py_bytes_count reads the
            # receiver + needle via bytes_data()/byte_from_obj() (same as
            # find/rfind) and returns an i64 count without raising, so no
            # py_err_occurred() check is needed. The raw i64 is boxed as an int
            # object by the DynType-width-64 marshal path (expr.ty is DynType),
            # mirroring how bytes .find/.rfind results already flow.
            recv = self._emit_expr(attr.obj)
            needle = self._emit_as_object(expr.args[0])
            return self.builder.call(
                self.runtime["py_bytes_count"],
                [recv, needle],
                name=self._fresh("bytes.count"),
            )
        if (
            isinstance(obj_ty, (BytesType, ByteArrayType))
            and attr.name in ("startswith", "endswith")
            and len(expr.args) == 1
            and not expr.kwargs
        ):
            # bytes/bytearray .startswith/.endswith(prefix-or-tuple-of-prefixes).
            # py_str_startswith / py_str_endswith read the receiver and each
            # tuple element via stringlike_bytes() (handles bytes/bytearray) and
            # already implement the tuple-of-prefixes case; they return i64 0/1.
            # Box as a bool object (result type is unmodelled -> object).
            recv = self._emit_expr(attr.obj)
            needle = self._emit_as_object(expr.args[0])
            fn = {
                "startswith": "py_str_startswith",
                "endswith": "py_str_endswith",
            }[attr.name]
            i64v = self.builder.call(
                self.runtime[fn],
                [recv, needle],
                name=self._fresh(f"bytes.{attr.name}"),
            )
            bit = self.builder.icmp_signed(
                "!=",
                i64v,
                ir.Constant(_I64, 0),
                name=self._fresh(f"bytes.{attr.name}.bit"),
            )
            bit32 = self.builder.zext(
                bit,
                _I32,
                name=self._fresh(f"bytes.{attr.name}.i32"),
            )
            return self.builder.call(
                self.runtime["py_bool_from_bit"],
                [bit32],
                name=self._fresh(f"bytes.{attr.name}.bool"),
            )
        if isinstance(obj_ty, (BytesType, ByteArrayType)) and attr.name == "decode":
            if not self._expr_looks_cpython(attr.obj):
                owned = self._emit_owned_bytes_decode(expr, False)
                if owned is not None:
                    return owned
            # decode() defaults to utf-8, and pcc str is utf-8 internally, so an
            # explicit "utf-8" encoding (+ optional supported errors mode) stays
            # in the native runtime. Other encodings / error modes fall back.
            encoding_arg = None
            errors_arg = None
            ok = True
            if len(expr.args) >= 1:
                encoding_arg = expr.args[0]
            if len(expr.args) >= 2:
                errors_arg = expr.args[1]
            if len(expr.args) > 2:
                ok = False
            for kname, kval in expr.kwargs or ():
                if kname == "encoding" and encoding_arg is None:
                    encoding_arg = kval
                elif kname == "errors" and errors_arg is None:
                    errors_arg = kval
                else:
                    ok = False
            if not ok:
                raise NotImplementedError(
                    "bytes.decode() accepts at most encoding and errors"
                )
            recv = self._emit_expr(attr.obj)
            operands = []
            if expr.args:
                operands.append(("encoding", expr.args[0]))
            if len(expr.args) == 2:
                operands.append(("errors", expr.args[1]))
            operands.extend(expr.kwargs)
            return self._emit_bytes_decode_call(recv, attr.obj, operands, expr.span)
        if (
            isinstance(obj_ty, (BytesType, ByteArrayType))
            and attr.name == "join"
            and len(expr.args) == 1
            and not expr.kwargs
        ):
            # b"sep".join(list_or_tuple_of_bytes) -> one O(n) allocation.
            # Publish into the consumer's root before retiring the input owners.
            if not self._expr_looks_cpython(attr.obj):
                return self._emit_owned_native_join_call(expr)
            recv = self._emit_expr(attr.obj)
            return self._emit_native_bytes_join(recv, expr.args[0], "bytes")
        if (
            isinstance(obj_ty, (BytesType, ByteArrayType))
            and attr.name == "hex"
            and not expr.args
            and not expr.kwargs
        ):
            # bytes.hex() -> lowercase two-hex-digits-per-byte string, the
            # receiver an owned operand and the NEW str published before
            # cleanup ("B:" + data.hex()).
            return self._emit_owned_unary_runtime_call(expr, "py_bytes_hex")
        if (
            isinstance(obj_ty, (BytesType, ByteArrayType))
            and attr.name in ("upper", "lower")
            and not expr.args
            and not expr.kwargs
        ):
            recv = self._emit_expr(attr.obj)
            fn = "py_bytes_upper" if attr.name == "upper" else "py_bytes_lower"
            return self.builder.call(
                self.runtime[fn],
                [recv],
                name=self._fresh(f"bytes.{attr.name}"),
            )
        if (
            isinstance(obj_ty, (BytesType, ByteArrayType))
            and attr.name in ("ljust", "rjust")
            and len(expr.args) in (1, 2)
            and not expr.kwargs
        ):
            padding = self._emit_owned_padding_method(expr)
            if padding is not None:
                return padding
        if (
            isinstance(obj_ty, (BytesType, ByteArrayType))
            and not expr.kwargs
            and (
                (attr.name in ("strip", "lstrip", "rstrip") and len(expr.args) <= 1)
                or (attr.name in ("ljust", "rjust") and len(expr.args) in (1, 2))
            )
        ):
            # These byte-family ABIs return NEW objects, including unchanged
            # values. Keep operands owned across later evaluation and width
            # callbacks; publish the result before releasing those owners.
            sink = self._slot_call_result_sink(expr)
            output = sink
            roots = []
            if output is None:
                output = self._new_slot_call_root("bytes.method.result")
                roots.append(output)
            previous = self._current_try_err_block()
            target = previous if previous is not None else self._ensure_fn_err_exit()
            saved_cpy = self._cpy_operand_cleanup_block
            operands = []
            try:
                self._try_err_block = self._slot_call_cleanup_block(tuple(roots), target)
                self._cpy_operand_cleanup_block = self._try_err_block
                for argument in (attr.obj,) + tuple(expr.args):
                    operand = self._emit_slot_call_operand(argument, "bytes.method.argument")
                    operands.append(operand)
                    roots.append(operand)
                    self._try_err_block = self._slot_call_cleanup_block(tuple(roots), target)
                    self._cpy_operand_cleanup_block = self._try_err_block
                runtime_name = "py_bytes_" + attr.name
                arguments = tuple(operands)
                suffix = ()
                order = ()
                if attr.name in ("ljust", "rjust"):
                    width = self.builder.call(
                        self.runtime["py_index_i64_checked_slots"],
                        [self._as_gc_ptr(operands[1])],
                        name=self._fresh("bytes.method.width"),
                    )
                    self._emit_post_call_err_check(expr.span)
                    arguments = (operands[0],)
                    suffix = (width, ir.Constant(ir.PointerType(ir.IntType(8)), None))
                    if len(expr.args) == 2:
                        arguments = (operands[0], operands[2])
                        suffix = (width,)
                        order = (0, 2, 1)
                elif expr.args:
                    runtime_name += "_chars"
                self._slot_call_runtime_call(
                    runtime_name, arguments, result_slot=output,
                    suffix_args=suffix, argument_order=order, span=expr.span,
                )
                self._release_slot_call_roots(tuple(operands))
                if sink is None:
                    return self._take_slot_call_root(output)
                return self.builder.load(output, name=self._fresh("bytes.method.current"))
            finally:
                self._try_err_block = previous
                self._cpy_operand_cleanup_block = saved_cpy
        if (
            isinstance(obj_ty, (BytesType, ByteArrayType))
            and attr.name == "split"
            and len(expr.args) in (1, 2)
            and not expr.kwargs
        ):
            # Both split ABIs return a NEW list. Preserve the receiver and each
            # argument through later evaluation, then publish the list before
            # lease release, pending-error checks, or operand disposal.
            sink = self._slot_call_result_sink(expr)
            output = sink
            roots = []
            if output is None:
                output = self._new_slot_call_root("bytes.split.result")
                roots.append(output)
            previous = self._current_try_err_block()
            target = previous if previous is not None else self._ensure_fn_err_exit()
            saved_cleanup = self._cpy_operand_cleanup_block
            operands = []
            try:
                self._try_err_block = self._slot_call_cleanup_block(tuple(roots), target)
                self._cpy_operand_cleanup_block = self._try_err_block
                receiver = self._emit_slot_call_operand(attr.obj, "bytes.split.receiver")
                roots.append(receiver)
                operands.append(receiver)
                for index, argument in enumerate(expr.args):
                    self._try_err_block = self._slot_call_cleanup_block(tuple(roots), target)
                    self._cpy_operand_cleanup_block = self._try_err_block
                    label = "bytes.split.separator" if index == 0 else "bytes.split.limit"
                    operand = self._emit_slot_call_operand(argument, label)
                    roots.append(operand)
                    operands.append(operand)
                self._try_err_block = self._slot_call_cleanup_block(tuple(roots), target)
                self._cpy_operand_cleanup_block = self._try_err_block
                runtime_name = "py_bytes_split" if len(expr.args) == 1 else "py_bytes_split_max"
                self._slot_call_runtime_call(
                    runtime_name, tuple(operands), result_slot=output, span=expr.span,
                )
                self._guard_cpy_value_not_null(self.builder.load(output))
                self._release_slot_call_roots(tuple(operands))
            finally:
                self._try_err_block = previous
                self._cpy_operand_cleanup_block = saved_cleanup
            if sink is not None:
                return self.builder.load(output, name=self._fresh("bytes.split.output"))
            return self._take_slot_call_root(output)
        if (
            isinstance(obj_ty, (BytesType, ByteArrayType))
            and attr.name == "partition"
            and len(expr.args) == 1
            and not expr.kwargs
        ):
            # bytes/bytearray .partition(sep) -> (before, sep, after) tuple.
            recv = self._emit_expr(attr.obj)
            sep = self._emit_as_object(expr.args[0])
            return self.builder.call(
                self.runtime["py_bytes_partition"],
                [recv, sep],
                name=self._fresh("bytes.partition"),
            )
        if (
            isinstance(obj_ty, (BytesType, ByteArrayType))
            and attr.name == "translate"
            and len(expr.args) == 1
            and not expr.kwargs
        ):
            recv = self._emit_expr(attr.obj)
            table_obj = self._emit_as_object(expr.args[0])
            result = self.builder.call(
                self.runtime["py_bytes_translate"],
                [recv, table_obj],
                name=self._fresh("bytes.translate"),
            )
            self._emit_post_call_err_check(expr.span)
            return result
        if (
            isinstance(obj_ty, (BytesType, ByteArrayType))
            and attr.name == "replace"
            and len(expr.args) == 2
            and not expr.kwargs
        ):
            recv = self._emit_expr(attr.obj)
            old_obj = self._emit_as_object(expr.args[0])
            new_obj = self._emit_as_object(expr.args[1])
            result = self.builder.call(
                self.runtime["py_bytes_replace"],
                [recv, old_obj, new_obj],
                name=self._fresh("bytes.replace"),
            )
            self._emit_post_call_err_check(expr.span)
            return result
        if isinstance(obj_ty, StrType):
            # Typed-str receiver with a method not on the pcc-native
            # fast path (``encode`` / ``rsplit`` / ``format`` / …).
            # Pcc's pure-self-host policy prefers a native helper here,
            # but to let the solo-compile survey advance we route the
            # unknown method through the libpython fallback and leave
            # a TODO. The emitted binary then links libpython for this
            # specific use. Revisit and add the helper once the call
            # shows up in the self-host critical path.
            native = self._maybe_emit_str_method_via_dyn(expr)
            if native is not None:
                return native
            raw_val = self._emit_expr(attr.obj)
            cpy_val, owned = self._marshal_to_cpython_consuming_source(
                raw_val,
                obj_ty,
                attr.obj,
            )
            result = self._emit_cpy_method_call_src(
                cpy_val,
                attr.name,
                expr.args,
                kwargs=expr.kwargs,
                operand_order=expr.operand_order,
                receiver_owned=owned,
            )
            return result
        if isinstance(obj_ty, NoneType):
            # Flow-insensitive inference can label a mutable module global by
            # its initial ``None`` even after a guarded branch has installed a
            # native instance.  The slot, not that stale expression type, owns
            # the runtime value.  In a closed no-libpython build, dispatch it
            # through the native object model; an actual None still raises
            # AttributeError there.
            if (
                getattr(self, "_strict_no_libpython", False)
                and isinstance(attr.obj, Name)
                and attr.obj.ident in getattr(self, "_module_globals", {})
                and not getattr(self, "_cpy_env_flags", {}).get(
                    attr.obj.ident, False
                )
                and isinstance(
                    self._module_globals[attr.obj.ident][0].value_type,
                    ir.PointerType,
                )
            ):
                return self._emit_callable_attribute_call(
                    attr.obj,
                    attr.name,
                    expr.args,
                    expr.kwargs,
                    expr.span,
                    call_expr=expr,
                )
            # Flow-insensitive inference can leave a guarded Optional[str]
            # receiver as ``NoneType`` inside branches like
            # ``x.strip() if x is not None else None``. Marshal the
            # runtime value to a CPython object and dispatch there so a
            # real str still works while an actual None preserves
            # CPython's AttributeError behavior.
            raw_val = self._emit_expr(attr.obj)
            cpy_val, owned = self._marshal_to_cpython_consuming_source(
                raw_val,
                obj_ty,
                attr.obj,
            )
            result = self._emit_cpy_method_call_src(
                cpy_val,
                attr.name,
                expr.args,
                kwargs=expr.kwargs,
                operand_order=expr.operand_order,
                receiver_owned=owned,
            )
            return result
        if isinstance(obj_ty, (ListType, DictType, TupleType)):
            # Typed-collection receiver whose method isn't on the pcc-
            # native fast path (e.g. ``.reverse`` / ``.clear`` /
            # ``.update``). Fall through to the CPython fallback the
            # same way StrType does: pulls libpython for that specific
            # use but lets the solo-compile survey advance. Revisit
            # with a native helper when the call reaches the
            # self-host critical path.
            raw_val = self._emit_expr(attr.obj)
            cpy_val, owned = self._marshal_to_cpython_consuming_source(
                raw_val,
                obj_ty,
                attr.obj,
            )
            result = self._emit_cpy_method_call_src(
                cpy_val,
                attr.name,
                expr.args,
                kwargs=expr.kwargs,
                operand_order=expr.operand_order,
                receiver_owned=owned,
            )
            return result
        if (
            isinstance(obj_ty, (FloatType, DynType))
            and attr.name == "is_integer"
            and not expr.args
            and not expr.kwargs
        ):
            # Native float.is_integer() — avoid the libpython fallback below so
            # it works under --python-libpython=off. Returns a bool (i1).
            # DynType is included so a boxed float from a dynamic expression
            # (e.g. ``(total / count).is_integer()`` where ``/`` is py_obj_truediv
            # -> a boxed float) works; py_float_is_integer reads the object via
            # py_float_to_f64 (float/int/bool). (A user class defining its own
            # is_integer would route here too, but that is vanishingly rare.)
            obj = self._emit_as_object(attr.obj)
            i64v = self.builder.call(
                self.runtime["py_float_is_integer"],
                [obj],
                name=self._fresh("float.is_integer"),
            )
            return self.builder.icmp_signed(
                "!=",
                i64v,
                ir.Constant(_I64, 0),
                name=self._fresh("float.is_integer.i1"),
            )
        if (
            isinstance(obj_ty, (IntType, DynType))
            and attr.name == "bit_length"
            and not expr.args
            and not expr.kwargs
        ):
            # Native int.bit_length() — avoid the libpython fallback so it works
            # under --python-libpython=off. Returns the bit count as an int (i64),
            # exact for bignums (py_int_bit_length). DynType included so a boxed
            # int from a dynamic expression works (py_int_bit_length reads the
            # object's tag: tagged int / bignum).
            obj = self._emit_as_object(attr.obj)
            return self.builder.call(
                self.runtime["py_int_bit_length"],
                [obj],
                name=self._fresh("int.bit_length"),
            )
        if (
            isinstance(obj_ty, (IntType, DynType))
            and attr.name == "bit_count"
            and not expr.args
            and not expr.kwargs
        ):
            # Native int.bit_count() — sibling of bit_length; avoid the libpython
            # fallback so it works under --python-libpython=off. Returns the
            # population count (set bits of abs(value)) as an int (i64), exact
            # for bignums (py_int_bit_count popcounts each limb). DynType included
            # so a boxed int from a dynamic expression works (py_int_bit_count
            # reads the object's tag: tagged int / bignum).
            obj = self._emit_as_object(attr.obj)
            return self.builder.call(
                self.runtime["py_int_bit_count"],
                [obj],
                name=self._fresh("int.bit_count"),
            )
        if isinstance(obj_ty, (IntType, FloatType, BoolType)):
            # Numeric method call (``int.to_bytes``, ``float.is_integer``,
            # ``bool.conjugate``, etc.) — box to a CPython object and
            # dispatch through the libpython fallback. Pulls libpython
            # for that specific use. The numeric value is marshalled
            # through the appropriate boxer so CPython sees a proper
            # Py_Long / Py_Float.
            raw_val = self._emit_expr(attr.obj)
            cpy_val, owned = self._marshal_to_cpython_consuming_source(
                raw_val,
                obj_ty,
                attr.obj,
            )
            return self._emit_cpy_method_call_src(
                cpy_val,
                attr.name,
                expr.args,
                kwargs=expr.kwargs,
                operand_order=expr.operand_order,
                receiver_owned=owned,
            )
        if isinstance(obj_ty, ClassType):
            receiver_hint = self._class_hint_for_expr(attr.obj)
            if receiver_hint is not None:
                class_info = self.class_lowering.classes.get(receiver_hint)
                if class_info is not None:
                    # A mixin can call a helper supplied only by the runtime
                    # subclass. Its native receiver remains native even when
                    # the lexical class has no declaration for that helper.
                    return self._emit_callable_attribute_call(
                        attr.obj,
                        attr.name,
                        expr.args,
                        expr.kwargs,
                        expr.span,
                        call_expr=expr,
                    )
            # An unresolved imported annotation shell still uses the explicit
            # compatibility path; it supplies no native class/domain evidence.
            if (self._slot_call_result_sink(expr) is not None
                    and not self._expr_looks_cpython(attr.obj)):
                return self._emit_slot_method_call(expr, "method." + attr.name)
            raw_val = self._emit_expr(attr.obj)
            if raw_val not in getattr(self, "_cpy_values", ()) and (
                getattr(self, "_strict_no_libpython", False)
                or obj_ty.name == "type"
            ):
                # No CPython value can reach a strict build, and ``type(x)``
                # of a native value is a native class (``type(cm).__aenter__
                # (cm)`` in async-with).  The compatibility path would stub
                # the whole function under --python-libpython=off.
                return self._emit_callable_attribute_call_on_value(
                    raw_val,
                    attr.name,
                    expr.args,
                    expr.kwargs,
                    expr.span,
                    release_receiver=self._method_receiver_is_owned(attr.obj, raw_val),
                )
            cpy_val, owned = self._marshal_to_cpython_consuming_source(
                raw_val,
                obj_ty,
                attr.obj,
            )
            result = self._emit_cpy_method_call_src(
                cpy_val,
                attr.name,
                expr.args,
                kwargs=expr.kwargs,
                operand_order=expr.operand_order,
                receiver_owned=owned,
            )
            return result

        return self._emit_callable_attribute_call(
            attr.obj,
            attr.name,
            expr.args,
            expr.kwargs,
            expr.span,
            call_expr=expr,
        )
