"""Terminal builtin and dynamic method dispatch after known-class resolution."""

from __future__ import annotations

from pcc.ir.compat import ir
from pcc.frontends.python.codegen.freestanding_abi_constants import (
    PY_TYPE_BOOL,
    PY_TYPE_FLOAT,
    PY_TYPE_INT,
)
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


def _bytes_split_operands(expr: Call):
    """``(role, argument)`` of a bytes ``split``/``rsplit`` call in source
    evaluation order (roles ``sep`` and ``maxsplit``), or None for a shape
    this lowering does not take (more than two arguments, starred or
    unknown keywords, or one argument given twice)."""
    positional = ("sep", "maxsplit")
    if len(expr.args) > len(positional):
        return None
    named = {}
    for name, value in expr.kwargs:
        if name not in positional or name in named:
            return None
        named[name] = value
    roles = list(positional[:len(expr.args)])
    if any(role in named for role in roles):
        return None
    for argument in expr.args:
        # ``*xs`` / ``**m`` arrive as marker calls among the arguments.
        if (isinstance(argument, Call) and isinstance(argument.func, Name)
                and argument.func.ident in ("*", "**", "__starred__")):
            return None
    order = list(expr.operand_order) or (
        [("arg", index) for index in range(len(expr.args))]
        + [("kw", index) for index in range(len(expr.kwargs))]
    )
    result = []
    for kind, index in order:
        if kind == "arg":
            result.append((roles[index], expr.args[index]))
        else:
            name, value = expr.kwargs[index]
            result.append((name, value))
    return result


