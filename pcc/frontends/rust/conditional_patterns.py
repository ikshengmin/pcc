"""Single-condition if-let syntax using the existing owned match machinery.

https://doc.rust-lang.org/reference/expressions/if-expr.html#if-let-patterns
Only the owned scalar/aggregate pattern domain is admitted. Reference matching,
destructor-bearing temporaries and condition chains need their own contracts;
this normalization does not certify ownership or invent a second matcher.
"""
from .syntax import Expr, Pattern


def reject_uncertified_if_let(functions):
    """Inspect unused original bodies before normalization or materialization."""
    pending = [function.body for function in functions if function.body is not None]
    while pending:
        expression = pending.pop()
        if expression.kind == 'if_let':
            raise expression.token.error(
                'if let control-flow admission requires an actual source/options-bound '
                'CheckedRustSource; caller attestation is not certification')
        pending.extend(expression.children)


def normalize_if_let(expression):
    """Reuse match scope, moves, pattern validation and single-value lowering.

    The original scrutinee and branches stay in source order and occur once.
    A missing else has unit value, not the consequent's inferred value. The
    wildcard alternative has no bindings and cannot expose consequent locals.
    Public admission checks the untouched source before this AST-only step.
    """
    assert expression.kind == 'if_let'
    token = expression.token
    if len(expression.children) == 2:
        expression.children.append(Expr('unit', token))
    expression.value = [expression.value, Pattern('wildcard', token)]
    expression.kind = 'match'
