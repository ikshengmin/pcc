"""Owned Go scanner, source AST and recursive-descent parser.

The scanner implements Go's newline/EOF semicolon insertion, including block
comments containing newlines. No source rewriting or other-language parser is
used. Unsupported grammar fails with a source location, never discarded tokens.
"""

from copy import deepcopy
from dataclasses import dataclass, field
import unicodedata

from pcc.frontends.go.types import Results, TYPE_NAMES
from pcc.frontends.go.diagnostics import GoDiagnostic, UnsupportedGo
from pcc.frontends.go.strings import literal_bytes


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


def scan(source, filename="<go>", directives=None):
    """Tokenize one file; recognized //go:linkname directives go to directives."""
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
            comment = source[pos:len(source) if end < 0 else end]
            # The Go compiler checks directive placement before interpreting
            # its keyword or arguments. Only source whitespace can precede a
            # //go: directive on this line; a preceding block comment is not
            # whitespace for this check, even if it began on an earlier line.
            if comment.startswith("//go:"):
                line_start = source.rfind("\n", 0, pos) + 1
                prefix = source[line_start:pos]
                if line_start == 0 and prefix.startswith("\ufeff"):
                    # scan() already consumed the sole permitted initial BOM.
                    # It is an encoding marker, not a preceding source token.
                    prefix = prefix[1:]
                if prefix.strip(" \t\r"):
                    raise GoDiagnostic(Token("comment", comment, filename, line, column),
                                       "misplaced compiler directive")
            # These are object-symbol ownership directives, not inert text.
            # Follow the official compiler's exact keyword-plus-space form;
            # block comments, literals and similarly spelled comments are
            # unaffected. Do not emit an ordinary global for a linker alias.
            if comment.startswith(("//go:linkname ", "//go:linknamestd ")):
                # Go strings.Fields uses Unicode White_Space; Python's
                # str.split additionally treats U+001C..U+001F as whitespace.
                whitespace = (" \t\n\v\f\r\u0085\u00a0\u1680\u2000\u2001\u2002\u2003"
                              "\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000")
                normalized = "".join(" " if char in whitespace else char for char in comment[2:])
                fields = [word for word in normalized.split(" ") if word]
                token = Token("comment", comment, filename, line, column)
                if len(fields) not in (2, 3):
                    raise GoDiagnostic(token, "usage: //" + fields[0] + " localname [linkname]")
                if fields[0] != "go:linkname" or directives is None:
                    # linknamestd is the standard library's own variant.
                    raise UnsupportedGo(token, 'go.unsupported.linkname-directives',
                                        "linkname directives require owned object-symbol binding and runtime ownership")
                directives.append(Linkname(token, fields[1], fields[2] if len(fields) == 3 else None))
            advance(comment)
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
class PointerSyntax:
    token: Token
    element: object


@dataclass
class ArraySyntax:
    token: Token
    length: object
    element: object


@dataclass
class SliceSyntax:
    token: Token
    element: object


@dataclass
class FunctionTypeSyntax:
    """`func(params) results`, retained as written (https://go.dev/ref/spec#Function_types)."""
    token: Token
    parameters: list
    names: list
    result: object
    result_names: list


@dataclass
class FunctionLiteral:
    """`func(params) results { body }` (https://go.dev/ref/spec#Function_literals)."""
    signature: FunctionTypeSyntax
    body: list


@dataclass
class InterfaceMethodSyntax:
    token: Token
    parameters: tuple
    result: object
    variadic: bool = False


@dataclass
class InterfaceElementSyntax:
    token: Token
    terms: tuple


@dataclass
class InterfaceSyntax:
    token: Token
    elements: tuple


@dataclass
class TypeName:
    token: Token
    qualifier: str | None = None


@dataclass
class StructSyntax:
    token: Token
    fields: list
    tags: list = field(default_factory=list)


