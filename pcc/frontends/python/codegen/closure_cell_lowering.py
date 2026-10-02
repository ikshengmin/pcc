"""Compiler-owned access to a closure's ordinary list-backed storage.

An internal NULL payload is unbound. Python None is the distinct py_None
object. The normal list getter/setter retain the shared GC slot/barrier and
publication-before-finalization contracts; no physical list layout is exposed
here. Only impossible source identifiers emitted by hoist_boxing reach this
lowering, so user callables cannot accidentally select it.
"""
from pcc.ir.compat import ir
from pcc.frontends.python.py_ast import BoolLit, Call, Name, StrLit
from pcc.frontends.python.codegen.hoist_boxing import CELL_CAPTURE, CELL_READ, CELL_UNBOUND, cell_capture_key


def emit_closure_cell_expr(host, expr):
    if not isinstance(expr, Call) or not isinstance(expr.func, Name):
        return None
    if expr.func.ident == CELL_CAPTURE:
        if len(expr.args) != 2 or expr.kwargs or not isinstance(expr.args[1], StrLit):
            raise ValueError("malformed compiler class-cell capture")
        receiver = host._emit_expr(expr.args[0])
        key = cell_capture_key(expr.args[1].value)
        cell = host.builder.call(
            host.runtime["py_instance_getattr_default"],
            [receiver, host._attr_name_ptr(key)],
            name=host._fresh("class.cell.capture"),
        )
        host._emit_attribute_error_if_null(cell, key, expr.span)
        host._note_owned_object_value(cell)
        return cell
    if expr.func.ident == CELL_UNBOUND and not expr.args and not expr.kwargs:
        return ir.Constant(ir.IntType(8).as_pointer(), None)
    if expr.func.ident != CELL_READ:
        return None
    if (len(expr.args) != 3 or expr.kwargs
            or not isinstance(expr.args[1], StrLit)
            or not isinstance(expr.args[2], BoolLit)):
        raise ValueError("malformed compiler closure-cell read")
    cell = host._emit_expr(expr.args[0])
    cell_owned = host._owned_release_needed(cell, expr.args[0])
    value = host.builder.call(
        host.runtime["py_list_getitem"], [cell, ir.Constant(ir.IntType(64), 0)],
        name=host._fresh("cell.value"),
    )
    # A malformed zero-length cell is still a list indexing error. Only a
    # successfully loaded NULL payload denotes an unbound lexical variable.
    host._emit_post_call_err_check(expr.span, release_on_error=(cell,) if cell_owned else ())
    if cell_owned:
        # Captures loaded from an instance own their list reference. Keep the
        # new payload stable if dropping that temporary can park a collector.
        # take_root consumes the original payload owner while its retained
        # root is live; releasing it here as well would drop that owner twice.
        keeper = host._extern_enter_root(value, True, "cell.payload")
        host._gc_release(cell)
        value = host._extern_take_root(keeper)
    bound = host.builder.icmp_unsigned(
        "!=", value, ir.Constant(value.type, None),
        name=host._fresh("cell.bound"),
    )
    ready = host.current_function.append_basic_block(name=host._fresh("cell.bound.ok"))
    missing = host.current_function.append_basic_block(name=host._fresh("cell.bound.error"))
    host.builder.cbranch(bound, ready, missing)
    host.builder.position_at_end(missing)
    name = expr.args[1].value
    if expr.args[2].value:
        exc = "NameError"
        message = "cannot access free variable '" + name + "' where it is not associated with a value in enclosing scope"
    else:
        exc = "UnboundLocalError"
        message = "cannot access local variable '" + name + "' where it is not associated with a value"
    host._emit_builtin_exception_and_branch(exc, message, expr.span)
    host.builder.position_at_end(ready)
    host._note_owned_object_value(value)
    return value
