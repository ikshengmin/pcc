"""Owned Java source syntax; no javac, class encoding or token rewriting.

Initial grammar: one ordinary class, static primitive/void methods and scalar locals,
structured control flow and scalar expressions. Unsupported grammar is never
discarded. Unicode escapes are translated before comments and tokens, retaining
raw-source diagnostic coordinates. Ordinary string literals retain UTF-16 code
units in the source AST, without claiming managed String semantics or lowering.
The nonlowering class model retains original named-reference declaration tokens;
the native scalar declaration grammar remains closed to these types.
Non-ASCII identifiers and text blocks remain explicit gaps.
"""
from bisect import bisect_right
from dataclasses import dataclass, field
import re

from .diagnostics import JavaError, UnsupportedJava
from .floating_semantics import FLOAT_LITERAL
from .source_types import ARRAY_COMPONENTS, ArrayType, DECLARED, NamedType, descriptor


KEYWORDS = frozenset((
    "abstract assert boolean break byte case catch char class const continue default "
    "do double else enum extends final finally float for goto if implements import "
    "instanceof int interface long native new package private protected public return "
    "short static strictfp super switch synchronized this throw throws transient try "
    "void volatile while true false null _ record sealed permits yield var"
).split())
# These contextual spellings are ordinary Identifiers in label positions (JLS 3.8/3.9).
LABEL_CONTEXTUAL_NAMES = frozenset("record sealed permits yield var".split())
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
    string_units: tuple[int, ...] | None = None

    def error(self, message, *, code=None):
        diagnostic = f"{self.filename}:{self.line}:{self.column}: {message}"
        if code is not None:
            raise UnsupportedJava(diagnostic, code=code)
        raise JavaError(diagnostic)


@dataclass
class Node:
    kind: str
    token: Token
    value: object = None
    children: list = field(default_factory=list)
    dtype: str | None = None
    symbol: object = None
    constant: int | float | bool | None = None
    completes: bool = True
    operation_type: str | None = None
    is_final: bool = False
    runtime_steps: tuple[str, ...] = ()
    control_target: object = field(default=None, repr=False, compare=False)
    flow_outcomes: object = field(default=None, repr=False, compare=False)
    string_constant: object = None  # Source content only, never runtime identity.
    managed_value: object = None  # Compiler-only requirements, never a runtime reference.
    arithmetic_plan: object = None  # Nonlowering checked arithmetic, never exception code.
    inference_token: Token | None = None  # Original local 'var', never a native type.


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
        return "(" + "".join(descriptor(dtype) for dtype in self.parameter_types) + ")" + descriptor(self.result)


@dataclass
class SourceField:
    token: Token
    dtype: str
    initializer: Node | None
    modifiers: frozenset[str]
    owner: str
    ordinal: int
    constant: int | float | bool | None = None
    string_constant: object = None


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
    static_imports: tuple[tuple[Token, ...], ...] = ()
    member_classes: tuple = ()
    enclosing_name: str | None = None
    canonical_name: str | None = None

    @property
    def package(self):
        return self.name.rpartition("/")[0]

    @property
    def simple_name(self):
        return self.token.text if self.token is not None else self.name.rsplit("/", 1)[-1]

    @property
    def source_name(self):
        return self.canonical_name or self.name


def _raw_token(text, filename, offset, line_starts, *, string_units=None):
    line = bisect_right(line_starts, offset)
    return Token(text, filename, line, offset - line_starts[line - 1] + 1, string_units)


def _translate_unicode_escapes(source, filename, line_starts):
    """JLS 3.3: one nonrecursive pass over raw input, before lexical analysis.

    A translated backslash makes the next raw backslash eligible regardless
    of parity. Otherwise eligibility uses the complete trailing backslash run
    in the translated result, including backslashes produced by escapes.

    Each escape records only where its contraction ends. Positions before
    that endpoint map to the escape's initial raw backslash; later positions
    add the cumulative contraction. No per-character source map is needed.
    """
    if "\\u" not in source:
        return source, [], []
    result, ends, contractions = [], [], []
    position, backslashes, previous_escape = 0, 0, False
    while position < len(source):
        start = position
        character = source[position]
        position += 1
        escaped = (character == "\\" and (previous_escape or backslashes % 2 == 0)
                   and source[position:position + 1] == "u")
        if escaped:
            while source[position:position + 1] == "u":
                position += 1
            digits = source[position:position + 4]
            if len(digits) != 4 or any(digit not in "0123456789abcdefABCDEF" for digit in digits):
                _raw_token("\\", filename, start, line_starts).error("malformed Java Unicode escape")
            # Each escape is one UTF-16 code unit, not a Unicode scalar value:
            # retain lone surrogates and do not combine adjacent surrogate pairs.
            character = chr(int(digits, 16))
            position += 4
            ends.append(len(result) + 1)
            contractions.append(position - len(result) - 1)
        result.append(character)
        backslashes = backslashes + 1 if character == "\\" else 0
        previous_escape = escaped
    return "".join(result), ends, contractions