@dataclass
class TypeDeclaration:
    token: Token
    type: object
    alias: bool = False
    resolved: object = None
    resolving: bool = False
    pointer_depth: int = 0
    identity: object = None
    signature_depth: int = 0


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
    callee: object = None
    receiver: object = None
    parenthesized: bool = False
    keys: list = field(default_factory=list)
    indices: list = field(default_factory=list)
    addressable: bool = False
    # Lexical region bounds, not alias identities or extended stack lifetimes.
    storage_depth: int = 0
    pointer_depth: int = 0
    pointer_nonnull: bool = False
    iteration_binding: bool = False
    indirect_receiver: bool = False
    # Set by the checker for an element literal whose type was elided.
    elided_type: object = None


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
    iota: int | None = None
    cases: list = field(default_factory=list)
    range_declaration: bool = False
    range_index: object = None
    evaluate_range: bool = True
    label: Token | None = None
    control_target: object = None


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
    receiver: object = None
    type_parameters: list = field(default_factory=list)
    type_bindings: dict = field(default_factory=dict)


@dataclass
class Import:
    token: Token
    alias: str | None
    path: str


@dataclass
class Linkname:
    """One //go:linkname localname [importpath.name] directive of a file."""
    token: Token
    local: str
    target: str | None


@dataclass
class Program:
    token: Token
    functions: list
    constants: list
    imports: list = field(default_factory=list)
    variables: list = field(default_factory=list)
    types: list = field(default_factory=list)
    linknames: list = field(default_factory=list)


BINARY_PRECEDENCE = {
    "||": 1, "&&": 2,
    "==": 3, "!=": 3, "<": 3, "<=": 3, ">": 3, ">=": 3,
    "+": 4, "-": 4, "|": 4, "^": 4,
    "*": 5, "/": 5, "%": 5, "<<": 5, ">>": 5, "&": 5, "&^": 5,
}


