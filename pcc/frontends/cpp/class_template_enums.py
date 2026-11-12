"""Unnamed unscoped member enums of class templates, instantiated per specialization.

N4659 [dcl.enum], [class.mem] and [temp.dep.constexpr]/2: an enumerator of a
member enum of the current instantiation is value-dependent when its
initializer is. The definition keeps each initializer as a typed Pattern and
declares each enumerator in the member scope; uses in member bodies and
array bounds become dependent 'member-enumerator' Patterns. Each
specialization builds its own EnumType with the ordinary enum owner (values,
implicit successors, underlying type and range) before its members are laid
out, and every Pattern use resolves to that specialization's enumerator.
Named and scoped member enums of class templates remain explicit gaps.
"""
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.sema import Symbol
from pcc.frontends.cpp.types import unqualified


def unsupported(coord, message):
    raise CppDiagnostic('cpp.unsupported.enum-templates', message, coord)


class EnumPattern:
    """The dependent unnamed enum type of the current instantiation."""
    cpp_dependent_type = True
    cpp_enum_pattern = True

    def __init__(self, owner, coord, underlying, access):
        self.owner, self.coord = owner, coord
        self.underlying, self.access = underlying, access
        self.enumerators = []
        self.name = '<unnamed enum>'

    def __str__(self):
        return self.owner.name + '::<unnamed enum>'


def parse_enum_pattern(parser, owner, access):
    """`enum [: integral] { name [= constant-expression], ... };` in a class template."""
    from pcc.frontends.cpp.class_templates import parse_array_bound
    from pcc.frontends.cpp.templates import dependent
    keyword = parser._expect('enum')
    if parser._peek().value in ('class', 'struct'):
        unsupported(parser._peek().coord, 'scoped member enums of class templates are not implemented')
    if parser._peek().value not in ('{', ':'):
        unsupported(parser._peek().coord, 'named member enums of class templates are not implemented')
    underlying = None
    if parser._accept(':'):
        underlying = unqualified(parser._type(check_lowering=False))
        if dependent(underlying):
            unsupported(keyword.coord, 'dependent enum bases are not implemented')
        if not parser.types.integral(underlying):
            raise CppDiagnostic('cpp.enum-underlying-type', 'enum base must name an integral type', keyword.coord)
    if parser._peek().value != '{':
        raise CppDiagnostic('cpp.enum-definition-required', 'an unnamed enumeration requires a definition',
                            parser._peek().coord)
    parser._expect('{')
    pattern = EnumPattern(owner, keyword.coord, underlying, access)
    scope = owner.member_scope
    if not parser._accept('}'):
        while True:
            name = parser._identifier()
            expression = None
            if parser._accept('='):
                # Parsed once as a definition-point Pattern, as array bounds are.
                expression = parse_array_bound(parser, owner)
                parser._require_value(expression)
                dtype = unqualified(expression.dtype)
                if not dependent(dtype) and not (parser.types.integral(dtype)
                                                 or getattr(dtype, 'cpp_enum_type', False) and not dtype.scoped):
                    raise CppDiagnostic('cpp.enum-initializer-type',
                                        'enumerator initializer must have integral or unscoped enum type', name.coord)
            if name.value in scope.names:
                raise CppDiagnostic('cpp.redeclaration', "duplicate member '" + name.value + "'", name.coord)
            if name.value == owner.name:
                raise CppDiagnostic('cpp.class-name-member', 'an enumerator cannot have its enclosing class name',
                                    name.coord)
            symbol = Symbol(name.value, 'template-enumerator', name.coord, dtype=pattern)
            symbol.enum_pattern, symbol.enum_index = pattern, len(pattern.enumerators)
            symbol.class_owner, symbol.access = owner.injected, access
            scope.declare(symbol)
            pattern.enumerators.append((name, expression))
            if parser._accept('}'):
                break
            parser._expect(',')
            if parser._accept('}'):
                break
    if parser._peek().value != ';':
        raise CppDiagnostic('cpp.unsupported.enum-declarators',
                            'enum definitions with object declarators are not implemented', parser._peek().coord)
    if not pattern.enumerators:
        raise CppDiagnostic('cpp.enum-member-name-required',
                            'an unnamed member enum without a declarator must declare an enumerator', keyword.coord)
    parser._expect(';')
    owner.enums.append(pattern)
    return pattern


def instantiate_enums(parser, owner, definition, arguments):
    """Build this specialization's enum types before any member uses them."""
    from pcc.frontends.cpp.enums import EnumType
    dtype = definition.dtype
    definition.template_enums = {}
    for pattern in owner.enums:
        enum_type = EnumType('<unnamed enum>', dtype.qualified_name + ('<unnamed enum>',), pattern.coord,
                             underlying=pattern.underlying, declaration_scope=definition.scope,
                             internal_linkage=dtype.internal_linkage, types=parser.types)
        enum_type.unnamed_ordinal = definition.unnamed_type_count
        definition.unnamed_type_count += 1
        # Registered first: a later initializer may name an earlier enumerator.
        definition.template_enums[pattern] = enum_type
        definition.set_access(pattern.access)
        enum_type.begin_definition()
        for name, expression in pattern.enumerators:
            fact = None
            if expression is not None:
                fact = parser._constant_fact(parser._instantiate_expression(expression, arguments, {}))
            enum_type.add_enumerator(name.value, name.coord, fact, parser.types)
        enum_type.finish(parser.types)


def enumerator_expression(parser, symbol, coord):
    """The current specialization's value of a member-enumerator Pattern."""
    specialization = getattr(parser, 'template_specialization', None)
    if specialization is None:
        context = getattr(parser, 'class_template_instance', None)
        specialization = None if context is None else context.symbol.class_owner
    enums = getattr(getattr(specialization, 'definition', None), 'template_enums', None)
    if enums is None or symbol.enum_pattern not in enums:
        unsupported(coord, 'member enumerators require their own class template specialization')
    enum_type = enums[symbol.enum_pattern]
    if symbol.enum_index >= len(enum_type.enumerators):
        raise CppDiagnostic('cpp.undeclared', "enumerator '" + symbol.name + "' is not yet declared", coord)
    enumerator = enum_type.enumerators[symbol.enum_index]
    return parser._enum_constant_projection(enumerator.dtype, enumerator.enum_value, coord)