def _scan_string_literal(source, start, token_at):
    """JLS 3.1/3.10.5/3.10.7: scan once, retaining immutable UTF-16 units.

    Consume octal escapes maximally without regex backtracking on malformed
    long input. This source data is never a pointer, allocated String,
    constant-expression value or proof of literal identity. Supplementary
    characters become surrogate pairs; lone surrogates stay individual units.
    Unicode escapes have already been translated and are never recursive here.
    """
    escapes = {"b": 8, "t": 9, "n": 10, "f": 12, "r": 13, "s": 32,
               '"': 34, "'": 39, "\\": 92}
    units, position = [], start + 1
    while position < len(source):
        character = source[position]
        position += 1
        if character == '"':
            return position, tuple(units)
        if character in "\r\n":
            break
        value = ord(character)
        if character == "\\":
            if position == len(source):
                break
            escape = source[position]
            position += 1
            if escape in "01234567":
                digits = escape
                limit = 3 if escape in "0123" else 2
                while (len(digits) < limit and position < len(source)
                       and source[position] in "01234567"):
                    digits += source[position]
                    position += 1
                value = int(digits, 8)
            elif escape in escapes:
                value = escapes[escape]
            else:
                break
        if value > 0xffff:
            value -= 0x10000
            units.extend((0xd800 + (value >> 10), 0xdc00 + (value & 0x3ff)))
        else:
            units.append(value)
    token_at('"', start).error("invalid Java string literal")


def tokenize(source, filename):
    if not isinstance(source, str):
        raise JavaError("Java source input must be text")
    if len(source) > 1_048_576:
        raise UnsupportedJava("Java source exceeds the initial 1 MiB input limit", code="java.unsupported.source-size-limit")
    # Diagnostics refer to the supplied source, not a rewritten copy. In
    # particular an escaped newline terminates a comment without inventing a
    # physical line in the user's file. Raw CRLF still counts as one newline.
    line_starts = [0, *(match.end() for match in re.finditer(r"\r\n|\r|\n", source))]
    source, escape_ends, contractions = _translate_unicode_escapes(source, filename, line_starts)

    def token_at(text, position, *, string_units=None):
        escape = bisect_right(escape_ends, position) - 1
        raw = position + (contractions[escape] if escape >= 0 else 0)
        return _raw_token(text, filename, raw, line_starts, string_units=string_units)

    tokens, pos = [], 0
    while pos < len(source):
        start = pos
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
                token_at("/*", pos).error("unterminated comment")
            pos = end + 2
        else:
            if source.startswith('"""', pos):
                # A text block is not three ordinary string delimiters. Reject
                # a malformed opening before reporting the unavailable domain.
                if not re.match(r'"""[ \t\f]*(?:\r\n|\r|\n)', source[pos:]):
                    token_at('"""', pos).error("invalid Java text block opening delimiter")
                token_at('"""', pos).error("text blocks require the next Java string lexical domain",
                                              code="java.unsupported.string-literals")
            if source[pos] == '"':
                pos, units = _scan_string_literal(source, pos, token_at)
                tokens.append(token_at(source[start:pos], start, string_units=units))
            else:
                match = (CHAR_LITERAL.match(source, pos) if source[pos] == "'" else
                         FLOAT_LITERAL.match(source, pos) or LEXEME.match(source, pos))
                if not match:
                    if source[pos] == "'":
                        token_at("'", pos).error("invalid Java character literal")
                    token = token_at(source[pos], pos)
                    if source[pos] == "@":
                        token.error("unsupported Java source token", code="java.unsupported.annotations")
                    if not source[pos].isascii():
                        token.error("unsupported Java source token", code="java.unsupported.unicode-tokens")
                    token.error("unsupported Java source token")
                pos = match.end()
                tokens.append(token_at(match.group(), start))
            if len(tokens) > 100_000:
                raise UnsupportedJava("Java source exceeds the initial token budget", code="java.unsupported.token-limit")
    tokens.append(token_at("<eof>", pos))
    return tokens


