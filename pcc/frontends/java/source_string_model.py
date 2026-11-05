"""Partial String source facts, not an owned bootstrap or runtime definition.

JLS 3.10.5,4.12.4,12.4.1,15.18.1,15.29. Content is immutable UTF-16 data;
it is never a managed object, intern-table entry or runtime reference value.
Native scalar analysis does not use this mixin. Every admitted String fact
retains unresolved bootstrap and, where needed, materialization obligations.
"""
from dataclasses import dataclass

from .source_types import BootstrapStringType


MAX_STRING_UNITS = 65_536
MAX_STRING_FOLD_WORK = 1_048_576


@dataclass(frozen=True)
class StringSourceConstant:
    units: tuple[int, ...]

    def __post_init__(self):
        if (type(self.units) is not tuple or len(self.units) > MAX_STRING_UNITS
                or any(type(unit) is not int or not 0 <= unit <= 0xffff for unit in self.units)):
            raise ValueError("String source constants require bounded immutable UTF-16 units")


@dataclass(frozen=True)
class StringContextObligation:
    owner: str
    tokens: tuple
    requirements: tuple[str, ...]
    type_name: str = "java/lang/String"


class StringModelAnalyzer:
    def admits_inferred_local_type(self, dtype):
        # Inference preserves the unresolved bootstrap identity and its
        # obligations. It does not supply native String storage or a layout.
        return isinstance(dtype, BootstrapStringType) or super().admits_inferred_local_type(dtype)

    def require_callable_string_context(self, method, token):
        """The selected signature owns the ABI contract, even for null actuals."""
        if (isinstance(method.result, BootstrapStringType)
                or any(isinstance(dtype, BootstrapStringType) for dtype in method.parameter_types)):
            self.project.require_string_context(self.program, token)

    def string_local_context(self):
        if getattr(self, "_string_constant_method", None) is not self.method:
            self._string_constant_method = self.method
            self.string_constant_locals = {}

    def expression(self, node, assigned):
        self.string_local_context()
        super().expression(node, assigned)
        if node.kind == "name" and isinstance(node.dtype, BootstrapStringType):
            node.string_constant = self.string_constant_locals.get(node.symbol)

    def type_expression(self, node, assigned):
        if node.kind == "string":
            self.project.require_string_context(self.program, node.token, materialization=True)
            self.project.charge_string_work(len(node.value), node.token)
            node.dtype = BootstrapStringType()
            node.string_constant = StringSourceConstant(node.value)
            return
        super().type_expression(node, assigned)
        if node.kind == "group":
            node.string_constant = node.children[0].string_constant
        elif node.kind == "conditional" and isinstance(node.dtype, BootstrapStringType):
            condition, yes, no = node.children
            # JLS 15.29 requires both arms to be constant expressions. Reuse
            # the selected immutable fact only after both arms were checked.
            if (condition.constant is not None
                    and yes.string_constant is not None and no.string_constant is not None):
                node.string_constant = (yes if condition.constant else no).string_constant

    def typed_binary_extension(self, node):
        left, right = node.children
        if not any(isinstance(child.dtype, BootstrapStringType) for child in (left, right)):
            return super().typed_binary_extension(node)
        if node.value != "+":
            # Equality is gated before the reference-comparison owner. Other
            # String arithmetic/logical operators remain ordinary type errors.
            return False
        if any(child.dtype == "void" for child in (left, right)):
            node.token.error("void value cannot participate in String concatenation")
        if not all(isinstance(child.dtype, BootstrapStringType) and child.string_constant is not None
                   for child in (left, right)):
            node.token.error("String concatenation requires the next source-constant or owned runtime conversion domain",
                             code="java.unsupported.string-literals")
        a, b = left.string_constant.units, right.string_constant.units
        self.project.charge_string_work(len(a) + len(b), node.token)
        node.dtype = BootstrapStringType()
        node.string_constant = StringSourceConstant(a + b)
        return True

    def require_switch_selector(self, node):
        if isinstance(node.dtype, BootstrapStringType):
            node.token.error("String switches require the owned String comparison/exception domain",
                             code="java.unsupported.string-literals")
        return super().require_switch_selector(node)

    def typed_assignment_extension(self, node):
        if isinstance(node.symbol.dtype, BootstrapStringType) and node.value == "+=":
            if any(child.dtype == "void" for child in node.children):
                node.token.error("void value cannot participate in String concatenation")
            node.token.error("String compound concatenation requires owned conversion/allocation semantics",
                             code="java.unsupported.string-literals")
        return super().typed_assignment_extension(node)

    def statement(self, node, assigned):
        self.string_local_context()
        result = super().statement(node, assigned)
        if (node.kind == "var" and node.is_final and isinstance(node.value, BootstrapStringType)
                and node.children and node.children[0].string_constant is not None):
            self.string_constant_locals[node.symbol] = node.children[0].string_constant
        return result

    def evaluate_string_field(self, field):
        state = self.field_states.get(field.token.text)
        if state == "complete":
            return None
        if state == "evaluating":
            field.token.error("cyclic String initialization requires the shared Java initialization contract",
                              code="java.unsupported.cyclic-initialization")
        self.field_states[field.token.text] = "evaluating"
        previous = self.current_field
        self.current_field = field
        try:
            self.expression(field.initializer, set())
            self.assignment_value(field.initializer, field.dtype)
            value = field.initializer.string_constant
            if "final" not in field.modifiers or value is None:
                field.token.error("static String initialization outside the implemented constant-expression subset requires the shared Java initialization contract",
                                  code="java.unsupported.static-initialization")
            field.string_constant = value
            self.field_states[field.token.text] = "complete"
            # Numeric/boolean .constant and runtime identity deliberately stay
            # absent. Reading a constant field does not initialize its class.
            return None
        finally:
            self.current_field = previous
