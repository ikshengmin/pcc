"""Compile-time nonmoving objects at borrowed native argument boundaries."""
from pcc.frontends.python.py_ast import BoolLit, IntLit, IntType, NoneLit, StrLit
from pcc.frontends.python.codegen.errors import L1CodegenError
from pcc.frontends.python.codegen.runtime_abi import declare_runtime_global


def emit_nonmoving_literal_operand(host, expression):
    """Return a proven permanent object, or decline without emitting code.

    This is not a type-annotation inference. Only these exact producers make
    tagged values or immortal image storage. Bytes, floats, calls, names and
    arbitrary Dyn values retain their ordinary owned-root/lease path.
    The consumer must borrow the value; an owning output root is unchanged.
    """
    if not host._strict_no_libpython:
        return None
    if isinstance(expression, StrLit):
        value = host._emit_str_literal(expression.value)
    elif isinstance(expression, NoneLit):
        value = host._emit_none_literal()
    elif isinstance(expression, BoolLit):
        symbol = "py_True" if expression.value else "py_False"
        value = host.builder.load(declare_runtime_global(host.module, symbol),
                                  name=host._fresh("static.argument.bool"))
        host._note_never_gc_object(value)
    elif isinstance(expression, IntLit) and (
        isinstance(expression.ty, IntType) and expression.ty.name == "int"
    ):
        value = host._emit_int_literal_object(expression.value)
    else:
        return None
    if not host._value_is_never_gc_object(value):
        raise L1CodegenError("literal producer no longer proves nonmoving storage")
    result = host._as_gc_ptr(value)
    host._note_never_gc_object(result)
    return result
