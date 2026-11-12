"""Primitive-array source semantics, deliberately without managed lowering.

JLS 10, 14.14.2, 15.10 and 15.26. The typed nodes retain read/store check order
and array iteration capture, conversion and control-flow obligations;
these are semantic obligations, not an emitted exception, allocation or GC ABI.
Only one-dimensional boolean/integral arrays are admitted here. Other reference fields,
Object supertypes, clone/casts and multidimensional/reference arrays remain gated.
"""
from dataclasses import dataclass

from pcc.support.managed_objects import ManagedValuePlan, unmet_requirements

from .source_syntax import Node, Token
from .floating_semantics import converted_constant, floating_binary
from .source_types import (
    ArrayType, BootstrapStringType, ClassType, FLOATING, INTEGRAL, NamedType,
    model_reference, promoted, widening,
)


@dataclass(frozen=True)
class ArrayIterationPlan:
    """JLS array translation obligations, not temporary slots or native IR.

    The source expression occurs once in the enhanced_for node. The retained
    reference is independent of later writes to its source variable, and must
    remain rooted/relocatable across every body call and continue backedge.
    There is no array address, hidden source identifier or guessed root ABI.
    """
    array_type: ArrayType
    variable_type: object
    captured_array: ManagedValuePlan
    entry_steps: tuple[str, ...] = (
        "evaluate-array-expression-once", "retain-captured-array", "initialize-int-index-zero",
    )
    condition_steps: tuple[str, ...] = (
        "captured-array", "null-check", "read-length", "compare-index-less-than-length",
    )
    iteration_steps: tuple[str, ...] = (
        "captured-array", "index", "bounds-check", "read-component",
        "assignment-conversion", "initialize-loop-variable", "body",
    )
    continue_steps: tuple[str, ...] = ("increment-int-index", "test-condition")

    @property
    def normal_completion_steps(self):
        # Only normal body completion and continue targeting this loop update
        # its index. Break, return and an outer-loop continue leave this loop.
        return self.continue_steps

    @property
    def managed_requirements(self):
        return (*unmet_requirements(self.captured_array),
                "enhanced-for-captured-array-root-through-loop-exit",
                "enhanced-for-captured-array-relocation-aware-access",
                "enhanced-for-captured-array-root-retirement-on-every-exit")

    @property
    def runtime_registered(self):
        return False


