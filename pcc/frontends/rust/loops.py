"""Unlabeled loop/while-let contexts.

The existing body checker, the move checker's loop fixed point and IR loop
targets own execution; reference-free loops need no external check. This
module neither checks borrows nor creates a second control-flow graph.
Value-carrying breaks and labels remain explicit parser capability boundaries.
"""
from dataclasses import dataclass


@dataclass
class LoopContext:
    kind: str
    has_break: bool = False
