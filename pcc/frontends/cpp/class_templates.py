"""Typed class-template member patterns and lazy, language-owned layout.

N4659 [temp.class], [temp.inst]: forming a pointer to a specialization does
not instantiate its layout. The shared InstanceRegistry owns identity and
scheduling; this owner only checks C++ completeness and emits ordinary C
records. No token replacement, alternate layout engine, or instance cache.
Ordinary member bodies are typed once at definition time and instantiated on
ODR-use. Complete-object D1s share the ordinary lifetime owner. Typed array
bounds retain definition-point Patterns and substitute before layout.
A single nondependent base is bound at the definition and shared by every
specialization (layout, construction, destruction and conversions use
inheritance.py). Virtual members and overriders are declared per
specialization, which then owns its table (virtual_functions.py); a
nondependent signature's override rules are checked at the definition.
Member classes ([temp.mem.class]) holding data members only are
NestedClassPatterns: each specialization builds its own nested class (Itanium
nested name under that specialization) before laying out its own members.
Naming base members inside member bodies, dependent and template-id bases,
class-element array lifetimes, member functions of member classes and
specializations remain explicit capability boundaries, never unparsed,
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
        self.enums = []
        self.constructors = Symbol(name, 'function', coord)
        self.method_definition = None
        self.base = None
        self.nested_classes = []


class NestedClassPattern:
    """A member class of a class template: one class per specialization.

    N4659 [temp.mem.class], [temp.dep.type]/1: a member class of the current
    instantiation is a dependent type. Its data member types are Patterns over
    the enclosing parameters, member enums and earlier member classes.
    """
    cpp_dependent_type = True
    cpp_nested_class_pattern = True

    def __init__(self, owner, name, class_key, coord, access):
        self.owner, self.name, self.class_key = owner, name, class_key
        self.coord, self.access = coord, access
        self.members = None
        self.member_scope = Scope(owner.member_scope, kind='class', name=name)
        self.member_scope.class_type = self

    def __str__(self):
        return self.owner.name + '::' + self.name


def parse_nested_class_pattern(parser, owner, access):
    """`struct|union|class name { data members };` inside a class template."""
    keyword = parser.cursor.take()
    name = parser._identifier()
    if any(parameter.name == name.value for parameter in owner.parameters):
        raise CppDiagnostic('cpp.template-parameter-shadowing',
                            'a member class cannot have a template parameter name', name.coord)
    if name.value == owner.name:
        raise CppDiagnostic('cpp.class-name-member', 'a member class cannot have its enclosing class name', name.coord)
    if name.value in owner.member_scope.names:
        raise CppDiagnostic('cpp.redeclaration', "'" + name.value + "' is already declared in this class", name.coord)
    if parser._peek().value == ':':
        if keyword.value == 'union':
            raise CppDiagnostic('cpp.invalid-union-base', 'a union cannot have base classes', parser._peek().coord)
        unsupported(parser._peek().coord, 'bases of member classes of class templates are not implemented')
    if parser._peek().value != '{':
        unsupported(name.coord, 'member class declarations of class templates require a definition here')
    nested = NestedClassPattern(owner, name.value, keyword.value, name.coord, access)
    # Declared before its body: members may point to the class itself.
    symbol = owner.member_scope.declare(Symbol(name.value, 'type', name.coord, dtype=nested))
    symbol.class_owner, symbol.access = owner.injected, access
    parser._expect('{')
    previous = parser.scope
    parser.scope = nested.member_scope
    members = []
    member_access = 'private' if keyword.value == 'class' else 'public'
    try:
        while not parser._accept('}'):
            token = parser._peek()
            if token.value in ('public', 'protected', 'private'):
                member_access = parser.cursor.take().value
                parser._expect(':')
                continue
            if parser._accept(';'):
                continue
            if token.value in ('using', 'typedef', 'class', 'struct', 'union', 'enum', 'friend', 'template',
                               'explicit', 'constexpr', 'static', 'virtual', 'mutable', '~', 'operator') or (
                    token.value == name.value and parser.cursor.tokens[parser.cursor.position + 1].value == '('):
                unsupported(token.coord, 'member classes of class templates currently hold data members only')
            base = parser._type(check_lowering=False)
            while True:
                from pcc.frontends.cpp.operators import declaration_name
                declaration = DeclaratorParser(parser.cursor, lambda: declaration_name(parser),
                                               parse_array_bound=lambda: parse_array_bound(parser, owner),
                                               parse_functions=False).parse()
                member = declaration.name.identifier
                if declaration.name.global_scope or len(declaration.name.parts) != 1 or member.kind != 'ID':
                    unsupported(declaration.coord, 'member classes of class templates currently hold data members only')
                if parser._peek().value in ('(', '=', '{', ':'):
                    unsupported(parser._peek().coord, 'member classes of class templates currently hold data members only')
                dtype = base
                for chunk in reversed(declaration.chunks):
                    if chunk.kind == 'array':
                        dtype = pattern_array_type(parser, dtype, chunk.bound, chunk.coord, nested_element=True)
                        continue
                    if chunk.kind != 'pointer' or 'volatile' in chunk.qualifiers:
                        unsupported(chunk.coord, 'member class reference/function/volatile members are not implemented')
                    dtype = PointerType(dtype)
                    if 'const' in chunk.qualifiers:
                        dtype = qualify_const(dtype)
                if unqualified(dtype) is nested:
                    raise CppDiagnostic('cpp.incomplete-class', 'a class cannot contain itself by value', member.coord)
                if member.value == name.value:
                    raise CppDiagnostic('cpp.class-name-member', 'a member cannot have its class name', member.coord)
                if array_type(dtype) and dtype.bound is None:
                    raise CppDiagnostic('cpp.invalid-array-bound', 'an array data member requires a bound', member.coord)
                if unqualified(dtype) == 'void':
                    raise CppDiagnostic('cpp.invalid-object-type', 'a data member cannot have type void', member.coord)
                member_symbol = nested.member_scope.declare(Symbol(member.value, 'member', member.coord, dtype=dtype))
                member_symbol.class_owner, member_symbol.access = nested, member_access
                members.append((member.value, dtype, member.coord, member_access))
                if not parser._accept(','):
                    break
            parser._expect(';')
        parser._expect(';')
    finally:
        parser.scope = previous
    nested.members = tuple(members)
    owner.nested_classes.append(nested)


def trivial_pattern_element(element):
    """A member class pattern or class whose array elements need no lifetime actions."""
    if getattr(unqualified(element), 'cpp_nested_class_pattern', False):
        return True  # Checked again on the substituted class.
    from pcc.frontends.cpp.classes import is_class_type, trivial_special_members
    return is_class_type(element) and trivial_special_members(element)


def instantiate_nested_classes(parser, pattern, definition, instance, arguments):
    """Build this specialization's member classes before its own members."""
    definition.template_nested = {}
    for nested in pattern.nested_classes:
        dtype = ClassType(nested.name, pattern.qualified_name + (nested.name,), nested.coord,
                          internal_linkage=instance.internal_linkage, declaration_scope=definition.scope)
        dtype.is_union = nested.class_key == 'union'
        # Itanium 5.1.2: a nested-name under this specialization.
        dtype.enclosing_class = instance
        dtype.c_tag = instance.c_tag + '_' + str(len(nested.name)) + nested.name
        symbol = definition.scope.declare(Symbol(nested.name, 'type', nested.coord, dtype=dtype))
        symbol.class_owner, symbol.access = instance, nested.access
        definition.template_nested[nested] = dtype
    for nested in pattern.nested_classes:
        dtype = definition.template_nested[nested]
        member_definition = ClassDefinition(dtype, definition.scope, nested.class_key)
        member_definition.layout_types = parser.types
        for name, member_type, at, access in nested.members:
            member_type = substitute_member(member_type, arguments, instance, at, parser)
            member_definition.set_access(access)
            member_definition.add_member(Symbol(name, 'member', at, dtype=member_type))
        member_definition.finish()
        parser.class_template_declarations.append(member_definition.c_declaration(parser._scalar_type))


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


