"""Definition-bound member expressions in function templates of ordinary classes.

Nondependent declarations and their access context are retained by identity;
dependent receivers are looked up only after genuine type substitution.
"""
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.templates import Pattern, TemplateArgument, argument_dependent, dependent
from pcc.frontends.cpp.types import ConstType, PointerType, ReferenceType, qualify_const, unqualified


def _lookup(expression, operator, token):
    from pcc.frontends.cpp.classes import ClassType, require_complete
    dtype = expression.dtype
    if isinstance(dtype, ReferenceType):
        dtype = dtype.referent
    if operator == '->':
        pointer = unqualified(dtype)
        if dependent(pointer) and not isinstance(pointer, PointerType):
            return None, None
        if not isinstance(pointer, PointerType):
            raise CppDiagnostic('cpp.invalid-member-access', 'arrow requires a class pointer', token.coord)
        dtype = pointer.pointee
    owner = unqualified(dtype)
    if dependent(owner):
        return None, dtype
    if not isinstance(owner, ClassType):
        raise CppDiagnostic('cpp.invalid-member-access', 'member access requires a class object', token.coord)
    require_complete(owner, token.coord)
    symbol = owner.definition.scope.names.get(token.value)
    if symbol is None:
        from pcc.frontends.cpp.inheritance import find_member
        inherited, _ = find_member(owner, token.value)
        if inherited is not None:
            raise CppDiagnostic('cpp.unsupported.base-classes',
                                'inherited member template patterns require base-subobject adjustment', token.coord)
        raise CppDiagnostic('cpp.no-member', 'class has no member '+token.value, token.coord)
    return symbol, dtype


def pattern_member_access(parser, expression, operator, token):
    from pcc.frontends.cpp.parser import Expression
    symbol, dtype = _lookup(expression, operator, token)
    if symbol is None or symbol.kind == 'function':
        if parser._accept('('):
            return pattern_call(parser, symbol, expression, parser._call_arguments(), token.coord,
                                operator=operator, token=token)
        if symbol is not None:
            raise CppDiagnostic('cpp.unsupported.member-pointers', 'member function requires an immediate call', token.coord)
    if symbol is not None:
        parser.scope.check_member_access(symbol, token.coord)
        if symbol.kind != 'member':
            raise CppDiagnostic('cpp.unsupported.member-templates', 'this member value requires its own Pattern owner', token.coord)
    if operator == '.' and not expression.lvalue:
        raise CppDiagnostic('cpp.unsupported.member-templates', 'class temporary requires materialization', token.coord)
    result = None if symbol is None else symbol.dtype
    if symbol is not None and isinstance(dtype, ConstType) and not symbol.mutable:
        result = qualify_const(result)
    return Expression(Pattern('function-member-field', token.coord,
                              (symbol, operator, token, parser.scope), [expression]),
                      result, True, symbol, decltype_entity=None if symbol is None else symbol.dtype)


def accepts_argument_count(method, count):
    """Missing fixed parameters require real defaults; ellipsis supplies none."""
    fixed = len(method.parameters)
    if count > fixed:
        return bool(method.variadic)
    if count == fixed:
        return True
    defaults = tuple(getattr(method, 'default_arguments', ()))
    return len(defaults) == fixed and all(default is not None for default in defaults[count:])


def pattern_named_call(parser, group, arguments, coord, virtual_dispatch=True, *, explicit=None):
    from pcc.frontends.cpp.members import is_mixed_group, is_static_group
    receiver = None
    if not is_static_group(group):
        try:
            receiver = parser._implicit_object(group.class_owner, coord)
        except CppDiagnostic as exc:
            if exc.code != 'cpp.nonstatic-member-without-object' or not is_mixed_group(group):
                raise
            # [over.call.func]: use the contrived object only for ranking. A
            # selected nonstatic function cannot be invoked without this.
    return pattern_call(parser, group, receiver, arguments, coord,
                        named=True, virtual_dispatch=virtual_dispatch, explicit=explicit)


