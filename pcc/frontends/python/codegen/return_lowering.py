"""Return-statement lowering for L1CodeGen."""

from __future__ import annotations

import os
import sys

from pcc.ir.compat import ir

from pcc.frontends.python.builtin_function_values import builtin_function_value_name_exists

from pcc.frontends.python.py_ast import (
    Attr,
    BinOp,
    BoolType,
    Call,
    ClassType,
    DynType,
    IfExpr,
    IntLit,
    IntType,
    ListType,
    Name,
    RawPointerType,
    Return,
    StrLit,
    StrType,
    Subscript,
    UnaryOp,
)
from pcc.frontends.python.codegen import marshal
from pcc.frontends.python.codegen.builtin_exceptions import BUILTIN_EXC_TAG
from pcc.frontends.python.codegen.cpy_import_state import live_import_global_slot
from pcc.frontends.python.codegen.str_default_cells import funcdef_defined_here
from pcc.frontends.python.codegen.errors import L1CodegenError
from pcc.runtime.py.py_abi_constants import PYOBJECTHEADER_FLAGS_OFFSET, PY_FLAG_GC_PINNED


_CSTR = ir.IntType(8).as_pointer()


def _boxed_int_string_parser_is_builtin(host, fd, value) -> bool:
    """Prove selection of the owning native string parser."""
    return (
        isinstance(value.ty, IntType) and value.ty.name == "int"
        and len(value.args) == 1
        and isinstance(value.args[0].ty, StrType)
        and not fd.is_method and fd.name not in host._hoisted_qualnames
        and not any(arg.name == "int" for arg in fd.args)
        and host._call_is_int_builtin(value)
        and host._native_builtin_value_for_name("int") == "builtins.int"
        and not host._expr_looks_cpython(value.func)
        and live_import_global_slot(host, "int") is None
        and not getattr(host, "_native_extension_star_module_env", {})
        and not getattr(host, "_cpy_star_module_env", {})
    )


def boxed_int_literal_parse_has_owner(host, fd, value) -> bool:
    return (_boxed_int_string_parser_is_builtin(host, fd, value)
            and isinstance(value.args[0], StrLit))


def boxed_int_formal_string_parse_has_owner(host, fd, value) -> bool:
    """A direct single-return callee parses its own typed string parameter."""
    if not _boxed_int_string_parser_is_builtin(host, fd, value):
        return False
    operand = value.args[0]
    if not isinstance(operand, Name) or len(fd.args) != 1:
        return False
    formal = fd.args[0]
    return (
        formal.kind in ("pos", "pos_only") and not formal.has_default
        and formal.name == operand.ident
        and isinstance(formal.annotation, StrType)
        and formal.annotation == operand.ty
    )


def _exact_int_repr(ty) -> bool:
    return isinstance(ty, IntType) and ty.name == "int" and ty.width == 64 and ty.signed


def boxed_int_instance_field_has_owner(host, fd, fn, value) -> bool:
    """Match the known native field path for the callee's sole receiver formal."""
    if (not isinstance(value, Attr) or not _exact_int_repr(value.ty)
            or not isinstance(value.obj, Name) or len(fd.args) != 1
            or fd.is_method or fd.name in host._hoisted_qualnames
            or not funcdef_defined_here(host, fd)
            or value.obj.ident == "self" or value.name.startswith("_")):
        return False
    arg = fd.args[0]
    ty = arg.annotation
    if (arg.name != value.obj.ident or arg.kind not in ("pos", "pos_only")
            or arg.has_default or not isinstance(ty, ClassType) or ty != value.obj.ty
            or ty.valueclass or ty.module != host.ast_module.name
            or host._resolve_class_alias(ty.name) != ty.name
            or live_import_global_slot(host, arg.name) is not None):
        return False
    params = fn.function_type.args
    if len(params) != 1 or not isinstance(params[0], ir.PointerType):
        return False
    # For a local class and a public, directly declared field, the exact-int
    # emitter resolves this same slot, performs the null/error check and
    # records py_instance_get_field's NEW reference.
    info = host.class_lowering.classes.get(ty.name)
    return (
        info is not None and info.owning_module in (None, ty.module)
        and not ty.bases and value.name in info.field_names
        and any(name == value.name and field_ty == value.ty
                for name, field_ty in ty.fields)
        and host.class_lowering.lookup_field_index(info, value.name) is not None
    )


