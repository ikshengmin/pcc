"""Checked integral division/remainder source plans, never exception lowering.

JLS 5.6, 15.7, 15.17.2/3 and 15.26.2. The scalar frontend still requires a
constant nonzero divisor. The nonlowering class model additionally preserves
the checked dynamic/zero domain, including compound assignment's saved left
value and variable. No ArithmeticException definition, root or handler exists
because a source plan names one; all such runtime requirements remain unmet.
"""
from dataclasses import dataclass, field

from .source_types import INTEGRAL, descriptor


INTEGRAL_ARITHMETIC_REQUIREMENTS = (
    "owned-integral-divisor-check-and-Java-MIN-minus-one-lowering",
    "owned-bootstrap-Throwable-hierarchy-and-runtime-type-identity",
    "owned-ArithmeticException-definition-allocation-and-initialization",
    "operand-and-exception-allocation-abrupt-completion",
    "shared-owning-root-and-safepoint-ABI",
    "exception-root-survival-through-handler-search-and-relocation",
    "shared-owned-exception-unwind-and-handler-dispatch",
    "Java-finally-monitor-cleanup-and-cross-language-unwind-policy",
    "owned-Java-uncaught-exception-delivery",
)
INTEGRAL_ZERO_STEPS = (
    "create-owned-ArithmeticException-or-propagate-allocation-failure",
    "retain-exception-in-owning-root", "match-handler-and-unwind-with-cleanup",
    "transfer-to-handler-or-uncaught-exception-owner",
)
INTEGRAL_ABRUPT_OPERAND_STEPS = (
    "skip-remaining-operands", "propagate-original-exception-unchanged",
    "do-not-perform-arithmetic-or-store",
)


@dataclass(frozen=True)
class IntegralArithmeticPlan:
    """An original operation's source contract, not a native executable plan."""
    operator: str
    promoted_type: str
    target_kind: str
    target_type: str | None
    divisor_constant: int | None
    operand_steps: tuple[str, ...]
    operation_steps: tuple[str, ...]
    zero_steps: tuple[str, ...] = INTEGRAL_ZERO_STEPS
    abrupt_operand_steps: tuple[str, ...] = INTEGRAL_ABRUPT_OPERAND_STEPS
    requirements: tuple[str, ...] = INTEGRAL_ARITHMETIC_REQUIREMENTS
    runtime_exception_type: str = field(default="java/lang/ArithmeticException", init=False)

    @property
    def runtime_registered(self):
        return False


@dataclass(frozen=True)
class IntegralArithmeticContextObligation:
    owner: str
    context_kind: str
    context_name: str
    context_descriptor: str
    tokens: tuple
    requirements: tuple[str, ...] = INTEGRAL_ARITHMETIC_REQUIREMENTS


class IntegralArithmeticModelAnalyzer:
    """Extend only the class model after existing owners check both operands."""

    def arithmetic_domain(self, node, operator, dtype, divisor):
        if (dtype not in INTEGRAL or operator not in {"/", "%"}
                or divisor.constant is not None and divisor.constant != 0):
            return super().arithmetic_domain(node, operator, dtype, divisor)
        # Do not analyze either operand again or replace original AST children.
        # Existing owners already checked their types, scope and assignment.
        target_type = None
        if node.kind == "binary":
            target_kind = "value"
            steps = ("evaluate-dividend-once", "branch-on-dividend-completion",
                     "evaluate-divisor-once", "branch-on-divisor-completion")
        elif node.kind == "array_assign":
            target_kind, target_type = "array-component", node.children[0].dtype
            steps = ("evaluate-array-reference-once", "branch-on-array-reference-completion",
                     "evaluate-index-once", "branch-on-index-completion",
                     "null-check", "bounds-check", "branch-on-array-access-check-completion",
                     "remember-array-component",
                     "read-and-save-lhs-value", "evaluate-divisor-once",
                     "branch-on-divisor-completion")
        elif node.kind == "field_assign":
            target_kind, target_type = "receiver-field", node.symbol.dtype
            steps = ("retain-current-receiver", "remember-instance-field",
                     "read-and-save-lhs-value", "evaluate-divisor-once",
                     "branch-on-divisor-completion")
        else:
            assert node.kind in {"assign", "mutation"}
            target_kind, target_type = "local", node.symbol.dtype
            steps = ("remember-local-variable", "read-and-save-lhs-value",
                     "evaluate-divisor-once", "branch-on-divisor-completion")
        operations = (
            "binary-numeric-promotion-to-" + dtype,
            "test-divisor-zero-after-operands", "branch-on-zero",
            "handle-MIN-by-minus-one-without-overflow-trap",
            "truncate-quotient-toward-zero",
            "produce-quotient" if operator == "/" else "produce-dividend-minus-quotient-times-divisor",
        )
        if target_type is not None:
            operations += ("convert-result-to-" + target_type,
                           "store-saved-variable-only-after-normal-operation",
                           "yield-stored-value")
        node.arithmetic_plan = IntegralArithmeticPlan(
            operator, dtype, target_kind, target_type, divisor.constant, steps, operations)
        self.project.record_integral_arithmetic(self, node, divisor)
        # Even a literal zero is not a syntactic abrupt-completion statement.
        # Reachability/DA stays with its normal owner; constant folding skips it.


def integral_arithmetic_context(analyzer, node, divisor):
    """Retain the real declaration context, including methodless field init."""
    field = analyzer.current_field
    method = analyzer.method
    if field is not None:
        kind, name, signature = "field-initializer", field.token.text, descriptor(field.dtype)
    elif analyzer.current_initializer is not None:
        kind, name, signature = "instance-initializer", "<instance-initializer>", "()V"
    else:
        assert method is not None
        kind = "constructor" if method.is_constructor else "method"
        name, signature = method.token.text, method.descriptor
    return IntegralArithmeticContextObligation(
        analyzer.program.name, kind, name, signature, (node.token, divisor.token))