def pattern_call(parser, group, receiver, arguments, coord, *, operator='->', token=None,
                 named=False, virtual_dispatch=True, explicit=None):
    from pcc.frontends.cpp.parser import Expression
    from pcc.frontends.cpp.member_template_calls import select
    from pcc.frontends.cpp.members import is_static_group
    if operator == '.' and not receiver.lvalue:
        raise CppDiagnostic('cpp.unsupported.member-templates', 'class temporary requires materialization', coord)
    selected = None
    if group is not None:
        if (explicit is None or not any(argument_dependent(arg) for arg in explicit)) and not any(dependent(arg.dtype) for arg in arguments) and (
                receiver is None or not dependent(receiver.dtype)):
            pointer = (Expression(None, PointerType(group.class_owner)) if receiver is None else
                       receiver if operator == '->' else Expression(None, PointerType(receiver.dtype)))
            selected = select(parser, group, pointer, arguments, coord, explicit)
            parser.scope.check_member_access(selected, coord)
            if selected.deleted_template:
                raise CppDiagnostic('cpp.deleted-function', 'selected member template is deleted', coord)
            if receiver is None and not selected.method_static:
                raise CppDiagnostic('cpp.nonstatic-member-without-object', 'a non-static member requires an object', coord)
        elif not any(accepts_argument_count(method, len(arguments))
                     for method in group.overloads if explicit is None or method.template_parameters):
            raise CppDiagnostic('cpp.no-viable-overload', 'no member has the supplied argument count', coord)
    result = selected.dtype if selected is not None else None
    metadata = (group, operator, token, parser.scope, selected, receiver is not None,
                named, virtual_dispatch, explicit)
    return Expression(Pattern('function-member-call', coord, metadata,
                              ([] if receiver is None else [receiver])+list(arguments)),
                      result.referent if isinstance(result, ReferenceType) else result,
                      isinstance(result, ReferenceType))


def replay(parser, pattern, bindings, environment):
    from pcc.frontends.cpp.classes import member_access
    from pcc.frontends.cpp.members import call_member, call_static_member, single_overload
    from pcc.frontends.cpp.member_template_calls import select
    from pcc.frontends.cpp.member_template_instances import request_selected
    from pcc.frontends.cpp.parser import Expression
    args = [parser._instantiate_expression(child, bindings, environment) for child in pattern.children]
    identity, operator, token, scope = pattern.value[:4]
    previous = parser.scope
    parser.scope = scope
    try:
        if pattern.kind == 'function-member-field':
            if identity is not None:
                current, _ = _lookup(args[0], operator, token)
                if current is not identity:
                    raise CppDiagnostic('cpp.member-identity', 'nondependent field identity changed', pattern.coord)
            return member_access(parser, args[0], operator, token)
        selected, has_receiver, named, virtual_dispatch, explicit = pattern.value[4:]
        receiver = args.pop(0) if has_receiver else None
        group = identity
        if group is None:
            group, _ = _lookup(receiver, operator, token)
            if group is None or group.kind != 'function':
                raise CppDiagnostic('cpp.no-member', 'substituted member is not a function', pattern.coord)
        if operator == '.':
            receiver = parser._unary_expression('&', receiver, pattern.coord)
        if selected is None:
            probe = receiver if receiver is not None else Expression(None, PointerType(group.class_owner))
            substituted = None if explicit is None else tuple(TemplateArgument(
                argument.kind,
                parser._substitute_pattern_type(argument.value, bindings, argument.coord)
                if argument.kind == 'type' else
                parser._instantiate_expression(argument.value, bindings, environment),
                argument.coord, argument.tokens) for argument in explicit)
            selected = select(parser, group, probe, args, pattern.coord, substituted)
        # Already selected nondependent candidates retain their declaration
        # identity. Do not rededuce them or enqueue any losing candidate.
        parser.scope.check_member_access(selected, pattern.coord)
        if receiver is None and not selected.method_static:
            raise CppDiagnostic('cpp.nonstatic-member-without-object', 'a non-static member requires an object', pattern.coord)
        if selected.deleted_template:
            raise CppDiagnostic('cpp.deleted-function', 'selected member template is deleted', pattern.coord)
        if selected.template_origin is not None:
            selected = request_selected(parser, selected, pattern.coord)
        single = single_overload(group, selected, pattern.coord)
        if selected.method_static:
            return call_static_member(parser, single, args, pattern.coord,
                                      None if named or receiver is None else receiver.node)
        return call_member(parser, single, receiver, args, pattern.coord,
                           virtual_dispatch=virtual_dispatch)
    finally:
        parser.scope = previous
