"""Owned Go scanner, source AST and recursive-descent parser.

The scanner implements Go's newline/EOF semicolon insertion, including block
comments containing newlines. No source rewriting or other-language parser is
used. Unsupported grammar fails with a source location, never discarded tokens.
"""

from dataclasses import dataclass, field
import unicodedata

from pcc.frontends.go.types import Results, TYPE_NAMES


class GoDiagnostic(ValueError):
    """A rejected Go program or an explicit unimplemented language boundary."""

    def __init__(self, token, message):
        self.filename = token.filename
        self.line = token.line
        self.column = token.column
        super().__init__(f"{self.filename}:{self.line}:{self.column}: Go: {message}")


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    filename: str
    line: int
    column: int


KEYWORDS = frozenset(
    "break default func interface select case defer go map struct chan else "
    "goto package switch const fallthrough if range type continue for import "
    "return var".split()
)
OPERATORS = tuple(sorted(
    "<<= >>= &^= ... == != <= >= := ++ -- && || << >> &^ += -= *= /= %= "
    "&= |= ^= <- + - * / % & | ^ ! < > = ( ) [ ] { } , ; : . ~".split(),
    key=len, reverse=True,
))


def _letter(char):
    return char == "_" or unicodedata.category(char).startswith("L")


def _digit(char):
    return unicodedata.category(char) == "Nd"


def scan(source, filename="<go>"):
    tokens = []
    pos, line, column = 0, 1, 1
    pending = False

    def emit(kind, text, at_line, at_column):
        nonlocal pending
        tokens.append(Token(kind, text, filename, at_line, at_column))
        pending = kind in ("name", "integer", "string", "rune") or text in (
            "break", "continue", "fallthrough", "return", "++", "--", ")", "]", "}"
        )

    def advance(text):
        nonlocal pos, line, column
        pos += len(text)
        count = text.count("\n")
        if count:
            line += count
            column = len(text.rsplit("\n", 1)[1]) + 1
        else:
            column += len(text)

    if source.startswith("\ufeff"):
        advance(source[0])
    while pos < len(source):
        char = source[pos]
        if char in " \t\r":
            advance(char)
            continue
        if char == "\n":
            if pending:
                emit(";", ";", line, column)
            advance(char)
            continue
        if source.startswith("//", pos):
            end = source.find("\n", pos)
            advance(source[pos:len(source) if end < 0 else end])
            continue
        if source.startswith("/*", pos):
            end = source.find("*/", pos + 2)
            if end < 0:
                raise GoDiagnostic(Token("", "", filename, line, column), "unterminated comment")
            comment = source[pos:end + 2]
            if "\n" in comment and pending:
                emit(";", ";", line, column)
            advance(comment)
            continue
        at_line, at_column, start = line, column, pos
        if _letter(char):
            end = pos + 1
            while end < len(source) and (_letter(source[end]) or _digit(source[end])):
                end += 1
            text = source[pos:end]
            emit(text if text in KEYWORDS else "name", text, at_line, at_column)
            advance(text)
            continue
        if "0" <= char <= "9":
            end = pos + 1
            while end < len(source) and (source[end].isalnum() or source[end] in "_."):
                end += 1
            text = source[pos:end]
            emit("integer", text, at_line, at_column)
            advance(text)
            continue
        if char in "\"'`":
            end = pos + 1
            while end < len(source):
                if source[end] == char:
                    end += 1
                    break
                if source[end] == "\\" and char != "`":
                    end += 2
                else:
                    end += 1
            else:
                raise GoDiagnostic(Token("", "", filename, line, column), "unterminated literal")
            text = source[start:end]
            emit("rune" if char == "'" else "string", text, at_line, at_column)
            advance(text)
            continue
        for op in OPERATORS:
            if source.startswith(op, pos):
                emit(op, op, at_line, at_column)
                advance(op)
                break
        else:
            raise GoDiagnostic(Token("", char, filename, line, column), f"unsupported character {char!r}")
    if pending:
        emit(";", ";", line, column)
    tokens.append(Token("eof", "", filename, line, column))
    return tokens


