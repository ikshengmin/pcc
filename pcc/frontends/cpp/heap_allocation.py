"""Allocating new/delete expressions over the global replaceable allocation functions.

N4659 [expr.new], [expr.delete], [basic.stc.dynamic]/2, [new.delete.single],
[new.delete.array]. Every translation unit implicitly declares the usual
global allocation and deallocation functions; their Itanium names are
_Znwm/_Znam/_ZdlPv/_ZdlPvm/_ZdaPv/_ZdaPvm on LP64 targets. A new-expression
calls `operator new(sizeof(T))` (or `operator new[](n * sizeof(E))`) and then
initializes the object; a delete-expression of a non-null pointer destroys the
object and calls the sized `operator delete` ([expr.delete]/10 selects the
sized usual function for a complete type) or the unsized `operator delete[]`
for an array without a cookie. The allocation functions come from the program
(replacements) or from the owned default definitions linked by the driver.

There is no exception support: an allocation failure terminates inside the
allocation function, and an array size that overflows (or a negative count) is
passed as SIZE_MAX so that allocation fails, as the Itanium ABI permits.
Construction or destruction that could throw would terminate as well, so no
deallocation cleanup path is generated. Class-specific allocation functions,
placement forms other than the reserved one, aligned (over-aligned) forms,
braced initializers, multidimensional and class-element arrays with
lifetimes, and deleting a polymorphic object remain explicit gaps.
"""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.c_abi_layout import c_size_type_name
from pcc.frontends.cpp.classes import is_class_type, require_complete
from pcc.frontends.cpp.constants import not_constant
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import PointerType, array_type, function_type, unqualified, ReferenceType

ALLOCATION_NAMES = {
    ('new', False): '_Znwm', ('new', True): '_Znam',
    ('delete', False): '_ZdlPvm', ('delete', True): '_ZdaPv',
}


def unsupported(coord, message):
    raise CppDiagnostic('cpp.unsupported.allocation', message, coord)


def _require_lp64(parser, coord):
    if c_size_type_name(parser.types.target_triple) != 'unsigned long':
        unsupported(coord, 'allocation function names are modeled for LP64 targets only')


def _prototype(parser, c_name, result, parameters, coord):
    """Publish one C prototype for an implicitly declared global allocation function."""
    published = parser.__dict__.setdefault('allocation_prototypes', set())
    if c_name in published:
        return
    published.add(c_name)
    declaration = parser._decl(c_name, result, coord)
    params = [parser._decl('__pcc_cpp_allocation_' + str(index), dtype, coord)
              for index, dtype in enumerate(parameters)]
    declaration.type = c_ast.FuncDecl(c_ast.ParamList(params, coord), declaration.type, coord)
    parser.class_template_declarations.append(declaration)


def _call(parser, c_name, result, parameters, arguments, coord):
    _prototype(parser, c_name, result, parameters, coord)
    return c_ast.FuncCall(c_ast.ID(c_name, coord), c_ast.ExprList(arguments, coord), coord)


def _cast(parser, node, dtype, coord):
    return c_ast.Cast(c_ast.Typename(None, [], parser._scalar_type(None, dtype, coord), coord), node, coord)


def _no_class_allocation_functions(dtype, global_only, coord):
    owner = unqualified(dtype)
    if not global_only and is_class_type(owner):
        names = owner.definition.scope.names
        if any(name in names for name in ('operator new', 'operator new[]', 'operator delete', 'operator delete[]')):
            raise CppDiagnostic('cpp.unsupported.class-allocation-functions',
                                'class-specific allocation lookup is not implemented', coord)


def check_new_type(parser, dtype, coord, array):
    plain = unqualified(dtype)
    if isinstance(plain, ReferenceType) or plain == 'void' or function_type(plain):
        raise CppDiagnostic('cpp.invalid-new-type', 'new requires a complete object type', coord)
    if array_type(plain):
        unsupported(coord, 'new of an array type-id or multidimensional array-new is not implemented')
    require_complete(plain, coord)
    from pcc.frontends.cpp.virtual_functions import require_concrete
    require_concrete(plain, coord)
    from pcc.frontends.cpp.size_queries import object_layout
    if object_layout(plain, parser.types, coord).alignment > 16:
        unsupported(coord, 'over-aligned allocation requires the align_val_t allocation functions')
    if array and is_class_type(plain):
        from pcc.frontends.cpp.classes import trivial_special_members
        if not trivial_special_members(plain):
            raise CppDiagnostic('cpp.unsupported.class-array-lifetime',
                                'array-new class elements require constructor, destructor and cookie semantics', coord)