class SourceParser:
    def __init__(self, source, filename, *, class_model=False):
        self.tokens = tokenize(source, filename)
        self.position = 0
        self.class_model = class_model
        self.inferred_initializer_depth = 0

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

    def identifier(self, *, label=False, type_name=False):
        token = self.take()
        keywords = (KEYWORDS - LABEL_CONTEXTUAL_NAMES if label else
                    KEYWORDS if type_name else KEYWORDS - {"var"})
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", token.text) or token.text in keywords:
            token.error("expected a non-keyword Java identifier")
        return token

    @staticmethod
    def type_boundary(token, message, *, code, location=None):
        location = token if location is None else location
        if (token.text in {"byte", "short", "char", "float", "double"}
                or re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", token.text)
                and token.text not in KEYWORDS):
            location.error(message, code=code)
        location.error(message)

    def array_suffix(self, dtype):
        while self.peek().text == "[":
            token = self.take()
            if not self.class_model:
                token.error("array fields require shared Java storage and class initialization",
                            code="java.unsupported.array-storage")
            self.expect("]")
            if isinstance(dtype, ArrayType):
                token.error("multidimensional arrays require the next array semantic domain",
                            code="java.unsupported.array-model-shape")
            if dtype == "void":
                token.error("an array component cannot have void type")
            if dtype not in ARRAY_COMPONENTS:
                token.error(("reference array components" if isinstance(dtype, NamedType) else "floating array components")
                            + " require the next array semantic domain",
                            code="java.unsupported.array-model-shape")
            dtype = ArrayType(dtype)
        return dtype

    def declaration_type(self, token):
        """Retain named reference declarations only in the nonlowering model."""
        if token.text in DECLARED | {"void"}:
            return token.text
        if self.class_model and re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", token.text) and token.text not in KEYWORDS - {"var"}:
            names = [token]
            while self.accept("."):
                names.append(self.identifier())
            if names[-1].text == "var":
                names[-1].error("var is not a declared reference type")
            if self.peek().text == "<":
                self.peek().error("generic reference declarations require the next source type domain",
                                  code="java.unsupported.generic-declarations")
            return NamedType(tuple(names))
        self.type_boundary(token, "source method results require boolean, integral primitives or void",
                           code="java.unsupported.declared-result-type")

    def named_local_start(self):
        """Recognize declaration prefixes without reclassifying value calls."""
        index = 0
        while True:
            token = self.peek(index)
            if token.text in KEYWORDS - {"var"} or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", token.text):
                return False
            if self.peek(index + 1).text != ".":
                break
            index += 2
        following = self.peek(index + 1)
        return (following.text not in KEYWORDS - {"var"}
                and re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", following.text) is not None
                or following.text == "[" and self.peek(index + 2).text == "]"
                or following.text == "<")

    def reference_cast_start(self):
        index = 0
        while True:
            token = self.peek(index)
            if token.text in KEYWORDS - {"var"} or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", token.text):
                return False
            if self.peek(index + 1).text != ".":
                break
            index += 2
        if self.peek(index + 1).text != ")" or self.peek(index).text == "var":
            return False
        operand = self.peek(index + 2)
        # Reference casts use UnaryExpressionNotPlusMinus. Preserve ordinary
        # grouped variables followed by +, -, *, comparisons and postfix ops.
        return (operand.text in {"null", "this", "new", "!", "~", "("}
                or operand.text not in KEYWORDS - {"var"}
                and re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", operand.text) is not None
                or operand.text[0:1].isdigit() or operand.text.startswith(('"', "'")))

    def array_initializer(self, dtype):
        token = self.expect("{")
        values = []
        while self.peek().text != "}":
            if self.peek().text == "{":
                self.peek().error("a primitive array component cannot have an array initializer")
            values.append(self.expression())
            if not self.accept(","):
                break
        self.expect("}")
        return Node("array_initializer", token, dtype, values)

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
        imports, static_imports = [], []
        while self.accept("import"):
            static = self.accept("static")
            imported = [self.identifier()]
            while self.accept("."):
                if self.peek().text == "*":
                    if static:
                        self.peek().error("static on-demand imports require the next source-project lookup domain",
                                          code="java.unsupported.static-imports")
                    self.peek().error("on-demand imports require the next source-project lookup domain", code="java.unsupported.on-demand-imports")
                imported.append(self.identifier())
            self.expect(";")
            if not static and imported[-1].text == "var":
                imported[-1].error("var cannot be imported as a type name")
            if len(imported) < (3 if static else 2):
                imported[0].error("cannot import a type from the unnamed package")
            (static_imports if static else imports).append(tuple(imported))
        program = self.class_declaration(self.modifiers(), "/".join(package),
                                         tuple(imports), tuple(static_imports))
        self.expect("<eof>")
        return program

    def class_declaration(self, mods, package, imports, static_imports, *, enclosing=None, enclosing_names=()):
        # JLS 8.1.1: unsupported abstract classes differ from illegal
        # top-level private/protected/static or method-only modifiers.
        allowed = {"public", "final", "strictfp", "abstract"}
        if enclosing is not None:
            allowed |= {"private", "protected", "static"}
        if mods - allowed or {"abstract", "final"} <= mods:
            self.peek().error("invalid member class modifiers" if enclosing is not None else
                              "unsupported top-level class modifiers")
        if "abstract" in mods:
            self.peek().error("unsupported top-level class modifiers", code="java.unsupported.abstract-classes")
        self.expect("class")
        class_token = self.identifier(type_name=True)
        name = class_token.text
        if name in enclosing_names:
            class_token.error("member class cannot have the same simple name as an enclosing class")
        owner = enclosing.name + "$" + name if enclosing is not None else (package + "/" if package else "") + name
        canonical = enclosing.source_name + "/" + name if enclosing is not None else owner
        program = SourceClass(owner, (), frozenset(mods), imports, token=class_token,
                              static_imports=static_imports,
                              enclosing_name=enclosing.name if enclosing is not None else None,
                              canonical_name=canonical)
        if self.peek().text in {"extends", "implements", "<", "permits"}:
            self.peek().error("inheritance, generics and interfaces require the shared Java runtime", code="java.unsupported.class-supertypes-generics")
        if self.peek().text != "{":
            self.peek().error("inheritance, generics and interfaces require the shared Java runtime")
        self.expect("{")
        methods, fields, constructors, initializers, initialization_steps, members = [], [], [], [], [], []
        while not self.accept("}"):
            if self.accept(";"):
                continue
            mods = self.modifiers()
            if self.peek().text in {"class", "interface", "enum", "record"}:
                if self.peek().text != "class" or self.class_model or "static" not in mods:
                    self.peek().error("only native static member classes have an owned source domain",
                                      code="java.unsupported.member-types")
                members.append(self.class_declaration(mods, package, imports, static_imports,
                                                      enclosing=program, enclosing_names=(*enclosing_names, name)))
                continue
            if not self.class_model and mods == {"static"} and self.peek().text == "{":
                self.peek().error("source method results require boolean, integral primitives or void",
                                  code="java.unsupported.static-initialization")
            if self.class_model and self.peek().text == "{":
                if mods == {"static"}:
                    self.peek().error("static initializer execution requires shared Java class initialization", code="java.unsupported.static-initialization")
                if mods:
                    self.peek().error("invalid instance initializer modifier")
                initializer = self.block()
                # All fields from this ordinal onward occur after this block.
                initializer.value = len(fields)
                initializers.append(initializer)
                initialization_steps.append(initializer)
                continue
            if self.peek().text == name and self.peek(1).text == "(":
                constructor = self.take()
                if mods - {"public", "private", "protected"}:
                    constructor.error("invalid constructor modifier")
                parameters, parameter_types, parameter_final = self.parameters(constructor, receiver=True)
                self.throws_boundary()
                # Keep the original constructor separate from ordinary
                # methods. Native sema admits only private, no-argument,
                # syntactically empty declarations; it never emits a
                # constructor or treats its implicit super() as a no-op.
                constructors.append(SourceMethod(constructor, "void", parameters, self.block(),
                                                 owner=owner, modifiers=frozenset(mods),
                                                 parameter_types=parameter_types, parameter_final=parameter_final,
                                                 is_constructor=True))
                continue
            if "abstract" in mods and mods & {"private", "static", "final", "native", "synchronized", "strictfp"}:
                self.peek().error("only ordinary static source methods are implemented")
            if mods & {"abstract", "native", "synchronized"}:
                self.peek().error("only ordinary static source methods are implemented", code="java.unsupported.special-method-modifiers")
            if not self.class_model and "static" not in mods:
                self.peek().error("only ordinary static source methods are implemented", code="java.unsupported.instance-methods")
            if self.peek().text == "<":
                self.peek().error("source method results require boolean, integral primitives or void",
                                  code="java.unsupported.generic-declarations")
            result = self.take()
            result_type = self.array_suffix(self.declaration_type(result))
            method_name = self.identifier()
            if self.peek().text != "(":
                common_type = result_type
                if result.text == "void":
                    result.error("a field cannot have void type")
                if mods - {"public", "private", "protected", "static", "final"}:
                    result.error("invalid constant-field modifier")
                instance = self.class_model and "static" not in mods
                if not instance and "final" not in mods:
                    result.error("mutable static fields require shared Java class initialization", code="java.unsupported.static-initialization")
                while True:
                    field_type = self.array_suffix(common_type)
                    if isinstance(field_type, ArrayType) and not instance:
                        result.error("static array fields require shared Java class initialization",
                                     code="java.unsupported.array-storage")
                    initialized = bool(self.accept("="))
                    if instance and "final" in mods and not (isinstance(field_type, ArrayType) and initialized):
                        result.error("final instance fields require constructor definite-unassignment semantics",
                                     code="java.unsupported.final-instance-fields")
                    if not initialized and not instance:
                        method_name.error("blank final fields require shared Java class initialization", code="java.unsupported.static-initialization")
                    initializer = ((self.array_initializer(field_type)
                                    if isinstance(field_type, ArrayType) and self.peek().text == "{" else self.expression())
                                   if initialized else None)
                    field = SourceField(method_name, field_type, initializer, frozenset(mods), owner, len(fields))
                    fields.append(field)
                    if instance and initialized:
                        initialization_steps.append(field)
                    if not self.accept(","):
                        break
                    method_name = self.identifier()
                self.expect(";")
                continue
            # In model mode, legacy result brackets may follow the parameter
            # list (JLS 8.4); scalar native diagnostics keep their old boundary.
            if not self.class_model and result.text not in DECLARED | {"void"}:
                self.type_boundary(result, "source method results require boolean, integral primitives or void",
                                   code="java.unsupported.declared-result-type")
            parameters, parameter_types, parameter_final = self.parameters(method_name, receiver="static" not in mods)
            result_type = self.array_suffix(result_type)
            self.throws_boundary()
            if result.text not in DECLARED | {"void"} and not isinstance(result_type, (ArrayType, NamedType)):
                self.type_boundary(result, "source method results require boolean, integral primitives or void",
                                   code="java.unsupported.declared-result-type")
            methods.append(SourceMethod(method_name, result_type, parameters, self.block(),
                                        owner=owner, modifiers=frozenset(mods), parameter_types=parameter_types,
                                        parameter_final=parameter_final))
        program.methods, program.fields, program.constructors = tuple(methods), tuple(fields), tuple(constructors)
        program.instance_initializers, program.initialization_steps = tuple(initializers), tuple(initialization_steps)
        program.member_classes = tuple(members)
        return program

    def throws_boundary(self):
        if self.peek().text == "throws":
            self.peek().error("throws clauses require the owned Throwable hierarchy and checked-exception analysis",
                              code="java.unsupported.throwable-types")

    def parameters(self, token, *, receiver=False):
        self.expect("(")
        parameters, types, finals = [], [], []
        if self.peek().text != ")":
            while True:
                finals.append(bool(self.accept("final")))
                if self.peek().text == "final":
                    self.peek().error("duplicate final parameter modifier")
                dtype = self.take()
                if dtype.text not in DECLARED and not self.class_model:
                    self.type_boundary(dtype, "source parameters require boolean or integral primitives",
                                       code="java.unsupported.declared-parameter-type")
                parameter_type = self.array_suffix(self.declaration_type(dtype))
                if parameter_type == "void":
                    dtype.error("a parameter cannot have void type")
                parameters.append(self.identifier())
                parameter_type = self.array_suffix(parameter_type)
                if parameter_type not in DECLARED and not isinstance(parameter_type, (ArrayType, NamedType)):
                    self.type_boundary(dtype, "source parameters require boolean or integral primitives",
                                       code="java.unsupported.declared-parameter-type")
                types.append(parameter_type)
                if not self.accept(","):
                    break
        self.expect(")")
        if sum(2 if dtype in {"long", "double"} else 1 for dtype in types) + int(receiver) > 255:
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
        if token.text not in KEYWORDS - LABEL_CONTEXTUAL_NAMES and self.peek(1).text == ":":
            label = self.identifier(label=True)
            self.expect(":")
            return Node("label", label, label.text, [self.statement()])
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
            initial = Node("empty", token) if self.peek().text == ";" else self.for_expressions(allow_declaration=True)
            if initial.kind == "enhanced_var":
                self.expect(":")
                expression = self.expression()
                self.expect(")")
                return Node("enhanced_for", token, children=[initial, expression, self.statement()])
            self.expect(";")
            condition = Node("bool", token, True) if self.peek().text == ";" else self.expression()
            self.expect(";")
            update = Node("empty", token) if self.peek().text == ")" else self.for_expressions()
            self.expect(")")
            return Node("for", token, children=[initial, condition, update, self.statement()])
        if self.class_model and self.accept("throw"):
            expression = self.expression()
            self.expect(";")
            return Node("throw", token, children=[expression])
        if self.accept("return"):
            children = [] if self.peek().text == ";" else [self.expression()]
            self.expect(";")
            return Node("return", token, children=children)
        if token.text in ("break", "continue"):
            self.take()
            label = None if self.peek().text == ";" else self.identifier(label=True).text
            self.expect(";")
            return Node(token.text, token, label)
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
                elif self.class_model and self.peek().text == "throw":
                    statements = [self.statement()]
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

    def for_expressions(self, *, allow_declaration=False):
        """JLS 14.14.1: a statement-expression list is not a comma operator.

        Each item has the same restricted grammar as an expression statement;
        a sequence neither adds a lexical scope nor admits arbitrary values.
        Local declarations remain a distinct alternative to the list.
        """
        first = self.simple(allow_declaration=allow_declaration, allow_enhanced=allow_declaration)
        if (first.kind in {"var", "enhanced_var"} or first.kind == "sequence" and first.value == "local_declarations"
                or self.peek().text != ","):
            return first
        expressions = [first]
        while self.accept(","):
            expressions.append(self.simple())
        return Node("sequence", first.token, children=expressions)

    def local_declaration(self, type_token, *, is_final, allow_enhanced=False):
        """JLS 14.4/6.3: ordered declarators in the enclosing lexical scope.

        Brackets after a name belong only to that declarator, while brackets
        after the shared element type belong to all of them. Keep every var
        node separate: Sema declares each binding immediately before checking
        its own initializer, then exposes it to subsequent declarators.
        """
        common_type = self.array_suffix(self.declaration_type(type_token))
        declarations = []
        while True:
            name = self.identifier()
            dtype = self.array_suffix(common_type)
            if dtype not in DECLARED and not isinstance(dtype, (ArrayType, NamedType)):
                self.type_boundary(type_token, "source locals require boolean or integral primitives",
                                   code="java.unsupported.declared-local-type")
            if allow_enhanced and not declarations and self.peek().text == ":":
                # The iteration variable has no source initializer, but is not
                # a blank final local: each iteration initializes it (14.14.2).
                # Retain the original expression separately from this binding;
                # its scope starts only in the body, never in that expression.
                if not self.class_model:
                    self.peek().error("enhanced for requires shared managed-array or Iterable lowering",
                                      code="java.unsupported.array-storage")
                return Node("enhanced_var", name, dtype, is_final=is_final)
            if self.accept("="):
                initializer = (self.array_initializer(dtype) if isinstance(dtype, ArrayType) and self.peek().text == "{"
                               else self.expression())
                children = [initializer]
            else:
                children = []
            if is_final and not children and self.class_model:
                # Native scalar methods run a separate typed-AST DU pass.
                # Model constructors/initializers/enhanced-for and reference
                # storage retain their boundary until every frame is covered.
                name.error("blank final locals require the class-model definite-unassignment domain",
                           code="java.unsupported.blank-final-local")
            declarations.append(Node("var", name, dtype, children, is_final=is_final))
            if not self.accept(","):
                break
        return (declarations[0] if len(declarations) == 1 else
                Node("sequence", type_token, "local_declarations", declarations))

    def simple(self, allow_declaration=False, *, allow_enhanced=False):
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
        following = self.peek(1)
        inferred = (self.peek().text == "var" and
                    (following.text == "[" and self.peek(2).text == "]"
                     or following.text not in KEYWORDS - {"var"}
                     and re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", following.text)))
        if inferred:
            if not allow_declaration:
                token.error("a local declaration requires a block or for initializer")
            return self.inferred_local(is_final=is_final, allow_enhanced=allow_enhanced)
        if self.peek().text in DECLARED or (self.class_model and self.peek().text in ARRAY_COMPONENTS):
            type_token = self.take()
            if not allow_declaration:
                token.error("a local declaration requires a block or for initializer")
            return self.local_declaration(type_token, is_final=is_final, allow_enhanced=allow_enhanced)
        if self.named_local_start():
            if not allow_declaration:
                token.error("a local declaration requires a block or for initializer")
            if not self.class_model:
                self.peek().error("source locals require boolean or integral primitives",
                                  code="java.unsupported.declared-local-type")
            return self.local_declaration(self.take(), is_final=is_final, allow_enhanced=allow_enhanced)
        if is_final:
            self.type_boundary(self.peek(), "final local declarations require boolean or integral primitives",
                               code="java.unsupported.declared-local-type", location=token)
        if self.peek(1).text in ASSIGNMENTS | {"++", "--"}:
            name = self.identifier()
            operation = self.take().text
            children = [] if operation in ("++", "--") else [self.expression()]
            return Node("assign", name, operation, children)
        expression = self.expression(1, statement_target=True) if self.class_model else self.expression()
        if self.class_model and self.peek().text in ASSIGNMENTS | {"++", "--"}:
            operation = self.take()
            target = expression
            while target.kind == "group":
                target = target.children[0]
            if (target.kind == "name" and "." not in target.value
                    or target.kind == "receiver_field"):
                # JLS 15.8.5: grouping preserves the variable. Keep an explicit
                # receiver distinct from a bare name so local shadowing cannot
                # retarget this.field; neither is an array-component access.
                rhs = [] if operation.text in {"++", "--"} else [self.expression()]
                kind = "field_assign" if target.kind == "receiver_field" else "assign"
                return Node(kind, target.token, operation.text, rhs)
            if expression.kind not in {"array_access", "array_length", "group", "name"}:
                operation.error("array assignment requires an array component variable")
            children = [expression] + ([] if operation.text in {"++", "--"} else [self.expression()])
            return Node("array_assign", operation, operation.text, children)
        if expression.kind not in {"call", "mutation"} and not (self.class_model and expression.kind in {"receiver_call", "class_new"}):
            expression.token.error("only assignment, increment or method invocation is an expression statement")
        return Node("expression", token, children=[expression])

    def inferred_local(self, *, is_final, allow_enhanced):
        token = self.expect("var")
        if self.peek().text == "[":
            self.peek().error("var local declarations cannot add array dimensions")
        name = self.identifier()
        if self.peek().text == "[":
            self.peek().error("var local declarations cannot add array dimensions")
        if allow_enhanced and self.peek().text == ":":
            self.peek().error("enhanced for inference requires the next array or Iterable inference domain",
                              code="java.unsupported.local-inference-domain")
        if not self.accept("="):
            name.error("an inferred local variable requires an initializer")
        if self.peek().text == "{":
            self.peek().error("an inferred local cannot use a bare array initializer")
        self.inferred_initializer_depth += 1
        try:
            initializer = self.expression()
        finally:
            self.inferred_initializer_depth -= 1
        if self.peek().text == ",":
            self.peek().error("an inferred local declaration must have exactly one declarator")
        return Node("var", name, "var", [initializer], is_final=is_final, inference_token=token)

    def expression(self, minimum=0, *, statement_target=False):
        token = self.take()
        if token.text in ("++", "--"):
            node = Node("mutation", token, token.text, [self.expression(11)])
        elif token.text in ("+", "-", "~", "!"):
            node = Node("unary", token, token.text, [self.expression(11)])
        elif token.text == "(":
            if self.class_model and self.peek().text in ARRAY_COMPONENTS and self.peek(1).text == "[":
                token.error("array casts require the next reference conversion domain",
                            code="java.unsupported.array-model-shape")
            if self.class_model and self.reference_cast_start():
                token.error("reference casts require the next reference conversion domain",
                            code="java.unsupported.object-expressions")
            if self.peek().text in DECLARED and self.peek(1).text == ")":
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
        elif (self.class_model or self.inferred_initializer_depth) and token.text == "null":
            node = Node("null", token)
        elif self.class_model and token.text == "new" and self.peek().text in ARRAY_COMPONENTS:
            component = self.take()
            self.expect("[")
            dtype = ArrayType(component.text)
            if self.accept("]"):
                if self.peek().text == "[":
                    self.peek().error("multidimensional arrays require the next array semantic domain",
                                      code="java.unsupported.array-model-shape")
                node = self.array_initializer(dtype)
            else:
                dimension = self.expression()
                self.expect("]")
                if self.peek().text == "[":
                    self.peek().error("multidimensional arrays require the next array semantic domain",
                                      code="java.unsupported.array-model-shape")
                node = Node("array_new", token, dtype, [dimension])
        elif (self.class_model and token.text == "new" and
              re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", self.peek().text) and
              self.peek().text not in KEYWORDS):
            name = self.identifier()
            if self.peek().text == ".":
                self.peek().error("qualified class creation requires complete implicit/member-type metadata; use a resolved single-type import",
                                  code="java.unsupported.implicit-type-metadata")
            if self.peek().text == "<":
                self.peek().error("generic/diamond class creation requires the next source type domain",
                                  code="java.unsupported.generic-declarations")
            if self.peek().text == "[":
                self.peek().error("reference array creation requires the next array semantic domain",
                                  code="java.unsupported.array-model-shape")
            self.expect("(")
            node = Node("class_new", token, NamedType((name,)), self.arguments())
            if self.peek().text == "{":
                self.peek().error("anonymous class creation requires the nested-class source domain",
                                  code="java.unsupported.member-types")
            if self.peek().text == ".":
                self.peek().error("created-reference member access requires the next managed receiver domain",
                                  code="java.unsupported.object-expressions")
        elif token.text in ("true", "false"):
            node = Node("bool", token, token.text == "true")
        elif token.text.startswith("'"):
            node = Node("char", token, token.text)
        elif token.text.startswith('"'):
            node = Node("string", token, token.string_units)
        elif FLOAT_LITERAL.fullmatch(token.text):
            node = Node("floating", token, token.text)
        elif token.text[0:1].isdigit():
            node = Node("integer", token, token.text)
        elif re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", token.text) and token.text not in KEYWORDS - {"var"}:
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
            # An unimplemented grammar prefix is not proof the whole input is
            # valid. Known malformed expression starts remain ordinary errors.
            if token.text in {"new", "this", "super", "null"}:
                token.error("unsupported Java expression or statement", code="java.unsupported.object-expressions")
            if token.text == "switch":
                token.error("unsupported Java expression or statement", code="java.unsupported.switch-expressions")
            if self.class_model and token.text == "throw":
                token.error("throw is a statement, not an expression")
            if token.text in {"throw", "try", "synchronized"}:
                token.error("unsupported Java expression or statement", code="java.unsupported.runtime-statements")
            if token.text == "assert":
                token.error("unsupported Java expression or statement", code="java.unsupported.assert-statements")
            if token.text in {"byte", "short", "char", "float", "double"}:
                token.error("unsupported Java expression or statement", code="java.unsupported.declared-local-type")
            if token.text in {"class", "interface", "enum", "record"}:
                token.error("unsupported Java expression or statement", code="java.unsupported.local-types")
            token.error("unsupported Java expression or statement")
        if not self.class_model and (self.peek().text == "[" or
                                     (self.peek().text == "." and self.peek(1).text == "length")):
            self.peek().error("array access and length require shared managed-array lowering",
                              code="java.unsupported.array-storage")
        while self.class_model and self.peek().text in {"[", "."}:
            postfix = self.take()
            if postfix.text == "[":
                index = self.expression()
                self.expect("]")
                node = Node("array_access", postfix, children=[node, index])
            else:
                member = self.identifier()
                if self.peek().text == "(":
                    member.error("reference method invocation requires the next managed receiver domain",
                                 code="java.unsupported.object-expressions")
                if member.text != "length":
                    member.error("array members other than length require the next reference domain",
                                 code="java.unsupported.array-model-shape")
                node = Node("array_length", member, children=[node])
        if not statement_target and self.peek().text in {"++", "--"}:
            operator = self.take()
            node = Node("mutation", operator, "post" + operator.text, [node])
        while PRECEDENCE.get(self.peek().text, 0) >= max(minimum, 1):
            operator = self.take()
            node = Node("binary", operator, operator.text,
                        [node, self.expression(PRECEDENCE[operator.text] + 1)])
        if minimum <= 1 and self.peek().text == "?":
            operator = self.take()
            yes = self.expression()
            self.expect(":")
            node = Node("conditional", operator, children=[node, yes, self.expression(1)])
        if minimum == 0 and self.peek().text in ASSIGNMENTS:
            operator = self.take()
            node = Node("mutation", operator, operator.text, [node, self.expression()])
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
