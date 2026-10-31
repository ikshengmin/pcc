"""Handwritten C++ parser with semantic lookup and reversible lookahead.

This increment accepts arithmetic/builtin-pointer free functions, deduced
arithmetic function templates and scalar control-flow statements. It builds the existing C AST only *after*
resolving names and checking supported semantics. It does not rewrite source
text, invoke the C parser, or discard missing lifetime/exception/template
semantics. Unimplemented features remain explicit errors.
"""

from pcc.frontends.c.ast import c_ast
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
    is_class_type, member_access, parse_class, require_complete,
)
from pcc.frontends.cpp.initializers import (
    lower_aggregate_initializer, parse_initializer_list,
)
from pcc.frontends.cpp.constants import (
    binary_fact, converted_fact, known, not_constant, unary_fact, unmodeled,
)
from pcc.frontends.cpp.enums import parse_enum
from pcc.frontends.cpp.arrays import (
    const_array_elements, lower_array_initializer, make_array_type,
    require_complete_array,
)
from pcc.frontends.cpp.objects import parse_namespace_object
from pcc.frontends.cpp.types import (
    ArithmeticTypes, ArrayType, ConstType, PointerType, TYPE_WORDS, array_type, canonical_type, c_type_names, const_qualified, contains_array,
    is_enum_type, pointer_type, qualify_const, unqualified,
)
from pcc.frontends.cpp.templates import (
    Pattern, TypeParameter, dependent, same_type, substitute,
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
                 discarded_node=None, discarded_fact=None, static_address=None):
        self.node = node
        self.dtype = dtype
        self.lvalue = lvalue
        self.symbol = symbol
        self.null_pointer = null_pointer
        self.assignment_result = assignment_result
        self.constant_fact = constant_fact
        # Typed evaluation plan for discarding an admitted class glvalue,
        # without an lvalue-to-rvalue conversion. None is a proven effect-free
        # plan only when discarded_known is true; unknown plans fail closed.
        self.discarded_known = discarded_known
        self.discarded_node = discarded_node
        self.discarded_fact = discarded_fact
        self.static_address = static_address


