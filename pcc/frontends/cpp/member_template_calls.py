"""Overload and call boundary for templates on an ordinary (non-template) class.

The implicit object participates in overload ranking, not in deduction of the
source template's explicit parameter list. Do not register or emit a body while
forming candidates. The selected concrete member reuses the ordinary call path.
"""
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.sema import Symbol, resolve_overload
from pcc.frontends.cpp.templates import deduce
from pcc.frontends.cpp.types import PointerType


def has_templates(group):
    return any(method.template_parameters for method in group.overloads)


def active_definition(parser):
    return bool(getattr(getattr(parser, 'template_function', None),
                        'ordinary_member_template', False))


def active_instance(parser):
    origin = getattr(getattr(parser, 'member_function', None), 'template_origin', None)
    return getattr(origin, 'class_owner', None) is not None


def select(parser, group, receiver, arguments, coord, explicit=None):
    from pcc.frontends.cpp.member_template_instances import make_candidate
    from pcc.frontends.cpp.members import object_pointer_type
    actuals = [receiver] + arguments
    survey = Symbol(group.name, 'function', coord)
    binding_errors = []
    for origin in group.overloads:
        method = origin
        if origin.template_parameters:
            try:
                bindings = deduce(origin, [a.dtype for a in arguments], explicit, parser.types,
                                 unbound_is_nonviable=True)
            except CppDiagnostic as exc:
                # A capability error is not a substitution failure. This
                # candidate could be the best, so never silently discard it.
                if exc.code.startswith('cpp.unsupported.'):
                    raise
                binding_errors.append(exc)
                continue
            if bindings is None:
                continue
            from pcc.frontends.cpp.member_template_substitution import immediate_reference_to_void
            if immediate_reference_to_void(origin, bindings):
                continue
            method = make_candidate(parser, origin, bindings)
        elif explicit is not None:
            continue
        # Reuse ordinary conversion ranking, including reference value category
        # and const object, with non-template preference only on tied ranks.
        candidate = Symbol(method.name, 'function', coord, dtype=method.dtype,
                           parameters=(object_pointer_type(method),) + method.parameters)
        candidate.neutral_object = getattr(method, 'method_static', False)
        candidate.template_origin = method.template_origin
        candidate.variadic = method.variadic
        candidate.member_origin = method
        candidate.default_arguments = (None,) + tuple(getattr(
            method, 'default_arguments', (None,) * len(method.parameters)))
        survey.overloads.append(candidate)
    if not survey.overloads and binding_errors and all(
            exc.code == binding_errors[0].code for exc in binding_errors):
        raise binding_errors[0]
    selected = resolve_overload(survey, [a.dtype for a in actuals], parser.types, coord,
                                [a.null_pointer for a in actuals],
                                [a.lvalue or a.assignment_result for a in actuals],
                                distinguish_template_ambiguity=True)
    return selected.member_origin


def call(parser, group, receiver, arguments, coord, *, explicit=None, named=False,
         virtual_dispatch=True):
    from pcc.frontends.cpp.member_template_instances import request_selected
    from pcc.frontends.cpp.members import call_member, call_static_member, single_overload
    from pcc.frontends.cpp.parser import Expression
    if active_definition(parser) and named:
        from pcc.frontends.cpp.member_template_patterns import pattern_named_call
        return pattern_named_call(parser, group, arguments, coord, virtual_dispatch, explicit=explicit)
    if active_definition(parser) or getattr(parser, 'class_template_method', None) is not None:
        raise CppDiagnostic('cpp.unsupported.member-templates',
                            'a member template-id in a dependent call requires call replay', coord)
    if named:
        try:
            receiver = parser._implicit_object(group.class_owner, coord)
        except CppDiagnostic as exc:
            if exc.code != 'cpp.nonstatic-member-without-object':
                raise
            receiver = None
    actuals = [parser._decay_array(arg) for arg in arguments]
    probe = receiver if receiver is not None else Expression(None, PointerType(group.class_owner))
    selected = select(parser, group, probe, actuals, coord, explicit)
    parser.scope.check_member_access(selected, coord)
    is_static = getattr(selected, 'method_static', False)
    if receiver is None and not is_static:
        raise CppDiagnostic('cpp.nonstatic-member-without-object',
                            'a non-static member requires an object', coord)
    parser._check_call_value_abi(selected, coord)
    if selected.template_origin is not None and not parser.unevaluated_depth:
        selected = request_selected(parser, selected, coord)
    single = single_overload(group, selected, coord)
    if is_static:
        return call_static_member(parser, single, actuals, coord,
                                  None if named or receiver is None else receiver.node)
    return call_member(parser, single, receiver, actuals, coord, True,
                       virtual_dispatch=virtual_dispatch)


def signature_operation(parser, operation, *args, **kwargs):
    """Admit only the header type operators owned by this declaration."""
    previous = getattr(parser, 'function_template_type_patterns', False)
    parser.function_template_type_patterns = True
    try:
        return operation(*args, **kwargs)
    finally:
        parser.function_template_type_patterns = previous
