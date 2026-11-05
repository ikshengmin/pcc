"""Typed C++ varargs over the existing owned C/SysV intrinsic owner.

N4659 [cstdarg.syn], [expr.call]; Itanium ellipsis z.
https://timsong-cpp.github.io/cppwp/n4659/cstdarg.syn
https://timsong-cpp.github.io/cppwp/n4659/expr.call#9
The private C AST va_arg shape is an explicit backend protocol, not source
rewriting or a declaration of an external C helper. Other ABI owners and
nontrivial argument/array lifetime semantics remain explicit capabilities.
"""
from pcc.frontends.c.ast import c_ast
from pcc.backend.self_backend_target_match import is_x86_64_linux_triple
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import (
    ConstType, PointerType, array_type, contains_function, is_enum_type,
    pointer_type, unqualified,
)

BUILTINS = frozenset(('__builtin_va_start', '__builtin_va_arg',
                      '__builtin_va_end', '__builtin_va_copy'))


def require_sysv_target(parser, coord):
    if not is_x86_64_linux_triple(parser.types.target_triple):
        raise CppDiagnostic('cpp.unsupported.varargs-target',
                            'owned C++ va_list execution currently requires SysV x86_64 Linux', coord)


def convert_ellipsis_arguments(parser, arguments, coord):
    """One default-promotion owner for ordinary free and member ellipses.

    The call owner checks fixed formals and rejects class-value tails before
    reaching this conversion. Object parameters never enter the ellipsis.
    """
    converted = []
    for argument in arguments:
        source = unqualified(argument.dtype)
        if is_enum_type(source) and source.scoped:
            raise CppDiagnostic("cpp.unsupported.variadic-argument",
                                "scoped-enum variadic argument ABI is not implemented", coord)
        if not parser.types.value_lowering_supported(source):
            raise CppDiagnostic("cpp.unsupported.variadic-argument",
                                "extended floating variadic argument ABI is not implemented", coord)
        destination = (PointerType("void") if source == "nullptr" else
                       "double" if source == "float" else
                       parser.types.promote(source) if parser.types.integral_operand(source) else source)
        converted.append(parser._convert(argument, destination, coord))
    return converted


def _cursor(parser, expression, coord):
    # __pcc_sysv_va_list is the reserved implementation record defined by the
    # owned ABI header, not an arbitrary user's structurally similar record.
    record = parser.scope.global_scope().names.get('__pcc_sysv_va_list')
    if record is None or record.kind != 'type' or not getattr(record.dtype, 'complete', False):
        raise CppDiagnostic('cpp.invalid-va-list', 'a complete owned va_list ABI declaration is required', coord)
    owner = record.dtype
    fields = tuple((member.name, member.dtype) for member in owner.definition.members)
    expected = (('gp_offset', 'unsigned int'), ('fp_offset', 'unsigned int'),
                ('overflow_arg_area', PointerType('void')), ('reg_save_area', PointerType('void')))
    if fields != expected or owner.definition.constructors.overloads or owner.definition.destructor is not None:
        raise CppDiagnostic('cpp.invalid-va-list-abi', 'owned va_list record does not match its target ABI', coord)
    dtype = unqualified(expression.dtype)
    if array_type(dtype):
        if dtype.bound != 1 or unqualified(dtype.element) is not owner:
            raise CppDiagnostic('cpp.invalid-va-list', 'va_list requires its exact one-record array type', coord)
        expression = parser._decay_array(expression)
        dtype = unqualified(expression.dtype)
    if not pointer_type(dtype) or unqualified(dtype.pointee) is not owner:
        raise CppDiagnostic('cpp.invalid-va-list', 'operand does not denote the owned va_list cursor', coord)
    if isinstance(dtype.pointee, ConstType):
        raise CppDiagnostic('cpp.unsupported.const-va-list', 'const va_list builtin access is not implemented', coord)
    parser._require_value(expression)
    # Both local record[1] and adjusted parameter record* reach C as record*.
    # Uniform &ap or void* erasure would break C's cursor/va_copy storage owner.
    return expression.node