@dataclass
class ArraySyntax:
    token: Token
    length: object
    element: object


@dataclass
class Expr:
    kind: str
    token: Token
    value: object = None
    args: list = field(default_factory=list)
    type: str | None = None
    constant: object = None
    symbol: object = None
    qualifier: str | None = None
    parenthesized: bool = False
    keys: list = field(default_factory=list)
    indices: list = field(default_factory=list)
    addressable: bool = False


@dataclass
class Stmt:
    kind: str
    token: Token
    exprs: list = field(default_factory=list)
    body: list = field(default_factory=list)
    otherwise: list | None = None
    names: list = field(default_factory=list)
    type: str | None = None
    init: object = None
    post: object = None
    symbols: list = field(default_factory=list)
    terminates: bool = False
    targets: list = field(default_factory=list)


@dataclass
class Function:
    token: Token
    params: list
    result: str
    body: list
    locals: list = field(default_factory=list)
    parameters: list = field(default_factory=list)
    abi_symbol: str = ""
    temporaries: list = field(default_factory=list)
    result_names: list = field(default_factory=list)
    result_symbols: list = field(default_factory=list)


@dataclass
class Import:
    token: Token
    alias: str | None
    path: str


@dataclass
class Program:
    token: Token
    functions: list
    constants: list
    imports: list = field(default_factory=list)
    variables: list = field(default_factory=list)


BINARY_PRECEDENCE = {
    "||": 1, "&&": 2,
    "==": 3, "!=": 3, "<": 3, "<=": 3, ">": 3, ">=": 3,
    "+": 4, "-": 4, "|": 4, "^": 4,
    "*": 5, "/": 5, "%": 5, "<<": 5, ">>": 5, "&": 5, "&^": 5,
}


