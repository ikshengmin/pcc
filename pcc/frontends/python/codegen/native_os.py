"""Native ``os``/``os.path``/``platform`` lowering helpers."""
from __future__ import annotations

from typing import Optional

from pcc.ir.compat import ir

from pcc.frontends.python.py_ast import (
    Attr,
    Call,
    Expr,
    ListType,
    Name,
    NoneLit,
    NoneType,
    Slice,
    StrType,
    StrLit,
    Subscript,
    TupleType,
)
from pcc.frontends.python.codegen import marshal


_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_PYOBJ = ir.IntType(8).as_pointer()



class NativeOsLoweringMixin:
    def _emit_owned_os_getenv_call(self, expr: Call) -> ir.Value:
        """Retain both operands and publish getenv's uniform NEW result."""
        previous = self._current_try_err_block()
        target = previous if previous is not None else self._ensure_fn_err_exit()
        saved_cpy = self._cpy_operand_cleanup_block
        sink = self._slot_call_result_sink(expr)
        output = sink
        roots = []
        if output is None:
            output = self._new_slot_call_root("os.getenv.result")
            roots.append(output)
        try:
            self._try_err_block = self._slot_call_cleanup_block(tuple(roots), target)
            self._cpy_operand_cleanup_block = self._try_err_block
            key = self._emit_slot_call_operand(expr.args[0], "os.getenv.key")
            roots.append(key)
            self._try_err_block = self._slot_call_cleanup_block(tuple(roots), target)
            self._cpy_operand_cleanup_block = self._try_err_block
            default_expr = (
                expr.args[1] if len(expr.args) == 2
                else NoneLit(span=expr.span, ty=NoneType(name="None"))
            )
            default = self._emit_slot_call_operand(default_expr, "os.getenv.default")
            roots.append(default)
            self._try_err_block = self._slot_call_cleanup_block(tuple(roots), target)
            self._cpy_operand_cleanup_block = self._try_err_block
            self._slot_call_runtime_call(
                "py_os_getenv", (key, default), result_slot=output, span=expr.span,
            )
            self._release_slot_call_roots((key, default))
            if sink is None:
                return self._take_slot_call_root(output)
            return self.builder.load(output, name=self._fresh("os.getenv.current"))
        finally:
            self._try_err_block = previous
            self._cpy_operand_cleanup_block = saved_cpy

    def _is_os_environ_attr(self, expr: Expr) -> bool:
        """Recognise the ``os.environ`` attribute expression."""
        return (
            isinstance(expr, Attr)
            and expr.name == "environ"
            and isinstance(expr.obj, Name)
            and self._native_builtin_module_for_name(expr.obj.ident) == "os"
        )

    def _emit_native_os_environ_call(self,
        expr: Call,
    ) -> Optional[ir.Value]:
        """Lower ``os.environ.get(name[, default])`` to ``py_os_getenv``.

        ``os.environ.get`` and ``os.getenv`` are semantically equivalent
        for the missing-key case (both return ``default``), so they
        share a runtime helper.
        """
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs or not self._is_os_environ_attr(attr.obj):
            return None
        if attr.name in ("keys", "values", "items") and not expr.args:
            # The snapshot is a real dict, so its own method lowering takes
            # it from here; every caller of these wants a materialised view.
            snapshot = self._emit_native_os_environ_snapshot()
            result = self.builder.call(
                self.runtime["py_dict_" + attr.name],
                [snapshot],
                name=self._fresh("os.environ." + attr.name),
            )
            self._gc_release(snapshot)
            return result
        if attr.name == "get" and 1 <= len(expr.args) <= 2:
            return self._emit_owned_os_getenv_call(expr)
        if attr.name == "pop" and 1 <= len(expr.args) <= 2:
            key_obj = self._emit_as_object(expr.args[0])
            default_obj = self._emit_as_object(expr.args[1]) if len(expr.args) == 2 else None
            missing_bb = None
            join_bb = None
            if default_obj is not None:
                present = self.builder.call(
                    self.runtime["py_os_environ_contains"], [key_obj],
                    name=self._fresh("os.environ.pop.present"),
                )
                self._emit_post_call_err_check(getattr(expr, "span", None))
                found = self.builder.icmp_signed("!=", present, ir.Constant(_I32, 0))
                present_bb = self.current_function.append_basic_block(name=self._fresh("environ.pop.found"))
                missing_bb = self.current_function.append_basic_block(name=self._fresh("environ.pop.missing"))
                join_bb = self.current_function.append_basic_block(name=self._fresh("environ.pop.join"))
                self.builder.cbranch(found, present_bb, missing_bb)
                self.builder.position_at_end(present_bb)
            value = self.builder.call(
                self.runtime["py_os_environ_getitem"], [key_obj],
                name=self._fresh("os.environ.pop.value"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            self._gc_pin(value)
            self.builder.call(self.runtime["py_os_unsetenv"], [key_obj])
            self._emit_post_call_err_check(
                getattr(expr, "span", None), pinned_release_on_error=((value, True),),
            )
            self._gc_unpin(value)
            if default_obj is None:
                return value
            present_exit = self.builder._block
            self.builder.branch(join_bb)
            self.builder.position_at_end(missing_bb)
            default_value = self._gc_retain(default_obj)
            missing_exit = self.builder._block
            self.builder.branch(join_bb)
            self.builder.position_at_end(join_bb)
            result = self.builder.phi(_PYOBJ, name=self._fresh("os.environ.pop.result"))
            result.add_incoming(value, present_exit)
            result.add_incoming(default_value, missing_exit)
            self._note_owned_object_value(result)
            return result
        return None

    def _emit_native_os_environ_snapshot(self) -> ir.Value:
        """``dict(os.environ)`` -- materialise the special form as a dict."""
        return self.builder.call(
            self.runtime["py_os_environ_snapshot"],
            [],
            name=self._fresh("os.environ.snapshot"),
        )

    def _maybe_emit_native_os_environ_dict(self, expr: Call) -> Optional[ir.Value]:
        """``dict(os.environ)``.

        `run_runtime_make` opens with exactly this, and without a lowering
        the call fell through to CPython, which under
        ``--python-libpython=off`` stubbed the whole function -- leaving
        pcc1 unable to rebuild its own runtime archive.
        """
        if expr.kwargs or len(expr.args) != 1:
            return None
        if not self._is_os_environ_attr(expr.args[0]):
            return None
        return self._emit_native_os_environ_snapshot()

    def _emit_native_os_environ_subscript(self,
        expr: Subscript,
    ) -> Optional[ir.Value]:
        """Lower ``os.environ[key]`` to ``py_os_environ_getitem``.

        CPython mapping semantics: a missing variable raises KeyError
        (carrying the key) and a non-str key raises TypeError, so the
        runtime helper raises instead of returning a ``None`` fallback.
        ``os.environ.get`` / ``os.getenv`` keep the non-raising
        ``py_os_getenv`` path.
        """
        if isinstance(expr.idx, Slice):
            return None
        if not self._is_os_environ_attr(expr.obj):
            return None
        item = self.builder.call(
            self.runtime["py_os_environ_getitem"],
            [self._emit_as_object(expr.idx)],
            name=self._fresh("os.environ.getitem"),
        )
        # py_os_environ_getitem raises KeyError/TypeError; branch to the
        # surrounding try handler (or the fn err exit) when it did.
        self._emit_post_call_err_check(getattr(expr, "span", None))
        return item

    def _emit_native_os_environ_setitem_store(
        self,
        target: Subscript,
        value_expr: Expr,
    ) -> bool:
        """Store hook for ``os.environ[key] = value`` statements.

        Lowers to ``py_os_environ_setitem`` (str-only key/value with
        TypeError otherwise, setenv-backed store — CPython's putenv-
        backed ``__setitem__``). Returns True when handled natively.
        """
        if isinstance(target.idx, Slice):
            return False
        if not self._is_os_environ_attr(target.obj):
            return False
        v_obj = self._emit_expr_as_pcc_object(value_expr)
        self.builder.call(
            self.runtime["py_os_environ_setitem"],
            [self._emit_as_object(target.idx), v_obj],
            name=self._fresh("os.environ.setitem"),
        )
        self._emit_post_call_err_check(getattr(target, "span", None))
        self._gc_release_if_owned(v_obj, value_expr)
        return True

    def _emit_native_os_environ_setitem_value(
        self,
        target: Subscript,
        value: ir.Value,
        value_ty,
        *,
        release_value: bool = False,
    ) -> bool:
        """Store hook for ``os.environ[key] = <pre-computed value>``
        (tuple-unpack targets). Mirrors the release discipline of
        ``_store_value_at_subscript``. Returns True when handled."""
        if isinstance(target.idx, Slice):
            return False
        if not self._is_os_environ_attr(target.obj):
            return False
        v_obj = marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            value,
            value_ty,
        )
        self.builder.call(
            self.runtime["py_os_environ_setitem"],
            [self._emit_as_object(target.idx), v_obj],
            name=self._fresh("os.environ.setitem"),
        )
        self._emit_post_call_err_check(getattr(target, "span", None))
        if release_value and isinstance(v_obj.type, ir.PointerType):
            if v_obj not in getattr(self, "_cpy_values", ()):
                self._gc_release(v_obj)
        return True

    def _emit_native_os_environ_delitem(self, target: Subscript) -> bool:
        """Delete ``os.environ[key]`` with CPython mapping semantics.

        ``os.unsetenv`` itself deliberately does not raise for a missing
        variable.  Mapping deletion must raise ``KeyError``, so compose the
        existing raising getitem operation with unsetenv.  The key expression
        is evaluated once and shared by both calls.
        """
        if isinstance(target.idx, Slice):
            return False
        if not self._is_os_environ_attr(target.obj):
            return False
        key_obj = self._emit_as_object(target.idx)
        previous = self.builder.call(
            self.runtime["py_os_environ_getitem"],
            [key_obj],
            name=self._fresh("os.environ.delitem.previous"),
        )
        self._emit_post_call_err_check(getattr(target, "span", None))
        self._gc_release(previous)
        self.builder.call(
            self.runtime["py_os_unsetenv"],
            [key_obj],
            name=self._fresh("os.environ.delitem"),
        )
        return True

    def _emit_native_os_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if (
            isinstance(attr.obj, Name)
            and self._native_builtin_module_for_name(attr.obj.ident) == "os"
            and attr.name == "makedirs"
            and 1 <= len(expr.args) <= 3
        ):
            mode = ir.Constant(_I64, 0o777)
            exist_ok = ir.Constant(_I32, 0)
            mode_supplied = len(expr.args) >= 2
            exist_ok_supplied = len(expr.args) >= 3
            if mode_supplied:
                mode = self._emit_expr_as_i64(expr.args[1])
            if exist_ok_supplied:
                value_expr = expr.args[2]
                raw = self._emit_expr(value_expr)
                truthy = self._truthy(raw, value_expr.ty)
                exist_ok = self.builder.zext(
                    truthy,
                    _I32,
                    name=self._fresh("os.makedirs.exist_ok"),
                )
            for keyword, value_expr in expr.kwargs:
                if keyword == "mode" and not mode_supplied:
                    mode = self._emit_expr_as_i64(value_expr)
                    mode_supplied = True
                    continue
                if keyword == "exist_ok" and not exist_ok_supplied:
                    raw = self._emit_expr(value_expr)
                    truthy = self._truthy(raw, value_expr.ty)
                    exist_ok = self.builder.zext(
                        truthy,
                        _I32,
                        name=self._fresh("os.makedirs.exist_ok"),
                    )
                    exist_ok_supplied = True
                    continue
                if keyword != "mode" and keyword != "exist_ok":
                    return None
                return None
            result = self.builder.call(
                self.runtime["py_os_makedirs"],
                [self._emit_as_object(expr.args[0]), mode, exist_ok],
                name=self._fresh("os.makedirs"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if expr.kwargs:
            return None
        if (
            not isinstance(attr.obj, Name)
            or self._native_builtin_module_for_name(attr.obj.ident) != "os"
        ):
            return None
        name = attr.name
        if name == "getenv" and 1 <= len(expr.args) <= 2:
            return self._emit_owned_os_getenv_call(expr)
        if name == "putenv" and len(expr.args) == 2:
            return self.builder.call(
                self.runtime["py_os_putenv"],
                [
                    self._emit_as_object(expr.args[0]),
                    self._emit_as_object(expr.args[1]),
                ],
                name=self._fresh("os.putenv"),
            )
        if name == "unsetenv" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_os_unsetenv"],
                [self._emit_as_object(expr.args[0])],
                name=self._fresh("os.unsetenv"),
            )
        if name == "getcwd" and len(expr.args) == 0:
            return self.builder.call(
                self.runtime["py_os_getcwd_str"],
                [],
                name=self._fresh("os.getcwd"),
            )
        if name == "getpid" and len(expr.args) == 0:
            return self.builder.call(
                self.runtime["py_os_getpid"],
                [],
                name=self._fresh("os.getpid"),
            )
        if name == "kill" and len(expr.args) == 2:
            pid = self._emit_as_object(expr.args[0])
            pid_root = self._enter_container_temp_root(pid, self._fresh("os.kill.pid"))
            pid_lifetime = (
                pid_root,
                self._owned_release_needed(pid, expr.args[0])
                or self._pcc_pointer_source_is_owned(expr.args[0]),
            )
            # Evaluate the second operand in source order. Its factory can
            # collect or raise before the runtime helper has been entered.
            signal_number = self._emit_expr_with_cpy_operand_cleanup(
                expr.args[1], (), as_object=True,
                rooted_pcc_lifetimes=(pid_lifetime,),
            )
            signal_root = self._enter_container_temp_root(
                signal_number, self._fresh("os.kill.signal"),
            )
            roots = (pid_lifetime, (
                signal_root,
                self._owned_release_needed(signal_number, expr.args[1])
                or self._pcc_pointer_source_is_owned(expr.args[1]),
            ))
            operands = []
            for root, _owned in roots:
                operands.append(self.builder.call(
                    self.runtime["pcc_gc_load_ptr"],
                    [ir.Constant(_PYOBJ, None), self._as_gc_ptr(root)],
                    name=self._fresh("os.kill.argument"),
                ))
            old_error = self._current_try_err_block()
            error_target = old_error if old_error is not None else self._ensure_fn_err_exit()
            self._try_err_block = self._make_cpy_operand_cleanup_block(
                (), (), error_target, "os.kill.error.cleanup",
                rooted_pcc_lifetimes=roots,
            )
            try:
                self.builder.call(
                    self.runtime["py_os_kill"], operands,
                    name=self._fresh("os.kill"),
                )
                self._emit_post_call_err_check(getattr(expr, "span", None))
            finally:
                self._try_err_block = old_error
            self._release_rooted_pcc_lifetimes(roots)
            # The success result is always the immortal None singleton. Load
            # it after operand finalizers have run, with no unrooted result.
            return self._emit_none_literal()
        if name == "cpu_count" and len(expr.args) == 0:
            return self.builder.call(
                self.runtime["py_os_cpu_count"],
                [],
                name=self._fresh("os.cpu_count"),
            )
        if name == "uname" and len(expr.args) == 0:
            result = self.builder.call(
                self.runtime["py_os_uname"],
                [],
                name=self._fresh("os.uname"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if name == "listdir" and len(expr.args) == 1:
            return self._emit_owned_unary_runtime_call(expr, "py_os_listdir")
        if name == "write" and len(expr.args) == 2:
            fd_i64 = self._emit_expr_as_i64(expr.args[0])
            fd_val = self.builder.trunc(
                fd_i64,
                _I32,
                name=self._fresh("os.write.fd"),
            )
            data_val = self._emit_as_object(expr.args[1])
            i32v = self.builder.call(
                self.runtime["py_os_write"],
                [fd_val, data_val],
                name=self._fresh("os.write"),
            )
            return self.builder.sext(
                i32v,
                _I64,
                name=self._fresh("os.write.res"),
            )
        if name in ("unlink", "remove", "rmdir") and len(expr.args) == 1:
            result = self.builder.call(
                self.runtime["py_os_rmdir" if name == "rmdir" else "py_os_unlink"],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os." + name),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if name in ("replace", "rename") and len(expr.args) == 2:
            result = self.builder.call(
                self.runtime["py_os_replace"],
                [
                    self._emit_os_path_arg_object(expr.args[0]),
                    self._emit_os_path_arg_object(expr.args[1]),
                ],
                name=self._fresh("os." + name),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if name == "chmod" and len(expr.args) == 2:
            mode = self._emit_expr_as_i64(expr.args[1])
            result = self.builder.call(
                self.runtime["py_os_chmod"],
                [self._emit_os_path_arg_object(expr.args[0]), mode],
                name=self._fresh("os.chmod"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if name == "fsync" and len(expr.args) == 1:
            result = self.builder.call(
                self.runtime["py_os_fsync"],
                [self._emit_expr_as_i64(expr.args[0])],
                name=self._fresh("os.fsync"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if name == "access" and len(expr.args) == 2:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            mode_i64 = self._emit_expr_as_i64(expr.args[1])
            mode_val = self.builder.trunc(
                mode_i64,
                _I32,
                name=self._fresh("os.access.mode"),
            )
            i32v = self.builder.call(
                self.runtime["py_os_access"],
                [self._emit_os_path_arg_object(expr.args[0]), mode_val],
                name=self._fresh("os.access"),
            )
            return self.builder.icmp_signed(
                "!=",
                i32v,
                ir.Constant(_I32, 0),
                name=self._fresh("os.access.i1"),
            )
        if name == "_pcc_http_download_to_file" and len(expr.args) == 2:
            return self.builder.call(
                self.runtime["py_http_download_to_file"],
                [
                    self._emit_as_object(expr.args[0]),
                    self._emit_as_object(expr.args[1]),
                ],
                name=self._fresh("os.pcc_http_download_to_file"),
            )
        if name == "_pcc_sha256_file_hex" and len(expr.args) == 1:
            return self.builder.call(
                self.runtime["py_sha256_file_hex"],
                [self._emit_as_object(expr.args[0])],
                name=self._fresh("os.pcc_sha256_file_hex"),
            )
        if name == "_pcc_sha256_file_hex_bounded" and len(expr.args) == 2:
            path_object = self._emit_as_object(expr.args[0])
            path_root = self._enter_container_temp_root(
                path_object,
                "os.pcc_sha256_file_hex_bounded.path",
            )
            path_lifetime = (
                path_root,
                self._pcc_pointer_source_is_owned(expr.args[0]),
            )
            max_bytes = self._emit_expr_with_cpy_operand_cleanup(
                expr.args[1],
                (),
                as_i64=True,
                rooted_pcc_lifetimes=(path_lifetime,),
            )
            rooted_path = self.builder.call(
                self.runtime["pcc_gc_load_ptr"],
                [
                    ir.Constant(_PYOBJ, None),
                    self._as_gc_ptr(
                        path_root,
                        name=self._fresh("os.sha256.path.root.ptr"),
                    ),
                ],
                name=self._fresh("os.sha256.path.rooted"),
            )
            result = self.builder.call(
                self.runtime["py_sha256_file_hex_bounded"],
                [rooted_path, max_bytes],
                name=self._fresh("os.pcc_sha256_file_hex_bounded"),
            )
            # The owned digest result is not in a Python local yet.  Keep this
            # fresh string immobile while earlier operand roots are unwound
            # and while the post-call error flag is inspected.
            self._gc_pin(result)
            self._release_rooted_pcc_lifetimes((path_lifetime,))
            self._emit_post_call_err_check(
                getattr(expr, "span", None),
                pinned_release_on_error=((result, True),),
            )
            self._gc_unpin(result)
            return result
        return None

    def _emit_native_os_uname_attr(self, expr: Attr) -> Optional[ir.Value]:
        """Lower direct ``os.uname().<field>`` access.

        ``py_os_uname`` returns the correct five-field sequence for unpacking;
        this preserves the named-result access used by platform/bootstrap code
        without routing the result through libpython.
        """
        call = expr.obj
        if not isinstance(call, Call) or call.args or call.kwargs:
            return None
        func = call.func
        if (
            not isinstance(func, Attr)
            or func.name != "uname"
            or not isinstance(func.obj, Name)
            or self._native_builtin_module_for_name(func.obj.ident) != "os"
        ):
            return None
        field_index = {
            "sysname": 0,
            "nodename": 1,
            "release": 2,
            "version": 3,
            "machine": 4,
        }.get(expr.name)
        if field_index is None:
            return None
        result = self.builder.call(
            self.runtime["py_os_uname"],
            [],
            name=self._fresh("os.uname.attr.result"),
        )
        self._emit_post_call_err_check(getattr(expr, "span", None))
        field = self.builder.call(
            self.runtime["py_tuple_get"],
            [result, ir.Constant(_I64, field_index)],
            name=self._fresh(f"os.uname.{expr.name}"),
        )
        self._gc_release(result)
        return field

    def _emit_native_platform_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs or expr.args:
            return None
        if (
            not isinstance(attr.obj, Name)
            or self._native_builtin_module_for_name(attr.obj.ident) != "platform"
        ):
            return None
        if attr.name == "machine":
            return self.builder.call(
                self.runtime["py_platform_machine_str"],
                [],
                name=self._fresh("platform.machine"),
            )
        if attr.name == "release":
            return self.builder.call(
                self.runtime["py_platform_release_str"],
                [],
                name=self._fresh("platform.release"),
            )
        return None

    # Method names recognised by `_emit_native_os_path_call` —
    # nested calls of the form ``os.path.X(...)`` return native
    # PyObject* and are therefore safe to feed back into another
    # native os.path dispatch (no CPython contagion).
    _NATIVE_OS_PATH_DISPATCH_METHODS = frozenset(
        {
            "join",
            "basename",
            "dirname",
            "split",
            "exists",
            "isabs",
            "isfile",
            "isdir",
            "islink",
            "getmtime",
            "getsize",
            "abspath",
            "commonpath",
            "commonprefix",
            "splitext",
            "normcase",
            "normpath",
            "splitdrive",
            "expanduser",
            "expandvars",
            "relpath",
            "realpath",
        }
    )

    def _is_native_os_path_call_shape(self, expr: Expr) -> bool:
        """Recognise ``os.path.X(...)`` shapes that the native
        dispatch will lower — used by the arg-stays-native check to
        accept dispatched-call results without forcing the outer
        call back through the CPython path."""
        if not isinstance(expr, Call):
            return False
        func = expr.func
        if not isinstance(func, Attr):
            return False
        if self._native_builtin_value_kind_for_expr(func.obj) != "os.path":
            return False
        return func.name in self._NATIVE_OS_PATH_DISPATCH_METHODS

    def _native_os_path_arg_can_stay_native(self, expr: Expr) -> bool:
        if self._is_starred_unpack_expr(expr):
            return False
        if isinstance(expr, Name):
            ident = expr.ident
            if getattr(self, "_cpy_module_flags", {}).get(ident, False):
                return False
            if ident in getattr(self, "_cpy_module_env", {}):
                return False
            return True
        if isinstance(expr, Call):
            if self._is_native_os_path_call_shape(expr):
                return True
            func = expr.func
            if isinstance(func, Name) and func.ident == "str":
                return True
            if (
                isinstance(func, Attr)
                and func.name == "getcwd"
                and not expr.args
                and not expr.kwargs
                and isinstance(func.obj, Name)
                and self._native_builtin_module_for_name(func.obj.ident) == "os"
            ):
                return True
            return True
        if isinstance(expr, Attr):
            if expr.name == "_raw" and isinstance(expr.obj, Name) and expr.obj.ident == "self":
                return True
            if (
                expr.name in ("executable", "prefix", "base_prefix")
                and isinstance(expr.obj, Name)
                and self._native_builtin_module_for_name(expr.obj.ident) == "sys"
            ):
                return True
            if self._native_builtin_value_kind_for_expr(expr) == "os.path":
                return False
            if isinstance(expr.obj, Name):
                if self._native_builtin_module_for_name(expr.obj.ident) is not None:
                    return False
                if getattr(self, "_cpy_module_flags", {}).get(expr.obj.ident, False):
                    return False
                if expr.obj.ident in getattr(self, "_cpy_module_env", {}):
                    return False
            return True
        if isinstance(expr, Subscript):
            if isinstance(expr.idx, Slice):
                return isinstance(expr.ty, StrType) and self._native_os_path_arg_can_stay_native(expr.obj)
            if (
                isinstance(expr.obj, Attr)
                and expr.obj.name == "argv"
                and isinstance(expr.obj.obj, Name)
                and self._native_builtin_module_for_name(expr.obj.obj.ident) == "sys"
            ):
                return True
            return self._native_os_path_arg_can_stay_native(expr.obj)
        return True

    def _emit_os_path_arg_object(self, expr: Expr) -> ir.Value:
        value = self._emit_expr(expr)
        if value in getattr(self, "_cpy_values", ()):
            return self.builder.call(
                self.runtime["py_cpy_to_pcc_obj"],
                [value],
                name=self._fresh("cpy.path.arg"),
            )
        return marshal.marshal_to_object(
            self.builder,
            self.module,
            self.runtime,
            value,
            expr.ty,
        )

    def _pathlib_ctor_arg_for_suffix_attr(self, expr: Attr) -> Optional[Expr]:
        if expr.name != "suffix":
            return None
        ctor = expr.obj
        if not isinstance(ctor, Call) or ctor.kwargs or len(ctor.args) != 1:
            return None
        value_kind = self._native_builtin_value_kind_for_expr(ctor.func)
        if value_kind not in ("pathlib.Path", "pathlib.PurePath"):
            return None
        path_arg = ctor.args[0]
        if not self._native_os_path_arg_can_stay_native(path_arg):
            return None
        return path_arg

    def _emit_native_pathlib_suffix_attr(self, expr: Attr) -> Optional[ir.Value]:
        path_arg = self._pathlib_ctor_arg_for_suffix_attr(expr)
        if path_arg is None:
            return None
        split = self.builder.call(
            self.runtime["py_os_path_splitext"],
            [self._emit_os_path_arg_object(path_arg)],
            name=self._fresh("pathlib.suffix.splitext"),
        )
        suffix = self.builder.call(
            self.runtime["py_tuple_get"],
            [split, ir.Constant(_I64, 1)],
            name=self._fresh("pathlib.suffix"),
        )
        self._gc_release(split)
        return suffix

    def _emit_native_os_path_call(self, expr: Call) -> Optional[ir.Value]:
        attr = expr.func
        assert isinstance(attr, Attr)
        if expr.kwargs:
            return None
        if self._native_builtin_value_kind_for_expr(attr.obj) != "os.path":
            return None
        name = attr.name
        if name == "join":
            if len(expr.args) == 0:
                return None
            # Each arg is either positional (must be a known-native
            # value) or a splat ``*xs`` (xs must be a known-native
            # expression; py_list_extend owns list/tuple fast paths
            # and the generic iterable protocol).
            for arg in expr.args:
                if self._is_starred_unpack_expr(arg):
                    inner = arg.args[0]
                    if not self._native_os_path_arg_can_stay_native(inner):
                        return None
                    # ``py_list_extend`` natively handles ``list`` and
                    # ``tuple`` only (offsets into PyListObject /
                    # PyTupleObject); for ``DynType`` parts it falls to
                    # ``py_obj_iter`` which raises TypeError on CPython
                    # objects without ``__iter__`` dispatch coverage.
                    # Require a typed list / tuple here so the splat
                    # path can't smuggle a CPython container into a
                    # native iteration that won't handle it.
                    if not isinstance(inner.ty, (ListType, TupleType)):
                        return None
                else:
                    if not self._native_os_path_arg_can_stay_native(arg):
                        return None
            lst = self.builder.call(
                self.runtime["py_list_new"],
                [ir.Constant(_I64, 0)],
                name=self._fresh("os.path.join.args"),
            )
            for arg in expr.args:
                if self._is_starred_unpack_expr(arg):
                    inner_val = self._emit_as_object(arg.args[0])
                    self.builder.call(
                        self.runtime["py_list_extend"],
                        [lst, inner_val],
                    )
                else:
                    self.builder.call(
                        self.runtime["py_list_append"],
                        [lst, self._emit_os_path_arg_object(arg)],
                    )
            return self.builder.call(
                self.runtime["py_os_path_join"],
                [lst],
                name=self._fresh("os.path.join"),
            )
        if name == "basename" and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            return self.builder.call(
                self.runtime["py_os_path_basename"],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os.path.basename"),
            )
        if name == "dirname" and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            return self.builder.call(
                self.runtime["py_os_path_dirname"],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os.path.dirname"),
            )
        if name == "split" and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            return self.builder.call(
                self.runtime["py_os_path_split"],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os.path.split"),
            )
        if name == "exists" and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            i32v = self.builder.call(
                self.runtime["py_os_path_exists"],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os.path.exists"),
            )
            return self.builder.icmp_signed(
                "!=",
                i32v,
                ir.Constant(_I32, 0),
                name=self._fresh("os.path.exists.i1"),
            )
        if name in ("isabs", "isfile", "isdir", "islink") and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            helper = {
                "isabs": "py_os_path_isabs",
                "isfile": "py_os_path_isfile",
                "isdir": "py_os_path_isdir",
                "islink": "py_os_path_islink",
            }[name]
            i32v = self.builder.call(
                self.runtime[helper],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh(f"os.path.{name}"),
            )
            return self.builder.icmp_signed(
                "!=",
                i32v,
                ir.Constant(_I32, 0),
                name=self._fresh(f"os.path.{name}.i1"),
            )
        if name == "getmtime" and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            result = self.builder.call(
                self.runtime["py_os_path_getmtime"],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os.path.getmtime"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if name == "getsize" and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            helper = self.runtime.get("py_os_path_getsize")
            if helper is None:
                helper = self._declare_external_function(
                    "py_os_path_getsize",
                    _PYOBJ,
                    [_PYOBJ],
                )
            result = self.builder.call(
                helper,
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os.path.getsize"),
            )
            self._emit_post_call_err_check(getattr(expr, "span", None))
            return result
        if name == "commonpath" and len(expr.args) == 1:
            # Single-arg form: ``os.path.commonpath([a, b, ...])``.
            # The arg must be a list/tuple expression we can lower
            # natively; the runtime helper coerces each element.
            paths_arg = expr.args[0]
            if not self._native_os_path_arg_can_stay_native(paths_arg):
                return None
            return self.builder.call(
                self.runtime["py_os_path_commonpath"],
                [self._emit_os_path_arg_object(paths_arg)],
                name=self._fresh("os.path.commonpath"),
            )
        if name == "commonprefix" and len(expr.args) == 1:
            paths_arg = expr.args[0]
            if not self._native_os_path_arg_can_stay_native(paths_arg):
                return None
            return self.builder.call(
                self.runtime["py_os_path_commonprefix"],
                [self._emit_os_path_arg_object(paths_arg)],
                name=self._fresh("os.path.commonprefix"),
            )
        if name == "splitext" and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            return self.builder.call(
                self.runtime["py_os_path_splitext"],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os.path.splitext"),
            )
        if name == "normcase" and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            return self.builder.call(
                self.runtime["py_os_path_normcase"],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os.path.normcase"),
            )
        if name == "splitdrive" and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            return self.builder.call(
                self.runtime["py_os_path_splitdrive"],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os.path.splitdrive"),
            )
        if name == "expanduser" and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            return self.builder.call(
                self.runtime["py_os_path_expanduser"],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os.path.expanduser"),
            )
        if name == "expandvars" and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            return self.builder.call(
                self.runtime["py_os_path_expandvars"],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os.path.expandvars"),
            )
        if ((name in ("abspath", "normpath") and len(expr.args) == 1)
                or (name == "relpath" and len(expr.args) in (1, 2))) and not expr.kwargs:
            # Keep operands alive and pinned across later argument factories
            # and runtime entry polls. relpath validates its original path
            # before absolutizing, so an empty path still raises ValueError.
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            if len(expr.args) == 2 and not self._native_os_path_arg_can_stay_native(
                expr.args[1]
            ):
                return None
            path_obj = self._emit_os_path_arg_object(expr.args[0])
            path_root = self._enter_container_temp_root(path_obj, self._fresh("os.path.relpath.path"))
            path_lifetime = (path_root, self._owned_release_needed(path_obj, expr.args[0])
                             or self._pcc_pointer_source_is_owned(expr.args[0]))
            roots = (path_lifetime,)
            if name == "relpath":
                start_expr = expr.args[1] if len(expr.args) == 2 else StrLit(
                    span=getattr(expr, "span", None), ty=StrType(name="str"), value="."
                )
                start_obj = self._emit_expr_with_cpy_operand_cleanup(
                    start_expr, (), as_object=True, rooted_pcc_lifetimes=(path_lifetime,),
                )
                start_root = self._enter_container_temp_root(start_obj, self._fresh("os.path.start"))
                roots = roots + ((start_root,
                    self._owned_release_needed(start_obj, start_expr)
                    or self._pcc_pointer_source_is_owned(start_expr)),)
            operands = []
            for root, _owned in roots:
                operands.append(self.builder.call(
                    self.runtime["pcc_gc_load_ptr"],
                    [ir.Constant(_PYOBJ, None), self._as_gc_ptr(root)],
                    name=self._fresh("os.path.argument"),
                ))
            result = self.builder.call(
                self.runtime["py_os_path_" + name], operands,
                name=self._fresh("os.path.relpath"),
            )
            result_root = self._enter_container_temp_root(result, self._fresh("os.path.relpath.result"))
            old_error = self._current_try_err_block()
            error_target = old_error if old_error is not None else self._ensure_fn_err_exit()
            self._try_err_block = self._make_cpy_operand_cleanup_block(
                (), (), error_target, "os.path.relpath.error.cleanup",
                rooted_pcc_lifetimes=roots + ((result_root, True),),
            )
            try:
                self._emit_post_call_err_check(getattr(expr, "span", None))
            finally:
                self._try_err_block = old_error
            self._release_rooted_pcc_lifetimes(roots)
            result = self.builder.call(
                self.runtime["pcc_gc_load_ptr"],
                [ir.Constant(_PYOBJ, None), self._as_gc_ptr(result_root)],
                name=self._fresh("os.path.relpath.result.current"),
            )
            self._gc_pin(result)
            self._leave_container_temp_root(result_root)
            self._gc_unpin(result)
            self._note_owned_object_value(result)
            return result
        if name == "realpath" and len(expr.args) == 1:
            if not self._native_os_path_arg_can_stay_native(expr.args[0]):
                return None
            return self.builder.call(
                self.runtime["py_os_path_realpath"],
                [self._emit_os_path_arg_object(expr.args[0])],
                name=self._fresh("os.path.realpath"),
            )
        return None


__all__ = ["NativeOsLoweringMixin"]