def _last_parameter(parser, expression, coord):
    function = parser.current_function
    parameters = getattr(function, 'parameter_symbols', ())
    if function is None or not function.variadic or not parameters or parameters[-1] is None:
        raise CppDiagnostic('cpp.invalid-va-start', 'va_start requires a variadic definition with a named final parameter', coord)
    last = parameters[-1]
    if expression.symbol is not last or not expression.lvalue or expression.assignment_result:
        raise CppDiagnostic('cpp.invalid-va-start', 'va_start second operand must name the final fixed parameter', coord)
    # C++ [cstdarg.syn] uses the parameter's adjusted type, not the C-only
    # prohibition on array/function declaration spelling. An array parameter
    # adjusted to an object pointer retains that type under default promotions.
    plain = unqualified(last.dtype)
    if (is_enum_type(plain) and not plain.scoped or plain in ('float', 'nullptr')
            or parser.types.integral(plain) and parser.types.promote(plain) != plain):
        raise CppDiagnostic('cpp.invalid-va-start', 'va_start final parameter must retain its default-promoted type', coord)


def parse_builtin(parser, token):
    from pcc.frontends.cpp.parser import Expression
    require_sysv_target(parser, token.coord)
    if parser.parsing_template or parser.instantiating_template:
        raise CppDiagnostic('cpp.unsupported.varargs-templates', 'dependent va_list builtin typing is not implemented', token.coord)
    parser._expect('(')
    first = parser._expression()
    cursor = _cursor(parser, first, token.coord)
    arguments = [cursor]
    dtype = 'void'
    if token.value == '__builtin_va_arg':
        parser._expect(',')
        dtype = unqualified(parser._abstract_pointer_type(parser._type()))
        if dtype == 'float':
            raise CppDiagnostic('cpp.invalid-va-arg-type', 'va_arg float is changed by the default argument promotions', token.coord)
        if (contains_function(dtype) or is_enum_type(dtype)
                or not (parser.types.integral(dtype) or dtype == 'double' or pointer_type(dtype))):
            raise CppDiagnostic('cpp.unsupported.va-arg-type', 'va_arg requires an implemented promoted scalar or object pointer', token.coord)
        if parser.types.integral(dtype) and parser.types.promote(dtype) != dtype:
            raise CppDiagnostic('cpp.invalid-va-arg-type', 'va_arg type is changed by the default argument promotions', token.coord)
    elif token.value == '__builtin_va_start':
        parser._expect(',')
        last = parser._expression()
        _last_parameter(parser, last, token.coord)
        # The validated parmN identity affects the function ABI, not evaluation
        # of a fabricated extra operand to C's one-argument va_start intrinsic.
    elif token.value == '__builtin_va_copy':
        parser._expect(',')
        arguments.append(_cursor(parser, parser._expression(), token.coord))
    parser._expect(')')
    call = c_ast.FuncCall(c_ast.ID(token.value, token.coord), c_ast.ExprList(arguments, token.coord), token.coord)
    if token.value == '__builtin_va_arg':
        target = c_ast.Typename(None, [], parser._scalar_type(None, PointerType(dtype), token.coord), token.coord)
        # This exact typed dereference is consumed by existing C codegen's
        # va_arg owner. A generic untyped __builtin_va_arg call is not a value.
        call = c_ast.UnaryOp('*', c_ast.Cast(target, call, token.coord), token.coord)
        # Preserve the requested signedness at the C value boundary as well
        # as in C++ Sema, including an unsigned va_arg used without a store.
        value_type = c_ast.Typename(None, [], parser._scalar_type(None, dtype, token.coord), token.coord)
        call = c_ast.Cast(value_type, call, token.coord)
    return Expression(call, dtype)
