"""Primitive-array source semantics, deliberately without managed lowering.

JLS 10, 15.10 and 15.26. The typed nodes retain distinct read/store check order;
these are semantic obligations, not an emitted exception, allocation or GC ABI.
Only one-dimensional boolean/integral arrays are admitted here. Reference fields,
Object supertypes, clone/casts and multidimensional/reference arrays remain gated.
"""
from .source_syntax import Node, Token
from .source_types import ArrayType, INTEGRAL, array_reference, promoted, widening


class ArrayModelAnalyzer:
    """Mixin used only by ClassAnalyzer, never the native scalar analyzer."""

    def assignment_value(self, node, target):
        if isinstance(target, ArrayType):
            if not widening(node.dtype, target):
                node.token.error("incompatible array assignment conversion from " + str(node.dtype) + " to " + str(target))
            return
        return super().assignment_value(node, target)

    def require_array(self, node):
        if not isinstance(node.dtype, ArrayType):
            node.token.error("array reference required, got " + str(node.dtype))

    def require_array_index(self, node):
        # Unary numeric promotion, not assignment conversion: even 0L is illegal.
        if node.dtype not in {"byte", "short", "char", "int"}:
            node.token.error("array index or dimension must promote to int")

    def array_expression(self, node, assigned):
        if node.kind == "name" and "." in node.value:
            root, suffix = node.value.split(".", 1)
            if any(root in scope for scope in self.scopes):
                if suffix != "length":
                    node.token.error("array members other than length require the next reference domain",
                                     code="java.unsupported.array-model-shape")
                token = Token(root, node.token.filename, node.token.line, node.token.column)
                node.kind, node.value, node.children = "array_length", None, [Node("name", token, root)]
        if node.kind == "call" and "." in node.value:
            root = node.value.split(".", 1)[0]
            binding = next((scope[root] for scope in reversed(self.scopes) if root in scope), None)
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
            if array_reference(left.dtype) or array_reference(right.dtype):
                if not (array_reference(left.dtype) and array_reference(right.dtype)
                        and (left.dtype == right.dtype or "null" in (left.dtype, right.dtype))):
                    node.token.error("incomparable array reference types")
                node.dtype = "boolean"
                return True
            if left.dtype != "boolean" or right.dtype != "boolean":
                self.require_int(left)
                self.require_int(right)
                node.operation_type = promoted(left.dtype, right.dtype)
            node.dtype = "boolean"
            if left.constant is not None and right.constant is not None:
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
                    self.require_int(rhs)
                    node.operation_type = promoted(dtype, rhs.dtype, shift=op in {"<<=", ">>=", ">>>="})
                if op in {"/=", "%="}:
                    self.require_nonzero_constant_divisor(rhs)
            node.runtime_steps = ("array-reference", "index", "null-check", "bounds-check", "read-component",
                                  *(() if op in {"++", "--"} else ("rhs",)), "operation", "narrow", "store-component")
        # Updating a component does not definitely assign the reference variable.
        return set(assigned)
