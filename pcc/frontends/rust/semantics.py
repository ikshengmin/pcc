"""Name resolution, monomorphic scalar inference, and Rust type checking."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .syntax import Enumeration, Expr, Function, Record, RustError, Token
from .types import SharedReference, TupleType

INTEGERS = {"i32", "i64", "u32", "u64"}
NEVER = "!"


@dataclass(eq=False)
class InferredInteger:
    bound: object = None


@dataclass
class Binding:
    name: str
    ty: object
    mutable: bool
    serial: int


def resolve(ty: object) -> object:
    if isinstance(ty, SharedReference):
        return ty
    if isinstance(ty, TupleType):
        return TupleType(tuple(resolve(item) for item in ty.elements))
    if isinstance(ty, InferredInteger):
        if ty.bound is None:
            ty.bound = "i32"
        ty.bound = resolve(ty.bound)
        return ty.bound
    return str(ty)


def _root(ty: object) -> object:
    while isinstance(ty, InferredInteger) and ty.bound is not None:
        ty = ty.bound
    return ty


def unify(left: object, right: object, token: Token) -> object:
    left, right = _root(left), _root(right)
    if left == NEVER:
        return right
    if right == NEVER:
        return left
    if left is right or left == right:
        return left
    if isinstance(left, TupleType) and isinstance(right, TupleType) and len(left.elements) == len(right.elements):
        return TupleType(tuple(unify(a, b, token) for a, b in zip(left.elements, right.elements)))
    if isinstance(left, InferredInteger):
        if isinstance(right, InferredInteger) or right in INTEGERS:
            left.bound = right
            return right
    elif isinstance(right, InferredInteger):
        return unify(right, left, token)
    raise token.error(f"type mismatch: expected {left}, found {right}")


def integer_literal(expr: Expr) -> tuple[int, object]:
    match = re.fullmatch(r"(0x[0-9a-fA-F_]+|0o[0-7_]+|0b[01_]+|[0-9][0-9_]*)(i32|i64|u32|u64)?", str(expr.value))
    if match is None:
        raise expr.token.error("invalid or unsupported integer literal (supported suffixes: i32, i64, u32, u64)")
    raw = match.group(1).replace("_", "")
    try:
        value = int(raw, 0 if raw.startswith(("0x", "0o", "0b")) else 10)
    except ValueError as exc:
        raise expr.token.error("invalid integer literal") from exc
    return value, match.group(2) or InferredInteger()


def bounds(ty: str) -> tuple[int, int]:
    bits = int(ty[1:])
    return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if ty[0] == "i" else (0, (1 << bits) - 1)


def cast_constant(value: int, ty: str) -> int:
    """Rust integer casts truncate modulo 2**width, then interpret signedness."""
    bits = int(ty[1:])
    value &= (1 << bits) - 1
    if ty.startswith("i") and value >= (1 << (bits - 1)):
        value -= 1 << bits
    return value


class Checker:
    def __init__(self, functions: list[Function], records: list[Record] = (), enums: list[Enumeration] = ()):
        self.functions = functions
        self.records: dict[str, Record] = {}
        for record in records:
            if record.token.text in self.records or record.token.text in INTEGERS | {"bool"}:
                raise record.token.error("duplicate or reserved struct type")
            self.records[record.token.text] = record
        self.enums: dict[str, Enumeration] = {}
        for enum in enums:
            name = enum.token.text
            if name in self.records or name in self.enums or name in INTEGERS | {"bool"}:
                raise enum.token.error("duplicate or reserved enum type")
            self.enums[name] = enum
        self.names: dict[str, Function] = {}
        self.scopes: list[dict[str, Binding]] = []
        self.serial = 0
        self.loops = 0
        self.result = "()"
        self.parameters: dict[str, list[Binding]] = {}
        self.unsafe_depth = 0

    def bind(self, token: Token, ty: object, mutable: bool) -> Binding:
        self.serial += 1
        binding = Binding(token.text, ty, mutable, self.serial)
        self.scopes[-1][token.text] = binding
        return binding

    def local(self, name: str) -> Binding | None:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    def fields(self, ty: object) -> tuple[object, ...]:
        if isinstance(ty, TupleType):
            return ty.elements
        if ty in self.records:
            return tuple(field_ty for _, field_ty in self.records[ty].fields)
        return ()

    def aggregate(self, ty: object) -> bool:
        return isinstance(ty, TupleType) or ty in self.records

    def copy_type(self, ty: object) -> bool:
        if isinstance(ty, SharedReference):
            return True
        if isinstance(ty, TupleType):
            return all(self.copy_type(item) for item in ty.elements)
        # Named Rust structs do NOT become Copy just because their fields are.
        return ty in INTEGERS | {"bool", "()"}

    def valid_type(self, ty: object, token: Token, stack: tuple[str, ...] = (), *, allow_reference=True) -> None:
        if ty in INTEGERS | {"bool", "()"} or ty in self.enums:
            return
        if isinstance(ty, SharedReference):
            if not allow_reference:
                raise token.error("reference returns and aggregate reference storage require lifetime analysis, which is not implemented")
            record = self.records.get(ty.referent)
            if record is None or not record.repr_c or not record.fields or any(field_ty not in INTEGERS for _, field_ty in record.fields):
                raise token.error("unsupported Rust type: shared references currently require a repr(C) record containing only i32/i64/u32/u64 fields")
            return
        if isinstance(ty, TupleType):
            for item in ty.elements:
                self.valid_type(item, token, stack, allow_reference=False)
            return
        if ty not in self.records:
            raise token.error(f"unsupported Rust type {ty!r}")
        if ty in stack:
            raise token.error("recursive aggregate has infinite size")
        record = self.records[ty]
        if not record.fields:
            raise record.token.error("zero-field structs are not implemented")
        names = set()
        for field, field_ty in record.fields:
            if field.text in names:
                raise field.error("duplicate struct field")
            names.add(field.text)
            self.valid_type(field_ty, field, stack + (ty,), allow_reference=False)

    def place(self, expr: Expr):
        if expr.kind == "group":
            return self.place(expr.children[0])
        if expr.kind == "name":
            return expr.binding, ()
        if expr.kind == "field":
            base = self.place(expr.children[0])
            if base is not None:
                return base[0], base[1] + (expr.value,)
        return None

    def through_reference(self, expr: Expr) -> bool:
        if expr.kind == "group":
            return self.through_reference(expr.children[0])
        if expr.kind == "field":
            return isinstance(expr.children[0].ty, SharedReference) or self.through_reference(expr.children[0])
        return False

    def check(self) -> None:
        from .patterns import check_enums

        check_enums(self)
        for name, record in self.records.items():
            self.valid_type(name, record.token)
        for fn in self.functions:
            self.valid_type(fn.result, fn.token, allow_reference=False)
            parameter_names = set()
            for token, ty, _ in fn.params:
                if token.text in parameter_names:
                    raise token.error(f"duplicate parameter {token.text!r}")
                parameter_names.add(token.text)
                self.valid_type(ty, token)
            name = fn.token.text
            if name in self.names:
                raise fn.token.error(f"duplicate function {name!r}")
            if name.startswith("__pcc_rust_"):
                raise fn.token.error("reserved PCC Rust symbol prefix")
            if fn.no_mangle and fn.abi != "C":
                raise fn.token.error("no_mangle is supported only on extern C function definitions")
            self.names[name] = fn
            if fn.abi == "C" and (any(ty not in INTEGERS and not isinstance(ty, SharedReference) for _, ty, _ in fn.params) or fn.result not in INTEGERS | {"()"}):
                raise fn.token.error("extern C currently supports integer/shared-reference parameters and integer/unit returns; aggregate ABI requires a separately qualified repr(C) shape")
        for fn in self.functions:
            if fn.body is None:
                continue
            self.scopes = [{}]
            self.result = fn.result
            self.unsafe_depth = int(fn.unsafe)
            params: list[Binding] = []
            for name, ty, mutable in fn.params:
                if name.text in self.scopes[0]:
                    raise name.error(f"duplicate parameter {name.text!r}")
                params.append(self.bind(name, ty, mutable))
            self.parameters[fn.token.text] = params
            unify(fn.result, self.expression(fn.body), fn.body.token)
            self.validate(fn.body)

    def expression(self, expr: Expr) -> object:
        kind, children = expr.kind, expr.children
        if kind == "integer":
            expr.value, ty = integer_literal(expr)
        elif kind == "group":
            ty = self.expression(children[0])
        elif kind == "bool":
            ty = "bool"
        elif kind == "variant":
            enum_name, variant = expr.value
            enum = self.enums.get(enum_name)
            if enum is None or variant not in enum.discriminants:
                raise expr.token.error("unknown enum variant")
            expr.value = enum.discriminants[variant]
            ty = enum_name
        elif kind in {"unit", "let", "discard", "statement"}:
            ty = "()"
            if kind == "let":
                annotation, mutable = expr.value
                init_ty = self.expression(children[0])
                if init_ty == NEVER:
                    raise expr.token.error("diverging let initializers are not implemented")
                if annotation is not None:
                    self.valid_type(annotation, expr.token)
                    unify(annotation, init_ty, expr.token)
                    init_ty = annotation
                expr.binding = self.bind(expr.token, init_ty, mutable)
            elif children:
                child_ty = self.expression(children[0])
                if kind == "statement":
                    unify("()", child_ty, expr.token)
                if child_ty == NEVER:
                    ty = NEVER
        elif kind == "tuple":
            elements = tuple(self.expression(child) for child in children)
            if NEVER in elements:
                raise expr.token.error("diverging tuple operands require never-type layout support, which is not implemented")
            if any(isinstance(item, SharedReference) for item in elements):
                raise expr.token.error("aggregate reference storage requires lifetime analysis, which is not implemented")
            ty = TupleType(elements)
        elif kind == "construct":
            record = self.records.get(expr.token.text)
            if record is None:
                raise expr.token.error(f"unknown struct {expr.token.text!r}")
            provided = set()
            indices = []
            names = [field.text for field, _ in record.fields]
            for field, child in zip(expr.value, children):
                if field.text not in names:
                    raise field.error("unknown struct field")
                if field.text in provided:
                    raise field.error("duplicate field initializer")
                provided.add(field.text)
                index = names.index(field.text)
                indices.append(index)
                unify(record.fields[index][1], self.expression(child), field)
            if provided != set(names):
                raise expr.token.error("missing struct field initializer")
            expr.value = indices
            ty = record.token.text
        elif kind == "field":
            base_ty = self.expression(children[0])
            if isinstance(base_ty, SharedReference):
                base_ty = base_ty.referent
            if isinstance(base_ty, TupleType):
                if not str(expr.value).isdecimal() or int(expr.value) >= len(base_ty.elements):
                    raise expr.token.error("unknown tuple field")
                index = int(expr.value)
                ty = base_ty.elements[index]
            elif base_ty in self.records:
                fields = self.records[base_ty].fields
                names = [field.text for field, _ in fields]
                if expr.value not in names:
                    raise expr.token.error("unknown struct field")
                index = names.index(expr.value)
                ty = fields[index][1]
            else:
                raise expr.token.error("field access requires a struct or tuple")
            expr.value = index
        elif kind == "name":
            binding = self.local(str(expr.value))
            if binding is None:
                raise expr.token.error(f"unknown local {expr.value!r}; function values are not implemented")
            expr.binding = binding
            ty = binding.ty
        elif kind == "block":
            self.scopes.append({})
            diverges = False
            for statement in children[:expr.value]:
                if self.expression(statement) == NEVER:
                    diverges = True
            if len(children) > expr.value:
                ty = self.expression(children[-1])
            else:
                ty = NEVER if diverges else "()"
            self.scopes.pop()
        elif kind == "unsafe":
            self.unsafe_depth += 1
            ty = self.expression(children[0])
            self.unsafe_depth -= 1
        elif kind == "call":
            callee = children[0]
            while callee.kind == "group":
                callee = callee.children[0]
            if callee.kind != "name" or self.local(str(callee.value)) is not None:
                raise callee.token.error("only unshadowed direct function calls are supported")
            fn = self.names.get(str(callee.value))
            if fn is None:
                raise callee.token.error(f"unknown function {callee.value!r}")
            if fn.unsafe and not self.unsafe_depth:
                raise expr.token.error(f"call to unsafe function {fn.token.text!r} requires an unsafe context")
            if len(children) - 1 != len(fn.params):
                raise expr.token.error(f"function {fn.token.text!r} expects {len(fn.params)} arguments")
            expr.binding = fn
            for arg, (_, param_ty, _) in zip(children[1:], fn.params):
                unify(param_ty, self.expression(arg), arg.token)
            ty = fn.result
        elif kind == "unary":
            ty = self.expression(children[0])
            literal = children[0]
            while literal.kind == "group":
                literal = literal.children[0]
            if expr.value == "-" and literal.kind == "integer":
                literal.binding = "negative-literal"
        elif kind == "binary":
            left = self.expression(children[0])
            right = self.expression(children[1])
            common = unify(left, right, expr.token)
            if expr.value in {"&&", "||"}:
                unify("bool", common, expr.token)
                ty = "bool"
            elif expr.value in {"==", "!=", "<", "<=", ">", ">="}:
                ty = "bool"
            else:
                ty = common
        elif kind == "assign":
            lhs = children[0]
            target = self.expression(lhs)
            place = self.place(lhs)
            if place is None:
                raise lhs.token.error("assignment target must be a local binding or field")
            if self.through_reference(lhs):
                raise lhs.token.error("cannot assign through a shared reference")
            if not place[0].mutable:
                raise lhs.token.error(f"cannot assign to immutable binding {place[0].name!r}")
            unify(target, self.expression(children[1]), expr.token)
            ty = "()"
        elif kind == "cast":
            self.expression(children[0])
            ty = expr.value
        elif kind == "if":
            unify("bool", self.expression(children[0]), children[0].token)
            yes = self.expression(children[1])
            no = self.expression(children[2]) if len(children) == 3 else "()"
            ty = unify(yes, no, expr.token)
        elif kind == "match":
            from .patterns import check_pattern

            scrutinee_ty = self.expression(children[0])
            ty = NEVER
            for pattern, arm in zip(expr.value, children[1:]):
                check_pattern(self, pattern, scrutinee_ty)
                # Each arm expression owns its lexical block scopes normally;
                # patterns in this slice bind no locals.
                ty = unify(ty, self.expression(arm), arm.token)
        elif kind == "while":
            unify("bool", self.expression(children[0]), children[0].token)
            self.loops += 1
            body = self.expression(children[1])
            self.loops -= 1
            unify("()", body, children[1].token)
            ty = "()"
        elif kind == "return":
            unify(self.result, self.expression(children[0]) if children else "()", expr.token)
            ty = NEVER
        elif kind in {"break", "continue"}:
            if not self.loops:
                raise expr.token.error(f"{kind} outside a loop")
            ty = NEVER
        else:
            raise expr.token.error(f"unsupported semantic node {kind}")
        expr.ty = ty
        return ty

    def validate(self, expr: Expr) -> int | None:
        # Resolve only after the complete function has contributed constraints:
        # `let x = 1; takes_i64(x)` must infer i64, not prematurely default i32.
        expr.ty = resolve(expr.ty)
        values = [self.validate(child) for child in expr.children if not (expr.kind == "call" and child is expr.children[0])]
        if isinstance(expr.binding, Binding):
            expr.binding.ty = resolve(expr.binding.ty)
        kind, op = expr.kind, expr.value
        if kind == "match":
            from .patterns import validate_match

            validate_match(self, expr)
        if kind == "variant":
            return expr.value
        if kind == "group":
            return values[0]
        if kind == "bool":
            return expr.value
        if kind == "block" and expr.value == 0 and values:
            return values[0]
        if kind == "integer":
            low, high = bounds(expr.ty)
            if expr.binding == "negative-literal" and expr.ty.startswith("i"):
                high += 1
            if not low <= op <= high:
                raise expr.token.error(f"integer literal out of range for {expr.ty}")
            return op
        if kind == "unary":
            operand_ty = expr.children[0].ty
            if op == "-" and operand_ty not in {"i32", "i64", NEVER}:
                raise expr.token.error("unary '-' requires a signed integer")
            if op == "!" and operand_ty not in INTEGERS | {"bool", NEVER}:
                raise expr.token.error("unary '!' requires bool or integer")
            if op == "-" and values[0] is not None:
                value = -values[0]
                if not bounds(expr.ty)[0] <= value <= bounds(expr.ty)[1]:
                    raise expr.token.error("constant integer overflow")
                return value
            if op == "!" and values[0] is not None:
                return not values[0] if expr.ty == "bool" else cast_constant(~values[0], expr.ty)
        if kind == "cast":
            if expr.ty not in INTEGERS or (expr.children[0].ty not in INTEGERS | {"bool"} and expr.children[0].ty not in self.enums):
                raise expr.token.error("supported casts are integer-to-integer, bool-to-integer and unit-only-enum-to-integer")
            if values[0] is not None:
                return cast_constant(int(values[0]), expr.ty)
        if kind == "binary":
            operand_ty = expr.children[0].ty
            allowed = INTEGERS | {"bool"} if op in {"&", "|", "^", "==", "!=", "<", "<=", ">", ">=", "&&", "||"} else INTEGERS
            if operand_ty not in allowed:
                raise expr.token.error(f"operator {op!r} does not accept {operand_ty}")
            if op in {"/", "%"}:
                divisor = values[1]
                if divisor is None or divisor == 0 or (operand_ty.startswith("i") and divisor == -1):
                    raise expr.token.error("panic-capable division/remainder is unsupported; divisor must be a nonzero constant, not signed -1")
            if op in {"+", "-", "*"} and all(value is not None for value in values):
                left, right = values
                value = left + right if op == "+" else left - right if op == "-" else left * right
                if not bounds(operand_ty)[0] <= value <= bounds(operand_ty)[1]:
                    raise expr.token.error("constant integer overflow")
                return value
            if all(value is not None for value in values):
                left, right = values
                if op in {"/", "%"}:
                    quotient = abs(left) // abs(right)
                    if (left < 0) != (right < 0):
                        quotient = -quotient
                    return quotient if op == "/" else left - quotient * right
                if op in {"&", "|", "^"}:
                    return left & right if op == "&" else left | right if op == "|" else left ^ right
                if op in {"&&", "||"}:
                    return left and right if op == "&&" else left or right
                if op in {"==", "!=", "<", "<=", ">", ">="}:
                    return {"==": left == right, "!=": left != right, "<": left < right,
                            "<=": left <= right, ">": left > right, ">=": left >= right}[op]
        if kind == "assign" and op != "=" and expr.children[0].ty not in INTEGERS:
            raise expr.token.error(f"operator {op!r} requires integer operands")
        return None