def boxed_int_list_zero_has_owner(host, fd, fn, value) -> bool:
    """Prove the owning getitem path for a sole native list[int] formal."""
    if (not isinstance(value, Subscript) or not _exact_int_repr(value.ty)
            or not isinstance(value.obj, Name) or len(fd.args) != 1
            or not isinstance(value.idx, IntLit) or value.idx.value != 0
            or not _exact_int_repr(value.idx.ty)
            or fd.is_method or fd.name in host._hoisted_qualnames
            or not funcdef_defined_here(host, fd)):
        return False
    formal = fd.args[0]
    ty = formal.annotation
    params = fn.function_type.args
    return (
        formal.name == value.obj.ident and formal.kind in ("pos", "pos_only")
        and not formal.has_default and isinstance(ty, ListType)
        and _exact_int_repr(ty.elem) and ty == value.obj.ty
        and live_import_global_slot(host, formal.name) is None
        and len(params) == 1 and isinstance(params[0], ir.PointerType)
    )


def _direct_unary_boxed_int_def(host, fd, fn) -> bool:
    if (fd is None or not isinstance(fn, ir.Function)
            or fd.is_async or fd.is_method or fd.decorators or fd.manual_pointer_abi
            or fd.name in host._hoisted_qualnames
            or fn.name in host._manual_pointer_abi_functions
            or not funcdef_defined_here(host, fd)
            or not _exact_int_repr(fd.return_ty) or len(fd.args) != 1 or len(fd.body) != 1
            or not isinstance(fd.body[0], Return)
            or not isinstance(fn.function_type.return_type, ir.PointerType)):
        return False
    formal = fd.args[0]
    params = fn.function_type.args
    return (
        formal.kind in ("pos", "pos_only") and not formal.has_default
        and _exact_int_repr(formal.annotation)
        and len(params) == 1 and isinstance(params[0], ir.PointerType)
        and host._funcdef_uses_boxed_int_abi(fd, c_abi_sym=host._func_c_abi_export_symbol(fd))
    )


def boxed_int_direct_forward_has_owner(host, fd, value) -> bool:
    """A single plain direct call that forwards a proven leaf's one owner."""
    if (not isinstance(value, Call) or not isinstance(value.func, Name)
            or value.kwargs or len(value.args) != 1 or not isinstance(value.args[0], Name)
            or not _exact_int_repr(value.ty)
            or host._module_has_c_abi_export or host._module_uses_raw_int_scaffold
            or host._suppress_borrowed_return_retain
            or not _direct_unary_boxed_int_def(host, fd, host.functions.get(fd.name))):
        return False
    name = value.func.ident
    original = fd.args[0]
    if value.args[0].ident != original.name or value.args[0].ty != original.annotation:
        return False
    # These bindings can bypass ordinary declared-function lowering. The
    # wrapper has no locals besides its formal and no hoisted captures.
    if (name == original.name or name == fd.name or name.startswith("_")
            or name in ("cast", "cls", "dir", "open", "range", "xrange",
                        "enumerate", "zip", "reversed", "slice", "frozenset",
                        "globals", "locals", "vars", "type", "super")
            or builtin_function_value_name_exists(name) or name in BUILTIN_EXC_TAG
            or name in host._module_globals or name in host._cross_module_func_defs
            or name in host._vthread_may_park_func_names
            or name in getattr(host, "_extern_decls", {})
            or host._native_builtin_value_for_name(name) is not None
            or host._unsafe_intrinsic_for_name(name) is not None
            or host._resolve_class_alias(name) in host.class_lowering.classes
            or live_import_global_slot(host, name) is not None
            or host._expr_looks_cpython(value.func)
            or getattr(host, "_native_extension_star_module_env", {})
            or getattr(host, "_cpy_star_module_env", {})):
        return False
    target = host.functions.get(name)
    if not isinstance(target, ir.Function) or target.name != host._user_symbol(name):
        return False
    leaf = host._native_symbol_funcdefs.get(target.name)
    if not _direct_unary_boxed_int_def(host, leaf, target):
        return False
    result = leaf.body[0].value
    formal = leaf.args[0]
    if isinstance(result, Name):
        # The normal return lowering retains a borrowed parameter once.
        return result.ident == formal.name and result.ty == formal.annotation
    if isinstance(result, BinOp):
        # The same rooted addition producer used by ordinary boxed returns.
        return (
            result.op == "+" and _exact_int_repr(result.ty)
            and isinstance(result.lhs, Name) and result.lhs.ident == formal.name
            and result.lhs.ty == formal.annotation
            and isinstance(result.rhs, IntLit) and _exact_int_repr(result.rhs.ty)
        )
    return isinstance(result, Call) and boxed_int_literal_parse_has_owner(host, leaf, result)


