"""C++ declarator syntax, separate from semantic type identity and lowering.

Chunks run from the declared name outwards: ``*f()`` has function/pointer
chunks, whereas ``(*f)()`` has pointer/function chunks. Sema can apply them in
reverse to a resolved base type. They are not CppTypes, and parsing a chunk
does not authorize lowering it as a C pointer, reference, array or function.

The public frontend currently uses only ``parse_plain_name``. Rich declarators
remain syntax-only until lookup, conversion, lifetime and ABI rules exist.
Parameter and array callbacks must retain their semantic payloads; this module
neither rewrites source text nor evaluates or discards those expressions.
"""

from pcc.frontends.cpp.diagnostics import CppDiagnostic


_CV_QUALIFIERS = ("const", "volatile")
_CHUNK_FEATURES = {
    "pointer": "pointers",
    "lvalue-reference": "references",
    "rvalue-reference": "references",
    "array": "arrays-or-lambdas",
    "function": "function-declarators",
}


def _unsupported(feature, token):
    raise CppDiagnostic(
        "cpp.unsupported." + feature,
        "C++ declarator semantics not implemented at '" + token.value + "'",
        token.coord,
    )


def _scope_separator(cursor):
    """Consume one scope token, accommodating the existing C lexer's colons.

    Two separate preprocessing tokens ``: :`` must not become ``::``. Source
    coordinates distinguish adjacency without rebuilding or rewriting text.
    A future C++ lexer can supply the single scope token directly.
    """
    first = cursor.peek()
    if first.value == "::":
        cursor.take()
        return first
    index = cursor.position + 1
    if first.value != ":" or index >= len(cursor.tokens):
        return None
    second = cursor.tokens[index]
    if (second.value != ":" or first.coord.file != second.coord.file
            or first.coord.line != second.coord.line
            or first.coord.column is None
            or second.coord.column != first.coord.column + 1):
        return None
    cursor.take()
    cursor.take()
    return first


class QualifiedName:
    """An unresolved identifier path; never a claim that lookup succeeded."""

    def __init__(self, tokens, global_scope=False, coord=None):
        self.tokens = tuple(tokens)
        self.parts = tuple(token.value for token in tokens)
        self.identifier = tokens[-1]
        self.global_scope = global_scope
        self.coord = coord if coord is not None else tokens[0].coord


def parse_qualified_name(cursor, parse_identifier):
    """Read an identifier path, leaving all lookup and dependent typing to Sema.

    ``parse_identifier`` consumes one ordinary identifier on the same cursor
    and enforces the frontend's keyword/extension spelling policy.
    Template-ids and member-pointer operators are explicit remaining gaps.
    """
    start = cursor.peek()
    global_scope = _scope_separator(cursor) is not None
    tokens = [parse_identifier()]
    while True:
        if cursor.peek().value == "<":
            _unsupported("template-id-declarators", cursor.peek())
        if _scope_separator(cursor) is None:
            break
        if cursor.peek().value == "*":
            _unsupported("member-pointers", cursor.peek())
        if cursor.peek().value == "template":
            _unsupported("dependent-template-names", cursor.peek())
        tokens.append(parse_identifier())
    if cursor.peek().value == ":":
        raise CppDiagnostic(
            "cpp.invalid-qualified-name", "expected an adjacent '::' token",
            cursor.peek().coord,
        )
    return QualifiedName(tokens, global_scope, start.coord)


def parse_cv_qualifiers(cursor):
    """Return cv syntax in canonical order, without applying it to a type."""
    qualifiers = []
    while cursor.peek().value in _CV_QUALIFIERS:
        token = cursor.take()
        if token.value in qualifiers:
            raise CppDiagnostic(
                "cpp.duplicate-cv-qualifier", "duplicate '" + token.value + "' qualifier",
                token.coord,
            )
        qualifiers.append(token.value)
    return tuple(value for value in _CV_QUALIFIERS if value in qualifiers)


class DeclaratorChunk:
    def __init__(self, kind, token, qualifiers=(), parameters=None, bound=None,
                 ref_qualifier=None):
        self.kind = kind
        self.token = token
        self.coord = token.coord
        self.qualifiers = qualifiers
        self.parameters = parameters
        self.bound = bound
        self.ref_qualifier = ref_qualifier


class Declarator:
    def __init__(self, name, chunks, coord):
        self.name = name
        self.chunks = tuple(chunks)
        self.coord = coord


