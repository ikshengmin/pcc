"""Declaration-bound GNU statement-expression type queries, without emission.

Uses the C generator's lexical maps and existing scalar/registered type owners.
Variable array query shapes are not runtime pointer lowering capabilities.
"""
from copy import copy

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.codegen.c_declaration_state import CodegenError
from pcc.frontends.c.codegen.c_scope_context import (
    TypedefDefinitionScope,
    UnevaluatedDeclarationScope,
)
from pcc.ir.compat import ir_c as ir


class UnevaluatedVLAType(ir.Type):
    """Retain the actual array identity/bound; never fabricate a static extent."""
    def __init__(self, element, declarator, binding=None):
        self.element = element
        self.declarator = declarator
        self.binding = binding

    def __str__(self):
        return 'c.variable-array.query'

    def __eq__(self, other):
        return self is other

    __hash__ = object.__hash__


class CUnevaluatedTypeMixin:
    def _unevaluated_declaration_scope(self):
        return UnevaluatedDeclarationScope(self)

    def _query_typedef_definition_scope(self, name):
        """Resolve an existing alias in its declaring scope, not a later shadow."""
        return TypedefDefinitionScope(self, name)

    def _freeze_query_declarator(self, node, seen=()):
        """Bind typedef use at its declaration point, retaining nominal nodes."""
        if isinstance(node, c_ast.TypeDecl):
            inner = node.type
            if isinstance(inner, c_ast.IdentifierType) and len(inner.names) == 1:
                name = inner.names[0]
                alias = self._lookup_typedef_ast_type(name)
                if alias is not None:
                    if name in seen:
                        raise CodegenError('cyclic typedef in unevaluated declaration')
                    with self._query_typedef_definition_scope(name):
                        result = self._freeze_query_declarator(alias, seen + (name,))
                    if hasattr(result, 'quals'):
                        result.quals = list(dict.fromkeys(list(result.quals or []) + list(node.quals or [])))
                    return result
            result = copy(node)
            # Struct/union/enum nodes retain their nominal identity; no layout
            # owner is invoked and no replacement nominal type is synthesized.
            result.type = inner
            return result
        if isinstance(node, (c_ast.PtrDecl, c_ast.ArrayDecl, c_ast.FuncDecl)):
            result = copy(node)
            result.type = self._freeze_query_declarator(node.type, seen)
            if isinstance(node, c_ast.ArrayDecl) and node.dim is not None:
                self._validate_query_expression(node.dim)
                if self._query_bound_is_constant(node.dim):
                    # Canonicalize a proved extent while its declaration scope
                    # is active; a later same-named object cannot rebind it.
                    result.dim = c_ast.Constant('int', str(int(self._eval_const_expr(node.dim))), coord=node.dim.coord)
            if isinstance(node, c_ast.FuncDecl) and node.args is not None:
                result.args = copy(node.args)
                result.args.params = []
                for parameter in node.args.params:
                    item = copy(parameter)
                    if hasattr(parameter, 'type'):
                        item.type = self._freeze_query_declarator(parameter.type, seen)
                    result.args.params.append(item)
            return result
        return node

    def _resolve_query_declarator(self, node):
        if isinstance(node, (c_ast.TypeDecl, c_ast.Typename)):
            return self._resolve_query_declarator(node.type)
        if isinstance(node, c_ast.IdentifierType):
            if len(node.names) == 1 and '__typedef_' + node.names[0] in self.env:
                name = node.names[0]
                with self._query_typedef_definition_scope(name):
                    bound = self.env['__typedef_' + name]
                    if isinstance(bound, ir.Type):
                        return bound
                    if isinstance(bound, str) and bound.startswith('__struct_'):
                        if bound not in self.env:
                            raise CodegenError('unresolved nominal typedef in unevaluated query')
                        return self.env[bound][0]
                    declaration = self._lookup_typedef_ast_type(name)
                    if declaration is not None:
                        return self._resolve_query_declarator(declaration)
                    return self._get_ir_type(node.names)
            return self._get_ir_type(node.names)
        if isinstance(node, c_ast.PtrDecl):
            pointee = self._resolve_query_declarator(node.type)
            if isinstance(pointee, ir.VoidType):
                return ir.PointerType(ir.IntType(8))
            return ir.PointerType(pointee)
        if isinstance(node, c_ast.ArrayDecl):
            element = self._resolve_query_declarator(node.type)
            if isinstance(element, (ir.VoidType, ir.FunctionType)):
                raise CodegenError('array element cannot be void or a function')
            if node.dim is None:
                raise CodegenError('unevaluated incomplete array declaration requires a bound')
            self._validate_query_expression(node.dim)
            bound_type = self._infer_sizeof_operand_ir_type(node.dim)
            if not isinstance(bound_type, ir.IntType):
                raise CodegenError('array bound requires an integer type')
            if not self._query_bound_is_constant(node.dim):
                return UnevaluatedVLAType(element, node)
            bound = self._eval_const_expr(node.dim)
            if not isinstance(bound, int) or bound < 0:
                raise CodegenError('array bound must be nonnegative')
            if isinstance(element, UnevaluatedVLAType):
                return UnevaluatedVLAType(element, node)
            return ir.ArrayType(element, bound)
        if isinstance(node, c_ast.FuncDecl):
            result = self._resolve_query_declarator(node.type)
            if isinstance(result, (ir.ArrayType, UnevaluatedVLAType, ir.FunctionType)):
                raise CodegenError('function cannot return an array or function')
            params = []
            variadic = node.args is None
            for parameter in (node.args.params if node.args is not None else []):
                if isinstance(parameter, c_ast.EllipsisParam):
                    variadic = True
                    continue
                dtype = self._resolve_query_declarator(parameter.type)
                if isinstance(dtype, ir.VoidType) and len(node.args.params) == 1:
                    continue
                params.append(self._decay_ir_type(dtype))
            return ir.FunctionType(result, params, var_arg=variadic)
        if isinstance(node, (c_ast.Struct, c_ast.Union)):
            if node.decls is not None:
                raise CodegenError('fresh aggregate definitions in unevaluated query require pure layout binding')
            key = self._tag_type_key(node.name)
            if key not in self.env:
                raise CodegenError('unresolved aggregate in unevaluated query')
            return self.env[key][0]
        if isinstance(node, c_ast.Enum):
            if node.values is not None:
                raise CodegenError('fresh enum definitions in unevaluated query require pure enum binding')
            key = self._enum_tag_key(node.name)
            if key not in self.env:
                raise CodegenError('unresolved enum in unevaluated query')
            return ir.IntType(32)
        raise CodegenError('unsupported unevaluated declaration type: ' + type(node).__name__)

    def _query_bound_is_constant(self, node):
        """Classify before using the existing ICE evaluator; never probe emitters."""
        if isinstance(node, c_ast.Constant):
            return node.type in ('int', 'char', 'float', 'double')
        if isinstance(node, c_ast.ID):
            _dtype, binding = self.lookup(node.name)
            return isinstance(binding, ir.values.Constant)
        if isinstance(node, c_ast.UnaryOp):
            if node.op in ('sizeof', '_Alignof', '__alignof', '__alignof__'):
                dtype = (self._resolve_query_declarator(node.expr.type)
                         if isinstance(node.expr, c_ast.Typename)
                         else self._infer_sizeof_operand_ir_type(node.expr))
                return not isinstance(dtype, UnevaluatedVLAType)
            return node.op in ('+', '-', '~', '!') and self._query_bound_is_constant(node.expr)
        if isinstance(node, c_ast.BinaryOp):
            return self._query_bound_is_constant(node.left) and self._query_bound_is_constant(node.right)
        if isinstance(node, c_ast.Cast):
            dtype = self._resolve_query_declarator(node.to_type.type)
            return isinstance(dtype, ir.IntType) and self._query_bound_is_constant(node.expr)
        if isinstance(node, c_ast.TernaryOp):
            return (self._query_bound_is_constant(node.cond)
                    and self._query_bound_is_constant(node.iftrue if node.iftrue is not None else node.cond)
                    and self._query_bound_is_constant(node.iffalse))
        if isinstance(node, c_ast.GenericSelection):
            selected = self._select_generic_association(node)
            return selected is not None and self._query_bound_is_constant(selected)
        return False

    def _validate_query_expression(self, node):
        """Resolve expression names/types, but never execute their operations."""
        if node is None:
            return
        if isinstance(node, c_ast.StmtExpr):
            self._infer_unevaluated_statement_expression(node)
            return
        if isinstance(node, c_ast.ID):
            self._infer_sizeof_operand_ir_type(node)
            return
        if isinstance(node, c_ast.UnaryOp) and node.op == '&':
            self._infer_unevaluated_address_pointee(node.expr)
            return
        if isinstance(node, c_ast.FuncCall):
            self._validate_query_expression(node.name)
            for argument in (node.args.exprs if node.args is not None else []):
                self._validate_query_expression(argument)
            dtype = self._infer_sizeof_operand_ir_type(node.name)
            if isinstance(dtype, ir.PointerType):
                dtype = dtype.pointee
            if not isinstance(dtype, ir.FunctionType):
                raise CodegenError('called expression does not have function type')
            from pcc.frontends.c.codegen.c_codegen import _validate_call_arity
            _validate_call_arity(dtype, len(node.args.exprs if node.args is not None else []))
            return
        if isinstance(node, c_ast.StructRef):
            self._validate_query_expression(node.name)
            # The field identifier belongs to its aggregate, not this scope.
            base = self._infer_sizeof_operand_ir_type(node.name)
            if node.type == '->':
                base = self._decay_ir_type(base)
                if not isinstance(base, ir.PointerType):
                    raise CodegenError('member arrow requires a pointer')
                base = base.pointee
            if self._aggregate_field_path(base, node.field.name) is None:
                raise CodegenError('unknown aggregate member in unevaluated expression')
            return
        if isinstance(node, c_ast.Typename):
            self._resolve_query_declarator(node.type)
            return
        if isinstance(node, c_ast.CompoundLiteral):
            # A nested compound literal owns its own initializer target type,
            # including fixed/inferred array designators. Merely walking its
            # initializer values misses names used in designator expressions.
            self._generic_expr_type_key(node, value_context=False)
            return
        if isinstance(node, c_ast.NamedInitializer):
            self._validate_query_expression(node.expr)
            return
        for _name, child in node.children():
            self._validate_query_expression(child)

    def _bind_query_statement(self, node):
        if node is None:
            return
        if isinstance(node, (c_ast.Decl, c_ast.Typedef)):
            self._bind_unevaluated_declaration(node)
        elif isinstance(node, c_ast.DeclList):
            for declaration in node.decls:
                self._bind_unevaluated_declaration(declaration)
        elif isinstance(node, c_ast.Compound):
            with self._unevaluated_declaration_scope():
                for item in node.block_items or []:
                    self._bind_query_statement(item)
        elif isinstance(node, c_ast.For):
            with self._unevaluated_declaration_scope():
                self._bind_query_statement(node.init)
                self._validate_query_expression(node.cond)
                self._validate_query_expression(node.next)
                self._bind_query_statement(node.stmt)
        elif isinstance(node, (c_ast.If, c_ast.While, c_ast.DoWhile, c_ast.Switch)):
            self._validate_query_expression(node.cond)
            if isinstance(node, c_ast.If):
                self._bind_query_statement(node.iftrue)
                self._bind_query_statement(node.iffalse)
            else:
                self._bind_query_statement(node.stmt)
        elif isinstance(node, c_ast.Label):
            self._bind_query_statement(node.stmt)
        elif isinstance(node, (c_ast.Case, c_ast.Default)):
            if isinstance(node, c_ast.Case):
                self._validate_query_expression(node.expr)
            for item in node.stmts or []:
                self._bind_query_statement(item)
        elif isinstance(node, c_ast.StaticAssert):
            self._validate_query_expression(node.cond)
            if not self._query_bound_is_constant(node.cond):
                raise CodegenError('static assertion requires a constant expression')
            if not self._eval_const_expr(node.cond):
                raise CodegenError('static assertion failed in unevaluated block')
        elif isinstance(node, c_ast.Return):
            self._validate_query_expression(node.expr)
        elif isinstance(node, (c_ast.Goto, c_ast.Break, c_ast.Continue, c_ast.EmptyStatement)):
            return
        else:
            self._validate_query_expression(node)

    def _bind_unevaluated_declaration(self, node):
        dtype = self._resolve_query_declarator(node.type)
        declarator = self._freeze_query_declarator(node.type)
        if isinstance(node, c_ast.Typedef):
            self._record_typedef_ast_type(node.name, declarator)
            self.define('__typedef_' + node.name, dtype)
        else:
            if node.name is None:
                raise CodegenError('unevaluated declaration requires an existing named type')
            if isinstance(dtype, ir.VoidType):
                raise CodegenError('an object cannot have void type')
            self._record_decl_ast_type(node.name, declarator)
            self.define(node.name, (dtype, None))
            self._validate_query_expression(node.init)
        # Declared initializer names/types are checked, but no initializer is
        # executed or lowered to storage by this query-only owner.

    def _infer_unevaluated_statement_expression(self, node, address_context=False):
        with self._unevaluated_declaration_scope():
            items = list(getattr(node.stmt, 'block_items', None) or [])
            for item in items:
                self._bind_query_statement(item)
            if not items or not self._is_expression_node(items[-1]):
                raise CodegenError('unevaluated statement expression requires a final expression')
            final = items[-1]
            if address_context:
                return self._infer_unevaluated_address_pointee(final)
            return self._infer_sizeof_operand_ir_type(final)

    def _infer_unevaluated_address_pointee(self, node):
        if isinstance(node, c_ast.StmtExpr):
            return self._infer_unevaluated_statement_expression(node, address_context=True)
        if isinstance(node, c_ast.GenericSelection):
            self._validate_query_expression(node)
            selected = self._select_generic_association(node)
            if selected is None:
                raise CodegenError('no matching association in unevaluated address query')
            return self._infer_unevaluated_address_pointee(selected)
        self._validate_address_shape(node)
        self._validate_query_expression(node)
        if isinstance(node, c_ast.ID) and node.name not in {'__func__', '__FUNCTION__', '__PRETTY_FUNCTION__'}:
            _dtype, binding = self.lookup(node.name)
            for scope in self.env.maps:
                if node.name in scope:
                    if binding is not None and scope.get('__pcc_enum_constant_' + node.name) is binding:
                        raise CodegenError('address query requires an object or function, not an enumerator')
                    break
            if (binding is not None and not isinstance(binding, ir.Function)
                    and not isinstance(getattr(binding, 'type', None), ir.PointerType)
                    and self._lookup_decl_ast_type(node.name) is None):
                raise CodegenError('address query requires an object or function, not a value constant')
        return self._infer_sizeof_operand_ir_type(node)
