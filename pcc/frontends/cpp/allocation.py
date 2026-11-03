"""Reserved nonallocating placement construction, not a heap/EH substitute.

N4659 [expr.new], [new.delete.placement], [class.cdtor], [basic.life].
The reserved (size_t, void*) form returns its pointer and does nothing else.
Other allocation functions retain their own identities and are never redirected
here. Initialization uses the existing complete-object constructor owner.
"""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.c_abi_layout import c_size_type_name
from pcc.frontends.cpp.classes import is_class_type, require_complete
from pcc.frontends.cpp.constants import not_constant
from pcc.frontends.cpp.declarators import _scope_separator
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.sema import resolve_overload
from pcc.frontends.cpp.types import ALLOCATION_OPERATOR_CODES, ConstType, PointerType, array_type, unqualified


OPERATOR_CODES = ALLOCATION_OPERATOR_CODES

def declaration_name(parser):
    if parser._peek().value != 'operator':
        return parser._identifier()
    from pcc.frontends.cpp.parser import CppToken
    token = parser.cursor.take()
    operation = parser.cursor.take()
    if operation.value == '=':
        return CppToken('OPERATOR_FUNCTION_ID', 'operator=', token.coord)
    if operation.value not in ('new', 'delete'):
        parser._unsupported(operation, 'operator-function semantics are not implemented')
    name = 'operator ' + operation.value
    if parser._accept('['):
        parser._expect(']')
        name += '[]'
    return CppToken('OPERATOR_FUNCTION_ID', name, token.coord)


def validate_declaration(parser, name, result, parameters, nonthrowing,
                         defining, member, storage, template_parameters, variadic):
    if name.value == 'operator=':
        if member is None or len(parameters) != 1 or variadic or storage is not None:
            raise CppDiagnostic('cpp.invalid-assignment-operator',
                                'assignment operator requires a non-static member with one parameter', name.coord)
        if defining or template_parameters:
            raise CppDiagnostic('cpp.unsupported.assignment-operators',
                                'assignment operator bodies and calls require overload/lifetime semantics', name.coord)
        return
    if name.value not in OPERATOR_CODES:
        return
    if member is not None:
        raise CppDiagnostic('cpp.unsupported.class-allocation-functions',
                            'class allocation lookup and static operator rules are not implemented', name.coord)
    if parser.scope.namespace_path() or parser.scope.internal_linkage or parser.language_linkage == 'C' or (storage is not None and storage.value != 'extern'):
        raise CppDiagnostic('cpp.invalid-allocation-function',
                            'non-member allocation functions require global C++ linkage', name.coord)
    if template_parameters:
        raise CppDiagnostic('cpp.unsupported.allocation-templates',
                            'allocation function templates require dedicated overload rules', name.coord)
    size = c_size_type_name(parser.types.target_triple)
    allocation = name.value.startswith('operator new')
    expected_result = PointerType('void') if allocation else 'void'
    first = size if allocation else PointerType('void')
    if result != expected_result or not parameters or unqualified(parameters[0]) != first:
        raise CppDiagnostic('cpp.invalid-allocation-function',
                            'allocation/deallocation function has an invalid result or first parameter', name.coord)
    reserved = not variadic and tuple(unqualified(p) for p in parameters) == (first, PointerType('void'))
    if reserved and not nonthrowing:
        raise CppDiagnostic('cpp.invalid-placement-function',
                            'the reserved placement function has a non-throwing exception specification', name.coord)
    if reserved and defining:
        raise CppDiagnostic('cpp.reserved-placement-definition',
                            'a program cannot replace a reserved standard placement function', name.coord)
    if defining:
        raise CppDiagnostic('cpp.unsupported.allocation-function-definitions',
                            'allocation replacement definitions require heap/failure/deallocation semantics', name.coord)


def global_new_ahead(parser):
    with parser.cursor.tentative():
        return _scope_separator(parser.cursor) is not None and parser._peek().value == 'new'


