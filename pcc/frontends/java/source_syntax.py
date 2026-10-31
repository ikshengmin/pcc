"""Owned Java source syntax; no javac, class encoding or token rewriting.

Initial grammar: one ordinary class, static boolean/int/long/void methods and scalar locals,
structured control flow and scalar expressions. Unsupported grammar is never
discarded. Unicode-escape translation and non-ASCII identifiers are explicit
boundaries rather than an approximation of Java's lexical translation rules.
"""
from dataclasses import dataclass, field
import re

from .diagnostics import JavaError, UnsupportedJava
from .source_types import DECLARED, DESCRIPTORS


KEYWORDS = frozenset((
    "abstract assert boolean break byte case catch char class const continue default "
    "do double else enum extends final finally float for goto if implements import "
    "instanceof int interface long native new package private protected public return "
    "short static strictfp super switch synchronized this throw throws transient try "
    "void volatile while true false null _ record sealed permits yield var"
).split())
MODIFIERS = frozenset("public protected private static final abstract native synchronized strictfp".split())
PRECEDENCE = {"||": 1, "&&": 2, "|": 3, "^": 4, "&": 5,
              "==": 6, "!=": 6, "<": 7, ">": 7, "<=": 7, ">=": 7,
              "<<": 8, ">>": 8, ">>>": 8, "+": 9, "-": 9,
              "*": 10, "/": 10, "%": 10}
ASSIGNMENTS = frozenset(("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=", ">>>="))
LEXEME = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*|[0-9][A-Za-z0-9_]*|>>>=|>>>|>>=|<<=|==|!=|<=|>=|&&|\|\||\+\+|--|[+*/%&|^\-]=|<<|>>|[{}()[\];,.+*/%&|^!~<>=\-]")


@dataclass(frozen=True)
class Token:
    text: str
    filename: str
    line: int
    column: int

    def error(self, message, unsupported=False):
        kind = UnsupportedJava if unsupported else JavaError
        raise kind(f"{self.filename}:{self.line}:{self.column}: {message}")


@dataclass
class Node:
    kind: str
    token: Token
    value: object = None
    children: list = field(default_factory=list)
    dtype: str | None = None
    symbol: object = None
    constant: int | bool | None = None
    completes: bool = True
    operation_type: str | None = None


@dataclass
class SourceMethod:
    token: Token
    result: str
    parameters: tuple[Token, ...]
    body: Node
    locals: list = field(default_factory=list)
    owner: str = ""
    modifiers: frozenset[str] = frozenset()
    parameter_types: tuple[str, ...] = ()

    @property
    def descriptor(self):
        return "(" + "".join(DESCRIPTORS[dtype] for dtype in self.parameter_types) + ")" + DESCRIPTORS[self.result]


@dataclass
class SourceClass:
    name: str
    methods: tuple[SourceMethod, ...]
    modifiers: frozenset[str] = frozenset()
    imports: tuple[tuple[Token, ...], ...] = ()

    @property
    def package(self):
        return self.name.rpartition("/")[0]


def tokenize(source, filename):
    if not isinstance(source, str):
        raise JavaError("Java source input must be text")
    if len(source) > 1_048_576:
        raise UnsupportedJava("Java source exceeds the initial 1 MiB input limit")
    if "\\u" in source:
        raise UnsupportedJava("Java Unicode-escape translation is not implemented")
    tokens, pos, line, column = [], 0, 1, 1
    while pos < len(source):
        start, at_line, at_column = pos, line, column
        if source[pos] in " \t\f\r\n":
            pos += 1
            if source[start] == "\r" and source[pos:pos + 1] == "\n":
                pos += 1
        elif source.startswith("//", pos):
            end = re.search(r"[\r\n]", source[pos:])
            pos = pos + end.start() if end else len(source)
        elif source.startswith("/*", pos):
            end = source.find("*/", pos + 2)
            if end < 0:
                Token("/*", filename, line, column).error("unterminated comment")
            pos = end + 2
        else:
            match = LEXEME.match(source, pos)
            if not match:
                Token(source[pos], filename, line, column).error("unsupported Java source token", True)
            pos = match.end()
            tokens.append(Token(match.group(), filename, line, column))
            if len(tokens) > 100_000:
                raise UnsupportedJava("Java source exceeds the initial token budget")
        fragment = source[start:pos].replace("\r\n", "\n").replace("\r", "\n")
        if "\n" in fragment:
            line += fragment.count("\n")
            column = len(fragment.rsplit("\n", 1)[1]) + 1
        else:
            column += pos - start
    tokens.append(Token("<eof>", filename, line, column))
    return tokens


