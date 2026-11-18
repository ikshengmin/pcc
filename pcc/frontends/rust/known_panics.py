"""Owned `arithmetic_overflow` lint: constant overflow rustc rejects at build time.

rustc's deny-by-default arithmetic_overflow lint (its KnownPanicsLint MIR pass)
rejects an integer `+`, `-`, `*` or negation whose operands are known
constants and whose result does not fit the type, and a shift by a known
amount whose unsigned bit pattern is at least the shifted width (whatever the
shifted value), even when the operands are locals. Known `&`, `|`, `^`, `!`
and shift results propagate. It runs when a crate is built, not in a metadata
check, so the
external check never saw it; PCC owns it here for every admission mode.

The propagation follows the pass's reach, measured against rustc 1.92.0
builds of original programs (tests/rust/test_rust_known_panics.py):
- a local assigned exactly once keeps its known value everywhere;
- a local assigned more than once is known only until the next basic-block
  boundary: a call, an array index (bounds check), a branch or loop entry or
  join, a short-circuit operator, or a division/remainder (zero check);
- tuple and struct values keep known fields, casts wrap like Rust casts and
  match bindings take known parts of a known scrutinee;
- an `if` or `while` on a known condition visits only what it can reach, and
  a match on a known scrutinee skips arms that cannot match and every arm
  after one that must (unless guarded).
Values computed before a boundary in one expression are temporaries assigned
once, so they stay known. Generic bodies are evaluated too; values of
type-parameter type are never known. A local whose address is taken anywhere
in the body (a borrow not through a reference, an autoref receiver, or the
fake borrow a guarded match takes of its scrutinee) is never propagated, as in
the pass's CanConstProp (indirect mutation cannot be reasoned about).
"""
from __future__ import annotations

from .semantics import INTEGERS, SHIFTS, bounds, cast_constant, shift_overflows, shift_value
from .types import TupleType

UNKNOWN = object()
BOUNDARY_KINDS = frozenset({"call", "method_call", "index"})


def borrowed_locals(checker, body):
    """Serials of locals whose address is taken: rustc never propagates them."""
    borrowed = set()
    pending = [body]
    while pending:
        expr = pending.pop()
        place = None
        if expr.kind == "borrow" and not checker.through_reference(expr.children[0]):
            place = checker.place(expr.children[0])
        elif expr.kind == "match" and any(arm.kind == "match_guard" for arm in expr.children[1:]):
            if not checker.through_reference(expr.children[0]):
                place = checker.place(expr.children[0])  # guards fake-borrow the scrutinee
        if place is not None:
            borrowed.add(place[0].serial)
        pending.extend(expr.children)
    return borrowed


def assignment_counts(body):
    """How many times each local binding is assigned, by binding serial."""
    from .patterns import pattern_bindings
    counts = {}
    pending = [body]
    while pending:
        expr = pending.pop()
        if expr.kind == "let" and expr.binding is not None:
            counts[expr.binding.serial] = counts.get(expr.binding.serial, 0) + 1
        patterns = (expr.value if expr.kind == "match" else (expr.value[0],) if expr.kind == "pattern_let"
                    else (expr.value,) if expr.kind == "while_let" else ())
        for pattern in patterns:
            for binding in pattern_bindings(pattern):
                counts[binding.serial] = counts.get(binding.serial, 0) + 1
        if expr.kind == "assign":
            target = expr.children[0]
            while target.kind in {"field", "group", "index"}:
                target = target.children[0]
            if target.kind == "name" and target.binding is not None:
                counts[target.binding.serial] = counts.get(target.binding.serial, 0) + 2
        pending.extend(expr.children)
    return counts


