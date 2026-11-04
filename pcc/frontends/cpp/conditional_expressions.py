"""Typed builtin conditional/comma expressions, N4659 [expr.cond]/[expr.comma].

Sema checks both arms; the ordinary C control-flow projection evaluates only
the selected arm. Lvalue results select addresses, never copies of objects.
Class conversions, temporary materialization, bit-fields and throw expressions
keep their existing explicit capability boundaries. Constant facts are separate
from runtime AST and do not delete an operand before its static checks.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.classes import is_class_type
from pcc.frontends.cpp.constants import (
    KNOWN, conditional_fact, converted_fact, known,
)
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.templates import Pattern, dependent
from pcc.frontends.cpp.types import (
    PointerType, ReferenceType, const_qualified, contains_array,
    pointer_type, qualify_const, unqualified,
)


def _cast(parser, node, dtype, coord):
    target = c_ast.Typename(None, [], parser._scalar_type(None, dtype, coord), coord)
    return c_ast.Cast(target, node, coord)


def _address(expression, coord):
    node = expression.node
    return node.expr if isinstance(node, c_ast.UnaryOp) and node.op == '*' else c_ast.UnaryOp('&', node, coord)


def _typed_pointer(parser, node, dtype, coord):
    node = _cast(parser, node, dtype, coord)
    if contains_array(dtype):
        # The generic C cast projection erases an array pointee. The existing
        # declaration owner retains that shape, also for multi-level pointers.
        name = parser._fresh_projection_name('__pcc_cpp_conditional_pointer_')
        declaration = parser._decl(name, dtype, coord, node)
        node = c_ast.StmtExpr(c_ast.Compound([declaration, c_ast.ID(name, coord)], coord), coord)
    return node


def _lvalue_type(first, second):
    if not (first.lvalue or first.assignment_result) or not (second.lvalue or second.assignment_result):
        return None
    a, b = unqualified(first.dtype), unqualified(second.dtype)
    if a != b:
        return None
    return qualify_const(a) if const_qualified(first.dtype) or const_qualified(second.dtype) else a


def _discarded_fact(parser, expression, coord):
    # Discarding a nonvolatile named object does not read its stored value.
    # A reference binding itself may need evaluation; do not mint that proof.
    if (expression.lvalue and expression.symbol is not None
            and expression.symbol.kind == 'value'
            and not isinstance(expression.symbol.dtype, ReferenceType)):
        return known('int', 0, coord)
    if expression.discarded_known and expression.discarded_node is None:
        return known('int', 0, coord)
    if expression.discarded_fact is not None:
        return expression.discarded_fact
    return parser._constant_fact(expression)


def _discarded_node(parser, expression, coord):
    if expression.discarded_known:
        return (expression.discarded_node if expression.discarded_node is not None
                else c_ast.Constant('int', '0', coord))
    if expression.lvalue:
        # All admitted cv types are nonvolatile. [expr]/12 does not apply
        # lvalue-to-rvalue conversion here, including to an uninitialized int.
        return _cast(parser, _address(expression, coord), 'void', coord)
    return parser._discarded_value_node(expression)


def _sequence_fact(parser, first, second, dtype, coord):
    first = _discarded_fact(parser, first, coord)
    if first.status != KNOWN:
        # Reuse the fact propagation rules without inventing a runtime fold.
        return conditional_fact(first, second, second, dtype, parser.types, coord)
    return converted_fact(second, dtype, parser.types, coord)


def conditional_expression(parser, condition, consequent, alternative, coord):
    from pcc.frontends.cpp.parser import Expression

    condition_node = parser._convert(condition, 'bool', coord, contextual=True)
    parser._require_value(consequent, allow_void=True, allow_class=True)
    parser._require_value(alternative, allow_void=True, allow_class=True)
    operands = [condition, consequent, alternative]
    if dependent(consequent.dtype) or dependent(alternative.dtype):
        return Expression(Pattern('conditional', coord, children=operands), None,
                          consequent.lvalue and alternative.lvalue)

    first, second = unqualified(consequent.dtype), unqualified(alternative.dtype)
    lvalue_dtype = _lvalue_type(consequent, alternative)
    if first == 'void' or second == 'void':
        if first != second:
            raise CppDiagnostic('cpp.invalid-operands', 'conditional void operands must both have type void', coord)
        dtype = 'void'
        lvalue_dtype = None
    elif lvalue_dtype is not None:
        if consequent.assignment_result or alternative.assignment_result:
            raise CppDiagnostic('cpp.unsupported.references',
                                'conditional assignment-result lvalues require address-preserving lowering', coord)
        dtype = lvalue_dtype
    else:
        if is_class_type(first) or is_class_type(second):
            raise CppDiagnostic('cpp.unsupported.class-conversions',
                                'conditional class conversions/materialization are not implemented', coord)
        consequent, alternative = parser._decay_array(consequent), parser._decay_array(alternative)
        first, second = unqualified(consequent.dtype), unqualified(alternative.dtype)
        if first == second:
            dtype = first
        elif (first == 'nullptr' and alternative.null_pointer
              or second == 'nullptr' and consequent.null_pointer):
            dtype = 'nullptr'
        elif pointer_type(first) or pointer_type(second):
            dtype = parser._composite_pointer_type(consequent, alternative, coord)
        elif parser.types.arithmetic_operand(first) and parser.types.arithmetic_operand(second):
            dtype = parser.types.common(first, second)
        else:
            raise CppDiagnostic('cpp.invalid-operands', 'incompatible conditional operands', coord)

    fact = conditional_fact(parser._constant_fact(condition), parser._constant_fact(consequent),
                            parser._constant_fact(alternative), dtype, parser.types, coord)
    if parser.template_function is not None:
        return Expression(Pattern('conditional', coord, children=operands), dtype,
                          lvalue_dtype is not None, constant_fact=fact)

    if lvalue_dtype is not None:
        pointer = PointerType(dtype)
        selected = c_ast.TernaryOp(condition_node, _address(consequent, coord),
                                  _address(alternative, coord), coord)
        selected = _typed_pointer(parser, selected, pointer, coord)
        discarded = _cast(parser, c_ast.TernaryOp(
            condition_node, _discarded_node(parser, consequent, coord),
            _discarded_node(parser, alternative, coord), coord), 'void', coord)
        discard_fact = conditional_fact(parser._constant_fact(condition),
                                        _discarded_fact(parser, consequent, coord),
                                        _discarded_fact(parser, alternative, coord),
                                        'int', parser.types, coord)
        return Expression(c_ast.UnaryOp('*', selected, coord), dtype, True,
                          constant_fact=fact, discarded_known=True,
                          discarded_node=discarded, discarded_fact=discard_fact)

    def converted(operand):
        # [expr.cond]/7.5 permits nullptr_t with a null pointer constant. It
        # does not introduce an ordinary int-to-nullptr_t conversion elsewhere.
        if dtype == 'nullptr' and unqualified(operand.dtype) != 'nullptr':
            return _cast(parser, operand.node, dtype, coord)
        return parser._convert(operand, dtype, coord, force=dtype == 'void')

    node = c_ast.TernaryOp(condition_node, converted(consequent), converted(alternative), coord)
    # C's conditional arithmetic promotions must not erase C++ bool/char/enum
    # identity; its generic void result also must not escape as an integer.
    node = (_typed_pointer(parser, node, dtype, coord) if pointer_type(dtype)
            else _cast(parser, node, dtype, coord))
    # A compound nullptr_t result is not an effect-free null literal proof for
    # namespace static initialization. Its dtype suffices for value conversion.
    return Expression(node, dtype, constant_fact=fact)


def comma_expression(parser, first, second, coord):
    from pcc.frontends.cpp.parser import Expression

    parser._require_value(first, allow_void=True, allow_class=True)
    parser._require_value(second, allow_void=True, allow_class=True)
    if is_class_type(first.dtype) or is_class_type(second.dtype):
        raise CppDiagnostic('cpp.unsupported.class-operators', 'class comma requires operator lookup', coord)
    fact = _sequence_fact(parser, first, parser._constant_fact(second), second.dtype, coord)
    if parser.template_function is not None:
        return Expression(Pattern('comma', coord, children=[first, second]), second.dtype,
                          second.lvalue, assignment_result=second.assignment_result, constant_fact=fact)
    discarded = _discarded_node(parser, first, coord)
    if second.lvalue:
        pointer = PointerType(second.dtype)
        selected = c_ast.ExprList([discarded, _address(second, coord)], coord)
        selected = _typed_pointer(parser, selected, pointer, coord)
        discard_plan = c_ast.ExprList([discarded, _discarded_node(parser, second, coord)], coord)
        discard_fact = _sequence_fact(parser, first, _discarded_fact(parser, second, coord), 'int', coord)
        return Expression(c_ast.UnaryOp('*', selected, coord), second.dtype, True,
                          constant_fact=fact, discarded_known=True,
                          discarded_node=discard_plan, discarded_fact=discard_fact)
    return Expression(c_ast.ExprList([discarded, second.node], coord), second.dtype,
                      assignment_result=second.assignment_result, constant_fact=fact)


def expression_sequence(parser, template_argument=False):
    """Full expression grammar for a ?: middle operand or parentheses."""
    result = parser._expression(template_argument=template_argument)
    while parser._peek().value == ',':
        comma = parser.cursor.take()
        following = parser._expression(template_argument=template_argument)
        result = comma_expression(parser, result, following, comma.coord)
    return result
