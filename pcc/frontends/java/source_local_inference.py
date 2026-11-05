"""Primitive local type inference before ordinary owned native lowering.

JLS 6.3 and 14.4. The initializer has no target type and is checked once. Its
own local name is in scope during inference, but must not be read or assigned.
The scope-only marker never enters method locals, DA snapshots or native slots.
The native owner admits primitive values only; nonlowering models may admit
additional checked types without granting native reference storage.
"""
from dataclasses import dataclass, field

from .source_types import DECLARED


@dataclass(frozen=True)
class PendingInferredLocal:
    name: str
    dtype: None = field(default=None, init=False)


class LocalInferenceAnalyzer:
    def admits_inferred_local_type(self, dtype):
        return dtype in DECLARED

    def require_completed_local(self, binding, token):
        if isinstance(binding, PendingInferredLocal):
            token.error("cannot reference local variable in its own inferred initializer: " + binding.name)
        return binding

    def check_inferred_reference(self, node):
        if node.kind not in {"name", "call"}:
            return
        name = node.value
        # A simple method name occupies a different namespace from a local.
        if node.kind == "call" and "." not in name:
            return
        root = name.split(".", 1)[0]
        binding = next((scope[root] for scope in reversed(self.scopes) if root in scope), None)
        self.require_completed_local(binding, node.token)

    def infer_local(self, node, assigned):
        if any(node.token.text in scope for scope in self.scopes):
            node.token.error("local or parameter name is already in scope")
        initializer, = node.children
        scope = self.scopes[-1]
        pending = PendingInferredLocal(node.token.text)
        scope[node.token.text] = pending
        try:
            # Admitted initializer expressions introduce no nested local scope.
            # Check source name dependencies even behind a currently unsupported
            # write target, without typing or evaluating an expression twice.
            remaining = [initializer]
            while remaining:
                expression = remaining.pop()
                self.check_inferred_reference(expression)
                remaining.extend(reversed(expression.children))
            self.expression(initializer, assigned)
        finally:
            # Do not leave an unknown type behind, including after rejection.
            assert scope[node.token.text] is pending
            del scope[node.token.text]
        dtype = initializer.dtype
        if dtype in {"void", "null"}:
            node.token.error("cannot infer a local variable type from " + dtype)
        if not self.admits_inferred_local_type(dtype):
            node.inference_token.error("reference local inference requires the next source inference and managed-storage domain",
                                       code="java.unsupported.local-inference-domain")
        node.value = dtype
        node.symbol = self.declare(node.token, dtype, node.is_final)
