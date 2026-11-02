"""Typed class-template member patterns and lazy, language-owned layout.

N4659 [temp.class], [temp.inst]: forming a pointer to a specialization does
not instantiate its layout. The shared InstanceRegistry owns identity and
scheduling; this owner only checks C++ completeness and emits ordinary C
records. No token replacement, alternate layout engine, or instance cache.
Member-function patterns, bases, arrays and specializations remain explicit
capability boundaries rather than unparsed, supposedly validated bodies.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.classes import ClassDefinition, ClassType
from pcc.frontends.cpp.declarators import DeclaratorParser
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.sema import Scope, Symbol
from pcc.frontends.cpp.templates import (
    TypeParameter, convert_integral_argument, substitute, type_key,
)
from pcc.frontends.cpp.types import ConstType, PointerType, ReferenceType, array_type, contains_function, is_enum_type, qualify_const, unqualified


def unsupported(coord, message):
    raise CppDiagnostic('cpp.unsupported.class-templates', message, coord)


class InjectedClassPattern:
    def __init__(self, owner):
        self.owner = owner


class ClassTemplatePattern:
    def __init__(self, name, parameters, scope, class_key, coord):
        self.name, self.parameters, self.scope = name, parameters, scope
        self.class_key, self.coord = class_key, coord
        self.qualified_name = scope.namespace_path() + (name,)
        self.members = None
        self.injected = InjectedClassPattern(self)


def parameter_key(parameters):
    return tuple(('type',) if isinstance(p, TypeParameter)
                 else ('value', type_key(p.dtype)) for p in parameters)


def parse_declaration(parser, parameters, parent):
    keyword = parser.cursor.take()
    name = parser._identifier()
    if any(parameter.name == name.value for parameter in parameters):
        raise CppDiagnostic('cpp.template-parameter-shadowing', 'a template parameter cannot have the template name', name.coord)
    if parent.kind != 'namespace':
        unsupported(keyword.coord, 'nested/local class templates are not implemented')
    if parser._peek().value == '<':
        unsupported(name.coord, 'class template specializations are not implemented')
    previous = parent.names.get(name.value)
    if previous is None:
        pattern = ClassTemplatePattern(name.value, parameters, parent, keyword.value, name.coord)
        previous = parent.declare(Symbol(name.value, 'class-template', name.coord, dtype=pattern))
    elif previous.kind != 'class-template':
        raise CppDiagnostic('cpp.redeclaration', 'class template conflicts with another declaration', name.coord)
    else:
        if previous.owner is not parent:
            unsupported(name.coord, 'an imported class template cannot be redeclared in another namespace')
        pattern = previous.dtype
        if parameter_key(pattern.parameters) != parameter_key(parameters):
            raise CppDiagnostic('cpp.redeclaration', 'class template parameter lists differ', name.coord)
    if parser._accept(';'):
        return
    if pattern.members is not None:
        raise CppDiagnostic('cpp.class-redefinition', 'class template is already defined', name.coord)
    if parser._peek().value == ':':
        unsupported(parser._peek().coord, 'class template base layout is not implemented')
    parser._expect('{')
    parameter_scope = parser.scope
    scope = Scope(parameter_scope, kind='class', name=name.value)
    scope.declare(Symbol(name.value, 'type', name.coord, dtype=pattern.injected))
    parser.scope = scope
    members = []
    access = 'private' if keyword.value == 'class' else 'public'
    try:
        while not parser._accept('}'):
            token = parser._peek()
            if token.value in ('public', 'protected', 'private'):
                access = parser.cursor.take().value
                parser._expect(':')
                continue
            if parser._accept(';'):
                continue
            if token.value in ('using', 'typedef', 'enum', 'class', 'struct', 'union',
                               'friend', 'static', 'virtual', 'template', '~', 'explicit',
                               'constexpr') or token.value == name.value and parser.cursor.tokens[parser.cursor.position + 1].value == '(':
                unsupported(token.coord, 'class template members currently require scalar/pointer data declarations')
            mutable = parser._accept('mutable') is not None
            base = parser._type(check_lowering=False)
            while True:
                declaration = DeclaratorParser(parser.cursor, parser._identifier,
                                                parse_functions=False).parse()
                if declaration.name.global_scope or len(declaration.name.parts) != 1:
                    unsupported(declaration.coord, 'qualified class template member declarators are not implemented')
                dtype = base
                for chunk in reversed(declaration.chunks):
                    if chunk.kind != 'pointer':
                        unsupported(chunk.coord, 'class template array/reference/function members are not implemented')
                    if 'volatile' in chunk.qualifiers:
                        raise CppDiagnostic('cpp.unsupported.volatile', 'volatile members are not implemented', chunk.coord)
                    if isinstance(unqualified(dtype), ReferenceType):
                        raise CppDiagnostic('cpp.invalid-reference-type', 'a pointer cannot point to a reference', chunk.coord)
                    dtype = PointerType(dtype)
                    if 'const' in chunk.qualifiers:
                        dtype = qualify_const(dtype)
                if parser._peek().value in ('(', '=', '{', ':'):
                    unsupported(parser._peek().coord, 'class template methods, member initializers and bit-fields are not implemented')
                member = declaration.name.identifier
                if member.value == name.value:
                    unsupported(member.coord, 'same-named class template data members are not implemented')
                if contains_function(dtype) or isinstance(unqualified(dtype), ReferenceType):
                    unsupported(member.coord, 'class template function/reference members are not implemented')
                if isinstance(unqualified(dtype), ClassType):
                    unsupported(member.coord, 'by-value class template subobjects are not implemented')
                if unqualified(dtype) == 'void':
                    raise CppDiagnostic('cpp.invalid-object-type', 'a data member cannot have type void', member.coord)
                if mutable and isinstance(dtype, ConstType):
                    raise CppDiagnostic('cpp.invalid-mutable', 'a mutable member cannot be const-qualified', member.coord)
                symbol = scope.declare(Symbol(member.value, 'member', member.coord, dtype=dtype))
                members.append((symbol.name, dtype, member.coord, access, mutable))
                if not parser._accept(','):
                    break
            parser._expect(';')
        parser._expect(';')
    finally:
        parser.scope = parameter_scope
    # Resolved nondependent type identities are stored now, not looked up again
    # in the specialization's use-site namespace. Renamed redeclaration
    # parameters still substitute by their stable mixed-list ordinal.
    pattern.members = tuple(members)
    pattern.class_key = keyword.value


def bind_arguments(parser, pattern):
    explicit = parser._explicit_template_arguments(allow_class_types=True)
    if len(explicit) != len(pattern.parameters):
        raise CppDiagnostic('cpp.template-argument-count', 'class template argument count differs from its parameter list', pattern.coord)
    bindings = []
    for parameter, argument in zip(pattern.parameters, explicit):
        if isinstance(parameter, TypeParameter):
            if argument.kind != 'type':
                raise CppDiagnostic('cpp.template-argument-kind', 'class template parameter requires a type argument', argument.coord)
            dtype = argument.value
            if isinstance(unqualified(dtype), (TypeParameter, InjectedClassPattern)):
                unsupported(argument.coord, 'dependent class template-ids are not implemented')
            bindings.append(dtype)
        else:
            if argument.kind != 'value':
                raise CppDiagnostic('cpp.template-argument-kind', 'class template parameter requires a value argument', argument.coord)
            dtype = substitute(parameter.dtype, bindings)
            bindings.append(convert_integral_argument(argument.value, dtype, parser.types, argument.coord))
    return tuple(bindings)


def specialize(parser, symbol, coord):
    pattern = symbol.dtype
    if parser.parsing_template:
        unsupported(coord, 'dependent/nested template-id member type patterns are not implemented')
    if parser._peek().value != '<':
        unsupported(coord, 'class template argument deduction is not implemented')
    arguments = bind_arguments(parser, pattern)
    key = (symbol, arguments)
    entry = parser.instance_registry.get(key)
    if entry is not None:
        return entry.value
    dtype = ClassType(pattern.name, pattern.qualified_name, coord,
                      internal_linkage=pattern.scope.internal_linkage,
                      declaration_scope=pattern.scope)
    dtype.template_origin, dtype.template_arguments = symbol, arguments
    # The ABI encoding is deterministic across TUs and preserves nominal and
    # integral-argument type identity; it is not a source spelling cache key.
    from pcc.frontends.cpp.types import _mangle_type
    dtype.c_tag = '__pcc_cpp_class_template_' + _mangle_type(dtype, [])
    dtype.layout_state = 'incomplete'
    dtype.layout_failure = None
    dtype.complete_layout = lambda at: complete_layout(parser, dtype, pattern, arguments, at)
    parser.instance_registry.request(key, dtype, pattern)
    return dtype


def substitute_member(dtype, arguments, instance, coord):
    if isinstance(dtype, TypeParameter):
        return arguments[dtype.index]
    if isinstance(dtype, InjectedClassPattern):
        return instance
    if isinstance(dtype, ConstType):
        return qualify_const(substitute_member(dtype.base, arguments, instance, coord))
    if isinstance(dtype, PointerType):
        pointee = substitute_member(dtype.pointee, arguments, instance, coord)
        if isinstance(unqualified(pointee), ReferenceType):
            raise CppDiagnostic('cpp.invalid-reference-type', 'substitution cannot form a pointer to a reference', coord)
        return PointerType(pointee)
    return dtype


def complete_layout(parser, dtype, pattern, arguments, coord):
    if dtype.layout_state == 'complete':
        return
    if dtype.layout_state == 'failed':
        raise dtype.layout_failure
    if dtype.layout_state == 'in-progress':
        raise CppDiagnostic('cpp.incomplete-class', 'recursive by-value class template layout', coord)
    if pattern.members is None:
        raise CppDiagnostic('cpp.incomplete-class', 'class template definition is unavailable', coord)
    dtype.layout_state = 'in-progress'
    try:
        definition = ClassDefinition(dtype, pattern.scope, pattern.class_key)
        for name, member_type, at, access, mutable in pattern.members:
            member_type = substitute_member(member_type, arguments, dtype, at)
            plain = unqualified(member_type)
            if isinstance(plain, ReferenceType) or array_type(plain) or contains_function(member_type):
                unsupported(at, 'substituted reference/array/function members are not implemented')
            if is_enum_type(plain) and not plain.complete:
                raise CppDiagnostic('cpp.incomplete-enum', 'a member requires a complete enum type', at)
            if not (isinstance(plain, PointerType) or isinstance(plain, ClassType) or is_enum_type(plain)
                    or isinstance(plain, str) and (plain == 'nullptr' or parser.types.arithmetic_operand(plain))):
                raise CppDiagnostic('cpp.invalid-object-type', 'class template member does not have an object type', at)
            if isinstance(plain, ClassType) and plain is not dtype:
                unsupported(at, 'by-value class template subobjects are not implemented')
            definition.set_access(access)
            definition.add_member(Symbol(name, 'member', at, dtype=member_type), mutable)
        definition.finish()
        dtype.layout_state = 'complete'
        parser.class_template_declarations.append(definition.c_declaration(parser._scalar_type))
    except BaseException as error:
        dtype.layout_failure, dtype.layout_state = error, 'failed'
        raise


def emit_instance(parser, entry):
    dtype = entry.value
    if dtype.layout_state == 'incomplete':
        parser.class_template_declarations.append(c_ast.Decl(
            None, [], [], [], c_ast.Struct(dtype.c_tag, None, dtype.coord), None, None, dtype.coord))
    elif dtype.layout_state == 'failed':
        raise dtype.layout_failure
    return None
