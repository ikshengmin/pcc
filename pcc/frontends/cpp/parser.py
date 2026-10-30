"""Handwritten C++ parser with semantic lookup and reversible lookahead.

This increment accepts arithmetic free functions and block statements. It
builds the existing C AST only *after* resolving names and checking supported
semantics. It does not rewrite source text, invoke the C parser, or discard C++
lifetime/exception/template semantics. Those features are explicit errors.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.parse.c_lex import CLexer
from pcc.frontends.c.parse.plyparser import Coord
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.sema import Scope, Symbol, resolve_overload
from pcc.frontends.cpp.types import (
    ArithmeticTypes, TYPE_WORDS, canonical_type, c_type_names,
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
    def __init__(self, node, dtype="int", lvalue=False, symbol=None):
        self.node = node
        self.dtype = dtype
        self.lvalue = lvalue
        self.symbol = symbol


class CppParser:
    def __init__(self, target_triple=None):
        self.types = ArithmeticTypes(target_triple)

    def parse(self, text, filename=""):
        self.scope = Scope()
        self.return_type = None
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
        return token.value in TYPE_WORDS or self.scope.is_type_name(token.value)

    def _check_value_type(self, dtype, token):
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
        if token.value not in TYPE_WORDS:
            self.cursor.take()
            dtype = self.scope.lookup(token.value).dtype
            return self._check_value_type(dtype, token) if check_lowering else dtype
        words = []
        while self._peek().value in TYPE_WORDS:
            words.append(self.cursor.take().value)
        try:
            dtype = canonical_type(words)
        except ValueError as error:
            raise CppDiagnostic("cpp.invalid-type", str(error), token.coord)
        return self._check_value_type(dtype, token) if check_lowering else dtype

    def _alias(self):
        keyword = self.cursor.take()
        if keyword.value == "using":
            name = self._identifier()
            self._expect("=")
            dtype = self._type()
        else:
            dtype = self._type()
            name = self._identifier()
        self._expect(";")
        self.scope.declare(Symbol(name.value, "type", name.coord, dtype=dtype))

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
            try:
                self._type(check_lowering=False)
                self._declarator_name()
            except CppDiagnostic:
                return False
            return self._peek().value in (";", "=", "(", ",", "[")

    @staticmethod
    def _scalar_type(name, dtype, coord):
        return c_ast.TypeDecl(name, [], c_ast.IdentifierType(c_type_names(dtype), coord), coord)

    def _decl(self, name, dtype, coord, init=None):
        return c_ast.Decl(name, [], [], [], self._scalar_type(name, dtype, coord), init, None, coord)

    def _convert(self, expression, dtype, coord, force=False):
        self._require_value(expression)
        if self.types.conversion_rank(expression.dtype, dtype) is None:
            raise CppDiagnostic("cpp.invalid-conversion", "unsupported conversion", coord)
        if expression.dtype == dtype and not force:
            return expression.node
        typename = c_ast.Typename(None, [], self._scalar_type(None, dtype, coord), coord)
        return c_ast.Cast(typename, expression.node, coord)

    def _function(self):
        result_type = self._type()
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
                    dtype = self._type()
                    param = None
                    if self._peek().value not in (",", ")"):
                        param = self._identifier()
                        # A preceding parameter can hide an outer type in a
                        # later parameter declaration, including prototypes.
                        self.scope.declare(Symbol(param.value, "value", param.coord, dtype=dtype))
                    params.append((param, dtype))
                    if self._accept(")"):
                        break
                    self._expect(",")
        finally:
            self.scope = parent
        definition = self._peek().value == "{"
        if name.value == "main" and params:
            self._unsupported(name, "main parameters are not implemented")
        if name.value == "main" and result_type != "int":
            raise CppDiagnostic("cpp.main-return-type", "main must return int", name.coord)
        symbol = self.scope.declare_function(
            Symbol(name.value, "function", name.coord, arity=len(params), dtype=result_type,
                   parameters=tuple(dtype for param, dtype in params)), definition
        )
        param_decls = [self._decl(p.value if p else None, dtype, p.coord if p else name.coord)
                       for p, dtype in params]
        declaration = self._decl(symbol.c_name, result_type, name.coord)
        declaration.type = c_ast.FuncDecl(c_ast.ParamList(param_decls, name.coord),
                                          declaration.type, name.coord)
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
            return c_ast.Return(self._convert(expression, self.return_type, token.coord), token.coord)
        if self._is_declaration():
            dtype = self._type()
            name = self._declarator_name()
            # The point of declaration precedes the initializer.
            self.scope.declare(Symbol(name.value, "value", name.coord, dtype=dtype))
            init = None
            if self._accept("="):
                expression = self._expression()
                self._require_value(expression)
                init = self._convert(expression, dtype, name.coord)
            elif self._accept("("):
                if self._peek().value == ")" or self._is_type(self._peek()):
                    self._unsupported(self._peek(), "block function declarations")
                expression = self._expression()
                self._require_value(expression)
                init = self._convert(expression, dtype, name.coord)
                self._expect(")")
            self._expect(";")
            return self._decl(name.value, dtype, name.coord, init)
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
            return Expression(c_ast.Assignment("=", lhs.node,
                                               self._convert(rhs, lhs.dtype, assignment.coord),
                                               assignment.coord), lhs.dtype)
        return lhs

    def _binary(self, minimum):
        lhs = self._unary()
        while _BINARY_PRECEDENCE.get(self._peek().value, 0) >= minimum:
            token = self.cursor.take()
            rhs = self._binary(_BINARY_PRECEDENCE[token.value] + 1)
            self._require_value(lhs)
            self._require_value(rhs)
            if token.value in ("%", "&", "|", "^") and not (
                    self.types.integral(lhs.dtype) and self.types.integral(rhs.dtype)):
                raise CppDiagnostic("cpp.invalid-operands", "operator requires integral operands",
                                    token.coord)
            dtype = self.types.common(lhs.dtype, rhs.dtype)
            lhs = Expression(c_ast.BinaryOp(token.value, self._convert(lhs, dtype, token.coord),
                                             self._convert(rhs, dtype, token.coord), token.coord), dtype)
        return lhs

    def _unary(self):
        token = self._peek()
        if token.value in ("+", "-", "~"):
            self.cursor.take()
            expression = self._unary()
            self._require_value(expression)
            if token.value == "~" and not self.types.integral(expression.dtype):
                raise CppDiagnostic("cpp.invalid-operands", "operator requires an integral operand",
                                    token.coord)
            dtype = self.types.promote(expression.dtype)
            return Expression(c_ast.UnaryOp(token.value, self._convert(expression, dtype, token.coord),
                                            token.coord), dtype)
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
                dtype, value = self.types.integer_literal(token.value)
            except ValueError as error:
                raise CppDiagnostic("cpp.invalid-literal", str(error), token.coord)
            return Expression(c_ast.Constant("int", value, token.coord), dtype)
        if token.kind in ("FLOAT_CONST", "HEX_FLOAT_CONST"):
            try:
                dtype = self.types.floating_literal_type(token.value)
            except ValueError as error:
                raise CppDiagnostic("cpp.invalid-literal", str(error), token.coord)
            self._check_value_type(dtype, token)
            return Expression(c_ast.Constant("float", token.value, token.coord), dtype)
        if token.value in ("true", "false"):
            value = "1" if token.value == "true" else "0"
            node = self._convert(Expression(c_ast.Constant("int", value, token.coord)),
                                 "bool", token.coord)
            return Expression(node, "bool")
        if self._is_type(token):
            dtype = (canonical_type([token.value]) if token.value in TYPE_WORDS
                     else self.scope.lookup(token.value).dtype)
            self._expect("(")
            if self._accept(")"):
                zero = Expression(c_ast.Constant("int", "0", token.coord))
                return Expression(self._convert(zero, dtype, token.coord, force=True), dtype)
            expression = self._expression()
            self._require_value(expression)
            self._expect(")")
            return Expression(self._convert(expression, dtype, token.coord, force=True), dtype)
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
                    arguments.append(expression)
                    if self._accept(")"):
                        break
                    self._expect(",")
            selected = resolve_overload(symbol, [arg.dtype for arg in arguments], self.types, token.coord)
            converted = [self._convert(arg, dtype, token.coord)
                         for arg, dtype in zip(arguments, selected.parameters)]
            return Expression(c_ast.FuncCall(c_ast.ID(selected.c_name, token.coord),
                                              c_ast.ExprList(converted, token.coord), token.coord),
                              selected.dtype)
        return Expression(c_ast.ID(symbol.c_name, token.coord), symbol.dtype,
                          symbol.kind == "value", symbol)
