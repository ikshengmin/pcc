"""Explicit class-qualified ordinary member calls, never qualified data access."""
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import unqualified


def fail(coord, detail):
    raise CppDiagnostic('cpp.unsupported.base-classes', detail, coord)


def ordinary_qualifier(parser, name, object_class, coord):
    """[basic.lookup.classref]/4: search the object class before the lexical scope."""
    from pcc.frontends.cpp.declarators import QualifiedName
    from pcc.frontends.cpp.inheritance import base_chain, require_member_access
    if len(name.tokens) != 2 or name.global_scope:
        fail(coord, 'qualified call needs a single ordinary class qualifier')
    part = name.parts[0]
    for current in (object_class, *base_chain(object_class)):
        member = current.definition.scope.names.get(part)
        if member is not None:
            if member.kind != 'type':
                fail(coord, 'non-type or dependent receiver qualifier needs qualified lookup')
            require_member_access(parser.scope, member, object_class, None, coord)
            return member
        if current.name == part:
            symbol = current.declaration_symbol
            if symbol is None:
                fail(coord, 'the injected class name has no ordinary declaration')
            parser.scope.check_named_member_access(symbol, coord)
            return symbol
    qualifier = QualifiedName(name.tokens[:-1], False, name.coord)
    return parser.scope.resolve_name(qualifier, qualifier_only=True)


def call(parser, expression, operator, first, object_class):
    from pcc.frontends.cpp.declarators import parse_qualified_name
    from pcc.frontends.cpp.classes import is_class_type, require_complete
    from pcc.frontends.cpp.inheritance import (
        is_proper_base, require_accessible_base, require_member_access, find_member, base_pointer,
    )
    from pcc.frontends.cpp.members import (
        select_member_overload, single_overload, call_member, call_static_member,
    )
    coord = first.coord
    if parser.template_function is not None or parser.instantiating_template:
        fail(coord, 'qualified member calls in template contexts require lookup replay')
    # declaration_name consumed the initial identifier; reuse the same tokens
    # with the existing qualified-id grammar instead of reparsing source text.
    if parser.cursor.tokens[parser.cursor.position - 1] is not first:
        fail(coord, 'qualified operator or reconstructed names require their own grammar')
    parser.cursor.position -= 1
    name = parse_qualified_name(parser.cursor, parser._identifier)
    if len(name.tokens) < 2:
        fail(coord, 'qualified member call requires a class qualifier')
    symbol = ordinary_qualifier(parser, name, object_class, coord)
    if symbol is None or symbol.kind != 'type' or not is_class_type(symbol.dtype):
        fail(coord, 'qualified member call qualifier must name a known class')
    naming = unqualified(symbol.dtype)
    require_complete(naming, coord)
    if naming is not object_class and not is_proper_base(naming, object_class):
        raise CppDiagnostic('cpp.invalid-member-access', 'qualifying class is not the object class or its base', coord)
    if naming is not object_class:
        require_accessible_base(parser.scope, object_class, naming, coord)
    group, declaring = find_member(naming, name.identifier.value)
    if group is None or group.kind != 'function':
        fail(coord, 'qualified data/member-type access is not implemented')
    if any(method.template_parameters for method in group.overloads):
        fail(coord, 'qualified member templates require their own lookup owner')
    if not parser._accept('('):
        fail(coord, 'qualified member function requires an immediate call')
    arguments = parser._call_arguments()
    if operator == '.':
        if not expression.lvalue:
            fail(coord, 'qualified call requires a stable object glvalue')
        receiver = parser._unary_expression('&', expression, coord)
    else:
        receiver = expression
    if declaring is not object_class:
        require_accessible_base(parser.scope, object_class, declaring, coord)
        receiver = base_pointer(receiver, declaring, coord)
    selected = select_member_overload(parser, group, receiver,
                                      [parser._decay_array(a) for a in arguments], coord)
    static = getattr(selected, 'method_static', False)
    require_member_access(parser.scope, selected, naming, None if static else object_class, coord)
    parser.scope.check_member_access(selected, coord)
    if not selected.defined or parser.current_function is selected:
        fail(coord, 'qualified calls require an already bound same-TU ordinary definition')
    for pending in selected.class_owner.definition.inline_methods:
        if pending.symbol is selected and pending.function is None:
            fail(coord, 'qualified callee body has not yet been bound')
    one = single_overload(group, selected, coord)
    if static:
        if operator == '.':
            if not expression.discarded_known:
                fail(coord, 'static receiver discarded evaluation is not proven')
            discarded = expression.discarded_node
        else:
            discarded = expression.node
        return call_static_member(parser, one, arguments, coord, discarded)
    return call_member(parser, one, receiver, arguments, coord, virtual_dispatch=False)