class CppParser:
    def __init__(self, target_triple=None):
        self.types = ArithmeticTypes(target_triple)
        self.instantiating_template = False

    def parse(self, text, filename=""):
        self.scope = Scope()
        self.return_type = None
        self.template_function = None
        self.member_function = None
        self.this_symbol = None
        self.parsing_template = False
        self.instantiating_template = False
        self.instance_registry = InstanceRegistry()
        self.loop_depth = 0
        self.switch_contexts = []
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
        instances = self.instance_registry.drain(
            lambda entry: self._instantiate_body(entry.value, entry.context),
        )
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
                # prototypes after its record definition, with explicit this.
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
        lexer.input(text)
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
        if token.value in TYPE_WORDS or token.value in ("const", "volatile"):
            return True
        if token.kind == "ID" and token.value not in _KEYWORDS or token.value in (":", "::"):
            symbol = self._type_symbol()
            return symbol is not None and symbol.kind in ("type", "template-type")
        return False

    def _check_value_type(self, dtype, token):
        plain = unqualified(dtype)
        nominal = unqualified(plain.pointee) if isinstance(plain, PointerType) else plain
        if self.parsing_template and contains_array(dtype):
            raise CppDiagnostic("cpp.unsupported.array-templates", "array template typing/deduction is not implemented", token.coord)
        if self.parsing_template and is_enum_type(nominal):
            raise CppDiagnostic("cpp.unsupported.enum-templates",
                                "enum-related template semantics are not implemented", token.coord)
        if self.parsing_template and (pointer_type(dtype) or isinstance(dtype, ConstType)):
            raise CppDiagnostic("cpp.unsupported.pointer-templates",
                                "pointer/cv template types are not implemented", token.coord)
        if dependent(dtype):
            return dtype
        if not self.types.value_lowering_supported(dtype):
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
            elif not words and dtype is None and self._is_type(self._peek()):
                dtype = self.scope.resolve_name(self._name()).dtype
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
                dtype = self._abstract_pointer_type(self._type())
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
            name, dtype = self._declarator(dtype)
        self._expect(";")
        self.scope.declare(Symbol(name.value, "type", name.coord, dtype=dtype))

    def _pointer(self, dtype, token, qualifiers=()):
        leaf = dtype
        while array_type(leaf):
            leaf = leaf.element
        if array_type(dtype) and pointer_type(leaf):
            raise CppDiagnostic("cpp.unsupported.multilevel-pointers", "array-of-pointer pointee qualification requires deeper pointer rules", token.coord)
        if array_type(dtype) and dtype.bound is None:
            raise CppDiagnostic("cpp.unsupported.unknown-bound-array-pointers", "pointer to an unknown-bound array requires separate completeness rules", token.coord)
        if self.parsing_template:
            raise CppDiagnostic("cpp.unsupported.pointer-templates", "pointer template types are not implemented", token.coord)
        if pointer_type(dtype):
            raise CppDiagnostic("cpp.unsupported.multilevel-pointers", "multilevel pointer semantics are not implemented", token.coord)
        if "volatile" in qualifiers:
            raise CppDiagnostic("cpp.unsupported.volatile", "volatile access lowering is not implemented", token.coord)
        result = PointerType(dtype)
        return qualify_const(result) if "const" in qualifiers else result

    def _abstract_pointer_type(self, dtype):
        while self._peek().value == "*":
            token = self.cursor.take()
            dtype = self._pointer(dtype, token, parse_cv_qualifiers(self.cursor))
        return dtype

    def _apply_declarator(self, declaration, dtype, function=False, member=False):
        name = declaration.name
        if not member and (name.global_scope or len(name.parts) != 1):
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
                dtype = make_array_type(dtype, chunk.bound, self.types, chunk.coord)
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

    def _declarator(self, dtype, parse_arrays=True):
        declaration = DeclaratorParser(self.cursor, self._identifier,
                                       parse_array_bound=self._array_bound,
                                       parse_functions=False).parse(parse_suffixes=parse_arrays)
        return self._apply_declarator(declaration, dtype)

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
        if array_type(dtype):
            dimension = None if dtype.bound is None else c_ast.Constant("int", str(dtype.bound), coord)
            return c_ast.ArrayDecl(CppParser._scalar_type(name, dtype.element, coord), dimension, [], coord)
        if is_class_type(dtype):
            return c_class_type(name, dtype, coord)
        qualifiers = ["const"] if isinstance(dtype, ConstType) else []
        dtype = unqualified(dtype)
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
        return Expression(c_ast.Cast(target, expression.node, coord), dtype,
                          constant_fact=unmodeled(dtype, "array address constant proof is not implemented", coord))

    def _convert(self, expression, dtype, coord, force=False, contextual=False):
        dtype = unqualified(dtype)
        if array_type(dtype):
            raise CppDiagnostic("cpp.invalid-array-conversion", "an array is not a scalar conversion destination", coord)
        expression = self._decay_array(expression)
        if is_class_type(dtype) or is_class_type(expression.dtype):
            raise CppDiagnostic("cpp.unsupported.class-conversions",
                                "class value conversion/copy/lifetime semantics are not implemented", coord)
        # Only an explicit conversion may discard an arithmetic result. Return
        # statements perform their own void check and preserve their expression.
        if dtype == "void" and force:
            self._require_value(expression, allow_void=True)
            if self.template_function is not None:
                return Pattern("cast", coord, dtype, [expression])
            typename = c_ast.Typename(None, [], self._scalar_type(None, dtype, coord), coord)
            return c_ast.Cast(typename, expression.node, coord)
        self._require_value(expression)
        if self.template_function is not None:
            if not dependent(expression.dtype) and not dependent(dtype):
                if self.types.conversion_rank(expression.dtype, dtype, expression.null_pointer) is None:
                    raise CppDiagnostic("cpp.invalid-conversion", "unsupported conversion", coord)
            if same_type(expression.dtype, dtype) and not force:
                return expression.node
            return Pattern("cast", coord, dtype, [expression])
        nullptr_bool = expression.dtype == "nullptr" and dtype == "bool" and (force or contextual)
        enum_explicit = (force and is_enum_type(expression.dtype)
                         and self.types.arithmetic_operand(dtype) and not is_enum_type(dtype))
        if (force and is_enum_type(dtype)
                and unqualified(expression.dtype) is not dtype):
            raise CppDiagnostic("cpp.unsupported.enum-casts",
                                "casts to a distinct enum require range/conversion semantics", coord)
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
        if self.scope.namespace_path():
            raise CppDiagnostic("cpp.unsupported.namespace-templates",
                                "namespace templates require qualified template ABI and lookup", keyword.coord)
        self._expect("<")
        parent = self.scope
        self.scope = Scope(parent)
        self.parsing_template = True
        parameters = []
        try:
            while True:
                if self._peek().value not in ("class", "typename"):
                    self._unsupported(self._peek(), "only type template parameters are implemented")
                self.cursor.take()
                name = self._identifier()
                dtype = TypeParameter(len(parameters), name.value, name.coord)
                self.scope.declare(Symbol(name.value, "template-type", name.coord, dtype=dtype))
                parameters.append(dtype)
                if self._accept(">"):
                    break
                self._expect(",")
            if not parameters:
                self._unsupported(keyword, "explicit specializations are not implemented")
            self._function(template_parameters=tuple(parameters), declaration_scope=parent)
        finally:
            self.scope = parent
            self.parsing_template = False

    def _function(self, template_parameters=(), declaration_scope=None, implicit_extern=False,
                  member_definition=None):
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
            result_type = self._type(storage_specifiers=storage_specifiers)
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

        def declarator_name(name):
            nonlocal member_owner
            if member_definition is not None:
                if name.global_scope or len(name.parts) != 1:
                    raise CppDiagnostic("cpp.invalid-member-declaration", "in-class member names must be unqualified", name.coord)
                return
            if len(name.parts) < 2:
                return
            from pcc.frontends.cpp.declarators import QualifiedName
            qualifier = QualifiedName(name.tokens[:-1], name.global_scope, name.coord)
            target = parent.resolve_name(qualifier)
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

        def parameter_clause():
            if self._peek().value != ")":
                while True:
                    dtype_token = self._peek()
                    dtype = self._type()
                    # Exactly one unnamed void parameter denotes an empty
                    # parameter list, including a typedef naming void. A named
                    # or additional void parameter is never silently removed.
                    if dtype == "void" and self._peek().value == ")" and not params:
                        break
                    param = None
                    with self.cursor.tentative():
                        self._abstract_pointer_type(dtype)
                        unnamed = self._peek().value in (",", ")")
                    if unnamed:
                        dtype = self._abstract_pointer_type(dtype)
                    else:
                        param, dtype = self._declarator(dtype)
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
                    if self._peek().value == ")":
                        break
                    self._expect(",")
            return tuple(params)

        try:
            declarator = DeclaratorParser(self.cursor, self._identifier, parameter_clause, self._array_bound,
                                          on_name=declarator_name).parse()
            is_function = any(chunk.kind == "function" for chunk in declarator.chunks)
            name, result_type = self._apply_declarator(declarator, result_type, function=is_function,
                                                       member=member_owner is not None and is_function)
        finally:
            self.scope = parent
        access_scope = member_owner.scope if member_owner is not None else parent
        for accessed, access_coord in return_lookup.deferred_member_access:
            access_scope.check_member_access(accessed, access_coord)
        if not is_function:
            if template_parameters:
                raise CppDiagnostic("cpp.unsupported.variable-templates",
                                    "variable templates require instantiation and ODR semantics", name.coord)
            return parse_namespace_object(self, name, result_type, storage, implicit_extern)
        if array_type(result_type):
            raise CppDiagnostic("cpp.invalid-function-return", "a function cannot return an array", name.coord)
        if storage is not None and (storage.value == "static" or template_parameters):
            raise CppDiagnostic("cpp.unsupported.function-storage",
                                "function storage specifiers require function linkage semantics", storage.coord)
        if self.scope.lookup_lexical(name.value) is not None and self.scope.lookup_lexical(name.value).kind == "template-type":
            raise CppDiagnostic("cpp.template-parameter-shadowing",
                                "function name shadows a template parameter", name.coord)
        for signature_type in (result_type,) + tuple(dtype for param, dtype in params):
            plain = unqualified(signature_type)
            if is_class_type(plain):
                raise CppDiagnostic("cpp.unsupported.class-value-abi",
                                    "class by-value function ABI/lifetime is not implemented", name.coord)
            pointee = unqualified(plain.pointee) if isinstance(plain, PointerType) else plain
            if isinstance(pointee, ClassType) and pointee.internal_linkage:
                raise CppDiagnostic("cpp.unsupported.class-linkage",
                                    "function signatures involving internal-linkage class types are not implemented", name.coord)
            if is_enum_type(pointee) and pointee.internal_linkage:
                raise CppDiagnostic("cpp.unsupported.enum-linkage",
                                    "function signatures involving internal-linkage enum types are not implemented", name.coord)
        if (member_owner is None and name.value == "main" and self.language_linkage is not None
                and (not parent.namespace_path() or self.language_linkage == "C")):
            raise CppDiagnostic("cpp.main-language-linkage",
                                "main cannot have a language linkage specification", name.coord)
        definition = self._peek().value == "{"
        if member_owner is not None:
            if template_parameters or self.parsing_template:
                raise CppDiagnostic("cpp.unsupported.member-templates", "member templates require separate deduction/ODR semantics", name.coord)
            if storage is not None:
                raise CppDiagnostic("cpp.unsupported.member-storage", "static member functions require separate object rules", name.coord)
            if member_definition is not None and definition:
                raise CppDiagnostic("cpp.unsupported.inline-member-functions", "in-class bodies need C++ inline/ODR emission", name.coord)
            if member_definition is None and not definition:
                raise CppDiagnostic("cpp.member-definition-required", "an out-of-class member declaration must be a definition", name.coord)
        if member_owner is None and name.value == "main" and not parent.namespace_path() and params:
            self._unsupported(name, "main parameters are not implemented")
        if member_owner is None and name.value == "main" and not parent.namespace_path() and result_type != "int":
            raise CppDiagnostic("cpp.main-return-type", "main must return int", name.coord)
        if member_owner is None and name.value == "main" and not parent.namespace_path() and template_parameters:
            raise CppDiagnostic("cpp.main-template", "main cannot be a template", name.coord)
        new_symbol = Symbol(name.value, "function", name.coord, arity=len(params), dtype=result_type,
                            parameters=tuple(unqualified(dtype) for param, dtype in params))
        new_symbol.template_parameters = template_parameters
        new_symbol.requested_language_linkage = self.language_linkage
        if member_owner is None and self.language_linkage == "C":
            # Pointers retain their complete semantic pointee type here. The
            # declarator/type owners have already rejected unsupported function
            # pointers, references and object types. C linkage changes the
            # function's name/identity, not pointee cv/nominal identity or layout.
            # Incomplete object pointees and void are valid: this admission
            # neither completes them nor promises cross-language layout.
            if any(not self.types.integral(dtype) and dtype != "void" and not pointer_type(dtype)
                   for dtype in (result_type,) + new_symbol.parameters):
                raise CppDiagnostic("cpp.unsupported.c-linkage-signature",
                                    "C-linkage signatures require fundamental integers, void, or object/void pointers", name.coord)
            if parent.internal_linkage:
                raise CppDiagnostic("cpp.unsupported.c-linkage-internal",
                                    "C function type with internal name linkage is not implemented", name.coord)
        registry = self.scope if declaration_scope is None else declaration_scope
        if member_owner is not None:
            from pcc.frontends.cpp.members import declare_member_function
            const = "const" in declarator.chunks[0].qualifiers
            symbol = declare_member_function(member_owner, new_symbol, const,
                                             outside=member_definition is None)
        else:
            symbol = registry.declare_function(new_symbol, definition)
        if template_parameters:
            # Sema may inspect viable signatures without registering work.
            # Only the selected call requests an entry in this shared owner.
            symbol.instances = self.instance_registry.value_view(symbol)
        self.function_projection_names.add(symbol.c_name)
        for parameter in parameter_symbols:
            if parameter is not None:
                parameter.c_name = self._local_projection_name(parameter.name)
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
        this_symbol = None
        if member_owner is not None:
            from pcc.frontends.cpp.members import object_pointer_type
            this_symbol = Symbol("this", "value", name.coord,
                                 c_name=self._local_projection_name("this"),
                                 dtype=object_pointer_type(symbol))
            param_decls.insert(0, self._decl(this_symbol.c_name, this_symbol.dtype, name.coord))
        declaration = self._decl(symbol.c_name, result_type, name.coord)
        declaration.type = c_ast.FuncDecl(c_ast.ParamList(param_decls, name.coord),
                                          declaration.type, name.coord)
        if symbol.internal_linkage:
            declaration.storage = ["static"]
        if not definition:
            self._expect(";")
            return declaration
        self.scope = parameter_scope
        previous_return_type = self.return_type
        previous_member, previous_this = self.member_function, self.this_symbol
        self.member_function, self.this_symbol = (symbol if member_owner is not None else None), this_symbol
        self.return_type = result_type
        try:
            body = self._block(new_scope=False)
        finally:
            self.scope = parent
            self.return_type = previous_return_type
            self.member_function, self.this_symbol = previous_member, previous_this
        if member_owner is None and name.value == "main" and not parent.namespace_path():
            # Reaching the end of main is a C++ return of zero, not C's general
            # fallthrough behavior. Appending after a return is harmless AST.
            body.block_items.append(c_ast.Return(c_ast.Constant("int", "0", name.coord),
                                                 name.coord))
        return c_ast.FuncDef(declaration, None, body, name.coord)

    def _block(self, new_scope=True):
        start = self._expect("{")
        parent = self.scope
        if new_scope:
            self.scope = Scope(parent)
        items = []
        try:
            while not self._accept("}"):
                if self._peek().kind == "EOF":
                    self._expect("}")
                item = self._statement()
                if item is not None:
                    items.append(item)
        finally:
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
            try:
                body = self._substatement()
            finally:
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
            return c_ast.Break(token.coord) if token.value == "break" else c_ast.Continue(token.coord)
        if self._accept("return"):
            expression = None if self._peek().value == ";" else self._expression()
            self._expect(";")
            return self._return_statement(expression, token.coord)
        if self._is_declaration(allow_range=for_init):
            dtype = self._type()
            name, dtype = self._declarator(dtype)
            self._check_object_type(dtype, name.coord)
            # The point of declaration precedes the initializer.
            symbol = self.scope.declare(Symbol(
                name.value, "value", name.coord, dtype=dtype,
                c_name=self._local_projection_name(name.value),
            ))
            init = None
            initializer = None
            if array_type(dtype):
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
                equals = self._accept("=")
                if self._peek().value == "{":
                    initializer_list = parse_initializer_list(self)
                    init = lower_aggregate_initializer(
                        dtype, initializer_list, self.types, self._convert,
                    )
                elif equals is not None or self._peek().value == "(":
                    coord = equals.coord if equals is not None else self._peek().coord
                    raise CppDiagnostic("cpp.unsupported.class-initialization", "class copy/direct initialization is not implemented", coord)
                else:
                    init = default_initialize(dtype, name.coord)
                self._expect(";")
                symbol.jump_entry = "class-default" if init is None else "initialized"
                return self._decl(symbol.c_name, dtype, name.coord, init)
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
                return Pattern("decl", name.coord, symbol,
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
        return expression.node

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
        return c_ast.ExprList([expression.node for expression in expressions], coord)

    def _for_statement(self):
        start = self._expect("for")
        self._expect("(")
        parent = self.scope
        self.scope = Scope(parent)
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
            try:
                body = self._substatement(for_scope=self.scope)
            finally:
                self.loop_depth -= 1
        finally:
            self.scope = parent
        if self.template_function is not None:
            return Pattern("for", start.coord, children=[init, condition, step, body])
        # C's For owner scopes only its body, not its init-declaration. A real
        # compound gives the header its own lowering scope without changing C.
        return c_ast.Compound([c_ast.For(init, None if condition is None else condition.node,
                                         step, body, start.coord)], start.coord)

    def _do_while_statement(self):
        start = self._expect("do")
        self.loop_depth += 1
        try:
            body = self._substatement()
        finally:
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
        try:
            statement = (self._block(new_scope=False)
                         if for_scope is not None and self._peek().value == "{"
                         else self._statement())
        finally:
            self.scope = parent
        if statement is None:
            return Pattern("empty", coord) if self.template_function is not None else c_ast.EmptyStatement(coord)
        return statement

    def _check_object_type(self, dtype, coord):
        require_complete_array(dtype, coord)
        require_complete(dtype, coord)
        if is_enum_type(dtype) and not unqualified(dtype).complete:
            raise CppDiagnostic("cpp.incomplete-enum", "an object requires a complete enum type", coord)
        if unqualified(dtype) == "void":
            raise CppDiagnostic("cpp.invalid-object-type", "an object cannot have type void", coord)

    def _return_statement(self, expression, coord):
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
            return c_ast.Return(None, coord)
        if result_type == "void":
            # Existing C lowering evaluates node.expr before ret void. Keep the
            # checked void expression intact so calls and other effects survive.
            return c_ast.Return(expression.node, coord)
        return c_ast.Return(self._convert(expression, self.return_type, coord), coord)

    def _require_value(self, expression, allow_void=False, allow_class=False):
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

    def _binary(self, minimum):
        lhs = self._unary()
        while _BINARY_PRECEDENCE.get(self._peek().value, 0) >= minimum:
            token = self.cursor.take()
            rhs = self._binary(_BINARY_PRECEDENCE[token.value] + 1)
            lhs = self._binary_expression(token.value, lhs, rhs, token.coord)
        return lhs

    def _binary_expression(self, operator, lhs, rhs, coord):
        lhs, rhs = self._decay_array(lhs), self._decay_array(rhs)
        if is_class_type(lhs.dtype) or is_class_type(rhs.dtype):
            raise CppDiagnostic("cpp.unsupported.class-operators", "class operator lookup/conversion is not implemented", coord)
        self._require_value(lhs)
        self._require_value(rhs)
        left, right = unqualified(lhs.dtype), unqualified(rhs.dtype)
        if any(is_enum_type(t) and t.scoped for t in (left, right)):
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
        pointer_operands = any(pointer_type(e.dtype) or e.dtype == "nullptr" for e in (lhs, rhs))
        if pointer_operands and not logical:
            if operator not in ("==", "!="):
                raise CppDiagnostic("cpp.unsupported.pointer-operators",
                                    "pointer arithmetic and ordering are not implemented", coord)
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
        if self.template_function is not None:
            return Expression(Pattern("binary", coord, operator, [lhs, rhs]), dtype)
        rhs_type = self.types.promote(rhs.dtype) if shift else operand_type
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
        if isinstance(first, PointerType) and rhs.null_pointer:
            return first
        if isinstance(second, PointerType) and lhs.null_pointer:
            return second
        if lhs.null_pointer and rhs.null_pointer:
            return PointerType("void")
        if isinstance(first, PointerType) and isinstance(second, PointerType):
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

    def _unary(self):
        token = self._peek()
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
            member = self._identifier()
            if self.template_function is not None:
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
        address = c_ast.UnaryOp("&", element, coord)
        body = c_ast.StmtExpr(c_ast.Compound(declarations + [address], coord), coord)
        return Expression(c_ast.UnaryOp("*", body, coord), dtype, True,
                          constant_fact=unmodeled(unqualified(dtype), "array element constant proof is not implemented", coord))

    def _increment_expression(self, operator, expression, coord, postfix=False):
        dtype = unqualified(expression.dtype)
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
        if postfix:
            return Expression(c_ast.UnaryOp("p" + operator, expression.node, coord), dtype,
                              constant_fact=fact)
        # Unlike C, builtin prefix ++/-- is an lvalue in C++. Capture its
        # address once, perform the update, and preserve that very address.
        # The outer dereference also composes with checked &* cancellation.
        name = "__pcc_cpp_increment_" + str(self.assignment_index)
        self.assignment_index += 1
        address = (expression.node.expr
                   if isinstance(expression.node, c_ast.UnaryOp) and expression.node.op == "*"
                   else c_ast.UnaryOp("&", expression.node, coord))
        temporary = self._decl(name, PointerType(dtype), coord, address)
        pointer = c_ast.ID(name, coord)
        update = c_ast.UnaryOp(operator, c_ast.UnaryOp("*", pointer, coord), coord)
        address_expression = c_ast.StmtExpr(c_ast.Compound([temporary, update, pointer], coord), coord)
        return Expression(c_ast.UnaryOp("*", address_expression, coord), dtype, True,
                          constant_fact=fact)

    def _unary_expression(self, operator, expression, coord):
        if operator != "&":
            expression = self._decay_array(expression)
        self._require_value(expression, allow_class=operator == "&")
        if operator in ("&", "*"):
            if self.parsing_template:
                raise CppDiagnostic("cpp.unsupported.pointer-templates", "pointer template expressions are not implemented", coord)
            if operator == "&":
                if expression.assignment_result:
                    raise CppDiagnostic("cpp.unsupported.syntax", "assignment-result lvalue lowering", coord)
                if not expression.lvalue:
                    raise CppDiagnostic("cpp.not-lvalue", "address-of requires an object lvalue", coord)
                if pointer_type(expression.dtype):
                    raise CppDiagnostic("cpp.unsupported.multilevel-pointers", "multilevel pointer semantics are not implemented", coord)
                dtype = PointerType(expression.dtype)
            else:
                pointer = unqualified(expression.dtype)
                if not isinstance(pointer, PointerType) or unqualified(pointer.pointee) == "void":
                    raise CppDiagnostic("cpp.invalid-dereference", "dereference requires a pointer to an object", coord)
                dtype = pointer.pointee
            # Do not emit a load through the pointee merely to take its address.
            if operator == "&" and isinstance(expression.node, c_ast.UnaryOp) and expression.node.op == "*":
                return Expression(expression.node.expr, dtype)
            class_glvalue = operator == "*" and is_class_type(dtype)
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
            if (pointer_type(expression.dtype) or expression.dtype == "nullptr") and operator != "!":
                raise CppDiagnostic("cpp.unsupported.pointer-operators", "unary pointer operator is not implemented", coord)
            if operator == "~" and not self.types.integral_operand(expression.dtype):
                raise CppDiagnostic("cpp.invalid-operands", "operator requires an integral operand", coord)
            dtype = "bool" if operator == "!" else self.types.promote(expression.dtype)
        if self.template_function is not None:
            return Expression(Pattern("unary", coord, operator, [expression]), dtype)
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
                          null_pointer=null_pointer, constant_fact=fact)

    def _call_expression(self, group, arguments, coord):
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
            if not any(dependent(t) for t in argument_types):
                dtype = resolve_overload(snapshot, argument_types, self.types, coord, null_constants).dtype
                result = unqualified(dtype)
                nominal = unqualified(result.pointee) if isinstance(result, PointerType) else result
                if contains_array(result):
                    raise CppDiagnostic("cpp.unsupported.array-templates", "array-related template call results are not implemented", coord)
                if is_enum_type(nominal):
                    raise CppDiagnostic("cpp.unsupported.enum-templates",
                                        "enum results in template definitions require dependent lookup", coord)
            return Expression(Pattern("call", coord, snapshot, arguments), dtype)
        selected = resolve_overload(group, argument_types, self.types, coord, null_constants)
        if selected.template_origin is not None:
            origin = selected.template_origin
            entry = self.instance_registry.request(
                (origin, selected.template_arguments), selected, context=coord,
            )
            selected = entry.value
        converted = [self._convert(arg, dtype, coord)
                     for arg, dtype in zip(arguments, selected.parameters)]
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
        if self.member_function is None or self.member_function.class_owner is not owner:
            raise CppDiagnostic("cpp.nonstatic-member-without-object", "a non-static member requires an object", coord)
        symbol = self.this_symbol
        return Expression(c_ast.ID(symbol.c_name, coord), symbol.dtype,
                          constant_fact=not_constant(symbol.dtype, "implicit object parameter", coord))

    def _primary(self):
        token = self.cursor.take()
        if token.value == "this":
            if self.member_function is None:
                raise CppDiagnostic("cpp.this-outside-member", "this requires a non-static member function", token.coord)
            return self._implicit_object(self.member_function.class_owner, token.coord)
        if token.value == "static_cast":
            self._expect("<")
            dtype = self._abstract_pointer_type(self._type())
            self._expect(">")
            self._expect("(")
            expression = self._expression()
            self._expect(")")
            return self._explicit_arithmetic_cast(dtype, expression, token.coord, "static_cast")
        if token.value == "nullptr":
            if self.parsing_template:
                raise CppDiagnostic("cpp.unsupported.pointer-templates", "nullptr template expressions are not implemented", token.coord)
            return self._literal("int", "0", "nullptr", token.coord)
        if token.value == "(":
            expression = self._expression()
            self._expect(")")
            return expression
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
            self._unsupported(token)
        if token.value in TYPE_WORDS or symbol is not None and symbol.kind in ("type", "template-type"):
            dtype = canonical_type([token.value]) if token.value in TYPE_WORDS else symbol.dtype
            if array_type(dtype):
                raise CppDiagnostic("cpp.invalid-array-conversion", "functional conversion to an array is not permitted", token.coord)
            if is_class_type(dtype):
                raise CppDiagnostic("cpp.unsupported.class-initialization", "class temporary materialization is not implemented", token.coord)
            self._expect("(")
            if self._accept(")"):
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
                from pcc.frontends.cpp.members import call_member
                return call_member(self, symbol, self._implicit_object(symbol.class_owner, token.coord), arguments, token.coord)
            if not name.global_scope and len(name.parts) == 1:
                symbol = argument_dependent_lookup(token.value, [arg.dtype for arg in arguments], symbol, token.coord)
            if symbol is None:
                if self.template_function is not None and any(dependent(arg.dtype) for arg in arguments):
                    raise CppDiagnostic("cpp.unsupported.dependent-lookup",
                                        "ADL-only dependent call lookup is not implemented", token.coord)
                raise CppDiagnostic("cpp.undeclared", f"undeclared name '{token.value}'", token.coord)
            return self._call_expression(symbol, arguments, token.coord)
        if symbol is None:
            raise CppDiagnostic("cpp.undeclared", f"undeclared name '{token.value}'", token.coord)
        if symbol.kind == "enumerator":
            return self._enum_constant_expression(symbol.dtype, symbol.enum_value, token.coord)
        if symbol.kind == "member":
            return member_access(self, self._implicit_object(symbol.class_owner, token.coord), "->", token)
        if self.template_function is not None and getattr(symbol, "namespace_object", False):
            raise CppDiagnostic("cpp.unsupported.namespace-object-templates",
                                "template namespace object bindings are not implemented", token.coord)
        if self.template_function is not None:
            return Expression(Pattern("name", token.coord, symbol), symbol.dtype,
                              symbol.kind == "value", symbol)
        fact = self._symbol_constant_fact(symbol, token.coord)
        return Expression(c_ast.ID(symbol.c_name, token.coord), symbol.dtype,
                          symbol.kind == "value", symbol,
                          constant_fact=fact,
                          discarded_known=symbol.kind == "value" and is_class_type(symbol.dtype),
                          discarded_fact=fact)

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

    def _instantiate_expression(self, expression, bindings, environment):
        pattern = expression.node if isinstance(expression, Expression) else expression
        kind, coord = pattern.kind, pattern.coord
        if kind == "literal":
            literal_kind, value, dtype, integer_value = pattern.value
            return self._literal(literal_kind, value, dtype, coord, integer_value)
        if kind == "name":
            symbol = pattern.value
            return environment[id(symbol)]
        arguments = [self._instantiate_expression(child, bindings, environment)
                     for child in pattern.children]
        if kind == "cast":
            dtype = substitute(pattern.value, bindings)
            return Expression(self._convert(arguments[0], dtype, coord, force=True), dtype,
                              constant_fact=converted_fact(self._constant_fact(arguments[0]), dtype,
                                                           self.types, coord))
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
            return c_ast.ExprList([self._instantiate_expression(child, bindings, environment).node
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
            symbol = pattern.value
            dtype = substitute(symbol.dtype, bindings)
            self._check_object_type(dtype, coord)
            environment[id(symbol)] = Expression(c_ast.ID(symbol.c_name, coord), dtype, True)
            init = None
            if pattern.children:
                expression = self._instantiate_expression(pattern.children[0], bindings, environment)
                init = self._convert(expression, dtype, coord)
            return self._decl(symbol.c_name, dtype, coord, init)
        if kind == "return":
            expression = (self._instantiate_expression(pattern.children[0], bindings, environment)
                          if pattern.children else None)
            return self._return_statement(expression, coord)
        return self._instantiate_expression(pattern, bindings, environment).node

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
