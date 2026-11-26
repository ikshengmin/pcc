"""Assertion failure projection for the owned C++17 <cassert>/<assert.h>.

The header selects its real conditional expression; ordinary C++ Sema checks
its contextual conversion to bool and controls single evaluation. Success
performs no I/O. Failure emits one source-attributed diagnostic on stderr,
then calls the target's actual abort owner. No arbitrary noexcept/noreturn
call is admitted by this builtin. NDEBUG is handled only by the header.
"""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.constants import not_constant
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.string_literals import decode, spelling_for
from pcc.frontends.cpp.templates import Pattern


def _literal_string(parser):
    token = parser.cursor.take()
    if token.kind != 'STRING_LITERAL':
        raise CppDiagnostic('cpp.invalid-assertion-metadata', 'assertion metadata requires a string literal', token.coord)
    return decode(token.value, token.coord)


def parse_failure(parser, token):
    parser._expect('(')
    expression = _literal_string(parser)
    parser._expect(',')
    filename = _literal_string(parser)
    parser._expect(',')
    line = parser.cursor.take()
    if line.kind != 'INT_CONST_DEC' or not line.value.isdecimal():
        raise CppDiagnostic('cpp.invalid-assertion-metadata', 'assertion metadata requires a source line', line.coord)
    parser._expect(')')
    function = parser.template_function or parser.current_function or parser.member_function
    name = function.name if function is not None else '<global>'
    message = filename + b':' + line.value.encode('ascii') + b': ' + name.encode('utf-8')
    message += b": Assertion `" + expression + b"' failed.\n"
    return failure(parser, message, token.coord)


def failure(parser, message, coord):
    from pcc.frontends.cpp.parser import Expression
    target = parser.types.target_triple
    if not any(part in target for part in ('-linux-', '-darwin', '-apple-')):
        raise CppDiagnostic('cpp.unsupported.assertion-target', 'owned assertion diagnostics require a supported Linux or Darwin libc ABI', coord)
    fact = not_constant('void', 'failed assertion invokes abort', coord)
    if parser.template_function is not None:
        return Expression(Pattern('assertion-failure', coord, message), 'void', constant_fact=fact)
    # These external names are the target C runtime's existing write and
    # abort ABI, not the fake-libc assert macro or an external compiler.
    write = c_ast.FuncCall(c_ast.ID('write', coord), c_ast.ExprList([
        c_ast.Constant('int', '2', coord),
        c_ast.Constant('string', spelling_for(message), coord),
        c_ast.Constant('int', str(len(message)) + 'UL', coord),
    ], coord), coord)
    abort = c_ast.FuncCall(c_ast.ID('abort', coord), None, coord)
    return Expression(c_ast.ExprList([write, abort], coord), 'void', constant_fact=fact)
