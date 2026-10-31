"""Java source typing, lexical bindings, reachability and definite assignment.

This pass creates typed source AST, not JVM instructions. The scalar
subset has no assignment expressions, final locals or reference values; those
boundaries keep its structured definite-assignment rules explicit.
"""
from dataclasses import dataclass
import re

from .integer_semantics import wrap_integer
from .diagnostics import UnsupportedJava


from .source_types import INTEGRAL, promoted, select_method, widening


@dataclass(frozen=True)
class Binding:
    name: str
    slot: int
    dtype: str = "int"


def integer_literal(node, allow_min=False):
    text = node.value
    dtype = "long" if text[-1:] in ("l", "L") else "int"
    width = 64 if dtype == "long" else 32
    text = text[:-1] if dtype == "long" else text
    if len(text) > 128:
        node.token.error("integer literal exceeds " + dtype + " range")
    patterns = ((r"0[xX][0-9a-fA-F](?:[0-9a-fA-F_]*[0-9a-fA-F])?", 16, 2),
                (r"0[bB][01](?:[01_]*[01])?", 2, 2),
                (r"0(?:[0-7_]*[0-7])", 8, 0),
                (r"(?:0|[1-9](?:[0-9_]*[0-9])?)", 10, 0))
    for pattern, base, prefix in patterns:
        if re.fullmatch(pattern, text):
            value = int(text[prefix:].replace("_", ""), base)
            maximum = (1 << (width - 1)) if base == 10 and allow_min else (1 << (width - 1 if base == 10 else width)) - 1
            if value > maximum:
                node.token.error("integer literal exceeds " + dtype + " range")
            return dtype, value if base == 10 else wrap_integer(value, width)
    node.token.error("invalid " + dtype + " literal")