def global_delete_ahead(parser):
    with parser.cursor.tentative():
        return _scope_separator(parser.cursor) is not None and parser._peek().value == 'delete'


def _delete_operand(expression, coord):
    """Check known operand constraints without pretending to own deallocation."""
    from pcc.frontends.cpp.templates import dependent
    from pcc.frontends.cpp.types import FunctionType
    dtype = unqualified(expression.dtype)
    if dependent(dtype):
        return
    if dtype == 'nullptr':
        return
    if is_class_type(dtype):
        raise CppDiagnostic('cpp.unsupported.allocation',
                            'delete class conversions and deallocation lookup are not implemented', coord)
    if (not isinstance(dtype, PointerType) or unqualified(dtype.pointee) == 'void'
            or isinstance(unqualified(dtype.pointee), FunctionType)):
        raise CppDiagnostic('cpp.invalid-delete-operand', 'delete requires a pointer to an object', coord)


def lower_delete(parser, expression, global_only, array, coord):
    """A real instantiated use must retain the owned heap/deallocation boundary."""
    _delete_operand(expression, coord)
    operation = 'delete[]' if array else 'delete'
    raise CppDiagnostic('cpp.unsupported.allocation',
                        operation + ' requires owned deallocation, object lifetime and failure semantics', coord)


def parse_delete(parser):
    """Retain dependent delete syntax/operand identity, never replay source tokens."""
    from pcc.frontends.cpp.parser import Expression
    from pcc.frontends.cpp.templates import Pattern, dependent
    global_only = _scope_separator(parser.cursor) is not None
    token = parser._expect('delete')
    array = parser._accept('[') is not None
    if array:
        parser._expect(']')
    operand = parser._unary()
    parser._require_value(operand, allow_class=True)
    operand = parser._decay_array(operand)
    _delete_operand(operand, token.coord)
    if parser.template_function is not None and dependent(operand.dtype):
        return Expression(Pattern('delete', token.coord, (global_only, array), [operand]), 'void')
    return lower_delete(parser, operand, global_only, array, token.coord)


def _cast(parser, node, dtype, coord):
    return c_ast.Cast(c_ast.Typename(None, [], parser._scalar_type(None, dtype, coord), coord), node, coord)


