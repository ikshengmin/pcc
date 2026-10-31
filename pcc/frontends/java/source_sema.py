"""Java source typing, lexical bindings, reachability and definite assignment.

This pass creates typed source AST, not JVM instructions. The initial scalar
subset has no assignment expressions, final locals or reference values; those
boundaries keep its structured definite-assignment rules explicit.
"""
from dataclasses import dataclass
import re

from .integer_semantics import wrap_i32
from .diagnostics import UnsupportedJava


INTEGRAL = frozenset(("int", "byte", "short", "char"))


@dataclass(frozen=True)
class Binding:
    name: str
    slot: int


def integer_literal(node, allow_min=False):
    text = node.value
    if text[-1:] in ("l", "L"):
        node.token.error("long literals are not implemented in the int source slice", True)
    if len(text) > 128:
        node.token.error("integer literal exceeds int range")
    patterns = ((r"0[xX][0-9a-fA-F](?:[0-9a-fA-F_]*[0-9a-fA-F])?", 16, 2),
                (r"0[bB][01](?:[01_]*[01])?", 2, 2),
                (r"0(?:[0-7_]*[0-7])", 8, 0),
                (r"(?:0|[1-9](?:[0-9_]*[0-9])?)", 10, 0))
    for pattern, base, prefix in patterns:
        if re.fullmatch(pattern, text):
            value = int(text[prefix:].replace("_", ""), base)
            maximum = (1 << 31) if base == 10 and allow_min else (1 << (31 if base == 10 else 32)) - 1
            if value > maximum:
                node.token.error("integer literal exceeds int range")
            return value if base == 10 else wrap_i32(value)
    node.token.error("invalid int literal")


class SourceAnalyzer:
    def __init__(self, program):
        self.program = program
        self.methods = {}
        self.scopes = []
        self.loops = []

    def analyze(self):
        if self.program.name == "java/lang/Object":
            raise UnsupportedJava("bootstrap Object requires the shared runtime definition")
        for method in self.program.methods:
            key = (method.token.text, len(method.parameters))
            if not method.parameters and method.token.text in {
                    "hashCode", "getClass", "toString", "clone", "finalize", "notify", "notifyAll", "wait"}:
                method.token.error("static method conflicts with inherited java.lang.Object instance method")
            if key in self.methods:
                method.token.error("duplicate method signature (return type does not overload)")
            self.methods[key] = method
        for method in self.program.methods:
            self.method = method
            self.scopes, self.loops = [{}], []
            assigned = set()
            for parameter in method.parameters:
                assigned.add(self.declare(parameter))
            outgoing = self.statement(method.body, assigned)
            if method.result == "int" and outgoing is not None:
                method.token.error("missing return in int method")
        return self.program

    def declare(self, token):
        if any(token.text in scope for scope in self.scopes):
            token.error("local or parameter name is already in scope")
        binding = Binding(token.text, len(self.method.locals))
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
            node.dtype, node.constant = "int", integer_literal(node)
            return
        if kind == "bool":
            node.dtype, node.constant = "boolean", op
            return
        if kind == "name":
            node.symbol = self.lookup(node.token)
            if node.symbol not in assigned:
                node.token.error("local variable is not definitely assigned: " + node.symbol.name)
            node.dtype = "int"
            return
        if kind == "call":
            method = self.methods.get((op, len(node.children)))
            if method is None:
                node.token.error("unresolved same-class static method " + str(op))
            for argument in node.children:
                self.expression(argument, assigned)
                self.require_int(argument)
            node.dtype, node.symbol = method.result, method
            return
        # JLS 3.10.1 permits decimal 2147483648 only as the immediate operand
        # of unary minus, not a grouped positive literal or general value.
        if (kind == "unary" and op == "-" and node.children[0].kind == "integer"
                and node.children[0].value.replace("_", "") == "2147483648"):
            node.children[0].dtype, node.children[0].constant = "int", integer_literal(node.children[0], True)
            node.dtype, node.constant = "int", -2147483648
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
            self.require_int(first)
            node.dtype = op
            if first.constant is not None:
                bits = 8 if op == "byte" else 16 if op in ("short", "char") else 32
                value = first.constant & ((1 << bits) - 1)
                node.constant = value if op == "char" or value < (1 << (bits - 1)) else value - (1 << bits)
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
                node.dtype = "int"
                if first.constant is not None:
                    node.constant = wrap_i32({"+": lambda x: x, "-": lambda x: -x, "~": lambda x: ~x}[op](first.constant))
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
            node.dtype = "boolean" if op in ("==", "!=", "<", "<=", ">", ">=") else "int"
        if first.constant is not None and second.constant is not None:
            a, b = first.constant, second.constant
            operations = {"+": lambda: wrap_i32(a + b), "-": lambda: wrap_i32(a - b),
                          "*": lambda: wrap_i32(a * b), "&": lambda: a & b, "|": lambda: a | b,
                          "^": lambda: a ^ b, "<<": lambda: wrap_i32(a << (b & 31)),
                          ">>": lambda: a >> (b & 31), ">>>": lambda: wrap_i32((a & 0xffffffff) >> (b & 31)),
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
            node.symbol = self.declare(node.token)
            if node.children:
                self.expression(node.children[0], assigned)
                self.require_int(node.children[0])
                assigned.add(node.symbol)
        elif kind == "assign":
            node.symbol = self.lookup(node.token)
            if node.value != "=" and node.symbol not in assigned:
                node.token.error("local variable is not definitely assigned: " + node.symbol.name)
            for child in node.children:
                self.expression(child, assigned)
                self.require_int(child)
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
                    node.token.error("int method must return a value")
                self.expression(node.children[0], assigned)
                self.require_int(node.children[0])
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
