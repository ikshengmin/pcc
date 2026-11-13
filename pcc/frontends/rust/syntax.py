"""A Rust token stream and recursive-descent/Pratt parser, not a translator."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .crate_policy import CratePolicy
from .visibility import parse_visibility, restriction_components
from .types import ArrayLength, FixedArrayType, SharedReference, SliceType, TupleType, TypeApplication


class RustError(ValueError):
    """A located syntax, type, or unsupported-capability diagnostic."""


@dataclass(frozen=True)
class Token:
    text: str
    kind: str
    line: int
    column: int
    source_start: int | None = None
    source_end: int | None = None
    literal_value: str | None = None
    source_path: str | None = None

    def error(self, message: str) -> RustError:
        prefix = self.source_path + ": " if self.source_path is not None else ""
        return RustError(f"{prefix}{self.line}:{self.column}: {message}")


# Rust uses Unicode Pattern_White_Space, not Python str.isspace().
_WHITESPACE = "\t\n\v\f\r \u0085\u200e\u200f\u2028\u2029"
_KEYWORDS = set("as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while yield abstract become box do final macro override priv typeof unsized virtual try union".split())
_OPERATORS = ("<<=", ">>=", "->", "=>", "::", "==", "!=", "<=", ">=", "&&", "||", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<", ">>", "..=", "..")


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
        # Retain documentation as tokens, never ordinary comment whitespace.
        # Payload normalization follows Rust CRLF processing; spans refer to
        # codepoint offsets in the untouched original source.
        line_doc = source.startswith("//!", i) or (source.startswith("///", i) and not source.startswith("////", i))
        block_doc = source.startswith("/*!", i) or (source.startswith("/**", i) and i + 3 < len(source) and source[i + 3] not in "*/")
        if line_doc:
            end = source.find("\n", i)
            if end < 0:
                end = len(source)
            value = source[i + 3:end]
            if end < len(source) and value.endswith("\r"):
                value = value[:-1]
            if "\r" in value:
                raise Token(source[i:i + 3], "", ln, column).error("bare carriage return in documentation comment")
            kind = "doc_inner" if source[i + 2] == "!" else "doc_outer"
            tokens.append(Token(source[start:end], kind, ln, column, start, end, value))
            advance(end)
            continue
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
            if block_doc:
                value = source[i + 3:end - 2].replace("\r\n", "\n")
                if "\r" in value:
                    raise Token(source[i:i + 3], "", ln, column).error("bare carriage return in documentation comment")
                kind = "doc_inner" if source[i + 2] == "!" else "doc_outer"
                tokens.append(Token(source[start:end], kind, ln, column, start, end, value))
            advance(end)
            continue
        if char == "'":
            end = i + 1
            while end < len(source) and source[end].isascii() and (source[end].isalnum() or source[end] == "_"):
                end += 1
            if (end == i + 1 or source[i + 1].isdigit()
                    or end < len(source) and source[end] == "'"):
                raise Token(char, "", ln, column).error("character literals are not implemented")
            tokens.append(Token(source[i:end], "lifetime", ln, column))
            advance(end)
            continue
        if char == '"' or char == 'r' and i + 1 < len(source) and source[i + 1] in '#"':
            from .attribute_strings import scan_string
            scanned = scan_string(source, i, Token(char, "", ln, column))
            if scanned is not None:
                value, end = scanned
                tokens.append(Token(source[start:end], "string", ln, column, start, end, value))
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
        op = next((op for op in _OPERATORS if source.startswith(op, i)), None)
        # `$` is a Rust token (reference/tokens.html#punctuation) used only by
        # macros by example; the parser reports macro syntax as a gap. `@`
        # separates an identifier pattern from its subpattern.
        if op is None and char in "{}()[];,:.+-*/%!=<>&|^#?$@":
            op = char
        if op is None:
            raise Token(char, "", ln, column).error(f"unsupported Rust token {char!r}")
        tokens.append(Token(op, "punct", ln, column))
        advance(start + len(op))
    tokens.append(Token("<eof>", "eof", line, col))
    return tokens


@dataclass(frozen=True)
class Documentation:
    token: Token
    text: str | None
    inner: bool
    # Half-open Unicode-codepoint offsets in the original source, not bytes.
    source_span: tuple[int, int]
    syntax: str
    options: tuple = ()


@dataclass
class Expr:
    kind: str
    token: Token
    value: object = None
    children: list[Expr] = field(default_factory=list)
    ty: object = None
    binding: object = None
    # Generic arguments on an associated/method path segment, distinct from
    # the owner's type arguments and from a value field's spelling.
    type_arguments: tuple[object, ...] | None = None
    # Parser identity survives raw-template deepcopy and semantic rewriting.
    source_call_id: int | None = None


@dataclass(frozen=True)
class Implementation:
    token: Token
    owner: object
    type_parameters: tuple[Token, ...] = ()
    trait: str | None = None
    documentation: tuple[Documentation, ...] = ()
    parameter_bounds: tuple[tuple[Token, tuple[TypeBound, ...]], ...] = ()
    where_predicates: tuple[WherePredicate, ...] = ()
    associated_types: tuple[AssociatedType, ...] = ()
    # Erased after the matching actual rustc check (items/implementations.html#generic-implementations).
    lifetime_parameters: tuple[Token, ...] = ()

    @property
    def effective_parameter_bounds(self):
        return merge_parameter_bounds(self.parameter_bounds, self.where_predicates)


@dataclass
class Trait:
    token: Token
    methods: list[Function] = field(default_factory=list)
    documentation: tuple[Documentation, ...] = ()
    public: bool = False
    language_item: object = None
    type_parameters: tuple[Token, ...] = ()
    supertraits: tuple = ()
    associated_types: list[AssociatedType] = field(default_factory=list)


@dataclass(frozen=True)
class AssociatedType:
    token: Token
    bounds: tuple[TypeBound, ...] = ()
    value: object = None
    documentation: tuple[Documentation, ...] = ()

    @property
    def effective_parameter_bounds(self):
        return ((self.token, self.bounds),)


@dataclass(frozen=True)
class ClosureSignature:
    moves: bool
    parameters: tuple[tuple[object, object], ...]
    result: object = None


# Standard closure traits accepting parenthesized argument sugar.
CALLABLE_TRAITS = frozenset({'Fn', 'FnMut', 'FnOnce'})


@dataclass(frozen=True)
class CallableSignature:
    """Parenthesized Fn-family sugar: `Fn(A, B) -> R` (reference/trait-bound.html).

    Inputs and the output keep their unnormalized source types; an omitted
    output is the unit type.
    """
    inputs: tuple[object, ...]
    output: object = "()"


@dataclass(frozen=True)
class TypeBound:
    token: Token
    path: str
    relaxed: bool = False
    callable: CallableSignature | None = None


@dataclass(frozen=True)
class WherePredicate:
    parameter: Token
    bounds: tuple[TypeBound, ...]


def merge_parameter_bounds(parameter_bounds, where_predicates):
    # Keep source ASTs intact; repeated predicates accumulate obligations.
    merged = {}
    groups = list(parameter_bounds)
    groups.extend((predicate.parameter, predicate.bounds) for predicate in where_predicates)
    for parameter, bounds in groups:
        token, combined = merged.setdefault(parameter.text, (parameter, []))
        combined.extend(bounds)
        if len(combined) > 16:
            raise parameter.error('combined type parameter bounds exceed implementation limit (16)')
    return tuple((token, tuple(bounds)) for token, bounds in merged.values())


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
    type_parameters: tuple[Token, ...] = ()
    instance_arguments: tuple[object, ...] | None = None
    instance_name: str | None = None
    implementation: Implementation | None = None
    receiver: bool = False
    lifetime_parameters: tuple[Token, ...] = ()
    explicit_receiver: bool = False
    documentation: tuple[Documentation, ...] = ()
    item_identity: object = None
    parameter_bounds: tuple[tuple[Token, tuple[TypeBound, ...]], ...] = ()
    # Abstract trait signature only: never an emitted body or separate instance.
    bound_trait: tuple[str, object] | None = None
    where_token: Token | None = None
    where_predicates: tuple[WherePredicate, ...] = ()
    # Trait method `where Self: ...` bounds; Self is not a method parameter.
    self_bounds: tuple[TypeBound, ...] = ()
    inline_attributes: tuple[object, ...] = ()
    # `const fn`: callable at run time like any function. Its const-context
    # body rules are rustc's to check (compilation already requires that
    # check or its attestation); PCC never evaluates it at compile time.
    const_fn: bool = False
    # Synthesized concrete body inherited from this declaration-owned default.
    default_source: tuple[str, str] | None = None

    @property
    def effective_parameter_bounds(self):
        # Preserve both source forms on the AST. Their obligations accumulate
        # by declared parameter; a later predicate must never reset an earlier
        # inline/where constraint. Keep duplicates for relaxed-bound validation.
        return merge_parameter_bounds(self.parameter_bounds, self.where_predicates)

    @property
    def owner_parameter_count(self):
        return len(self.implementation.type_parameters) if self.implementation else 0

    @property
    def declaration_name(self):
        if self.implementation is None:
            return self.item_identity.binding_key if self.item_identity is not None else self.token.text
        owner = self.implementation.owner
        if self.implementation.trait is not None:
            return "<" + str(owner) + " as " + self.implementation.trait + ">::" + self.token.text
        return (owner.name if isinstance(owner, TypeApplication) else str(owner)) + "::" + self.token.text

    @property
    def identity(self):
        if self.instance_name:
            return self.instance_name
        if self.implementation is not None:
            return "__pcc_rust_method_" + self.declaration_name.encode('utf-8').hex()
        if self.item_identity is not None:
            return "__pcc_rust_item_" + self.declaration_name.encode('utf-8').hex()
        return self.declaration_name

    @property
    def export_name(self):
        if self.item_identity is not None:
            name = '::'.join((*self.item_identity.module.components, self.token.text))
            if self.instance_arguments is not None:
                name += "::<" + ", ".join(map(str, self.instance_arguments)) + ">"
            return name
        if self.instance_arguments is None:
            return self.declaration_name
        return self.declaration_name + "::<" + ", ".join(map(str, self.instance_arguments)) + ">"


@dataclass
class Record:
    token: Token
    fields: list[tuple[Token, object]]
    repr_c: bool = False
    type_parameters: tuple[Token, ...] = ()
    documentation: tuple[Documentation, ...] = ()
    field_documentation: tuple[tuple[Documentation, ...], ...] = ()
    public: object = False  # False, True or a VisibilityRestriction
    field_public: tuple[object, ...] = ()
    item_identity: object = None
    lifetime_parameters: tuple[Token, ...] = ()
    derives: tuple = ()
    helper_attributes: tuple = ()

    @property
    def declaration_name(self):
        return self.item_identity.binding_key if self.item_identity is not None else self.token.text


@dataclass
class Enumeration:
    token: Token
    variants: list[tuple[Token, Expr | None]]
    representation: str | None = None
    discriminants: dict[str, int] = field(default_factory=dict)
    payloads: dict[str, tuple[object, ...]] = field(default_factory=dict)
    type_parameters: tuple[Token, ...] = ()
    documentation: tuple[Documentation, ...] = ()
    variant_documentation: tuple[tuple[Documentation, ...], ...] = ()
    payload_documentation: dict[str, tuple[tuple[Documentation, ...], ...]] = field(default_factory=dict)
    # Struct-like variants: field name tokens in declaration (payload) order.
    named_payloads: dict[str, tuple[Token, ...]] = field(default_factory=dict)
    public: bool = False
    item_identity: object = None
    lifetime_parameters: tuple[Token, ...] = ()
    derives: tuple = ()
    helper_attributes: tuple = ()

    @property
    def declaration_name(self):
        return self.item_identity.binding_key if self.item_identity is not None else self.token.text


@dataclass
class Module:
    """A lexical module boundary, never a flattened translation unit."""
    token: Token
    public: bool
    documentation: tuple[Documentation, ...]
    path: str | None
    body: object = None  # ModuleBody for inline modules; None for `mod name;`.


@dataclass
class CrateSelfAlias:
    token: Token
    public: bool = False
    documentation: tuple[Documentation, ...] = ()


@dataclass(frozen=True)
class ExternCrateItem:
    token: Token
    crate: Token
    alias: Token | None
    public: bool
    documentation: tuple[Documentation, ...]
    source_span: tuple[int, int]


@dataclass(frozen=True)
class UseDeclaration:
    """One module-scope use path; never a copied target declaration.

    `public` is a `pub use` re-export: the binding is visible wherever a
    `pub` item of its module would be (Rust Reference items/use-declarations,
    "use Visibility").
    """
    token: Token
    path: tuple[Token, ...]
    alias: Token | None
    documentation: tuple[Documentation, ...]
    source_span: tuple[int, int]
    public: bool = False

    @property
    def binding(self):
        return self.alias if self.alias is not None else self.path[-1]


@dataclass
class ConstItem:
    """A module-scope `const NAME: Type = expr;` item (Rust Reference
    items/constant-items). Each use denotes the evaluated value itself; the
    item has no address or storage."""
    token: Token
    ty: object
    expression: Expr
    public: object = False
    documentation: tuple[Documentation, ...] = ()
    item_identity: object = None
    value: object = None
    evaluated: bool = False

    @property
    def declaration_name(self):
        return self.item_identity.binding_key if self.item_identity is not None else self.token.text


@dataclass
class StaticItem:
    """A module-scope `static [mut] NAME: Type = expr;` (Rust Reference
    items/static-items): one place in memory, unlike a const."""
    token: Token
    ty: object
    expression: Expr
    mutable: bool = False
    public: object = False
    documentation: tuple[Documentation, ...] = ()
    item_identity: object = None
    value: object = None
    evaluated: bool = False

    @property
    def declaration_name(self):
        return self.item_identity.binding_key if self.item_identity is not None else self.token.text


@dataclass
class ModuleBody:
    functions: list[Function]
    records: list[Record]
    enums: list[Enumeration]
    implementations: list[Implementation]
    traits: list[Trait]
    modules: list[Module]
    documentation: tuple[Documentation, ...]
    crate_aliases: list[CrateSelfAlias] = field(default_factory=list)
    crate_policy: CratePolicy = field(default_factory=CratePolicy)
    conditional_items: tuple = ()
    uses: list[UseDeclaration] = field(default_factory=list)
    consts: list[ConstItem] = field(default_factory=list)
    statics: list[StaticItem] = field(default_factory=list)


@dataclass
class Pattern:
    kind: str
    token: Token
    value: object = None
    children: list[Pattern] = field(default_factory=list)
    ty: object = None
    binding: object = None
    variant: str | None = None
    # A struct-like variant pattern `V { a, b: p, .. }`: written field tokens
    # and the rest flag, until checking converts it to positional children.
    named_fields: object = None
    named: bool = False


_TYPES = {"i32", "i64", "u32", "u64", "isize", "usize", "bool", "()"}
_PRECEDENCE = {"=": 1, "+=": 1, "-=": 1, "*=": 1, "||": 2, "&&": 3, "|": 4, "^": 5, "&": 6, "==": 7, "!=": 7, "<": 7, "<=": 7, ">": 7, ">=": 7, "+": 9, "-": 9, "*": 10, "/": 10, "%": 10, "as": 11}
_COMPARISONS = {"==", "!=", "<", "<=", ">", ">="}


class Parser:
    # Built-in derives and core's macros (env!, option_env!, matches!) come
    # from core; lang-item context inspection (a crate defining core items
    # itself) turns them off.
    core_prelude = True

    def __init__(self, source: str, *, source_path=None, edition=2021, rustc_check=None,
                 configuration=None, environment=None):
        if type(edition) is not int or edition not in {2015, 2018, 2021, 2024}:
            raise RustError('Rust parser crate policy requires an explicit recognized edition')
        if configuration is not None:
            from .configuration import OwnedRustConfiguration
            if type(configuration) is not OwnedRustConfiguration or configuration.edition != edition:
                raise RustError('Rust parser configuration must be an OwnedRustConfiguration for this edition')
            if rustc_check is not None:
                raise RustError('an owned cfg configuration and a CheckedRustSource cannot both '
                                'select cfg; the checked command fixes its own configuration')
        self._configuration = configuration
        if environment is not None:
            from .builtin_macros import CompileEnvironment
            if type(environment) is not CompileEnvironment:
                raise RustError('Rust parser environment must be a CompileEnvironment')
        # Explicit env!/option_env! input, and every built-in expansion made.
        self.environment = environment
        self.macro_expansions = []
        self.source = source
        self.crate_policy = CratePolicy(edition)
        self.crate_aliases = []
        self.uses = []
        self.consts = []
        self.statics = []
        # Every parsed `pub(...)`, shared with inline child-module parsers.
        self.visibility_restrictions = []
        self.line_offsets = [0]
        for index, char in enumerate(source):
            if char == "\n":
                self.line_offsets.append(index + 1)
        self.tokens = lex(source)
        if source_path is not None:
            self.tokens = [replace(token, source_path=source_path) for token in self.tokens]
        self.crate_documentation: tuple[Documentation, ...] = ()
        self.crate_lint_attributes: tuple = ()
        self.crate_cfg_attributes: tuple = ()
        self.crate_cfg_syntax: tuple = ()
        self.item_cfg_syntax: tuple = ()
        self.conditional_items: tuple = ()
        self._rustc_check = rustc_check
        self.crate_stability = None
        self.attribute_requirements = []
        self.pos = 0
        self.call_serial = 0
        self.records: list[Record] = []
        self.enums: list[Enumeration] = []
        self.implementations: list[Implementation] = []
        self.traits: list[Trait] = []
        self.type_argument_depth = 0
        self.modules: list[Module] = []
        self.module_depth = 0

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
        if text == "_":
            # Preserve the source placeholder, not a shared inference variable.
            # Only call-site/local normalization may mint a fresh variable for it.
            return text
        if text == "[":
            element = self.type()
            if self.accept("]"):
                return SliceType(element, token)
            self.expect(";")
            length = self.expression()
            self.expect("]")
            return FixedArrayType(element, ArrayLength(length))
        if text == "&&":
            raise token.error("unit and nested references require additional reference layout/lifetime support")
        if text == "&":
            lifetime = self.take().text if self.token.kind == "lifetime" else None
            mutable = self.accept("mut")
            return SharedReference(self.type(), mutable, lifetime)
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
        if text in _TYPES or text in {"Self", "self", "super", "crate"} or (token.kind == "name" and text != "_"):
            while self.accept("::"):
                segment = self.take()
                if segment.kind != "name" and segment.text != "super":
                    raise segment.error("expected a type path segment")
                text += "::" + segment.text
            if self.token.text == "<":
                lifetimes = []
                arguments = self.type_arguments(lifetimes=lifetimes)
                return TypeApplication(text, arguments, tuple(lifetimes))
            return text
        raise token.error(f"unsupported Rust type {text!r}; references and traits are not implemented")

    def generic_close(self) -> bool:
        # Rust punctuation is split only in a type-argument context. Keep the
        # remainder (including '=') with its own original source coordinate.
        token = self.token
        if token.text == ">":
            self.take()
            return True
        if token.text in {">>", ">=", ">>="}:
            self.tokens[self.pos] = replace(token, text=token.text[1:], column=token.column + 1,
                                            source_start=None if token.source_start is None else token.source_start + 1)
            return True
        return False

    def type_arguments(self, *, allow_empty=False, lifetimes=None) -> tuple[object, ...]:
        """Generic type arguments. With a `lifetimes` list, leading lifetime
        arguments (`'a`, `'_`, `'static`) are collected into it (Rust
        Reference paths.html#paths-in-expressions, GenericArgs)."""
        start = self.expect("<")
        self.type_argument_depth += 1
        if self.type_argument_depth > 16:
            raise start.error("generic argument nesting exceeds implementation limit (16)")
        arguments = []
        try:
            if self.generic_close():
                if allow_empty:
                    return ()
                raise start.error("empty/inferred generic arguments are not implemented; supply explicit types")
            while True:
                if self.token.kind == "lifetime":
                    lifetime = self.take()
                    if lifetimes is None:
                        raise lifetime.error("lifetime arguments in this generic argument list are not implemented")
                    if arguments:
                        raise lifetime.error("lifetime arguments must be provided before type arguments (rustc E0747)")
                    lifetimes.append(lifetime.text)
                else:
                    arguments.append(self.type())
                if len(arguments) + (len(lifetimes) if lifetimes is not None else 0) > 16:
                    raise start.error("generic argument count exceeds implementation limit (16)")
                if self.generic_close():
                    return tuple(arguments)
                self.expect(",")
                if self.generic_close():
                    return tuple(arguments)
        finally:
            self.type_argument_depth -= 1

    def generic_parameters(self, *, allow_lifetimes=False, allow_bounds=False) -> tuple[Token, ...]:
        self.last_lifetime_parameters = []
        self.last_parameter_bounds = []
        if not self.accept("<"):
            return ()
        if self.token.text == ">":
            raise self.token.error("empty generic parameter lists are not implemented")
        parameters = []
        while True:
            if self.token.text == "const":
                raise self.token.error("const generic parameters are not implemented")
            lifetime_parameter = self.token.kind == "lifetime"
            if lifetime_parameter:
                if not allow_lifetimes:
                    raise self.token.error("lifetime parameters on data declarations are not implemented")
                self.last_lifetime_parameters.append(self.take())
            else:
                parameters.append(self.name())
            if len(parameters) + len(self.last_lifetime_parameters) > 16:
                raise self.token.error("generic parameter count exceeds implementation limit (16)")
            if self.token.text == ":" and allow_bounds and not lifetime_parameter:
                self.take()
                self.last_parameter_bounds.append((parameters[-1], self.type_parameter_bounds({',', '>'})))
            if self.token.text in {":", "="}:
                raise self.token.error("generic bounds and default arguments require trait/constraint checking, which is not implemented")
            if self.accept(">"):
                break
            self.expect(",")
            if self.accept(">"):
                break
        return tuple(parameters)

    def type_parameter_bounds(self, terminators):
        bounds = []
        while self.token.text not in terminators:
            bound_token = self.token
            if self.token.kind == 'lifetime':
                raise self.token.error('lifetime bounds require lifetime solving, which is not implemented')
            if self.token.text == 'for':
                raise self.token.error('higher-ranked trait bounds are not implemented')
            if self.token.text == '(':
                raise self.token.error('parenthesized trait bounds are not implemented')
            relaxed = self.accept('?')
            path = self.name().text
            while self.accept('::'):
                path += '::' + self.name().text
            callable = None
            if self.token.text == '(':
                if path.rsplit('::', 1)[-1] not in CALLABLE_TRAITS:
                    raise self.token.error('parenthesized arguments are only Fn, FnMut and FnOnce trait sugar')
                if relaxed:
                    raise bound_token.error('only Sized bounds can be relaxed')
                callable = self.callable_signature()
            if self.token.text == '<':
                raise self.token.error('generic trait bounds and associated projections are not implemented')
            bounds.append(TypeBound(bound_token, path, relaxed, callable))
            if len(bounds) > 16:
                raise bound_token.error('type parameter bounds exceed implementation limit (16)')
            if not self.accept('+'):
                break
        return tuple(bounds)

    def closure_expression(self, token):
        """`move`? `|params|` (`-> T` block | expression), reference/expressions/closure-expr.html.

        The body extends as far as an ordinary expression. Parameter patterns
        are single (unparenthesized `|` cannot start an or-pattern here).
        """
        moves = token.text == "move"
        opener = self.take() if moves else token
        parameters = []
        if opener.text == "|":
            while not self.accept("|"):
                pattern = self.pattern(alternatives=False)
                annotation = self.type() if self.accept(":") else None
                parameters.append((pattern, annotation))
                if len(parameters) > 16:
                    raise pattern.token.error("closure parameter count exceeds implementation limit (16)")
                if self.token.text != "|":
                    self.expect(",")
        result = None
        if self.accept("->"):
            result = self.type()
            if self.token.text != "{":
                raise self.token.error("a closure with an explicit return type requires a block body")
            body = self.block()
        else:
            body = self.expression()
        return Expr("closure", opener, ClosureSignature(moves, tuple(parameters), result), [body])

    def callable_signature(self):
        self.expect('(')
        inputs = []
        while not self.accept(')'):
            inputs.append(self.type())
            if len(inputs) > 16:
                raise self.token.error('Fn-family parameter count exceeds implementation limit (16)')
            if self.token.text != ')':
                self.expect(',')
        return CallableSignature(tuple(inputs), self.type() if self.accept('->') else '()')

    def associated_type(self, *, declaration, documentation=()):
        self.expect('type')
        name = self.name()
        if self.token.text == '<':
            raise self.token.error('generic associated types are not implemented')
        bounds = ()
        if self.accept(':'):
            if not declaration:
                raise name.error('bounds on a trait implementation associated type are not allowed')
            bounds = self.type_parameter_bounds({';', '=', 'where'})
        if self.token.text == 'where':
            raise self.token.error('associated type where constraints are not implemented')
        value = None
        if declaration:
            if self.token.text == '=':
                raise self.token.error('associated type defaults are not implemented')
        else:
            self.expect('=')
            value = self.type()
            if self.token.text == 'where':
                raise self.token.error('associated type where constraints are not implemented')
        self.expect(';')
        return AssociatedType(name, bounds, value, tuple(documentation))

    def function_where_clause(self, parameters, *, owner_parameters=None, declaration=False):
        declared = {parameter.text for parameter in parameters}
        owners = {parameter.text for parameter in owner_parameters or ()}
        # Required associated functions end in a semicolon; provided methods
        # continue into a body after the same parameter-only where clause.
        terminators = {'{', ';'} if declaration else {'{'}
        predicates = []
        self.last_self_bounds = []
        while self.token.text not in terminators:
            if self.token.kind == 'lifetime' or self.token.text == 'for':
                raise self.token.error('lifetime and higher-ranked where predicates are not implemented')
            if owner_parameters is not None and self.token.text == 'Self':
                if not declaration:
                    raise self.token.error('where Self predicates on implementation methods require concrete-owner obligations, which are not implemented')
                # A trait method's Self predicate; the trait owner checks it.
                subject = self.take()
                if self.token.text != ':':
                    raise self.token.error('where projections/equality predicates are not implemented; expected a parameter colon')
                self.take()
                self.last_self_bounds.extend(self.type_parameter_bounds({','} | terminators))
                if len(self.last_self_bounds) > 16:
                    raise subject.error('combined Self bounds exceed implementation limit (16)')
                if not self.accept(','):
                    break
                continue
            if self.token.kind != 'name' or self.token.text not in declared:
                raise self.token.error('where predicate subjects must be declared type parameters; concrete/compound predicates are not implemented')
            parameter = self.take()
            if self.token.text != ':':
                raise self.token.error('where projections/equality predicates are not implemented; expected a parameter colon')
            self.take()
            bounds = self.type_parameter_bounds({','} | terminators)
            if parameter.text in owners and any(bound.relaxed for bound in bounds):
                # The enclosing impl/data parameter already has its Sized
                # declaration. A method cannot retroactively relax it.
                raise parameter.error('relaxed bounds on enclosing impl parameters are not supported; declare relaxation at the parameter definition')
            predicates.append(WherePredicate(parameter, bounds))
            if len(predicates) > 64:
                raise parameter.error('where predicates exceed implementation limit (64)')
            if not self.accept(','):
                break
        return tuple(predicates)

    def expression_path(self, token: Token) -> tuple[object, str | None]:
        parts = [token.text]
        self.last_path_type_arguments = None
        while self.accept("::"):
            if self.token.text == "<":
                arguments = self.type_arguments(allow_empty=True)
                if not arguments and self.token.text != "(":
                    raise token.error("empty/inferred generic owner arguments are not implemented; use a direct function call or explicit owner types")
                owner = TypeApplication("::".join(parts), arguments)
                if not self.accept("::"):
                    if len(parts) == 1 or self.token.text == "{":
                        return owner, None
                    self.last_path_type_arguments = arguments
                    return "::".join(parts[:-1]), parts[-1]
                return owner, self.name().text
            segment = self.take()
            if segment.kind != "name" and segment.text != "super":
                raise segment.error("expected an expression path segment")
            parts.append(segment.text)
        if len(parts) == 1:
            return parts[0], None
        return "::".join(parts[:-1]), parts[-1]

    def documentation(self, *, inner=False, crate=False):
        result = []
        while True:
            token = self.token
            if token.kind in {"doc_inner", "doc_outer"}:
                actual_inner = token.kind == "doc_inner"
                if actual_inner != inner:
                    if inner:
                        break
                    raise token.error("inner documentation attribute is not allowed at this item position")
                self.take()
                syntax = "block" if self.source[token.source_start:token.source_start + 2] == "/*" else "line"
                result.append(Documentation(token, token.literal_value, inner, (token.source_start, token.source_end), syntax))
                continue
            if token.text != "#":
                break
            actual_inner = self.tokens[self.pos + 1].text == "!"
            if actual_inner != inner:
                if inner:
                    break
                raise token.error("inner attribute is not allowed at this item position")
            name_index = self.pos + (3 if inner else 2)
            if name_index >= len(self.tokens) or self.tokens[name_index - 1].text != "[" or self.tokens[name_index].text != "doc":
                break
            self.take()
            if inner:
                self.expect("!")
            self.expect("[")
            self.expect("doc")
            options, value = (), None
            if self.token.text == "(":
                from .attribute_metadata import meta_list, validate_doc_options
                options = meta_list(self)
                self.attribute_requirements.extend(validate_doc_options(options, crate=crate))
            elif self.accept("=") and self.token.kind == "string":
                value = self.take().literal_value
            else:
                raise token.error("documentation attributes require literal string payloads or structured doc options; macro expansion is not implemented")
            end = self.expect("]")
            start_offset = self.line_offsets[token.line - 1] + token.column - 1
            end_offset = self.line_offsets[end.line - 1] + end.column
            result.append(Documentation(token, value, inner, (start_offset, end_offset), "attribute", options))
        return tuple(result)

    def record(self, repr_c: bool, documentation=(), public=False, helpers=False) -> None:
        name = self.name()
        parameters = self.generic_parameters(allow_lifetimes=True)
        lifetime_parameters = tuple(self.last_lifetime_parameters)
        if self.token.text == "where":
            raise self.token.error("generic where constraints and traits are not implemented")
        self.expect("{")
        from .derives import helper_attributes
        fields, field_docs, field_public, retained = [], [], [], []
        while not self.accept("}"):
            field_docs.append(self.documentation())
            retained.extend(helper_attributes(self, helpers))
            field_public.append(parse_visibility(self))
            field = self.name()
            self.expect(":")
            fields.append((field, self.type()))
            if self.accept("}"):
                break
            self.expect(",")
        self.records.append(Record(name, fields, repr_c, parameters, documentation, tuple(field_docs), public,
                                   tuple(field_public), lifetime_parameters=lifetime_parameters,
                                   helper_attributes=tuple(retained)))

    def module_body(self):
        # Reuse the exact token/source coordinate space. No slicing, rewriting,
        # or re-lexing an inline module; declaration collections remain separate.
        if self.module_depth >= 32:
            raise self.token.error("inline module nesting exceeds implementation limit (32)")
        child = object.__new__(type(self))
        child.__dict__ = self.__dict__.copy()
        child.module_depth += 1
        child.records, child.enums, child.implementations, child.traits = [], [], [], []
        child.modules, child.attribute_requirements, child.crate_aliases = [], [], []
        child.uses = []
        child.consts = []
        child.statics = []
        child.crate_stability = None
        child.crate_documentation = ()
        child.crate_lint_attributes = ()
        child.crate_cfg_attributes = ()
        child.crate_cfg_syntax = ()
        child.item_cfg_syntax = ()
        child.conditional_items = ()
        functions = child.parse(_module=True, _inline=True)
        self.pos = child.pos
        return ModuleBody(functions, child.records, child.enums, child.implementations,
                          child.traits, child.modules, child.crate_documentation,
                          child.crate_aliases, child.crate_policy, child.conditional_items,
                          uses=child.uses, consts=child.consts, statics=child.statics)

    def const_item(self, public, documentation):
        """`const NAME: Type = Expression;` (Rust Reference items/constant-items)."""
        if self.token.text == '_':
            raise self.token.error('unnamed const items (`const _`) are not implemented')
        name = self.name()
        self.expect(':')
        ty = self.type()
        if not self.accept('='):
            raise self.token.error('a free const item requires an initializer expression')
        expression = self.expression()
        self.expect(';')
        self.consts.append(ConstItem(name, ty, expression, public, documentation))

    def use_declaration(self, token, public, documentation):
        """Retain bounded SimplePaths; resolution owns every actual binding.

        Rust Reference items/use-declarations.html: a grouped UseTree
        `prefix::{tree, ...}` (including an empty prefix, `use {a, b::c};`,
        nesting and a trailing comma) declares one binding per leaf, with the
        prefix and leaf paths concatenated, exactly as separate simple use
        declarations would. Every leaf keeps the declaration's `use` token,
        documentation and source span; resolution checks each one, used or
        not. Globs, absolute paths, `self` leaves and empty groups remain gaps.
        A `pub use` re-export keeps `public`; resolution checks that every
        re-exported binding is itself public (rustc E0364/E0365).
        """
        from .attribute_metadata import offset
        if self.token.text == '::':
            raise self.token.error('absolute use paths are not implemented')
        leaves = []
        self.use_tree(token, (), leaves, 0)
        end = self.expect(';')
        span = (offset(self, token), offset(self, end) + 1)
        for path, alias in leaves:
            self.uses.append(UseDeclaration(token, path, alias, documentation, span, public))

    def use_tree(self, token, prefix, leaves, depth):
        if self.token.text == '*':
            raise self.token.error('glob use declarations require glob import resolution, which is not implemented')
        if self.token.text == '{':
            self.use_group(token, prefix, leaves, depth)
            return
        path = list(prefix)
        while True:
            if self.token.text in {'crate', 'self', 'super'}:
                if path and not (self.token.text == 'super' and all(p.text == 'super' for p in path)):
                    raise self.token.error('nonleading self/crate/super use segments are not implemented')
                path.append(self.take())
            else:
                path.append(self.name())
            if len(path) > 128:
                raise token.error('use path exceeds implementation limit (128 segments)')
            if not self.accept('::'):
                break
            if self.token.text == '*':
                raise self.token.error('glob use declarations require glob import resolution, which is not implemented')
            if self.token.text == '{':
                self.use_group(token, tuple(path), leaves, depth)
                return
        alias = None
        if self.accept('as'):
            if self.token.text == '_':
                raise self.token.error('anonymous use bindings are not implemented')
            alias = self.name()
        if path[-1].kind != 'name':
            raise path[-1].error('use paths naming a module qualifier alone are not implemented')
        if len(leaves) >= 4096:
            raise token.error('use declaration binding count exceeds implementation limit (4096)')
        leaves.append((tuple(path), alias))

    def use_group(self, token, prefix, leaves, depth):
        opener = self.expect('{')
        if depth >= 32:
            raise opener.error('use tree nesting exceeds implementation limit (32)')
        if self.token.text == '}':
            raise opener.error('empty use groups require binding-free path resolution, which is not implemented')
        while True:
            self.use_tree(token, prefix, leaves, depth + 1)
            if self.accept('}'):
                return
            self.expect(',')
            if self.accept('}'):
                return

    def extern_crate_item(self, token, start, public, documentation):
        """Parse the real extern-crate grammar, including inactive items."""
        from .attribute_metadata import offset
        crate = self.take() if self.token.text == 'self' else self.name()
        alias = None
        if self.accept('as'):
            alias = self.take() if self.token.text == '_' else self.name()
        end = self.expect(';')
        return ExternCrateItem(token, crate, alias, public, documentation,
                               (offset(self, start), offset(self, end) + 1))

    def register_extern_crate(self, item):
        # Inactive dependency declarations never enter this semantic owner.
        # Active external dependencies retain their existing explicit boundary.
        if item.crate.text != 'self':
            raise item.crate.error('external crate dependency loading is not implemented; only an explicit crate-self alias is supported')
        if item.alias is None:
            raise item.crate.error("expected 'as' for an explicit crate-self alias")
        if item.alias.text == '_':
            raise item.alias.error('anonymous crate aliases are not implemented')
        self.crate_aliases.append(CrateSelfAlias(item.alias, item.public, item.documentation))

    def parse(self, *, _module=False, _inline=False) -> list[Function]:
        functions: list[Function] = []
        crate_docs = []
        while True:
            crate_docs.extend(self.documentation(inner=True, crate=not _module))
            if self.token.text != "#" or self.tokens[self.pos + 1].text != "!":
                break
            start = self.take(); self.expect("!"); self.expect("[")
            attribute = self.take()
            if _module:
                raise attribute.error("semantic inner module attributes require module context; this form is not implemented")
            if attribute.text in {'allow', 'warn', 'deny', 'forbid'}:
                from .attribute_metadata import crate_lint_attribute
                lint = crate_lint_attribute(self, attribute, start)
                self.crate_lint_attributes += (lint,)
                self.crate_documentation = tuple(crate_docs)
                if lint.builtin_lints:
                    self.require_checked_crate_lint(lint)
                continue
            if attribute.text == 'cfg_attr':
                from .configuration import crate_cfg_attribute, apply_crate_cfg
                syntax = crate_cfg_attribute(self, attribute, start)
                self.crate_cfg_syntax += (syntax,)
                self.crate_documentation = tuple(crate_docs)
                context = self.cfg_configuration(attribute, 'cfg_attr')
                self.crate_policy, configured = apply_crate_cfg(syntax, context, self.crate_policy)
                self.crate_cfg_attributes += (configured,)
                continue
            if attribute.text == "stable":
                from .attribute_metadata import stable_metadata
                if self.crate_stability is not None:
                    raise attribute.error("multiple stability levels on the same crate")
                self.crate_stability = stable_metadata(self, attribute, start)
                self.attribute_requirements.append((attribute, "staged_api"))
                continue
            if attribute.text == "no_core":
                self.crate_documentation = tuple(crate_docs)
                self.expect("]")
                self.crate_policy = self.crate_policy.with_no_core(attribute)
                self.validate_crate_policy()
                continue
            if self.compiler_context_attribute(attribute, start):
                continue
            if attribute.text != "no_std":
                self.crate_documentation = tuple(crate_docs)
                raise attribute.error(f"unsupported semantic crate attribute {attribute.text!r}; no attribute is silently ignored")
            self.expect("]")
            self.crate_policy = self.crate_policy.with_no_std(attribute)
        self.crate_documentation = tuple(crate_docs)
        self.validate_attribute_context()
        pending_cfg = None
        while self.token.kind != "eof" and not (_inline and self.token.text == "}"):
            self.finish_configured_item(pending_cfg, functions)
            pending_cfg = None
            item_start = self.pos
            cfg_attributes = []
            item_snapshot = self.item_snapshot(functions)
            try:
                repr_c = no_mangle = False
                module_path = None
                enum_repr = None
                language_item = None
                docs = []
                inline_attributes = []
                derives = []
                helpers = []
                while True:
                    docs.extend(self.documentation())
                    if not self.accept("#"):
                        break
                    attribute_start = self.tokens[self.pos - 1]
                    self.expect("[")
                    attribute = self.take()
                    if attribute.text == 'inline':
                        from .inline_attributes import inline_attribute
                        inline_attributes.append(inline_attribute(self, attribute, attribute_start))
                        continue
                    if attribute.text == 'cfg':
                        from .configuration import item_cfg_attribute, ConfiguredItemAttribute
                        syntax = item_cfg_attribute(self, attribute, attribute_start)
                        self.item_cfg_syntax += (syntax,)
                        context = self.cfg_configuration(attribute, 'cfg')
                        cfg_attributes.append(ConfiguredItemAttribute(syntax, context, context.predicate(syntax.predicate)))
                        continue
                    if attribute.text == "lang":
                        if language_item is not None:
                            raise attribute.error("multiple lang attributes on one item are not implemented")
                        language_item = self.language_item_attribute(attribute)
                    elif attribute.text == "path" and module_path is None:
                        self.expect("=")
                        literal = self.take()
                        if literal.kind != "string":
                            raise literal.error("module path requires a literal string; macro paths are not implemented")
                        module_path = literal.literal_value
                    elif attribute.text == "repr" and not repr_c and enum_repr is None:
                        self.expect("(")
                        representation = self.take()
                        if representation.text == "C":
                            repr_c = True
                        elif representation.text in {"i32", "i64", "u32", "u64"}:
                            enum_repr = representation.text
                        else:
                            raise representation.error("unsupported representation; enum repr supports i32/i64/u32/u64")
                        self.expect(")")
                    elif attribute.text == "no_mangle" and not no_mangle:
                        no_mangle = True
                    elif attribute.text == "derive" and not self.core_prelude:
                        raise attribute.error("unsupported Rust attribute in this context: built-in derives expand to "
                                              "impls of core traits, which this context does not model")
                    elif attribute.text == "derive":
                        from .derives import derive_attribute
                        derives.extend(derive_attribute(self, attribute, attribute_start))
                        continue
                    else:
                        from .derives import has_procedural_derive, helper_attribute
                        if has_procedural_derive(derives):
                            helpers.append(helper_attribute(self, attribute))
                            continue
                        raise attribute.error("unsupported Rust attribute; only repr(C) records and no_mangle functions are admitted")
                    self.expect("]")
                if docs and self.token.kind == "eof":
                    raise docs[-1].token.error("outer documentation attribute has no following item")
                # Taken before the visibility, so a disabled item's `pub(in ..)`
                # restriction is rolled back with everything else it declared.
                cfg_snapshot = self.item_snapshot(functions) if cfg_attributes else None
                public = parse_visibility(self)
                from .inline_attributes import require_function_target
                require_function_target(self, inline_attributes)
                if derives and self.token.text not in {"struct", "enum", "union"}:
                    raise derives[0].token.error("`derive` may only be applied to `struct`s, `enum`s and `union`s (rustc E0774)")
                if cfg_attributes and not (self.token.text == 'extern' and self.tokens[self.pos + 1].text == 'crate'):
                    # Any other item is parsed in full (rustc strips only parsed
                    # items), then kept or rolled back when the next item starts.
                    pending_cfg = (tuple(cfg_attributes), item_start, cfg_snapshot)
                if language_item is not None and self.token.text != "trait":
                    raise language_item.token.error("language item requires a trait target")
                if self.accept("mod"):
                    if repr_c or enum_repr or no_mangle:
                        raise self.token.error("representation/no_mangle attributes do not apply to modules")
                    name = self.name()
                    if self.accept(";"):
                        body = None
                    else:
                        if module_path is not None:
                            raise name.error("path attributes on inline modules are not implemented")
                        self.expect("{")
                        body = self.module_body()
                    self.modules.append(Module(name, public, tuple(docs), module_path, body))
                    continue
                if module_path is not None:
                    raise self.token.error("path attribute is implemented only for outlined modules")
                if self.accept('use'):
                    use_token = self.tokens[self.pos - 1]
                    if repr_c or enum_repr or no_mangle:
                        raise use_token.error('representation/no_mangle attributes do not apply to use declarations')
                    self.use_declaration(use_token, public, tuple(docs))
                    continue
                const_fn = None
                if self.token.text == 'const' and self.tokens[self.pos + 1].text not in {'fn', 'unsafe', 'extern', 'async'}:
                    const_token = self.take()
                    if repr_c or enum_repr or no_mangle or inline_attributes or language_item is not None:
                        raise const_token.error('representation/no_mangle/inline/lang attributes do not apply to const items')
                    self.const_item(public, tuple(docs))
                    continue
                if self.token.text == 'static':
                    static_token = self.take()
                    if repr_c or enum_repr or no_mangle or inline_attributes or language_item is not None:
                        raise static_token.error('representation/no_mangle/inline/lang attributes on static items are not implemented')
                    mutable = self.accept('mut')
                    name = self.name()
                    self.expect(':')
                    ty = self.type()
                    self.expect('=')
                    expression = self.expression()
                    self.expect(';')
                    self.statics.append(StaticItem(name, ty, expression, mutable, public, tuple(docs)))
                    continue
                if self.token.text == 'const':
                    # `const fn` / `const unsafe fn` / `const extern "C" fn`
                    # (Rust Reference items/functions.html#const-functions).
                    const_fn = self.take()
                    if self.token.text == 'async':
                        raise self.token.error('async functions are not implemented')
                if self.accept("trait"):
                    if repr_c or enum_repr or no_mangle:
                        raise self.token.error("attributes on traits are not implemented")
                    name = self.name()
                    parameters = self.generic_parameters(allow_lifetimes=True) if language_item is not None else ()
                    if language_item is not None and (parameters or self.last_lifetime_parameters):
                        raise language_item.token.error("language item trait requires exactly zero generic parameters")
                    supertraits = []
                    if self.accept(":"):
                        while self.token.text != "{":
                            bound = self.token
                            if bound.kind == "lifetime" or bound.text in {"?", "for"}:
                                raise bound.error("lifetime, relaxed and higher-ranked supertraits are not implemented")
                            name_path = self.type()
                            if not isinstance(name_path, str):
                                raise bound.error("generic supertraits are not implemented")
                            supertraits.append((bound, name_path))
                            if len(supertraits) > 128:
                                raise bound.error("supertrait list exceeds implementation limit (128)")
                            if not self.accept("+"):
                                break
                    if self.token.text != "{":
                        raise self.token.error("generic traits and where constraints are not implemented")
                    self.expect("{")
                    trait = Trait(name, documentation=tuple(docs), public=public,
                                  language_item=language_item, type_parameters=parameters,
                                  supertraits=tuple(supertraits))
                    while not self.accept("}"):
                        from .inline_attributes import function_attributes
                        method_docs, method_inline = function_attributes(self)
                        require_function_target(self, method_inline)
                        if self.token.text == 'type':
                            if language_item is not None:
                                raise self.token.error('associated types on inspected language-item traits are not implemented')
                            trait.associated_types.append(self.associated_type(
                                declaration=True, documentation=method_docs))
                            continue
                        if self.token.text != "fn":
                            raise self.token.error("only safe required or default trait methods and associated types are implemented; associated constants and unsafe methods are not implemented")
                        trait.methods.append(self.function("Rust", False, False, False,
                            declaration=True, implementation=Implementation(name, "Self", trait=name.text),
                            trait_declaration=True, documentation=method_docs, inline_attributes=method_inline))
                    self.traits.append(trait)
                    continue
                if self.accept("impl"):
                    if public or repr_c or enum_repr or no_mangle:
                        raise self.token.error("visibility and attributes on impl blocks are not implemented")
                    token = self.tokens[self.pos - 1]
                    parameters = self.generic_parameters(allow_lifetimes=True, allow_bounds=True)
                    parameter_bounds = tuple(self.last_parameter_bounds)
                    lifetime_parameters = tuple(self.last_lifetime_parameters)
                    owner = self.type()
                    trait_name = None
                    if self.accept("for"):
                        # Generic and blanket trait impls (`impl<T> Tr for S<T>`,
                        # `impl<'a, I: ?Sized> Tr for &'a mut I where I: Tr`) are
                        # parsed and retained; their instantiation is a semantic gap.
                        trait_name, owner = owner, self.type()
                    where_predicates = ()
                    if self.token.text == "where":
                        self.take()
                        where_predicates = self.function_where_clause(parameters, owner_parameters=())
                    self.expect("{")
                    docs.extend(self.documentation(inner=True))
                    implementation = Implementation(token, owner, parameters, trait_name, tuple(docs),
                                                    parameter_bounds, where_predicates,
                                                    lifetime_parameters=lifetime_parameters)
                    self.implementations.append(implementation)
                    associated_types = []
                    while not self.accept("}"):
                        from .inline_attributes import function_attributes
                        method_docs, method_inline = function_attributes(self)
                        visible = parse_visibility(self)
                        require_function_target(self, method_inline)
                        const_method = None
                        if self.token.text == 'const' and self.tokens[self.pos + 1].text in {'fn', 'unsafe', 'extern'}:
                            const_method = self.take()
                            if trait_name is not None:
                                raise const_method.error('functions in trait impls cannot be declared const (rustc E0379)')
                        elif self.token.text == 'const':
                            raise self.token.error('associated constants in impls are not implemented')
                        unsafe = self.accept("unsafe")
                        if trait_name is not None and (visible or unsafe):
                            raise self.token.error("trait impl methods must be safe and have no visibility qualifier in this slice")
                        if self.token.text == 'type':
                            if trait_name is None:
                                raise self.token.error('inherent associated types are not implemented')
                            associated_types.append(self.associated_type(
                                declaration=False, documentation=method_docs))
                            continue
                        if trait_name is not None and self.token.text != "fn":
                            raise self.token.error("trait associated constants are not implemented")
                        functions.append(self.function("Rust", visible, False, unsafe,
                            implementation=implementation, documentation=method_docs,
                            inline_attributes=method_inline))
                        functions[-1].const_fn = const_method is not None
                    if associated_types:
                        original = implementation
                        implementation = replace(original, associated_types=tuple(associated_types))
                        self.implementations[-1] = implementation
                        for function in functions:
                            if function.implementation is original:
                                function.implementation = implementation
                    continue
                if self.accept("enum"):
                    if repr_c or no_mangle:
                        raise self.token.error("enum repr(C) and no_mangle are not supported")
                    from .derives import has_procedural_derive
                    self.enumeration(enum_repr, tuple(docs), public=public, helpers=has_procedural_derive(derives))
                    self.enums[-1].derives = tuple(derives)
                    self.enums[-1].helper_attributes = tuple(helpers) + self.enums[-1].helper_attributes
                    continue
                if enum_repr is not None:
                    raise self.token.error("integer repr applies only to an enum")
                if self.accept("struct"):
                    if no_mangle:
                        raise self.token.error("no_mangle is supported only on extern C function definitions")
                    from .derives import has_procedural_derive
                    self.record(repr_c, tuple(docs), public=public, helpers=has_procedural_derive(derives))
                    self.records[-1].derives = tuple(derives)
                    self.records[-1].helper_attributes = tuple(helpers) + self.records[-1].helper_attributes
                    continue
                if repr_c:
                    raise self.token.error("repr(C) applies only to a struct")
                if self.token.kind == "name" and self.tokens[self.pos + 1].text == "!":
                    raise self.token.error("macro_rules! definitions and item macro invocations require "
                                           "macro-by-example expansion, which is not implemented")
                unsafe = self.accept("unsafe")
                abi = "Rust"
                if self.accept("extern"):
                    extern_token = self.tokens[self.pos - 1]
                    if self.accept("crate"):
                        if const_fn is not None:
                            raise const_fn.error("expected `fn` after `const extern`")
                        if unsafe or no_mangle or repr_c or enum_repr is not None or language_item is not None:
                            raise self.token.error("attributes/unsafe on crate aliases are not implemented")
                        item = self.extern_crate_item(extern_token, self.tokens[item_start], public, tuple(docs))
                        if cfg_attributes:
                            from .configuration import ConfiguredItem
                            configured = ConfiguredItem(tuple(cfg_attributes), item,
                                                        tuple(self.tokens[item_start:self.pos]), item.source_span)
                            self.conditional_items += (configured,)
                            if not configured.enabled:
                                del self.visibility_restrictions[cfg_snapshot[-1]:]
                                continue
                        self.register_extern_crate(item)
                        continue
                    if self.token.kind == "string":
                        token = self.take()
                        if token.literal_value == "C-unwind":
                            raise token.error("C-unwind requires owned cross-language unwinding, which is not implemented")
                        if token.literal_value != "C":
                            raise token.error('only the non-unwinding extern "C" ABI is supported')
                    abi = "C"
                    if self.accept("{"):
                        if const_fn is not None:
                            raise const_fn.error("an extern block cannot be `const`")
                        if no_mangle or public or docs:
                            raise self.token.error("attributes and visibility on extern blocks are not supported")
                        while not self.accept("}"):
                            from .inline_attributes import function_attributes
                            declaration_docs, declaration_inline = function_attributes(self)
                            visible = parse_visibility(self)
                            require_function_target(self, declaration_inline)
                            functions.append(self.function("C", visible, False, True, declaration=True,
                                documentation=declaration_docs, inline_attributes=declaration_inline))
                        continue
                functions.append(self.function(abi, public, no_mangle, unsafe, documentation=tuple(docs),
                                               inline_attributes=tuple(inline_attributes)))
                functions[-1].const_fn = const_fn is not None
            except RustError as error:
                # rustc strips a disabled item before resolving or expanding
                # anything in it; it only has to be valid syntax, which the
                # required rustc check (or its attestation) guarantees. When
                # PCC's own parse of such an item reaches an unimplemented
                # form, skip it by delimiter structure instead.
                if not any(not attribute.enabled for attribute in cfg_attributes):
                    raise
                pending_cfg = None
                self.skip_disabled_item(item_start, tuple(cfg_attributes), item_snapshot, functions, error)
        self.finish_configured_item(pending_cfg, functions)
        if _inline:
            self.expect("}")
        self.validate_attribute_context()
        if not _module and not (self.modules or self.crate_aliases or self.uses):
            # A single-module crate: every restriction is owned by the root.
            # Module crates validate each one against its owner in resolution.
            for restriction in self.visibility_restrictions:
                restriction_components(restriction, (), self.crate_policy.edition)
        return functions

    def validate_crate_policy(self):
        self.crate_policy.require_owned_lang_item_context()

    def cfg_configuration(self, attribute, kind):
        """The one cfg owner of this parse: owned, or an actual checked command."""
        if self._configuration is not None:
            return self._configuration
        from .external_check import CheckedRustSource
        if type(self._rustc_check) is not CheckedRustSource:
            raise attribute.error(f'{kind} requires an actual source/options-bound CheckedRustSource configuration')
        return self._rustc_check.configuration(self.source, edition=self.crate_policy.edition)

    # Every parser collection one module item can extend. A disabled item is
    # parsed like any other, then each collection is cut back to its length
    # before the item, so nothing it declared reaches names, files or IR.
    _ITEM_COLLECTIONS = ('records', 'enums', 'implementations', 'traits', 'modules', 'uses', 'consts', 'statics',
                         'crate_aliases', 'attribute_requirements', 'visibility_restrictions',
                         'macro_expansions')

    # Items whose syntax ends with a braced body (or `;` when bodiless); the
    # others end only at a top-level `;`, even when they contain braces.
    _BRACED_ITEMS = frozenset({'fn', 'struct', 'enum', 'union', 'trait', 'impl', 'mod', 'macro_rules'})
    _SEMICOLON_ITEMS = frozenset({'use', 'const', 'static', 'type'})

    def skip_token_tree(self):
        opener = self.take()
        closers = {'(': ')', '[': ']', '{': '}'}
        stack = [closers[opener.text]]
        while stack:
            token = self.take()
            if token.kind == 'eof':
                raise opener.error(f'unclosed delimiter {opener.text!r}')
            if token.kind == 'punct' and token.text in closers:
                stack.append(closers[token.text])
            elif token.kind == 'punct' and token.text in {')', ']', '}'}:
                if token.text != stack.pop():
                    raise token.error(f'mismatched closing delimiter {token.text!r}')

    def skip_disabled_item(self, start, attributes, snapshot, functions, error):
        """Skip one cfg-disabled item by token-tree structure and record it."""
        from .attribute_metadata import offset
        from .configuration import ConfiguredItem
        del functions[snapshot[0]:]
        for name, length in zip(self._ITEM_COLLECTIONS, snapshot[1:]):
            del getattr(self, name)[length:]
        self.pos = start
        while True:
            if self.token.kind == 'doc_outer':
                self.take()
            elif self.token.text == '#' and self.tokens[self.pos + 1].text == '[':
                self.take()
                self.skip_token_tree()
            else:
                break
        kind = None
        while True:
            token = self.token
            if token.kind == 'eof':
                raise error
            if kind is None and token.text in self._BRACED_ITEMS | self._SEMICOLON_ITEMS | {'extern'}:
                following = self.tokens[self.pos + 1]
                if token.text == 'const' and following.text in {'fn', 'unsafe', 'async', 'extern'}:
                    kind = 'fn'
                elif token.text == 'extern':
                    kind = 'use' if following.text == 'crate' else 'fn'
                else:
                    kind = token.text
            if token.text == ';':
                self.take()
                break
            if token.text in {'(', '[', '{'}:
                braced = token.text == '{' and kind not in self._SEMICOLON_ITEMS
                self.skip_token_tree()
                if braced:
                    break
                continue
            self.take()
        first, last = self.tokens[start], self.tokens[self.pos - 1]
        end = last.source_end if last.source_end is not None else offset(self, last) + len(last.text)
        self.conditional_items += (ConfiguredItem(attributes, (), tuple(self.tokens[start:self.pos]),
                                                  (offset(self, first), end)),)

    def item_snapshot(self, functions):
        return (len(functions),) + tuple(len(getattr(self, name)) for name in self._ITEM_COLLECTIONS)

    def finish_configured_item(self, pending, functions):
        if pending is None:
            return
        from .attribute_metadata import offset
        from .configuration import ConfiguredItem
        attributes, start, snapshot = pending
        declared = [functions[snapshot[0]:]] + [
            getattr(self, name)[length:] for name, length in zip(self._ITEM_COLLECTIONS, snapshot[1:])]
        first, last = self.tokens[start], self.tokens[self.pos - 1]
        end = last.source_end if last.source_end is not None else offset(self, last) + len(last.text)
        configured = ConfiguredItem(attributes, tuple(item for group in declared for item in group),
                                    tuple(self.tokens[start:self.pos]), (offset(self, first), end))
        self.conditional_items += (configured,)
        if not configured.enabled:
            del functions[snapshot[0]:]
            for name, length in zip(self._ITEM_COLLECTIONS, snapshot[1:]):
                del getattr(self, name)[length:]

    def require_checked_crate_lint(self, attribute):
        # A successful check of the exact source owns all lint-level effects,
        # including suppression, errors and forbidden overrides. Parsing this
        # finite metadata family is not permission to ignore any other attribute.
        from .external_check import CheckedRustSource
        checked = self._rustc_check
        if type(checked) is not CheckedRustSource:
            raise attribute.token.error('missing_docs lint semantics require an actual source/options-bound CheckedRustSource; owned Rust lint checking is not implemented')
        record = checked.provenance
        try:
            checked.validate(self.source, target_triple=record.target_triple,
                             overflow_checks=record.overflow_checks,
                             edition=self.crate_policy.edition)
        except RustError as error:
            raise attribute.token.error(str(error)) from error

    def compiler_context_attribute(self, attribute, start):
        return False

    def language_item_attribute(self, attribute):
        raise attribute.error("unsupported Rust attribute; language items require an owned compiler context")

    def validate_attribute_context(self):
        # Retained metadata is not permission to discard staging/feature rules.
        # No public override exists for unowned feature/lang/no_core semantics.
        if self.attribute_requirements:
            token, feature = self.attribute_requirements[0]
            raise token.error(f"attribute metadata requires {feature} compiler context; this context is not implemented")

    def function(self, abi, public, no_mangle, unsafe, declaration=False,
                 implementation=None, trait_declaration=False, documentation=(),
                 inline_attributes=()) -> Function:
        self.expect("fn")
        name = self.name()
        # Bound spelling belongs to the function/method suffix. StaticTraits
        # separately validates the canonical contract of required/impl methods;
        # an impl prefix still has its own implicit Sized obligations.
        parameters = self.generic_parameters(allow_lifetimes=True, allow_bounds=True)
        parameter_bounds = tuple(self.last_parameter_bounds)
        lifetime_parameters = tuple(self.last_lifetime_parameters)
        if implementation is not None:
            # Keep one declaration-bound parameter vector for the shared
            # function registry, with a fixed impl prefix and a method suffix.
            parameters = implementation.type_parameters + parameters
        if parameters and (abi != "Rust" or no_mangle or declaration and not trait_declaration):
            raise name.error("generic function foreign/export ABI is not implemented")
        self.expect("(")
        params = []
        receiver = False
        explicit_receiver = False
        if not self.accept(")"):
            while True:
                mutable = self.accept("mut")
                if self.token.text == "&":
                    ampersand = self.take()
                    if mutable:
                        raise ampersand.error("mut must follow & in a reference receiver")
                    lifetime = self.take().text if self.token.kind == "lifetime" else None
                    reference_mutable = self.accept("mut")
                    param = self.expect("self")
                    if implementation is None or params:
                        raise param.error("self receiver must be the first parameter of an inherent method")
                    if self.token.text == ":":
                        raise self.token.error("explicit receiver types are not implemented")
                    params.append((param, SharedReference("Self", reference_mutable, lifetime), False))
                    receiver = True
                    if self.accept(")"):
                        break
                    self.expect(",")
                    if self.accept(")"):
                        break
                    continue
                if self.token.text == "self":
                    param = self.take()
                    if implementation is None or params:
                        raise param.error("self receiver must be the first parameter of an inherent method")
                    explicit_receiver = self.accept(":")
                    receiver_type = self.type() if explicit_receiver else "Self"
                    if receiver_type != "Self" and not (isinstance(receiver_type, SharedReference) and receiver_type.referent == "Self"):
                        raise param.error("arbitrary explicit receiver types are not implemented")
                    params.append((param, receiver_type, mutable))
                    receiver = True
                    if self.accept(")"):
                        break
                    self.expect(",")
                    if self.accept(")"):
                        break
                    continue
                if declaration and not trait_declaration and mutable:
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
        where_token, where_predicates, self_bounds = None, (), ()
        if self.token.text == "where":
            where_token = self.take()
            if abi != 'Rust' or no_mangle or declaration and not trait_declaration:
                raise where_token.error('where constraints are implemented only on Rust functions and method type parameters')
            # Owner predicates constrain this method, not the whole impl.
            # Keep their subjects in the existing fixed-prefix/suffix vector;
            # GenericFunctions checks both before requesting an instance.
            where_predicates = self.function_where_clause(parameters,
                owner_parameters=implementation.type_parameters if implementation is not None else None,
                declaration=trait_declaration)
            self_bounds = tuple(self.last_self_bounds)
        if declaration and not (trait_declaration and self.token.text == "{"):
            self.expect(";")
            body = None
        else:
            body = self.block(function_inner_docs=True)
            documentation += self.last_function_documentation
        return Function(name, params, result, body, abi, public, no_mangle, unsafe,
                        type_parameters=parameters, implementation=implementation, receiver=receiver,
                        lifetime_parameters=lifetime_parameters, explicit_receiver=explicit_receiver, documentation=documentation,
                        parameter_bounds=parameter_bounds,
                        where_token=where_token, where_predicates=where_predicates,
                        self_bounds=self_bounds, inline_attributes=inline_attributes)

    def block(self, *, function_inner_docs=False) -> Expr:
        token = self.expect("{")
        inner_docs = self.documentation(inner=True) if function_inner_docs else ()
        statements: list[Expr] = []
        tail = None
        while self.token.text != "}":
            if self.token.kind == "eof":
                raise self.token.error("unterminated block")
            if self.token.text == 'use':
                raise self.token.error('block-scoped use declarations require lexical item scopes, which are not implemented')
            if self.token.text in {'const', 'static'}:
                raise self.token.error('block-scoped const/static items and inline const blocks require lexical item scopes, which are not implemented')
            if self.accept(";"):
                continue
            if self.accept("let"):
                mutable = self.accept("mut")
                if self.token.text == "[":
                    raise self.token.error("array destructuring patterns are not implemented")
                wildcard = self.token.text == "_"
                name = self.take() if wildcard else self.name()
                if wildcard and mutable:
                    raise name.error("wildcard let patterns cannot be mutable")
                annotation = self.type() if self.accept(":") else None
                self.expect("=")
                init = self.expression()
                self.expect(";")
                statements.append(Expr("wildcard_let" if wildcard else "let", name,
                                       (annotation, mutable), [init]))
                continue
            expr = self.expression(complete_block=True)
            if self.accept(";"):
                statements.append(Expr("discard", expr.token, children=[expr]))
            elif self.token.text == "}":
                tail = expr
                break
            elif expr.kind in {"if", "if_let", "block", "while", "while_let", "for", "for_range", "for_range_inclusive", "loop", "unsafe", "match"}:
                statements.append(Expr("statement", expr.token, children=[expr]))
            else:
                raise self.token.error("expected ';' or end of block")
        self.expect("}")
        if function_inner_docs:
            self.last_function_documentation = inner_docs
        return Expr("block", token, len(statements), statements + ([tail] if tail else []))

    def if_expression(self, token: Token) -> Expr:
        pattern = None
        if self.accept("let"):
            pattern = self.pattern()
            self.expect("=")
        condition = self.expression(allow_struct=False)
        if pattern is not None and (
                condition.kind == "assign"
                or condition.kind == "binary" and condition.value in {"&&", "||"}):
            raise condition.token.error(
                "if let condition chains and ungrouped assignment scrutinees are not implemented; "
                "group a boolean or assignment scrutinee explicitly")
        children = [condition, self.block()]
        if self.accept("else"):
            # An else-if is an IfExpression, not a fresh postfix expression.
            # Trailing indexing/field/call syntax belongs to the complete
            # outer chain in value context (or to its statement boundary).
            children.append(self.if_expression(self.take()) if self.token.text == "if" else self.block())
        return Expr("if_let" if pattern is not None else "if", token,
                    value=pattern, children=children)

    def expression(self, minimum: int = 1, allow_struct: bool = True, *, complete_block: bool = False) -> Expr:
        token = self.take()
        text = token.text
        if token.kind in {"doc_inner", "doc_outer"}:
            raise token.error("documentation attributes on statements and nested block expressions are not implemented")
        if token.kind == "integer":
            expr = Expr("integer", token, text)
        elif token.kind == "string":
            # A string literal is a `&'static str` value (see types.STATIC_STR).
            expr = Expr("string", token, token.literal_value)
        elif text in {"true", "false"}:
            expr = Expr("bool", token, text == "true")
        elif text == "[":
            if self.accept("]"):
                expr = Expr("array", token, children=[])
            else:
                first = self.expression()
                if self.accept(";"):
                    length = self.expression()
                    self.expect("]")
                    expr = Expr("array_repeat", token, ArrayLength(length), [first])
                else:
                    items = [first]
                    while self.accept(",") and self.token.text != "]":
                        items.append(self.expression())
                    self.expect("]")
                    expr = Expr("array", token, children=items)
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
            expr = self.if_expression(token)
        elif text == "while":
            if self.accept("let"):
                pattern = self.pattern()
                self.expect("=")
                condition = self.expression(allow_struct=False)
                if condition.kind == "binary" and condition.value in {"&&", "||"}:
                    raise condition.token.error("while let condition chains are not implemented; group a boolean scrutinee explicitly")
                expr = Expr("while_let", token, pattern, [condition, self.block()])
            else:
                condition = self.expression(allow_struct=False)
                expr = Expr("while", token, children=[condition, self.block()])
        elif text == "loop":
            expr = Expr("loop", token, children=[self.block()])
        elif text == "for":
            pattern = self.pattern()
            if pattern.kind not in {"binding", "wildcard"}:
                raise pattern.token.error("for destructuring/refutable patterns are not implemented; use an identifier or wildcard")
            self.expect("in")
            source = self.expression(allow_struct=False)
            if self.token.text in {"..", "..="}:
                # Rust Reference range expressions: `start..end` / `start..=end`.
                # Both bounds are evaluated once, start first, before the loop.
                operator = self.take()
                if self.token.text == "{":
                    raise operator.error("unbounded range iteration (RangeFrom) is not implemented")
                end = self.expression(allow_struct=False)
                kind = "for_range_inclusive" if operator.text == "..=" else "for_range"
                expr = Expr(kind, operator, pattern, [source, end, self.block()])
            else:
                expr = Expr("for", token, pattern, [source, self.block()])
        elif text == "match":
            scrutinee = self.expression(allow_struct=False)
            self.expect("{")
            patterns, arms = [], []
            while not self.accept("}"):
                patterns.append(self.pattern())
                if self.token.text == "if":
                    raise self.token.error("match guards require additional effect and binding analysis, which is not implemented")
                self.expect("=>")
                arm = self.expression(complete_block=True)
                arms.append(arm)
                if self.accept("}"):
                    break
                if not self.accept(",") and arm.kind not in {"if", "if_let", "block", "while", "while_let", "for", "for_range", "for_range_inclusive", "loop", "unsafe", "match"}:
                    raise self.token.error("expected ',' between match arms")
            expr = Expr("match", token, patterns, [scrutinee] + arms)
        elif text in {"return", "break", "continue"}:
            children = []
            if self.token.text not in {";", "}", ")", ","}:
                if text != "return":
                    raise token.error("break values and loop labels are not implemented")
                children.append(self.expression())
            expr = Expr(text, token, children=children)
        elif text in {"-", "!"}:
            expr = Expr("unary", token, text, [self.expression(12, allow_struct=allow_struct)])
        elif text in {"&", "*"}:
            mutable = text == "&" and self.accept("mut")
            expr = Expr("borrow" if text == "&" else "deref", token, mutable,
                        children=[self.expression(12, allow_struct=allow_struct)])
        elif text == "<":
            owner = self.type()
            self.expect("as")
            trait = self.name()
            self.expect(">")
            self.expect("::")
            method = self.name()
            expr = Expr("trait_path", token, (owner, trait.text, method.text))
        elif text in {"|", "||"} or (text == "move" and self.token.text in {"|", "||"}):
            expr = self.closure_expression(token)
        elif (token.kind == "name" and self.token.text == "!" and text in {"env", "option_env", "matches"}
                and self.core_prelude):
            # Core's expression macros; no macro_rules! can shadow them here,
            # since macro definitions are not admitted.
            from .builtin_macros import expand_expression_macro, expand_matches
            expr = (expand_matches(self, token) if text == "matches"
                    else expand_expression_macro(self, token))
        elif token.kind == "name" and self.token.text == "!":
            raise token.error("macro invocations require macro-by-example expansion, which is not implemented")
        elif (token.kind == "name" and text != "_") or text in {"self", "Self", "super", "crate"}:
            owner, variant = self.expression_path(token)
            if variant is not None:
                if self.last_path_type_arguments is not None and self.token.text != "(":
                    raise token.error("generic associated items require a direct call; stored function values are not implemented")
                expr = Expr("variant", token, (owner, variant),
                            type_arguments=self.last_path_type_arguments)
            else:
                expr = Expr("name", token, owner)
        else:
            raise token.error(f"unsupported expression {text!r}; borrowing, heap, macros and panic are not implemented")
        while True:
            # Statement/arm grammar gives a complete ExpressionWithBlock
            # priority over an ambiguous call/index/infix continuation. Dot
            # access cannot begin a new expression and remains a continuation.
            # Grouped expressions and initializer/operand value contexts do
            # not have this restriction. Keep else-if under the same context.
            if (complete_block and expr.kind in {"if", "if_let", "block", "while", "while_let", "for", "for_range", "for_range_inclusive", "loop", "unsafe", "match"}
                    and self.token.text != "."):
                break
            if self.token.text == "::" and expr.kind in {"field", "variant", "trait_path"}:
                separator = self.take()
                if expr.kind == "field" and expr.token.kind != "name":
                    raise separator.error("tuple fields do not have generic method arguments")
                if expr.type_arguments is not None or self.token.text != "<":
                    raise separator.error("expected one method type-argument list")
                expr.type_arguments = self.type_arguments(allow_empty=True)
                if self.token.text != "(":
                    raise separator.error("generic associated items require a direct call; stored function values are not implemented")
                continue
            if self.token.text == "{" and expr.kind in {"name", "variant"} and allow_struct:
                self.take()
                fields, values = [], []
                while not self.accept("}"):
                    field = self.name()
                    fields.append(field)
                    values.append(self.expression() if self.accept(":") else Expr("name", field, field.text))
                    if self.accept("}"):
                        break
                    self.expect(",")
                path = expr.value
                if expr.kind == "variant":
                    owner, member = path
                    if not isinstance(owner, str):
                        raise expr.token.error("named-field associated constructors are not implemented")
                    path = owner + "::" + member
                expr = Expr("construct", expr.token, fields, values, binding=path)
                continue
            if self.token.text == "[":
                index_token = self.take()
                index = self.expression()
                self.expect("]")
                expr = Expr("index", index_token, children=[expr, index])
                continue
            if self.token.text == "?":
                # The question-mark operator is postfix and binds like field,
                # call and index continuations, before any unary operator.
                expr = Expr("try", self.take(), children=[expr])
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
                self.call_serial += 1
                expr = Expr("call", expr.token, children=[expr] + args, source_call_id=self.call_serial)
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
        if expr.kind == "trait_path":
            raise expr.token.error("qualified trait items require a direct call; stored function values are not implemented")
        return expr

    def enumeration(self, representation, documentation=(), public=False, helpers=False) -> None:
        name = self.name()
        parameters = self.generic_parameters(allow_lifetimes=True)
        lifetime_parameters = tuple(self.last_lifetime_parameters)
        if self.token.text == "where":
            raise self.token.error("generic where constraints and traits are not implemented")
        self.expect("{")
        from .derives import helper_attributes
        variants, payloads, variant_docs, payload_docs, retained = [], {}, [], {}, []
        named_payloads = {}
        while not self.accept("}"):
            variant_docs.append(self.documentation())
            retained.extend(helper_attributes(self, helpers))
            variant = self.name()
            if self.accept("("):
                fields, field_docs = [], []
                while not self.accept(")"):
                    field_docs.append(self.documentation())
                    retained.extend(helper_attributes(self, helpers))
                    fields.append(self.type())
                    if self.accept(")"):
                        break
                    self.expect(",")
                if not fields:
                    raise variant.error("empty tuple variants are not implemented; use a unit variant")
                payloads[variant.text] = tuple(fields)
                payload_docs[variant.text] = tuple(field_docs)
            if self.accept("{"):
                # A struct-like variant (Rust Reference items/enumerations):
                # stored positionally like a tuple payload, with its names.
                fields, field_docs, names = [], [], []
                while not self.accept("}"):
                    field_docs.append(self.documentation())
                    retained.extend(helper_attributes(self, helpers))
                    if self.token.text == "pub":
                        raise self.token.error("visibility qualifiers are not permitted on enum variant fields (rustc E0449)")
                    field_name = self.name()
                    if field_name.text in {item.text for item in names}:
                        raise field_name.error(f"field `{field_name.text}` is already declared (rustc E0124)")
                    self.expect(":")
                    names.append(field_name)
                    fields.append(self.type())
                    if self.accept("}"):
                        break
                    self.expect(",")
                if not fields:
                    raise variant.error("empty struct-like variants are not implemented; use a unit variant")
                payloads[variant.text] = tuple(fields)
                payload_docs[variant.text] = tuple(field_docs)
                named_payloads[variant.text] = tuple(names)
            value = self.expression() if self.accept("=") else None
            variants.append((variant, value))
            if self.accept("}"):
                break
            self.expect(",")
        if not variants:
            raise name.error("empty enums require uninhabited-type analysis, which is not implemented")
        if payloads and representation is None and any(value is not None for _, value in variants):
            raise name.error("explicit discriminants on an enum with payloads require an integer repr")
        self.enums.append(Enumeration(name, variants, representation, payloads=payloads, public=public,
                                      type_parameters=parameters, documentation=documentation,
                                      variant_documentation=tuple(variant_docs), payload_documentation=payload_docs,
                                      lifetime_parameters=lifetime_parameters, helper_attributes=tuple(retained),
                                      named_payloads=named_payloads))

    def pattern(self, *, alternatives=True) -> Pattern:
        if alternatives:
            self.accept("|")
        token = self.take()
        if token.text == "[":
            raise token.error("array destructuring patterns are not implemented")
        if token.text == "_":
            result = Pattern("wildcard", token)
        elif token.text == "(":
            if self.accept(")"):
                result = Pattern("unit", token)
            else:
                first = self.pattern()
                if self.accept(")"):
                    result = first
                else:
                    self.expect(",")
                    children = [first]
                    while not self.accept(")"):
                        children.append(self.pattern())
                        if self.accept(")"):
                            break
                        self.expect(",")
                    result = Pattern("tuple", token, children=children)
        elif token.kind == "integer" or token.text in {"-", "true", "false"}:
            self.pos -= 1
            # A literal pattern is not an arbitrary constant expression.
            if token.text == "-":
                self.take()
                literal = self.take()
                if literal.kind != "integer":
                    raise literal.error("negative literal pattern requires an integer")
                value = Expr("unary", token, "-", [Expr("integer", literal, literal.text)])
            elif token.kind == "integer":
                literal = self.take()
                value = Expr("integer", literal, literal.text)
            else:
                self.take()
                value = Expr("bool", token, token.text == "true")
            result = Pattern("literal", token, value)
        elif (token.kind == "name" or token.text in {"Self", "self", "super", "crate"}) and self.token.text == "::":
            owner, variant = self.expression_path(token)
            if self.last_path_type_arguments is not None:
                raise token.error("type arguments on enum variant pattern segments are not implemented; supply them on the enum owner")
            if variant is None:
                raise token.error("generic type path is not a value binding pattern")
            result = Pattern("variant", token, (owner, variant))
            if self.accept("{"):
                result.kind, result.named = "payload", True
                written, rest = [], False
                while not self.accept("}"):
                    if self.accept(".."):
                        rest = True
                        self.expect("}")
                        break
                    if self.token.text == "ref":
                        raise self.token.error("ref binding patterns are not implemented")
                    mutable = self.accept("mut")
                    field_name = self.name()
                    if mutable or not self.accept(":"):
                        result.children.append(Pattern("binding", field_name, mutable))
                    else:
                        result.children.append(self.pattern())
                    written.append(field_name)
                    if self.accept("}"):
                        break
                    self.expect(",")
                result.named_fields = (tuple(written), rest)
            elif self.accept("("):
                result.kind = "payload"
                while not self.accept(")"):
                    result.children.append(self.pattern())
                    if self.accept(")"):
                        break
                    self.expect(",")
        elif token.kind == "name" and self.token.text == "(":
            # An unqualified tuple-struct/variant pattern such as `Some(e)`.
            # Resolution of the single value-namespace segment is semantic.
            result = Pattern("payload", token, (None, token.text))
            self.take()
            while not self.accept(")"):
                result.children.append(self.pattern())
                if self.accept(")"):
                    break
                self.expect(",")
        elif token.kind == "name" or token.text == "mut":
            name = self.name() if token.text == "mut" else token
            if name.text == "_":
                raise name.error("mut wildcard is not a binding")
            result = Pattern("binding", name, token.text == "mut")
            if self.accept("@"):
                # Rust Reference patterns.html#identifier-patterns:
                # `ref? mut? IDENTIFIER @ PatternNoTopAlt`. The binding takes
                # the whole matched value; the subpattern decides the match.
                # A top-level `|` after it belongs to the enclosing pattern.
                result.kind = "at"
                result.children.append(self.pattern(alternatives=False))
        else:
            raise token.error("reference, range, named-field and rest patterns are not implemented")
        if alternatives and self.accept("|"):
            options = [result, self.pattern(alternatives=False)]
            while self.accept("|"):
                options.append(self.pattern(alternatives=False))
            result = Pattern("or", token, children=options)
        return result
