"""Declaration-bound friendship to existing ordinary classes.

N4659 [class.friend], [basic.lookup.elab] and [dcl.type.elab], with CWG 138's
P1787R6 lookup boundary for using-directives to nested namespaces. The admitted
form is ``friend class/struct qualified-name;`` naming a previously declared
non-template class. A friend declaration neither adds a class member nor
changes layout, linkage or the nominated declaration's ordinary visibility.
Friend-introduced names, friend functions, templates and simple-type forms
remain explicit gaps rather than borrowing another declaration mechanism.
"""

from pcc.frontends.cpp.classes import ClassType
from pcc.frontends.cpp.declarators import parse_qualified_name
from pcc.frontends.cpp.diagnostics import CppDiagnostic


def unsupported(token, detail):
    raise CppDiagnostic('cpp.unsupported.classes', detail, token.coord)


def parse_friend_class(parser, definition):
    keyword = parser._expect('friend')
    if parser._peek().value not in ('class', 'struct'):
        unsupported(keyword, 'only elaborated friendship to an existing ordinary class is implemented')
    parser.cursor.take()
    name = parse_qualified_name(parser.cursor, parser._identifier,
                                reject_template_ids=False)
    if parser._peek().value != ';':
        unsupported(parser._peek(), 'friend class declarations require one existing non-template class name')
    parser._expect(';')
    # [namespace.memdef]/3 stops an unqualified friend declaration at the
    # innermost namespace. Retain ordinary type-only lookup within that bound:
    # directives whose common ancestor lies outside it must not supply a name.
    # Qualified names keep ordinary elaborated lookup, including outer scopes.
    if not name.global_scope and len(name.parts) == 1:
        symbol = parser.scope.lookup(
            name.parts[0], name.coord, ('type', 'template-type', 'class-template'),
            stop_at=parser.scope.namespace())
        parser.scope.check_member_access(symbol, name.coord)
    else:
        symbol = parser.scope.resolve_name(name, elaborated_type=True)
    if symbol is None:
        if name.global_scope or len(name.parts) != 1:
            raise CppDiagnostic('cpp.undeclared', 'qualified friend class was not previously declared', name.coord)
        unsupported(name.identifier, 'friend-introduced class names require separate namespace visibility semantics')
    if symbol.kind in ('template-type', 'class-template'):
        unsupported(name.identifier, 'template class friendship is not implemented')
    if (not getattr(symbol, 'class_declaration', False)
            or not isinstance(symbol.dtype, ClassType)):
        raise CppDiagnostic('cpp.redeclaration', 'elaborated friend requires a class-name, not an alias or other type', name.coord)
    friend = symbol.dtype
    if all(existing is not friend for existing in definition.friend_classes):
        definition.friend_classes.append(friend)