class GoParser:
    def parse(self, source, filename="<go>", *, require_main=True):
        linknames = []
        self.tokens = scan(source, filename, linknames)
        self.index = 0
        self.named_literals = True
        self.take("package")
        package = self.take("name")
        if require_main and package.text != "main":
            self.fail(package, "unsupported package: initial frontend requires package main")
        self.take(";")
        if package.text == "_":
            self.fail(package, "package name cannot be blank")
        functions, constants, imports, variables, types = [], [], [], [], []
        declarations_seen = False
        while not self.at("eof"):
            if self.at(";"):
                self.fail(self.current, "empty package declarations are not permitted")
            if not self.at("import"):
                declarations_seen = True
            if self.at("import"):
                if declarations_seen:
                    self.fail(self.current, "imports must precede package declarations")
                imports.extend(self.import_declaration())
            elif self.at("func"):
                functions.append(self.function())
            elif self.at("const"):
                constants.extend(self.declarations())
            elif self.at("var"):
                variables.extend(self.declarations())
            elif self.at("type"):
                types.extend(self.type_declarations())
            else:
                self.fail(self.current, "unsupported package declaration; expected func, const or var")
            self.end_statement()
        if linknames and not any(imported.path == "unsafe" for imported in imports):
            # cmd/compile: the directive subverts the type system, so a file
            # must import unsafe to use it.
            self.fail(linknames[0].token, '//go:linkname only allowed in Go files that import "unsafe"')
        return Program(package, functions, constants, imports, variables, types, linknames)

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

    def unsupported(self, token, code, message):
        raise UnsupportedGo(token, code, message)

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
            if grouped and self.at(";"):
                self.fail(self.current, "empty import specifications are not permitted")
            if self.at("name"):
                alias = self.take("name").text
            else:
                alias = "." if self.accept(".") else None
            token = self.take("string")
            # ImportPath is a Go string literal, not its quoted source spelling.
            # Decode once before validation and dependency identity/lookup.
            try:
                path = literal_bytes(token).decode("utf-8")
            except UnicodeDecodeError:
                self.fail(token, "invalid UTF-8 in import path")
            # Go permits graphic Unicode categories, excluding all spaces.
            # L/M/N/P/S remain after excluding the graphic Zs category.
            if (not path or any(unicodedata.category(char)[0] not in "LMNPS" for char in path) or
                    any(char in '!"#$%&\'()*,:;<=>?[\\]^`{|}\ufffd' for char in path)):
                self.fail(token, "unsupported or invalid import path")
            imports.append(Import(token, alias, path))
            if not grouped:
                break
            if not self.accept(";") and not self.at(")"):
                self.fail(self.current, "expected import separator")
        return imports

    def datatype(self, *, literal=False, signature=False, constraint=False):
        if self.accept("("):
            dtype = self.datatype(signature=signature, constraint=constraint)
            self.take(")")
            return dtype
        if self.at("interface"):
            return self.interface_type()
        if self.at("func"):
            return self.function_type()
        if signature and self.current.kind in ("map", "chan", "<-"):
            token = self.current
            self.constraint_type()
            self.unsupported(token, 'go.unsupported.interface-types',
                             "this interface signature type constructor needs semantic metadata support")
        if self.at("*"):
            token = self.take("*")
            return PointerSyntax(token, self.datatype(signature=signature))
        if self.at("["):
            token = self.take("[")
            if self.at("]"):
                # A slice type is ordinary syntax; outside signatures the
                # checker reports the missing backing storage at its use.
                self.take("]")
                return SliceSyntax(token, self.datatype(signature=signature))
            if self.accept("..."):
                if not literal:
                    self.fail(token, "inferred array length is only valid in a composite literal")
                length = None
            else:
                length = self.expression()
            self.take("]")
            element = self.datatype(signature=signature)
            return ArraySyntax(token, length, element)
        if self.at("struct"):
            token = self.take("struct")
            self.take("{")
            fields, tags = [], []
            while not self.at("}"):
                names = [self.take("name")]
                while self.accept(","):
                    names.append(self.take("name"))
                if self.at(";") or self.at("}") or self.at(".") or self.at("string"):
                    self.unsupported(names[0], 'go.unsupported.embedded-fields', "embedded fields require method/promotion support")
                dtype = self.datatype(signature=signature)
                tag = self.accept("string")
                fields.extend((name, dtype) for name in names)
                tags.extend([tag] * len(names))
                self.end_statement()
            self.take("}")
            return StructSyntax(token, fields, tags)
        token = self.take("name")
        qualifier = token if self.accept(".") else None
        if qualifier is not None:
            token = self.take("name")
        if self.accept("["):
            # An instantiated type is valid syntax even when this frontend
            # cannot resolve its generic declaration. Consume its actual type
            # arguments before reporting the capability frontier; malformed
            # argument lists remain ordinary source syntax errors.
            self.constraint_type()
            while self.accept(",") and not self.at("]"):
                self.constraint_type()
            self.take("]")
            self.unsupported(token, 'go.unsupported.generic-boundaries',
                             "instantiated types require generic type resolution")
        if qualifier is not None:
            return TypeName(token, qualifier.text)
        if token.text in TYPE_NAMES:
            return TYPE_NAMES[token.text]
        capability = ({"error": "interface-types", "any": "interface-types",
                       "float32": "floating-types", "float64": "floating-types",
                       "complex64": "complex-types", "complex128": "complex-types"}).get(token.text)
        if capability is not None:
            self.unsupported(token, "go.unsupported." + capability,
                             f"unsupported type {token.text!r}; shared runtime or numeric support is missing")
        if token.text == "comparable":
            if constraint:
                return TypeName(token)
            self.fail(token, "comparable is only permitted as a type constraint")
        return TypeName(token)

    def interface_type(self):
        """Retain method signatures and ordered type elements, not value ABI."""
        token = self.take("interface")
        self.take("{")
        elements = []
        while not self.at("}"):
            start = self.current
            if self.at("name") and self.tokens[self.index + 1].kind == "[":
                end = self.constraint_bracket_end(self.index + 1)
                if end is not None and self.tokens[end].kind == "(":
                    self.fail(start, "interface methods must not declare type parameters")
                self.constraint_element()
                self.unsupported(start, 'go.unsupported.generic-boundaries',
                                 "instantiated interface elements require generic type resolution")
            if self.at("name") and self.tokens[self.index + 1].kind == "(":
                name = self.take("name")
                self.take("(")
                parameters, names, variadic = self.interface_parameters()
                results, result_names = [], []
                if self.accept("("):
                    results, result_names, _ = self.interface_parameters(allow_variadic=False)
                elif self.current.kind in ("name", "*", "[", "struct", "interface", "map", "chan", "<-", "func"):
                    results = [self.datatype(signature=True)]
                declared = set()
                for item in names + result_names:
                    if item is not None and item.text != "_":
                        if item.text in declared:
                            self.fail(item, "duplicate parameter or result name " + item.text)
                        declared.add(item.text)
                result = Results(tuple(results)) if len(results) > 1 else results[0] if results else "void"
                elements.append(InterfaceMethodSyntax(name, tuple(parameters), result, variadic))
            else:
                terms = []
                while True:
                    approximate = bool(self.accept("~"))
                    terms.append((approximate, self.datatype(signature=True, constraint=True)))
                    if not self.accept("|"):
                        break
                elements.append(InterfaceElementSyntax(start, tuple(terms)))
            self.end_statement()
        self.take("}")
        return InterfaceSyntax(token, tuple(elements))

    def interface_parameters(self, *, allow_variadic=True):
        types, names, naming, variadic = [], [], None, False
        while not self.at(")"):
            named = self.named_parameters()
            if naming is not None and naming != named:
                self.fail(self.current, "mixed named and unnamed parameters")
            naming = named
            group = [self.take("name")] if named else [None]
            if named:
                while self.accept(","):
                    group.append(self.take("name"))
            ellipsis = self.accept("...")
            if ellipsis:
                if not allow_variadic:
                    self.fail(ellipsis, "variadic result is not permitted")
                if len(group) != 1:
                    self.fail(ellipsis, "variadic parameter must be exactly one parameter")
                variadic = True
            dtype = self.datatype(signature=True)
            if ellipsis:
                dtype = SliceSyntax(ellipsis, dtype)
            types.extend([dtype] * len(group))
            names.extend(group)
            if len(types) > 256:
                self.unsupported(self.current, 'go.unsupported.limit-function-arity',
                                 "interface signature exceeds implementation limit (256 results/parameters)")
            if not self.accept(","):
                break
            if variadic and not self.at(")"):
                self.fail(self.current, "variadic parameter must be last")
        self.take(")")
        return types, names, variadic

    def type_declarations(self):
        self.take("type")
        grouped = bool(self.accept("("))
        declarations = []
        while not grouped or not self.accept(")"):
            name = self.take("name")
            # `[N]element` remains an ordinary array type declaration. The
            # supported generic-declaration frontier must not steal that
            # preexisting grammar merely because both begin with a bracket.
            if (self.at("[") and self.tokens[self.index + 1].kind == "name"
                    and self.tokens[self.index + 2].kind in
                    ("name", ",", "~", "interface", "[", "struct", "chan", "map", "func")):
                self.unsupported(name, 'go.unsupported.generic-boundaries',
                                 "generic type declarations require separate identity/layout checking")
            alias = bool(self.accept("="))
            declarations.append(TypeDeclaration(name, self.datatype(), alias))
            if not grouped:
                break
            if not self.accept(";") and not self.at(")"):
                self.fail(self.current, "expected type declaration separator")
        return declarations

    def named_parameters(self):
        # Identifier lists need a following type. A comma-separated list ending
        # directly in ')' is instead a list of unnamed result/parameter types.
        index = self.index
        while self.tokens[index].kind == "name":
            index += 1
            if self.tokens[index].kind == "[":
                end = self.constraint_bracket_end(index)
                if end is not None and self.tokens[end].kind in (",", ")"):
                    return False  # Box[T], not the named array parameter x [N]T.
            if self.tokens[index].kind in ("name", "[", "struct", "*", "interface", "(",
                                           "map", "chan", "<-", "func", "..."):
                return True
            if self.tokens[index].kind != ",":
                return False
            index += 1
        return False

    def parameter_list(self):
        types, names = [], []
        named = self.named_parameters()
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
                self.unsupported(self.current, 'go.unsupported.limit-function-arity', "function exceeds implementation limit (256 results/parameters)")
            if not self.accept(","):
                break
        self.take(")")
        return types, names

    def function(self):
        self.take("func")
        receiver = None
        if self.accept("("):
            receiver_types, receiver_names = self.parameter_list()
            if len(receiver_types) != 1:
                self.fail(self.current, "method receiver must be exactly one non-variadic parameter")
            receiver = (receiver_names[0], receiver_types[0])
        name = self.take("name")
        type_parameters = self.type_parameter_list() if self.accept("[") else []
        if receiver is not None and type_parameters:
            self.fail(name, "methods must not declare function type parameters")
        self.take("(")
        types, names = self.parameter_list()
        blank = Token("name", "_", name.filename, name.line, name.column)
        params = [(token or blank, dtype) for token, dtype in zip(names, types)]
        if receiver is not None:
            receiver = (receiver[0] or blank, receiver[1])
        result, result_names = self.function_results()
        return Function(name, params, result, self.block(), result_names=result_names,
                        receiver=receiver, type_parameters=type_parameters)

    def type_parameter_list(self):
        parameters = []
        if self.at("]"):
            self.fail(self.current, "type parameter list must not be empty")
        while True:
            names = [self.take("name")]
            while self.accept(","):
                names.append(self.take("name"))
            constraint = self.current
            # Parse type terms before reporting the capability boundary. A
            # valid exact *int, []int or struct constraint is not invalid Go;
            # conversely, a missing pointee/element/field type remains syntax
            # invalid. This reader never supplies a representation to Sema.
            simple_name = self.at("name") and self.tokens[self.index + 1].kind in (",", "]")
            self.constraint_element()
            if not self.at(",") and not self.at("]"):
                self.fail(self.current, "expected ',' or ']' after type parameter constraint")
            if not simple_name:
                self.unsupported(constraint, 'go.unsupported.generic-boundaries',
                                 "non-name constraints require general type-set resolution")
            parameters.extend((name, constraint) for name in names)
            if len(parameters) > 256:
                self.unsupported(constraint, 'go.unsupported.limit-function-arity',
                                 "function exceeds implementation limit (256 type parameters)")
            if not self.accept(",") or self.at("]"):
                break
        self.take("]")
        return parameters

    def constraint_element(self):
        """Syntax-only TypeElem reader for the explicit type-set frontier."""
        while True:
            self.accept("~")
            self.constraint_type()
            if not self.accept("|"):
                return

    def constraint_type(self):
        # Unlike datatype(), this syntax reader admits runtime type spellings
        # without admitting their storage or operations. Its entire result is
        # discarded at the generic-boundaries diagnostic above.
        if self.accept("*"):
            self.constraint_type()
        elif self.accept("["):
            if not self.accept("]"):
                self.expression()
                self.take("]")
            self.constraint_type()
        elif self.accept("("):
            self.constraint_type()
            self.take(")")
        elif self.accept("map"):
            self.take("[")
            self.constraint_type()
            self.take("]")
            self.constraint_type()
        elif self.accept("chan"):
            self.accept("<-")
            self.constraint_type()
        elif self.accept("<-"):
            self.take("chan")
            self.constraint_type()
        elif self.accept("func"):
            self.constraint_signature()
        elif self.accept("struct"):
            self.take("{")
            while not self.at("}"):
                if self.at("*"):
                    self.take("*")
                    self.constraint_type_name()
                elif self.constraint_embedded_field():
                    self.constraint_type_name()
                else:
                    self.take("name")
                    while self.accept(","):
                        self.take("name")
                    self.constraint_type()
                self.accept("string")  # Optional field tag.
                self.end_statement()
            self.take("}")
        elif self.accept("interface"):
            self.take("{")
            while not self.at("}"):
                if self.at("name") and self.tokens[self.index + 1].kind == "(":
                    self.take("name")
                    self.constraint_signature()
                else:
                    self.constraint_element()
                self.end_statement()
            self.take("}")
        else:
            self.constraint_type_name()

    def constraint_embedded_field(self):
        if not self.at("name"):
            return False
        following = self.tokens[self.index + 1].kind
        if following in (";", "}", ".", "string"):
            return True
        if following != "[":
            return False
        # X[A] alone is an instantiated embedded type; X [N]int instead
        # declares a named array field. Do not guess from '[' alone.
        index = self.constraint_bracket_end(self.index + 1)
        return index is not None and self.tokens[index].kind in (";", "}", "string")

    def constraint_bracket_end(self, index):
        depth = 0
        while self.tokens[index].kind != "eof":
            kind = self.tokens[index].kind
            depth += kind == "["
            depth -= kind == "]"
            index += 1
            if depth == 0:
                return index
        return None

    def constraint_type_name(self):
        self.take("name")
        if self.accept("."):
            self.take("name")
        if self.accept("["):
            self.constraint_type()
            while self.accept(",") and not self.at("]"):
                self.constraint_type()
            self.take("]")

    def constraint_signature(self):
        self.take("(")
        self.constraint_parameters()
        if self.accept("("):
            self.constraint_parameters(allow_variadic=False)
        elif self.current.kind in ("name", "*", "[", "struct", "interface", "map", "chan", "<-", "func"):
            self.constraint_type()

    def constraint_parameters(self, *, allow_variadic=True):
        naming = None
        while not self.at(")"):
            # Distinguish identifier lists followed by a type from unnamed
            # types. Scope/type validity is intentionally not claimed here.
            index, named = self.index, False
            while self.tokens[index].kind == "name":
                index += 1
                if self.tokens[index].kind == "[":
                    end = self.constraint_bracket_end(index)
                    if end is not None and self.tokens[end].kind in (",", ")"):
                        break  # Unnamed instantiated type, not a named array.
                if self.tokens[index].kind in (
                    "name", "*", "[", "(", "struct", "interface", "map", "chan", "<-", "func", "...",
                ):
                    named = True
                    break
                if self.tokens[index].kind != ",":
                    break
                index += 1
            if naming is not None and naming != named:
                self.fail(self.current, "mixed named and unnamed parameters")
            naming = named
            if named:
                self.take("name")
                while self.accept(","):
                    self.take("name")
            variadic = bool(self.accept("..."))
            if variadic and not allow_variadic:
                self.fail(self.current, "variadic result is not permitted")
            self.constraint_type()
            if not self.accept(","):
                break
            if variadic and not self.at(")"):
                self.fail(self.current, "variadic parameter must be last")
        self.take(")")

    def function_type(self):
        token = self.take("func")
        self.take("(")
        types, names = self.parameter_list()
        result, result_names = self.function_results()
        return FunctionTypeSyntax(token, types, names, result, result_names)

    def function_results(self):
        if not self.accept("("):
            if self.current.kind in ("name", "[", "struct", "*", "interface", "func"):
                return self.datatype(), [None]
            return "void", []
        types, names = self.parameter_list()
        return (Results(tuple(types)) if len(types) > 1 else types[0] if types else "void"), names

    def block(self):
        self.take("{")
        statements = []
        while not self.at("}"):
            if self.at("eof"):
                self.fail(self.current, "unterminated block")
            if self.accept(";"):
                continue
            if self.at("const") or self.at("var"):
                statements.extend(self.declarations())
            else:
                statements.append(self.statement())
            self.end_statement()
        self.take("}")
        return statements

    def declarations(self):
        token = self.current
        self.index += 1
        grouped = bool(self.accept("("))
        declarations, previous = [], None
        while not grouped or not self.accept(")"):
            # An empty declaration group is valid, but empty ConstSpecs are
            # not: separators do not advance iota independently of a spec.
            names = [self.take("name")]
            while self.accept(","):
                names.append(self.take("name"))
            # A VarSpec/ConstSpec uses Type, including its parenthesized form.
            # The declaration group's own parentheses were consumed above.
            dtype = self.datatype() if self.current.kind in ("name", "[", "struct", "*", "interface", "(") else None
            exprs = self.expression_list() if self.accept("=") else []
            if token.kind == "const" and not exprs:
                if not grouped or previous is None or dtype is not None:
                    self.fail(names[0], "const declaration requires an initializer or a preceding ConstSpec")
                # Omission repeats source expressions and their type, not the
                # previous computed values or mutable typed AST. In particular
                # iota and lexical references are evaluated at this new spec.
                dtype, exprs = deepcopy(previous)
            if not exprs and dtype is None:
                self.fail(names[0], "declaration requires a type or initializer")
            if token.kind == "const":
                previous = deepcopy((dtype, exprs))
            declarations.append(Stmt(token.kind, token, names=names, type=dtype,
                                     exprs=exprs, iota=len(declarations) if token.kind == "const" else None))
            if not grouped:
                break
            if not self.accept(";") and not self.at(")"):
                self.fail(self.current, "expected declaration separator")
        # Even an empty group is a declaration statement, not EmptyStmt.
        # Retain it so a preceding return cannot satisfy the final-statement
        # termination rule through an erased const()/var() declaration.
        return declarations or [Stmt(token.kind, token)]

    def statement(self):
        token = self.current
        if self.at("name") and self.tokens[self.index + 1].kind == ":":
            self.index += 2
            if self.at(";") or self.at("}"):
                body = [Stmt("empty", self.current)]
            elif self.at("const") or self.at("var"):
                body = self.declarations()
            else:
                body = [self.statement()]
            return Stmt("label", token, body=body, label=token)
        if self.at("{"):
            return Stmt("block", token, body=self.block())
        if self.accept("return"):
            exprs = [] if self.at(";") or self.at("}") else self.expression_list()
            return Stmt("return", token, exprs=exprs)
        if self.accept("break") or self.accept("continue"):
            return Stmt(token.kind, token, label=self.take("name") if self.at("name") else None)
        if self.accept("goto"):
            return Stmt("goto", token, label=self.take("name"))
        if self.accept("fallthrough"):
            return Stmt("fallthrough", token)
        if self.accept("switch"):
            previous_literals, self.named_literals = self.named_literals, False
            init, selector = None, None
            if not self.at("{"):
                first = None if self.at(";") else self.simple()
                if self.accept(";"):
                    init = first
                    selector = None if self.at("{") else self.expression()
                elif first is not None and first.kind == "expression":
                    selector = first.exprs[0]
                else:
                    self.fail(token, "switch requires a single expression or initialization clause")
            self.named_literals = previous_literals
            self.take("{")
            cases = []
            while not self.accept("}"):
                label = self.current
                if self.accept("case"):
                    expressions = self.expression_list()
                elif self.accept("default"):
                    expressions = []
                else:
                    self.fail(label, "expected case or default in expression switch")
                self.take(":")
                body = []
                while not self.at("case") and not self.at("default") and not self.at("}"):
                    if self.accept(";"):
                        continue
                    if self.at("const") or self.at("var"):
                        body.extend(self.declarations())
                    else:
                        body.append(self.statement())
                    self.end_statement()
                cases.append(Stmt(label.kind, label, exprs=expressions, body=body))
            return Stmt("switch", token, exprs=[] if selector is None else [selector], init=init, cases=cases)
        if self.accept("if"):
            previous_literals, self.named_literals = self.named_literals, False
            # The optional SimpleStmt before ';' may itself be empty.
            # Keep an absent initializer out of semantic/evaluation handling.
            first = None if self.at(";") else self.simple()
            init = None
            if self.accept(";"):
                init, condition = first, self.expression()
            elif first is not None and first.kind == "expression" and len(first.exprs) == 1:
                condition = first.exprs[0]
            else:
                self.fail(token, "if requires a boolean condition")
            self.named_literals = previous_literals
            body, otherwise = self.block(), None
            if self.accept("else"):
                otherwise = [self.statement()] if self.at("if") else self.block()
            return Stmt("if", token, exprs=[condition], body=body, otherwise=otherwise, init=init)
        if self.accept("for"):
            previous_literals, self.named_literals = self.named_literals, False
            init, condition, post = None, None, None
            if self.accept("range"):
                ranged = Stmt("range", token, exprs=[self.expression()])
            else:
                ranged = None
            if ranged is None and not self.at("{"):
                first = None if self.at(";") else self.simple(allow_range=True)
                if first is not None and first.kind == "range":
                    ranged = first
                elif self.accept(";"):
                    init = first
                    condition = None if self.at(";") else self.expression()
                    self.take(";")
                    post = None if self.at("{") else self.simple()
                    if post is not None and post.kind == ":=":
                        self.fail(post.token, "for post statement cannot be a short variable declaration")
                elif first is not None and first.kind == "expression":
                    condition = first.exprs[0]
                else:
                    self.fail(token, "unsupported for clause")
            self.named_literals = previous_literals
            if ranged is not None:
                ranged.body = self.block()
                return ranged
            return Stmt("for", token, exprs=[] if condition is None else [condition],
                        body=self.block(), init=init, post=post)
        if token.kind == "type":
            self.unsupported(token, 'go.unsupported.local-type-declarations', "function-local type declarations are not implemented")
        if token.kind in KEYWORDS and token.kind != "func":
            # `func` starts an expression statement (a called function literal).
            self.fail(token, f"unsupported statement {token.text!r}")
        return self.simple()

    def simple(self, *, allow_range=False):
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
            if allow_range and token.kind in ("=", ":=") and self.accept("range"):
                return Stmt("range", token, exprs=[self.expression()], names=[expr.token for expr in left],
                            targets=left, range_declaration=token.kind == ":=")
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

    def composite_literal(self, token, dtype):
        self.take("{")
        previous, self.named_literals = self.named_literals, True
        args, keys = [], []
        while not self.at("}"):
            item = self.literal_element()
            key = item if self.accept(":") else None
            args.append(self.literal_element() if key is not None else item)
            keys.append(key)
            if not self.accept(","):
                break
        self.take("}")
        self.named_literals = previous
        return Expr("array" if isinstance(dtype, ArraySyntax) else "composite", token, dtype, args, keys=keys)

    def literal_element(self):
        # Go spec, Composite literals: an element or key that is itself a
        # composite literal may elide its type ({...}); the checker supplies
        # it from the enclosing array, slice or map type, or rejects it.
        if self.at("{"):
            return self.composite_literal(self.current, None)
        return self.expression()

    def expression(self, minimum=1):
        token = self.current
        if token.kind in ("+", "-", "!", "^", "&", "*"):
            self.index += 1
            left = Expr("unary", token, token.kind, [self.expression(6)])
        elif self.accept("("):
            previous, self.named_literals = self.named_literals, True
            left = self.expression()
            self.take(")")
            self.named_literals = previous
            left.parenthesized = True
        elif self.at("func"):
            # A function literal is a signature followed by a body; without
            # the body the same spelling is a function type operand.
            signature = self.function_type()
            if self.at("{"):
                previous, self.named_literals = self.named_literals, True
                body = self.block()
                self.named_literals = previous
                left = Expr("func_literal", token, FunctionLiteral(signature, body))
            else:
                left = Expr("type", token, signature)
        elif self.at("[") or self.at("struct") or self.at("interface"):
            dtype = self.datatype(literal=True)
            left = self.composite_literal(token, dtype) if self.at("{") else Expr("type", token, dtype)
        elif self.accept("integer"):
            left = Expr("integer", token, token.text)
        elif self.accept("rune"):
            left = Expr("rune", token, token.text)
        elif self.accept("string"):
            left = Expr("string", token, token.text)
        elif self.accept("name"):
            left = Expr("name", token, token.text)
        else:
            self.fail(token, f"unsupported expression token {token.text!r}")
        while True:
            if self.accept("."):
                member = self.take("name")
                left = Expr("selector", member, member.text, [left])
            elif self.named_literals and self.at("{") and left.kind in ("name", "selector") and not left.parenthesized:
                if left.kind == "selector":
                    if left.args[0].kind != "name" or left.args[0].parenthesized:
                        self.fail(left.token, "composite literal requires an unparenthesized type name")
                    dtype = TypeName(left.token, left.args[0].value)
                else:
                    dtype = TypeName(left.token)
                left = self.composite_literal(left.token, dtype)
            elif self.accept("("):
                if left.kind not in ("name", "selector", "type", "index", "type_apply", "func_literal") and not (left.kind == "unary" and left.value == "*"):
                    self.fail(token, "only named function calls/conversions are supported")
                previous, self.named_literals = self.named_literals, True
                args = [] if self.at(")") else self.expression_list(terminator=")")
                self.take(")")
                self.named_literals = previous
                qualifier = left.args[0].value if left.kind == "selector" and left.args[0].kind == "name" else None
                left = Expr("call", left.token, left.value, args, qualifier=qualifier, callee=left)
            elif self.accept("["):
                arguments = self.expression_list(terminator="]")
                self.take("]")
                # A final comma does not add another argument. Keep the
                # single-argument form ambiguous until Sema resolves its base
                # as an indexable value or a directly instantiated function.
                left = (Expr("index", arguments[0].token, args=[left, arguments[0]])
                        if len(arguments) == 1 else
                        Expr("type_apply", left.token, args=arguments, callee=left))
            else:
                break
        while BINARY_PRECEDENCE.get(self.current.kind, 0) >= minimum:
            operator = self.current
            self.index += 1
            right = self.expression(BINARY_PRECEDENCE[operator.kind] + 1)
            left = Expr("binary", operator, operator.kind, [left, right])
        return left
