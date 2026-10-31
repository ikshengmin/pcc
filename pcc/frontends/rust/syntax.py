"""A Rust token stream and recursive-descent/Pratt parser, not a translator."""
from __future__ import annotations

from dataclasses import dataclass, field

from .types import SharedReference, TupleType


class RustError(ValueError):
    """A located syntax, type, or unsupported-capability diagnostic."""


@dataclass(frozen=True)
class Token:
    text: str
    kind: str
    line: int
    column: int

    def error(self, message: str) -> RustError:
        return RustError(f"{self.line}:{self.column}: {message}")


# Rust uses Unicode Pattern_White_Space, not Python str.isspace().
_WHITESPACE = "\t\n\v\f\r \u0085\u200e\u200f\u2028\u2029"
_KEYWORDS = set("as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while yield abstract become box do final macro override priv typeof unsized virtual try union".split())
_OPERATORS = ("<<=", ">>=", "->", "::", "==", "!=", "<=", ">=", "&&", "||", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<", ">>", "..=", "..")


def lex(source: str) -> list[Token]:
    tokens: list[Token] = []
    i, line, col = 0, 1, 1

    def advance(end: int) -> None:
        nonlocal i, line, col
        text = source[i:end]
        count = text.count("\n")
        col = len(text.rsplit("\n", 1)[-1]) + 1 if count else col + len(text)
        line += count
        i = end

    while i < len(source):
        start, ln, column = i, line, col
        char = source[i]
        if char in _WHITESPACE:
            advance(i + 1)
            continue
        # Documentation comments are attributes, not whitespace. Until the
        # attribute grammar lands, reject them rather than silently accepting
        # invalid attachment positions. Four slashes and /***/ remain comments.
        if (source.startswith("//!", i)
                or (source.startswith("///", i) and not source.startswith("////", i))
                or source.startswith("/*!", i)
                or (source.startswith("/**", i)
                    and i + 3 < len(source) and source[i + 3] not in "*/")):
            raise Token(source[i:i + 3], "", ln, column).error(
                "Rust documentation attributes are not implemented"
            )
        if source.startswith("//", i):
            end = source.find("\n", i)
            advance(len(source) if end < 0 else end)
            continue
        if source.startswith("/*", i):
            depth, end = 1, i + 2
            while depth and end < len(source):
                if source.startswith("/*", end):
                    depth += 1
                    end += 2
                elif source.startswith("*/", end):
                    depth -= 1
                    end += 2
                else:
                    end += 1
            if depth:
                raise Token("/*", "", ln, column).error("unterminated block comment")
            advance(end)
            continue
        if char.isascii() and (char.isalpha() or char == "_"):
            end = i + 1
            while end < len(source) and source[end].isascii() and (source[end].isalnum() or source[end] == "_"):
                end += 1
            word = source[i:end]
            tokens.append(Token(word, "keyword" if word in _KEYWORDS else "name", ln, column))
            advance(end)
            continue
        if char.isascii() and char.isdigit():
            end = i + 1
            while end < len(source) and source[end].isascii() and (source[end].isalnum() or source[end] == "_"):
                end += 1
            tokens.append(Token(source[i:end], "integer", ln, column))
            advance(end)
            continue
        if char == '"':
            end = source.find('"', i + 1)
            if end < 0:
                raise Token(char, "", ln, column).error("unterminated string")
            tokens.append(Token(source[i + 1:end], "string", ln, column))
            advance(end + 1)
            continue
        op = next((op for op in _OPERATORS if source.startswith(op, i)), None)
        if op is None and char in "{}()[];,:.+-*/%!=<>&|^#":
            op = char
        if op is None:
            raise Token(char, "", ln, column).error(f"unsupported Rust token {char!r}")
        tokens.append(Token(op, "punct", ln, column))
        advance(start + len(op))
    tokens.append(Token("<eof>", "eof", line, col))
    return tokens


@dataclass
class Expr:
    kind: str
    token: Token
    value: object = None
    children: list[Expr] = field(default_factory=list)
    ty: object = None
    binding: object = None


@dataclass
class Function:
    token: Token
    params: list[tuple[Token, object, bool]]
    result: object
    body: Expr | None
    abi: str = "Rust"
    public: bool = False
    no_mangle: bool = False
    unsafe: bool = False


@dataclass
class Record:
    token: Token
    fields: list[tuple[Token, object]]
    repr_c: bool = False


_TYPES = {"i32", "i64", "u32", "u64", "bool", "()"}
_PRECEDENCE = {"=": 1, "+=": 1, "-=": 1, "*=": 1, "||": 2, "&&": 3, "|": 4, "^": 5, "&": 6, "==": 7, "!=": 7, "<": 7, "<=": 7, ">": 7, ">=": 7, "+": 9, "-": 9, "*": 10, "/": 10, "%": 10, "as": 11}
_COMPARISONS = {"==", "!=", "<", "<=", ">", ">="}


class Parser:
    def __init__(self, source: str):
        self.tokens = lex(source)
        self.pos = 0
        self.records: list[Record] = []

    @property
    def token(self) -> Token:
        return self.tokens[self.pos]

    def take(self) -> Token:
        token = self.token
        self.pos += 1
        return token

    def accept(self, text: str) -> bool:
        if self.token.text == text:
            self.take()
            return True
        return False

    def expect(self, text: str) -> Token:
        if self.token.text != text:
            raise self.token.error(f"expected {text!r}, found {self.token.text!r}")
        return self.take()

    def name(self) -> Token:
        if self.token.kind != "name" or self.token.text == "_":
            raise self.token.error("expected an identifier")
        return self.take()

    def type(self) -> object:
        token = self.take()
        text = token.text
        if text == "&":
            if self.token.text == "mut":
                raise self.token.error("mutable references and borrow checking are not implemented")
            return SharedReference(self.type())
        if text == "(":
            if self.accept(")"):
                return "()"
            first = self.type()
            if self.accept(")"):
                return first
            self.expect(",")
            elements = [first]
            while not self.accept(")"):
                elements.append(self.type())
                if self.accept(")"):
                    break
                self.expect(",")
            return TupleType(tuple(elements))
        if text in _TYPES or (token.kind == "name" and text != "_"):
            return text
        raise token.error(f"unsupported Rust type {text!r}; references and traits are not implemented")

    def record(self, repr_c: bool) -> None:
        name = self.name()
        self.expect("{")
        fields = []
        while not self.accept("}"):
            self.accept("pub")
            field = self.name()
            self.expect(":")
            fields.append((field, self.type()))
            if self.accept("}"):
                break
            self.expect(",")
        self.records.append(Record(name, fields, repr_c))

    def parse(self) -> list[Function]:
        functions: list[Function] = []
        while self.token.text == "#" and self.tokens[self.pos + 1].text == "!":
            self.take()
            self.expect("!")
            self.expect("[")
            self.expect("no_std")
            self.expect("]")
        while self.token.kind != "eof":
            repr_c = no_mangle = False
            while self.accept("#"):
                self.expect("[")
                attribute = self.take()
                if attribute.text == "repr" and not repr_c:
                    self.expect("(")
                    self.expect("C")
                    self.expect(")")
                    repr_c = True
                elif attribute.text == "no_mangle" and not no_mangle:
                    no_mangle = True
                else:
                    raise attribute.error("unsupported Rust attribute; only repr(C) records and no_mangle functions are admitted")
                self.expect("]")
            public = self.accept("pub")
            if self.accept("struct"):
                if no_mangle:
                    raise self.token.error("no_mangle is supported only on extern C function definitions")
                self.record(repr_c)
                continue
            if repr_c:
                raise self.token.error("repr(C) applies only to a struct")
            unsafe = self.accept("unsafe")
            abi = "Rust"
            if self.accept("extern"):
                if self.token.kind == "string":
                    token = self.take()
                    if token.text == "C-unwind":
                        raise token.error("C-unwind requires owned cross-language unwinding, which is not implemented")
                    if token.text != "C":
                        raise token.error('only the non-unwinding extern "C" ABI is supported')
                abi = "C"
                if self.accept("{"):
                    if no_mangle or public:
                        raise self.token.error("attributes and visibility on extern blocks are not supported")
                    while not self.accept("}"):
                        visible = self.accept("pub")
                        functions.append(self.function("C", visible, False, True, declaration=True))
                    continue
            functions.append(self.function(abi, public, no_mangle, unsafe))
        return functions

    def function(self, abi, public, no_mangle, unsafe, declaration=False) -> Function:
        self.expect("fn")
        name = self.name()
        self.expect("(")
        params = []
        if not self.accept(")"):
            while True:
                mutable = self.accept("mut")
                if declaration and mutable:
                    raise self.token.error("extern declarations cannot contain parameter patterns")
                param = self.name()
                self.expect(":")
                params.append((param, self.type(), mutable))
                if self.accept(")"):
                    break
                self.expect(",")
                if self.accept(")"):
                    break
        result = self.type() if self.accept("->") else "()"
        if declaration:
            self.expect(";")
            body = None
        else:
            body = self.block()
        return Function(name, params, result, body, abi, public, no_mangle, unsafe)

    def block(self) -> Expr:
        token = self.expect("{")
        statements: list[Expr] = []
        tail = None
        while self.token.text != "}":
            if self.token.kind == "eof":
                raise self.token.error("unterminated block")
            if self.accept(";"):
                continue
            if self.accept("let"):
                mutable = self.accept("mut")
                name = self.name()
                annotation = self.type() if self.accept(":") else None
                self.expect("=")
                init = self.expression()
                self.expect(";")
                statements.append(Expr("let", name, (annotation, mutable), [init]))
                continue
            expr = self.expression()
            if self.accept(";"):
                statements.append(Expr("discard", expr.token, children=[expr]))
            elif self.token.text == "}":
                tail = expr
                break
            elif expr.kind in {"if", "block", "while", "unsafe"}:
                statements.append(Expr("statement", expr.token, children=[expr]))
            else:
                raise self.token.error("expected ';' or end of block")
        self.expect("}")
        return Expr("block", token, len(statements), statements + ([tail] if tail else []))

    def expression(self, minimum: int = 1, allow_struct: bool = True) -> Expr:
        token = self.take()
        text = token.text
        if token.kind == "integer":
            expr = Expr("integer", token, text)
        elif text in {"true", "false"}:
            expr = Expr("bool", token, text == "true")
        elif text == "(":
            if self.accept(")"):
                expr = Expr("unit", token)
            else:
                first = self.expression()
                if self.accept(","):
                    elements = [first]
                    while not self.accept(")"):
                        elements.append(self.expression())
                        if self.accept(")"):
                            break
                        self.expect(",")
                    expr = Expr("tuple", token, children=elements)
                else:
                    self.expect(")")
                    expr = Expr("group", token, children=[first])
        elif text == "{":
            self.pos -= 1
            expr = self.block()
        elif text == "unsafe":
            expr = Expr("unsafe", token, children=[self.block()])
        elif text == "if":
            condition = self.expression(allow_struct=False)
            yes = self.block()
            children = [condition, yes]
            if self.accept("else"):
                children.append(self.expression(12) if self.token.text == "if" else self.block())
            expr = Expr("if", token, children=children)
        elif text == "while":
            condition = self.expression(allow_struct=False)
            expr = Expr("while", token, children=[condition, self.block()])
        elif text in {"return", "break", "continue"}:
            children = []
            if self.token.text not in {";", "}", ")", ","}:
                if text != "return":
                    raise token.error("break values and loop labels are not implemented")
                children.append(self.expression())
            expr = Expr(text, token, children=children)
        elif text in {"-", "!"}:
            expr = Expr("unary", token, text, [self.expression(12, allow_struct=allow_struct)])
        elif token.kind == "name" and text != "_":
            expr = Expr("name", token, text)
        else:
            raise token.error(f"unsupported expression {text!r}; borrowing, heap, macros and panic are not implemented")
        while True:
            if self.token.text == "{" and expr.kind == "name" and allow_struct:
                self.take()
                fields, values = [], []
                while not self.accept("}"):
                    field = self.name()
                    fields.append(field)
                    values.append(self.expression() if self.accept(":") else Expr("name", field, field.text))
                    if self.accept("}"):
                        break
                    self.expect(",")
                expr = Expr("construct", expr.token, fields, values)
                continue
            if self.token.text == ".":
                self.take()
                field = self.take()
                if field.kind != "name" and not (field.kind == "integer" and field.text.isdecimal() and field.text == str(int(field.text))):
                    raise field.error("expected a named field or unsuffixed decimal tuple index")
                expr = Expr("field", field, field.text, [expr])
                continue
            if self.token.text == "(" and minimum <= 13:
                self.take()
                args: list[Expr] = []
                if not self.accept(")"):
                    while True:
                        args.append(self.expression())
                        if self.accept(")"):
                            break
                        self.expect(",")
                        if self.accept(")"):
                            break
                expr = Expr("call", expr.token, children=[expr] + args)
                continue
            op = self.token.text
            level = _PRECEDENCE.get(op, 0)
            if level < minimum:
                break
            operator = self.take()
            if op == "as":
                expr = Expr("cast", operator, self.type(), [expr])
                continue
            if op in _COMPARISONS and expr.kind == "binary" and expr.value in _COMPARISONS:
                raise operator.error("comparison operators cannot be chained")
            right = self.expression(level if level == 1 else level + 1, allow_struct=allow_struct)
            expr = Expr("assign" if level == 1 else "binary", operator, op, [expr, right])
        return expr
