"""Static member calls and enumerators named through a nondependent class template-id.

N4659 [temp.names]/1, [basic.lookup.qual]/1 and [class.static.mfct]: in
``S<int>::f(args)`` the simple-template-id names one class specialization,
and qualified lookup finds the static member in that specialization's scope.
The specialization is the shared instance owner of class_templates.py, so
its body is requested exactly as for an object call. Named this way outside
its class, a non-static member function is invalid. ``S<1>::K`` names that
specialization's member enumerator, a constant with its member access
([class.access]); data members, member types, implicit-object calls and a
functional cast of the template-id remain explicit gaps. The specialization
owner itself rejects dependent template-ids.
"""
from pcc.frontends.cpp.classes import require_complete
from pcc.frontends.cpp.declarators import _scope_separator
from pcc.frontends.cpp.diagnostics import CppDiagnostic


def member_expression(parser, symbol, coord):
    from pcc.frontends.cpp.class_templates import specialize
    owner = specialize(parser, symbol, coord)
    if _scope_separator(parser.cursor) is None:
        raise CppDiagnostic("cpp.unsupported.class-initialization",
                            "class temporary materialization is not implemented", coord)
    member = parser._identifier()
    require_complete(owner, member.coord)
    group = owner.definition.scope.names.get(member.value)
    if group is None:
        raise CppDiagnostic("cpp.no-member", "class '" + owner.name + "' has no member '" + member.value + "'",
                            member.coord)
    from pcc.frontends.cpp.members import call_static_member, is_static_group
    if group.kind == "enumerator":
        parser.scope.check_member_access(group, member.coord)
        result = parser._enum_constant_expression(group.dtype, group.enum_value, member.coord)
        result.decltype_entity = group.dtype
        return result
    if group.kind == "function" and not is_static_group(group) and parser._peek().value == "(":
        current = parser.member_function
        if current is None or getattr(current, "class_owner", None) is not owner:
            # [class.mfct.non-static]/1: no object outside its own members.
            raise CppDiagnostic("cpp.nonstatic-member-without-object", "a non-static member requires an object",
                                member.coord)
    if group.kind != "function" or not is_static_group(group) or parser._peek().value != "(":
        raise CppDiagnostic("cpp.unsupported.class-templates",
                            "only static member calls through a class template-id are implemented", member.coord)
    parser._expect("(")
    return call_static_member(parser, group, parser._call_arguments(), member.coord)
