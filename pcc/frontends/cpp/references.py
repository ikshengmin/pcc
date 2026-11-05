"""Direct lvalue-reference binding, N4659 [dcl.ref], [dcl.init.ref].

Reference identity survives overload selection and declaration matching. Only
projection to C uses an address parameter; source names denote the referent.
Selected constructor parameters and local aliases may borrow class lvalues.
Ordinary reference results borrow existing scalar/pointer/class lvalues.
Temporary materialization, class-value calls and implicit class copies remain
gaps; class-reference results in template patterns/replay remain separate.
"""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import (
    ConstType, ReferenceType, array_type, function_type, unqualified,
)


def lvalue_reference(referent, coord):
    """Construct reference identity, retaining unsupported collapsing provenance."""
    if isinstance(referent, ReferenceType):
        raise CppDiagnostic('cpp.unsupported.references', 'reference collapsing requires declaration provenance', coord)
    if unqualified(referent) == 'void':
        raise CppDiagnostic('cpp.invalid-reference-type', 'a reference cannot refer to void', coord)
    if array_type(referent) or function_type(referent):
        raise CppDiagnostic('cpp.unsupported.references', 'array/function reference binding is not implemented', coord)
    return ReferenceType(referent)


def ordinary_class_result_context(parser):
    return not (parser.parsing_template or parser.template_function is not None
                or parser.instantiating_template
                or getattr(parser, 'class_template_method', None) is not None)


def check_result(dtype, coord, *, allow_class=False):
    """No temporary, class-value, callback or array-result ABI is implied."""
    from pcc.frontends.cpp.classes import is_class_type
    from pcc.frontends.cpp.types import contains_array, contains_function
    if not isinstance(dtype, ReferenceType):
        return
    if ((is_class_type(dtype.referent) and not allow_class)
            or contains_array(dtype.referent) or contains_function(dtype.referent)):
        raise CppDiagnostic('cpp.unsupported.references', 'reference results require scalar or object-pointer referents', coord)


def call_result(node, dtype, coord, *, allow_class=False):
    """A reference call returns an address in C and a referent lvalue in C++."""
    from pcc.frontends.cpp.constants import not_constant
    from pcc.frontends.cpp.classes import is_class_type
    from pcc.frontends.cpp.parser import Expression
    if isinstance(dtype, ReferenceType):
        check_result(dtype, coord, allow_class=allow_class)
        no_fetch = is_class_type(dtype.referent) or unqualified(dtype.referent) == 'nullptr'
        return Expression(c_ast.UnaryOp('*', node, coord), dtype.referent, True,
                          discarded_known=no_fetch, discarded_node=node if no_fetch else None,
                          constant_fact=not_constant(unqualified(dtype.referent), 'reference call', coord))
    dtype = unqualified(dtype)
    return Expression(node, dtype, constant_fact=not_constant(dtype, 'call to a non-constexpr function', coord))


def directly_compatible(source, destination, lvalue):
    referent = destination.referent
    return (lvalue and unqualified(source) == unqualified(referent)
            and (not isinstance(source, ConstType) or isinstance(referent, ConstType)))


def conversion_rank(source, destination, lvalue, types, null_pointer=False):
    if directly_compatible(source, destination, lvalue):
        return 0
    if isinstance(destination.referent, ConstType):
        # Keep otherwise-valid temporary bindings in overload resolution. If
        # selected, bind() rejects their missing lifetime implementation; do
        # not silently select a worse by-value overload by dropping them here.
        return types.conversion_rank(source, destination.referent, null_pointer)
    return None


def compare(source, first, second, types, null_pointer=False):
    first_ref, second_ref = isinstance(first, ReferenceType), isinstance(second, ReferenceType)
    if first_ref and second_ref:
        a, b = first.referent, second.referent
        if unqualified(a) == unqualified(b):
            return int(isinstance(a, ConstType)) - int(isinstance(b, ConstType))
    # A reference and a by-value exact match do not gain a fictitious ordering.
    return types.compare_conversions(source,
        first.referent if first_ref else first,
        second.referent if second_ref else second, null_pointer)


def bind(parser, expression, destination, coord, *, allow_class=False):
    from pcc.frontends.cpp.classes import is_class_type
    if expression.assignment_result:
        raise CppDiagnostic('cpp.unsupported.references',
                            'binding assignment-result lvalues requires address-preserving lowering', coord)
    if is_class_type(destination.referent) and not allow_class:
        raise CppDiagnostic('cpp.unsupported.class-reference-values',
                            'class-reference declarations do not implement class value/copy calls', coord)
    if not directly_compatible(expression.dtype, destination, expression.lvalue):
        if isinstance(destination.referent, ConstType) and parser.types.conversion_rank(
                expression.dtype, destination.referent, expression.null_pointer) is not None:
            raise CppDiagnostic('cpp.unsupported.reference-temporaries',
                                'reference binding that materializes a temporary requires lifetime support', coord)
        raise CppDiagnostic('cpp.invalid-reference-binding',
                            'lvalue reference requires a compatible lvalue without discarding const', coord)
    # Taking this address evaluates an effectful subscript/dereference once;
    # it never fetches the referent or manufactures a new source pointer value.
    node = expression.node
    return node.expr if isinstance(node, c_ast.UnaryOp) and node.op == '*' else c_ast.UnaryOp('&', node, coord)


def declare_local(parser, name, dtype, *, for_init=False):
    """Bind an automatic reference with '=' without owning the referent.

    The binding captures its address once. There is no temporary, object copy
    or new destructor obligation; the existing object's lifetime owner remains
    responsible for cleanup. Other initialization forms keep their old gate.
    """
    from pcc.frontends.cpp.sema import Symbol
    from pcc.frontends.cpp.types import contains_function

    if parser.parsing_template or parser.template_function is not None or parser.instantiating_template:
        raise CppDiagnostic('cpp.unsupported.references',
                            'local reference template replay requires binding patterns', name.coord)
    if contains_function(dtype.referent):
        raise CppDiagnostic('cpp.unsupported.function-pointers',
                            'callback reference storage requires its own value rules', name.coord)
    # [basic.scope.pdecl]: the reference hides any outer spelling during its
    # initializer. In particular, do not silently bind a self-reference to it.
    symbol = parser.scope.declare(Symbol(
        name.value, 'value', name.coord, dtype=dtype,
        c_name=parser._local_projection_name(name.value),
    ))
    if not parser._accept('='):
        if parser._peek().value == ';':
            raise CppDiagnostic('cpp.reference-initializer-required',
                                'a local reference requires an initializer', name.coord)
        raise CppDiagnostic('cpp.unsupported.references',
                            'local references currently require an equals initializer', name.coord)
    expression = parser._expression()
    parser._require_value(expression, allow_class=True, object_address=True)
    initializer = bind(parser, expression, dtype, name.coord, allow_class=True)
    if for_init and parser._peek().value == ',':
        raise CppDiagnostic('cpp.unsupported.for-declaration-list',
                            'multiple init-declarators are not implemented', parser._peek().coord)
    parser._expect(';')
    symbol.initialization_complete = True
    symbol.jump_entry = 'initialized'
    return parser._decl(symbol.c_name, dtype, name.coord, initializer)