class SourceAnalyzer:
    def __init__(self, program, project=None):
        self.program = program
        self.project = project
        self.methods = {}
        self.scopes = []
        self.loops = []

    def declare_methods(self):
        if self.program.name == "java/lang/Object":
            raise UnsupportedJava("bootstrap Object requires the shared runtime definition")
        for method in self.program.methods:
            key = (method.token.text, method.parameter_types)
            if not method.parameters and method.token.text in {
                    "hashCode", "getClass", "toString", "clone", "finalize", "notify", "notifyAll", "wait"}:
                method.token.error("static method conflicts with inherited java.lang.Object instance method")
            if method.token.text == "wait" and method.parameter_types in (("long",), ("long", "int")):
                method.token.error("static method conflicts with inherited java.lang.Object instance method")
            if key in self.methods:
                method.token.error("duplicate method signature (return type does not overload)")
            self.methods[key] = method

    def analyze(self):
        if not self.methods:
            self.declare_methods()
        for method in self.program.methods:
            self.method = method
            self.scopes, self.loops = [{}], []
            assigned = set()
            for parameter, dtype in zip(method.parameters, method.parameter_types):
                assigned.add(self.declare(parameter, dtype))
            outgoing = self.statement(method.body, assigned)
            if method.result != "void" and outgoing is not None:
                method.token.error("missing return in " + method.result + " method")
        return self.program

    def declare(self, token, dtype="int"):
        if any(token.text in scope for scope in self.scopes):
            token.error("local or parameter name is already in scope")
        binding = Binding(token.text, len(self.method.locals), dtype)
        self.method.locals.append(binding)
        if len(self.method.locals) > 4096:
            token.error("source method exceeds the local-variable budget", True)
        self.scopes[-1][token.text] = binding
        return binding

    def lookup(self, token):
        for scope in reversed(self.scopes):
            if token.text in scope:
                return scope[token.text]
        token.error("undefined local variable " + token.text)

    def require_int(self, node):
        if node.dtype not in INTEGRAL:
            node.token.error("integer value required, got " + str(node.dtype))

    def assignment_value(self, node, target):
        if target == "boolean":
            if node.dtype != "boolean":
                node.token.error("boolean value required")
        else:
            self.require_int(node)
        if not widening(node.dtype, target):
            node.token.error("incompatible assignment conversion from " + node.dtype + " to " + target)

    def condition(self, node, assigned):
        self.expression(node, assigned)
        if node.dtype != "boolean":
            node.token.error("Java condition requires boolean, not integer truth conversion")
        return self.truth_states(node, assigned)

    def truth_states(self, node, assigned):
        """JLS 16.1 boolean DA, separate from JLS 14 syntactic reachability.

        Assignment expressions are not admitted, so ordinary expressions keep
        their input state. Impossible outcomes vacuously assign only variables
        already declared at this point; later block declarations stay unassigned.
        """
        if node.constant is True:
            return set(assigned), set(self.method.locals)
        if node.constant is False:
            return set(self.method.locals), set(assigned)
        if node.kind == "group":
            return self.truth_states(node.children[0], assigned)
        if node.kind == "unary" and node.value == "!":
            yes, no = self.truth_states(node.children[0], assigned)
            return no, yes
        if node.kind == "binary" and node.value == "&&":
            left_true, left_false = self.truth_states(node.children[0], assigned)
            right_true, right_false = self.truth_states(node.children[1], left_true)
            return right_true, left_false & right_false
        if node.kind == "binary" and node.value == "||":
            left_true, left_false = self.truth_states(node.children[0], assigned)
            right_true, right_false = self.truth_states(node.children[1], left_false)
            return left_true & right_true, right_false
        return set(assigned), set(assigned)

    def expression(self, node, assigned):
        kind, op = node.kind, node.value
        if kind == "integer":
            node.dtype, node.constant = integer_literal(node)
            return
        if kind == "bool":
            node.dtype, node.constant = "boolean", op
            return
        if kind == "name":
            node.symbol = self.lookup(node.token)
            if node.symbol not in assigned:
                node.token.error("local variable is not definitely assigned: " + node.symbol.name)
            node.dtype = node.symbol.dtype
            return
        if kind == "call":
            for argument in node.children:
                self.expression(argument, assigned)
                if argument.dtype == "void":
                    argument.token.error("void value cannot be a method argument")
            method = (self.project.resolve_call(self, node) if self.project is not None
                      else select_method(self.methods, node))
            if method is None:
                node.token.error("unresolved same-class static method " + str(op))
            node.dtype, node.symbol = method.result, method
            return
        # Only an immediately negated decimal minimum may exceed the positive
        # literal limit. Parentheses do not inherit this lexical exception.
        if kind == "unary" and op == "-" and node.children[0].kind == "integer":
            child = node.children[0]
            spelling = child.value.replace("_", "")
            if spelling in ("2147483648", "9223372036854775808L", "9223372036854775808l"):
                child.dtype, child.constant = integer_literal(child, True)
                node.dtype, node.constant = child.dtype, -child.constant
                return
        if kind == "binary" and op in ("&&", "||"):
            self.expression(node.children[0], assigned)
            states = self.truth_states(node.children[0], assigned)
            self.expression(node.children[1], states[0 if op == "&&" else 1])
        else:
            for child in node.children:
                self.expression(child, assigned)
        first = node.children[0]
        if kind == "group":
            node.dtype, node.constant = first.dtype, first.constant
            return
        if kind == "cast":
            if op == "boolean":
                if first.dtype != "boolean":
                    node.token.error("boolean casts require boolean operands")
                node.dtype, node.constant = "boolean", first.constant
                return
            self.require_int(first)
            node.dtype = op
            if first.constant is not None:
                bits = 8 if op == "byte" else 16 if op in ("short", "char") else 64 if op == "long" else 32
                node.constant = (first.constant & 0xffff) if op == "char" else wrap_integer(first.constant, bits)
            return
        if kind == "unary":
            if op == "!":
                if first.dtype != "boolean":
                    node.token.error("logical negation requires boolean")
                node.dtype = "boolean"
                if first.constant is not None:
                    node.constant = not first.constant
            else:
                self.require_int(first)
                node.dtype = promoted(first.dtype)
                if first.constant is not None:
                    value = {"+": lambda x: x, "-": lambda x: -x, "~": lambda x: ~x}[op](first.constant)
                    node.constant = wrap_integer(value, 64 if node.dtype == "long" else 32)
            return
        if kind != "binary":
            raise AssertionError("untyped source expression " + kind)
        second = node.children[1]
        if op in ("/", "%"):
            node.token.error("integer division/remainder requires shared ArithmeticException support", True)
        if op in ("&&", "||"):
            if first.dtype != "boolean" or second.dtype != "boolean":
                node.token.error("conditional logical operators require boolean operands")
            node.dtype = "boolean"
        elif op in ("==", "!=") and first.dtype == second.dtype == "boolean":
            node.dtype = "boolean"
        elif op in ("&", "|", "^") and first.dtype == second.dtype == "boolean":
            node.dtype = "boolean"
        else:
            self.require_int(first)
            self.require_int(second)
            node.operation_type = promoted(first.dtype, second.dtype, shift=op in ("<<", ">>", ">>>"))
            node.dtype = "boolean" if op in ("==", "!=", "<", "<=", ">", ">=") else node.operation_type
        if first.constant is not None and second.constant is not None:
            a, b = first.constant, second.constant
            width = 64 if node.operation_type == "long" else 32
            wrap = lambda value: wrap_integer(value, width)
            operations = {"+": lambda: wrap(a + b), "-": lambda: wrap(a - b),
                          "*": lambda: wrap(a * b), "&": lambda: a & b, "|": lambda: a | b,
                          "^": lambda: a ^ b, "<<": lambda: wrap(a << (b & (width - 1))),
                          ">>": lambda: a >> (b & (width - 1)), ">>>": lambda: wrap((a & ((1 << width) - 1)) >> (b & (width - 1))),
                          "==": lambda: a == b, "!=": lambda: a != b, "<": lambda: a < b,
                          "<=": lambda: a <= b, ">": lambda: a > b, ">=": lambda: a >= b,
                          "&&": lambda: a and b, "||": lambda: a or b}
            node.constant = operations[op]()

    def statement(self, node, assigned):
        kind = node.kind
        assigned = set(assigned)
        if kind == "block":
            self.scopes.append({})
            for child in node.children:
                if assigned is None:
                    child.token.error("unreachable statement")
                assigned = self.statement(child, assigned)
            self.scopes.pop()
        elif kind == "var":
            node.symbol = self.declare(node.token, node.value)
            if node.children:
                self.expression(node.children[0], assigned)
                self.assignment_value(node.children[0], node.symbol.dtype)
                assigned.add(node.symbol)
        elif kind == "assign":
            node.symbol = self.lookup(node.token)
            if node.value != "=" and node.symbol not in assigned:
                node.token.error("local variable is not definitely assigned: " + node.symbol.name)
            for child in node.children:
                self.expression(child, assigned)
            dtype = node.symbol.dtype
            if node.value == "=":
                self.assignment_value(node.children[0], dtype)
            elif node.value in ("++", "--"):
                if dtype not in INTEGRAL:
                    node.token.error("increment/decrement requires an integer variable")
                node.operation_type = promoted(dtype)
            elif node.value in ("&=", "|=", "^=") and dtype == node.children[0].dtype == "boolean":
                node.operation_type = "boolean"
            else:
                if dtype not in INTEGRAL:
                    node.token.error("compound arithmetic requires an integer variable")
                self.require_int(node.children[0])
                node.operation_type = promoted(dtype, node.children[0].dtype, shift=node.value in ("<<=", ">>=", ">>>="))
            if node.value in ("/=", "%="):
                node.token.error("integer division/remainder requires shared ArithmeticException support", True)
            assigned.add(node.symbol)
        elif kind == "expression":
            self.expression(node.children[0], assigned)
        elif kind == "return":
            if self.method.result == "void":
                if node.children:
                    node.token.error("void method cannot return a value")
            else:
                if not node.children:
                    node.token.error(self.method.result + " method must return a value")
                self.expression(node.children[0], assigned)
                self.assignment_value(node.children[0], self.method.result)
            assigned = None
        elif kind == "if":
            condition, yes, no = node.children
            when_true, when_false = self.condition(condition, assigned)
            y = self.statement(yes, when_true)
            n = self.statement(no, when_false)
            # Syntactic reachability intentionally does not fold if(constant):
            # JLS 14.22 allows conditional compilation inside either branch.
            if y is None and n is None:
                assigned = None
            elif y is None:
                assigned = n
            elif n is None:
                assigned = y
            else:
                assigned = y & n
        elif kind in ("while", "for"):
            self.scopes.append({})
            if kind == "for":
                initial, condition, update, body = node.children
                assigned = self.statement(initial, assigned)
            else:
                condition, body = node.children
                update = None
            when_true, when_false = self.condition(condition, assigned)
            if condition.constant is False:
                body.token.error("unreachable loop body")
            loop = {"break": [], "continue": []}
            self.loops.append(loop)
            outgoing = self.statement(body, when_true)
            if update is not None:
                paths = loop["continue"] + ([outgoing] if outgoing is not None else [])
                state = set.intersection(*paths) if paths else set(self.method.locals)
                self.statement(update, state)
            self.loops.pop()
            paths = loop["break"] + ([] if condition.constant is True else [when_false])
            assigned = set.intersection(*paths) if paths else None
            self.scopes.pop()
        elif kind in ("break", "continue"):
            if not self.loops:
                node.token.error(kind + " requires an enclosing loop")
            self.loops[-1][kind].append(assigned)
            assigned = None
        elif kind != "empty":
            raise AssertionError("unknown source statement " + kind)
        node.completes = assigned is not None
        return assigned
