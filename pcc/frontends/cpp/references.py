"""Direct lvalue-reference parameters, N4659 [dcl.ref], [dcl.init.ref].

Reference identity survives overload selection and declaration matching. Only
projection to C uses an address parameter; source names denote the referent.
Selected constructor parameters may also borrow compatible class lvalues;
temporary materialization, reference results and implicit class copies remain gaps.
"""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import ConstType, ReferenceType, unqualified


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