class SourceParser:
    def __init__(self, source, filename):
        self.tokens = tokenize(source, filename)
        self.position = 0

    def peek(self, distance=0):
        return self.tokens[min(self.position + distance, len(self.tokens) - 1)]

    def take(self):
        token = self.peek()
        self.position += 1
        return token

    def accept(self, text):
        if self.peek().text == text:
            return self.take()
        return None

    def expect(self, text):
        if self.peek().text != text:
            self.peek().error(f"expected {text!r}, got {self.peek().text!r}")
        return self.take()

    def identifier(self):
        token = self.take()
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", token.text) or token.text in KEYWORDS:
            token.error("expected a non-keyword Java identifier")
        return token

    def modifiers(self):
        result = set()
        while self.peek().text in MODIFIERS:
            token = self.take()
            if token.text in result:
                token.error("duplicate modifier")
            result.add(token.text)
        if len(result & {"public", "private", "protected"}) > 1:
            self.peek().error("mutually exclusive access modifiers")
        return result

    def parse(self):
        package = []
        if self.accept("package"):
            package.append(self.identifier().text)
            while self.accept("."):
                package.append(self.identifier().text)
            self.expect(";")
        imports = []
        while self.accept("import"):
            if self.peek().text == "static":
                self.peek().error("static imports require the next source-project lookup domain", True)
            imported = [self.identifier()]
            while self.accept("."):
                if self.peek().text == "*":
                    self.peek().error("on-demand imports require the next source-project lookup domain", True)
                imported.append(self.identifier())
            self.expect(";")
            if len(imported) < 2:
                imported[0].error("cannot import a type from the unnamed package")
            imports.append(tuple(imported))
        mods = self.modifiers()
        if mods - {"public", "final", "strictfp"}:
            self.peek().error("unsupported top-level class modifiers", True)
        self.expect("class")
        name = self.identifier().text
        class_modifiers = frozenset(mods)
        owner = "/".join(package + [name])
        if self.peek().text != "{":
            self.peek().error("inheritance, generics and interfaces require the shared Java runtime", True)
        self.expect("{")
        methods = []
        while not self.accept("}"):
            if self.accept(";"):
                continue
            mods = self.modifiers()
            if "static" not in mods or mods & {"abstract", "native", "synchronized"}:
                self.peek().error("only ordinary static source methods are implemented", True)
            result = self.take()
            if result.text not in DECLARED | {"void"}:
                result.error("source method results currently require boolean, int, long or void", True)
            method_name = self.identifier()
            if self.peek().text != "(":
                self.peek().error("fields and class initialization require the shared Java runtime", True)
            self.expect("(")
            parameters, parameter_types = [], []
            if self.peek().text != ")":
                while True:
                    dtype = self.take()
                    if dtype.text not in DECLARED:
                        dtype.error("source parameters require boolean, int or long", True)
                    parameter_types.append(dtype.text)
                    parameters.append(self.identifier())
                    if not self.accept(","):
                        break
            self.expect(")")
            if sum(2 if dtype == "long" else 1 for dtype in parameter_types) > 255:
                method_name.error("too many static method parameters")
            methods.append(SourceMethod(method_name, result.text, tuple(parameters), self.block(),
                                        owner=owner, modifiers=frozenset(mods), parameter_types=tuple(parameter_types)))
        self.expect("<eof>")
        return SourceClass(owner, tuple(methods), class_modifiers, tuple(imports))

    def block(self):
        token = self.expect("{")
        children = []
        while not self.accept("}"):
            if self.peek().text == "<eof>":
                self.peek().error("unterminated block")
            children.append(self.statement(in_block=True))
        return Node("block", token, children=children)

    def statement(self, in_block=False):
        token = self.peek()
        if token.text == "{":
            return self.block()
        if self.accept(";"):
            return Node("empty", token)
        if self.accept("if"):
            self.expect("(")
            condition = self.expression()
            self.expect(")")
            yes = self.statement()
            no = self.statement() if self.accept("else") else Node("empty", token)
            return Node("if", token, value=no.kind != "empty" or no.token is not token,
                        children=[condition, yes, no])
        if self.accept("while"):
            self.expect("(")
            condition = self.expression()
            self.expect(")")
            return Node("while", token, children=[condition, self.statement()])
        if self.accept("for"):
            self.expect("(")
            initial = Node("empty", token) if self.peek().text == ";" else self.simple(allow_declaration=True)
            self.expect(";")
            condition = Node("bool", token, True) if self.peek().text == ";" else self.expression()
            self.expect(";")
            update = Node("empty", token) if self.peek().text == ")" else self.simple()
            self.expect(")")
            return Node("for", token, children=[initial, condition, update, self.statement()])
        if self.accept("return"):
            children = [] if self.peek().text == ";" else [self.expression()]
            self.expect(";")
            return Node("return", token, children=children)
        if token.text in ("break", "continue"):
            self.take()
            self.expect(";")
            return Node(token.text, token)
        node = self.simple(allow_declaration=in_block)
        self.expect(";")
        return node

    def simple(self, allow_declaration=False):
        token = self.peek()
        if token.text in DECLARED:
            self.take()
            if not allow_declaration:
                token.error("a local declaration requires a block or for initializer")
            name = self.identifier()
            children = [self.expression()] if self.accept("=") else []
            return Node("var", name, token.text, children)
        if self.peek(1).text in ASSIGNMENTS | {"++", "--"}:
            name = self.identifier()
            operation = self.take().text
            children = [] if operation in ("++", "--") else [self.expression()]
            return Node("assign", name, operation, children)
        expression = self.expression()
        if expression.kind != "call":
            expression.token.error("only assignment, increment or method invocation is an expression statement")
        return Node("expression", token, children=[expression])

    def expression(self, minimum=1):
        token = self.take()
        if token.text in ("+", "-", "~", "!"):
            node = Node("unary", token, token.text, [self.expression(11)])
        elif token.text == "(":
            if self.peek().text in ("int", "long", "boolean", "byte", "short", "char") and self.peek(1).text == ")":
                dtype = self.take().text
                self.expect(")")
                node = Node("cast", token, dtype, [self.expression(11)])
            else:
                node = Node("group", token, children=[self.expression()])
                self.expect(")")
        elif token.text in ("true", "false"):
            node = Node("bool", token, token.text == "true")
        elif token.text[0:1].isdigit():
            node = Node("integer", token, token.text)
        elif re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", token.text) and token.text not in KEYWORDS:
            parts = [token.text]
            while self.accept("."):
                parts.append(self.identifier().text)
            if self.accept("("):
                arguments = []
                if self.peek().text != ")":
                    while True:
                        arguments.append(self.expression())
                        if not self.accept(","):
                            break
                self.expect(")")
                node = Node("call", token, ".".join(parts), arguments)
            else:
                if len(parts) != 1:
                    token.error("field values require shared Java class/object storage", True)
                node = Node("name", token, token.text)
        else:
            token.error("unsupported Java expression or statement", True)
        while PRECEDENCE.get(self.peek().text, 0) >= minimum:
            operator = self.take()
            node = Node("binary", operator, operator.text,
                        [node, self.expression(PRECEDENCE[operator.text] + 1)])
        return node