def parse_new(parser):
    from pcc.frontends.cpp.parser import Expression
    from pcc.frontends.cpp.constructors import constructor_call
    global_only = _scope_separator(parser.cursor) is not None
    token = parser._expect('new')
    coord = token.coord
    if parser.template_function is not None or parser.parsing_template or parser.instantiating_template:
        raise CppDiagnostic('cpp.unsupported.allocation-templates', 'dependent allocation is not implemented', coord)
    if not parser._accept('('):
        raise CppDiagnostic('cpp.unsupported.allocation', 'allocating new requires the owned allocation/failure runtime', coord)
    if parser._is_type(parser._peek()):
        raise CppDiagnostic('cpp.unsupported.new-type-id', 'parenthesized allocation type-ids are not implemented', coord)
    placement = []
    while True:
        expression = parser._expression()
        parser._require_value(expression)
        placement.append(parser._decay_array(expression))
        if parser._accept(')'):
            break
        parser._expect(',')
    dtype = parser._abstract_pointer_type(parser._type(check_lowering=False))
    if parser._peek().value == '[' or array_type(dtype):
        raise CppDiagnostic('cpp.unsupported.array-new', 'array construction, cookies and failure cleanup are not implemented', coord)
    if not is_class_type(dtype):
        raise CppDiagnostic('cpp.unsupported.placement-object-type', 'placement initialization currently requires a complete class constructor', coord)
    require_complete(dtype, coord)
    owner = unqualified(dtype)
    if any(array_type(member.dtype) for member in owner.definition.members):
        raise CppDiagnostic('cpp.unsupported.placement-subobjects', 'placement array subobject lifetime initialization is not implemented', coord)
    if not global_only and 'operator new' in owner.definition.scope.names:
        raise CppDiagnostic('cpp.unsupported.class-allocation-functions', 'class-specific allocation lookup is not implemented', coord)
    group = parser.scope.global_scope().names.get('operator new')
    if group is None or group.kind != 'function':
        raise CppDiagnostic('cpp.undeclared-allocation-function', 'placement new requires its allocation function declaration', coord)
    size_type = c_size_type_name(parser.types.target_triple)
    selected = resolve_overload(group, [size_type] + [arg.dtype for arg in placement], parser.types,
                                coord, [False] + [arg.null_pointer for arg in placement],
                                [False] + [arg.lvalue or arg.assignment_result for arg in placement])
    if (selected.variadic or selected.parameters != (size_type, PointerType('void')) or not selected.nonthrowing
            or selected.language_linkage != 'C++' or selected.dtype != PointerType('void')):
        raise CppDiagnostic('cpp.unsupported.placement-allocation-overload',
                            'only the reserved nonallocating placement function is implemented', coord)
    if len(placement) != 1:
        raise AssertionError('reserved placement overload has one placement argument')
    arguments = []
    initialization_kind = 'default'
    if parser._accept('('):
        if parser._accept(')'):
            initialization_kind = 'value'
        else:
            while True:
                expression = parser._expression()
                parser._require_value(expression)
                arguments.append(parser._decay_array(expression))
                if parser._accept(')'):
                    break
                parser._expect(',')
    elif parser._peek().value == '{':
        raise CppDiagnostic('cpp.unsupported.placement-list-initialization', 'placement list initialization requires its distinct rules', coord)
    # No initializer and () both select a default constructor but differ in
    # default/value initialization. Only a non-user-provided selected C1 can
    # require the preceding value-initialization zero phase.
    if not owner.definition.constructors.overloads:
        raise CppDiagnostic('cpp.unsupported.placement-implicit-construction',
                            'implicit placement default/value initialization is not implemented', coord)
    name = parser._fresh_projection_name('__pcc_cpp_placement_')
    pointer_type = PointerType(dtype)
    storage = parser._convert(placement[0], PointerType('void'), coord)
    temporary = parser._decl(name, pointer_type, coord, _cast(parser, storage, pointer_type, coord))
    address = c_ast.ID(name, coord)
    # Initialization can write a const complete object; the returned pointer
    # retains const. This internal this conversion does not relax source casts.
    this_address = _cast(parser, address, PointerType(owner), coord) if isinstance(dtype, ConstType) else address
    # A source-bound complete C1 proof may contain exact generated member C1
    # calls. The constructor owner checks their complete argument/default
    # effects and final member addresses; placement adds no automatic cleanup.
    call = constructor_call(parser, owner, arguments, this_address, coord, require_closed=True,
                            initialization_kind=initialization_kind, object_type=dtype)
    node = c_ast.StmtExpr(c_ast.Compound([temporary, call, c_ast.ID(name, coord)], coord), coord)
    return Expression(node, pointer_type, constant_fact=not_constant(pointer_type, 'placement construction', coord))


