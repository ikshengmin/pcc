"""Operator-function names and the bounded C++17 member-subscript contract."""

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
