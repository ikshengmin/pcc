"""Typed class-template member patterns and lazy, language-owned layout.

N4659 [temp.class], [temp.inst]: forming a pointer to a specialization does
not instantiate its layout. The shared InstanceRegistry owns identity and
scheduling; this owner only checks C++ completeness and emits ordinary C
records. No token replacement, alternate layout engine, or instance cache.
Ordinary member bodies are typed once at definition time and instantiated on
ODR-use. Complete-object D1s share the ordinary lifetime owner. Typed array
bounds retain definition-point Patterns and substitute before layout.
A single nondependent non-polymorphic base is bound at the definition and
shared by every specialization (layout, construction, destruction and
conversions use inheritance.py); naming its members inside member bodies,
dependent, template-id and polymorphic bases, class-element array lifetimes
and specializations remain explicit capability boundaries, never unparsed,
supposedly validated bodies.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.classes import ClassDefinition, ClassType
from pcc.frontends.cpp.declarators import DeclaratorParser
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.sema import Scope, Symbol
from pcc.frontends.cpp.templates import (
    ArrayBoundPattern, TypeParameter, convert_integral_argument, dependent,
    substitute, type_key, value_dependent,
)
from pcc.frontends.cpp.types import (
    ArrayType, ConstType, PointerType, ReferenceType, array_type,
    contains_function, is_enum_type, qualify_const, unqualified,
)


def unsupported(coord, message):
    raise CppDiagnostic('cpp.unsupported.class-templates', message, coord)


class InjectedClassPattern:
    cpp_class_pattern = True

    def __init__(self, owner):
        self.owner = owner
        self.name, self.qualified_name, self.coord = owner.name, owner.qualified_name, owner.coord
        self.internal_linkage = owner.scope.internal_linkage
        self.declaration_scope = owner.scope


class ClassTemplatePattern:
    def __init__(self, name, parameters, scope, class_key, coord):
        self.name, self.parameters, self.scope = name, parameters, scope
        self.class_key, self.coord = class_key, coord
        self.qualified_name = scope.namespace_path() + (name,)
        self.members = None
        self.injected = InjectedClassPattern(self)
        self.member_scope = None
        self.member_symbols = []
        self.methods = []
        self.constructors = Symbol(name, 'function', coord)
        self.method_definition = None
        self.base = None


def parameter_key(parameters):
    return tuple(('type',) if isinstance(p, TypeParameter)
                 else ('value', type_key(p.dtype)) for p in parameters)


def parse_array_bound(parser, pattern):
    """Parse once using the same typed expression owner as member bodies."""
    previous = parser.template_function
    parser.template_function = pattern
    try:
        return parser._expression()
    finally:
        parser.template_function = previous


def pattern_array_type(parser, element, expression, coord):
    from pcc.frontends.cpp.arrays import check_array_element, make_array_type
    check_array_element(element, coord)
    if expression is not None and value_dependent(expression):
        if not dependent(expression.dtype) and not parser.types.integral_operand(expression.dtype):
            raise CppDiagnostic('cpp.invalid-array-bound',
                                'array bound requires an integral converted constant expression', coord)
        return ArrayType(element, ArrayBoundPattern(expression))
    return make_array_type(element, expression, parser.types, coord)


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
    base = None
    if parser._peek().value == ':':
        # [temp.dep]/3: a nondependent base is bound at the definition.
        from pcc.frontends.cpp.inheritance import parse_base_clause
        base = parse_base_clause(parser, keyword.value, pattern.injected)
        if base.dtype.definition.polymorphic:
            unsupported(base.coord, 'polymorphic bases of class templates require virtual class template members')
    pattern.base = base
    pattern.injected.base = base
    parser._expect('{')
    parameter_scope = parser.scope
    scope = Scope(parameter_scope, kind='class', name=name.value)
    scope.declare(Symbol(name.value, 'type', name.coord, dtype=pattern.injected))
    parser.scope = scope
    scope.class_type = pattern.injected
    pattern.member_scope = scope
    from pcc.frontends.cpp.class_template_members import PatternDefinition
    pattern.method_definition = PatternDefinition(pattern)
    pattern.method_definition.base = base
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
                               'friend', 'virtual', 'template', 'explicit',
                               'constexpr'):
                unsupported(token.coord, 'class template members currently require scalar/pointer data declarations')
            from pcc.frontends.cpp.class_template_members import parse_signature
            pattern.method_definition.access = access
            if token.value == '~':
                from pcc.frontends.cpp.class_template_members import parse_destructor_signature
                parse_destructor_signature(parser, pattern)
                continue
            if token.value == name.value and parser.cursor.tokens[parser.cursor.position + 1].value == '(':
                parse_signature(parser, pattern, parser._identifier(), 'void', constructor=True)
                continue
            static = parser._accept('static') is not None
            mutable = parser._accept('mutable') is not None
            base = parser._type(check_lowering=False)
            method = False
            prior_data = False
            while True:
                from pcc.frontends.cpp.operators import declaration_name
                declaration = DeclaratorParser(parser.cursor, lambda: declaration_name(parser),
                                                parse_array_bound=lambda: parse_array_bound(parser, pattern),
                                                parse_functions=False).parse()
                if declaration.name.global_scope or len(declaration.name.parts) != 1:
                    unsupported(declaration.coord, 'qualified class template member declarators are not implemented')
                member = declaration.name.identifier
                if member.kind == 'OPERATOR_FUNCTION_ID' and member.value not in ('operator[]', 'operator='):
                    unsupported(member.coord, 'only class-template subscript and copy-assignment declarations have owned operator semantics')
                if member.kind == 'OPERATOR_FUNCTION_ID' and parser._peek().value != '(':
                    code = 'cpp.invalid-subscript-operator' if member.value == 'operator[]' else 'cpp.invalid-assignment-operator'
                    raise CppDiagnostic(code, 'operator name must declare a function', member.coord)
                dtype = base
                for chunk in reversed(declaration.chunks):
                    if chunk.kind == 'array':
                        dtype = pattern_array_type(parser, dtype, chunk.bound, chunk.coord)
                        continue
                    if chunk.kind == 'lvalue-reference':
                        from pcc.frontends.cpp.references import lvalue_reference
                        dtype = lvalue_reference(dtype, chunk.coord)
                        continue
                    if chunk.kind != 'pointer':
                        unsupported(chunk.coord, 'class template array/reference/function members are not implemented')
                    if 'volatile' in chunk.qualifiers:
                        raise CppDiagnostic('cpp.unsupported.volatile', 'volatile members are not implemented', chunk.coord)
                    if isinstance(unqualified(dtype), ReferenceType):
                        raise CppDiagnostic('cpp.invalid-reference-type', 'a pointer cannot point to a reference', chunk.coord)
                    dtype = PointerType(dtype)
                    if 'const' in chunk.qualifiers:
                        dtype = qualify_const(dtype)
                if parser._peek().value == '(':
                    if mutable:
                        raise CppDiagnostic('cpp.invalid-mutable', 'mutable applies only to data members', member.coord)
                    if prior_data:
                        unsupported(member.coord, 'mixed data/function declarator lists are not implemented')
                    parse_signature(parser, pattern, member, dtype, static=static)
                    method = True
                    break
                if static or parser._peek().value in ('=', '{', ':'):
                    unsupported(parser._peek().coord, 'class template static data, member initializers and bit-fields are not implemented')
                if member.value == name.value:
                    unsupported(member.coord, 'same-named class template data members are not implemented')
                if array_type(dtype) and dtype.bound is None:
                    raise CppDiagnostic('cpp.invalid-array-bound',
                                        'an array data member requires a bound', member.coord)
                if contains_function(dtype) or isinstance(unqualified(dtype), ReferenceType):
                    unsupported(member.coord, 'class template function/reference members are not implemented')
                if isinstance(unqualified(dtype), ClassType):
                    unsupported(member.coord, 'by-value class template subobjects are not implemented')
                if unqualified(dtype) == 'void':
                    raise CppDiagnostic('cpp.invalid-object-type', 'a data member cannot have type void', member.coord)
                from pcc.frontends.cpp.arrays import const_array_elements
                if mutable and const_array_elements(dtype):
                    raise CppDiagnostic('cpp.invalid-mutable', 'a mutable member cannot be const-qualified', member.coord)
                symbol = scope.declare(Symbol(member.value, 'member', member.coord, dtype=dtype))
                symbol.class_owner, symbol.access, symbol.mutable = pattern.injected, access, mutable
                pattern.member_symbols.append(symbol)
                members.append((symbol.name, dtype, member.coord, access, mutable))
                prior_data = True
                if not parser._accept(','):
                    break
            if not method:
                parser._expect(';')
        parser._expect(';')
    finally:
        parser.scope = parameter_scope
    # Resolved nondependent type identities are stored now, not looked up again
    # in the specialization's use-site namespace. Renamed redeclaration
    # parameters still substitute by their stable mixed-list ordinal.
    pattern.members = tuple(members)
    pattern.class_key = keyword.value
    from pcc.frontends.cpp.class_template_members import bind_patterns
    bind_patterns(parser, pattern)


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


def substitute_member(dtype, arguments, instance, coord, parser=None):
    if isinstance(dtype, TypeParameter):
        return arguments[dtype.index]
    if isinstance(dtype, InjectedClassPattern):
        return instance
    if isinstance(dtype, ConstType):
        return qualify_const(substitute_member(dtype.base, arguments, instance, coord, parser))
    if isinstance(dtype, ReferenceType):
        from pcc.frontends.cpp.references import lvalue_reference
        return lvalue_reference(substitute_member(dtype.referent, arguments, instance, coord, parser), coord)
    if isinstance(dtype, PointerType):
        pointee = substitute_member(dtype.pointee, arguments, instance, coord, parser)
        if isinstance(unqualified(pointee), ReferenceType):
            raise CppDiagnostic('cpp.invalid-reference-type', 'substitution cannot form a pointer to a reference', coord)
        return PointerType(pointee)
    if isinstance(dtype, ArrayType):
        from pcc.frontends.cpp.arrays import check_array_element, make_array_type
        element = substitute_member(dtype.element, arguments, instance, coord, parser)
        check_array_element(element, coord)
        if isinstance(dtype.bound, ArrayBoundPattern):
            if parser is None:
                raise CppDiagnostic('cpp.unsupported.array-templates',
                                    'dependent array bounds require a typed instantiation context', dtype.bound.coord)
            # Instantiation consumes the retained expression and bindings,
            # never tokens or use-site lookup. The ordinary bound owner checks
            # positivity, target size_t representation and constant proof.
            expression = parser._instantiate_expression(dtype.bound.expression, arguments, {})
            return make_array_type(element, expression, parser.types, dtype.bound.coord)
        return ArrayType(element, dtype.bound)
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
        # The nondependent base specifier is shared by every specialization.
        definition.base = pattern.base
        definition.layout_types = parser.types
        for name, member_type, at, access, mutable in pattern.members:
            member_type = substitute_member(member_type, arguments, dtype, at, parser)
            plain = unqualified(member_type)
            if isinstance(plain, ReferenceType) or contains_function(member_type):
                unsupported(at, 'substituted reference/function members are not implemented')
            if is_enum_type(plain) and not plain.complete:
                raise CppDiagnostic('cpp.incomplete-enum', 'a member requires a complete enum type', at)
            if not (isinstance(plain, PointerType) or isinstance(plain, ClassType) or array_type(plain) or is_enum_type(plain)
                    or isinstance(plain, str) and (plain == 'nullptr' or parser.types.arithmetic_operand(plain))):
                raise CppDiagnostic('cpp.invalid-object-type', 'class template member does not have an object type', at)
            if isinstance(plain, ClassType) and plain is not dtype:
                unsupported(at, 'by-value class template subobjects are not implemented')
            definition.set_access(access)
            definition.add_member(Symbol(name, 'member', at, dtype=member_type), mutable)
        from pcc.frontends.cpp.class_template_members import declare_instances
        before = len(parser.class_template_declarations)
        declare_instances(parser, pattern, definition, arguments)
        definition.finish()
        if any(array_type(member.dtype) for member in definition.members):
            from pcc.frontends.cpp.size_queries import sizeof_type
            sizeof_type(dtype, parser.types, coord)
        dtype.layout_state = 'complete'
        parser.class_template_declarations.insert(before, definition.c_declaration(parser._scalar_type))
    except BaseException as error:
        if dtype.definition is not None:
            dtype.definition.complete = False
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
