"""Single-condition if-let syntax using the existing owned match machinery.

https://doc.rust-lang.org/reference/expressions/if-expr.html#if-let-patterns
Only the owned scalar/aggregate pattern domain is admitted. Reference matching,
destructor-bearing temporaries and condition chains need their own contracts;
this normalization does not invent a second matcher: the move checker decides
the normalized match like any other.
"""
from .syntax import Expr, Pattern


def normalize_if_let(expression):
    """Reuse match scope, moves, pattern validation and single-value lowering.

    The original scrutinee and branches stay in source order and occur once.
    A missing else has unit value, not the consequent's inferred value. The
    wildcard alternative has no bindings and cannot expose consequent locals.
    The reference admission gate inspects the untouched source before this
    AST-only step.
    """
    assert expression.kind == 'if_let'
    token = expression.token
    if len(expression.children) == 2:
        expression.children.append(Expr('unit', token))
    expression.value = [expression.value, Pattern('wildcard', token)]
    expression.kind = 'match'
