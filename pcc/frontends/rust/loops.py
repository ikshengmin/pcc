"""Unlabeled loop/while-let contexts and explicit public admission.

The existing body checker, move fixed point and IR loop targets own execution.
This module neither certifies borrows nor creates a second control-flow graph.
Value-carrying breaks and labels remain explicit parser capability boundaries.
"""
from dataclasses import dataclass


@dataclass
class LoopContext:
    kind: str
    has_break: bool = False


def reject_uncertified_loops(functions):
    """Even unused loop bodies need the actual whole-source rustc record."""
    pending = [function.body for function in functions if function.body is not None]
    while pending:
        expression = pending.pop()
        if expression.kind in {'loop', 'while_let'}:
            kind = 'while let' if expression.kind == 'while_let' else 'loop'
            raise expression.token.error(
                kind + ' control-flow admission requires an actual source/options-bound '
                'CheckedRustSource; caller attestation is not certification')
        pending.extend(expression.children)