class KnownPanics:
    def __init__(self, checker):
        self.checker = checker
        self.pointer_bits = checker.pointer_bits

    def check(self, functions):
        for function in functions:
            if function.body is None:
                continue
            self.once = {serial for serial, count in assignment_counts(function.body).items() if count == 1}
            self.borrowed = borrowed_locals(self.checker, function.body)
            self.env = {}
            self.value(function.body)

    def boundary(self):
        """A basic block ends: locals assigned more than once are forgotten."""
        self.env = {serial: value for serial, value in self.env.items() if serial in self.once}

    def integer(self, value, ty):
        return value if type(value) is int and isinstance(ty, str) and ty in INTEGERS else UNKNOWN

    def overflow(self, expr, result):
        low, high = bounds(expr.ty, self.pointer_bits)
        if not low <= result <= high:
            raise expr.token.error("this arithmetic operation will overflow (rustc arithmetic_overflow)")
        return result

    def bind(self, binding, value):
        if binding is not None and binding.serial not in self.borrowed:
            self.env[binding.serial] = value

    def value(self, expr):
        kind, children = expr.kind, expr.children
        if kind == "integer":
            return expr.value if type(expr.value) is int else UNKNOWN
        if kind in {"bool", "character"}:
            return expr.value
        if kind == "group":
            return self.value(children[0])
        if kind == "name":
            return self.env.get(expr.binding.serial, UNKNOWN) if expr.binding is not None else UNKNOWN
        if kind == "let":
            value = self.value(children[0]) if children else UNKNOWN
            self.bind(expr.binding, value)
            return UNKNOWN
        if kind == "pattern_let":
            value = self.value(children[0])
            self.bind_pattern(expr.value[0], value)
            return UNKNOWN
        if kind == "assign":
            target = children[0]
            if expr.value == "=":
                value = self.value(children[1])
            else:
                current = self.value(target)
                operand = self.value(children[1])
                op = expr.value[:-1]
                if op in SHIFTS:
                    value = self.shift(expr, op, current, operand, target.ty, children[1].ty)
                elif op in {"+", "-", "*"}:
                    value = self.arithmetic(expr, op, current, operand, target.ty)
                elif op in {"&", "|", "^"}:
                    value = self.bitwise(op, current, operand)
                else:
                    self.boundary()  # `/=` and `%=`: the divisor check ends the block
                    value = UNKNOWN
            if target.kind == "name" and target.binding is not None:
                if target.binding.serial not in self.borrowed:
                    self.env[target.binding.serial] = value
            else:
                root = target
                while root.kind in {"field", "group", "index"}:
                    root = root.children[0]
                if root.kind == "name" and root.binding is not None:
                    self.env.pop(root.binding.serial, None)
            return UNKNOWN
        if kind == "tuple":
            return {index: self.value(child) for index, child in enumerate(children)}
        if kind == "construct":
            values = [self.value(child) for child in children]
            if isinstance(expr.value, (list, tuple)) and len(expr.value) == len(values):
                return dict(zip(expr.value, values))
            return UNKNOWN
        if kind == "field":
            base = self.value(children[0])
            return base.get(expr.value, UNKNOWN) if isinstance(base, dict) else UNKNOWN
        if kind == "cast":
            value = self.value(children[0])
            from .types import RUST_CHAR, RUST_U8
            if expr.ty == RUST_CHAR and children[0].ty in {RUST_CHAR, RUST_U8}:
                return value
            if type(value) is bool:
                value = int(value)
            if type(value) is int and isinstance(expr.ty, str) and expr.ty in INTEGERS:
                return cast_constant(value, expr.ty, self.pointer_bits)
            return UNKNOWN
        if kind == "unary":
            value = self.value(children[0])
            if expr.value == "-" and type(value) is int:
                return self.overflow(expr, -value)
            if expr.value == "!" and type(value) is bool:
                return not value
            if expr.value == "!" and self.integer(value, expr.ty) is not UNKNOWN:
                return cast_constant(~value, expr.ty, self.pointer_bits)
            return UNKNOWN
        if kind == "binary":
            return self.binary(expr)
        if kind == "block":
            result = UNKNOWN
            for child in children:
                result = self.value(child)
            # value is the statement count; one more child is the tail expression.
            return result if len(children) > expr.value else UNKNOWN
        if kind == "if":
            condition = self.value(children[0])
            self.boundary()
            if type(condition) is bool:
                taken = children[1] if condition else (children[2] if len(children) == 3 else None)
                result = self.value(taken) if taken is not None else UNKNOWN
            else:
                for branch in children[1:]:
                    self.value(branch)
                    self.boundary()
                result = UNKNOWN
            self.boundary()
            return result
        if kind == "match":
            scrutinee = self.value(children[0])
            self.boundary()
            for pattern, arm in zip(expr.value, children[1:]):
                matched = self.matches(pattern, scrutinee)
                if matched is False:
                    continue
                self.bind_pattern(pattern, scrutinee)
                self.value(arm)
                self.boundary()
                if matched is True and arm.kind != "match_guard":
                    break
            return UNKNOWN
        if kind == "match_guard":
            self.value(children[0])
            self.boundary()
            self.value(children[1])
            return UNKNOWN
        if kind == "while":
            self.boundary()
            if self.value(children[0]) is not False:
                self.boundary()
                self.value(children[1])
            self.boundary()
            return UNKNOWN
        if kind in {"for_range", "for_range_inclusive", "loop", "while_let", "for"}:
            self.boundary()
            for child in children:
                self.value(child)
                self.boundary()
            return UNKNOWN
        for child in children[1:] if kind == "call" else children:
            self.value(child)
        if kind in BOUNDARY_KINDS:
            self.boundary()
        return UNKNOWN

    def binary(self, expr):
        op = expr.value
        left = self.value(expr.children[0])
        if op in {"&&", "||"}:
            self.boundary()
            self.value(expr.children[1])
            self.boundary()
            return UNKNOWN
        right = self.value(expr.children[1])
        if op in {"/", "%"}:
            self.boundary()
            return UNKNOWN
        if op in {"+", "-", "*"}:
            return self.arithmetic(expr, op, left, right, expr.ty)
        if op in SHIFTS:
            return self.shift(expr, op, left, right, expr.ty, expr.children[1].ty)
        if op in {"&", "|", "^"}:
            return self.bitwise(op, left, right)
        if type(left) is int and type(right) is int:
            if op in {"==", "!=", "<", "<=", ">", ">="}:
                return {"==": left == right, "!=": left != right, "<": left < right, "<=": left <= right,
                        ">": left > right, ">=": left >= right}[op]
        return UNKNOWN

    def arithmetic(self, expr, op, left, right, ty):
        left, right = self.integer(left, ty), self.integer(right, ty)
        if left is UNKNOWN or right is UNKNOWN:
            return UNKNOWN
        result = left + right if op == "+" else left - right if op == "-" else left * right
        low, high = bounds(ty, self.pointer_bits)
        if not low <= result <= high:
            raise expr.token.error("this arithmetic operation will overflow (rustc arithmetic_overflow)")
        return result

    def bitwise(self, op, left, right):
        """In-range operands give an in-range result, signed ones included."""
        if type(left) is not type(right) or type(left) not in (int, bool):
            return UNKNOWN
        return left & right if op == "&" else left | right if op == "|" else left ^ right

    def shift(self, expr, op, left, right, ty, amount_ty):
        """A known amount at least the width overflows, whatever the shifted
        value; with both known, the result propagates."""
        right = self.integer(right, amount_ty)
        if right is UNKNOWN:
            return UNKNOWN
        if shift_overflows(right, amount_ty, ty, self.pointer_bits):
            raise expr.token.error("this arithmetic operation will overflow (rustc arithmetic_overflow)")
        left = self.integer(left, ty)
        return UNKNOWN if left is UNKNOWN else shift_value(op, left, right, ty, self.pointer_bits)

    def matches(self, pattern, value):
        """True, False, or None when a known value's match is not decided here."""
        kind = pattern.kind
        if kind in {"wildcard", "binding"}:
            return True
        if kind == "at":
            return self.matches(pattern.children[0], value)
        if kind == "literal":
            return pattern.value == value if type(value) in (int, bool) and type(pattern.value) in (int, bool) else None
        if kind == "tuple" and isinstance(value, dict):
            results = [self.matches(child, value.get(index, UNKNOWN)) for index, child in enumerate(pattern.children)]
            return False if False in results else True if all(results) else None
        if kind == "or":
            results = [self.matches(option, value) for option in pattern.children]
            return True if True in results else False if all(result is False for result in results) else None
        return None

    def bind_pattern(self, pattern, value):
        kind = pattern.kind
        if kind in {"binding", "at"}:
            self.bind(pattern.binding, value)
            if kind == "at":
                self.bind_pattern(pattern.children[0], value)
            return
        if kind == "tuple" and isinstance(pattern.ty, TupleType):
            for index, child in enumerate(pattern.children):
                self.bind_pattern(child, value.get(index, UNKNOWN) if isinstance(value, dict) else UNKNOWN)
            return
        for child in pattern.children:
            self.bind_pattern(child, UNKNOWN)
