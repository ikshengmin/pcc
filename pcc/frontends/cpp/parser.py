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
from pcc.frontends.cpp.types import (
    ArithmeticTypes, ConstType, PointerType, TYPE_WORDS, canonical_type, c_type_names,
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
                       "bitor": "|", "xor": "^", "compl": "~", "not_eq": "!="}


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
                 discarded_node=None, discarded_fact=None):
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


class CppParser:
    def __init__(self, target_triple=None):
        self.types = ArithmeticTypes(target_triple)
        self.instantiating_template = False

    def parse(self, text, filename=""):
        self.scope = Scope()
        self.return_type = None
        self.template_function = None
        self.parsing_template = False
        self.instantiating_template = False
        self.pending_instances = []
        self.loop_depth = 0
        self.assignment_index = 0
        self.cursor = TokenCursor(self._tokenize(text, filename))
        declarations = self._declarations()
        instances = []
        index = 0
        while index < len(self.pending_instances):
            instance, request_coord = self.pending_instances[index]
            instances.append(self._instantiate_body(instance, request_coord))
            index += 1
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

    def _declarations(self, namespace_body=False):
        declarations = []
        while self._peek().kind != "EOF" and not (namespace_body and self._peek().value == "}"):
            if self._accept(";"):
                continue
            value = self._peek().value
            if value == "namespace" or value == "inline":
                declarations.extend(self._namespace())
            elif value in ("class", "struct", "union"):
                declarations.append(parse_class(self))
            elif value == "enum":
                parse_enum(self)
            elif value in ("using", "typedef"):
                self._alias()
            elif value == "template":
                self._template()
            else:
                declarations.append(self._function())
        return declarations

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

    def _type(self, check_lowering=True):
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
            if value in ("const", "volatile"):
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

    def _apply_declarator(self, declaration, dtype, function=False):
        name = declaration.name
        if name.global_scope or len(name.parts) != 1:
            raise CppDiagnostic("cpp.unsupported.qualified-names",
                                "out-of-namespace member declarations are not implemented", name.coord)
        chunks = list(declaration.chunks)
        if function:
            if not chunks or chunks[0].kind != "function":
                feature = "references" if chunks and "reference" in chunks[0].kind else "function-pointers"
                raise CppDiagnostic("cpp.unsupported." + feature, "only free function declarations are implemented", declaration.coord)
            clause = chunks.pop(0)
            if clause.qualifiers or clause.ref_qualifier:
                raise CppDiagnostic("cpp.unsupported.function-declarators", "function cv/ref qualifiers are not implemented", clause.coord)
        for chunk in reversed(chunks):
            if chunk.kind != "pointer":
                feature = "references" if "reference" in chunk.kind else (
                    "arrays-or-lambdas" if chunk.kind == "array" else "function-pointers")
                raise CppDiagnostic("cpp.unsupported." + feature, "declarator semantics are not implemented", chunk.coord)
            dtype = self._pointer(dtype, chunk.token, chunk.qualifiers)
        return name.identifier, dtype

    def _declarator(self, dtype):
        declaration = DeclaratorParser(self.cursor, self._identifier).parse(parse_suffixes=False)
        return self._apply_declarator(declaration, dtype)

    def _is_declaration(self):
        if not self._is_type(self._peek()):
            return False
        with self.cursor.tentative():
            try:
                dtype = self._type(check_lowering=False)
            except CppDiagnostic:
                return True
            pointer_prefix = self._peek().value in ("*", "&", "&&")
            try:
                self._declarator(dtype)
            except CppDiagnostic:
                # A type followed by a failed declarator is not permission to
                # reinterpret unsupported pointer/reference syntax as a value.
                return pointer_prefix
            return self._peek().value in (";", "=", "(", ",", "[", "{")

    @staticmethod
    def _scalar_type(name, dtype, coord):
        if is_class_type(dtype):
            return c_class_type(name, dtype, coord)
        qualifiers = ["const"] if isinstance(dtype, ConstType) else []
        dtype = unqualified(dtype)
        if isinstance(dtype, PointerType):
            return c_ast.PtrDecl(qualifiers, CppParser._scalar_type(name, dtype.pointee, coord), coord)
        return c_ast.TypeDecl(name, qualifiers, c_ast.IdentifierType(c_type_names(dtype), coord), coord)

    def _decl(self, name, dtype, coord, init=None):
        return c_ast.Decl(name, [], [], [], self._scalar_type(name, dtype, coord), init, None, coord)

    def _convert(self, expression, dtype, coord, force=False, contextual=False):
        dtype = unqualified(dtype)
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

    def _function(self, template_parameters=(), declaration_scope=None):
        result_type = self._type()
        params = []
        parameter_symbols = []
        parent = self.scope
        parameter_scope = Scope(parent)
        self.scope = parameter_scope

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
            declarator = DeclaratorParser(self.cursor, self._identifier, parameter_clause).parse()
            name, result_type = self._apply_declarator(declarator, result_type, function=True)
        finally:
            self.scope = parent
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
        definition = self._peek().value == "{"
        if name.value == "main" and not parent.namespace_path() and params:
            self._unsupported(name, "main parameters are not implemented")
        if name.value == "main" and not parent.namespace_path() and result_type != "int":
            raise CppDiagnostic("cpp.main-return-type", "main must return int", name.coord)
        if name.value == "main" and not parent.namespace_path() and template_parameters:
            raise CppDiagnostic("cpp.main-template", "main cannot be a template", name.coord)
        new_symbol = Symbol(name.value, "function", name.coord, arity=len(params), dtype=result_type,
                            parameters=tuple(unqualified(dtype) for param, dtype in params))
        new_symbol.template_parameters = template_parameters
        registry = self.scope if declaration_scope is None else declaration_scope
        symbol = registry.declare_function(new_symbol, definition)
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
        param_decls = [self._decl(p.value if p else None, dtype, p.coord if p else name.coord)
                       for p, dtype in params]
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
        self.return_type = result_type
        try:
            for param, dtype in params:
                if param is None:
                    self._unsupported(name, "unnamed definition parameters")
            body = self._block(new_scope=False)
        finally:
            self.scope = parent
            self.return_type = previous_return_type
        if name.value == "main" and not parent.namespace_path():
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

    def _statement(self):
        token = self._peek()
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
            if not self.loop_depth:
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
        if self._is_declaration():
            dtype = self._type()
            name, dtype = self._declarator(dtype)
            self._check_object_type(dtype, name.coord)
            # The point of declaration precedes the initializer.
            symbol = self.scope.declare(Symbol(name.value, "value", name.coord, dtype=dtype))
            init = None
            initializer = None
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
                return self._decl(name.value, dtype, name.coord, init)
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
            self._expect(";")
            if self.template_function is not None:
                return Pattern("decl", name.coord, symbol,
                               [] if initializer is None else [initializer])
            if (isinstance(dtype, ConstType) and (self.types.integral(dtype) or is_enum_type(dtype))
                    and initializer is not None):
                symbol.constant_fact = converted_fact(
                    self._constant_fact(initializer), dtype, self.types, name.coord,
                )
            symbol.initialization_complete = True
            return self._decl(name.value, dtype, name.coord, init)
        expression = self._expression()
        self._require_value(expression, allow_void=True)
        self._expect(";")
        return expression.node

    def _condition(self):
        self._expect("(")
        if self._is_declaration():
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

    def _substatement(self):
        # C++ selection/iteration substatements have block scope even without
        # braces. In particular an unbraced declaration must not leak its name.
        parent = self.scope
        self.scope = Scope(parent)
        coord = self._peek().coord
        try:
            statement = self._statement()
        finally:
            self.scope = parent
        if statement is None:
            return Pattern("empty", coord) if self.template_function is not None else c_ast.EmptyStatement(coord)
        return statement

    def _check_object_type(self, dtype, coord):
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
                                  "&=", "^=", "|=", "and_eq", "or_eq", "xor_eq"):
            raise CppDiagnostic("cpp.unsupported.compound-assignment",
                                "compound assignment semantics are not implemented", self._peek().coord)
        assignment = self._accept("=")
        if assignment:
            rhs = self._expression()
            return self._assignment_expression(lhs, rhs, assignment.coord)
        return lhs

    def _assignment_expression(self, lhs, rhs, coord):
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
                if isinstance(first.pointee, ConstType) or isinstance(second.pointee, ConstType):
                    pointee = qualify_const(pointee)
                return PointerType(pointee)
        raise CppDiagnostic("cpp.invalid-operands", "incompatible operands for pointer equality", coord)

    def _unary(self):
        token = self._peek()
        if token.value in ("+", "-", "~", "!", "&", "*"):
            self.cursor.take()
            expression = self._unary()
            return self._unary_expression(token.value, expression, token.coord)
        expression = self._primary()
        while self._peek().value in (".", "->"):
            operator = self.cursor.take()
            member = self._identifier()
            if self.template_function is not None:
                raise CppDiagnostic("cpp.unsupported.dependent-lookup", "template member lookup is not implemented", operator.coord)
            expression = member_access(self, expression, operator.value, member)
        return expression

    def _unary_expression(self, operator, expression, coord):
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
                              discarded_fact=self._constant_fact(expression) if class_glvalue else None)
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
                if is_enum_type(nominal):
                    raise CppDiagnostic("cpp.unsupported.enum-templates",
                                        "enum results in template definitions require dependent lookup", coord)
            return Expression(Pattern("call", coord, snapshot, arguments), dtype)
        selected = resolve_overload(group, argument_types, self.types, coord, null_constants)
        if selected.template_origin is not None:
            origin = selected.template_origin
            cached = origin.instances.get(selected.template_arguments)
            if cached is None:
                origin.instances[selected.template_arguments] = selected
                self.pending_instances.append((selected, coord))
            else:
                selected = cached
        converted = [self._convert(arg, dtype, coord)
                     for arg, dtype in zip(arguments, selected.parameters)]
        dtype = unqualified(selected.dtype)
        nominal = unqualified(dtype.pointee) if isinstance(dtype, PointerType) else dtype
        if self.instantiating_template and is_enum_type(nominal):
            raise CppDiagnostic("cpp.unsupported.enum-templates",
                                "enum results in template instantiation require dependent lookup", coord)
        return Expression(c_ast.FuncCall(c_ast.ID(selected.c_name, coord),
                                          c_ast.ExprList(converted, coord), coord), dtype,
                          constant_fact=not_constant(dtype, "call to a non-constexpr function", coord))

    def _primary(self):
        token = self.cursor.take()
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
                and any(candidate.owner is not None and not candidate.owner.namespace_path()
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
            raise CppDiagnostic("cpp.nonstatic-member-without-object",
                                "a non-static data member value requires an object", token.coord)
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
        if kind == "assignment":
            return self._assignment_expression(arguments[0], arguments[1], coord)
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
            parameters.append(self._decl(symbol.name, dtype, symbol.coord))
            environment[id(symbol)] = Expression(c_ast.ID(symbol.name, symbol.coord), dtype, True)
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