def lower_new(parser, dtype, count, kind, arguments, global_only, coord):
    """`new T`, `new T()`, `new T(args)`, `new E[count]` after Sema of the operands.

    ``kind`` is 'default', 'value' or 'direct'; ``count`` is None for a
    non-array new. Returns the typed Expression of the new pointer.
    """
    from pcc.frontends.cpp.parser import Expression
    _require_lp64(parser, coord)
    check_new_type(parser, dtype, coord, count is not None)
    _no_class_allocation_functions(dtype, global_only, coord)
    from pcc.frontends.cpp.size_queries import sizeof_type
    size_type = c_size_type_name(parser.types.target_triple)
    element_size = sizeof_type(dtype, parser.types, coord)
    pointer_type = PointerType(dtype)
    name = parser._fresh_projection_name('__pcc_cpp_new_')
    items = []
    if count is None:
        size = parser._sizeof_result(element_size, coord).node
        raw = _call(parser, ALLOCATION_NAMES[('new', False)], PointerType('void'), (size_type,), [size], coord)
    else:
        if kind != 'default':
            unsupported(coord, 'initialized array-new is not implemented')
        if not parser.types.integral_operand(count.dtype):
            raise CppDiagnostic('cpp.invalid-new-array-bound', 'an array-new bound must have integral or unscoped enum type',
                                coord)
        from pcc.frontends.cpp.types import is_enum_type
        count_base = unqualified(count.dtype)
        count_base = count_base.underlying if is_enum_type(count_base) else count_base
        count_name = parser._fresh_projection_name('__pcc_cpp_new_count_')
        items.append(parser._decl(count_name, count.dtype, coord, count.node))
        count_id = c_ast.ID(count_name, coord)
        limit = (1 << parser.types.width(size_type)) - 1
        maximum = parser._sizeof_result(limit // element_size, coord).node
        all_ones = parser._sizeof_result(limit, coord).node
        as_size = _cast(parser, count_id, size_type, coord)
        too_large = c_ast.BinaryOp('>', as_size, maximum, coord)
        if not parser.types.unsigned(count_base):
            too_large = c_ast.BinaryOp('||', c_ast.BinaryOp('<', c_ast.ID(count_name, coord),
                                                             c_ast.Constant('int', '0', coord), coord), too_large, coord)
        # [expr.new]/7: an invalid size makes allocation fail (Itanium: pass SIZE_MAX).
        size = c_ast.TernaryOp(too_large, all_ones,
                               c_ast.BinaryOp('*', _cast(parser, c_ast.ID(count_name, coord), size_type, coord),
                                              parser._sizeof_result(element_size, coord).node, coord), coord)
        raw = _call(parser, ALLOCATION_NAMES[('new', True)], PointerType('void'), (size_type,), [size], coord)
    items.append(parser._decl(name, pointer_type, coord, _cast(parser, raw, pointer_type, coord)))
    pointer = c_ast.ID(name, coord)
    if count is None:
        items.extend(_initialize(parser, dtype, kind, arguments, pointer, coord))
    items.append(c_ast.ID(name, coord))
    node = c_ast.StmtExpr(c_ast.Compound(items, coord), coord)
    return Expression(node, pointer_type, constant_fact=not_constant(pointer_type, 'C++17 new-expression', coord))


def _initialize(parser, dtype, kind, arguments, pointer, coord):
    plain = unqualified(dtype)
    if is_class_type(plain):
        definition = plain.definition
        if definition.constructors.overloads:
            from pcc.frontends.cpp.constructors import constructor_call
            # Only value-initialization adds the zeroing phase before C1.
            initialization = 'value' if kind == 'value' else 'default'
            return [constructor_call(parser, plain, list(arguments), pointer, coord,
                                     initialization_kind=initialization, object_type=dtype)]
        if kind == 'direct':
            unsupported(coord, 'parenthesized aggregate initialization of a new object is not implemented')
        from pcc.frontends.cpp.classes import trivial_special_members
        if not trivial_special_members(plain):
            raise CppDiagnostic('cpp.unsupported.destructor-subobjects',
                                'implicit non-trivial construction of a new object requires special-member synthesis',
                                coord)
        if kind == 'default':
            return []
        from pcc.frontends.cpp.classes import empty_list_initialize
        value = empty_list_initialize(dtype, coord, scope=parser.scope)
        literal = c_ast.CompoundLiteral(c_ast.Typename(None, [], parser._scalar_type(None, plain, coord), coord),
                                        value, coord)
        return [c_ast.Assignment('=', c_ast.UnaryOp('*', pointer, coord), literal, coord)]
    if kind == 'default':
        return []
    if kind == 'value':
        zero = parser._literal('int', '0', 'int', coord, 0)
        value = parser._convert(zero, plain, coord, force=True)
    else:
        if len(arguments) != 1:
            raise CppDiagnostic('cpp.invalid-initializer', 'a scalar new-initializer takes one expression', coord)
        value = parser._convert(arguments[0], plain, coord)
    return [c_ast.Assignment('=', c_ast.UnaryOp('*', pointer, coord), value, coord)]


def lower_delete(parser, expression, global_only, array, coord):
    """`delete p` / `delete[] p` for a pointer to a complete object type."""
    from pcc.frontends.cpp.parser import Expression
    _require_lp64(parser, coord)
    dtype = unqualified(expression.dtype)
    if dtype == 'nullptr':
        return Expression(c_ast.Cast(c_ast.Typename(None, [], c_ast.TypeDecl(None, [], c_ast.IdentifierType(['void'], coord), coord), coord),
                                     c_ast.Constant('int', '0', coord), coord), 'void')
    element = unqualified(dtype.pointee)
    require_complete(element, coord)
    _no_class_allocation_functions(element, global_only, coord)
    from pcc.frontends.cpp.size_queries import object_layout, sizeof_type
    if object_layout(element, parser.types, coord).alignment > 16:
        unsupported(coord, 'over-aligned deallocation requires the align_val_t deallocation functions')
    size_type = c_size_type_name(parser.types.target_triple)
    name = parser._fresh_projection_name('__pcc_cpp_delete_')
    pointer_type = PointerType(dtype.pointee)
    items = [parser._decl(name, pointer_type, coord, expression.node)]
    body = []
    if is_class_type(element):
        definition = element.definition
        if array:
            from pcc.frontends.cpp.classes import trivial_special_members
            if not trivial_special_members(element):
                raise CppDiagnostic('cpp.unsupported.class-array-lifetime',
                                    'array-delete class elements require destructor and cookie semantics', coord)
        else:
            if definition.polymorphic:
                unsupported(coord, 'deleting a polymorphic object requires virtual deleting destructors')
            destructor = definition.destructor
            from pcc.frontends.cpp.member_initialization import requires_destruction
            if destructor is None and requires_destruction(element):
                raise CppDiagnostic('cpp.unsupported.destructor-subobjects',
                                    'implicit member destruction requires special-member synthesis', coord)
            if destructor is not None:
                from pcc.frontends.cpp.destructors import check_usable, request_body
                check_usable(parser.scope, destructor, coord)
                request_body(parser, destructor, coord)
                this = _cast(parser, c_ast.ID(name, coord), PointerType(element), coord)
                body.append(c_ast.FuncCall(c_ast.ID(destructor.c_name, coord), c_ast.ExprList([this], coord), coord))
    void_pointer = _cast(parser, c_ast.ID(name, coord), PointerType('void'), coord)
    if array:
        body.append(_call(parser, ALLOCATION_NAMES[('delete', True)], 'void', (PointerType('void'),),
                          [void_pointer], coord))
    else:
        size = parser._sizeof_result(sizeof_type(element, parser.types, coord), coord).node
        body.append(_call(parser, ALLOCATION_NAMES[('delete', False)], 'void', (PointerType('void'), size_type),
                          [void_pointer, size], coord))
    items.append(c_ast.If(c_ast.BinaryOp('!=', c_ast.ID(name, coord), c_ast.Constant('int', '0', coord), coord),
                          c_ast.Compound(body, coord), None, coord))
    items.append(c_ast.Cast(c_ast.Typename(None, [], c_ast.TypeDecl(None, [], c_ast.IdentifierType(['void'], coord), coord), coord),
                            c_ast.Constant('int', '0', coord), coord))
    return Expression(c_ast.StmtExpr(c_ast.Compound(items, coord), coord), 'void')
