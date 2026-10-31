"""Typed integral constant facts for C++ Sema, not a second IR evaluator.

The parser supplies validated literal values and its already-selected operand
conversion/result types. This module performs no parsing, name lookup, overload
resolution or C-AST rewriting. ArithmeticTypes owns target widths/signedness.
Facts prove constant-expression eligibility, not merely runtime foldability.

N4659 [expr.const], [expr.mul], [expr.shift], [conv.integral] govern this
integral boundary. Floating/pointer constant evaluation and implementation-
defined signed representation/conversion policies remain explicitly unmodeled.
An operation that would have undefined behavior produces NOT_CONSTANT rather
than raising a source diagnostic: an enclosing logical operator may not
evaluate that operation at all.
"""

from pcc.frontends.cpp.types import is_enum_type, unqualified


KNOWN = "KNOWN"
NOT_CONSTANT = "NOT_CONSTANT"
UNMODELED = "UNMODELED"


class ConstantFact:
    """A scalar prvalue fact. Treat facts as immutable once constructed."""

    def __init__(self, status, dtype, value=None, reason="", coord=None):
        self.status = status
        self.dtype = unqualified(dtype)
        self.value = value
        self.reason = reason
        self.coord = coord


def known(dtype, value, coord=None):
    """Record a validated typed integer; never obtain it through host float.

    The caller owns literal/type validation. Operations below also verify that
    a supplied value fits its source type on the selected target, so malformed
    facts cannot silently turn into a proof for a different target.
    """
    if not isinstance(value, int):
        raise TypeError("an integral constant fact requires an integer value")
    return ConstantFact(KNOWN, dtype, int(value), coord=coord)


def not_constant(dtype, reason, coord=None):
    return ConstantFact(NOT_CONSTANT, dtype, reason=reason, coord=coord)


def unmodeled(dtype, reason, coord=None):
    return ConstantFact(UNMODELED, dtype, reason=reason, coord=coord)


def _range(dtype, types):
    dtype = unqualified(dtype)
    if dtype == "bool":
        return 0, 1
    width = types.width(dtype)
    if types.unsigned(dtype):
        return 0, (1 << width) - 1
    return -(1 << (width - 1)), (1 << (width - 1)) - 1


def _propagate(fact, dtype, coord):
    if fact is None:
        return unmodeled(dtype, "constant facts are unavailable", coord)
    origin = fact.coord if fact.coord is not None else coord
    if fact.status == NOT_CONSTANT:
        return not_constant(dtype, fact.reason, origin)
    if fact.status == UNMODELED:
        return unmodeled(dtype, fact.reason, origin)
    return unmodeled(dtype, "invalid constant fact state", origin)


def converted_fact(fact, destination, types, coord=None, allow_scoped=False):
    """Evaluate an already-selected integral conversion; do not rank it."""
    destination = unqualified(destination)
    if fact is None:
        return unmodeled(destination, "constant facts are unavailable", coord)
    if coord is None:
        coord = fact.coord
    if is_enum_type(fact.dtype):
        source = unqualified(fact.dtype)
        if not source.complete:
            return unmodeled(destination, "incomplete enum constant type", coord)
        if destination is source:
            storage = ConstantFact(fact.status, source.underlying, fact.value, fact.reason, fact.coord)
            checked = converted_fact(storage, source.underlying, types, coord)
            return ConstantFact(checked.status, source, checked.value, checked.reason, checked.coord)
        if source.scoped and not allow_scoped:
            return unmodeled(destination, "scoped enum needs an explicit conversion context", coord)
        # Sema selected an enum promotion/conversion, or an explicit scoped
        # enum conversion. Keep the original expression/nominal type unchanged.
        fact = ConstantFact(fact.status, source.underlying, fact.value, fact.reason, fact.coord)
    if is_enum_type(destination):
        return unmodeled(destination, "integer-to-enum constant casts are not implemented", coord)
    if not types.integral(fact.dtype) or not types.integral(destination):
        return unmodeled(destination, "non-integral constant conversion is not implemented", coord)
    if fact.status != KNOWN:
        return _propagate(fact, destination, coord)
    minimum, maximum = _range(fact.dtype, types)
    if not isinstance(fact.value, int) or not minimum <= fact.value <= maximum:
        return unmodeled(destination, "constant value does not fit its source target type", coord)
    if destination == "bool":
        return known(destination, int(fact.value != 0), coord)
    if types.unsigned(destination):
        return known(destination, fact.value % (1 << types.width(destination)), coord)
    minimum, maximum = _range(destination, types)
    if minimum <= fact.value <= maximum:
        return known(destination, fact.value, coord)
    return unmodeled(destination, "out-of-range signed conversion needs target policy", coord)


def _result(value, dtype, types, coord):
    """Apply arithmetic result rules, not explicit signed conversion rules."""
    dtype = unqualified(dtype)
    if not types.integral(dtype):
        return unmodeled(dtype, "non-integral constant result is not implemented", coord)
    if dtype == "bool":
        return known(dtype, int(value != 0), coord)
    if types.unsigned(dtype):
        return known(dtype, value % (1 << types.width(dtype)), coord)
    minimum, maximum = _range(dtype, types)
    if minimum <= value <= maximum:
        return known(dtype, value, coord)
    return not_constant(dtype, "signed integral overflow", coord)


