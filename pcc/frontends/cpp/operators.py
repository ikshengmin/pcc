"""Operator names, member subscripts and explicit assignment-function calls.

Ordinary operator= bodies/calls use the member and reference owners. Infix
class assignment, implicit special members and template bodies remain gated.
"""

from pcc.frontends.cpp.diagnostics import CppDiagnostic


def declaration_name(parser):
    if parser._peek().value != 'operator':
        return parser._identifier()
    from pcc.frontends.cpp.parser import CppToken
    token = parser.cursor.take()
    operation = parser.cursor.take()
    if operation.value == '=':
        return CppToken('OPERATOR_FUNCTION_ID', 'operator=', token.coord)
    if operation.value == '[':
        parser._expect(']')
        return CppToken('OPERATOR_FUNCTION_ID', 'operator[]', token.coord)
    if operation.value not in ('new', 'delete'):
        parser._unsupported(operation, 'operator-function semantics are not implemented')
    name = 'operator ' + operation.value
    if parser._accept('['):
        parser._expect(']')
        name += '[]'
    return CppToken('OPERATOR_FUNCTION_ID', name, token.coord)


def validate_subscript(name, parameters, *, member, static=False, variadic=False, defaults=()):
    if name.value != 'operator[]':
        return
    if not member or static or variadic or len(parameters) != 1 or any(defaults):
        raise CppDiagnostic('cpp.invalid-subscript-operator',
                            'C++17 operator[] requires a non-static member with one parameter and no defaults', name.coord)


def validate_assignment(name, parameters, *, member, static=False, variadic=False):
    if name.value == 'operator=' and (not member or static or variadic or len(parameters) != 1):
        raise CppDiagnostic('cpp.invalid-assignment-operator',
                            'assignment operator requires a non-static member with one parameter', name.coord)


def require_explicit_assignment_candidates(owner, group, arguments, coord):
    """Do not mistake an unsynthesized implicit copy for invalid overloads.

    An ordinary non-copy operator= does not suppress implicit copy assignment.
    Scalar/pointer calls cannot select that implicit candidate in this subset;
    a same-class argument must not enter an incomplete candidate set instead.
    """
    from pcc.frontends.cpp.types import ReferenceType, unqualified
    if len(arguments) != 1 or unqualified(arguments[0].dtype) is not owner:
        return
    for method in group.overloads:
        parameter, = method.parameters  # Declaration admission requires one.
        if isinstance(parameter, ReferenceType) and unqualified(parameter.referent) is owner:
            return
    raise CppDiagnostic('cpp.unsupported.class-assignment',
                        'explicit copy assignment requires an owned implicit special-member candidate', coord)