class ArrayModelAnalyzer:
    """Mixin used only by ClassAnalyzer, never the native scalar analyzer."""
    # This model, not source_native_arrays, owns array semantics here.
    native_array_domain = False

    def admits_inferred_local_type(self, dtype):
        return isinstance(dtype, ArrayType) or super().admits_inferred_local_type(dtype)

    def array_iteration(self, node, assigned):
        variable, expression, body = node.children
        if isinstance(variable.value, NamedType):
            variable.value = self.project.resolve_named_type(self.program, variable.value)
        # Expression is checked exactly once in the enclosing scope. Its side
        # effects precede even a zero-iteration exit; the binder is not visible.
        after_expression = set(assigned)
        self.expression(expression, after_expression)
        if isinstance(expression.dtype, ClassType):
            expression.token.error("Iterable enhanced for requires the owned iterable type/iterator domain",
                                   code="java.unsupported.enhanced-for-iterable")
        if isinstance(expression.dtype, BootstrapStringType):
            expression.token.error("String iteration requires the owned bootstrap type definition",
                                   code="java.unsupported.string-literals")
        if not isinstance(expression.dtype, ArrayType):
            expression.token.error("enhanced for requires an array or Iterable expression")
        component = expression.dtype.component
        if variable.inference_token is not None:
            # JLS 14.14.2: array var inference selects the component type,
            # without promotion or making the binder visible in the expression.
            variable.value = component
        if isinstance(variable.value, (ClassType, BootstrapStringType)):
            # Assignment conversion can box an element and then widen its
            # reference. Supplied names alone do not establish that hierarchy
            # or the owned wrapper allocation needed to perform it.
            variable.token.error("reference iteration variables require owned boxing/reference conversion",
                                 code="java.unsupported.enhanced-for-boxing")
        # A read component is not a constant expression, even for a final
        # variable iterating a singleton literal. Do not narrow int to byte.
        if not widening(component, variable.value):
            variable.token.error("incompatible enhanced for element assignment conversion from " + component
                                 + " to " + str(variable.value))
        node.value = ArrayIterationPlan(expression.dtype, variable.value, expression.managed_value)
        self.scopes.append({})
        loop = {"node": node, "break": [], "continue": []}
        self.controls.append(loop)
        try:
            variable.symbol = self.declare(variable.token, variable.value, variable.is_final)
            self.statement(body, after_expression | {variable.symbol})
        finally:
            self.controls.pop()
            self.scopes.pop()
        # JLS 16.2.12: the generated length comparison may be false before
        # the body executes. Body-only assignments never become definite here.
        outgoing = set.intersection(after_expression, *loop["break"])
        node.completes = True
        return outgoing

    def assignment_value(self, node, target):
        if isinstance(target, ArrayType):
            if not widening(node.dtype, target):
                node.token.error("incompatible array assignment conversion from " + str(node.dtype) + " to " + str(target))
            return
        return super().assignment_value(node, target)

    def require_array(self, node):
        if isinstance(node.dtype, BootstrapStringType):
            node.token.error("String member/index operations require the owned String source/runtime domain",
                             code="java.unsupported.string-literals")
        if not isinstance(node.dtype, ArrayType):
            node.token.error("array reference required, got " + str(node.dtype))

    def require_array_index(self, node):
        # Unary numeric promotion, not assignment conversion: even 0L is illegal.
        if node.dtype not in {"byte", "short", "char", "int"}:
            node.token.error("array index or dimension must promote to int")

    def array_expression(self, node, assigned):
        if node.kind == "name" and "." in node.value:
            root, suffix = node.value.split(".", 1)
            if (any(root in scope for scope in self.scopes)
                    or root in self.fields and isinstance(self.fields[root].dtype, ArrayType)):
                if suffix != "length":
                    node.token.error("array members other than length require the next reference domain",
                                     code="java.unsupported.array-model-shape")
                token = Token(root, node.token.filename, node.token.line, node.token.column)
                node.kind, node.value, node.children = "array_length", None, [Node("name", token, root)]
        if node.kind == "call" and "." in node.value:
            root = node.value.split(".", 1)[0]
            binding = next((scope[root] for scope in reversed(self.scopes) if root in scope), None)
            if binding is None:
                binding = self.fields.get(root)
            if binding is not None and isinstance(binding.dtype, ArrayType):
                node.token.error("array methods require the next reference domain",
                                 code="java.unsupported.array-model-shape")
        kind = node.kind
        if kind == "null":
            node.dtype = "null"
            # A null literal is not a JLS constant expression.
            return True
        if kind == "array_new":
            self.expression(node.children[0], assigned)
            self.require_array_index(node.children[0])
            node.dtype = node.value
            # Negative dimensions are valid source with an abrupt runtime path.
            node.runtime_steps = ("dimension", "negative-size-check", "allocate-zeroed-array")
            return True
        if kind == "array_initializer":
            for value in node.children:
                self.expression(value, assigned)
                self.assignment_value(value, node.value.component)
            node.dtype = node.value
            node.runtime_steps = ("allocate-zeroed-array", "initializers-left-to-right", "store-each-component")
            return True
        if kind == "array_length":
            self.expression(node.children[0], assigned)
            if isinstance(node.children[0].dtype, ClassType):
                node.token.error("source-class length member access requires the next managed receiver domain",
                                 code="java.unsupported.object-expressions")
            self.require_array(node.children[0])
            node.dtype = "int"
            node.runtime_steps = ("array-reference", "null-check", "read-length")
            return True
        if kind == "array_access":
            array, index = node.children
            self.expression(array, assigned)
            self.expression(index, assigned)
            self.require_array(array)
            self.require_array_index(index)
            node.dtype = array.dtype.component
            node.runtime_steps = ("array-reference", "index", "null-check", "bounds-check", "read-component")
            return True
        if kind == "binary" and node.value in {"==", "!="}:
            # Resolve operands once. This branch owns both scalar and admitted
            # array equality, avoiding a second traversal of receiver/call nodes.
            left, right = node.children
            self.expression(left, assigned)
            self.expression(right, assigned)
            if any(isinstance(child.dtype, BootstrapStringType) for child in (left, right)):
                if not (model_reference(left.dtype) and model_reference(right.dtype)
                        and (left.dtype == right.dtype or "null" in (left.dtype, right.dtype))):
                    node.token.error("incomparable String reference types")
                node.token.error("String equality requires complete constant-expression/interned identity semantics",
                                 code="java.unsupported.string-literals")
            if model_reference(left.dtype) or model_reference(right.dtype):
                if not (model_reference(left.dtype) and model_reference(right.dtype)
                        and (left.dtype == right.dtype or "null" in (left.dtype, right.dtype))):
                    node.token.error("incomparable array or source-class reference types")
                node.dtype = "boolean"
                return True
            if left.dtype != "boolean" or right.dtype != "boolean":
                self.require_numeric(left)
                self.require_numeric(right)
                node.operation_type = promoted(left.dtype, right.dtype)
            node.dtype = "boolean"
            if left.constant is not None and right.constant is not None:
                if node.operation_type in FLOATING:
                    a = converted_constant(left.constant, left.dtype, node.operation_type)
                    b = converted_constant(right.constant, right.dtype, node.operation_type)
                    node.constant = floating_binary(node.value, a, b, node.operation_type)
                else:
                    node.constant = (left.constant == right.constant) == (node.value == "==")
            return True
        return False

    def array_assignment(self, node, assigned):
        target = node.children[0]
        self.expression(target, assigned)
        while target.kind == "group":
            target = target.children[0]
        if target.kind == "array_length":
            target.token.error("array length is final and cannot be assigned")
        if target.kind != "array_access":
            target.token.error("array assignment requires an array component variable")
        # Keep the variable access distinct from a read. The store plan owns the
        # checks; simple assignment evaluates RHS before null/bounds checks.
        target.kind, target.runtime_steps = "array_place", ()
        dtype, op = target.dtype, node.value
        if len(node.children) > 1:
            rhs = node.children[1]
            self.expression(rhs, assigned)
        if op == "=":
            self.assignment_value(rhs, dtype)
            node.runtime_steps = ("array-reference", "index", "rhs", "null-check", "bounds-check", "store-component")
        else:
            if op in {"&=", "|=", "^="} and dtype == rhs.dtype == "boolean":
                node.operation_type = "boolean"
            else:
                if dtype not in INTEGRAL:
                    node.token.error("array compound arithmetic requires an integer component")
                if op in {"++", "--"}:
                    node.operation_type = promoted(dtype)
                else:
                    integral_only = op in {"&=", "|=", "^=", "<<=", ">>=", ">>>="}
                    (self.require_int if integral_only else self.require_numeric)(rhs)
                    node.operation_type = promoted(dtype, rhs.dtype, shift=op in {"<<=", ">>=", ">>>="})
                if op in {"/=", "%="}:
                    self.arithmetic_domain(node, op[:-1], node.operation_type, rhs)
            node.runtime_steps = ("array-reference", "index", "null-check", "bounds-check", "read-component",
                                  *(() if op in {"++", "--"} else ("rhs",)), "operation", "narrow", "store-component")
        # Updating a component does not definitely assign the reference variable.
        return set(assigned)
