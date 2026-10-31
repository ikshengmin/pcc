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
LEXEME = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*|[0-9][A-Za-z0-9_]*|>>>=|>>>|>>=|<<=|==|!=|<=|>=|&&|\|\||\+\+|--|->|[+*/%&|^\-]=|<<|>>|[{}()[\];,.?:+*/%&|^!~<>=\-]")
CHAR_LITERAL = re.compile(r"'(?:[^'\\\r\n]|\\(?:[btnfrs\"'\\]|[0-3][0-7]{2}|[0-7]{1,2}))'")


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
    is_final: bool = False


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
    parameter_final: tuple[bool, ...] = ()
    is_constructor: bool = False
    implicit: bool = False
    constructor_target: object = None
    initialization_steps: tuple = ()

    @property
    def descriptor(self):
        return "(" + "".join(DESCRIPTORS[dtype] for dtype in self.parameter_types) + ")" + DESCRIPTORS[self.result]


@dataclass
class SourceField:
    token: Token
    dtype: str
    initializer: Node | None
    modifiers: frozenset[str]
    owner: str
    ordinal: int
    constant: int | bool | None = None


@dataclass
class SourceClass:
    name: str
    methods: tuple[SourceMethod, ...]
    modifiers: frozenset[str] = frozenset()
    imports: tuple[tuple[Token, ...], ...] = ()
    fields: tuple[SourceField, ...] = ()
    constructors: tuple[SourceMethod, ...] = ()
    token: Token | None = None
    instance_initializers: tuple[Node, ...] = ()
    initialization_steps: tuple = ()

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
            match = (CHAR_LITERAL if source[pos] == "'" else LEXEME).match(source, pos)
            if not match:
                if source[pos] == "'":
                    Token("'", filename, line, column).error("invalid Java character literal")
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
    def __init__(self, source, filename, *, class_model=False):
        self.tokens = tokenize(source, filename)
        self.position = 0
        self.class_model = class_model

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
        class_token = self.identifier()
        name = class_token.text
        class_modifiers = frozenset(mods)
        owner = "/".join(package + [name])
        if self.peek().text != "{":
            self.peek().error("inheritance, generics and interfaces require the shared Java runtime", True)
        self.expect("{")
        methods, fields, constructors, initializers, initialization_steps = [], [], [], [], []
        while not self.accept("}"):
            if self.accept(";"):
                continue
            mods = self.modifiers()
            if self.class_model and self.peek().text == "{":
                if mods == {"static"}:
                    self.peek().error("static initializer execution requires shared Java class initialization", True)
                if mods:
                    self.peek().error("invalid instance initializer modifier")
                initializer = self.block()
                # All fields from this ordinal onward occur after this block.
                initializer.value = len(fields)
                initializers.append(initializer)
                initialization_steps.append(initializer)
                continue
            if self.class_model and self.peek().text == name and self.peek(1).text == "(":
                constructor = self.take()
                if mods - {"public", "private", "protected"}:
                    constructor.error("invalid constructor modifier")
                parameters, parameter_types, parameter_final = self.parameters(constructor, receiver=True)
                constructors.append(SourceMethod(constructor, "void", parameters, self.block(),
                                                 owner=owner, modifiers=frozenset(mods),
                                                 parameter_types=parameter_types, parameter_final=parameter_final,
                                                 is_constructor=True))
                continue
            if (not self.class_model and "static" not in mods) or mods & {"abstract", "native", "synchronized"}:
                self.peek().error("only ordinary static source methods are implemented", True)
            result = self.take()
            if result.text not in DECLARED | {"void", "byte", "short", "char"}:
                result.error("source method results currently require boolean, int, long or void", True)
            if self.peek().text == "[":
                self.peek().error("array fields require shared Java storage and class initialization", True)
            method_name = self.identifier()
            if self.peek().text != "(":
                if result.text == "void":
                    result.error("a field cannot have void type")
                if mods - {"public", "private", "protected", "static", "final"}:
                    result.error("invalid constant-field modifier")
                instance = self.class_model and "static" not in mods
                if instance and "final" in mods:
                    result.error("final instance fields require constructor definite-unassignment semantics", True)
                if not instance and "final" not in mods:
                    result.error("mutable static fields require shared Java class initialization", True)
                while True:
                    initialized = bool(self.accept("="))
                    if not initialized and not instance:
                        method_name.error("blank final fields require shared Java class initialization", True)
                    initializer = self.expression() if initialized else None
                    field = SourceField(method_name, result.text, initializer, frozenset(mods), owner, len(fields))
                    fields.append(field)
                    if instance and initialized:
                        initialization_steps.append(field)
                    if not self.accept(","):
                        break
                    method_name = self.identifier()
                self.expect(";")
                continue
            if result.text not in DECLARED | {"void"}:
                result.error("source method results currently require boolean, int, long or void", True)
            parameters, parameter_types, parameter_final = self.parameters(method_name, receiver="static" not in mods)
            methods.append(SourceMethod(method_name, result.text, parameters, self.block(),
                                        owner=owner, modifiers=frozenset(mods), parameter_types=parameter_types,
                                        parameter_final=parameter_final))
        self.expect("<eof>")
        return SourceClass(owner, tuple(methods), class_modifiers, tuple(imports), tuple(fields), tuple(constructors),
                           class_token, tuple(initializers), tuple(initialization_steps))

    def parameters(self, token, *, receiver=False):
        self.expect("(")
        parameters, types, finals = [], [], []
        if self.peek().text != ")":
            while True:
                finals.append(bool(self.accept("final")))
                if self.peek().text == "final":
                    self.peek().error("duplicate final parameter modifier")
                dtype = self.take()
                if dtype.text not in DECLARED:
                    dtype.error("source parameters require boolean, int or long", True)
                types.append(dtype.text)
                parameters.append(self.identifier())
                if not self.accept(","):
                    break
        self.expect(")")
        if sum(2 if dtype == "long" else 1 for dtype in types) + int(receiver) > 255:
            token.error("too many method parameters including receiver" if receiver else "too many static method parameters")
        return tuple(parameters), tuple(types), tuple(finals)

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
        if self.class_model and token.text in ("this", "super") and self.peek(1).text == "(":
            self.take()
            self.expect("(")
            arguments = self.arguments()
            self.expect(";")
            return Node("constructor_call", token, token.text, arguments)
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
        if self.accept("do"):
            body = self.statement()
            self.expect("while")
            self.expect("(")
            condition = self.expression()
            self.expect(")")
            self.expect(";")
            return Node("do", token, children=[condition, body])
        if self.accept("switch"):
            return self.switch_statement(token)
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
        if token.text in ("case", "default"):
            token.error("case/default requires a directly enclosing switch block")
        node = self.simple(allow_declaration=in_block)
        self.expect(";")
        return node

    def switch_statement(self, token):
        self.expect("(")
        selector = self.expression()
        self.expect(")")
        self.expect("{")
        groups, style = [], None
        while not self.accept("}"):
            label_token = self.take()
            if label_token.text == "default":
                labels = [Node("default", label_token)]
            elif label_token.text == "case":
                labels = [Node("case", label_token, children=[self.expression()])]
                while self.accept(","):
                    labels.append(Node("case", label_token, children=[self.expression()]))
            else:
                label_token.error("switch block requires a case/default label")
            if self.accept(":"):
                current = "colon"
                statements = []
                while self.peek().text not in ("case", "default", "}"):
                    statements.append(self.statement(in_block=True))
            elif self.accept("->"):
                current = "arrow"
                if self.peek().text == "{":
                    statements = [self.block()]
                else:
                    statements = [self.simple()]
                    self.expect(";")
            else:
                self.peek().error("expected ':' or '->' after switch label")
            if style is not None and style != current:
                label_token.error("cannot mix switch rules and statement groups")
            style = current
            groups.append(Node("switch_group", label_token, labels, statements))
        return Node("switch", token, style or "colon", [selector, *groups])

    def simple(self, allow_declaration=False):
        token = self.peek()
        if self.class_model and token.text == "this" and self.peek(1).text == "." and self.peek(3).text in ASSIGNMENTS | {"++", "--"}:
            self.take()
            self.expect(".")
            member = self.identifier()
            operation = self.take().text
            children = [] if operation in ("++", "--") else [self.expression()]
            return Node("field_assign", member, operation, children)
        is_final = bool(self.accept("final"))
        if is_final and self.peek().text == "final":
            self.peek().error("duplicate final local modifier")
        if self.peek().text in DECLARED:
            dtype = self.take().text
            if not allow_declaration:
                token.error("a local declaration requires a block or for initializer")
            name = self.identifier()
            children = [self.expression()] if self.accept("=") else []
            if is_final and not children:
                name.error("blank final locals require the definite-unassignment source domain", True)
            return Node("var", name, dtype, children, is_final=is_final)
        if is_final:
            token.error("final local declarations require boolean, int or long", True)
        if self.peek(1).text in ASSIGNMENTS | {"++", "--"}:
            name = self.identifier()
            operation = self.take().text
            children = [] if operation in ("++", "--") else [self.expression()]
            return Node("assign", name, operation, children)
        expression = self.expression()
        if expression.kind != "call" and not (self.class_model and expression.kind == "receiver_call"):
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
        elif self.class_model and token.text == "this":
            if not self.accept("."):
                node = Node("this", token)
            else:
                member = self.identifier()
                if self.accept("("):
                    node = Node("receiver_call", member, member.text, self.arguments())
                else:
                    node = Node("receiver_field", member, member.text)
        elif token.text in ("true", "false"):
            node = Node("bool", token, token.text == "true")
        elif token.text.startswith("'"):
            node = Node("char", token, token.text)
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
                node = Node("name", token, ".".join(parts))
        else:
            token.error("unsupported Java expression or statement", True)
        while PRECEDENCE.get(self.peek().text, 0) >= minimum:
            operator = self.take()
            node = Node("binary", operator, operator.text,
                        [node, self.expression(PRECEDENCE[operator.text] + 1)])
        if minimum == 1 and self.peek().text == "?":
            operator = self.take()
            yes = self.expression()
            self.expect(":")
            node = Node("conditional", operator, children=[node, yes, self.expression()])
        return node

    def arguments(self):
        arguments = []
        if self.peek().text != ")":
            while True:
                arguments.append(self.expression())
                if not self.accept(","):
                    break
        self.expect(")")
        return arguments