def boxed_int_mixed_return_uses_owner_slot(fd, expr) -> bool:
    """One conditional return with a boxed addition and its input alias.

    This proof is shared by the producer and the ordinary same-module direct
    caller. The two arms must use the slot protocol, not a pointer phi:
    addition publishes its owner; the name copies its parameter root.
    """
    if (fd.is_async or fd.is_method or fd.decorators or len(fd.body) != 1
            or not isinstance(fd.body[0], Return) or not isinstance(expr, IfExpr)):
        return False
    condition, fresh, alias = expr.cond, expr.then_e, expr.else_e
    if (not isinstance(condition, Name) or not isinstance(condition.ty, BoolType)
            or not isinstance(fresh, BinOp) or fresh.op != "+"
            or not isinstance(fresh.lhs, Name) or not isinstance(fresh.rhs, IntLit)
            or not isinstance(alias, Name) or fresh.lhs.ident != alias.ident):
        return False
    for ty in (expr.ty, fresh.ty, fresh.lhs.ty, fresh.rhs.ty, alias.ty):
        if not isinstance(ty, IntType) or ty.name != "int" or ty.width != 64 or not ty.signed:
            return False
    return (any(arg.name == alias.ident and arg.annotation == alias.ty for arg in fd.args)
            and any(arg.name == condition.ident and arg.annotation == condition.ty for arg in fd.args))


def _boxed_int_return_needs_identity_slot(host, expr) -> bool:
    """Select the owning expression path for int identity returns.

    Exact-int scalar projection may forward its borrowed input for both
    `int(n)` and `+n`. That projection is not an ownership contract.
    Use the existing runtime producers at the return boundary; a conditional
    must publish each selected branch independently, not retain its joined
    pointer and leak on the branch that already produced an owner.
    """
    pending = [expr]
    while pending:
        current = pending.pop()
        if isinstance(current, IfExpr):
            pending.extend((current.then_e, current.else_e))
            continue
        if isinstance(current, Call) and len(current.args) == 1:
            if (not host._call_is_int_builtin(current)
                    or host._native_builtin_value_for_name("int") != "builtins.int"):
                continue
            operand = current.args[0]
        elif (isinstance(current, UnaryOp) and current.op == "+"
              and host._slot_call_unary_runtime(current) == "py_obj_pos"):
            operand = current.operand
        else:
            continue
        if (isinstance(operand.ty, IntType) and operand.ty.name == "int"
                and not host._expr_looks_cpython(operand)
                and not host._expr_returns_unsafe_raw_pointer(operand)):
            return True
    return False