class MethodCallFallbackLoweringMixin:
    def _emit_guarded_dynamic_numeric_method(self, expr, attr):
        """Select numeric helpers by the receiver tag, never by name alone.

        The shared object-call path owns custom lookup and argument binding.
        Both branches borrow the same rooted receiver evaluated exactly once.
        """
        previous = self._current_try_err_block()
        saved_cpy = self._cpy_operand_cleanup_block
        target = previous if previous is not None else self._ensure_fn_err_exit()
        sink = self._slot_call_result_sink(expr)
        output = sink
        roots = []
        if output is None:
            output = self._new_slot_call_root("numeric.method.result")
            roots.append(output)
        try:
            self._try_err_block = self._slot_call_cleanup_block(tuple(roots), target)
            self._cpy_operand_cleanup_block = self._try_err_block
            receiver = self._emit_slot_call_operand(attr.obj, "numeric.method.receiver")
            roots.append(receiver)
            self._try_err_block = self._slot_call_cleanup_block(tuple(roots), target)
            self._cpy_operand_cleanup_block = self._try_err_block
            tag = self._slot_call_runtime_call("py_obj_type_tag", (receiver,), span=expr.span)
            tags = (PY_TYPE_INT, PY_TYPE_BOOL)
            if attr.name == "is_integer":
                tags = (PY_TYPE_INT, PY_TYPE_BOOL, PY_TYPE_FLOAT)
            matches = self.builder.icmp_signed("==", tag, ir.Constant(_I64, tags[0]))
            for type_tag in tags[1:]:
                matches = self.builder.or_(matches, self.builder.icmp_signed(
                    "==", tag, ir.Constant(_I64, type_tag)))
            function = self.current_function
            native = function.append_basic_block(self._fresh("numeric.method.native"))
            generic = function.append_basic_block(self._fresh("numeric.method.generic"))
            done = function.append_basic_block(self._fresh("numeric.method.done"))
            self.builder.cbranch(matches, native, generic)

            self.builder.position_at_end(generic)
            self._emit_slot_call_object(
                expr, "numeric.method.custom", receiver_slot=receiver, result_slot=output,
            )
            self.builder.branch(done)

            self.builder.position_at_end(native)
            if attr.name == "to_bytes":
                result = self._emit_int_to_bytes_method(expr, attr, receiver_slot=receiver)
                self._publish_slot_call_owned(output, result, label="numeric.to_bytes")
            elif attr.name == "is_integer":
                # All integers, including arbitrary-precision integers, are
                # integral. Converting them to double could overflow or round.
                floating = function.append_basic_block(self._fresh("numeric.float"))
                integral = function.append_basic_block(self._fresh("numeric.integral"))
                joined = function.append_basic_block(self._fresh("numeric.truth"))
                self.builder.cbranch(self.builder.icmp_signed(
                    "==", tag, ir.Constant(_I64, PY_TYPE_FLOAT)), floating, integral)
                self.builder.position_at_end(floating)
                value = self._slot_call_runtime_call("py_float_is_integer", (receiver,), span=expr.span)
                bit = self.builder.trunc(value, _I32)
                float_exit = self.builder.block
                self.builder.branch(joined)
                self.builder.position_at_end(integral)
                self.builder.branch(joined)
                self.builder.position_at_end(joined)
                truth = self.builder.phi(_I32, name=self._fresh("numeric.is_integer"))
                truth.add_incoming(bit, float_exit)
                truth.add_incoming(ir.Constant(_I32, 1), integral)
                self._slot_call_runtime_call(
                    "py_bool_from_bit", (), suffix_args=(truth,), result_slot=output, span=expr.span,
                )
            else:
                boolean = function.append_basic_block(self._fresh("numeric.boolean"))
                integer = function.append_basic_block(self._fresh("numeric.integer"))
                joined = function.append_basic_block(self._fresh("numeric.count"))
                self.builder.cbranch(self.builder.icmp_signed(
                    "==", tag, ir.Constant(_I64, PY_TYPE_BOOL)), boolean, integer)
                self.builder.position_at_end(boolean)
                bool_value = self._slot_call_runtime_call("py_obj_truthy", (receiver,), span=expr.span)
                bool_exit = self.builder.block
                self.builder.branch(joined)
                self.builder.position_at_end(integer)
                helper = "py_int_bit_length" if attr.name == "bit_length" else "py_int_bit_count"
                int_value = self._slot_call_runtime_call(helper, (receiver,), span=expr.span)
                int_exit = self.builder.block
                self.builder.branch(joined)
                self.builder.position_at_end(joined)
                value = self.builder.phi(_I64, name=self._fresh("numeric.bit_count"))
                value.add_incoming(bool_value, bool_exit)
                value.add_incoming(int_value, int_exit)
                self._slot_call_runtime_call(
                    "py_int_from_i64", (), suffix_args=(value,), result_slot=output, span=expr.span,
                )
            self.builder.branch(done)
            self.builder.position_at_end(done)
            self._release_slot_call_roots((receiver,))
        finally:
            self._try_err_block = previous
            self._cpy_operand_cleanup_block = saved_cpy
        if sink is not None:
            return self.builder.load(output, name=self._fresh("numeric.method.current"))
        return self._take_slot_call_root(output, source_expr=expr)

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
        if isinstance(obj_ty, DynType) and (
            attr.name == "to_bytes" and not expanded_int_bytes_call
            or attr.name in ("is_integer", "bit_length", "bit_count")
            and not expr.args and not expr.kwargs
        ):
            return self._emit_guarded_dynamic_numeric_method(expr, attr)
        if (
            attr.name == "to_bytes"
            and isinstance(obj_ty, (IntType, BoolType))
            and not expanded_int_bytes_call
        ):
            return self._emit_int_to_bytes_method(expr, attr)
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
            # General dynamic calls need the same operand/result transaction
            # whether a containing expression supplied a sink or not.
            return self._emit_slot_method_call(expr, "method." + attr.name)
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
        split_operands = (
            _bytes_split_operands(expr)
            if isinstance(obj_ty, (BytesType, ByteArrayType))
            and attr.name in ("split", "rsplit") else None
        )
        if split_operands is not None:
            # split/rsplit(sep=None, maxsplit=-1) return a NEW list. Preserve
            # the receiver and each argument (in source order) through later
            # evaluation, then publish the list before lease release,
            # pending-error checks, or operand disposal. An omitted argument
            # is an empty root: the runtime reads NULL as its default.
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
            roles = []
            try:
                self._try_err_block = self._slot_call_cleanup_block(tuple(roots), target)
                self._cpy_operand_cleanup_block = self._try_err_block
                receiver = self._emit_slot_call_operand(attr.obj, "bytes.split.receiver")
                roots.append(receiver)
                operands.append(receiver)
                roles.append("receiver")
                for role, argument in split_operands:
                    self._try_err_block = self._slot_call_cleanup_block(tuple(roots), target)
                    self._cpy_operand_cleanup_block = self._try_err_block
                    operand = self._emit_slot_call_operand(argument, "bytes.split." + role)
                    roots.append(operand)
                    operands.append(operand)
                    roles.append(role)
                for role in ("sep", "maxsplit"):
                    if role not in roles:
                        absent = self._new_slot_call_root("bytes.split." + role + ".default")
                        roots.append(absent)
                        operands.append(absent)
                        roles.append(role)
                self._try_err_block = self._slot_call_cleanup_block(tuple(roots), target)
                self._cpy_operand_cleanup_block = self._try_err_block
                self._slot_call_runtime_call(
                    "py_bytes_split_full", tuple(operands), result_slot=output,
                    suffix_args=(ir.Constant(_I64, 1 if attr.name == "rsplit" else 0),),
                    argument_order=(roles.index("receiver"), roles.index("sep"),
                                    roles.index("maxsplit"), len(operands)),
                    span=expr.span,
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
                and not self._cpy_env_flags.get(
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
            isinstance(obj_ty, FloatType)
            and attr.name == "is_integer"
            and not expr.args
            and not expr.kwargs
        ):
            # Native float.is_integer() returns a bool (i1). Dynamic receivers
            # use the guarded branch above, preserving ordinary user methods.
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
            isinstance(obj_ty, IntType)
            and attr.name == "bit_length"
            and not expr.args
            and not expr.kwargs
        ):
            # Native int.bit_length() — avoid the libpython fallback so it works
            # under --python-libpython=off. Returns the bit count as an int (i64),
            # exact for bignums (py_int_bit_length). Dynamic receivers use the
            # guarded branch above.
            obj = self._emit_as_object(attr.obj)
            return self.builder.call(
                self.runtime["py_int_bit_length"],
                [obj],
                name=self._fresh("int.bit_length"),
            )
        if (
            isinstance(obj_ty, IntType)
            and attr.name == "bit_count"
            and not expr.args
            and not expr.kwargs
        ):
            # Native int.bit_count() — sibling of bit_length; avoid the libpython
            # fallback so it works under --python-libpython=off. Returns the
            # population count (set bits of abs(value)) as an int (i64), exact
            # for bignums (py_int_bit_count popcounts each limb). Dynamic
            # receivers use the guarded branch above.
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
            if raw_val not in self._cpy_values and (
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

    def _emit_int_to_bytes_method(self, expr, attr, receiver_slot=None):
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
            for index, operand in enumerate(operands):
                cleanup = self._extern_cleanup_block(tuple(roots), target)
                self._try_err_block = cleanup
                self._cpy_operand_cleanup_block = cleanup
                if index == 0 and receiver_slot is not None:
                    value = self.builder.load(receiver_slot, name=self._fresh("to_bytes.receiver.current"))
                    owned = False
                else:
                    value = self._emit_expr_with_cpy_operand_cleanup(
                        operand, (), as_pcc_object=True,
                    )
                    owned = self._owned_release_needed(value, operand)
                roots.append(self._extern_enter_root(
                    value, owned, "to_bytes.argument.root",
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
