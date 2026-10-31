"""Java source typing, lexical bindings, reachability and definite assignment.

This pass creates typed source AST, not JVM instructions. The scalar
subset has no assignment expressions, blank final locals or reference values; those
boundaries keep its structured definite-assignment rules explicit.
"""
from dataclasses import dataclass
import re

from .integer_semantics import evaluate_division, wrap_integer
from .diagnostics import UnsupportedJava


from .source_types import INTEGRAL, conditional_type, promoted, select_method, widening


@dataclass(frozen=True)
class Binding:
    name: str
    slot: int
    dtype: str = "int"
    is_final: bool = False


def character_literal(node):
    text = node.value[1:-1]
    if text.startswith("\\"):
        escapes = {"b": 8, "t": 9, "n": 10, "f": 12, "r": 13, "s": 32,
                   '"': 34, "'": 39, "\\": 92}
        value = int(text[1:], 8) if text[1] in "01234567" else escapes[text[1]]
    else:
        value = ord(text)
    if value > 0xffff:
        node.token.error("character literal must represent one UTF-16 code unit")
    return value


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
        self.fields = {}
        self.field_states = {}
        self.current_field = None
        self.method = None
        self.scopes = []
        self.controls = []

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

    def declare_fields(self):
        for field in self.program.fields:
            if field.token.text in self.fields:
                field.token.error("duplicate field name " + field.token.text)
            self.fields[field.token.text] = field

    def evaluate_field(self, field):
        state = self.field_states.get(field.token.text)
        if state == "complete":
            return field.constant
        if state == "evaluating":
            field.token.error("cyclic field initialization requires the shared Java initialization contract", True)
        self.field_states[field.token.text] = "evaluating"
        previous = self.current_field
        self.current_field = field
        try:
            self.expression(field.initializer, set())
            self.assignment_value(field.initializer, field.dtype)
            if field.initializer.constant is None:
                field.token.error("nonconstant static initialization requires the shared Java initialization contract", True)
            field.constant = field.initializer.constant
            self.field_states[field.token.text] = "complete"
            return field.constant
        finally:
            self.current_field = previous

    def analyze(self):
        if not self.methods:
            self.declare_methods()
        for method in self.program.methods:
            self.method = method
            self.scopes, self.controls = [{}], []
            self.constant_locals = {}
            assigned = set()
            for parameter, dtype, is_final in zip(method.parameters, method.parameter_types, method.parameter_final):
                assigned.add(self.declare(parameter, dtype, is_final))
            outgoing = self.statement(method.body, assigned)
            if method.result != "void" and outgoing is not None:
                method.token.error("missing return in " + method.result + " method")
        return self.program

    def declare(self, token, dtype="int", is_final=False):
        if any(token.text in scope for scope in self.scopes):
            token.error("local or parameter name is already in scope")
        binding = Binding(token.text, len(self.method.locals), dtype, is_final)
        self.method.locals.append(binding)
        if len(self.method.locals) > 4096:
            token.error("source method exceeds the local-variable budget", True)
        self.scopes[-1][token.text] = binding
        return binding

    def lookup(self, token):
        for scope in reversed(self.scopes):
            if token.text in scope:
                return scope[token.text]
        if token.text in self.fields:
            token.error("cannot assign a final constant field " + token.text)
        token.error("undefined local variable " + token.text)

    def require_int(self, node):
        if node.dtype not in INTEGRAL:
            node.token.error("integer value required, got " + str(node.dtype))

    def require_nonzero_constant_divisor(self, node):
        if node.constant is None or node.constant == 0:
            node.token.error("nonconstant or zero integer divisor requires shared ArithmeticException support", True)

    def assignment_value(self, node, target):
        if target == "boolean":
            if node.dtype != "boolean":
                node.token.error("boolean value required")
        else:
            self.require_int(node)
        bounds = {"byte": (-128, 127), "short": (-32768, 32767), "char": (0, 65535)}
        narrow_constant = (target in bounds and node.dtype in {"byte", "short", "char", "int"}
                           and node.constant is not None and bounds[target][0] <= node.constant <= bounds[target][1])
        if not widening(node.dtype, target) and not narrow_constant:
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
            return set(assigned), set(self.method.locals if self.method else ())
        if node.constant is False:
            return set(self.method.locals if self.method else ()), set(assigned)
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
        if node.kind == "conditional":
            condition, yes, no = node.children
            when_true, when_false = self.truth_states(condition, assigned)
            yes_true, yes_false = self.truth_states(yes, when_true)
            no_true, no_false = self.truth_states(no, when_false)
            return yes_true & no_true, yes_false & no_false
        return set(assigned), set(assigned)

    def expression(self, node, assigned):
        kind, op = node.kind, node.value
        if kind == "integer":
            node.dtype, node.constant = integer_literal(node)
            return
        if kind == "bool":
            node.dtype, node.constant = "boolean", op
            return
        if kind == "char":
            node.dtype, node.constant = "char", character_literal(node)
            return
        if kind == "name":
            if "." not in op:
                for scope in reversed(self.scopes):
                    if op in scope:
                        node.symbol = scope[op]
                        if node.symbol not in assigned:
                            node.token.error("local variable is not definitely assigned: " + node.symbol.name)
                        node.dtype = node.symbol.dtype
                        node.constant = self.constant_locals.get(node.symbol)
                        return
            field = self.project.resolve_field(self, node)
            if (self.current_field is not None and "." not in op and field.owner == self.program.name
                    and field.ordinal >= self.current_field.ordinal):
                node.token.error("illegal forward or self reference in a static field initializer")
            node.symbol, node.dtype = field, field.dtype
            node.constant = self.project.analyzers[field.owner].evaluate_field(field)
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
        if kind == "conditional":
            condition, yes, no = node.children
            when_true, when_false = self.condition(condition, assigned)
            self.expression(yes, when_true)
            self.expression(no, when_false)
            node.dtype = conditional_type(yes, no, node.token)
            # Both arms must be constant expressions, even when the condition
            # happens to choose a constant arm. This is not value propagation.
            if all(child.constant is not None for child in node.children):
                node.constant = yes.constant if condition.constant else no.constant
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
        if op in ("/", "%"):
            self.require_nonzero_constant_divisor(second)
        if first.constant is not None and second.constant is not None:
            a, b = first.constant, second.constant
            width = 64 if node.operation_type == "long" else 32
            wrap = lambda value: wrap_integer(value, width)
            operations = {"+": lambda: wrap(a + b), "-": lambda: wrap(a - b),
                          "/": lambda: evaluate_division("/", a, b, width),
                          "%": lambda: evaluate_division("%", a, b, width),
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
            node.symbol = self.declare(node.token, node.value, node.is_final)
            if node.children:
                self.expression(node.children[0], assigned)
                self.assignment_value(node.children[0], node.symbol.dtype)
                assigned.add(node.symbol)
                if node.is_final and node.children[0].constant is not None:
                    self.constant_locals[node.symbol] = node.children[0].constant
        elif kind == "assign":
            node.symbol = self.lookup(node.token)
            if node.symbol.is_final:
                node.token.error("cannot assign an initialized final local or final parameter: " + node.symbol.name)
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
                self.require_nonzero_constant_divisor(node.children[0])
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
        elif kind == "switch":
            selector, *groups = node.children
            self.expression(selector, assigned)
            if selector.dtype not in {"byte", "short", "char", "int"}:
                selector.token.error("switch selector requires byte, short, char or int")
            self.scopes.append({})
            context = {"break": []}
            self.controls.append(context)
            values, has_default = set(), False
            outgoing, rule_exits = None, []
            for group in groups:
                for label in group.value:
                    if label.kind == "default":
                        if has_default:
                            label.token.error("duplicate default label")
                        has_default = True
                    else:
                        expression = label.children[0]
                        # Constant-label reads still require definite assignment
                        # at dispatch; a preceding group's initializer may be
                        # skipped even when the referenced variable is final.
                        self.expression(expression, assigned)
                        self.assignment_value(expression, selector.dtype)
                        if expression.constant is None:
                            expression.token.error("case label requires a constant expression")
                        label.constant = expression.constant
                        if label.constant in values:
                            label.token.error("duplicate case value")
                        values.add(label.constant)
                state = (assigned & outgoing if node.value == "colon" and outgoing is not None
                         else set(assigned))
                for child in group.children:
                    if state is None:
                        child.token.error("unreachable statement")
                    state = self.statement(child, state)
                outgoing = state
                group.completes = state is not None
                if node.value == "arrow" and state is not None:
                    rule_exits.append(state)
            self.controls.pop()
            self.scopes.pop()
            paths = context["break"] + rule_exits
            if node.value == "colon" and outgoing is not None:
                paths.append(outgoing)
            if not has_default:
                paths.append(assigned)
            assigned = set.intersection(*paths) if paths else None
        elif kind == "do":
            condition, body = node.children
            loop = {"break": [], "continue": []}
            self.controls.append(loop)
            outgoing = self.statement(body, assigned)
            self.controls.pop()
            test_paths = loop["continue"] + ([outgoing] if outgoing is not None else [])
            test_state = set.intersection(*test_paths) if test_paths else set(self.method.locals)
            _, when_false = self.condition(condition, test_state)
            paths = loop["break"] + ([when_false] if test_paths and condition.constant is not True else [])
            assigned = set.intersection(*paths) if paths else None
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
            self.controls.append(loop)
            outgoing = self.statement(body, when_true)
            if update is not None:
                paths = loop["continue"] + ([outgoing] if outgoing is not None else [])
                state = set.intersection(*paths) if paths else set(self.method.locals)
                self.statement(update, state)
            self.controls.pop()
            paths = loop["break"] + ([] if condition.constant is True else [when_false])
            assigned = set.intersection(*paths) if paths else None
            self.scopes.pop()
        elif kind in ("break", "continue"):
            target = next((context for context in reversed(self.controls) if kind in context), None)
            if target is None:
                node.token.error(kind + " requires an enclosing loop" + (" or switch" if kind == "break" else ""))
            target[kind].append(assigned)
            assigned = None
        elif kind != "empty":
            raise AssertionError("unknown source statement " + kind)
        node.completes = assigned is not None
        return assigned