class ReturnLoweringMixin:
    def _shared_return_slot_is_rooted(self, ret_ty: ir.Type) -> bool:
        """Whether a pointer return value may live in a traced root slot."""
        if not isinstance(ret_ty, ir.PointerType):
            return False
        if getattr(self, "_suppress_implicit_gc_roots", False) or self._runtime_port_module:
            return False
        if (self.ast_module.name or "").startswith("pcc.runtime.py."):
            return False
        return not isinstance(self.current_func_def.return_ty, RawPointerType)

    def _shared_return_exit_allowed(self) -> bool:
        if self.current_func_def is None or self.current_function is None:
            return False
        if self._freestanding_module or getattr(self, "_generator_ctx_stack", ()):
            return False
        if getattr(self, "_async_body_depth", 0) > 0:
            return False
        # Runtime libraries drive pcc_gc_frame_* by hand (only they do), so
        # their returns can leave with different active frames; a shared exit
        # would join those states. Ordinary functions exit with the entry's.
        return not (self._python_library or self._runtime_port_module)

    def _shared_return_exit(self, rooted: bool):
        """This function's shared return exit: ``(leave block, value slot)``.

        A return releases its own locals, stores its value in the slot and
        branches here; the exit leaves every registered root frame once and
        returns the slot's value. Repeating the leaves at every return cost
        R*S ``pcc_gc_frame_leave`` calls for R returns and S rooted slots.

        Each return keeps the protocol it had on its own: a value that needs
        a cleanup root is rooted by a LIFO frame entered at that return, so a
        function pays nothing a return did not already pay. Those returns and
        the unrooted ones join separate exits, whose predecessors therefore
        all have the same active frames.
        """
        fn = self.current_function
        state = self._shared_return_exits.get(fn.name)
        if state is None or state[0] is not fn:
            state = [fn, None, None, {}]
            self._shared_return_exits[fn.name] = state
        index = 2 if rooted else 1
        if state[index] is not None:
            return state[index]
        ret_ty = fn.function_type.return_type
        slot = None
        if not isinstance(ret_ty, ir.VoidType):
            slot = self._alloca_in_entry(ret_ty, name=self._fresh("ret.shared"), init_null=rooted)
            if rooted:
                # The shared tail is built before its incoming returns emit
                # their enters. Establish this slot's exact LIFO ABI now so
                # its early leave selects the same local/mapped entry. This
                # only selects the protocol; each rooted return still emits
                # its own enter, and unrooted/error exits never own this slot.
                self._gc_frame_entry(
                    "pcc_gc_frame_enter_lifo", slot, self._gc_one_slot_frame_map(),
                )
        leave = fn.append_basic_block(name=self._fresh("ret.shared.leave"))
        tail = fn.append_basic_block(name=self._fresh("ret.shared.tail"))
        saved_block = self.builder._block
        saved_pos = self.builder._pos
        self.builder.position_at_end(leave)
        from pcc.frontends.python.codegen.frame_root_groups import frame_root_group

        # Several independently owned cells may share one physical entry.
        # Keep each identity key's owner alive until this exit is complete.
        emitted: dict = {}
        for record in reversed(self._fn_gc_root_slot_registry.get(fn.name, ())):
            group = frame_root_group(self, record[1])
            base = record[1] if group is None else group["base"]
            slot_id = id(base)
            if slot_id in emitted:
                continue
            emitted[slot_id] = base
            self._emit_gc_frame_leave_for_slot(base)
        self.builder.branch(tail)
        # Slots registered later retro-patch their leave into this block.
        self._fn_gc_root_exit_sites.setdefault(fn.name, []).append(leave)
        self.builder.position_at_end(tail)
        if slot is None:
            self.builder.ret_void()
        elif rooted:
            # The value stayed traced while the frames were left; reread it
            # through the slot for relocating collectors.
            slot_ptr = self._as_gc_ptr(slot, name=self._fresh("ret.shared.ptr"))
            value = self.builder.call(
                self.runtime["pcc_gc_load_ptr"],
                [ir.Constant(ret_ty, None), slot_ptr],
                name=self._fresh("ret.shared.value"),
            )
            # The slot roots the value; it never owned a reference.
            self.builder.call(self.runtime["pcc_gc_store_return_root"], [slot_ptr, ir.Constant(ret_ty, None)])
            self._emit_gc_frame_leave_lifo_for_slot(slot)
            self.builder.ret(value)
        else:
            self.builder.ret(self.builder.load(slot, name=self._fresh("ret.shared.value")))
        if saved_pos == self.builder._END:
            self.builder.position_at_end(saved_block)
        else:
            self.builder._block = saved_block
            self.builder._pos = saved_pos
        state[index] = (leave, slot)
        return state[index]

    def _shared_return_pinned_cleanup(self, slots, target):
        """Share exact pinned-root cleanup suffixes before a shared return.

        ``slots`` is this return's already selected execution order. These
        persistent temporary roots are entry allocas; their loads stay in the
        caller, after its owned-local releases and before its frame leaves.
        A later return may prepend nodes, but never adds cleanup to an earlier
        return. Rooted and unrooted exits have different targets and chains.
        """
        cache = self._shared_return_exits[self.current_function.name][3]
        saved_block = self.builder._block
        saved_pos = self.builder._pos
        head = target
        try:
            for slot in reversed(slots):
                key = (id(slot), id(head))
                prior = cache.get(key)
                if prior is not None:
                    head = prior[2]
                    continue
                cleanup = self.current_function.append_basic_block(
                    name=self._fresh("ret.shared.unpin"),
                )
                self.builder.position_at_end(cleanup)
                if id(slot) in self._fresh_container_root_slots:
                    # A literal's fresh-container root clears as it was set.
                    self.builder.call(
                        self.runtime["pcc_gc_temp_root_leave_fresh"],
                        [self._as_gc_ptr(slot, name=self._fresh("gc.root.cleanup.ptr"))],
                    )
                else:
                    pinned = self.builder.load(
                        slot, name=self._fresh("gc.root.cleanup.unpin"),
                    )
                    self.builder.call(
                        self.runtime["pcc_gc_unpin"],
                        [self._value_available_at_insertion_point(pinned)],
                    )
                    self.builder.call(
                        self.runtime["pcc_gc_store_root"],
                        [self._as_gc_ptr(slot, name=self._fresh("gc.root.cleanup.ptr")),
                         ir.Constant(_CSTR, None)],
                    )
                self.builder.branch(head)
                # Keep both identity-key owners alive; publish only a complete
                # node. IR slots/blocks have no structural equality contract.
                cache[key] = (slot, head, cleanup)
                head = cleanup
        finally:
            if saved_pos == self.builder._END:
                self.builder.position_at_end(saved_block)
            else:
                self.builder._block = saved_block
                self.builder._pos = saved_pos
        return head

    def _return_through_shared_exit(self, value, skip_name=None, rooted: bool = False) -> bool:
        """Store ``value`` (None for void), release this return's locals and
        branch to the shared exit; False leaves the return to the caller.

        ``rooted`` is the return's own cleanup-root decision."""
        if not self._shared_return_exit_allowed():
            return False
        if value is not None and value in self._cpy_values:
            return False
        if rooted and not self._shared_return_slot_is_rooted(self.current_function.function_type.return_type):
            return False
        leave, slot = self._shared_return_exit(rooted)
        if slot is not None:
            if rooted:
                # A root for the value while this activation's locals are
                # released and its frames left; the value's reference stays
                # with its producer or the returned local.
                self.builder.call(
                    self.runtime["pcc_gc_store_return_root"],
                    [self._as_gc_ptr(slot, name=self._fresh("ret.shared.store")), value],
                )
                self._emit_current_gc_frame_enter_lifo(self._gc_one_slot_frame_map(), slot)
            else:
                self.builder.store(value, slot)
        cleanup = self._emit_owned_local_cleanup(
            skip_name=skip_name, shared_exit_leaves=True, shared_pinned_target=leave,
        )
        self.builder.branch(cleanup)
        return True

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

    def _emit_cancel_pending_return_roots(self, loop_exit: bool = False, root_base: int = 0, only_slot=None) -> None:
        slots = []
        if only_slot is not None:
            slots.append(only_slot)
        else:
            for owner, slot, loop_depth in reversed(self._return_cleanup_roots[root_base:]):
                if owner is self.current_function and (
                    not loop_exit or len(self.loop_stack) <= loop_depth
                ):
                    slots.append(slot)
        if not slots:
            return
        # Abandoning a result can run finalizers or weakref callbacks. Keep
        # the pending exception in an independent authoritative owner until
        # every abandoned result has been cleared, including normal exits
        # whose original TLS value is empty.
        exception_slot = self._alloca_in_entry(
            _CSTR, name=self._fresh("return.cancel.exception"), init_null=True,
        )
        self._emit_current_gc_frame_enter_lifo(
            self._gc_one_slot_frame_map(), exception_slot,
        )
        swap_exception = self.module.globals.get("py_tls_exc_swap_slot")
        if swap_exception is None:
            swap_exception = ir.Function(
                self.module, ir.FunctionType(ir.VoidType(), [_CSTR]),
                name="py_tls_exc_swap_slot",
            )
        self.builder.call(swap_exception, [self._as_gc_ptr(exception_slot)])
        for slot in slots:
            self._clear_exception_selection_owner_slot(slot)
        self.builder.call(self.runtime["py_clear_exception"], [])
        self.builder.call(swap_exception, [self._as_gc_ptr(exception_slot)])
        self._emit_gc_frame_leave_lifo_for_slot(exception_slot)

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
        if value in self._cpy_values:
            return False
        if self._value_is_never_gc_object(value):
            # A tagged immediate, None/True/False or a static literal: its
            # count never changes, so the caller's release of the returned
            # owner is a no-op and a retain here would be one too.
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
            and value not in self._cpy_values
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
        if value in self._cpy_values:
            return False
        if self._expr_returns_unsafe_raw_pointer(stmt.value):
            return False
        if self._value_is_never_gc_object(value):
            # Immortal statics and tagged immediates never move or die: the
            # return needs no root while the frames are left.
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

    def _emit_owned_return_through_finally(self, value, stmt: Return, source_slot=None) -> None:
        # Only non-resumable activations use this path. Generator returns use
        # their preplanned managed heap-frame slots in generator_lowering.
        # Alternative returns never coexist at runtime. Reuse one function
        # owner per simultaneously pending return depth, instead of enrolling
        # a fresh permanent local in every earlier/later return's cleanup.
        depth = 0
        for owner, _pending_slot, _loop_depth in self._return_cleanup_roots:
            if owner is self.current_function:
                depth += 1
        hidden = ".pcc.return.cleanup.owner." + self.current_function.name + "." + str(depth)
        cached = self.env.get(hidden)
        if cached is not None:
            slot = cached[0]
            # An earlier lexical branch may never have executed. Every site
            # acquiring this owner must publish its own cleanup flag.
            flag = self._ensure_owned_local_flag(hidden, slot)
            self.builder.store(ir.Constant(ir.IntType(1), 1), flag)
        if source_slot is None:
            if cached is None:
                slot = self._exception_selection_owner_slot(value, "return.cleanup", hidden)
            else:
                self.builder.call(
                    self.runtime["pcc_gc_store_root"], [self._as_gc_ptr(slot), value],
                )
            owned = self.builder.call(
                self.runtime["pcc_gc_load_ptr"],
                [ir.Constant(value.type, None), self._as_gc_ptr(slot)],
                name=self._fresh("return.cleanup.transfer"),
            )
            self._gc_release(owned)
        else:
            # The producer already published its NEW result before any poll.
            # Register the pending-return owner empty, then transfer between
            # authoritative roots. Never take a raw result before registration.
            if cached is None:
                slot = self._exception_selection_owner_slot(
                    ir.Constant(value.type, None), "return.cleanup", hidden,
                )
            previous = self._current_try_err_block()
            target = previous if previous is not None else self._ensure_fn_err_exit()
            saved_cpy = self._cpy_operand_cleanup_block
            self._try_err_block = self._slot_call_cleanup_block((source_slot,), target)
            self._cpy_operand_cleanup_block = self._try_err_block
            try:
                # This reserved synthetic owner is a fresh alloca in this
                # non-resumable activation, never a user-visible local cell.
                moved = self._emit_root_slot_move(
                    slot,
                    source_slot,
                    label="return.cleanup.move",
                    private_destination=True,
                )
                self._slot_call_check_status(moved, "return result transfer", stmt.span)
                self._release_slot_call_roots((source_slot,))
            finally:
                self._try_err_block = previous
                self._cpy_operand_cleanup_block = saved_cpy
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
            # A tagged integer or NULL result has no header to pin and
            # nothing a collector can move: it needs neither the graph
            # transaction nor a pin. Forwarding never changes those bits, so
            # a plain load of this private slot decides.
            peek = self.builder.load(slot, name=self._fresh("return.cleanup.peek"))
            peek_bits = self.builder.ptrtoint(peek, ir.IntType(64))
            peek_tagged = self.builder.icmp_unsigned(
                "!=", self.builder.and_(peek_bits, ir.Constant(ir.IntType(64), 1)),
                ir.Constant(ir.IntType(64), 0),
            )
            peek_nonnull = self.builder.icmp_unsigned("!=", peek, ir.Constant(peek.type, None))
            peek_managed = self.builder.and_(peek_nonnull, self.builder.not_(peek_tagged))
            header = self.current_function.append_basic_block(name=self._fresh("return.pin.header"))
            ready = self.current_function.append_basic_block(name=self._fresh("return.pin.ready"))
            empty = self.builder._block
            self.builder.cbranch(peek_managed, header, ready)
            self.builder.position_at_end(header)
            pin_locked = None
            if source_slot is not None:
                # Reload, inspect and pin under the graph transaction. The
                # registered owner remains live through the lock's entry poll.
                pin_locked = self._emit_root_pin_lock()
            value = self.builder.call(
                self.runtime["pcc_gc_load_ptr"],
                [ir.Constant(value.type, None), self._as_gc_ptr(slot)],
                name=self._fresh("return.cleanup.current"),
            )
            # Preserve an existing pin lease. The new owned return remains
            # pinned through every local finalizer and root unregistration.
            address = self.builder.gep(value, [ir.Constant(ir.IntType(64), PYOBJECTHEADER_FLAGS_OFFSET)])
            flags = self.builder.load(self.builder.bitcast(address, ir.IntType(32).as_pointer()))
            prior_bits = self.builder.and_(flags, ir.Constant(ir.IntType(32), PY_FLAG_GC_PINNED))
            prior_value = self.builder.zext(prior_bits, ir.IntType(64))
            self._gc_pin(value)
            if pin_locked is not None:
                self._emit_root_pin_unlock(pin_locked)
            previous = self.builder._block
            self.builder.branch(ready)
            self.builder.position_at_end(ready)
            prior = self.builder.phi(ir.IntType(64), name=self._fresh("return.pin.prior"))
            prior.add_incoming(ir.Constant(ir.IntType(64), 0), empty)
            prior.add_incoming(prior_value, previous)
            # Keeps its own exit: the handoff below must follow every frame
            # leave (pcc_gc_take_pinned_slot's no-park contract), which a
            # shared exit would run after it.
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
        self._emit_cancel_pending_return_roots(only_slot=slot)
        self.builder.branch(saved_error or self._ensure_fn_err_exit())
        self.builder.position_at_end(continuation)

    def _emit_return(self, stmt: Return) -> None:
        saved_forward_return = self._vthread_forward_return
        self._vthread_forward_return = stmt.value
        try:
            self._emit_return_value(stmt)
        finally:
            self._vthread_forward_return = saved_forward_return

    def _emit_return_value(self, stmt: Return) -> None:
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
                if not self._return_through_shared_exit(None):
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
            if isinstance(ret_ty, ir.PointerType):
                # A bare Python return is a successful ``None`` result. NULL
                # is reserved for the C-API failure sentinel.
                result = self._emit_none_literal()
            elif isinstance(ret_ty, ir.IntType):
                result = ir.Constant(ret_ty, 0)
            elif isinstance(ret_ty, (ir.FloatType, ir.DoubleType)):
                result = ir.Constant(ret_ty, 0.0)
            else:
                raise L1CodegenError(f"bare 'return' fallback can't zero-init {ret_ty}")
            if not self._return_through_shared_exit(result):
                self._emit_owned_local_cleanup()
                self.builder.ret(result)
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
            if not self._return_through_shared_exit(None):
                self._emit_owned_local_cleanup()
                self._return_log("value void ret")
                self.builder.ret_void()
            self._return_log("value void end")
            return
        if (
            isinstance(ret_ty, ir.PointerType)
            and not isinstance(self.current_func_def.return_ty, RawPointerType)
            and not getattr(self, "_generator_ctx_stack", ())
            and isinstance(stmt.value, BinOp)
            and self._slot_call_binary_runtime(stmt.value, object_boundary=True) is not None
        ):
            # A pointer-return ABI is an owning object consumer. Ordinary
            # numeric annotations do not authorize a scalar/exact-int shortcut
            # before the actual runtime protocol has selected the result kind.
            source_slot = self._emit_slot_call_operand(stmt.value, "return.binary")
            self._emit_owned_return_through_finally(
                ir.Constant(ret_ty, None), stmt, source_slot,
            )
            return
        if isinstance(ret_ty, ir.PointerType) and isinstance(
            self.current_func_def.return_ty, IntType
        ):
            fd = self.current_func_def
            mixed = boxed_int_mixed_return_uses_owner_slot(fd, stmt.value)
            forwarded = boxed_int_direct_forward_has_owner(self, fd, stmt.value)
            if (
                fd.return_ty.name == "int"
                and not self._suppress_borrowed_return_retain
                and not getattr(self, "_generator_ctx_stack", ())
                and fn.name not in self._manual_pointer_abi_functions
                and self._funcdef_uses_boxed_int_abi(fd, c_abi_sym=self._func_c_abi_export_symbol(fd))
                and (mixed or forwarded or _boxed_int_return_needs_identity_slot(self, stmt.value))
            ):
                label = "return.int.mixed" if mixed else "return.int.identity"
                if forwarded:
                    label = "return.int.forwarded"
                source_slot = self._emit_slot_call_operand(stmt.value, label)
                self._emit_owned_return_through_finally(
                    ir.Constant(ret_ty, None), stmt, source_slot,
                )
                return
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
            if self._return_through_shared_exit(
                    value, skip_name,
                    rooted=self._return_value_needs_cleanup_root(value, stmt, force_object=True)):
                self._return_log("exact int shared exit")
                return
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
        valueclass_payload = self._emit_valueclass_payload_expr(stmt.value, valueclass_target_ty)
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
                and exact_object not in self._cpy_values
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
        if value in self._cpy_values:
            # A NULL from the libpython bridge must take the error edge while
            # the GC frames are still entered: the cleanup below leaves them,
            # and err.exit leaves them again (the precise stack map rejected
            # the join, and the frames were left twice).
            self._guard_cpy_value_not_null(value)
        if isinstance(value.type, ir.LiteralStructType) and self._is_valueclass_payload_type(self.current_func_def.return_ty):
            self._emit_valueclass_return(value, self.current_func_def.return_ty)
            return
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
        if value.type == ret_ty and self._return_through_shared_exit(
                value, skip_name,
                rooted=(isinstance(value.type, ir.PointerType)
                        and self._return_value_needs_cleanup_root(value, stmt))):
            self._return_log("value shared exit")
            return
        ret_root_slot, ret_root_ptr = self._enter_return_cleanup_root(value, stmt)
        self._return_log("value cleanup")
        self._emit_owned_local_cleanup(skip_name=skip_name)
        value = self._leave_return_cleanup_root(value, ret_root_slot, ret_root_ptr)
        if value in self._cpy_values:
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
