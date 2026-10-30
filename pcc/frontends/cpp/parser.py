"""Handwritten C++ parser with semantic lookup and reversible lookahead.

The first increment accepts int-only free functions and block statements. It
builds the existing C AST only *after* resolving names and checking supported
semantics. It does not rewrite source text, invoke the C parser, or discard C++
lifetime/exception/template semantics. Those features are explicit errors.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.parse.c_lex import CLexer
from pcc.frontends.c.parse.plyparser import Coord
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.sema import Scope, Symbol


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
_BINARY_PRECEDENCE = {"|": 1, "^": 2, "&": 3, "+": 4, "-": 4,
                      "*": 5, "/": 5, "%": 5}


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
    def __init__(self, node, lvalue=False, symbol=None):
        self.node = node
        self.lvalue = lvalue
        self.symbol = symbol


class CppParser:
    def parse(self, text, filename=""):
        self.scope = Scope()
        self.cursor = TokenCursor(self._tokenize(text, filename))
        declarations = []
        while self._peek().kind != "EOF":
            if self._accept(";"):
                continue
            if self._peek().value in ("using", "typedef"):
                self._alias()
                continue
            declarations.append(self._function())
        return c_ast.FileAST(declarations)

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
            tokens.append(CppToken(token.type, token.value,
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
        return token.value == "int" or self.scope.is_type_name(token.value)

    def _type(self):
        token = self._peek()
        if not self._is_type(token):
            if self.scope.lookup(token.value) is not None:
                raise CppDiagnostic("cpp.not-a-type", f"'{token.value}' does not name a type",
                                    token.coord)
            self._unsupported(token, "only int and resolved int aliases are implemented")
        return self.cursor.take()

    def _alias(self):
        keyword = self.cursor.take()
        if keyword.value == "using":
            name = self._identifier()
            self._expect("=")
            self._type()
        else:
            self._type()
            name = self._identifier()
        self._expect(";")
        self.scope.declare(Symbol(name.value, "type", name.coord))

    def _declarator_name(self):
        if self._accept("("):
            token = self._declarator_name()
            self._expect(")")
            return token
        return self._identifier()

    def _is_declaration(self):
        if not self._is_type(self._peek()):
            return False
        with self.cursor.tentative():
            self.cursor.take()
            try:
                self._declarator_name()
            except CppDiagnostic:
                return False
            return self._peek().value in (";", "=", "(", ",", "[")

    @staticmethod
    def _int_type(name, coord):
        return c_ast.TypeDecl(name, [], c_ast.IdentifierType(["int"], coord), coord)

    def _decl(self, name, coord, init=None):
        return c_ast.Decl(name, [], [], [], self._int_type(name, coord), init, None, coord)

    def _function(self):
        self._type()
        name = self._identifier()
        self._expect("(")
        params = []
        parent = self.scope
        parameter_scope = Scope(parent)
        self.scope = parameter_scope
        try:
            if self._accept("void"):
                self._expect(")")
            elif not self._accept(")"):
                while True:
                    self._type()
                    param = None
                    if self._peek().value not in (",", ")"):
                        param = self._identifier()
                        # A preceding parameter can hide an outer type in a
                        # later parameter declaration, including prototypes.
                        self.scope.declare(Symbol(param.value, "value", param.coord))
                    params.append(param)
                    if self._accept(")"):
                        break
                    self._expect(",")
        finally:
            self.scope = parent
        definition = self._peek().value == "{"
        # This small fundamental signature has the standard Itanium encoding.
        # No claim of complete C++ ABI support (in particular not MSVC) is made.
        c_name = name.value if name.value == "main" else (
            "_Z" + str(len(name.value)) + name.value + ("i" * len(params) or "v")
        )
        if name.value == "main" and params:
            self._unsupported(name, "main parameters are not implemented")
        symbol = self.scope.declare_function(
            Symbol(name.value, "function", name.coord, c_name, len(params)), definition
        )
        param_decls = [self._decl(p.value if p else None, p.coord if p else name.coord)
                       for p in params]
        declaration = self._decl(symbol.c_name, name.coord)
        declaration.type = c_ast.FuncDecl(c_ast.ParamList(param_decls, name.coord),
                                          declaration.type, name.coord)
        if not definition:
            self._expect(";")
            return declaration
        self.scope = parameter_scope
        try:
            for param in params:
                if param is None:
                    self._unsupported(name, "unnamed definition parameters")
            body = self._block(new_scope=False)
        finally:
            self.scope = parent
        if name.value == "main":
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
        return c_ast.Compound(items, start.coord)

    def _statement(self):
        token = self._peek()
        if token.value == "{":
            return self._block()
        if self._accept(";"):
            return c_ast.EmptyStatement(token.coord)
        if token.value in ("using", "typedef"):
            self._alias()
            return None
        if self._accept("return"):
            expression = self._expression()
            self._require_value(expression)
            self._expect(";")
            return c_ast.Return(expression.node, token.coord)
        if self._is_declaration():
            self._type()
            name = self._declarator_name()
            # The point of declaration precedes the initializer.
            self.scope.declare(Symbol(name.value, "value", name.coord))
            init = None
            if self._accept("="):
                expression = self._expression()
                self._require_value(expression)
                init = expression.node
            elif self._accept("("):
                if self._peek().value == ")" or self._is_type(self._peek()):
                    self._unsupported(self._peek(), "block function declarations")
                expression = self._expression()
                self._require_value(expression)
                init = expression.node
                self._expect(")")
            self._expect(";")
            return self._decl(name.value, name.coord, init)
        expression = self._expression()
        self._require_value(expression)
        self._expect(";")
        return expression.node

    def _require_value(self, expression):
        if expression.symbol is not None and expression.symbol.kind == "function":
            self._unsupported(self._peek(), "function-to-pointer conversion")

    def _expression(self):
        lhs = self._binary(1)
        assignment = self._accept("=")
        if assignment:
            if isinstance(lhs.node, c_ast.Assignment):
                self._unsupported(assignment, "assignment-result lvalue lowering")
            if not lhs.lvalue:
                raise CppDiagnostic("cpp.not-lvalue", "assignment requires an lvalue",
                                    assignment.coord)
            rhs = self._expression()
            self._require_value(rhs)
            # Assignment is itself an lvalue in C++, unlike C. Nested uses
            # requiring that difference are rejected until explicitly lowered.
            return Expression(c_ast.Assignment("=", lhs.node, rhs.node, assignment.coord))
        return lhs

    def _binary(self, minimum):
        lhs = self._unary()
        while _BINARY_PRECEDENCE.get(self._peek().value, 0) >= minimum:
            token = self.cursor.take()
            rhs = self._binary(_BINARY_PRECEDENCE[token.value] + 1)
            self._require_value(lhs)
            self._require_value(rhs)
            lhs = Expression(c_ast.BinaryOp(token.value, lhs.node, rhs.node, token.coord))
        return lhs

    def _unary(self):
        token = self._peek()
        if token.value in ("+", "-", "~"):
            self.cursor.take()
            expression = self._unary()
            self._require_value(expression)
            return Expression(c_ast.UnaryOp(token.value, expression.node, token.coord))
        return self._primary()

    def _primary(self):
        token = self.cursor.take()
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
                base = {"INT_CONST_DEC": 10, "INT_CONST_OCT": 8,
                        "INT_CONST_HEX": 16, "INT_CONST_BIN": 2}[token.kind]
                value = int(token.value, base)
            except ValueError:
                self._unsupported(token, "integer suffixes and user-defined literals")
            if value > 2147483647:
                self._unsupported(token, "literal requires a type wider than int")
            return Expression(c_ast.Constant("int", str(value), token.coord))
        if self._is_type(token):
            self._expect("(")
            if self._accept(")"):
                return Expression(c_ast.Constant("int", "0", token.coord))
            expression = self._expression()
            self._require_value(expression)
            self._expect(")")
            typename = c_ast.Typename(None, [], self._int_type(None, token.coord), token.coord)
            return Expression(c_ast.Cast(typename, expression.node, token.coord))
        if token.kind != "ID" or token.value in _KEYWORDS:
            self._unsupported(token)
        symbol = self.scope.lookup(token.value)
        if symbol is None:
            raise CppDiagnostic("cpp.undeclared", f"undeclared name '{token.value}'", token.coord)
        if token.value == "main":
            raise CppDiagnostic("cpp.main-use", "C++ does not permit use of main", token.coord)
        if self._accept("("):
            if symbol.kind != "function":
                raise CppDiagnostic("cpp.not-callable", f"'{token.value}' is not callable", token.coord)
            arguments = []
            if not self._accept(")"):
                while True:
                    expression = self._expression()
                    self._require_value(expression)
                    arguments.append(expression.node)
                    if self._accept(")"):
                        break
                    self._expect(",")
            if len(arguments) != symbol.arity:
                raise CppDiagnostic("cpp.call-arity", "argument count does not match declaration",
                                    token.coord)
            return Expression(c_ast.FuncCall(c_ast.ID(symbol.c_name, token.coord),
                                              c_ast.ExprList(arguments, token.coord), token.coord))
        return Expression(c_ast.ID(symbol.c_name, token.coord), symbol.kind == "value", symbol)
