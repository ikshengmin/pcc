"""Return-statement lowering for L1CodeGen."""

from __future__ import annotations

import os
import sys

from pcc.ir.compat import ir

from pcc.frontends.python.py_ast import DynType, IntType, Name, RawPointerType, Return
from pcc.frontends.python.codegen import marshal
from pcc.frontends.python.codegen.errors import L1CodegenError
from pcc.runtime.py.py_abi_constants import PYOBJECTHEADER_FLAGS_OFFSET, PY_FLAG_GC_PINNED


class ReturnLoweringMixin:
    def _return_log(self, label: str) -> None:
        if not os.environ.get("PCC_DEBUG_CODEGEN_PHASES"):
            return
        mod_name = self.ast_module.name or "<module>"
        func_name = (
            self.current_func_def.name if self.current_func_def is not None else "<top>"
        )
        sys.stderr.write(
            "[pcc.frontends.c.codegen] " + mod_name + ":" + func_name + ":return " + label + "\n"
        )

    def _emit_cancel_pending_return_roots(self, loop_exit: bool = False, root_base: int = 0) -> None:
        for owner, slot, loop_depth in reversed(self._return_cleanup_roots[root_base:]):
            if owner is self.current_function and (
                not loop_exit or len(self.loop_stack) <= loop_depth
            ):
                self._clear_exception_selection_owner_slot(slot)

    def _emit_finally_entry(self, entry) -> None:
        if callable(entry):
            entry()
            return
        if isinstance(entry, tuple) and len(entry) == 4 and entry[0] in ("pcc.finally.body", "pcc.finally.call"):
            outer_error = entry[2]
            root_base = entry[3]
            cleanup = None
            for owner, _slot, _loop_depth in self._return_cleanup_roots[root_base:]:
                if owner is self.current_function:
                    cleanup = self.current_function.append_basic_block(name=self._fresh("finally.return.error"))
                    break
            previous = self._push_try_err_block(cleanup or outer_error)
            try:
                if entry[0] == "pcc.finally.call":
                    entry[1]()
                else:
                    self._emit_stmts(entry[1])
            finally:
                self._restore_try_err_block(previous)
            continuation = self.builder._block
            if cleanup is not None:
                self.builder.position_at_end(cleanup)
                self._emit_cancel_pending_return_roots(root_base=root_base)
                self.builder.branch(outer_error or self._ensure_fn_err_exit())
                self.builder.position_at_end(continuation)
            return
        self._emit_stmts(entry)

    def _emit_pending_finally_blocks(self) -> None:
        self._return_log("finally begin")
        stack = self._finally_stack
        if not stack:
            return
        previous = self._emitting_finally
        try:
            index = len(stack) - 1
            while index >= 0:
                if self._builder_block_is_terminated():
                    return
                # An overriding exit must unwind the remaining OUTER entries,
                # without recursively running the finally currently executing.
                self._finally_stack = stack[:index]
                self._emitting_finally = False
                self._emit_finally_entry(stack[index])
                index -= 1
        finally:
            self._finally_stack = stack
            self._emitting_finally = previous
        self._return_log("finally end")

    def _return_value_needs_retain(self, value: ir.Value, stmt: Return) -> bool:
        """Return true when a PyObject* return value is borrowed locally.

        User/native pcc function calls use the normal Python/C-API ownership
        convention: the caller receives an owned reference.  Constructors,
        container literals, subscripts, and owned locals already satisfy that
        contract.  Parameters, module globals, and non-owned locals are borrowed
        in the callee, so returning them must promote the borrow to an owned
        result before caller-side cleanup may release it.
        """
        if stmt.value is None:
            return False
        if not isinstance(value.type, ir.PointerType):
            return False
        if isinstance(self.current_func_def.return_ty, RawPointerType):
            # _emit_return validates the explicit raw boundary before this.
            return False
        if value in getattr(self, "_cpy_values", ()):
            return False
        if self._value_is_owned_object(value):
            # The emitter registered this SSA value as carrying one owner (a
            # py_obj_getattr / py_obj_call result on the dynamic path).  The
            # AST-shape classifier below answers "not owned" for Attr and
            # dynamic Call, which retained a SECOND reference and returned that
            # copy while the original leaked (the attribute object's finalizer
            # never ran).  The ledger is authoritative, as it already is for
            # every borrowing consumer via _gc_release_if_owned: transfer the
            # existing owner to the caller without a retain.
            return False
        expr = stmt.value
        if self._expr_returns_unsafe_raw_pointer(expr):
            return False
        if isinstance(expr, Name):
            if expr.ident in getattr(self, "_owned_local_names", set()):
                return False
            if expr.ident in getattr(self, "_current_param_names", set()):
                return True
            if expr.ident in getattr(self, "_module_globals", {}):
                return True
            if expr.ident in self.env:
                return True
        if self._expr_returns_owned_object(expr):
            return False
        if getattr(self, "_module_has_c_abi_export", False) and getattr(
            self, "_module_uses_raw_int_scaffold", False
        ):
            return False
        expr_ty = getattr(expr, "ty", None)
        ret_decl_ty = getattr(self.current_func_def, "return_ty", None)
        return self._is_object(expr_ty) or self._is_object(ret_decl_ty)

    def _retain_borrowed_return_value(
        self,
        value: ir.Value,
        stmt: Return,
    ) -> ir.Value:
        if getattr(self, "_suppress_borrowed_return_retain", False):
            return value
        expr = stmt.value
        if (
            self._finally_stack
            and isinstance(value.type, ir.PointerType)
            and value not in self._cpy_values
            and isinstance(expr, Name)
            and expr.ident in self._owned_local_names
            and not self._expr_returns_unsafe_raw_pointer(expr)
        ):
            return self._gc_retain(value, name=self._fresh("ret.cleanup.owner"))
        if (
            isinstance(value.type, ir.PointerType)
            and value not in getattr(self, "_cpy_values", ())
            and not self._value_is_owned_object(value)
            and isinstance(expr, Name)
            and not self._expr_returns_unsafe_raw_pointer(expr)
            and expr.ident in getattr(self, "_owned_local_names", set())
        ):
            slot = self.env.get(expr.ident)
            flag = None if slot is None else self._owned_local_flag_for(expr.ident, slot[0])
            if flag is not None:
                # A local may initially borrow a parameter and acquire an
                # owner only on a conditional/loop path. Static membership
                # in _owned_local_names does not prove this path owns it.
                is_owned = self.builder.load(flag, name=self._fresh("ret.local.owned"))
                owned_end = self.builder.block
                retain_bb = self.current_function.append_basic_block(name=self._fresh("ret.local.borrowed"))
                merge_bb = self.current_function.append_basic_block(name=self._fresh("ret.local.ready"))
                self.builder.cbranch(is_owned, merge_bb, retain_bb)
                self.builder.position_at_end(retain_bb)
                retained = self._gc_retain(value, name=self._fresh("ret.local.retain"))
                retained_end = self.builder.block
                self.builder.branch(merge_bb)
                self.builder.position_at_end(merge_bb)
                result = self.builder.phi(value.type, name=self._fresh("ret.local.value"))
                result.add_incoming(value, owned_end)
                result.add_incoming(retained, retained_end)
                self._note_owned_object_value(result)
                return result
            # A same-name local with different storage has no ownership
            # proof from an old slot's flag. Treat its load as borrowed.
            return self._gc_retain(value, name=self._fresh("ret.retain"))
        if not self._return_value_needs_retain(value, stmt):
            return value
        return self._gc_retain(value, name=self._fresh("ret.retain"))

    def _return_value_needs_cleanup_root(
        self,
        value: ir.Value,
        stmt: Return,
        *,
        force_object: bool = False,
    ) -> bool:
        if getattr(self, "_suppress_implicit_gc_roots", False):
            return False
        mod_name = self.ast_module.name or ""
        if mod_name.startswith("pcc.runtime.py."):
            return False
        if stmt.value is None:
            return False
        if not isinstance(value.type, ir.PointerType):
            return False
        if isinstance(self.current_func_def.return_ty, RawPointerType):
            return False
        if value in getattr(self, "_cpy_values", ()):
            return False
        if self._expr_returns_unsafe_raw_pointer(stmt.value):
            return False
        if force_object:
            return True
        expr_ty = getattr(stmt.value, "ty", None)
        ret_decl_ty = getattr(self.current_func_def, "return_ty", None)
        return self._is_object(expr_ty) or self._is_object(ret_decl_ty)

    def _enter_return_cleanup_root(
        self,
        value: ir.Value,
        stmt: Return,
        *,
        force_object: bool = False,
    ):
        if not self._return_value_needs_cleanup_root(
            value,
            stmt,
            force_object=force_object,
        ):
            return None, None
        slot = self.builder.alloca(value.type, name=self._fresh("ret.tmp.root"))
        self.builder.store(ir.Constant(value.type, None), slot)
        slot_ptr = self._as_gc_ptr(slot, name=self._fresh("ret.tmp.root.ptr"))
        self.builder.call(self.runtime["pcc_gc_store_root"], [slot_ptr, value])
        self._emit_current_gc_frame_enter_lifo(self._gc_one_slot_frame_map(), slot)
        return slot, slot_ptr

    def _leave_return_cleanup_root(
        self,
        value: ir.Value,
        slot: ir.Value,
        slot_ptr: ir.Value,
    ) -> ir.Value:
        if slot is None or slot_ptr is None:
            return value
        current = self.builder.call(
            self.runtime["pcc_gc_load_ptr"],
            [ir.Constant(value.type, None), slot_ptr],
            name=self._fresh("ret.tmp.current"),
        )
        self.builder.call(
            self.runtime["pcc_gc_store_root"],
            [slot_ptr, ir.Constant(value.type, None)],
        )
        self._emit_gc_frame_leave_lifo_for_slot(slot)
        return current

    def _emit_owned_return_through_finally(self, value, stmt: Return) -> None:
        # Only non-resumable activations use this path. Generator returns use
        # their preplanned managed heap-frame slots in generator_lowering.
        hidden = self._fresh("return.cleanup.owner")
        slot = self._exception_selection_owner_slot(value, "return.cleanup", hidden)
        owned = self.builder.call(
            self.runtime["pcc_gc_load_ptr"],
            [ir.Constant(value.type, None), self._as_gc_ptr(slot)],
            name=self._fresh("return.cleanup.transfer"),
        )
        self._gc_release(owned)
        error = self.current_function.append_basic_block(name=self._fresh("return.cleanup.error"))
        saved_error = self._push_try_err_block(error)
        saved_roots = self._return_cleanup_roots
        self._return_cleanup_roots = list(saved_roots) + [(self.current_function, slot, len(self.loop_stack))]
        try:
            self._emit_pending_finally_blocks()
        finally:
            self._return_cleanup_roots = saved_roots
            self._restore_try_err_block(saved_error)
        if not self._builder_block_is_terminated():
            value = self.builder.call(
                self.runtime["pcc_gc_load_ptr"],
                [ir.Constant(value.type, None), self._as_gc_ptr(slot)],
                name=self._fresh("return.cleanup.current"),
            )
            # Preserve an existing pin lease. The new owned return remains
            # pinned through every local finalizer and root unregistration.
            bits = self.builder.ptrtoint(value, ir.IntType(64))
            tagged = self.builder.icmp_unsigned(
                "!=", self.builder.and_(bits, ir.Constant(ir.IntType(64), 1)),
                ir.Constant(ir.IntType(64), 0),
            )
            nonnull = self.builder.icmp_unsigned("!=", value, ir.Constant(value.type, None))
            managed = self.builder.and_(nonnull, self.builder.not_(tagged))
            header = self.current_function.append_basic_block(name=self._fresh("return.pin.header"))
            ready = self.current_function.append_basic_block(name=self._fresh("return.pin.ready"))
            empty = self.builder._block
            self.builder.cbranch(managed, header, ready)
            self.builder.position_at_end(header)
            address = self.builder.gep(value, [ir.Constant(ir.IntType(64), PYOBJECTHEADER_FLAGS_OFFSET)])
            flags = self.builder.load(self.builder.bitcast(address, ir.IntType(32).as_pointer()))
            prior_bits = self.builder.and_(flags, ir.Constant(ir.IntType(32), PY_FLAG_GC_PINNED))
            prior_value = self.builder.zext(prior_bits, ir.IntType(64))
            previous = self.builder._block
            self.builder.branch(ready)
            self.builder.position_at_end(ready)
            prior = self.builder.phi(ir.IntType(64), name=self._fresh("return.pin.prior"))
            prior.add_incoming(ir.Constant(ir.IntType(64), 0), empty)
            prior.add_incoming(prior_value, previous)
            self._gc_pin(value)
            self._emit_owned_local_cleanup(skip_name=hidden)
            result = self.builder.call(
                self.runtime["pcc_gc_take_pinned_slot"], [self._as_gc_ptr(slot), prior],
                name=self._fresh("return.cleanup.take"),
            )
            # Later-discovered frame leaves and recursion accounting must run
            # before the no-park ownership handoff, never after its unpin.
            self._return_handoff_sites.append((self.current_function, self.builder._block, self.builder._block._instrs[-1]))
            self.builder.ret(result)
        continuation = self.builder._block
        self.builder.position_at_end(error)
        self.builder.call(
            self.runtime["pcc_gc_store_root"], [self._as_gc_ptr(slot), ir.Constant(value.type, None)],
        )
        self.builder.branch(saved_error or self._ensure_fn_err_exit())
        self.builder.position_at_end(continuation)

    def _emit_return(self, stmt: Return) -> None:
        self._return_log("begin")
        fn = self.current_function
        ret_ty = fn.function_type.return_type
        if stmt.value is None:
            if getattr(self, "_async_body_depth", 0) > 0 and isinstance(
                ret_ty, ir.PointerType
            ):
                self._return_log("bare async ptr")
                self._emit_pending_finally_blocks()
                if self._builder_block_is_terminated():
                    self._return_log("bare async terminated")
                    return
                self._return_log("bare async cleanup")
                self._emit_owned_local_cleanup()
                self.builder.ret(self._emit_none_literal())
                self._return_log("bare async end")
                return
            if isinstance(ret_ty, ir.VoidType):
                self._return_log("bare void")
                self._emit_pending_finally_blocks()
                if self._builder_block_is_terminated():
                    self._return_log("bare void terminated")
                    return
                self._return_log("bare void cleanup")
                self._emit_owned_local_cleanup()
                self.builder.ret_void()
                self._return_log("bare void end")
                return
            self._return_log("bare nonvoid")
            self._emit_pending_finally_blocks()
            if self._builder_block_is_terminated():
                self._return_log("bare nonvoid terminated")
                return
            self._return_log("bare nonvoid cleanup")
            self._emit_owned_local_cleanup()
            if isinstance(ret_ty, ir.PointerType):
                # A bare Python return is a successful ``None`` result. NULL
                # is reserved for the C-API failure sentinel.
                self.builder.ret(self._emit_none_literal())
            elif isinstance(ret_ty, ir.IntType):
                self.builder.ret(ir.Constant(ret_ty, 0))
            elif isinstance(ret_ty, (ir.FloatType, ir.DoubleType)):
                self.builder.ret(ir.Constant(ret_ty, 0.0))
            else:
                raise L1CodegenError(f"bare 'return' fallback can't zero-init {ret_ty}")
            self._return_log("bare nonvoid end")
            return
        if isinstance(ret_ty, ir.VoidType):
            self._return_log("value void emit expr")
            value = self._emit_expr(stmt.value)
            self._return_log("value void release")
            self._gc_release_if_owned(value, stmt.value)
            self._return_log("value void finally")
            self._emit_pending_finally_blocks()
            if self._builder_block_is_terminated():
                self._return_log("value void terminated")
                return
            self._return_log("value void cleanup")
            self._emit_owned_local_cleanup()
            self._return_log("value void ret")
            self.builder.ret_void()
            self._return_log("value void end")
            return
        if isinstance(ret_ty, ir.PointerType) and isinstance(
            self.current_func_def.return_ty, IntType
        ):
            self._return_log("exact int object")
            value = self._emit_exact_int_operand_object(stmt.value)
            value = self._retain_borrowed_return_value(value, stmt)
            if self._finally_stack and self._return_value_needs_cleanup_root(value, stmt, force_object=True):
                self._emit_owned_return_through_finally(value, stmt)
                return
            self._emit_pending_finally_blocks()
            if self._builder_block_is_terminated():
                self._return_log("exact int terminated")
                return
            skip_name = stmt.value.ident if isinstance(stmt.value, Name) else None
            ret_root_slot, ret_root_ptr = self._enter_return_cleanup_root(
                value,
                stmt,
                force_object=True,
            )
            self._return_log("exact int cleanup")
            self._emit_owned_local_cleanup(skip_name=skip_name)
            value = self._leave_return_cleanup_root(
                value,
                ret_root_slot,
                ret_root_ptr,
            )
            self.builder.ret(value)
            self._return_log("exact int end")
            return
        self._return_log("value emit expr")
        exact_i64_return = (
            isinstance(ret_ty, ir.IntType)
            and isinstance(self.current_func_def.return_ty, IntType)
            and self._int_expr_needs_exact_object_boundary(stmt.value)
        )
        valueclass_target_ty = self.current_func_def.return_ty
        if not self._is_valueclass_payload_type(
            valueclass_target_ty
        ) and self._is_valueclass_payload_type(stmt.value.ty):
            valueclass_target_ty = stmt.value.ty
        valueclass_payload = self._maybe_emit_valueclass_constructor_payload(
            valueclass_target_ty,
            stmt.value,
        )
        valueclass_payload_fields_owned = False
        if exact_i64_return:
            # Compute in Python's arbitrary-precision object projection first;
            # only the explicit C-ABI boundary may project the final value back
            # to i64.  Unboxing exact operands before evaluating the expression
            # would silently turn a bignum add/shift into wrapping machine IR.
            exact_object = self._emit_exact_int_operand_object(stmt.value)
            exact_owned = self._pcc_pointer_source_is_owned(stmt.value)
            exact_pinned = (
                isinstance(exact_object.type, ir.PointerType)
                and exact_object not in getattr(self, "_cpy_values", ())
            )
            if exact_pinned:
                self._gc_pin(exact_object)
            value = marshal.marshal_from_object(
                self.builder,
                self.module,
                self.runtime,
                exact_object,
                self.current_func_def.return_ty,
            )
            if exact_pinned:
                self._gc_unpin(exact_object)
            if exact_owned:
                self._gc_release(
                    exact_object,
                    self._release_context_label("exact-i64-return"),
                )
        elif valueclass_payload is not None:
            value = valueclass_payload
            valueclass_payload_fields_owned = (
                self._valueclass_payload_expr_fields_are_owned(stmt.value)
            )
        elif isinstance(stmt.value, Name) and self._name_returns_owned_function_value(
            stmt.value.ident
        ):
            value = self._emit_expr_with_native_callable_values(stmt.value)
        elif (
            isinstance(ret_ty, ir.PointerType)
            and self._is_object(self.current_func_def.return_ty)
            and isinstance(stmt.value.ty, IntType)
        ):
            value = self._emit_expr_as_pcc_object(stmt.value)
        else:
            value = self._emit_expr(stmt.value)
        self._return_log("value coerce")
        if (
            self._is_valueclass_payload_type(valueclass_target_ty)
            and isinstance(ret_ty, ir.PointerType)
            and self._is_object(self.current_func_def.return_ty)
            and not isinstance(value.type, ir.PointerType)
        ):
            boxed_valueclass = self._emit_valueclass_payload_to_object(
                value,
                valueclass_target_ty,
                consume_fields=valueclass_payload_fields_owned,
            )
            if boxed_valueclass is not None:
                value = boxed_valueclass
            else:
                value = self._coerce(
                    value,
                    stmt.value.ty,
                    self.current_func_def.return_ty,
                )
        else:
            # Legacy runtime C exports may omit their ptr return annotation.
            # Keep that native ABI without claiming a managed conversion or
            # changing the unknown export's ownership metadata. Explicit
            # Python object annotations and ordinary helpers remain strict.
            raw_c_return = (
                getattr(self, "_runtime_port_module", False)
                and isinstance(stmt.value.ty, RawPointerType)
                and isinstance(self.current_func_def.return_ty, DynType)
                and not self.current_func_def.has_return_annotation
                and isinstance(value.type, ir.PointerType)
                and isinstance(ret_ty, ir.PointerType)
                and self._func_c_abi_export_symbol(self.current_func_def)
                    == self.current_function.name
            )
            raw_return_view = (
                (getattr(self, "_runtime_port_module", False)
                 or getattr(self, "_freestanding_module", False))
                and isinstance(self.current_func_def.return_ty, RawPointerType)
                and isinstance(stmt.value.ty, DynType)
                and isinstance(value.type, ir.PointerType)
                and isinstance(ret_ty, ir.PointerType)
            )
            if not raw_c_return and not raw_return_view:
                value = self._coerce(value, stmt.value.ty, self.current_func_def.return_ty, stmt.value)
        if value.type != ret_ty:
            self._return_log("value fix ret type")
            if isinstance(ret_ty, ir.IntType) and isinstance(value.type, ir.IntType):
                if value.type.width > ret_ty.width:
                    value = self.builder.trunc(
                        value,
                        ret_ty,
                        name=self._fresh("ret.trunc"),
                    )
                elif value.type.width < ret_ty.width:
                    if value.type.width == 1:
                        value = self.builder.zext(
                            value,
                            ret_ty,
                            name=self._fresh("ret.zext"),
                        )
                    else:
                        value = self.builder.sext(
                            value,
                            ret_ty,
                            name=self._fresh("ret.sext"),
                        )
            if isinstance(ret_ty, ir.PointerType) and not isinstance(
                value.type, ir.PointerType
            ):
                value = marshal.marshal_to_object(
                    self.builder,
                    self.module,
                    self.runtime,
                    value,
                    stmt.value.ty,
                )
        if value in getattr(self, "_cpy_values", ()):
            # A NULL from the libpython bridge must take the error edge while
            # the GC frames are still entered: the cleanup below leaves them,
            # and err.exit leaves them again (the precise stack map rejected
            # the join, and the frames were left twice).
            self._guard_cpy_value_not_null(value)
        value = self._retain_borrowed_return_value(value, stmt)
        if self._finally_stack and self._return_value_needs_cleanup_root(value, stmt):
            self._emit_owned_return_through_finally(value, stmt)
            return
        self._return_log("value finally")
        self._emit_pending_finally_blocks()
        if self._builder_block_is_terminated():
            self._return_log("value terminated")
            return
        # A local object can be skipped only when its owned pointer is being
        # transferred to a pointer-return ABI.  Exact-int locals returned
        # through an i64 C ABI have already been unboxed at this point; their
        # backing object must still be released during function cleanup.
        skip_name = (
            stmt.value.ident
            if isinstance(stmt.value, Name)
            and isinstance(value.type, ir.PointerType)
            else None
        )
        ret_root_slot, ret_root_ptr = self._enter_return_cleanup_root(value, stmt)
        self._return_log("value cleanup")
        self._emit_owned_local_cleanup(skip_name=skip_name)
        value = self._leave_return_cleanup_root(value, ret_root_slot, ret_root_ptr)
        if value in getattr(self, "_cpy_values", ()):
            if self._cpy_value_is_owned(value):
                # Transfer the expression's existing new reference to the
                # caller; no extra retain is needed.
                self._forget_owned_cpy_value(value)
            else:
                # Parameters/module globals/other borrowed CPython values must
                # be promoted so every successful function call returns an
                # owned reference.
                self.builder.call(self.runtime["py_cpy_incref"], [value])
        self._return_log("value ret")
        self.builder.ret(value)
        self._return_log("value end")