class DeclaratorParser:
    """Recursive named-declarator parser on the frontend's buffered cursor.

    Callbacks are called after an opening delimiter and must leave its closing
    delimiter unconsumed. They return checked parameter/bound objects, which
    are retained by identity. With no callback, only an empty clause/bound is
    recognized. Callbacks used during tentative parsing must be scope-read-only,
    just as this parser is. The caller owns declaration/expression ambiguity.

    Abstract declarators, attributes, packs, trailing returns, template-ids and
    exception specifications are not silently accepted by this component.
    """

    def __init__(self, cursor, parse_identifier, parse_parameters=None,
                 parse_array_bound=None):
        self.cursor = cursor
        self.parse_identifier = parse_identifier
        self.parse_parameters = parse_parameters
        self.parse_array_bound = parse_array_bound

    def _expect(self, value):
        token = self.cursor.peek()
        if token.value != value:
            raise CppDiagnostic(
                "cpp.invalid-declarator", "expected '" + value + "' in declarator",
                token.coord,
            )
        return self.cursor.take()

    def parse(self, parse_suffixes=True):
        return self._parse(parse_suffixes, False)

    def _parse(self, parse_suffixes, plain_only):
        start = self.cursor.peek()
        prefixes = []
        while self.cursor.peek().value in ("*", "&", "&&"):
            token = self.cursor.peek()
            kind = {"*": "pointer", "&": "lvalue-reference",
                    "&&": "rvalue-reference"}[token.value]
            if plain_only:
                _unsupported(_CHUNK_FEATURES[kind], token)
            self.cursor.take()
            qualifiers = ()
            if kind == "pointer":
                qualifiers = parse_cv_qualifiers(self.cursor)
            elif self.cursor.peek().value in _CV_QUALIFIERS:
                raise CppDiagnostic(
                    "cpp.invalid-declarator", "reference operators cannot have cv qualifiers",
                    self.cursor.peek().coord,
                )
            prefixes.append(DeclaratorChunk(kind, token, qualifiers))
        if self.cursor.peek().value == "(":
            self.cursor.take()
            inner = self._parse(parse_suffixes, plain_only)
            self._expect(")")
            name, chunks = inner.name, list(inner.chunks)
        else:
            name = parse_qualified_name(self.cursor, self.parse_identifier)
            chunks = []
        if plain_only and (name.global_scope or len(name.parts) != 1):
            _unsupported("qualified-names", name.tokens[0])
        while parse_suffixes and self.cursor.peek().value in ("(", "["):
            token = self.cursor.take()
            if token.value == "[":
                if self.cursor.peek().value == "[":
                    _unsupported("attributes", token)
                bound = None
                if self.cursor.peek().value != "]":
                    if self.parse_array_bound is None:
                        _unsupported("array-bounds", self.cursor.peek())
                    bound = self.parse_array_bound()
                    if bound is None:
                        raise CppDiagnostic(
                            "cpp.invalid-declarator", "array-bound callback discarded its expression",
                            token.coord,
                        )
                self._expect("]")
                chunks.append(DeclaratorChunk("array", token, bound=bound))
            else:
                parameters = ()
                if self.cursor.peek().value != ")":
                    if self.parse_parameters is None:
                        _unsupported("function-parameters", self.cursor.peek())
                    parameters = self.parse_parameters()
                    if parameters is None:
                        raise CppDiagnostic(
                            "cpp.invalid-declarator", "parameter callback discarded its clause",
                            token.coord,
                        )
                self._expect(")")
                qualifiers = parse_cv_qualifiers(self.cursor)
                ref_qualifier = None
                if self.cursor.peek().value in ("&", "&&"):
                    ref_qualifier = self.cursor.take().value
                chunks.append(DeclaratorChunk(
                    "function", token, qualifiers, parameters=parameters,
                    ref_qualifier=ref_qualifier,
                ))
                if self.cursor.peek().value in ("noexcept", "throw"):
                    _unsupported("exceptions", self.cursor.peek())
                if self.cursor.peek().value == "->":
                    _unsupported("trailing-return-types", self.cursor.peek())
        chunks.extend(reversed(prefixes))
        return Declarator(name, chunks, start.coord)


def require_plain_name(declarator):
    """Capability boundary: syntax success is not permission to lower a type."""
    name = declarator.name
    if name.global_scope or len(name.parts) != 1:
        _unsupported("qualified-names", name.tokens[0])
    for chunk in declarator.chunks:
        _unsupported(_CHUNK_FEATURES[chunk.kind], chunk.token)
    return name.identifier


def parse_plain_name(cursor, parse_identifier):
    """Read an optionally parenthesized scalar/function name, and nothing more.

    The owning parser keeps its parameter clause/direct initializer. Reject
    unsupported prefixes immediately, including inside redundant parentheses,
    rather than mistaking them for a missing closing parenthesis.
    """
    parser = DeclaratorParser(cursor, parse_identifier)
    return require_plain_name(parser._parse(False, True))
