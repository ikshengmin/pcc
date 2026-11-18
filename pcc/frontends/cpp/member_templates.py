"""Function-template declarations owned by a non-template class.

N4659 [temp.mem], [temp.over.link], [class.mfct]. Declaration identity uses
parameter positions and types, never names or arity alone. Definition bodies
are fully bound to typed Patterns, including nondependent lookup and access;
only specialization and emission are deferred to the shared instance owner.
"""
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.sema import Scope, Symbol
from pcc.frontends.cpp.templates import signature_key


def declare_member_template(definition, symbol, const=False, outside=False, static=False):
    if not symbol.template_parameters:
        raise ValueError('member template requires template parameters')
    if definition.dtype.is_union:
        raise CppDiagnostic('cpp.unsupported.member-templates',
                            'union member templates require lifetime-aware method support', symbol.coord)
    if static and const:
        raise CppDiagnostic('cpp.static-member-cv', 'a static member cannot have cv-qualifiers', symbol.coord)
    if symbol.name == definition.dtype.name:
        raise CppDiagnostic('cpp.invalid-constructor', 'a constructor cannot have a return type', symbol.coord)
    scope = definition.scope
    group = scope.names.get(symbol.name)
    if group is not None and (group.kind != 'function' or getattr(group, 'class_owner', None) is not definition.dtype):
        raise CppDiagnostic('cpp.redeclaration', 'member template conflicts with a member', symbol.coord)
    if group is not None:
        for previous in group.overloads:
            if not previous.template_parameters or signature_key(previous) != signature_key(symbol):
                continue
            if previous.variadic != symbol.variadic:
                continue
            # An out-of-class definition cannot repeat the static specifier.
            # Its static identity comes exclusively from the class declaration.
            previous_static = getattr(previous, 'method_static', False)
            if not outside and static != previous_static:
                raise CppDiagnostic('cpp.static-nonstatic-overload',
                                    'static and non-static members cannot share a signature', symbol.coord)
            if previous.method_const != const:
                if static or previous_static:
                    raise CppDiagnostic('cpp.static-member-cv', 'a static member cannot have cv-qualifiers', symbol.coord)
                continue
            if not outside:
                raise CppDiagnostic('cpp.redeclaration', 'a member cannot be declared twice in its class', symbol.coord)
            if previous.defined:
                raise CppDiagnostic('cpp.redefinition', 'member template already defined', symbol.coord)
            previous.defined = True
            return previous
    if outside:
        raise CppDiagnostic('cpp.no-matching-member', 'definition does not match a declared member template', symbol.coord)
    symbol.ordinary_member_template = True
    symbol.class_owner = definition.dtype
    symbol.method_const = const
    symbol.method_static = static
    symbol.access = definition.access
    symbol.owner = scope
    symbol.language_linkage = 'C++'
    symbol.internal_linkage = definition.dtype.internal_linkage
    # The pattern itself has no callable linker symbol. Concrete instances
    # receive their ABI names from the specialization owner.
    symbol.definition_linkage = 'external_odr'
    if group is None:
        group = Symbol(symbol.name, 'function', symbol.coord)
        group.class_owner = definition.dtype
        group.access = 'public'
        scope.declare(group)
    group.overloads.append(symbol)
    definition.member_templates.append(symbol)
    return symbol


def member_parameter_scope(parameter_scope, template_scope, definition):
    """Keep definition-local template names ahead of the class's member scope.

    Do not mutate the original namespace template scope: it is also the
    declarator's enclosing namespace witness and return-type lookup context.
    """
    context = Scope(definition.scope)
    context.names.update(template_scope.names)
    parameter_scope.parent = context


def bind_member_template_body(parser, symbol, result_type, parameter_scope, parameter_symbols):
    from pcc.frontends.cpp.members import object_pointer_type
    this = None if symbol.method_static else Symbol(
        'this', 'value', symbol.coord, c_name=parser._local_projection_name('this'),
        dtype=object_pointer_type(symbol))
    names = ('scope', 'template_function', 'return_type', 'member_function',
             'this_symbol', 'current_function', 'parsing_template')
    previous = tuple(getattr(parser, name) for name in names)
    parser.scope = parameter_scope
    parser.parsing_template = True
    parser.template_function = parser.member_function = parser.current_function = symbol
    parser.return_type, parser.this_symbol = result_type, this
    symbol.parameter_symbols, symbol.this_symbol = parameter_symbols, this
    try:
        symbol.pattern = parser._block(new_scope=False)
        symbol.defined = True
    finally:
        for name, value in zip(names, previous):
            setattr(parser, name, value)


def defer_member_template_body(parser, definition, symbol, result_type, parameter_scope, parameter_symbols):
    from pcc.frontends.cpp.inline_constructors import collect_body
    if symbol.defined:
        raise CppDiagnostic('cpp.redefinition', 'member template already defined', symbol.coord)
    symbol.defined = True  # Reserve the definition, even before completed-class lookup.
    definition.pending_member_templates.append((
        symbol, result_type, parameter_scope, parameter_symbols, collect_body(parser)))


def bind_member_templates(parser, definition):
    from pcc.frontends.cpp.parser import CppToken, TokenCursor
    previous_cursor = parser.cursor
    try:
        for symbol, result, scope, parameters, tokens in definition.pending_member_templates:
            parser.cursor = TokenCursor(tokens + (CppToken('EOF', '', symbol.coord),))
            bind_member_template_body(parser, symbol, result, scope, parameters)
            if parser._peek().kind != 'EOF':
                raise CppDiagnostic('cpp.unsupported.syntax', 'unexpected member template body tail', parser._peek().coord)
    finally:
        parser.cursor = previous_cursor
    definition.pending_member_templates.clear()
