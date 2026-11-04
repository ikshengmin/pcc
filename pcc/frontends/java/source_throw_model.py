"""Checked throw statements with an explicit, nonlowering null-type boundary.

JLS 11.2, 14.18 and 16.2.13. Null-typed operands are valid without a checked
exception declaration. Other reference operands need the owned Throwable
hierarchy and checked-exception analysis, neither supplied by nominal names.
No exception object, handler table, root slot or unwind code is created here.
"""
from dataclasses import dataclass, field

from .source_types import BootstrapStringType, ClassType


THROW_OPERAND_STEPS = ("evaluate-operand-once", "branch-on-operand-completion")
THROW_NULL_STEPS = (
    "create-owned-NullPointerException-or-propagate-allocation-failure",
    "retain-exception-in-owning-root", "match-handler-and-unwind-with-cleanup",
    "transfer-to-handler-or-uncaught-exception-owner",
)
THROW_OPERAND_ABRUPT_STEPS = ("propagate-original-exception-unchanged",)
THROW_REQUIREMENTS = (
    "owned-bootstrap-Throwable-hierarchy-and-runtime-type-identity",
    "owned-NullPointerException-definition-allocation-and-initialization",
    "operand-and-exception-allocation-abrupt-completion",
    "shared-owning-root-and-safepoint-ABI",
    "exception-root-survival-through-handler-search-and-relocation",
    "shared-owned-exception-unwind-and-handler-dispatch",
    "Java-finally-monitor-cleanup-and-cross-language-unwind-policy",
    "owned-Java-uncaught-exception-delivery",
)


@dataclass(frozen=True)
class ThrowSourcePlan:
    """Source facts and unmet contracts, never a native exception value."""
    operand_type: str
    runtime_exception_type: str = field(default="java/lang/NullPointerException", init=False)
    operand_steps: tuple[str, ...] = THROW_OPERAND_STEPS
    normal_operand_steps: tuple[str, ...] = THROW_NULL_STEPS
    abrupt_operand_steps: tuple[str, ...] = THROW_OPERAND_ABRUPT_STEPS
    requirements: tuple[str, ...] = THROW_REQUIREMENTS

    @property
    def runtime_registered(self):
        return False


@dataclass(frozen=True)
class ThrowContextObligation:
    owner: str
    method_name: str
    method_descriptor: str
    tokens: tuple
    requirements: tuple[str, ...] = THROW_REQUIREMENTS


class ThrowModelAnalyzer:
    """Used only by ClassAnalyzer; the scalar frontend still rejects throw."""

    def throw_statement(self, node, assigned):
        operand, = node.children
        # Check the entire expression once before classifying its static type.
        # An abrupt operand must propagate unchanged, before null conversion.
        self.expression(operand, set(assigned))
        if isinstance(operand.dtype, (ClassType, BootstrapStringType)):
            operand.token.error("reference throw operands require the owned Throwable hierarchy and checked-exception analysis",
                                code="java.unsupported.throwable-types")
        if operand.dtype != "null":
            operand.token.error("throw operand must have null type or a reference type assignable to Throwable")
        node.value = ThrowSourcePlan(operand.dtype)
        self.project.record_source_throw(self.program, self.method, node)
        # Throw has no normal successor. It is not a loop break/continue and
        # contributes no state to those exit joins, even inside a labeled loop.
        node.completes = False
        return None