def pattern_array_type(parser, element, expression, coord, nested_element=False):
    from pcc.frontends.cpp.arrays import check_array_element, make_array_type
    if getattr(unqualified(element), 'cpp_nested_class_pattern', False):
        # The element's layout exists only per specialization.
        if expression is None:
            raise CppDiagnostic('cpp.invalid-array-bound', 'an array data member requires a bound', coord)
        if not dependent(expression.dtype) and not parser.types.integral_operand(expression.dtype):
            raise CppDiagnostic('cpp.invalid-array-bound',
                                'array bound requires an integral converted constant expression', coord)
        return ArrayType(element, ArrayBoundPattern(expression))
    allow_class = nested_element and trivial_pattern_element(element)
    check_array_element(element, coord, allow_class)
    if expression is not None and value_dependent(expression):
        if not dependent(expression.dtype) and not parser.types.integral_operand(expression.dtype):
            raise CppDiagnostic('cpp.invalid-array-bound',
                                'array bound requires an integral converted constant expression', coord)
        return ArrayType(element, ArrayBoundPattern(expression))
    return make_array_type(element, expression, parser.types, coord, allow_class)


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
            if token.value == 'enum':
                from pcc.frontends.cpp.class_template_enums import parse_enum_pattern
                parse_enum_pattern(parser, pattern, access)
                continue
            if (token.value in ('class', 'struct', 'union')
                    and parser.cursor.tokens[parser.cursor.position + 1].kind == 'ID'
                    and parser.cursor.tokens[parser.cursor.position + 2].value in ('{', ':')):
                parse_nested_class_pattern(parser, pattern, access)
                continue
            if token.value in ('using', 'typedef', 'class', 'struct', 'union',
                               'friend', 'template', 'explicit',
                               'constexpr'):
                unsupported(token.coord, 'class template members currently require scalar/pointer data declarations')
            from pcc.frontends.cpp.class_template_members import parse_signature
            pattern.method_definition.access = access
            virtual = None
            if token.value == 'virtual':
                from pcc.frontends.cpp.virtual_functions import take_virtual
                virtual = take_virtual(parser, None)
                token = parser._peek()
                if token.value == 'virtual':
                    take_virtual(parser, virtual)
            if token.value == '~':
                from pcc.frontends.cpp.class_template_members import parse_destructor_signature
                parse_destructor_signature(parser, pattern, virtual=virtual)
                continue
            if token.value == name.value and parser.cursor.tokens[parser.cursor.position + 1].value == '(':
                if virtual is not None:
                    raise CppDiagnostic('cpp.invalid-virtual', 'a constructor cannot be virtual', virtual.coord)
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
                    parse_signature(parser, pattern, member, dtype, static=static, virtual=virtual)
                    method = True
                    break
                if virtual is not None:
                    raise CppDiagnostic('cpp.invalid-virtual', 'virtual applies only to member functions', virtual.coord)
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
    if getattr(dtype, 'cpp_enum_pattern', False):
        return instance.definition.template_enums[dtype]
    if getattr(dtype, 'cpp_nested_class_pattern', False):
        return instance.definition.template_nested[dtype]
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
        # Elements of a member class need no lifetime actions when every
        # special member is trivial (classes.trivial_special_members).
        allow_class = trivial_pattern_element(element)
        check_array_element(element, coord, allow_class)
        if isinstance(dtype.bound, ArrayBoundPattern):
            if parser is None:
                raise CppDiagnostic('cpp.unsupported.array-templates',
                                    'dependent array bounds require a typed instantiation context', dtype.bound.coord)
            # Instantiation consumes the retained expression and bindings,
            # never tokens or use-site lookup. The ordinary bound owner checks
            # positivity, target size_t representation and constant proof.
            expression = parser._instantiate_expression(dtype.bound.expression, arguments, {})
            return make_array_type(element, expression, parser.types, dtype.bound.coord, allow_class)
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
    previous_specialization = getattr(parser, 'template_specialization', None)
    try:
        definition = ClassDefinition(dtype, pattern.scope, pattern.class_key)
        # The nondependent base specifier is shared by every specialization.
        definition.base = pattern.base
        definition.layout_types = parser.types
        # Member-enumerator Patterns (in enumerators and array bounds) name
        # this specialization's enumerators.
        parser.template_specialization = dtype
        from pcc.frontends.cpp.class_template_enums import instantiate_enums
        instantiate_enums(parser, pattern, definition, arguments)
        instantiate_nested_classes(parser, pattern, definition, dtype, arguments)
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
            if isinstance(plain, ClassType) and plain is not dtype and not (
                    getattr(plain, 'enclosing_class', None) is dtype and trivial_pattern_element(plain)):
                # A member class of this specialization whose special members
                # are all trivial needs no construction or destruction.
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
        tables = []
        if definition.polymorphic:
            # After every member declaration of this specialization: its own
            # table holds each final overrider, so each is ODR-used.
            from pcc.frontends.cpp.virtual_functions import complete_class
            tables = complete_class(parser, definition)
            from pcc.frontends.cpp.class_template_members import request_body
            for entry in definition.virtual_entries:
                if not entry.deleting and not entry.overrider.method_pure:
                    request_body(parser, entry.overrider, coord)
        dtype.layout_state = 'complete'
        parser.class_template_declarations.insert(before, definition.c_declaration(parser._scalar_type))
        parser.class_template_declarations.extend(tables)
    except BaseException as error:
        if dtype.definition is not None:
            dtype.definition.complete = False
        dtype.layout_failure, dtype.layout_state = error, 'failed'
        raise
    finally:
        parser.template_specialization = previous_specialization


def emit_instance(parser, entry):
    dtype = entry.value
    if dtype.layout_state == 'incomplete':
        parser.class_template_declarations.append(c_ast.Decl(
            None, [], [], [], c_ast.Struct(dtype.c_tag, None, dtype.coord), None, None, dtype.coord))
    elif dtype.layout_state == 'failed':
        raise dtype.layout_failure
    return None