def explicit_destructor(parser, expression, operator):
    from pcc.frontends.cpp.parser import Expression
    coord = operator.coord
    parser._expect('~')
    name = parser._name()
    if name.global_scope or len(name.parts) != 1:
        raise CppDiagnostic('cpp.invalid-destructor-name', 'qualification must precede the destructor tilde', coord)
    if parser.template_function is not None or parser.instantiating_template:
        raise CppDiagnostic('cpp.unsupported.dependent-destruction', 'dependent destructor lookup is not implemented', coord)
    receiver = unqualified(expression.dtype)
    if operator.value == '->':
        if not isinstance(receiver, PointerType):
            raise CppDiagnostic('cpp.invalid-destructor-receiver', 'arrow destruction requires an object pointer', coord)
        dtype = receiver.pointee
        address = expression.node
    else:
        dtype = expression.dtype
        if not expression.lvalue:
            raise CppDiagnostic('cpp.unsupported.temporary-destruction', 'temporary explicit destruction is not implemented', coord)
        address = parser._unary_expression('&', expression, coord).node
    if not is_class_type(dtype):
        raise CppDiagnostic('cpp.unsupported.pseudo-destructors', 'scalar pseudo-destructor semantics are not implemented', coord)
    require_complete(dtype, coord)
    owner = unqualified(dtype)
    # [basic.lookup.classref]/3 requires both contexts, not class-first
    # hiding: an outer alias may name T even when a class member does not.
    context_name = parser.scope.resolve_name(name)
    # Class member lookup must not walk into the owner's enclosing namespace.
    # The current class model keeps the injected class declaration there, so
    # recover only that one nominal identity explicitly, not arbitrary names.
    class_name = owner.definition.scope.names.get(name.identifier.value)
    if class_name is None and name.identifier.value == owner.name:
        class_name = owner.declaration_scope.names.get(owner.name)
    def matches(candidate):
        return candidate is not None and (
            candidate.kind == 'type' and unqualified(candidate.dtype) == owner
            or candidate is getattr(owner, 'template_origin', None))
    if (matches(context_name) and matches(class_name) and context_name is not class_name
            and getattr(class_name, 'access', 'public') != 'public'):
        raise CppDiagnostic('cpp.unsupported.destructor-alias-lookup',
                            'dual matching aliases with non-public class access require further access resolution', coord)
    named = context_name if matches(context_name) else class_name
    if (not matches(named) and name.identifier.value == owner.name
            and class_name is not None and class_name.kind != 'type'):
        raise CppDiagnostic('cpp.unsupported.destructor-hidden-name',
                            'destructor type-only lookup with a hidden injected name is not implemented', coord)
    if not matches(named):
        raise CppDiagnostic('cpp.destructor-type-mismatch', 'destructor name must denote the receiver class', coord)
    # Owner lookup is not permission to use a private alias from the caller.
    parser.scope.check_member_access(named, coord)
    parser._expect('(')
    parser._expect(')')
    destructor = owner.definition.destructor
    if (any(array_type(member.dtype) for member in owner.definition.members)
            or (destructor is None and any(is_class_type(member.dtype) for member in owner.definition.members))):
        raise CppDiagnostic('cpp.unsupported.destructor-subobjects',
                            'explicit array or implicit member destruction requires its lifetime owner', coord)
    if destructor is not None:
        parser.scope.check_member_access(destructor, coord)
        if (getattr(destructor, 'class_template_instance', None) is not None
                and not getattr(destructor, 'closed_destruction_body', False)):
            raise CppDiagnostic('cpp.unsupported.dependent-destruction',
                                'explicit template D1 requires a bound closed-destruction proof, not a queued body', coord)
        # Only the destructor binder's exact generated cleanup-call chain is
        # trusted. Unknown user calls and declarations alone are not proofs.
        if not getattr(destructor, 'closed_destruction_body', False):
            raise CppDiagnostic('cpp.unsupported.placement-constructor-effects',
                                'unknown destruction effects need exception/cleanup integration', coord)
    saved = parser._fresh_projection_name('__pcc_cpp_destroy_')
    temporary = parser._decl(saved, PointerType(dtype), coord, address)
    pointer = _cast(parser, c_ast.ID(saved, coord), PointerType(owner), coord)
    effect = (c_ast.FuncCall(c_ast.ID(destructor.c_name, coord), c_ast.ExprList([pointer], coord), coord)
              if destructor is not None else _cast(parser, c_ast.ID(saved, coord), 'void', coord))
    node = c_ast.StmtExpr(c_ast.Compound([temporary, effect], coord), coord)
    return Expression(node, 'void', constant_fact=not_constant('void', 'explicit destruction', coord))
