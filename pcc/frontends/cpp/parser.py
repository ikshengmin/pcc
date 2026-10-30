"""Handwritten C++ parser with semantic lookup and reversible lookahead.

This increment accepts arithmetic free functions with arithmetic/void results,
deduced arithmetic function templates and scalar control-flow statements. It builds the existing C AST only *after*
resolving names and checking supported semantics. It does not rewrite source
text, invoke the C parser, or discard missing lifetime/exception/template
semantics. Unimplemented features remain explicit errors.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.parse.c_lex import CLexer
from pcc.frontends.c.parse.plyparser import Coord
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.declarators import parse_plain_name
from pcc.frontends.cpp.sema import Scope, Symbol, resolve_overload
from pcc.frontends.cpp.types import (
    ArithmeticTypes, TYPE_WORDS, canonical_type, c_type_names,
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
        self.template_function = None
        self.pending_instances = []
        self.loop_depth = 0
        self.cursor = TokenCursor(self._tokenize(text, filename))
        declarations = []
        while self._peek().kind != "EOF":
            if self._accept(";"):
                continue
            if self._peek().value in ("using", "typedef"):
                self._alias()
                continue
            if self._peek().value == "template":
                self._template()
                continue
            declarations.append(self._function())
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
                if decl.name not in names:
                    prototypes.append(decl)
                    names.add(decl.name)
            return c_ast.FileAST(prototypes + declarations + instances)
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
        return token.value in TYPE_WORDS or self.scope.is_type_name(token.value)

    def _check_value_type(self, dtype, token):
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
            name = self._declarator_name()
        self._expect(";")
        self.scope.declare(Symbol(name.value, "type", name.coord, dtype=dtype))

    def _declarator_name(self):
        return parse_plain_name(self.cursor, self._identifier)

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
                if self.types.conversion_rank(expression.dtype, dtype) is None:
                    raise CppDiagnostic("cpp.invalid-conversion", "unsupported conversion", coord)
            if same_type(expression.dtype, dtype) and not force:
                return expression.node
            return Pattern("cast", coord, dtype, [expression])
        if self.types.conversion_rank(expression.dtype, dtype) is None:
            raise CppDiagnostic("cpp.invalid-conversion", "unsupported conversion", coord)
        if expression.dtype == dtype and not force:
            return expression.node
        typename = c_ast.Typename(None, [], self._scalar_type(None, dtype, coord), coord)
        return c_ast.Cast(typename, expression.node, coord)

    def _template(self):
        keyword = self._expect("template")
        self._expect("<")
        parent = self.scope
        self.scope = Scope(parent)
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

    def _function(self, template_parameters=(), declaration_scope=None):
        result_type = self._type()
        name = self._declarator_name()
        if self.scope.lookup(name.value) is not None and self.scope.lookup(name.value).kind == "template-type":
            raise CppDiagnostic("cpp.template-parameter-shadowing",
                                "function name shadows a template parameter", name.coord)
        self._expect("(")
        params = []
        parameter_symbols = []
        parent = self.scope
        parameter_scope = Scope(parent)
        self.scope = parameter_scope
        try:
            if not self._accept(")"):
                while True:
                    dtype_token = self._peek()
                    dtype = self._type()
                    # Exactly one unnamed void parameter denotes an empty
                    # parameter list, including a typedef naming void. A named
                    # or additional void parameter is never silently removed.
                    if dtype == "void":
                        if not params and self._accept(")"):
                            break
                        raise CppDiagnostic("cpp.invalid-parameter",
                                            "void must be the sole unnamed parameter",
                                            dtype_token.coord)
                    param = None
                    if self._peek().value not in (",", ")"):
                        param = self._declarator_name()
                        # A preceding parameter can hide an outer type in a
                        # later parameter declaration, including prototypes.
                        parameter_symbols.append(self.scope.declare(
                            Symbol(param.value, "value", param.coord, dtype=dtype)))
                    else:
                        parameter_symbols.append(None)
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
        if name.value == "main" and template_parameters:
            raise CppDiagnostic("cpp.main-template", "main cannot be a template", name.coord)
        new_symbol = Symbol(name.value, "function", name.coord, arity=len(params), dtype=result_type,
                            parameters=tuple(dtype for param, dtype in params))
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
        if self.template_function is not None:
            return Pattern("block", start.coord, children=items)
        return c_ast.Compound(items, start.coord)

    def _statement(self):
        token = self._peek()
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
            name = self._declarator_name()
            self._check_object_type(dtype, name.coord)
            # The point of declaration precedes the initializer.
            symbol = self.scope.declare(Symbol(name.value, "value", name.coord, dtype=dtype))
            init = None
            initializer = None
            if self._accept("="):
                expression = self._expression()
                self._require_value(expression)
                init = self._convert(expression, dtype, name.coord)
                initializer = expression
            elif self._accept("("):
                if self._peek().value == ")" or self._is_type(self._peek()):
                    self._unsupported(self._peek(), "block function declarations")
                expression = self._expression()
                self._require_value(expression)
                init = self._convert(expression, dtype, name.coord)
                initializer = expression
                self._expect(")")
            self._expect(";")
            if self.template_function is not None:
                return Pattern("decl", name.coord, symbol,
                               [] if initializer is None else [initializer])
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
        return Expression(self._convert(expression, "bool", expression.node.coord), "bool")

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
        if dtype == "void":
            raise CppDiagnostic("cpp.invalid-object-type", "an object cannot have type void", coord)

    def _return_statement(self, expression, coord):
        if expression is None:
            if not dependent(self.return_type) and self.return_type != "void":
                raise CppDiagnostic("cpp.return-value-required",
                                    "non-void function requires a return expression", coord)
        else:
            self._require_value(expression, allow_void=True)
            if not dependent(self.return_type) and not dependent(expression.dtype):
                if self.return_type == "void" and expression.dtype != "void":
                    raise CppDiagnostic("cpp.return-value-in-void",
                                        "void function cannot return a non-void expression", coord)
                if self.return_type != "void" and expression.dtype == "void":
                    raise CppDiagnostic("cpp.return-void-expression",
                                        "non-void function cannot return a void expression", coord)
        if self.template_function is not None:
            if expression is not None and expression.dtype != "void" and self.return_type != "void":
                self._convert(expression, self.return_type, coord)
            return Pattern("return", coord, children=[] if expression is None else [expression])
        if expression is None:
            return c_ast.Return(None, coord)
        if self.return_type == "void":
            # Existing C lowering evaluates node.expr before ret void. Keep the
            # checked void expression intact so calls and other effects survive.
            return c_ast.Return(expression.node, coord)
        return c_ast.Return(self._convert(expression, self.return_type, coord), coord)

    def _require_value(self, expression, allow_void=False):
        if expression.dtype == "void" and not allow_void:
            raise CppDiagnostic("cpp.void-value", "void expression has no value", expression.node.coord)
        if expression.symbol is not None and expression.symbol.kind == "function":
            self._unsupported(self._peek(), "function-to-pointer conversion")

    def _expression(self):
        lhs = self._binary(1)
        assignment = self._accept("=")
        if assignment:
            rhs = self._expression()
            return self._assignment_expression(lhs, rhs, assignment.coord)
        return lhs

    def _assignment_expression(self, lhs, rhs, coord):
        if (isinstance(lhs.node, c_ast.Assignment)
                or isinstance(lhs.node, Pattern) and lhs.node.kind == "assignment"):
            raise CppDiagnostic("cpp.unsupported.syntax", "assignment-result lvalue lowering", coord)
        if not lhs.lvalue:
            raise CppDiagnostic("cpp.not-lvalue", "assignment requires an lvalue", coord)
        converted = self._convert(rhs, lhs.dtype, coord)
        # Assignment is an lvalue in C++; uses requiring that C difference
        # remain explicitly rejected, including within template patterns.
        if self.template_function is not None:
            return Expression(Pattern("assignment", coord, children=[lhs, rhs]), lhs.dtype)
        return Expression(c_ast.Assignment("=", lhs.node, converted, coord), lhs.dtype)

    def _binary(self, minimum):
        lhs = self._unary()
        while _BINARY_PRECEDENCE.get(self._peek().value, 0) >= minimum:
            token = self.cursor.take()
            rhs = self._binary(_BINARY_PRECEDENCE[token.value] + 1)
            lhs = self._binary_expression(token.value, lhs, rhs, token.coord)
        return lhs

    def _binary_expression(self, operator, lhs, rhs, coord):
        self._require_value(lhs)
        self._require_value(rhs)
        operand_type = None
        dtype = None
        logical = operator in ("&&", "||")
        comparison = operator in _COMPARISONS
        shift = operator in ("<<", ">>")
        if not dependent(lhs.dtype) and not dependent(rhs.dtype):
            if operator in ("%", "&", "|", "^", "<<", ">>") and not (
                    self.types.integral(lhs.dtype) and self.types.integral(rhs.dtype)):
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
        node = c_ast.BinaryOp(operator, self._convert(lhs, operand_type, coord),
                              self._convert(rhs, rhs_type, coord), coord)
        if logical or comparison:
            # C comparisons/logical operators yield int; C++ yields bool. Keep
            # the bool projection explicit for overloads and following C ops.
            node = self._convert(Expression(node, "int"), "bool", coord)
        return Expression(node, dtype)

    def _unary(self):
        token = self._peek()
        if token.value in ("+", "-", "~", "!"):
            self.cursor.take()
            expression = self._unary()
            return self._unary_expression(token.value, expression, token.coord)
        return self._primary()

    def _unary_expression(self, operator, expression, coord):
        self._require_value(expression)
        dtype = None
        if not dependent(expression.dtype):
            if operator == "~" and not self.types.integral(expression.dtype):
                raise CppDiagnostic("cpp.invalid-operands", "operator requires an integral operand", coord)
            dtype = "bool" if operator == "!" else self.types.promote(expression.dtype)
        if self.template_function is not None:
            return Expression(Pattern("unary", coord, operator, [expression]), dtype)
        node = c_ast.UnaryOp(operator, self._convert(expression, dtype, coord), coord)
        if operator == "!":
            node = self._convert(Expression(node, "int"), "bool", coord)
        return Expression(node, dtype)

    def _literal(self, kind, value, dtype, coord):
        if self.template_function is not None:
            return Expression(Pattern("literal", coord, (kind, value, dtype)), dtype)
        return Expression(c_ast.Constant(kind, value, coord), dtype)

    def _call_expression(self, group, arguments, coord):
        argument_types = [arg.dtype for arg in arguments]
        if self.template_function is not None:
            # A later overload declaration must not mutate the definition-point
            # candidate set of an unqualified dependent call (ADL still absent).
            snapshot = Symbol(group.name, "function", group.coord)
            snapshot.overloads = list(group.overloads)
            dtype = None
            if not any(dependent(t) for t in argument_types):
                dtype = resolve_overload(snapshot, argument_types, self.types, coord).dtype
            return Expression(Pattern("call", coord, snapshot, arguments), dtype)
        selected = resolve_overload(group, argument_types, self.types, coord)
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
        return Expression(c_ast.FuncCall(c_ast.ID(selected.c_name, coord),
                                          c_ast.ExprList(converted, coord), coord), selected.dtype)

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
            return self._literal("int", value, dtype, token.coord)
        if token.kind in ("FLOAT_CONST", "HEX_FLOAT_CONST"):
            try:
                dtype = self.types.floating_literal_type(token.value)
            except ValueError as error:
                raise CppDiagnostic("cpp.invalid-literal", str(error), token.coord)
            self._check_value_type(dtype, token)
            return self._literal("float", token.value, dtype, token.coord)
        if token.value in ("true", "false"):
            value = "1" if token.value == "true" else "0"
            node = self._convert(self._literal("int", value, "int", token.coord),
                                 "bool", token.coord)
            return Expression(node, "bool")
        if self._is_type(token):
            dtype = (canonical_type([token.value]) if token.value in TYPE_WORDS
                     else self.scope.lookup(token.value).dtype)
            self._expect("(")
            if self._accept(")"):
                zero = self._literal("int", "0", "int", token.coord)
                return Expression(self._convert(zero, dtype, token.coord, force=True), dtype)
            expression = self._expression()
            self._expect(")")
            return Expression(self._convert(expression, dtype, token.coord, force=True), dtype)
        if token.kind != "ID" or token.value in _KEYWORDS:
            self._unsupported(token)
        symbol = self.scope.lookup(token.value)
        if token.value == "main":
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
            if symbol is None:
                if self.template_function is not None and any(dependent(arg.dtype) for arg in arguments):
                    raise CppDiagnostic("cpp.unsupported.dependent-lookup",
                                        "ADL-only dependent call lookup is not implemented", token.coord)
                raise CppDiagnostic("cpp.undeclared", f"undeclared name '{token.value}'", token.coord)
            return self._call_expression(symbol, arguments, token.coord)
        if symbol is None:
            raise CppDiagnostic("cpp.undeclared", f"undeclared name '{token.value}'", token.coord)
        if self.template_function is not None:
            return Expression(Pattern("name", token.coord, symbol), symbol.dtype,
                              symbol.kind == "value", symbol)
        return Expression(c_ast.ID(symbol.c_name, token.coord), symbol.dtype,
                          symbol.kind == "value", symbol)

    def _instantiate_expression(self, expression, bindings, environment):
        pattern = expression.node if isinstance(expression, Expression) else expression
        kind, coord = pattern.kind, pattern.coord
        if kind == "literal":
            literal_kind, value, dtype = pattern.value
            return self._literal(literal_kind, value, dtype, coord)
        if kind == "name":
            symbol = pattern.value
            return environment[id(symbol)]
        arguments = [self._instantiate_expression(child, bindings, environment)
                     for child in pattern.children]
        if kind == "cast":
            dtype = substitute(pattern.value, bindings)
            return Expression(self._convert(arguments[0], dtype, coord, force=True), dtype)
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
        self.return_type = instance.dtype
        try:
            body = self._instantiate_statement(origin.pattern, instance.template_arguments, environment)
        except CppDiagnostic as error:
            raise CppDiagnostic(error.code, error.message + " (while instantiating " + instance.c_name
                                + " requested at " + str(request_coord) + ")", error.coord)
        finally:
            self.return_type = previous_return
        return c_ast.FuncDef(declaration, None, body, origin.coord)
