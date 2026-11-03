"""Handwritten C++ parser with semantic lookup and reversible lookahead.

This increment accepts arithmetic/builtin-pointer free functions, deduced
arithmetic function templates and scalar control-flow statements. It builds the existing C AST only *after*
resolving names and checking supported semantics. It does not rewrite source
text, invoke the C parser, or discard missing lifetime/exception/template
semantics. Unimplemented features remain explicit errors.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.c_abi_layout import c_size_type_name
from pcc.frontends.c.parse.c_lex import CLexer
from pcc.frontends.c.parse.plyparser import Coord
from pcc.support.instantiation import InstanceRegistry
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.declarators import (
    DeclaratorParser, parse_cv_qualifiers, parse_qualified_name,
)
from pcc.frontends.cpp.sema import (
    Scope, Symbol, argument_dependent_lookup, resolve_overload,
)
from pcc.frontends.cpp.classes import (
    ClassType, c_class_type, default_initialize,
    is_class_type, member_access, parse_class, record_declaration_projection_supported, require_complete,
)
from pcc.frontends.cpp.initializers import (
    lower_aggregate_initializer, parse_initializer_list,
)
from pcc.frontends.cpp.constants import (
    KNOWN, binary_fact, converted_fact, known, not_constant, unary_fact, unmodeled,
)
from pcc.frontends.cpp.enums import parse_enum
from pcc.frontends.cpp.arrays import (
    const_array_elements, lower_array_initializer, make_array_type,
    require_complete_array,
)
from pcc.frontends.cpp.objects import parse_namespace_object
from pcc.frontends.cpp.types import (
    ArithmeticTypes, ArrayType, ConstType, FunctionType, PointerType, ReferenceType, TYPE_WORDS, array_type, canonical_type, c_type_names, const_qualified, contains_array,
    composite_qualified_pointer, contains_function, function_type, is_enum_type, pointer_type, qualify_const, unqualified,
)
from pcc.frontends.cpp.templates import (
    Pattern, TemplateArgument, TypeParameter, ValueParameter, argument_dependent,
    dependent, same_type, substitute,
)


_KEYWORDS = frozenset(
    "alignas alignof and and_eq asm auto bitand bitor bool break case catch char "
    "char16_t char32_t class compl const constexpr const_cast continue decltype "
    "default delete do double dynamic_cast else enum explicit export extern false "
    "float for friend goto if inline int long mutable namespace new noexcept not "
    "not_eq nullptr operator or or_eq private protected public register "
    "reinterpret_cast return short signed sizeof static static_assert static_cast "
    "struct switch template this thread_local throw true try typedef typeid "
    "typename union unsigned using virtual void volatile wchar_t while xor xor_eq".split()
)
_FEATURES = {
    "#": "preprocessor",
    "template": "templates", "typename": "templates",
    "class": "classes", "struct": "classes", "union": "classes",
    "namespace": "namespaces", "try": "exceptions", "catch": "exceptions",
    "throw": "exceptions", "noexcept": "exceptions", "new": "allocation",
    "delete": "allocation", "this": "members", "virtual": "members",
    "operator": "overload", "&": "references", "&&": "references",
    "*": "pointers", "[": "arrays-or-lambdas",
}
_BINARY_PRECEDENCE = {
    "||": 1, "&&": 2, "|": 3, "^": 4, "&": 5, "==": 6, "!=": 6,
    "<": 7, "<=": 7, ">": 7, ">=": 7, "<<": 8, ">>": 8,
    "+": 9, "-": 9, "*": 10, "/": 10, "%": 10,
}
_COMPARISONS = frozenset(("==", "!=", "<", "<=", ">", ">="))
_ALTERNATIVE_TOKENS = {"and": "&&", "or": "||", "not": "!", "bitand": "&",
                       "bitor": "|", "xor": "^", "compl": "~", "not_eq": "!=",
                       "and_eq": "&=", "or_eq": "|=", "xor_eq": "^="}


class CppToken:
    def __init__(self, kind, value, coord):
        self.kind = kind
        self.value = value
        self.coord = coord


class TokenCursor:
    def __init__(self, tokens):
        self.tokens = tokens
        self.position = 0

    def peek(self):
        return self.tokens[self.position]

    def take(self):
        token = self.peek()
        if token.kind != "EOF":
            self.position += 1
        return token

    def tentative(self):
        return TentativeParse(self)


class TentativeParse:
    """Read-only syntax speculation: token position is restored on every exit.

    Semantic declarations are deliberately not performed by the speculative
    routines. The real parse commits names only after declaration recognition.
    """

    def __init__(self, cursor):
        self.cursor = cursor
        self.position = cursor.position

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.cursor.position = self.position
        return False


class Expression:
    def __init__(self, node, dtype="int", lvalue=False, symbol=None, null_pointer=False,
                 assignment_result=False, constant_fact=None, discarded_known=False,
                 discarded_node=None, discarded_fact=None, static_address=None,
                 qualified_member=None, decltype_entity=None):
        self.node = node
        self.dtype = dtype
        self.lvalue = lvalue
        self.symbol = symbol
        self.null_pointer = null_pointer
        self.assignment_result = assignment_result
        self.constant_fact = constant_fact
        # Typed evaluation plan for discarding a class/nullptr_t glvalue,
        # without fetching its stored value. None is a proven effect-free
        # plan only when discarded_known is true; unknown plans fail closed.
        self.discarded_known = discarded_known
        self.discarded_node = discarded_node
        self.discarded_fact = discarded_fact
        self.static_address = static_address
        # An unparenthesized qualified-id retains its declaration identity for
        # [expr.unary.op]/3-4. In particular &S::x is not an ordinary T* even
        # in an unevaluated operand. Parentheses remove this syntactic role,
        # but do not remove the implicit object's cv-qualified member type.
        self.qualified_member = qualified_member
        # Declaration type only for an unparenthesized id/member expression.
        # Parentheses clear this role without changing the value category.
        self.decltype_entity = decltype_entity


class CppParser:
    def __init__(self, target_triple=None):
        self.types = ArithmeticTypes(target_triple)
        self.unevaluated_depth = 0
        self.instantiating_template = False

    def parse(self, text, filename=""):
        self.scope = Scope()
        self.return_type = None
        self.template_function = None
        self.member_function = None
        self.current_function = None
        self.this_symbol = None
        self.parsing_template = False
        self.instantiating_template = False
        self.unevaluated_depth = 0
        self.instance_registry = InstanceRegistry()
        self.class_template_declarations = []
        self.loop_depth = 0
        self.switch_contexts = []
        from pcc.frontends.cpp.lifetimes import AutomaticLifetimes
        self.lifetimes = AutomaticLifetimes()
        self.assignment_index = 0
        self.local_projection_index = 0
        self.function_projection_names = set()
        self.namespace_objects = []
        self.language_linkage = None
        tokens = self._tokenize(text, filename)
        self.projection_names = {token.value for token in tokens if token.kind == "ID"}
        self.cursor = TokenCursor(tokens)
        declarations = self._declarations()
        for symbol in self.namespace_objects:
            if symbol.internal_linkage and not symbol.defined:
                raise CppDiagnostic("cpp.unsupported.internal-object-declaration",
                                    "undefined internal objects need ODR-use tracking", symbol.coord)
        from pcc.frontends.cpp.class_templates import ClassTemplatePattern, emit_instance
        from pcc.frontends.cpp.class_template_members import MethodInstance, instantiate_body
        instances = self.instance_registry.drain(
            lambda entry: emit_instance(self, entry) if isinstance(entry.context, ClassTemplatePattern)
            else instantiate_body(self, entry.context) if isinstance(entry.context, MethodInstance)
            else self._instantiate_body(entry.value, entry.context),
        )
        instances = [item for item in instances if item is not None]
        declarations = self.class_template_declarations + declarations
        if instances:
            # Instances may call declarations appearing anywhere earlier in
            # the source definition context. Publish ordinary C prototypes,
            # not a second C++ ABI/code generator, before emitting any bodies.
            prototypes = []
            names = set()
            for item in declarations + instances:
                decl = item.decl if isinstance(item, c_ast.FuncDef) else item
                if isinstance(decl.type, c_ast.FuncDecl) and decl.name not in names:
                    prototypes.append(decl)
                    names.add(decl.name)
            return c_ast.FileAST(prototypes + declarations + instances)
        return c_ast.FileAST(declarations)

    @property
    def pending_instances(self):
        # Compatibility inspection only: the shared registry is the sole
        # registration/state/worklist owner. This is not a second queue.
        return self.instance_registry.pending

    def _declarations(self, namespace_body=False, single=False):
        declarations = []
        while self._peek().kind != "EOF" and not (namespace_body and self._peek().value == "}"):
            if self._accept(";"):
                continue
            value = self._peek().value
            if value == "inline" and self.cursor.tokens[self.cursor.position + 1].value != "namespace":
                raise CppDiagnostic("cpp.unsupported.inline-declarations",
                                    "inline object/function ODR emission is not implemented", self._peek().coord)
            if (value == "extern" and
                    self.cursor.tokens[self.cursor.position + 1].kind == "STRING_LITERAL"):
                declarations.extend(self._linkage_specification())
            elif value == "namespace" or value == "inline":
                declarations.extend(self._namespace())
            elif value in ("class", "struct", "union"):
                declaration = parse_class(self)
                declarations.append(declaration)
                # A class has no storage field for a method. Publish real C
                # prototypes after its record definition, with this only for non-static members.
                dtype = self.last_class_definition
                if dtype is not None:
                    declarations.extend(dtype.method_declarations)
            elif value == "enum":
                parse_enum(self)
            elif value in ("using", "typedef"):
                self._alias()
            elif value == "template":
                self._template()
            else:
                from pcc.frontends.cpp.constructors import constructor_ahead, parse_constructor
                from pcc.frontends.cpp.destructors import destructor_ahead, parse_destructor
                if destructor_ahead(self):
                    declaration = parse_destructor(self)
                elif constructor_ahead(self):
                    declaration = parse_constructor(self)
                else:
                    declaration = self._function(implicit_extern=single)
                if declaration is not None:
                    declarations.append(declaration)
            if single:
                break
        return declarations

    def _linkage_specification(self):
        keyword = self._expect("extern")
        if self.scope.kind != "namespace":
            raise CppDiagnostic("cpp.linkage-scope",
                                "a language linkage specification requires namespace scope", keyword.coord)
        language = self.cursor.take()
        if language.value not in ('"C"', '"C++"'):
            raise CppDiagnostic("cpp.unsupported.language-linkage",
                                "only C and C++ language linkage strings are implemented", language.coord)
        previous = self.language_linkage
        self.language_linkage = language.value[1:-1]
        try:
            if self._accept("{"):
                declarations = self._declarations(namespace_body=True)
                self._expect("}")
                return declarations
            if self._peek().value in (";", "}") or self._peek().kind == "EOF":
                raise CppDiagnostic("cpp.linkage-declaration",
                                    "language linkage requires a declaration", self._peek().coord)
            if (self._peek().value == "static" or
                    self._peek().value == "extern" and
                    self.cursor.tokens[self.cursor.position + 1].kind != "STRING_LITERAL"):
                raise CppDiagnostic("cpp.linkage-storage",
                                    "a declaration directly in a linkage specification cannot specify storage", self._peek().coord)
            return self._declarations(single=True)
        finally:
            self.language_linkage = previous

    def _namespace(self):
        inline = self._accept("inline") is not None
        keyword = self._expect("namespace")
        parent = self.scope
        name = None if self._peek().value == "{" else parse_qualified_name(self.cursor, self._identifier)
        if self._accept("="):
            if inline or name is None or name.global_scope or len(name.parts) != 1:
                raise CppDiagnostic("cpp.invalid-namespace-alias", "invalid namespace alias declaration", keyword.coord)
            target_name = parse_qualified_name(self.cursor, self._identifier)
            target = self.scope.resolve_name(target_name, namespace_only=True)
            if target is None:
                raise CppDiagnostic("cpp.not-a-namespace", "namespace alias target not found", target_name.coord)
            self._expect(";")
            symbol = Symbol(name.parts[0], "namespace-alias", name.coord)
            symbol.namespace_scope = target.namespace_scope
            previous = self.scope.names.get(symbol.name)
            if previous is not None and previous.kind == "namespace-alias" and previous.namespace_scope is symbol.namespace_scope:
                return []
            self.scope.declare(symbol)
            return []
        if parent.kind != "namespace":
            raise CppDiagnostic("cpp.namespace-scope", "namespace definition requires namespace scope", keyword.coord)
        if name is not None and (name.global_scope or inline and len(name.parts) != 1):
            raise CppDiagnostic("cpp.invalid-namespace-definition", "invalid C++17 namespace definition", name.coord)
        self._expect("{")
        try:
            if name is None:
                self.scope = self.scope.open_namespace(None, keyword.coord, inline)
            else:
                for token in name.tokens:
                    self.scope = self.scope.open_namespace(token.value, token.coord, inline)
            declarations = self._declarations(namespace_body=True)
            self._expect("}")
            return declarations
        finally:
            self.scope = parent

    def _name(self):
        return parse_qualified_name(self.cursor, self._identifier, reject_template_ids=False)

    def _type_symbol(self):
        with self.cursor.tentative():
            return self.scope.resolve_name(self._name())

    def _tokenize(self, text, filename):
        def error(message, line, column):
            raise CppDiagnostic("cpp.lexical", message, Coord(lexer.filename, line, column))

        # Brace effects and typedef classification belong to the parser/Sema,
        # not to the lexer; buffered tokens must survive later scope changes.
        lexer = CLexer(error, lambda: None, lambda: None, lambda name: False)
        lexer.filename = filename
        # [lex.phases]/1: accept LF, CRLF and CR physical line endings, as
        # the owned preprocessor does. CLexer consumes LF only. Map before
        # lexing (including comments/literals), never by dropping CR tokens:
        # a physical newline inside an ordinary quoted literal stays invalid.
        # This private view leaves the caller's source/receipt bytes intact;
        # one LF per physical line also preserves line/column diagnostics.
        lexer.input(text.replace("\r\n", "\n").replace("\r", "\n"))
        tokens = []
        while True:
            token = lexer.token()
            if token is None:
                break
            kind = "CPP_OPERATOR" if token.value in _ALTERNATIVE_TOKENS else token.type
            tokens.append(CppToken(kind, _ALTERNATIVE_TOKENS.get(token.value, token.value),
                                   Coord(lexer.filename, token.lineno,
                                         lexer.find_tok_column(token))))
        tokens.append(CppToken("EOF", "<eof>", Coord(lexer.filename, lexer.lineno)))
        return tokens

    def _peek(self):
        return self.cursor.peek()

    def _accept(self, value):
        if self._peek().value == value:
            return self.cursor.take()
        return None

    def _expect(self, value):
        token = self._accept(value)
        if token is None:
            self._unsupported(self._peek(), f"expected '{value}'")
        return token

    def _unsupported(self, token, detail=""):
        feature = _FEATURES.get(token.value, "syntax")
        message = f"C++ feature not implemented at '{token.value}'"
        if detail:
            message += f" ({detail})"
        raise CppDiagnostic(f"cpp.unsupported.{feature}", message, token.coord)

    def _identifier(self):
        token = self._peek()
        if token.kind != "ID" or token.value in _KEYWORDS:
            self._unsupported(token, "expected an identifier")
        if not token.value.isascii() or "$" in token.value:
            self._unsupported(token, "non-ASCII or extension identifier")
        return self.cursor.take()

    def _is_type(self, token):
        if token.value in TYPE_WORDS or token.value in ("const", "volatile", "decltype"):
            return True
        if token.kind == "ID" and token.value not in _KEYWORDS or token.value in (":", "::"):
            symbol = self._type_symbol()
            return symbol is not None and symbol.kind in ("type", "template-type", "class-template")
        return False

    def _check_value_type(self, dtype, token):
        plain = unqualified(dtype)
        nominal = unqualified(plain.pointee) if isinstance(plain, PointerType) else plain
        if self.parsing_template and isinstance(dtype, ReferenceType):
            raise CppDiagnostic("cpp.unsupported.references", "reference template deduction/replay is not implemented", token.coord)
        if self.parsing_template and contains_array(dtype):
            raise CppDiagnostic("cpp.unsupported.array-templates", "array template typing/deduction is not implemented", token.coord)
        if self.parsing_template and is_enum_type(nominal):
            raise CppDiagnostic("cpp.unsupported.enum-templates",
                                "enum-related template semantics are not implemented", token.coord)
        if self.parsing_template and (pointer_type(dtype) or isinstance(dtype, ConstType)):
            raise CppDiagnostic("cpp.unsupported.pointer-templates",
                                "pointer/cv template types are not implemented", token.coord)
        return self._check_extended_value_type(dtype, token)

    def _check_extended_value_type(self, dtype, token):
        # Expression/call checks must not replay declaration-context template
        # restrictions (for example top-level const on an ordinary call result).
        if dependent(dtype):
            return dtype
        if not self.types.value_lowering_supported(dtype) and not self.unevaluated_depth:
            raise CppDiagnostic("cpp.unsupported.extended-float",
                                "target long double value/ABI lowering is not implemented",
                                token.coord)
        return dtype

    def _type(self, check_lowering=True, storage_specifiers=None):
        token = self._peek()
        if not self._is_type(token):
            if self.scope.lookup(token.value) is not None:
                raise CppDiagnostic("cpp.not-a-type", f"'{token.value}' does not name a type",
                                    token.coord)
            self._unsupported(token, "expected an implemented arithmetic type")
        words = []
        qualifiers = []
        dtype = None
        while True:
            value = self._peek().value
            if storage_specifiers is not None and value in ("extern", "static"):
                if storage_specifiers:
                    raise CppDiagnostic("cpp.invalid-storage-specifier",
                                        "multiple storage class specifiers", self._peek().coord)
                storage_specifiers.append(self.cursor.take())
            elif value in ("const", "volatile"):
                qualifier = self.cursor.take()
                if value == "volatile":
                    raise CppDiagnostic("cpp.unsupported.volatile", "volatile access lowering is not implemented", qualifier.coord)
                if value in qualifiers:
                    raise CppDiagnostic("cpp.duplicate-cv-qualifier", "duplicate const qualifier", qualifier.coord)
                qualifiers.append(value)
            elif value in TYPE_WORDS and dtype is None:
                words.append(self.cursor.take().value)
            elif not words and dtype is None and value == "decltype":
                from pcc.frontends.cpp.type_queries import parse_decltype
                dtype = parse_decltype(self)
            elif not words and dtype is None and self._is_type(self._peek()):
                name = self._name()
                symbol = self.scope.resolve_name(name)
                if symbol.kind == "class-template":
                    from pcc.frontends.cpp.class_templates import specialize
                    dtype = specialize(self, symbol, name.coord)
                else:
                    dtype = symbol.dtype
            else:
                break
        if dtype is None:
            try:
                dtype = canonical_type(words)
            except ValueError as error:
                raise CppDiagnostic("cpp.invalid-type", str(error), token.coord)
        if qualifiers:
            dtype = qualify_const(dtype)
        return self._check_value_type(dtype, token) if check_lowering else dtype

    def _alias(self):
        previous = getattr(self, "class_array_alias", False)
        self.class_array_alias = True
        try:
            return self._alias_declaration()
        finally:
            self.class_array_alias = previous

    def _alias_declaration(self):
        keyword = self.cursor.take()
        if keyword.value == "using":
            if self._accept("namespace"):
                name = parse_qualified_name(self.cursor, self._identifier)
                target = self.scope.resolve_name(name, namespace_only=True)
                if target is None:
                    raise CppDiagnostic("cpp.not-a-namespace", "using-directive target not found", name.coord)
                self._expect(";")
                self.scope.add_using_directive(target.namespace_scope)
                return
            name_path = parse_qualified_name(self.cursor, self._identifier)
            if self._accept("="):
                if name_path.global_scope or len(name_path.parts) != 1:
                    raise CppDiagnostic("cpp.invalid-alias", "alias name must be unqualified", name_path.coord)
                name = name_path.identifier
                dtype = self._type()
                declaration = DeclaratorParser(
                    self.cursor, self._identifier,
                    parse_parameters=self._sizeof_parameter_syntax,
                    parse_array_bound=self._array_bound,
                ).parse_abstract()
                dtype = self._alias_declarator_type(declaration, dtype)
            else:
                while True:
                    if not name_path.global_scope and len(name_path.parts) == 1:
                        raise CppDiagnostic("cpp.invalid-using-declaration", "using-declaration requires a qualified name", name_path.coord)
                    symbol = self.scope.resolve_name(name_path)
                    if symbol is None:
                        raise CppDiagnostic("cpp.undeclared", "using-declaration target not found", name_path.coord)
                    self.scope.add_using_declaration(symbol, name_path.coord)
                    if not self._accept(","):
                        break
                    name_path = parse_qualified_name(self.cursor, self._identifier)
                self._expect(";")
                return
        else:
            dtype = self._type()
            declaration = DeclaratorParser(
                self.cursor, self._identifier,
                parse_parameters=self._sizeof_parameter_syntax,
                parse_array_bound=self._array_bound,
            ).parse()
            if declaration.name.global_scope or len(declaration.name.parts) != 1:
                raise CppDiagnostic("cpp.invalid-alias", "typedef name must be unqualified", declaration.coord)
            name = declaration.name.identifier
            dtype = self._alias_declarator_type(declaration, dtype)
        self._expect(";")
        self.scope.declare(Symbol(name.value, "type", name.coord, dtype=dtype))

    def _alias_declarator_type(self, declaration, dtype):
        for chunk in reversed(declaration.chunks):
            if chunk.kind == "function":
                if chunk.qualifiers or chunk.ref_qualifier:
                    raise CppDiagnostic("cpp.unsupported.function-declarators", "cv/ref-qualified function aliases are not implemented", chunk.coord)
                if function_type(dtype) or array_type(dtype):
                    raise CppDiagnostic("cpp.invalid-function-return", "function cannot return a function or array", chunk.coord)
                signature = (dtype,) + tuple(t for _, t in chunk.parameters)
                for t in signature:
                    leaf = unqualified(t)
                    while pointer_type(leaf) or array_type(leaf):
                        leaf = unqualified(leaf.pointee if pointer_type(leaf) else leaf.element)
                    if is_class_type(leaf) or is_enum_type(leaf) or contains_function(t):
                        raise CppDiagnostic("cpp.unsupported.function-pointers", "nested callback and nominal function types require additional ABI rules", chunk.coord)
                dtype = FunctionType(dtype, [t for _, t in chunk.parameters], self.language_linkage or "C++")
            elif chunk.kind == "pointer":
                dtype = self._pointer(dtype, chunk.token, chunk.qualifiers)
            elif chunk.kind == "array":
                dtype = make_array_type(dtype, chunk.bound, self.types, chunk.coord,
                                        allow_class_element=getattr(self, "class_array_alias", False))
            elif chunk.kind in ("lvalue-reference", "rvalue-reference"):
                dtype = self._reference(dtype, chunk.token)
            else:
                raise CppDiagnostic("cpp.unsupported.references", "reference alias semantics are not implemented", chunk.coord)
        return dtype

    def _pointer(self, dtype, token, qualifiers=()):
        if isinstance(dtype, ReferenceType):
            raise CppDiagnostic("cpp.invalid-reference-type", "a pointer cannot point to a reference", token.coord)
        leaf = dtype
        while array_type(leaf):
            leaf = leaf.element
        if array_type(dtype) and pointer_type(leaf):
            raise CppDiagnostic("cpp.unsupported.multilevel-pointers", "array-of-pointer pointee qualification requires deeper pointer rules", token.coord)
        if array_type(dtype) and dtype.bound is None:
            raise CppDiagnostic("cpp.unsupported.unknown-bound-array-pointers", "pointer to an unknown-bound array requires separate completeness rules", token.coord)
        if self.parsing_template and not getattr(self, 'class_template_signature', False):
            raise CppDiagnostic("cpp.unsupported.pointer-templates", "pointer template types are not implemented", token.coord)
        if "volatile" in qualifiers:
            raise CppDiagnostic("cpp.unsupported.volatile", "volatile access lowering is not implemented", token.coord)
        result = PointerType(dtype)
        return qualify_const(result) if "const" in qualifiers else result

    def _reference(self, dtype, token):
        if token.value != '&' or self.parsing_template or self.template_function is not None:
            raise CppDiagnostic("cpp.unsupported.references", "rvalue/dependent references require additional semantics", token.coord)
        if isinstance(dtype, ReferenceType):
            raise CppDiagnostic("cpp.unsupported.references", "reference collapsing requires declaration provenance", token.coord)
        if unqualified(dtype) == 'void':
            raise CppDiagnostic("cpp.invalid-reference-type", "a reference cannot refer to void", token.coord)
        if array_type(dtype) or function_type(dtype):
            raise CppDiagnostic("cpp.unsupported.references", "array/function reference binding is not implemented", token.coord)
        return ReferenceType(dtype)

    def _abstract_pointer_type(self, dtype):
        while self._peek().value in ("*", "&", "&&"):
            token = self.cursor.take()
            if token.value == '*':
                dtype = self._pointer(dtype, token, parse_cv_qualifiers(self.cursor))
            else:
                dtype = self._reference(dtype, token)
        return dtype

    def _apply_declarator(self, declaration, dtype, function=False, member=False, allow_class_element=False,
                          namespace_function=False):
        name = declaration.name
        if not member and not namespace_function and (name.global_scope or len(name.parts) != 1):
            raise CppDiagnostic("cpp.unsupported.qualified-names",
                                "out-of-namespace member declarations are not implemented", name.coord)
        chunks = list(declaration.chunks)
        if function:
            if not chunks or chunks[0].kind != "function":
                feature = "references" if chunks and "reference" in chunks[0].kind else "function-pointers"
                raise CppDiagnostic("cpp.unsupported." + feature, "only free function declarations are implemented", declaration.coord)
            clause = chunks.pop(0)
            if member:
                if "volatile" in clause.qualifiers or clause.ref_qualifier:
                    raise CppDiagnostic("cpp.unsupported.member-qualifiers", "volatile/ref-qualified member functions require additional object rules", clause.coord)
            elif clause.qualifiers or clause.ref_qualifier:
                raise CppDiagnostic("cpp.unsupported.function-declarators", "function cv/ref qualifiers are not implemented", clause.coord)
        for chunk in reversed(chunks):
            if chunk.kind == "array":
                if self.parsing_template:
                    raise CppDiagnostic("cpp.unsupported.array-templates", "dependent array bounds/types are not implemented", chunk.coord)
                dtype = make_array_type(dtype, chunk.bound, self.types, chunk.coord,
                                        allow_class_element=allow_class_element or getattr(self, "class_array_alias", False))
                continue
            if chunk.kind in ("lvalue-reference", "rvalue-reference"):
                dtype = self._reference(dtype, chunk.token)
                continue
            if chunk.kind != "pointer":
                feature = "references" if "reference" in chunk.kind else (
                    "arrays-or-lambdas" if chunk.kind == "array" else "function-pointers")
                raise CppDiagnostic("cpp.unsupported." + feature, "declarator semantics are not implemented", chunk.coord)
            dtype = self._pointer(dtype, chunk.token, chunk.qualifiers)
        return name.identifier, dtype

    def _array_bound(self):
        if self.parsing_template:
            raise CppDiagnostic("cpp.unsupported.array-templates", "dependent array bound analysis is not implemented", self._peek().coord)
        return self._expression()

    def _declarator(self, dtype, parse_arrays=True, allow_class_element=False):
        declaration = DeclaratorParser(self.cursor, self._identifier,
                                       parse_array_bound=self._array_bound,
                                       parse_functions=False).parse(parse_suffixes=parse_arrays)
        return self._apply_declarator(declaration, dtype, allow_class_element=allow_class_element)

    def _is_declaration(self, allow_range=False):
        if not self._is_type(self._peek()):
            return False
        with self.cursor.tentative():
            try:
                dtype = self._type(check_lowering=False)
            except CppDiagnostic:
                return True
            pointer_prefix = self._peek().value in ("*", "&", "&&")
            try:
                self._declarator(dtype, parse_arrays=False)
            except CppDiagnostic:
                # A type followed by a failed declarator is not permission to
                # reinterpret unsupported pointer/reference syntax as a value.
                return pointer_prefix
            return self._peek().value in (";", "=", "(", ",", "[", "{") or (allow_range and self._peek().value == ":")

    @staticmethod
    def _scalar_type(name, dtype, coord):
        if isinstance(dtype, ReferenceType):
            return c_ast.PtrDecl([], CppParser._scalar_type(name, dtype.referent, coord), coord)
        if array_type(dtype):
            dimension = None if dtype.bound is None else c_ast.Constant("int", str(dtype.bound), coord)
            return c_ast.ArrayDecl(CppParser._scalar_type(name, dtype.element, coord), dimension, [], coord)
        if is_class_type(dtype):
            return c_class_type(name, dtype, coord)
        qualifiers = ["const"] if isinstance(dtype, ConstType) else []
        dtype = unqualified(dtype)
        if isinstance(dtype, FunctionType):
            parameters = [c_ast.Decl(None, [], [], [], CppParser._scalar_type(None, p, coord), None, None, coord)
                          for p in dtype.parameters]
            if not parameters:
                parameters = [c_ast.Typename(None, [], CppParser._scalar_type(None, "void", coord), coord)]
            return c_ast.FuncDecl(c_ast.ParamList(parameters, coord),
                                  CppParser._scalar_type(name, dtype.result, coord), coord)
        if dtype == "nullptr":
            # nullptr_t is nominally distinct from every pointer in Sema. Its
            # ABI storage/argument representation is that of void*, not int.
            return c_ast.PtrDecl(qualifiers, CppParser._scalar_type(name, "void", coord), coord)
        if isinstance(dtype, PointerType):
            return c_ast.PtrDecl(qualifiers, CppParser._scalar_type(name, dtype.pointee, coord), coord)
        return c_ast.TypeDecl(name, qualifiers, c_ast.IdentifierType(c_type_names(dtype), coord), coord)

    def _decl(self, name, dtype, coord, init=None):
        return c_ast.Decl(name, [], [], [], self._scalar_type(name, dtype, coord), init, None, coord)

    def _local_projection_name(self, name):
        # A qualified C++ reference can select a namespace entity even when a
        # local of the same spelling is visible. C AST has no qualified IDs:
        # do not let C lexical lookup rebind that already-resolved reference.
        # Preserve other local spellings, and allocate a private name only on
        # a collision with an emitted namespace object/function name.
        if (name not in self.function_projection_names
                and all(symbol.c_name != name for symbol in self.namespace_objects)):
            return name
        return self._fresh_projection_name("__pcc_cpp_local_")

    def _fresh_projection_name(self, prefix):
        while True:
            projected = prefix + str(self.local_projection_index)
            self.local_projection_index += 1
            if projected not in self.projection_names:
                self.projection_names.add(projected)
                return projected

    def _decay_array(self, expression):
        if not array_type(expression.dtype):
            return expression
        if self.template_function is not None:
            raise CppDiagnostic("cpp.unsupported.array-templates", "array decay in templates is not implemented", expression.node.coord)
        dtype = PointerType(expression.dtype.element)
        coord = expression.node.coord
        target = c_ast.Typename(None, [], self._scalar_type(None, dtype, coord), coord)
        # [conv.array] forms the first-element address, not an array read.
        # For *pointer-to-array, preserve the pointer evaluation directly, as
        # with the existing &* cancellation. C's generic pointer-to-array
        # function projection does not retain enough type to safely emit an
        # intervening dereference. This applies to every array element type,
        # not only the implementation's va_list record.
        address = expression.node
        if isinstance(address, c_ast.UnaryOp) and address.op == "*":
            address = address.expr
        return Expression(c_ast.Cast(target, address, coord), dtype,
                          constant_fact=unmodeled(dtype, "array address constant proof is not implemented", coord))

    def _nullptr_rvalue(self, expression):
        # N4659 [conv.lval]/3.1: reading a nullptr_t glvalue produces nullptr,
        # without accessing the stored object. In particular an uninitialized
        # nullptr_t object does not supply indeterminate pointer bits. Preserve
        # evaluation of its base/index/address, but never fetch its storage.
        # This is a value conversion, not a void* alias or blanket AST rewrite.
        if (unqualified(expression.dtype) != "nullptr" or self.template_function is not None
                or self.unevaluated_depth):
            # Unevaluated operands retain type/access/conversion checking but
            # never emit the operand AST, including a legal no-object member ID.
            return expression
        coord = expression.node.coord
        if expression.lvalue:
            if not expression.discarded_known:
                raise CppDiagnostic("cpp.unsupported.nullptr-glvalue-evaluation",
                                    "nullptr_t glvalue lacks an owned no-fetch evaluation plan", coord)
            evaluation = expression.discarded_node
        else:
            evaluation = (expression.discarded_node if expression.discarded_known
                          else expression.node)
        target = c_ast.Typename(None, [], self._scalar_type(None, "nullptr", coord), coord)
        null = c_ast.Cast(target, c_ast.Constant("int", "0", coord), coord)
        node = null if evaluation is None else c_ast.ExprList([evaluation, null], coord)
        return Expression(node, "nullptr", null_pointer=True,
                          discarded_known=evaluation is None,
                          constant_fact=expression.constant_fact)

    def _discarded_value_node(self, expression):
        return self._nullptr_rvalue(expression).node

    def _convert(self, expression, dtype, coord, force=False, contextual=False):
        if isinstance(dtype, ReferenceType):
            from pcc.frontends.cpp.references import bind
            return bind(self, expression, dtype, coord)
        dtype = unqualified(dtype)
        self._check_extended_value_type(dtype, expression.node)
        if array_type(dtype):
            raise CppDiagnostic("cpp.invalid-array-conversion", "an array is not a scalar conversion destination", coord)
        expression = self._nullptr_rvalue(self._decay_array(expression))
        if is_class_type(dtype) or is_class_type(expression.dtype):
            raise CppDiagnostic("cpp.unsupported.class-conversions",
                                "class value conversion/copy/lifetime semantics are not implemented", coord)
        # Only an explicit conversion may discard an arithmetic result. Return
        # statements perform their own void check and preserve their expression.
        if dtype == "void" and force:
            self._require_value(expression, allow_void=True)
            if self.template_function is not None:
                return Pattern("cast", coord, (dtype, force, contextual), [expression])
            typename = c_ast.Typename(None, [], self._scalar_type(None, dtype, coord), coord)
            return c_ast.Cast(typename, expression.node, coord)
        self._require_value(expression)
        nullptr_bool = unqualified(expression.dtype) == "nullptr" and dtype == "bool" and (force or contextual)
        enum_explicit = (force and is_enum_type(expression.dtype)
                         and self.types.arithmetic_operand(dtype) and not is_enum_type(dtype))
        if (force and is_enum_type(dtype)
                and unqualified(expression.dtype) is not dtype):
            raise CppDiagnostic("cpp.unsupported.enum-casts",
                                "casts to a distinct enum require range/conversion semantics", coord)
        if self.template_function is not None:
            if not dependent(expression.dtype) and not dependent(dtype):
                if (self.types.conversion_rank(expression.dtype, dtype, expression.null_pointer) is None
                        and not nullptr_bool and not enum_explicit):
                    raise CppDiagnostic("cpp.invalid-conversion", "unsupported conversion", coord)
            if same_type(expression.dtype, dtype) and not force:
                return expression.node
            return Pattern("cast", coord, (dtype, force, contextual), [expression])
        if (self.types.conversion_rank(expression.dtype, dtype, expression.null_pointer) is None
                and not nullptr_bool and not enum_explicit):
            raise CppDiagnostic("cpp.invalid-conversion", "unsupported conversion", coord)
        if expression.dtype == dtype and not force:
            return expression.node
        typename = c_ast.Typename(None, [], self._scalar_type(None, dtype, coord), coord)
        return c_ast.Cast(typename, expression.node, coord)

    def _template(self):
        keyword = self._expect("template")
        if self.language_linkage == "C":
            raise CppDiagnostic("cpp.template-c-linkage",
                                "templates cannot have C language linkage", keyword.coord)
        self._expect("<")
        parent = self.scope
        self.scope = Scope(parent)
        self.parsing_template = True
        parameters = []
        try:
            if self._peek().value == ">":
                self._unsupported(keyword, "explicit specializations are not implemented")
            while True:
                if self._peek().value in ("class", "typename"):
                    self.cursor.take()
                    name = self._identifier()
                    parameter = TypeParameter(len(parameters), name.value, name.coord)
                    self.scope.declare(Symbol(name.value, "template-type", name.coord, dtype=parameter))
                else:
                    token = self._peek()
                    dtype = self._type(check_lowering=False)
                    if self._peek().value in ("*", "&", "&&", "(", "..."):
                        raise CppDiagnostic("cpp.unsupported.non-type-template-parameters",
                                            "pointer/reference/pack non-type template parameters are not implemented", token.coord)
                    name = self._identifier()
                    if self._peek().value in ("[", "("):
                        raise CppDiagnostic("cpp.unsupported.non-type-template-parameters",
                                            "array/function non-type template parameters are not implemented", token.coord)
                    dtype = unqualified(dtype)
                    if is_enum_type(dtype):
                        raise CppDiagnostic("cpp.unsupported.enum-templates",
                                            "enum non-type template parameters are not implemented", token.coord)
                    if not isinstance(dtype, TypeParameter) and not self.types.integral(dtype):
                        raise CppDiagnostic("cpp.unsupported.non-type-template-parameters",
                                            "only integral non-type template parameters are implemented", token.coord)
                    parameter = ValueParameter(len(parameters), name.value, dtype, name.coord)
                    symbol = Symbol(name.value, "template-value", name.coord, dtype=dtype)
                    symbol.template_parameter = parameter
                    self.scope.declare(symbol)
                parameters.append(parameter)
                if self._peek().value == "=":
                    raise CppDiagnostic("cpp.unsupported.template-default-arguments",
                                        "template defaults, including value-dependent defaults, are not implemented", self._peek().coord)
                if self._accept(">"):
                    break
                self._expect(",")
            if self._peek().value in ("class", "struct"):
                from pcc.frontends.cpp.class_templates import parse_declaration
                parse_declaration(self, tuple(parameters), parent)
            else:
                self._function(template_parameters=tuple(parameters), declaration_scope=parent)
        finally:
            self.scope = parent
            self.parsing_template = False

    def _parameter_clause(self, params=None, parameter_symbols=None, variadic=None, check_lowering=True, defaults=None):
        # Share the existing fixed-parameter grammar with sizeof type-id
        # parsing. A supplied list keeps the ordinary function's declaration
        # identities and parameter-scope point-of-declaration behavior.
        if params is None:
            params = []
        if parameter_symbols is None:
            parameter_symbols = []
        if self._peek().value != ")":
            while True:
                if self._peek().value == "...":
                    token = self.cursor.take()
                    if variadic is None or not params:
                        raise CppDiagnostic("cpp.unsupported.variadic-functions",
                                            "this variadic declaration context is not implemented", token.coord)
                    variadic.append(True)
                    if self._peek().value != ")":
                        raise CppDiagnostic("cpp.invalid-parameter", "ellipsis must end the parameter list", token.coord)
                    break
                dtype_token = self._peek()
                dtype = self._type(check_lowering=check_lowering)
                # Exactly one unnamed void parameter denotes an empty
                # parameter list, including a typedef naming void. A named
                # or additional void parameter is never silently removed.
                if dtype == "void" and self._peek().value == ")" and not params:
                    break
                param = None
                with self.cursor.tentative():
                    self._abstract_pointer_type(dtype)
                    unnamed = self._peek().value in (",", ")", "...", "=")
                if unnamed:
                    dtype = self._abstract_pointer_type(dtype)
                else:
                    param, dtype = self._declarator(dtype)
                if function_type(dtype):
                    dtype = PointerType(dtype)
                if array_type(dtype):
                    leaf = dtype.element
                    while array_type(leaf):
                        leaf = leaf.element
                    if pointer_type(leaf):
                        raise CppDiagnostic("cpp.unsupported.multilevel-pointers", "array-of-pointer parameter qualification is not implemented", dtype_token.coord)
                    dtype = PointerType(dtype.element)
                if unqualified(dtype) == "void":
                    raise CppDiagnostic("cpp.invalid-parameter",
                                        "void must be the sole unnamed parameter",
                                        dtype_token.coord)
                if param is not None:
                    # A preceding parameter can hide an outer type in a
                    # later parameter declaration, including prototypes.
                    parameter_symbols.append(self.scope.declare(
                        Symbol(param.value, "value", param.coord, dtype=dtype)))
                else:
                    parameter_symbols.append(None)
                params.append((param, dtype))
                record = None
                if self._peek().value == "=":
                    if defaults is None:
                        raise CppDiagnostic("cpp.unsupported.default-argument-context",
                                            "default arguments in this declarator context are not implemented", self._peek().coord)
                    from pcc.frontends.cpp.default_arguments import collect
                    record = collect(self, dtype)
                if defaults is not None:
                    defaults.append(record)
                if self._peek().value == ")":
                    break
                if self._peek().value != "...":
                    self._expect(",")
        return tuple(params)

    def _declaration_noexcept(self):
        token = self._expect("noexcept")
        if not self._accept("("):
            return True
        # The real expression parser retains lookup and typed constant facts;
        # no token deletion or evaluation in the host language is involved.
        expression = self._expression()
        self._expect(")")
        fact = expression.constant_fact
        if (fact is None or fact.status != KNOWN
                or not self.types.integral_operand(expression.dtype)):
            raise CppDiagnostic("cpp.unsupported.exceptions", "noexcept declaration requires an implemented integral constant expression", token.coord)
        return bool(fact.value)

    def _function(self, template_parameters=(), declaration_scope=None, implicit_extern=False,
                  member_definition=None):
        noreturn = False
        if self._peek().value == "[":
            attribute = self.cursor.take()
            self._expect("[")
            if self._peek().value != "noreturn":
                raise CppDiagnostic("cpp.unsupported.attributes", "only a declaration's noreturn attribute is implemented", attribute.coord)
            self.cursor.take()
            self._expect("]")
            self._expect("]")
            noreturn = True
        storage = None
        if self._peek().value in ("extern", "static"):
            storage = self.cursor.take()
            if self._peek().value in ("extern", "static"):
                raise CppDiagnostic("cpp.invalid-storage-specifier",
                                    "multiple storage class specifiers", self._peek().coord)
            if self._peek().kind == "STRING_LITERAL":
                raise CppDiagnostic("cpp.unsupported.language-linkage",
                                    "language linkage specifications are not implemented", storage.coord)
        storage_specifiers = [] if storage is None else [storage]
        # [class.access]/6: a member's access context includes its return
        # type, before the qualified declarator identifies the class. Resolve
        # names normally now, but check those exact declaration identities only
        # after the declarator has established its owning class. The temporary
        # lookup scope owns this obligation list; no parser-global relaxation.
        return_parent = self.scope
        return_lookup = Scope(return_parent)
        return_lookup.deferred_member_access = []
        self.scope = return_lookup
        try:
            result_type = self._type(check_lowering=False, storage_specifiers=storage_specifiers)
        finally:
            self.scope = return_parent
        storage = storage_specifiers[0] if storage_specifiers else None
        if implicit_extern and storage is not None:
            raise CppDiagnostic("cpp.linkage-storage",
                                "a directly contained linkage declaration cannot specify storage", storage.coord)
        params = []
        parameter_symbols = []
        parent = self.scope
        parameter_scope = Scope(parent)
        self.scope = parameter_scope
        member_owner = member_definition
        namespace_owner = None

        def declarator_name(name):
            nonlocal member_owner, namespace_owner
            if member_definition is not None:
                if name.global_scope or len(name.parts) != 1:
                    raise CppDiagnostic("cpp.invalid-member-declaration", "in-class member names must be unqualified", name.coord)
                return
            if len(name.parts) < 2 and not name.global_scope:
                return
            from pcc.frontends.cpp.declarators import QualifiedName
            if len(name.parts) == 1:
                nominated = parent.global_scope()
                target = None
            else:
                qualifier = QualifiedName(name.tokens[:-1], name.global_scope, name.coord)
                target = parent.resolve_name(qualifier, qualifier_only=True)
                nominated = (target.namespace_scope if target is not None
                             and target.kind in ("namespace", "namespace-alias") else None)
            if nominated is not None:
                # This increment admits ordinary function declarators only;
                # namespace objects and grouped qualified declarators retain
                # the existing explicit boundary in _apply_declarator.
                if self._peek().value != "(":
                    return
                from pcc.frontends.cpp.namespace_functions import (
                    declaration_scope as namespace_declaration_scope,
                )
                if template_parameters or self.parsing_template:
                    raise CppDiagnostic("cpp.unsupported.namespace-templates",
                                        "qualified function templates require separate template declaration matching", name.coord)
                namespace_owner = namespace_declaration_scope(nominated, parent, name.parts[-1], name.coord)
                parameter_scope.parent = namespace_owner
                return
            if target is None or target.kind != "type" or not is_class_type(target.dtype):
                return
            owner = unqualified(target.dtype)
            require_complete(owner, name.coord)
            enclosing = owner.declaration_scope
            while enclosing is not None and enclosing is not parent:
                enclosing = enclosing.parent
            if enclosing is None:
                raise CppDiagnostic("cpp.member-definition-namespace", "member definition is not in an enclosing namespace", name.coord)
            member_owner = owner.definition
            parameter_scope.parent = member_owner.scope

        variadic = []
        default_arguments = []
        def parameter_clause():
            return self._parameter_clause(params, parameter_symbols, variadic,
                                          check_lowering=False, defaults=default_arguments)

        from pcc.frontends.cpp.allocation import declaration_name, validate_declaration
        try:
            declarator = DeclaratorParser(self.cursor, lambda: declaration_name(self), parameter_clause, self._array_bound,
                                          on_name=declarator_name,
                                          parse_noexcept=self._declaration_noexcept).parse()
            is_function = any(chunk.kind == "function" for chunk in declarator.chunks)
            name, result_type = self._apply_declarator(declarator, result_type, function=is_function,
                                                       member=member_owner is not None and is_function,
                                                       namespace_function=namespace_owner is not None and is_function)
        finally:
            self.scope = parent
        function_scope = namespace_owner if namespace_owner is not None else parent
        access_scope = member_owner.scope if member_owner is not None else parent
        for accessed, access_coord in return_lookup.deferred_member_access:
            access_scope.check_member_access(accessed, access_coord)
        if name.value == "operator=" and any(default_arguments):
            raise CppDiagnostic("cpp.invalid-assignment-operator", "assignment operator parameters cannot have defaults", name.coord)
        if any(default_arguments) and (not is_function or template_parameters or variadic
                or any(contains_function(dtype) for _, dtype in params)):
            raise CppDiagnostic("cpp.unsupported.default-argument-context",
                                "defaults require an ordinary non-variadic function with scalar/object-pointer parameters", name.coord)
        if isinstance(result_type, ReferenceType):
            raise CppDiagnostic("cpp.unsupported.references", "reference results and namespace reference objects require lifetime/return semantics", name.coord)
        if not is_function:
            if name.kind == "OPERATOR_FUNCTION_ID":
                raise CppDiagnostic("cpp.invalid-allocation-function", "operator name must declare a function", name.coord)
            self._check_value_type(result_type, name)
            if noreturn:
                raise CppDiagnostic("cpp.invalid-noreturn", "noreturn applies to a function declaration", name.coord)
            if member_owner is not None:
                raise CppDiagnostic("cpp.unsupported.static-data-members",
                                    "static data members require separate storage and ODR semantics", name.coord)
            if template_parameters:
                raise CppDiagnostic("cpp.unsupported.variable-templates",
                                    "variable templates require instantiation and ODR semantics", name.coord)
            return parse_namespace_object(self, name, result_type, storage, implicit_extern)
        if array_type(result_type) or function_type(result_type):
            raise CppDiagnostic("cpp.invalid-function-return", "a function cannot return an array or function", name.coord)
        if member_owner is None and storage is not None and (storage.value == "static" or template_parameters):
            raise CppDiagnostic("cpp.unsupported.function-storage",
                                "function storage specifiers require function linkage semantics", storage.coord)
        if self.scope.lookup_lexical(name.value) is not None and self.scope.lookup_lexical(name.value).kind in ("template-type", "template-value"):
            raise CppDiagnostic("cpp.template-parameter-shadowing",
                                "function name shadows a template parameter", name.coord)
        declaration_only = (self._peek().value == ";" and member_owner is None and not template_parameters)
        for signature_type in (result_type,) + tuple(dtype for param, dtype in params):
            plain = unqualified(signature_type)
            if is_class_type(plain) and not (
                    declaration_only and record_declaration_projection_supported(plain, self.types)):
                raise CppDiagnostic("cpp.unsupported.class-value-abi",
                                    "class by-value function ABI/lifetime is not implemented", name.coord)
            pointee = plain
            while pointer_type(pointee) or array_type(pointee) or isinstance(pointee, ReferenceType):
                pointee = unqualified(pointee.referent if isinstance(pointee, ReferenceType)
                                      else pointee.pointee if pointer_type(pointee) else pointee.element)
            if isinstance(pointee, ClassType) and pointee.internal_linkage:
                raise CppDiagnostic("cpp.unsupported.class-linkage",
                                    "function signatures involving internal-linkage class types are not implemented", name.coord)
            if is_enum_type(pointee) and pointee.internal_linkage:
                raise CppDiagnostic("cpp.unsupported.enum-linkage",
                                    "function signatures involving internal-linkage enum types are not implemented", name.coord)
        if (member_owner is None and name.value == "main" and self.language_linkage is not None
                and (not function_scope.namespace_path() or self.language_linkage == "C")):
            raise CppDiagnostic("cpp.main-language-linkage",
                                "main cannot have a language linkage specification", name.coord)
        definition = self._peek().value == "{"
        if variadic and any(isinstance(t, ReferenceType) for _, t in params):
            raise CppDiagnostic("cpp.unsupported.references", "variadic reference signatures require dedicated va_start/ABI admission", name.coord)
        if template_parameters and any(isinstance(t, ReferenceType) for _, t in params):
            raise CppDiagnostic("cpp.unsupported.references", "reference template parameter deduction is not implemented", name.coord)
        if any(isinstance(t, ReferenceType) and is_class_type(t.referent) for _, t in params) and definition:
            raise CppDiagnostic("cpp.unsupported.class-reference-values", "class-reference function bodies require class value/lifetime semantics", name.coord)
        if definition or member_owner is not None or template_parameters:
            for dtype in (result_type,) + tuple(t for _, t in params):
                self._check_value_type(dtype, name)
        exception_specification = declarator.chunks[0].exception_specification
        validate_declaration(self, name, result_type, tuple(t for _, t in params),
                             bool(exception_specification), definition, member_owner, storage, template_parameters, bool(variadic))
        if (exception_specification is not None or noreturn) and (definition or member_owner is not None or template_parameters):
            raise CppDiagnostic("cpp.unsupported.exceptions", "function contract definitions/members/templates require additional semantics", name.coord)
        if (definition or member_owner is not None or template_parameters) and any(
                contains_function(t) for t in (result_type,) + tuple(t for _, t in params)):
            raise CppDiagnostic("cpp.unsupported.function-pointers",
                                "callback definitions, members and templates require function-value semantics", name.coord)
        if variadic and (member_owner is not None or template_parameters):
            raise CppDiagnostic("cpp.unsupported.variadic-functions",
                                "variadic members/templates require additional ABI/lifetime semantics", name.coord)
        if variadic and definition:
            from pcc.frontends.cpp.varargs import require_sysv_target
            require_sysv_target(self, name.coord)
        if member_owner is not None:
            if template_parameters or self.parsing_template:
                raise CppDiagnostic("cpp.unsupported.member-templates", "member templates require separate deduction/ODR semantics", name.coord)
            if storage is not None and (member_definition is None or storage.value != "static"):
                raise CppDiagnostic("cpp.invalid-member-storage",
                                    "only an in-class declaration may specify static for a member function", storage.coord)
            if member_definition is not None and definition and member_owner.dtype.internal_linkage:
                raise CppDiagnostic("cpp.unsupported.class-linkage",
                                    "in-class methods of internal-linkage classes require distinct definition ownership", name.coord)
            if member_definition is None and not definition:
                raise CppDiagnostic("cpp.member-definition-required", "an out-of-class member declaration must be a definition", name.coord)
        if member_owner is None and name.value == "main" and not function_scope.namespace_path() and params:
            self._unsupported(name, "main parameters are not implemented")
        if member_owner is None and name.value == "main" and not function_scope.namespace_path() and result_type != "int":
            raise CppDiagnostic("cpp.main-return-type", "main must return int", name.coord)
        if member_owner is None and name.value == "main" and not function_scope.namespace_path() and template_parameters:
            raise CppDiagnostic("cpp.main-template", "main cannot be a template", name.coord)
        new_symbol = Symbol(name.value, "function", name.coord, arity=len(params), dtype=result_type,
                            parameters=tuple(unqualified(dtype) for param, dtype in params))
        new_symbol.variadic = bool(variadic)
        new_symbol.nonthrowing = bool(exception_specification)
        new_symbol.noreturn = noreturn
        new_symbol.template_parameters = template_parameters
        new_symbol.requested_language_linkage = self.language_linkage
        if member_owner is None and self.language_linkage == "C":
            # Pointers retain their complete semantic pointee type here. The
            # declarator/type owners have already rejected unsupported function
            # pointers, references and object types. C linkage changes the
            # function's name/identity, not pointee cv/nominal identity or layout.
            # Incomplete object pointees and void are valid: this admission
            # neither completes them nor promises cross-language layout.
            if any(not self.types.integral(dtype) and unqualified(dtype) not in ("void", "float", "double", "long double")
                   and not pointer_type(dtype) and not isinstance(dtype, ReferenceType) and not is_class_type(dtype)
                   for dtype in (result_type,) + new_symbol.parameters):
                raise CppDiagnostic("cpp.unsupported.c-linkage-signature",
                                    "C-linkage signatures require declarable fundamental scalars or pointers", name.coord)
            if function_scope.internal_linkage:
                raise CppDiagnostic("cpp.unsupported.c-linkage-internal",
                                    "C function type with internal name linkage is not implemented", name.coord)
        registry = function_scope if declaration_scope is None else declaration_scope
        if member_owner is not None:
            from pcc.frontends.cpp.members import declare_member_function
            const = "const" in declarator.chunks[0].qualifiers
            symbol = declare_member_function(member_owner, new_symbol, const,
                                             outside=member_definition is None,
                                             static=storage is not None and storage.value == "static")
        elif namespace_owner is not None:
            from pcc.frontends.cpp.namespace_functions import (
                declare_function as declare_namespace_function,
            )
            symbol = declare_namespace_function(namespace_owner, new_symbol, definition)
        else:
            symbol = registry.declare_function(new_symbol, definition)
        if definition and (symbol.nonthrowing or symbol.noreturn):
            raise CppDiagnostic("cpp.unsupported.exceptions", "definitions of inherited function contracts require runtime enforcement", name.coord)
        if template_parameters:
            # Sema may inspect viable signatures without registering work.
            # Only the selected call requests an entry in this shared owner.
            symbol.instances = self.instance_registry.value_view(symbol)
        self.function_projection_names.add(symbol.c_name)
        for parameter in parameter_symbols:
            if parameter is not None:
                parameter.c_name = self._local_projection_name(parameter.name)
        from pcc.frontends.cpp.default_arguments import merge
        merge(self, symbol, default_arguments, member_owner.scope if member_owner is not None else registry)
        if template_parameters:
            if not definition:
                self._expect(";")
                return None
            for param, dtype in params:
                if param is None:
                    self._unsupported(name, "unnamed definition parameters")
            previous_template, previous_return = self.template_function, self.return_type
            self.template_function, self.return_type = symbol, result_type
            self.scope = parameter_scope
            try:
                symbol.parameter_symbols = parameter_symbols
                symbol.pattern = self._block(new_scope=False)
            finally:
                self.template_function, self.return_type = previous_template, previous_return
                self.scope = parent
            return None
        # An unnamed definition parameter still occupies its ABI position,
        # but introduces no C++ binding. Only the C projection needs a private
        # name for its argument slot; never install it in the semantic scope.
        param_decls = [self._decl(parameter_symbols[index].c_name if p else
                                 ("__pcc_cpp_unnamed_" + str(index) if definition else None),
                                 dtype, p.coord if p else name.coord)
                       for index, (p, dtype) in enumerate(params)]
        if variadic:
            param_decls.append(c_ast.EllipsisParam(name.coord))
        this_symbol = None
        if member_owner is not None and not getattr(symbol, "method_static", False):
            from pcc.frontends.cpp.members import object_pointer_type
            this_symbol = Symbol("this", "value", name.coord,
                                 c_name=self._local_projection_name("this"),
                                 dtype=object_pointer_type(symbol))
            param_decls.insert(0, self._decl(this_symbol.c_name, this_symbol.dtype, name.coord))
        declaration = self._decl(symbol.c_name, result_type, name.coord)
        if symbol.noreturn:
            declaration.funcspec = ["_Noreturn"]
        declaration.type = c_ast.FuncDecl(c_ast.ParamList(param_decls, name.coord),
                                          declaration.type, name.coord)
        if symbol.internal_linkage:
            declaration.storage = ["static"]
        if not definition:
            self._expect(";")
            return declaration
        if member_definition is not None:
            from pcc.frontends.cpp.inline_methods import DeferredMethod
            from pcc.frontends.cpp.inline_constructors import collect_body
            symbol.definition_linkage = "external_odr"
            member_definition.inline_methods.append(DeferredMethod(
                symbol, result_type, parameter_scope, parameter_symbols, declaration,
                this_symbol, collect_body(self), name.coord,
            ))
            return declaration
        return self._bind_function_body(symbol, result_type, parameter_scope, parameter_symbols,
                                        declaration, this_symbol, member_owner, name.coord)

    def _bind_function_body(self, symbol, result_type, parameter_scope, parameter_symbols,
                            declaration, this_symbol, member_owner, coord):
        """One body-binding path for ordinary and delayed in-class definitions."""
        parent = self.scope
        self.scope = parameter_scope
        previous_return_type = self.return_type
        previous_member, previous_this = self.member_function, self.this_symbol
        self.member_function, self.this_symbol = (symbol if member_owner is not None else None), this_symbol
        self.return_type = result_type
        previous_function = self.current_function
        self.current_function = symbol
        symbol.parameter_symbols = parameter_symbols
        try:
            body = self._block(new_scope=False)
        finally:
            self.scope = parent
            self.return_type = previous_return_type
            self.current_function = previous_function
            self.member_function, self.this_symbol = previous_member, previous_this
        if (member_owner is None and symbol.name == "main"
                and symbol.owner is self.scope.global_scope()):
            # Reaching the end of main is a C++ return of zero, not C's general
            # fallthrough behavior. Appending after a return is harmless AST.
            body.block_items.append(c_ast.Return(c_ast.Constant("int", "0", coord), coord))
        return c_ast.FuncDef(declaration, None, body, coord)

    def _block(self, new_scope=True):
        start = self._expect("{")
        parent = self.scope
        if new_scope:
            self.scope = Scope(parent)
        items = []
        self.lifetimes.enter()
        try:
            while not self._accept("}"):
                if self._peek().kind == "EOF":
                    self._expect("}")
                item = self._statement()
                if isinstance(item, list):
                    items.extend(item)
                elif item is not None:
                    items.append(item)
            items.extend(self.lifetimes.current_cleanup(start.coord))
        finally:
            self.lifetimes.leave()
            self.scope = parent
        if self.template_function is not None:
            return Pattern("block", start.coord, children=items)
        return c_ast.Compound(items, start.coord)

    def _statement(self, for_init=False):
        token = self._peek()
        if (token.value == "extern" and
                self.cursor.tokens[self.cursor.position + 1].kind == "STRING_LITERAL"):
            raise CppDiagnostic("cpp.linkage-scope",
                                "language linkage specifications are not block declarations", token.coord)
        if token.value == "enum":
            raise CppDiagnostic("cpp.unsupported.enum-local-scope",
                                "local enum identity and ABI naming are not implemented", token.coord)
        if token.value == "namespace" or token.value == "inline":
            self._namespace()
            return None
        if token.value == "{":
            return self._block()
        if self._accept(";"):
            if self.template_function is not None:
                return Pattern("empty", token.coord)
            return c_ast.EmptyStatement(token.coord)
        if token.value in ("using", "typedef"):
            self._alias()
            return None
        if token.value == "switch":
            from pcc.frontends.cpp.switches import parse_switch
            return parse_switch(self)
        if token.value in ("case", "default"):
            from pcc.frontends.cpp.switches import parse_case
            return parse_case(self)
        if token.value == "if":
            self.cursor.take()
            condition = self._condition()
            consequent = self._substatement()
            alternative = self._substatement() if self._accept("else") else None
            if self.template_function is not None:
                children = [condition, consequent]
                if alternative is not None:
                    children.append(alternative)
                return Pattern("if", token.coord, children=children)
            return c_ast.If(condition.node, consequent, alternative, token.coord)
        if token.value == "for":
            return self._for_statement()
        if token.value == "do":
            return self._do_while_statement()
        if token.value == "while":
            self.cursor.take()
            condition = self._condition()
            self.loop_depth += 1
            self.lifetimes.push_control("loop")
            try:
                body = self._substatement()
            finally:
                self.lifetimes.pop_control()
                self.loop_depth -= 1
            if self.template_function is not None:
                return Pattern("while", token.coord, children=[condition, body])
            return c_ast.While(condition.node, body, token.coord)
        if token.value in ("break", "continue"):
            self.cursor.take()
            if not self.loop_depth and not (token.value == "break" and self.switch_contexts):
                raise CppDiagnostic("cpp.jump-outside-loop",
                                    token.value + " is not inside an implemented loop", token.coord)
            self._expect(";")
            if self.template_function is not None:
                return Pattern(token.value, token.coord)
            jump = c_ast.Break(token.coord) if token.value == "break" else c_ast.Continue(token.coord)
            return self.lifetimes.jump(token.value, jump, token.coord)
        if self._accept("return"):
            expression = None if self._peek().value == ";" else self._expression()
            self._expect(";")
            return self._return_statement(expression, token.coord)
        if self._is_declaration(allow_range=for_init):
            dtype = self._type()
            name, dtype = self._declarator(dtype, allow_class_element=True)
            if isinstance(dtype, ReferenceType):
                raise CppDiagnostic("cpp.unsupported.references", "local reference initialization/lifetime is not implemented", name.coord)
            self._check_object_type(dtype, name.coord)
            # The point of declaration precedes the initializer.
            symbol = self.scope.declare(Symbol(
                name.value, "value", name.coord, dtype=dtype,
                c_name=self._local_projection_name(name.value),
            ))
            init = None
            initializer = None
            direct_initializer = False
            if array_type(dtype):
                leaf = dtype
                while array_type(leaf):
                    leaf = leaf.element
                if is_class_type(leaf) and self._peek().value in ("=", "{", "("):
                    raise CppDiagnostic("cpp.unsupported.class-array-initialization",
                                        "record arrays currently require trivial default initialization", name.coord)
                equals = self._accept("=")
                if self._peek().value == "{":
                    initializer_list = parse_initializer_list(self)
                    init = lower_array_initializer(dtype, initializer_list, self.types, self._convert)
                elif equals is not None or self._peek().value == "(":
                    if self._peek().kind == "STRING_LITERAL":
                        raise CppDiagnostic("cpp.unsupported.string-array-initialization", "string encoding and terminator rules are not implemented", self._peek().coord)
                    raise CppDiagnostic("cpp.invalid-array-initializer", "C++17 arrays require braced or string initialization", self._peek().coord)
                elif const_array_elements(dtype):
                    raise CppDiagnostic("cpp.const-initializer", "const array elements require initialization", name.coord)
                self._expect(";")
                symbol.initialization_complete = True
                symbol.jump_entry = "uninitialized" if init is None else "initialized"
                return self._decl(symbol.c_name, dtype, name.coord, init)
            if is_class_type(dtype):
                if self.template_function is not None:
                    raise CppDiagnostic("cpp.unsupported.class-templates", "class objects in templates are not implemented", name.coord)
                if unqualified(dtype).definition.constructors.overloads:
                    from pcc.frontends.cpp.constructors import construct_local
                    statements = construct_local(self, symbol)
                    self.lifetimes.register(self, symbol)
                    return statements
                from pcc.frontends.cpp.default_member_initializers import has_initializers, initialize_local, unsupported
                if has_initializers(unqualified(dtype).definition):
                    if self._peek().value != ";":
                        unsupported(self._peek().coord)
                    statements = initialize_local(self, symbol)
                    self.lifetimes.register(self, symbol)
                    return statements
                equals = self._accept("=")
                aggregate_initialization = False
                if self._peek().value == "{":
                    initializer_list = parse_initializer_list(self)
                    init = lower_aggregate_initializer(
                        dtype, initializer_list, self.types, self._convert, scope=self.scope,
                    )
                    aggregate_initialization = unqualified(dtype).definition.aggregate
                elif equals is not None or self._peek().value == "(":
                    coord = equals.coord if equals is not None else self._peek().coord
                    raise CppDiagnostic("cpp.unsupported.class-initialization", "class copy/direct initialization is not implemented", coord)
                else:
                    init = default_initialize(dtype, name.coord)
                self._expect(";")
                symbol.jump_entry = "class-default" if init is None else "initialized"
                declaration = self._decl(symbol.c_name, dtype, name.coord, init)
                self.lifetimes.register(self, symbol, init, aggregate_initialization=aggregate_initialization)
                return declaration
            if self._accept("="):
                if is_enum_type(dtype) and self._peek().value == "{":
                    raise CppDiagnostic("cpp.unsupported.enum-list-initialization",
                                        "enum copy-list initialization is not implemented", self._peek().coord)
                expression = self._expression()
                self._require_value(expression)
                init = self._convert(expression, dtype, name.coord)
                initializer = expression
            elif self._accept("("):
                if self._peek().value == ")" or self._is_type(self._peek()):
                    self._unsupported(self._peek(), "block function declarations")
                expression = self._expression()
                self._require_value(expression)
                init = self._convert(expression, dtype, name.coord, contextual=True)
                initializer = expression
                direct_initializer = True
                self._expect(")")
            elif is_enum_type(dtype) and self._peek().value == "{":
                raise CppDiagnostic("cpp.unsupported.enum-list-initialization",
                                    "enum direct-list initialization requires context-sensitive rules", self._peek().coord)
            if isinstance(dtype, ConstType) and initializer is None:
                raise CppDiagnostic("cpp.const-initializer", "const objects require an initializer", name.coord)
            if for_init and self._peek().value == ":":
                raise CppDiagnostic("cpp.unsupported.range-for",
                                    "range-for requires range binding and lifetime semantics", self._peek().coord)
            if for_init and self._peek().value == ",":
                raise CppDiagnostic("cpp.unsupported.for-declaration-list",
                                    "multiple init-declarators are not implemented", self._peek().coord)
            self._expect(";")
            symbol.jump_entry = "uninitialized" if initializer is None else "initialized"
            if self.template_function is not None:
                return Pattern("decl", name.coord, (symbol, direct_initializer),
                               [] if initializer is None else [initializer])
            if (isinstance(dtype, ConstType) and (self.types.integral(dtype) or is_enum_type(dtype))
                    and initializer is not None):
                symbol.constant_fact = converted_fact(
                    self._constant_fact(initializer), dtype, self.types, name.coord,
                )
            symbol.initialization_complete = True
            return self._decl(symbol.c_name, dtype, name.coord, init)
        expression = self._expression()
        self._require_value(expression, allow_void=True)
        self._expect(";")
        return self._discarded_value_node(expression)

    def _discarded_expression_sequence(self):
        expressions = [self._expression()]
        self._require_value(expressions[0], allow_void=True)
        while self._accept(","):
            expression = self._expression()
            self._require_value(expression, allow_void=True)
            expressions.append(expression)
        coord = expressions[0].node.coord
        if self.template_function is not None:
            return Pattern("discarded-sequence", coord, children=expressions)
        return c_ast.ExprList([self._discarded_value_node(expression) for expression in expressions], coord)

    def _for_statement(self):
        start = self._expect("for")
        self._expect("(")
        parent = self.scope
        self.scope = Scope(parent)
        self.lifetimes.enter()
        try:
            init = None
            if self._accept(";"):
                pass
            elif self._is_declaration(allow_range=True):
                init = self._statement(for_init=True)
            elif self._peek().value == "typedef":
                self._alias()
            else:
                init = self._discarded_expression_sequence()
                self._expect(";")
            condition = None
            if not self._accept(";"):
                if self._is_declaration():
                    raise CppDiagnostic("cpp.unsupported.condition-declaration",
                                        "for condition declarations require per-iteration lifetime", self._peek().coord)
                condition = self._expression()
                coord = condition.node.coord
                condition = Expression(self._convert(condition, "bool", coord, contextual=True), "bool")
                self._expect(";")
            step = None
            if not self._accept(")"):
                step = self._discarded_expression_sequence()
                self._expect(")")
            self.loop_depth += 1
            self.lifetimes.push_control("loop")
            try:
                body = self._substatement(for_scope=self.scope)
            finally:
                self.lifetimes.pop_control()
                self.loop_depth -= 1
            cleanup = self.lifetimes.current_cleanup(start.coord)
        finally:
            self.lifetimes.leave()
            self.scope = parent
        if self.template_function is not None:
            return Pattern("for", start.coord, children=[init, condition, step, body])
        # C's For owner scopes only its body, not its init-declaration. A real
        # compound gives the header its own lowering scope without changing C.
        before = init if isinstance(init, list) else []
        loop_init = None if isinstance(init, list) else init
        return c_ast.Compound(before + [c_ast.For(loop_init, None if condition is None else condition.node,
                                                  step, body, start.coord)] + cleanup, start.coord)

    def _do_while_statement(self):
        start = self._expect("do")
        self.loop_depth += 1
        self.lifetimes.push_control("loop")
        try:
            body = self._substatement()
        finally:
            self.lifetimes.pop_control()
            self.loop_depth -= 1
        self._expect("while")
        # Unlike while/for, this grammar requires an expression, not a
        # condition-declaration. The body's scope has already ended here.
        condition = self._condition(allow_declaration=False)
        self._expect(";")
        if self.template_function is not None:
            return Pattern("do-while", start.coord, children=[condition, body])
        return c_ast.DoWhile(condition.node, body, start.coord)

    def _condition(self, allow_declaration=True):
        self._expect("(")
        if self._is_declaration():
            if not allow_declaration:
                raise CppDiagnostic("cpp.invalid-do-condition",
                                    "do-while requires an expression, not a declaration", self._peek().coord)
            raise CppDiagnostic("cpp.unsupported.condition-declaration",
                                "condition declarations and init-statements are not implemented",
                                self._peek().coord)
        expression = self._expression()
        self._expect(")")
        coord = expression.node.coord
        return Expression(self._convert(expression, "bool", coord, contextual=True), "bool",
                          constant_fact=converted_fact(self._constant_fact(expression),
                                                       "bool", self.types, coord))

    def _constant_fact(self, expression):
        if expression.constant_fact is not None:
            return expression.constant_fact
        return unmodeled(unqualified(expression.dtype),
                         "constant semantics for this expression are not implemented",
                         expression.node.coord)

    def _symbol_constant_fact(self, symbol, coord):
        # Snapshot the already-resolved declaration now. In particular, a read
        # during its own initializer must not acquire a proof retroactively.
        if (self.template_function is not None
                or not (self.types.integral(symbol.dtype) or is_enum_type(symbol.dtype))):
            return unmodeled(unqualified(symbol.dtype), "dependent or non-integral object", coord)
        if not isinstance(symbol.dtype, ConstType):
            return not_constant(unqualified(symbol.dtype), "read of a non-const integral object", coord)
        if not symbol.initialization_complete:
            return not_constant(unqualified(symbol.dtype), "const object has no preceding completed initialization", coord)
        if symbol.constant_fact is None:
            return unmodeled(unqualified(symbol.dtype), "const initialization proof is unavailable", coord)
        return symbol.constant_fact

    def _substatement(self, for_scope=None):
        # C++ selection/iteration substatements have block scope even without
        # braces. In particular an unbraced declaration must not leak its name.
        parent = self.scope
        self.scope = Scope(parent)
        if for_scope is not None:
            self.scope.forbidden_redeclarations = frozenset(for_scope.names)
        coord = self._peek().coord
        self.lifetimes.enter()
        try:
            statement = (self._block(new_scope=False)
                         if for_scope is not None and self._peek().value == "{"
                         else self._statement())
            cleanup = self.lifetimes.current_cleanup(coord)
        finally:
            self.lifetimes.leave()
            self.scope = parent
        if statement is None:
            return Pattern("empty", coord) if self.template_function is not None else c_ast.EmptyStatement(coord)
        if isinstance(statement, list):
            return c_ast.Compound(statement + cleanup, coord)
        if cleanup:
            return c_ast.Compound([statement] + cleanup, coord)
        return statement

    def _check_object_type(self, dtype, coord):
        if contains_function(dtype):
            raise CppDiagnostic("cpp.unsupported.function-pointers", "callback object/function-alias declaration semantics are not implemented", coord)
        element = dtype
        while array_type(element):
            element = element.element
        if array_type(dtype) and is_class_type(element):
            from pcc.frontends.cpp.arrays import require_trivial_record_array
            require_trivial_record_array(dtype, coord)
        require_complete_array(dtype, coord)
        require_complete(dtype, coord)
        if is_enum_type(dtype) and not unqualified(dtype).complete:
            raise CppDiagnostic("cpp.incomplete-enum", "an object requires a complete enum type", coord)
        if unqualified(dtype) == "void":
            raise CppDiagnostic("cpp.invalid-object-type", "an object cannot have type void", coord)

    def _return_statement(self, expression, coord):
        if getattr(self.member_function, "destructor", False) and expression is not None:
            raise CppDiagnostic("cpp.destructor-return-value", "destructors cannot return an expression", coord)
        if getattr(self.member_function, "constructor", False) and expression is not None:
            raise CppDiagnostic("cpp.constructor-return-value", "constructors cannot return an expression", coord)
        result_type = unqualified(self.return_type)
        if expression is None:
            if not dependent(result_type) and result_type != "void":
                raise CppDiagnostic("cpp.return-value-required",
                                    "non-void function requires a return expression", coord)
        else:
            self._require_value(expression, allow_void=True)
            if not dependent(result_type) and not dependent(expression.dtype):
                if result_type == "void" and unqualified(expression.dtype) != "void":
                    raise CppDiagnostic("cpp.return-value-in-void",
                                        "void function cannot return a non-void expression", coord)
                if result_type != "void" and unqualified(expression.dtype) == "void":
                    raise CppDiagnostic("cpp.return-void-expression",
                                        "non-void function cannot return a void expression", coord)
        if self.template_function is not None:
            if expression is not None and expression.dtype != "void" and self.return_type != "void":
                self._convert(expression, self.return_type, coord)
            return Pattern("return", coord, children=[] if expression is None else [expression])
        if expression is None:
            return self.lifetimes.return_statement(self, None, coord)
        if result_type == "void":
            # Existing C lowering evaluates node.expr before ret void. Keep the
            # checked void expression intact so calls and other effects survive.
            return self.lifetimes.return_statement(self, expression.node, coord)
        return self.lifetimes.return_statement(self, self._convert(expression, self.return_type, coord), coord)

    def _require_value(self, expression, allow_void=False, allow_class=False, object_address=False):
        if not object_address:
            self._check_extended_value_type(expression.dtype, expression.node)
        if contains_function(expression.dtype):
            raise CppDiagnostic("cpp.unsupported.function-pointers", "callback value/call semantics are not implemented", expression.node.coord)
        if is_class_type(expression.dtype) and not allow_class:
            raise CppDiagnostic("cpp.unsupported.class-values",
                                "class value/materialization semantics are not implemented", expression.node.coord)
        if unqualified(expression.dtype) == "void" and not allow_void:
            raise CppDiagnostic("cpp.void-value", "void expression has no value", expression.node.coord)
        if expression.symbol is not None and expression.symbol.kind == "function":
            self._unsupported(self._peek(), "function-to-pointer conversion")

    def _expression(self):
        lhs = self._binary(1)
        if self._peek().value in ("+=", "-=", "*=", "/=", "%=", "<<=", ">>=",
                                  "&=", "^=", "|="):
            assignment = self.cursor.take()
            rhs = self._expression()
            return self._compound_assignment_expression(assignment.value[:-1], lhs, rhs, assignment.coord)
        assignment = self._accept("=")
        if assignment:
            rhs = self._expression()
            return self._assignment_expression(lhs, rhs, assignment.coord)
        return lhs

    def _compound_assignment_expression(self, operator, lhs, rhs, coord):
        if array_type(lhs.dtype):
            raise CppDiagnostic("cpp.array-not-assignable", "builtin array assignment is not permitted", coord)
        if is_class_type(lhs.dtype) or is_class_type(rhs.dtype):
            raise CppDiagnostic("cpp.unsupported.class-assignment", "class compound assignment requires operator lookup", coord)
        if lhs.assignment_result:
            raise CppDiagnostic("cpp.unsupported.syntax", "simple-assignment result lvalue lowering", coord)
        if not lhs.lvalue:
            raise CppDiagnostic("cpp.not-lvalue", "compound assignment requires an lvalue", coord)
        if isinstance(lhs.dtype, ConstType):
            raise CppDiagnostic("cpp.const-assignment", "compound assignment requires a modifiable lvalue", coord)
        self._require_value(rhs)
        rhs = self._decay_array(rhs)
        dtype = unqualified(lhs.dtype)
        pointer = pointer_type(dtype)
        if pointer:
            if operator not in ("+", "-") or not self.types.integral_operand(rhs.dtype):
                raise CppDiagnostic("cpp.invalid-operands", "pointer compound assignment requires += or -= and an integral offset", coord)
            if unqualified(dtype.pointee) == "void":
                raise CppDiagnostic("cpp.invalid-operands", "pointer arithmetic requires a complete object type", coord)
            require_complete(dtype.pointee, coord)
            require_complete_array(dtype.pointee, coord)
            if is_enum_type(dtype.pointee) and not unqualified(dtype.pointee).complete:
                raise CppDiagnostic("cpp.incomplete-enum", "pointer arithmetic requires a complete enum", coord)
        if self.template_function is not None:
            # Type checking at definition when possible, then instance-local
            # replay. Do not lower dependent operations into mixed C/Pattern IR.
            result = self._binary_expression(operator, lhs, rhs, coord)
            self._convert(result, dtype, coord)
            return Expression(Pattern("compound-assignment", coord, operator, [lhs, rhs]), dtype, True)
        # [expr.ass] sequences RHS before LHS and evaluates LHS only once.
        # Retain that order independently of C's assignment operand policy.
        # The final address (not a copied value) preserves C++'s lvalue result.
        rhs_name = self._fresh_projection_name("__pcc_cpp_compound_rhs_")
        ptr_name = self._fresh_projection_name("__pcc_cpp_compound_ptr_")
        rhs_type = self.types.promote(rhs.dtype) if pointer else unqualified(rhs.dtype)
        rhs_decl = self._decl(rhs_name, rhs_type, coord, self._convert(rhs, rhs_type, coord))
        address = (lhs.node.expr if isinstance(lhs.node, c_ast.UnaryOp) and lhs.node.op == "*"
                   else c_ast.UnaryOp("&", lhs.node, coord))
        ptr_decl = self._decl(ptr_name, PointerType(dtype), coord, address)
        pointer_node = c_ast.ID(ptr_name, coord)
        object_node = c_ast.UnaryOp("*", pointer_node, coord)
        left_value = Expression(object_node, dtype, True)
        right_value = Expression(c_ast.ID(rhs_name, coord), rhs_type)
        if pointer:
            result = Expression(c_ast.BinaryOp(operator, object_node, right_value.node, coord), dtype)
        else:
            result = self._binary_expression(operator, left_value, right_value, coord)
        update = c_ast.Assignment("=", object_node, self._convert(result, dtype, coord), coord)
        body = c_ast.StmtExpr(c_ast.Compound([rhs_decl, ptr_decl, update, pointer_node], coord), coord)
        return Expression(c_ast.UnaryOp("*", body, coord), dtype, True,
                          constant_fact=not_constant(dtype, "modification of a runtime object", coord))

    def _assignment_expression(self, lhs, rhs, coord):
        if array_type(lhs.dtype):
            raise CppDiagnostic("cpp.array-not-assignable", "builtin array assignment is not permitted", coord)
        if is_class_type(lhs.dtype) or is_class_type(rhs.dtype):
            raise CppDiagnostic("cpp.unsupported.class-assignment", "class copy/move assignment is not implemented", coord)
        if lhs.assignment_result:
            raise CppDiagnostic("cpp.unsupported.syntax", "assignment-result lvalue lowering", coord)
        if not lhs.lvalue:
            raise CppDiagnostic("cpp.not-lvalue", "assignment requires an lvalue", coord)
        if isinstance(lhs.dtype, ConstType):
            raise CppDiagnostic("cpp.const-assignment", "assignment requires a modifiable lvalue", coord)
        converted = self._convert(rhs, lhs.dtype, coord)
        # Assignment is an lvalue in C++; uses requiring that C difference
        # remain explicitly rejected, including within template patterns.
        if self.template_function is not None:
            return Expression(Pattern("assignment", coord, children=[lhs, rhs]), lhs.dtype,
                              assignment_result=True)
        if not isinstance(lhs.node, c_ast.ID):
            # C++17 sequences RHS before LHS; the shared C Assignment emitter
            # is allowed to evaluate its LHS first. Materialize the converted
            # RHS once in an owned statement-expression scope, then evaluate
            # the lvalue exactly once. This does not alter C's policy or inject
            # a runtime helper. Only builtin scalar/pointer values enter here.
            name = "__pcc_cpp_assignment_" + str(self.assignment_index)
            self.assignment_index += 1
            temporary = self._decl(name, lhs.dtype, coord, converted)
            assignment = c_ast.Assignment("=", lhs.node, c_ast.ID(name, coord), coord)
            node = c_ast.StmtExpr(c_ast.Compound([temporary, assignment], coord), coord)
        else:
            node = c_ast.Assignment("=", lhs.node, converted, coord)
        return Expression(node, lhs.dtype, assignment_result=True,
                          constant_fact=not_constant(unqualified(lhs.dtype),
                                                     "modification of a runtime object", coord))

    def _binary(self, minimum, template_argument=False):
        lhs = self._unary()
        while _BINARY_PRECEDENCE.get(self._peek().value, 0) >= minimum:
            # [temp.names]: an unparenthesized > closes a template argument;
            # parentheses/call arguments enter their own expression grammar.
            if template_argument and self._peek().value in (">", ">>"):
                break
            token = self.cursor.take()
            rhs = self._binary(_BINARY_PRECEDENCE[token.value] + 1, template_argument)
            lhs = self._binary_expression(token.value, lhs, rhs, token.coord)
        return lhs

    def _pointer_additive_expression(self, operator, lhs, rhs, coord):
        """[expr.add] runtime pointer offsets with preserved typed row stride."""
        left, right = unqualified(lhs.dtype), unqualified(rhs.dtype)
        if self.template_function is not None and (dependent(left) or dependent(right)):
            # The substituted operand can change offset into pointer difference
            # or an invalid operation. Do not guess its result type yet.
            return Expression(Pattern("binary", coord, operator, [lhs, rhs]), None)
        left_pointer, right_pointer = pointer_type(left), pointer_type(right)
        if left_pointer and right_pointer:
            if operator == "-":
                raise CppDiagnostic("cpp.unsupported.pointer-operators",
                                    "pointer difference is not implemented", coord)
            raise CppDiagnostic("cpp.invalid-operands", "two pointers cannot be added", coord)
        if left_pointer:
            dtype, offset = left, rhs
        elif right_pointer and operator == "+":
            dtype, offset = right, lhs
        else:
            raise CppDiagnostic("cpp.invalid-operands",
                                "pointer offset requires a pointer and an integral operand", coord)
        pointee = unqualified(dtype.pointee)
        if pointee == "void" or function_type(pointee):
            raise CppDiagnostic("cpp.invalid-operands",
                                "pointer offset requires a complete object pointee", coord)
        require_complete(pointee, coord)
        require_complete_array(pointee, coord)
        if is_enum_type(pointee) and not pointee.complete:
            raise CppDiagnostic("cpp.incomplete-enum", "pointer offset requires a complete enum", coord)
        if not dependent(offset.dtype) and not self.types.integral_operand(offset.dtype):
            raise CppDiagnostic("cpp.invalid-operands",
                                "pointer offset requires an integral or unscoped enum operand", coord)
        if self.template_function is not None:
            # Retain original operands for instance-local type checking and
            # replay. Never mix typed C locals into a template Pattern tree.
            return Expression(Pattern("binary", coord, operator, [lhs, rhs]), dtype)
        if dependent(offset.dtype):
            raise CppDiagnostic("cpp.unsupported.pointer-templates",
                                "dependent pointer offset requires template instantiation", coord)
        offset_type = self.types.promote(offset.dtype)
        left_type, right_type = (dtype, offset_type) if left_pointer else (offset_type, dtype)
        left_name = self._fresh_projection_name("__pcc_cpp_offset_l_")
        right_name = self._fresh_projection_name("__pcc_cpp_offset_r_")
        declarations = [self._decl(left_name, left_type, coord, self._convert(lhs, left_type, coord)),
                        self._decl(right_name, right_type, coord, self._convert(rhs, right_type, coord))]
        # Additive operands have no required relative ordering; this chooses
        # one permitted evaluation order, once each. Typed locals also restore
        # array pointees erased by a generic C formal/result projection.
        value = c_ast.BinaryOp(operator, c_ast.ID(left_name, coord), c_ast.ID(right_name, coord), coord)
        node = c_ast.StmtExpr(c_ast.Compound(declarations + [value], coord), coord)
        return Expression(node, dtype, constant_fact=unmodeled(
            dtype, "pointer offset constant-expression proof is not implemented", coord))

    def _binary_expression(self, operator, lhs, rhs, coord):
        lhs, rhs = self._decay_array(lhs), self._decay_array(rhs)
        if is_class_type(lhs.dtype) or is_class_type(rhs.dtype):
            raise CppDiagnostic("cpp.unsupported.class-operators", "class operator lookup/conversion is not implemented", coord)
        self._require_value(lhs)
        self._require_value(rhs)
        left, right = unqualified(lhs.dtype), unqualified(rhs.dtype)
        if any(is_enum_type(t) and t.scoped for t in (left, right)):
            if self.template_function is not None:
                raise CppDiagnostic("cpp.unsupported.enum-templates",
                                    "scoped enum operations need typed template Pattern support", coord)
            if left is not right or operator not in _COMPARISONS:
                raise CppDiagnostic("cpp.invalid-operands",
                                    "scoped enums require same-type comparison operands", coord)
            node = c_ast.BinaryOp(operator, lhs.node, rhs.node, coord)
            node = self._convert(Expression(node, "int"), "bool", coord)
            a = converted_fact(self._constant_fact(lhs), left.underlying, self.types,
                               coord, allow_scoped=True)
            b = converted_fact(self._constant_fact(rhs), left.underlying, self.types,
                               coord, allow_scoped=True)
            return Expression(node, "bool", constant_fact=binary_fact(
                operator, a, b, left.underlying, left.underlying, "bool", self.types, coord,
            ))
        operand_type = None
        dtype = None
        logical = operator in ("&&", "||")
        comparison = operator in _COMPARISONS
        shift = operator in ("<<", ">>")
        pointer_operands = any(pointer_type(e.dtype) or unqualified(e.dtype) == "nullptr" for e in (lhs, rhs))
        if pointer_operands and not logical:
            if operator in ("+", "-"):
                return self._pointer_additive_expression(operator, lhs, rhs, coord)
            if operator not in ("==", "!="):
                raise CppDiagnostic("cpp.unsupported.pointer-operators",
                                    "pointer ordering and this pointer operator are not implemented", coord)
            if self.template_function is not None:
                if not dependent(lhs.dtype) and not dependent(rhs.dtype):
                    self._composite_pointer_type(lhs, rhs, coord)
                return Expression(Pattern("binary", coord, operator, [lhs, rhs]), "bool")
            operand_type = self._composite_pointer_type(lhs, rhs, coord)
            node = c_ast.BinaryOp(operator, self._convert(lhs, operand_type, coord),
                                  self._convert(rhs, operand_type, coord), coord)
            return Expression(self._convert(Expression(node, "int"), "bool", coord), "bool")
        if not dependent(lhs.dtype) and not dependent(rhs.dtype):
            if operator in ("%", "&", "|", "^", "<<", ">>") and not (
                    self.types.integral_operand(lhs.dtype) and self.types.integral_operand(rhs.dtype)):
                raise CppDiagnostic("cpp.invalid-operands", "operator requires integral operands", coord)
            if logical:
                operand_type = "bool"
            elif shift:
                operand_type = self.types.promote(lhs.dtype)
            else:
                operand_type = self.types.common(lhs.dtype, rhs.dtype)
            dtype = "bool" if logical or comparison else operand_type
        rhs_type = self.types.promote(rhs.dtype) if shift and not dependent(rhs.dtype) else operand_type
        if self.template_function is not None:
            fact = (None if dependent(dtype) else binary_fact(
                operator, self._constant_fact(lhs), self._constant_fact(rhs),
                operand_type, rhs_type, dtype, self.types, coord))
            return Expression(Pattern("binary", coord, operator, [lhs, rhs]), dtype, constant_fact=fact)
        node = c_ast.BinaryOp(operator, self._convert(lhs, operand_type, coord, contextual=logical),
                              self._convert(rhs, rhs_type, coord, contextual=logical), coord)
        if logical or comparison:
            # C comparisons/logical operators yield int; C++ yields bool. Keep
            # the bool projection explicit for overloads and following C ops.
            node = self._convert(Expression(node, "int"), "bool", coord)
        return Expression(node, dtype, constant_fact=binary_fact(
            operator, self._constant_fact(lhs), self._constant_fact(rhs),
            operand_type, rhs_type, dtype, self.types, coord,
        ))

    def _composite_pointer_type(self, lhs, rhs, coord):
        first, second = unqualified(lhs.dtype), unqualified(rhs.dtype)
        first_null = first == "nullptr" or lhs.null_pointer
        second_null = second == "nullptr" or rhs.null_pointer
        if isinstance(first, PointerType) and second_null:
            return first
        if isinstance(second, PointerType) and first_null:
            return second
        if first_null and second_null:
            return PointerType("void")
        if isinstance(first, PointerType) and isinstance(second, PointerType):
            composite = composite_qualified_pointer(first, second)
            if composite is not None:
                return composite
            a, b = unqualified(first.pointee), unqualified(second.pointee)
            if a == b or "void" in (a, b):
                pointee = "void" if "void" in (a, b) else a
                if const_qualified(first.pointee) or const_qualified(second.pointee):
                    pointee = qualify_const(pointee)
                return PointerType(pointee)
        raise CppDiagnostic("cpp.invalid-operands", "incompatible operands for pointer equality", coord)

    def _c_style_cast_ahead(self):
        if self._peek().value != "(":
            return False
        with self.cursor.tentative():
            self.cursor.take()
            if not self._is_type(self._peek()):
                return False
            try:
                self._abstract_pointer_type(self._type())
            except CppDiagnostic:
                return False
            # A type-id must finish here: (int(42)) is instead a parenthesized
            # functional conversion. A shadowing value is never re-found as a
            # typedef by this tentative ordinary-name lookup.
            return self._peek().value == ")"

    def _explicit_arithmetic_cast(self, dtype, expression, coord, style):
        target, source = unqualified(dtype), unqualified(expression.dtype)
        feature = "static-cast" if style == "static_cast" else "c-style-cast"
        if dependent(target) or dependent(source):
            raise CppDiagnostic("cpp.unsupported." + feature,
                                "dependent explicit casts require additional template conversion semantics", coord)
        if is_class_type(target) or is_class_type(source):
            raise CppDiagnostic("cpp.unsupported.class-conversions",
                                "explicit class conversions require constructor/conversion lookup", coord)
        if source == "void" and target != "void":
            self._require_value(expression)
        if style == "static_cast" and pointer_type(target):
            from pcc.frontends.cpp.static_casts import convert_void_pointer
            return convert_void_pointer(self, target, expression, coord)
        same_enum = is_enum_type(target) and source is target
        if is_enum_type(target) and not same_enum:
            raise CppDiagnostic("cpp.unsupported.enum-casts",
                                "integer/distinct-enum destination casts require range semantics", coord)
        if target != "void" and not (same_enum or
                self.types.arithmetic_operand(target)
                and (self.types.arithmetic_operand(source) or is_enum_type(source))):
            raise CppDiagnostic("cpp.unsupported." + feature,
                                "explicit pointer/reference/representation casts are not implemented", coord)
        node = self._convert(expression, target, coord, force=True)
        return Expression(node, target, constant_fact=converted_fact(
            self._constant_fact(expression), target, self.types, coord, allow_scoped=True,
        ))

    def _sizeof_type_id_ahead(self):
        if self._peek().value != "(":
            return False
        with self.cursor.tentative():
            self.cursor.take()
            if not self._is_type(self._peek()):
                return False
            self._type(check_lowering=False)
            if self._peek().value != "(":
                return self._peek().value in (")", "*", "&", "&&", "[")
            # Distinguish sizeof(int(42)) (a functional-cast expression) from
            # sizeof(int()) / sizeof(int(*)(int)) (actual function type-ids).
            while self._peek().value == "(":
                self.cursor.take()
            return self._peek().value in ("*", "&", "&&", ")", "[") or self._is_type(self._peek())

    def _sizeof_parameter_syntax(self):
        # Function types will be rejected by sizeof Sema, but parse their
        # parameter declarations in an isolated scope instead of discarding
        # syntax or installing names in the surrounding source scope.
        parent = self.scope
        self.scope = Scope(parent, kind="function")
        try:
            return self._parameter_clause()
        finally:
            self.scope = parent

    def _sizeof_type_id(self):
        dtype = self._type(check_lowering=False)
        declaration = DeclaratorParser(
            self.cursor, self._identifier,
            parse_parameters=self._sizeof_parameter_syntax,
            parse_array_bound=self._array_bound,
        ).parse_abstract()
        chunks = declaration.chunks
        if any(chunk.kind == "function" for chunk in chunks):
            if chunks[0].kind == "function":
                raise CppDiagnostic("cpp.invalid-sizeof", "sizeof cannot apply to a function type", declaration.coord)
            raise CppDiagnostic("cpp.unsupported.function-pointers", "function pointer sizeof requires a function-type model", declaration.coord)
        for chunk in reversed(chunks):
            if chunk.kind == "array":
                dtype = make_array_type(dtype, chunk.bound, self.types, chunk.coord)
            elif chunk.kind == "pointer":
                dtype = self._pointer(dtype, chunk.token, chunk.qualifiers)
            elif chunk.kind in ("lvalue-reference", "rvalue-reference"):
                dtype = self._reference(dtype, chunk.token)
            else:
                raise CppDiagnostic("cpp.unsupported.references", "reference sizeof requires a reference-type model", chunk.coord)
        return dtype

    def _sizeof_expression(self, token):
        from pcc.frontends.cpp.size_queries import sizeof_type
        if self.parsing_template or self.instantiating_template:
            raise CppDiagnostic("cpp.unsupported.sizeof-templates", "dependent sizeof and template replay are not implemented", token.coord)
        if self._peek().value == "...":
            raise CppDiagnostic("cpp.unsupported.sizeof-packs", "sizeof... requires parameter pack semantics", token.coord)
        self.unevaluated_depth += 1
        try:
            if self._sizeof_type_id_ahead():
                self._expect("(")
                dtype = self._sizeof_type_id()
                self._expect(")")
            else:
                expression = self._unary()
                if expression.symbol is not None and expression.symbol.kind == "function":
                    raise CppDiagnostic("cpp.invalid-sizeof", "sizeof cannot apply to a function or overload set", token.coord)
                dtype = expression.dtype
            size = sizeof_type(dtype, self.types, token.coord)
        finally:
            self.unevaluated_depth -= 1
        # Only a typed target-sized constant reaches C lowering. No operand
        # AST, object address, call, load, temporary or side effect is emitted.
        result_type = c_size_type_name(self.types.target_triple)
        return Expression(self._convert(
            Expression(c_ast.Constant("int", str(size) + "ULL", token.coord), "unsigned long long"),
            result_type, token.coord, force=True), result_type,
            constant_fact=known(result_type, size, token.coord))

    def _unary(self):
        token = self._peek()
        from pcc.frontends.cpp.allocation import global_new_ahead, parse_new
        if token.value == "new" or global_new_ahead(self):
            return parse_new(self)
        if token.value == "sizeof":
            self.cursor.take()
            return self._sizeof_expression(token)
        if self._c_style_cast_ahead():
            self.cursor.take()
            dtype = self._abstract_pointer_type(self._type())
            self._expect(")")
            return self._explicit_arithmetic_cast(dtype, self._unary(), token.coord, "C-style")
        if token.value in ("++", "--"):
            self.cursor.take()
            return self._increment_expression(token.value, self._unary(), token.coord)
        if token.value in ("+", "-", "~", "!", "&", "*"):
            self.cursor.take()
            expression = self._unary()
            return self._unary_expression(token.value, expression, token.coord)
        expression = self._primary()
        while self._peek().value in (".", "->", "++", "--", "["):
            operator = self.cursor.take()
            if operator.value == "[":
                index = self._expression()
                self._expect("]")
                expression = self._subscript_expression(expression, index, operator.coord)
                continue
            if operator.value in ("++", "--"):
                expression = self._increment_expression(operator.value, expression, operator.coord, postfix=True)
                continue
            if self._peek().value == "~":
                from pcc.frontends.cpp.allocation import explicit_destructor
                expression = explicit_destructor(self, expression, operator)
                continue
            member = self._identifier()
            if self.template_function is not None and getattr(self, 'class_template_method', None) is None:
                raise CppDiagnostic("cpp.unsupported.dependent-lookup", "template member lookup is not implemented", operator.coord)
            expression = member_access(self, expression, operator.value, member)
        return expression

    def _subscript_expression(self, left, right, coord):
        if self.template_function is not None or self.instantiating_template:
            raise CppDiagnostic("cpp.unsupported.array-templates", "dependent subscript lookup is not implemented", coord)
        left, right = self._decay_array(left), self._decay_array(right)
        if is_class_type(left.dtype) or is_class_type(right.dtype):
            raise CppDiagnostic("cpp.unsupported.class-operators", "subscript overload lookup is not implemented", coord)
        first_pointer = pointer_type(left.dtype)
        pointer, index = (left, right) if first_pointer else (right, left)
        if not pointer_type(pointer.dtype) or not self.types.integral_operand(index.dtype):
            raise CppDiagnostic("cpp.invalid-subscript", "builtin subscript requires one object pointer and one integral operand", coord)
        dtype = unqualified(pointer.dtype).pointee
        if unqualified(dtype) == "void":
            raise CppDiagnostic("cpp.invalid-subscript", "void pointer cannot be subscripted", coord)
        require_complete(dtype, coord)
        require_complete_array(dtype, coord)
        if is_enum_type(dtype) and not unqualified(dtype).complete:
            raise CppDiagnostic("cpp.incomplete-enum", "subscript requires a complete element", coord)
        promoted = self.types.promote(index.dtype)
        # [expr.sub] sequences the syntactic left operand before the right,
        # even in i[p]. Materialize both once before normalizing pointer/index.
        serial = str(self.assignment_index)
        self.assignment_index += 1
        left_name, right_name = "__pcc_cpp_index_l_" + serial, "__pcc_cpp_index_r_" + serial
        left_type = left.dtype if first_pointer else promoted
        right_type = promoted if first_pointer else right.dtype
        declarations = [self._decl(left_name, left_type, coord, self._convert(left, left_type, coord)),
                        self._decl(right_name, right_type, coord, self._convert(right, right_type, coord))]
        p_name, i_name = (left_name, right_name) if first_pointer else (right_name, left_name)
        element = c_ast.ArrayRef(c_ast.ID(p_name, coord), c_ast.ID(i_name, coord), coord)
        class_glvalue = is_class_type(dtype)
        # A class element is a glvalue, not an eager aggregate load. Pointer
        # addition uses the existing C record layout and also permits the
        # one-past address without materializing an out-of-bounds element.
        address = (c_ast.BinaryOp("+", c_ast.ID(p_name, coord), c_ast.ID(i_name, coord), coord)
                   if class_glvalue else c_ast.UnaryOp("&", element, coord))
        body = c_ast.StmtExpr(c_ast.Compound(declarations + [address], coord), coord)
        null_glvalue = unqualified(dtype) == "nullptr"
        # Preserve left-before-right evaluation, not an &ArrayRef that shared
        # C lowering may implement by first fetching the terminal element.
        discarded = (c_ast.StmtExpr(c_ast.Compound(
            declarations + [c_ast.Constant("int", "0", coord)], coord), coord)
                     if null_glvalue or class_glvalue else None)
        return Expression(c_ast.UnaryOp("*", body, coord), dtype, True,
                          discarded_known=null_glvalue or class_glvalue, discarded_node=discarded,
                          constant_fact=unmodeled(unqualified(dtype), "array element constant proof is not implemented", coord))

    def _increment_expression(self, operator, expression, coord, postfix=False):
        dtype = unqualified(expression.dtype)
        self._check_extended_value_type(dtype, expression.node)
        if array_type(dtype):
            raise CppDiagnostic("cpp.invalid-operands", "an array is not a modifiable scalar lvalue", coord)
        if is_class_type(dtype):
            raise CppDiagnostic("cpp.unsupported.class-operators", "class increment/decrement requires overload lookup", coord)
        if expression.assignment_result:
            raise CppDiagnostic("cpp.unsupported.syntax", "assignment-result lvalue lowering", coord)
        if not expression.lvalue:
            raise CppDiagnostic("cpp.not-lvalue", "increment/decrement requires an lvalue", coord)
        if isinstance(expression.dtype, ConstType):
            raise CppDiagnostic("cpp.const-assignment", "increment/decrement requires a modifiable lvalue", coord)
        if not dependent(dtype):
            if dtype == "bool" or is_enum_type(dtype) or dtype in ("void", "nullptr"):
                raise CppDiagnostic("cpp.invalid-operands", "increment/decrement requires non-bool arithmetic or object pointer", coord)
            if isinstance(dtype, PointerType):
                if unqualified(dtype.pointee) == "void":
                    raise CppDiagnostic("cpp.invalid-operands", "increment/decrement requires a complete object pointer", coord)
                require_complete(dtype.pointee, coord)
                require_complete_array(dtype.pointee, coord)
                if is_enum_type(dtype.pointee) and not unqualified(dtype.pointee).complete:
                    raise CppDiagnostic("cpp.incomplete-enum", "pointer increment requires a complete enum", coord)
        if self.template_function is not None:
            return Expression(Pattern("increment", coord, (operator, postfix), [expression]),
                              dtype, not postfix)
        fact = not_constant(dtype, "modification of a runtime object", coord)
        # Capture the typed object's address once for both forms. In
        # particular a pointer-to-array formal can arrive through C's erased
        # parameter projection; the typed local restores the actual row stride
        # before any update. Postfix must still return the old scalar value,
        # whereas prefix preserves the updated object's lvalue identity.
        name = "__pcc_cpp_increment_" + str(self.assignment_index)
        self.assignment_index += 1
        address = (expression.node.expr
                   if isinstance(expression.node, c_ast.UnaryOp) and expression.node.op == "*"
                   else c_ast.UnaryOp("&", expression.node, coord))
        temporary = self._decl(name, PointerType(dtype), coord, address)
        pointer = c_ast.ID(name, coord)
        object_node = c_ast.UnaryOp("*", pointer, coord)
        if postfix:
            old_value = c_ast.UnaryOp("p" + operator, object_node, coord)
            value = c_ast.StmtExpr(c_ast.Compound([temporary, old_value], coord), coord)
            return Expression(value, dtype, constant_fact=fact)
        update = c_ast.UnaryOp(operator, object_node, coord)
        address_expression = c_ast.StmtExpr(c_ast.Compound([temporary, update, pointer], coord), coord)
        return Expression(c_ast.UnaryOp("*", address_expression, coord), dtype, True,
                          constant_fact=fact)

    def _unary_expression(self, operator, expression, coord):
        if operator == "&" and expression.qualified_member is not None:
            raise CppDiagnostic("cpp.unsupported.member-pointers",
                                "qualified member address requires a pointer-to-member type and representation", coord)
        if operator != "&":
            expression = self._decay_array(expression)
        self._require_value(expression, allow_class=operator == "&", object_address=operator == "&")
        if operator in ("&", "*"):
            if self.parsing_template and getattr(self, 'class_template_method', None) is None:
                raise CppDiagnostic("cpp.unsupported.pointer-templates", "pointer template expressions are not implemented", coord)
            if operator == "&":
                if expression.assignment_result:
                    raise CppDiagnostic("cpp.unsupported.syntax", "assignment-result lvalue lowering", coord)
                if not expression.lvalue:
                    raise CppDiagnostic("cpp.not-lvalue", "address-of requires an object lvalue", coord)
                dtype = PointerType(expression.dtype)
            else:
                pointer = unqualified(expression.dtype)
                if self.template_function is not None and dependent(pointer) and not isinstance(pointer, PointerType):
                    return Expression(Pattern('unary', coord, operator, [expression]), None, True)
                if not isinstance(pointer, PointerType) or unqualified(pointer.pointee) == "void":
                    raise CppDiagnostic("cpp.invalid-dereference", "dereference requires a pointer to an object", coord)
                dtype = pointer.pointee
            if self.template_function is not None:
                return Expression(Pattern('unary', coord, operator, [expression]), dtype, operator == '*')
            # Do not emit a load through the pointee merely to take its address.
            if operator == "&" and isinstance(expression.node, c_ast.UnaryOp) and expression.node.op == "*":
                return Expression(expression.node.expr, dtype)
            class_glvalue = operator == "*" and (is_class_type(dtype) or unqualified(dtype) == "nullptr")
            return Expression(c_ast.UnaryOp(operator, expression.node, coord), dtype, operator == "*",
                              discarded_known=class_glvalue,
                              discarded_node=expression.node if class_glvalue else None,
                              discarded_fact=self._constant_fact(expression) if class_glvalue else None,
                              static_address=(expression.symbol if operator == "&"
                                              and getattr(expression.symbol, "namespace_object", False)
                                              else None))
        if is_class_type(expression.dtype):
            raise CppDiagnostic("cpp.unsupported.class-operators", "class unary operator lookup/conversion is not implemented", coord)
        if is_enum_type(expression.dtype) and unqualified(expression.dtype).scoped:
            raise CppDiagnostic("cpp.invalid-operands", "scoped enum has no builtin unary arithmetic conversion", coord)
        dtype = None
        if not dependent(expression.dtype):
            if (pointer_type(expression.dtype) or unqualified(expression.dtype) == "nullptr") and operator != "!":
                raise CppDiagnostic("cpp.unsupported.pointer-operators", "unary pointer operator is not implemented", coord)
            if operator == "~" and not self.types.integral_operand(expression.dtype):
                raise CppDiagnostic("cpp.invalid-operands", "operator requires an integral operand", coord)
            dtype = "bool" if operator == "!" else self.types.promote(expression.dtype)
        if self.template_function is not None:
            fact = (None if dependent(dtype) else unary_fact(
                operator, self._constant_fact(expression), dtype, dtype, self.types, coord))
            return Expression(Pattern("unary", coord, operator, [expression]), dtype, constant_fact=fact)
        node = c_ast.UnaryOp(operator, self._convert(expression, dtype, coord, contextual=operator == "!"), coord)
        if operator == "!":
            node = self._convert(Expression(node, "int"), "bool", coord)
        return Expression(node, dtype, constant_fact=unary_fact(
            operator, self._constant_fact(expression), dtype, dtype, self.types, coord,
        ))

    def _literal(self, kind, value, dtype, coord, integer_value=None):
        null_pointer = dtype == "nullptr" or kind == "int" and value.rstrip("uUlL") == "0"
        fact = (known(dtype, integer_value, coord) if integer_value is not None
                else unmodeled(dtype, "non-integral literal constant semantics", coord))
        if self.template_function is not None:
            return Expression(Pattern("literal", coord, (kind, value, dtype, integer_value)), dtype,
                              null_pointer=null_pointer, constant_fact=fact)
        return Expression(c_ast.Constant(kind, value, coord), dtype,
                          null_pointer=null_pointer, constant_fact=fact,
                          discarded_known=dtype == "nullptr")

    def _check_call_value_abi(self, selected, coord):
        for dtype in (selected.dtype,) + selected.parameters:
            if is_class_type(dtype):
                raise CppDiagnostic("cpp.unsupported.class-value-abi", "record declaration projection does not implement copy/materialization at a call", coord)
            self._check_extended_value_type(dtype, selected)

    def _call_expression(self, group, arguments, coord, explicit_arguments=None):
        if (self.template_function is not None or self.instantiating_template) and any(
                isinstance(t, ReferenceType) for candidate in group.overloads for t in candidate.parameters):
            raise CppDiagnostic("cpp.unsupported.references", "reference calls in template replay require value-category deduction", coord)
        if any(contains_array(arg.dtype) for arg in arguments):
            if (self.template_function is not None or self.instantiating_template
                    or any(candidate.template_parameters for candidate in group.overloads)):
                raise CppDiagnostic("cpp.unsupported.array-templates", "array-related template deduction is not implemented", coord)
        arguments = [self._decay_array(argument) for argument in arguments]
        enum_arguments = []
        for argument in arguments:
            dtype = unqualified(argument.dtype)
            enum_arguments.append(unqualified(dtype.pointee) if isinstance(dtype, PointerType) else dtype)
        if any(is_enum_type(dtype) for dtype in enum_arguments):
            if (self.template_function is not None or self.instantiating_template
                    or any(candidate.template_parameters for candidate in group.overloads)):
                raise CppDiagnostic("cpp.unsupported.enum-templates",
                                    "enum-related template deduction is not implemented", coord)
        if any(is_class_type(arg.dtype) for arg in arguments):
            raise CppDiagnostic("cpp.unsupported.class-value-abi", "class argument ABI/ADL is not implemented", coord)
        if any(pointer_type(arg.dtype) and is_class_type(unqualified(arg.dtype).pointee) for arg in arguments):
            # Class-pointer ADL has already run at the unqualified call site;
            # dependent template association/deduction remains a separate gate.
            if any(candidate.template_parameters for candidate in group.overloads):
                raise CppDiagnostic("cpp.unsupported.class-templates", "class-related template deduction/ADL is not implemented", coord)
        argument_types = [arg.dtype for arg in arguments]
        null_constants = [arg.null_pointer for arg in arguments]
        if self.template_function is not None:
            # A later overload declaration must not mutate the definition-point
            # candidate set of an unqualified dependent call (ADL still absent).
            snapshot = Symbol(group.name, "function", group.coord)
            snapshot.overloads = list(group.overloads)
            dtype = None
            if (not any(dependent(t) for t in argument_types)
                    and not any(argument_dependent(a) for a in explicit_arguments or ())):
                selected = resolve_overload(snapshot, argument_types, self.types, coord, null_constants,
                                            explicit_arguments=explicit_arguments)
                if len(arguments) < len(selected.parameters):
                    raise CppDiagnostic("cpp.unsupported.default-argument-context",
                                        "default arguments at template call sites are not implemented", coord)
                self._check_call_value_abi(selected, coord)
                if selected.nonthrowing or selected.noreturn:
                    raise CppDiagnostic("cpp.unsupported.exceptions", "template calls cannot discard declared function contracts", coord)
                if any(contains_function(t) for t in (selected.dtype,) + selected.parameters):
                    raise CppDiagnostic("cpp.unsupported.function-pointers", "template callback invocation is not implemented", coord)
                dtype = selected.dtype
                result = unqualified(dtype)
                nominal = unqualified(result.pointee) if isinstance(result, PointerType) else result
                if contains_array(result):
                    raise CppDiagnostic("cpp.unsupported.array-templates", "array-related template call results are not implemented", coord)
                if is_enum_type(nominal):
                    raise CppDiagnostic("cpp.unsupported.enum-templates",
                                        "enum results in template definitions require dependent lookup", coord)
            call = (snapshot, explicit_arguments) if explicit_arguments is not None else snapshot
            return Expression(Pattern("call", coord, call, arguments), dtype)
        selected = resolve_overload(group, argument_types, self.types, coord, null_constants,
                                    [arg.lvalue or arg.assignment_result for arg in arguments], explicit_arguments)
        self._check_call_value_abi(selected, coord)
        if selected.nonthrowing or selected.noreturn:
            raise CppDiagnostic("cpp.unsupported.exceptions", "invoking declared noexcept/noreturn contracts requires runtime contract support", coord)
        if any(contains_function(t) for t in (selected.dtype,) + selected.parameters):
            raise CppDiagnostic("cpp.unsupported.function-pointers", "callback invocation semantics are not implemented", coord)
        if selected.template_origin is not None and getattr(self, "default_argument_binding", None) is not None:
            raise CppDiagnostic("cpp.unsupported.default-argument-context",
                                "template calls in default arguments require instantiation-context support", coord)
        if selected.template_origin is not None and not self.unevaluated_depth:
            origin = selected.template_origin
            entry = self.instance_registry.request(
                (origin, selected.template_arguments), selected, context=coord,
            )
            selected = entry.value
        from pcc.frontends.cpp.default_arguments import expand
        arguments = expand(self, selected, arguments, coord)
        converted = [self._convert(arg, dtype, coord)
                     for arg, dtype in zip(arguments, selected.parameters)]
        if selected.variadic:
            for argument in arguments[len(selected.parameters):]:
                source = unqualified(argument.dtype)
                if is_enum_type(source) and source.scoped:
                    raise CppDiagnostic("cpp.unsupported.variadic-argument",
                                        "scoped-enum variadic argument ABI is not implemented", coord)
                if not self.types.value_lowering_supported(source):
                    raise CppDiagnostic("cpp.unsupported.variadic-argument",
                                        "extended floating variadic argument ABI is not implemented", coord)
                destination = (PointerType("void") if source == "nullptr" else
                               "double" if source == "float" else
                               self.types.promote(source) if self.types.integral_operand(source) else source)
                converted.append(self._convert(argument, destination, coord))
        dtype = unqualified(selected.dtype)
        nominal = unqualified(dtype.pointee) if isinstance(dtype, PointerType) else dtype
        if self.instantiating_template and contains_array(dtype):
            raise CppDiagnostic("cpp.unsupported.array-templates", "array-related template call results are not implemented", coord)
        if self.instantiating_template and is_enum_type(nominal):
            raise CppDiagnostic("cpp.unsupported.enum-templates",
                                "enum results in template instantiation require dependent lookup", coord)
        return Expression(c_ast.FuncCall(c_ast.ID(selected.c_name, coord),
                                          c_ast.ExprList(converted, coord), coord), dtype,
                          constant_fact=not_constant(dtype, "call to a non-constexpr function", coord))

    def _explicit_template_arguments(self, allow_class_types=False):
        """Parse type-ids or typed expressions, never re-lex or replace tokens.

        The expression parser itself owns nested parentheses and function calls.
        It stops only at an unparenthesized template closing delimiter. Nested
        class template type-ids remain outside this function-template slice.
        """
        self._expect("<")
        arguments = []
        if self._accept(">"):
            return tuple(arguments)
        while True:
            start = self.cursor.position
            coord = self._peek().coord
            is_type_argument = False
            if self._is_type(self._peek()):
                with self.cursor.tentative():
                    self._abstract_pointer_type(self._type(check_lowering=False))
                    is_type_argument = self._peek().value in (",", ">", ">>")
                    if self._peek().value == "[":
                        raise CppDiagnostic("cpp.unsupported.template-type-arguments",
                                            "array template type arguments are not implemented", coord)
                    if self._peek().value == "(":
                        while self._accept("("):
                            pass
                        if (self._peek().value in ("*", "&", "&&", ")", "[")
                                or self._is_type(self._peek())):
                            # [temp.arg]/2 resolves int() to a function type-id,
                            # irrespective of whether the parameter is a value.
                            # Do not reinterpret it as integer value-initialization.
                            raise CppDiagnostic("cpp.unsupported.template-type-arguments",
                                                "function/grouped template type arguments are not implemented", coord)
            if is_type_argument:
                dtype = self._abstract_pointer_type(self._type(check_lowering=False))
                if (not allow_class_types and (
                        not isinstance(dtype, TypeParameter)
                        and not self.types.arithmetic_operand(dtype) and dtype != "void"
                        or isinstance(dtype, ConstType) or is_enum_type(dtype))):
                    raise CppDiagnostic("cpp.unsupported.template-type-arguments",
                                        "explicit template type arguments require arithmetic types", coord)
                argument = TemplateArgument("type", dtype, coord)
            else:
                expression = self._binary(1, template_argument=True)
                self._require_value(expression)
                argument = TemplateArgument("value", expression, coord)
            argument.tokens = tuple(self.cursor.tokens[start:self.cursor.position])
            arguments.append(argument)
            if self._accept(">"):
                break
            if self._peek().value == ">>":
                raise CppDiagnostic("cpp.unsupported.template-argument-syntax",
                                    "nested template type-ids are not implemented; parenthesize value shifts", self._peek().coord)
            self._expect(",")
        return tuple(arguments)

    def _call_arguments(self):
        arguments = []
        if not self._accept(")"):
            while True:
                expression = self._expression()
                self._require_value(expression)
                arguments.append(expression)
                if self._accept(")"):
                    break
                self._expect(",")
        return arguments

    def _implicit_object(self, owner, coord):
        if self.member_function is None or self.this_symbol is None or self.member_function.class_owner is not owner:
            raise CppDiagnostic("cpp.nonstatic-member-without-object", "a non-static member requires an object", coord)
        symbol = self.this_symbol
        if getattr(self, 'class_template_method', None) is not None:
            return Expression(Pattern('member-this', coord, symbol), symbol.dtype)
        node = c_ast.ID(symbol.c_name, coord)
        receivers = getattr(self, 'default_initializer_receivers', None)
        if receivers is not None:
            receivers.append(node)
        return Expression(node, symbol.dtype,
                          constant_fact=not_constant(symbol.dtype, "implicit object parameter", coord))

    def _primary(self):
        token = self.cursor.take()
        from pcc.frontends.cpp.varargs import BUILTINS, parse_builtin
        if token.value in BUILTINS:
            return parse_builtin(self, token)
        if token.value == "this":
            if self.member_function is None or self.this_symbol is None:
                raise CppDiagnostic("cpp.this-outside-member", "this requires a non-static member function", token.coord)
            return self._implicit_object(self.member_function.class_owner, token.coord)
        if token.value == "const_cast":
            from pcc.frontends.cpp.const_casts import parse
            return parse(self, token)
        if token.value == "static_cast":
            from pcc.frontends.cpp.static_casts import parse
            return parse(self, token)
        if token.value == "nullptr":
            if self.parsing_template and getattr(self, 'class_template_method', None) is None:
                raise CppDiagnostic("cpp.unsupported.pointer-templates", "nullptr template expressions are not implemented", token.coord)
            return self._literal("int", "0", "nullptr", token.coord)
        if token.value == "(":
            expression = self._expression()
            self._expect(")")
            expression.qualified_member = None
            expression.decltype_entity = None
            return expression
        if token.kind in ("CHAR_CONST", "WCHAR_CONST"):
            from pcc.frontends.cpp.character_literals import character_expression
            return character_expression(self, token)
        if token.kind.startswith("INT_CONST_"):
            if (token.kind == "INT_CONST_DEC" and len(token.value) > 1
                    and token.value.startswith("0")):
                raise CppDiagnostic("cpp.invalid-literal", "invalid octal integer literal",
                                    token.coord)
            try:
                dtype, value, integer_value = self.types.integer_literal_info(token.value)
            except ValueError as error:
                raise CppDiagnostic("cpp.invalid-literal", str(error), token.coord)
            return self._literal("int", value, dtype, token.coord, integer_value)
        if token.kind in ("FLOAT_CONST", "HEX_FLOAT_CONST"):
            try:
                dtype = self.types.floating_literal_type(token.value)
            except ValueError as error:
                raise CppDiagnostic("cpp.invalid-literal", str(error), token.coord)
            self._check_value_type(dtype, token)
            return self._literal("float", token.value, dtype, token.coord)
        if token.value in ("true", "false"):
            value = "1" if token.value == "true" else "0"
            node = self._convert(self._literal("int", value, "int", token.coord, int(value)),
                                 "bool", token.coord)
            return Expression(node, "bool", constant_fact=known("bool", int(value), token.coord))
        symbol = None
        if token.kind == "ID" and token.value not in _KEYWORDS or token.value in (":", "::"):
            self.cursor.position -= 1
            name = self._name()
            token = name.identifier
            symbol = self.scope.resolve_name(name)
        elif token.value not in TYPE_WORDS:
            if token.value == "static_assert" and self.template_function is not None:
                raise CppDiagnostic("cpp.unsupported.template-static-assert",
                                    "dependent static assertions require instantiation-time assertion checking", token.coord)
            self._unsupported(token)
        if token.value in TYPE_WORDS or symbol is not None and symbol.kind in ("type", "template-type", "class-template"):
            dtype = canonical_type([token.value]) if token.value in TYPE_WORDS else symbol.dtype
            if isinstance(dtype, ReferenceType):
                raise CppDiagnostic("cpp.unsupported.references", "reference casts require value-category/lifetime semantics", token.coord)
            if contains_function(dtype):
                raise CppDiagnostic("cpp.unsupported.function-pointers", "functional callback conversions are not implemented", token.coord)
            if array_type(dtype):
                raise CppDiagnostic("cpp.invalid-array-conversion", "functional conversion to an array is not permitted", token.coord)
            if is_class_type(dtype):
                raise CppDiagnostic("cpp.unsupported.class-initialization", "class temporary materialization is not implemented", token.coord)
            # [expr]/6: a non-class, non-array prvalue loses top-level cv.
            # The named alias itself and entity decltype retain their cv type.
            dtype = unqualified(dtype)
            self._expect("(")
            if self._accept(")"):
                if unqualified(dtype) == "nullptr":
                    return Expression(self._convert(self._literal("int", "0", "nullptr", token.coord),
                                                    dtype, token.coord, force=True), dtype)
                if is_enum_type(dtype):
                    return self._enum_constant_expression(dtype, 0, token.coord)
                zero = self._literal("int", "0", "int", token.coord, 0)
                return Expression(self._convert(zero, dtype, token.coord, force=True), dtype,
                                  constant_fact=converted_fact(self._constant_fact(zero), dtype,
                                                               self.types, token.coord, allow_scoped=True))
            expression = self._expression()
            self._expect(")")
            return Expression(self._convert(expression, dtype, token.coord, force=True), dtype,
                              constant_fact=converted_fact(self._constant_fact(expression), dtype,
                                                           self.types, token.coord, allow_scoped=True))
        if symbol is not None and symbol.kind in ("namespace", "namespace-alias"):
            raise CppDiagnostic("cpp.not-a-value", "namespace name does not denote a value", token.coord)
        if (symbol is not None and symbol.kind == "function" and symbol.name == "main"
                and any(candidate.owner is not None and candidate.owner.kind == "namespace" and not candidate.owner.namespace_path()
                        for candidate in symbol.overloads)):
            raise CppDiagnostic("cpp.main-use", "C++ does not permit use of main", token.coord)
        explicit_arguments = None
        if (symbol is not None and symbol.kind == "function" and self._peek().value == "<"
                and any(candidate.template_parameters for candidate in symbol.overloads)):
            explicit_arguments = self._explicit_template_arguments()
            if self._peek().value != "(":
                raise CppDiagnostic("cpp.unsupported.template-id-values",
                                    "a function template-id is implemented only as a direct call", token.coord)
        if self._accept("("):
            if symbol is not None and symbol.kind != "function":
                raise CppDiagnostic("cpp.not-callable", f"'{token.value}' is not callable", token.coord)
            arguments = []
            if not self._accept(")"):
                while True:
                    expression = self._expression()
                    self._require_value(expression)
                    arguments.append(expression)
                    if self._accept(")"):
                        break
                    self._expect(",")
            if getattr(symbol, "class_owner", None) is not None:
                from pcc.frontends.cpp.members import call_member, call_static_member, is_static_group
                if is_static_group(symbol):
                    return call_static_member(self, symbol, arguments, token.coord)
                return call_member(self, symbol, self._implicit_object(symbol.class_owner, token.coord), arguments, token.coord)
            if not name.global_scope and len(name.parts) == 1:
                if (getattr(self, 'class_template_method', None) is not None
                        and (any(dependent(arg.dtype) for arg in arguments)
                             or any(argument_dependent(arg) for arg in explicit_arguments or ()))):
                    raise CppDiagnostic("cpp.unsupported.dependent-lookup",
                                        "unqualified dependent free calls require instantiation-time ADL", token.coord)
                symbol = argument_dependent_lookup(token.value, [arg.dtype for arg in arguments], symbol, token.coord)
            if symbol is None:
                if self.template_function is not None and any(dependent(arg.dtype) for arg in arguments):
                    raise CppDiagnostic("cpp.unsupported.dependent-lookup",
                                        "ADL-only dependent call lookup is not implemented", token.coord)
                raise CppDiagnostic("cpp.undeclared", f"undeclared name '{token.value}'", token.coord)
            return self._call_expression(symbol, arguments, token.coord, explicit_arguments)
        if symbol is None:
            raise CppDiagnostic("cpp.undeclared", f"undeclared name '{token.value}'", token.coord)
        default_binding = getattr(self, "default_argument_binding", None)
        if (default_binding is not None and symbol in default_binding.parameters
                and not self.unevaluated_depth):
            raise CppDiagnostic("cpp.default-argument-parameter-use",
                                "a parameter cannot be potentially evaluated in a default argument", token.coord)
        if symbol.kind == "template-value":
            return Expression(Pattern("template-value", token.coord, symbol.template_parameter),
                              symbol.dtype, symbol=symbol)
        if symbol.kind == "enumerator":
            result = self._enum_constant_expression(symbol.dtype, symbol.enum_value, token.coord)
            result.decltype_entity = symbol.dtype
            return result
        if symbol.kind == "member":
            if default_binding is not None:
                # [dcl.fct.default]/9 is stricter than the general unevaluated
                # no-object rule. Explicit obj.member is handled by member_access
                # instead; pointer-to-member defaults remain a capability gap.
                raise CppDiagnostic("cpp.unsupported.default-argument-context",
                                    "bare non-static members cannot be default values here; pointer-to-member defaults are not implemented", token.coord)
            object_available = (self.member_function is not None and self.this_symbol is not None
                                and self.member_function.class_owner is symbol.class_owner)
            if self.unevaluated_depth and not object_available:
                # [expr.prim.id]/2.3 permits a no-object data member name in
                # an unevaluated operand, with ordinary lookup/access intact.
                result = Expression(c_ast.ID(symbol.c_name, token.coord), symbol.dtype,
                                    True, symbol)
            else:
                # [class.mfct.non-static]/3 still transforms the id through
                # implicit this in unevaluated contexts. Reuse the same cv and
                # mutable-member checks; sizeof must not admit writes to const.
                result = member_access(self, self._implicit_object(symbol.class_owner, token.coord), "->", token)
            result.decltype_entity = symbol.dtype
            if name.global_scope or len(name.parts) > 1:
                result.qualified_member = symbol
            return result
        if self.template_function is not None and getattr(symbol, "namespace_object", False):
            fact = symbol.constant_fact
            if (isinstance(symbol.dtype, ConstType) and self.types.integral(symbol.dtype)
                    and symbol.initialization_complete and fact is not None and fact.status == KNOWN):
                # Capture this declaration's completed integral constant proof,
                # not its spelling or a later lookup in the caller's scope.
                return Expression(Pattern("constant-name", token.coord, (symbol, fact)),
                                  symbol.dtype, True, symbol, constant_fact=fact)
            raise CppDiagnostic("cpp.unsupported.namespace-object-templates",
                                "template namespace object bindings require a completed integral constant", token.coord)
        if self.template_function is not None:
            return Expression(Pattern("name", token.coord, symbol), symbol.dtype,
                              symbol.kind == "value", symbol)
        if isinstance(symbol.dtype, ReferenceType):
            result = Expression(c_ast.UnaryOp('*', c_ast.ID(symbol.c_name, token.coord), token.coord),
                                symbol.dtype.referent, True, symbol)
            result.decltype_entity = symbol.dtype
            return result
        fact = self._symbol_constant_fact(symbol, token.coord)
        return Expression(c_ast.ID(symbol.c_name, token.coord), symbol.dtype,
                          symbol.kind == "value", symbol,
                          constant_fact=fact,
                          discarded_known=symbol.kind == "value" and (is_class_type(symbol.dtype)
                                                                     or unqualified(symbol.dtype) == "nullptr"),
                          discarded_fact=fact, decltype_entity=symbol.dtype)

    def _enum_constant_expression(self, dtype, value, coord):
        # Enumerator values have no storage. Project their already-checked
        # value, but preserve the nominal type and never mark zero as a null
        # pointer literal. Definition-body enumerators still have integer type.
        if self.template_function is not None or self.instantiating_template:
            raise CppDiagnostic("cpp.unsupported.enum-templates",
                                "enum expressions in templates require dependent lookup", coord)
        if is_enum_type(dtype) and not unqualified(dtype).complete:
            raise CppDiagnostic("cpp.incomplete-enum", "enum value initialization requires a complete type", coord)
        scalar = unqualified(dtype).underlying if is_enum_type(dtype) else unqualified(dtype)
        suffix = {"unsigned int": "u", "long": "l", "unsigned long": "ul",
                  "long long": "ll", "unsigned long long": "ull"}.get(scalar, "")
        node = c_ast.Constant("int", str(value) + suffix, coord)
        typename = c_ast.Typename(None, [], self._scalar_type(None, dtype, coord), coord)
        return Expression(c_ast.Cast(typename, node, coord), dtype,
                          constant_fact=known(dtype, value, coord))

    def _substitute_pattern_type(self, dtype, bindings, coord):
        context = getattr(self, 'class_template_instance', None)
        if context is None:
            return substitute(dtype, bindings)
        from pcc.frontends.cpp.class_templates import substitute_member
        return substitute_member(dtype, bindings, context.symbol.class_owner, coord)

    def _instantiate_expression(self, expression, bindings, environment):
        pattern = expression.node if isinstance(expression, Expression) else expression
        kind, coord = pattern.kind, pattern.coord
        if kind == "literal":
            literal_kind, value, dtype, integer_value = pattern.value
            return self._literal(literal_kind, value, dtype, coord, integer_value)
        if kind in ("template-value", "constant-name"):
            argument = (bindings[pattern.value.index] if kind == "template-value" else pattern.value[1])
            # Project a checked typed value; bool/character/small integers need
            # an explicit C cast because an integer AST token alone is int.
            suffix = {"unsigned int": "u", "long": "l", "unsigned long": "ul",
                      "long long": "ll", "unsigned long long": "ull"}.get(argument.dtype, "")
            node = c_ast.Constant("int", str(argument.value) + suffix, coord)
            typename = c_ast.Typename(None, [], self._scalar_type(None, argument.dtype, coord), coord)
            return Expression(c_ast.Cast(typename, node, coord), argument.dtype,
                              constant_fact=known(argument.dtype, argument.value, coord))
        if kind in ("name", "member-this"):
            symbol = pattern.value
            return environment[id(symbol)]
        if kind == 'member-field':
            member, operator, token = pattern.value
            base = self._instantiate_expression(pattern.children[0], bindings, environment)
            return member_access(self, base, operator, token)
        arguments = [self._instantiate_expression(child, bindings, environment)
                     for child in pattern.children]
        if kind == 'member-call':
            from pcc.frontends.cpp.members import call_member, call_static_member
            group, static, has_receiver, address_available = pattern.value
            context = self.class_template_instance
            concrete = context.symbol.class_owner.template_member_groups[group]
            receiver = arguments.pop(0) if has_receiver else None
            if static:
                return call_static_member(self, concrete, arguments, coord,
                                          None if receiver is None else receiver.node)
            return call_member(self, concrete, receiver, arguments, coord, address_available)
        if kind == "cast":
            original_type, force, contextual = pattern.value
            dtype = self._substitute_pattern_type(original_type, bindings, coord)
            return Expression(self._convert(arguments[0], dtype, coord, force=force, contextual=contextual), dtype,
                              constant_fact=converted_fact(self._constant_fact(arguments[0]), dtype,
                                                           self.types, coord, allow_scoped=force))
        if kind == "binary":
            return self._binary_expression(pattern.value, arguments[0], arguments[1], coord)
        if kind == "unary":
            return self._unary_expression(pattern.value, arguments[0], coord)
        if kind == "increment":
            operator, postfix = pattern.value
            return self._increment_expression(operator, arguments[0], coord, postfix)
        if kind == "assignment":
            return self._assignment_expression(arguments[0], arguments[1], coord)
        if kind == "compound-assignment":
            return self._compound_assignment_expression(pattern.value, arguments[0], arguments[1], coord)
        if kind == "call":
            if isinstance(pattern.value, tuple):
                group, explicit = pattern.value
                substituted = tuple(TemplateArgument(
                    argument.kind,
                    substitute(argument.value, bindings) if argument.kind == "type" else
                    self._instantiate_expression(argument.value, bindings, environment),
                    argument.coord, argument.tokens) for argument in explicit)
                return self._call_expression(group, arguments, coord, substituted)
            return self._call_expression(pattern.value, arguments, coord)
        raise CppDiagnostic("cpp.internal-pattern", "unknown expression pattern", coord)

    def _instantiate_statement(self, pattern, bindings, environment):
        kind, coord = pattern.kind, pattern.coord
        if kind == "block":
            return c_ast.Compound([self._instantiate_statement(child, bindings, environment)
                                   for child in pattern.children], coord)
        if kind == "empty":
            return c_ast.EmptyStatement(coord)
        if kind == "if":
            condition = self._instantiate_expression(pattern.children[0], bindings, environment)
            consequent = self._instantiate_statement(pattern.children[1], bindings, environment)
            alternative = (self._instantiate_statement(pattern.children[2], bindings, environment)
                           if len(pattern.children) > 2 else None)
            return c_ast.If(condition.node, consequent, alternative, coord)
        if kind == "for":
            local = dict(environment)
            init, condition, step, body = pattern.children
            init_node = None if init is None else self._instantiate_statement(init, bindings, local)
            condition_node = None if condition is None else self._instantiate_expression(condition, bindings, local).node
            step_node = None if step is None else self._instantiate_statement(step, bindings, local)
            body_node = self._instantiate_statement(body, bindings, local)
            return c_ast.Compound([c_ast.For(init_node, condition_node, step_node, body_node, coord)], coord)
        if kind == "discarded-sequence":
            return c_ast.ExprList([self._discarded_value_node(self._instantiate_expression(child, bindings, environment))
                                   for child in pattern.children], coord)
        if kind == "do-while":
            condition = self._instantiate_expression(pattern.children[0], bindings, environment)
            body = self._instantiate_statement(pattern.children[1], bindings, dict(environment))
            return c_ast.DoWhile(condition.node, body, coord)
        if kind == "while":
            condition = self._instantiate_expression(pattern.children[0], bindings, environment)
            body = self._instantiate_statement(pattern.children[1], bindings, environment)
            return c_ast.While(condition.node, body, coord)
        if kind in ("break", "continue"):
            return c_ast.Break(coord) if kind == "break" else c_ast.Continue(coord)
        if kind == "decl":
            symbol, direct_initializer = pattern.value
            dtype = self._substitute_pattern_type(symbol.dtype, bindings, coord)
            if (getattr(self, 'class_template_instance', None) is not None
                    and (is_class_type(dtype) or isinstance(dtype, ReferenceType) or contains_array(dtype))):
                raise CppDiagnostic('cpp.unsupported.member-templates',
                                    'class template local subobjects/references/arrays require lifetime-aware Pattern replay', coord)
            self._check_object_type(dtype, coord)
            if isinstance(dtype, ConstType) and not pattern.children:
                raise CppDiagnostic("cpp.const-initializer", "const objects require an initializer", coord)
            environment[id(symbol)] = Expression(c_ast.ID(symbol.c_name, coord), dtype, True)
            init = None
            if pattern.children:
                expression = self._instantiate_expression(pattern.children[0], bindings, environment)
                init = self._convert(expression, dtype, coord, contextual=direct_initializer)
            return self._decl(symbol.c_name, dtype, coord, init)
        if kind == "return":
            expression = (self._instantiate_expression(pattern.children[0], bindings, environment)
                          if pattern.children else None)
            return self._return_statement(expression, coord)
        return self._discarded_value_node(self._instantiate_expression(pattern, bindings, environment))

    def _instantiate_body(self, instance, request_coord):
        origin = instance.template_origin
        if origin.pattern is None:
            raise CppDiagnostic("cpp.template-definition", "selected template has no available definition",
                                request_coord)
        parameters = []
        environment = {}
        for symbol, dtype in zip(origin.parameter_symbols, instance.parameters):
            if dtype == "void":
                raise CppDiagnostic("cpp.invalid-parameter",
                                    "template substitution formed a void parameter", symbol.coord)
            parameters.append(self._decl(symbol.c_name, dtype, symbol.coord))
            environment[id(symbol)] = Expression(c_ast.ID(symbol.c_name, symbol.coord), dtype, True)
        declaration = self._decl(instance.c_name, instance.dtype, origin.coord)
        if instance.internal_linkage:
            declaration.storage = ["static"]
        declaration.type = c_ast.FuncDecl(c_ast.ParamList(parameters, origin.coord),
                                          declaration.type, origin.coord)
        previous_return = self.return_type
        previous_instantiating = self.instantiating_template
        self.return_type = instance.dtype
        self.instantiating_template = True
        try:
            body = self._instantiate_statement(origin.pattern, instance.template_arguments, environment)
        except CppDiagnostic as error:
            raise CppDiagnostic(error.code, error.message + " (while instantiating " + instance.c_name
                                + " requested at " + str(request_coord) + ")", error.coord)
        finally:
            self.return_type = previous_return
            self.instantiating_template = previous_instantiating
        return c_ast.FuncDef(declaration, None, body, origin.coord)