class GoParser:
    def parse(self, source, filename="<go>", *, require_main=True):
        self.tokens = scan(source, filename)
        self.index = 0
        self.take("package")
        package = self.take("name")
        if require_main and package.text != "main":
            self.fail(package, "unsupported package: initial frontend requires package main")
        self.take(";")
        if package.text == "_":
            self.fail(package, "package name cannot be blank")
        functions, constants, imports, variables = [], [], [], []
        while not self.at("eof"):
            if self.accept(";"):
                continue
            if self.at("import"):
                if functions or constants or variables:
                    self.fail(self.current, "imports must precede package declarations")
                imports.extend(self.import_declaration())
            elif self.at("func"):
                functions.append(self.function())
            elif self.at("const"):
                constants.append(self.declaration())
            elif self.at("var"):
                variables.append(self.declaration())
            else:
                self.fail(self.current, "unsupported package declaration; expected func, const or var")
            self.end_statement()
        return Program(package, functions, constants, imports, variables)

    @property
    def current(self):
        return self.tokens[self.index]

    def at(self, kind):
        return self.current.kind == kind

    def accept(self, kind):
        if self.at(kind):
            token = self.current
            self.index += 1
            return token
        return None

    def take(self, kind):
        token = self.accept(kind)
        if token is None:
            self.fail(self.current, f"expected {kind!r}, got {self.current.text!r}")
        return token

    def fail(self, token, message):
        raise GoDiagnostic(token, message)

    def end_statement(self):
        if not self.accept(";") and not self.at("}") and not self.at("eof"):
            self.fail(self.current, "expected semicolon or newline")

    def import_declaration(self):
        self.take("import")
        grouped = bool(self.accept("("))
        imports = []
        while True:
            if grouped and self.accept(")"):
                break
            if grouped and self.accept(";"):
                continue
            alias = self.take("name").text if self.at("name") else None
            if self.at("."):
                self.fail(self.current, "dot imports are not implemented")
            token = self.take("string")
            path = token.text[1:-1]
            if (not path or "\\" in path or any(char.isspace() for char in path) or
                    any(char in '!"#$%&\'()*,:;<=>?[\\]^`{|}\ufffd' for char in path)):
                self.fail(token, "unsupported or invalid import path")
            imports.append(Import(token, alias, path))
            if not grouped:
                break
            if not self.accept(";") and not self.at(")"):
                self.fail(self.current, "expected import separator")
        return imports

    def datatype(self, *, literal=False):
        if self.at("["):
            token = self.take("[")
            if self.at("]"):
                self.fail(token, "slice types require shared-runtime backing storage support")
            if self.accept("..."):
                if not literal:
                    self.fail(token, "inferred array length is only valid in a composite literal")
                length = None
            else:
                length = self.expression()
            self.take("]")
            element = self.datatype()
            return ArraySyntax(token, length, element)
        token = self.take("name")
        if token.text not in TYPE_NAMES:
            self.fail(token, f"unsupported type {token.text!r}; available: fixed-width integers, int, uint, uintptr, bool")
        return TYPE_NAMES[token.text]

    def function(self):
        self.take("func")
        name = self.take("name")
        self.take("(")
        params = []
        while not self.at(")"):
            names = [self.take("name")]
            while self.accept(","):
                names.append(self.take("name"))
            dtype = self.datatype()
            params.extend((token, dtype) for token in names)
            if not self.accept(","):
                break
        self.take(")")
        result, result_names = self.function_results()
        return Function(name, params, result, self.block(), result_names=result_names)

    def function_results(self):
        if not self.accept("("):
            if self.at("name") or self.at("["):
                return self.datatype(), [None]
            return "void", []
        types, names = [], []
        named = self.at("name") and self.current.text not in TYPE_NAMES
        while not self.at(")"):
            if named:
                group = [self.take("name")]
                while self.accept(","):
                    group.append(self.take("name"))
                dtype = self.datatype()
                types.extend([dtype] * len(group))
                names.extend(group)
            else:
                types.append(self.datatype())
                names.append(None)
            if len(types) > 256:
                self.fail(self.current, "function exceeds implementation limit (256 results)")
            if not self.accept(","):
                break
        self.take(")")
        return (Results(tuple(types)) if len(types) > 1 else types[0] if types else "void"), names

    def block(self):
        self.take("{")
        statements = []
        while not self.at("}"):
            if self.at("eof"):
                self.fail(self.current, "unterminated block")
            if self.accept(";"):
                continue
            statements.append(self.statement())
            self.end_statement()
        self.take("}")
        return statements

    def declaration(self):
        token = self.current
        self.index += 1
        names = [self.take("name")]
        while self.accept(","):
            names.append(self.take("name"))
        dtype = self.datatype() if self.at("name") or self.at("[") else None
        exprs = self.expression_list() if self.accept("=") else []
        if not exprs and (token.kind == "const" or dtype is None):
            self.fail(token, "declaration requires a type or initializer (const requires initializer)")
        return Stmt(token.kind, token, names=names, type=dtype, exprs=exprs)

    def statement(self):
        token = self.current
        if self.at("var") or self.at("const"):
            return self.declaration()
        if self.at("{"):
            return Stmt("block", token, body=self.block())
        if self.accept("return"):
            exprs = [] if self.at(";") or self.at("}") else self.expression_list()
            return Stmt("return", token, exprs=exprs)
        if self.accept("break") or self.accept("continue"):
            return Stmt(token.kind, token)
        if self.accept("if"):
            first = self.simple()
            init = None
            if self.accept(";"):
                init, condition = first, self.expression()
            elif first.kind == "expression" and len(first.exprs) == 1:
                condition = first.exprs[0]
            else:
                self.fail(token, "if requires a boolean condition")
            body, otherwise = self.block(), None
            if self.accept("else"):
                otherwise = [self.statement()] if self.at("if") else self.block()
            return Stmt("if", token, exprs=[condition], body=body, otherwise=otherwise, init=init)
        if self.accept("for"):
            init, condition, post = None, None, None
            if not self.at("{"):
                first = None if self.at(";") else self.simple()
                if self.accept(";"):
                    init = first
                    condition = None if self.at(";") else self.expression()
                    self.take(";")
                    post = None if self.at("{") else self.simple()
                    if post is not None and post.kind == ":=":
                        self.fail(post.token, "for post statement cannot be a short variable declaration")
                elif first is not None and first.kind == "expression":
                    condition = first.exprs[0]
                else:
                    self.fail(token, "unsupported for clause (range is not implemented)")
            return Stmt("for", token, exprs=[] if condition is None else [condition],
                        body=self.block(), init=init, post=post)
        if token.kind in KEYWORDS:
            self.fail(token, f"unsupported statement {token.text!r}")
        return self.simple()

    def simple(self):
        first = self.expression()
        left = [first]
        while self.accept(","):
            left.append(self.expression())
        token = self.current
        if token.kind in ("=", ":=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "&^=", "<<=", ">>="):
            self.index += 1
            if token.kind == ":=" and any(expr.kind != "name" for expr in left):
                self.fail(token, "short declaration requires identifiers")
            if token.kind == ":=" and any(expr.parenthesized for expr in left):
                self.fail(token, "short declaration requires unparenthesized identifiers")
            return Stmt(token.kind, token, names=[expr.token for expr in left], exprs=self.expression_list(), targets=left)
        if self.accept("++") or self.accept("--"):
            if len(left) != 1:
                self.fail(token, "increment/decrement requires one variable")
            return Stmt(token.kind, token, names=[first.token], targets=left)
        if len(left) != 1:
            self.fail(token, "expression statement must contain one function call")
        return Stmt("expression", first.token, exprs=left)

    def expression_list(self, terminator=None):
        result = [self.expression()]
        while self.accept(","):
            if terminator is not None and self.at(terminator):
                break
            result.append(self.expression())
        return result

    def expression(self, minimum=1):
        token = self.current
        if token.kind in ("+", "-", "!", "^"):
            self.index += 1
            left = Expr("unary", token, token.kind, [self.expression(6)])
        elif self.accept("("):
            left = self.expression()
            self.take(")")
            left.parenthesized = True
        elif self.at("["):
            dtype = self.datatype(literal=True)
            self.take("{")
            args, keys = [], []
            while not self.at("}"):
                item = self.expression()
                key = item if self.accept(":") else None
                args.append(self.expression() if key is not None else item)
                keys.append(key)
                if not self.accept(","):
                    break
            self.take("}")
            left = Expr("array", token, dtype, args, keys=keys)
        elif self.accept("integer"):
            left = Expr("integer", token, token.text)
        elif self.accept("name"):
            left = Expr("name", token, token.text)
        else:
            self.fail(token, f"unsupported expression token {token.text!r}")
        if self.accept("."):
            if left.kind != "name" or left.parenthesized:
                self.fail(token, "only unparenthesized imported package selectors are supported")
            member = self.take("name")
            left = Expr("selector", member, member.text, [left])
        if self.accept("("):
            if left.kind not in ("name", "selector"):
                self.fail(token, "only named function calls/conversions are supported")
            args = [] if self.at(")") else self.expression_list(terminator=")")
            self.take(")")
            qualifier = left.args[0].value if left.kind == "selector" else None
            left = Expr("call", left.token, left.value, args, qualifier=qualifier)
        while self.accept("["):
            index = self.expression()
            self.take("]")
            left = Expr("index", index.token, args=[left, index])
        while BINARY_PRECEDENCE.get(self.current.kind, 0) >= minimum:
            operator = self.current
            self.index += 1
            right = self.expression(BINARY_PRECEDENCE[operator.kind] + 1)
            left = Expr("binary", operator, operator.kind, [left, right])
        return left