def unary_fact(operator, operand, promoted_type, result_type, types, coord=None):
    """Evaluate a typed builtin integral unary operator after Sema."""
    if coord is None and operand is not None:
        coord = operand.coord
    if operator not in ("+", "-", "~", "!"):
        return unmodeled(result_type, "constant unary operator is not implemented", coord)
    operand = converted_fact(operand, promoted_type, types, coord)
    if operand.status != KNOWN:
        return _propagate(operand, result_type, coord)
    if operator == "+":
        return _result(operand.value, result_type, types, coord)
    if operator == "-":
        return _result(-operand.value, result_type, types, coord)
    if operator == "!":
        return _result(int(operand.value == 0), result_type, types, coord)
    if not types.unsigned(promoted_type):
        return unmodeled(result_type, "signed complement needs target representation policy", coord)
    mask = (1 << types.width(promoted_type)) - 1
    return _result(mask ^ operand.value, result_type, types, coord)


def _logical_fact(operator, lhs, rhs, lhs_type, rhs_type, result_type, types, coord):
    lhs = converted_fact(lhs, lhs_type, types, coord)
    if lhs.status != KNOWN:
        # A nonconstant LHS is always evaluated. An unmodeled LHS might be a
        # constant that short-circuits the RHS, even if RHS is NOT_CONSTANT.
        return _propagate(lhs, result_type, coord)
    truth = lhs.value != 0
    if operator == "&&" and not truth or operator == "||" and truth:
        return _result(int(truth), result_type, types, coord)
    rhs = converted_fact(rhs, rhs_type, types, coord)
    if rhs.status != KNOWN:
        return _propagate(rhs, result_type, coord)
    return _result(int(rhs.value != 0), result_type, types, coord)


def binary_fact(operator, lhs, rhs, lhs_type, rhs_type, result_type, types, coord=None):
    """Evaluate integral operands using parser-selected conversion types.

    Both facts can be constructed before this call, but constant-evaluation
    failures are inert facts. Logical short-circuiting suppresses an unevaluated
    fact; static well-formedness remains the parser's separate responsibility.
    """
    if coord is None and lhs is not None:
        coord = lhs.coord
    if operator in ("&&", "||"):
        return _logical_fact(operator, lhs, rhs, lhs_type, rhs_type, result_type, types, coord)
    if operator not in ("+", "-", "*", "/", "%", "<<", ">>", "&", "|", "^",
                        "==", "!=", "<", "<=", ">", ">="):
        return unmodeled(result_type, "constant binary operator is not implemented", coord)
    lhs = converted_fact(lhs, lhs_type, types, coord)
    rhs = converted_fact(rhs, rhs_type, types, coord)
    # These operators evaluate both operands. Either proven nonconstant
    # operand suffices, even if evaluation of the other is not yet modeled.
    for fact in (lhs, rhs):
        if fact.status == NOT_CONSTANT:
            return _propagate(fact, result_type, coord)
    for fact in (lhs, rhs):
        if fact.status != KNOWN:
            return _propagate(fact, result_type, coord)
    left, right = lhs.value, rhs.value
    if operator == "+":
        value = left + right
    elif operator == "-":
        value = left - right
    elif operator == "*":
        value = left * right
    elif operator in ("/", "%"):
        if right == 0:
            return not_constant(result_type, "integral division or remainder by zero", coord)
        quotient = abs(left) // abs(right)
        if (left < 0) != (right < 0):
            quotient = -quotient
        minimum, maximum = _range(lhs_type, types)
        if not minimum <= quotient <= maximum:
            # MIN % -1 is also undefined, even though mathematical remainder
            # zero itself would fit the signed result type.
            return not_constant(result_type, "signed integral division overflow", coord)
        value = quotient if operator == "/" else left - quotient * right
    elif operator in ("<<", ">>"):
        width = types.width(lhs_type)
        if right < 0 or right >= width:
            return not_constant(result_type, "invalid integral shift count", coord)
        if operator == ">>":
            if left < 0:
                return unmodeled(result_type, "negative signed right shift needs target policy", coord)
            value = left >> right
        elif types.unsigned(lhs_type):
            value = (left << right) % (1 << width)
        else:
            if left < 0:
                return not_constant(result_type, "negative signed left shift", coord)
            value = left << right
            if value >= 1 << width:
                return not_constant(result_type, "signed left shift exceeds corresponding unsigned range", coord)
            if value > _range(lhs_type, types)[1]:
                return unmodeled(result_type, "signed left-shift result conversion needs target policy", coord)
    elif operator in ("&", "|", "^"):
        if left < 0 or right < 0:
            return unmodeled(result_type, "negative signed bitwise operand needs target representation policy", coord)
        if operator == "&":
            value = left & right
        elif operator == "|":
            value = left | right
        else:
            value = left ^ right
    elif operator == "==":
        value = int(left == right)
    elif operator == "!=":
        value = int(left != right)
    elif operator == "<":
        value = int(left < right)
    elif operator == "<=":
        value = int(left <= right)
    elif operator == ">":
        value = int(left > right)
    else:
        value = int(left >= right)
    return _result(value, result_type, types, coord)
