"""Definition-point member Patterns, instantiated only after ODR-use.

Original tokens are parsed once after the primary class's member declarations
are complete. Instances consume typed Patterns and declaration identities, not
source text or tokens. Concrete initialization and destruction reuse the C1/D1
owners; projection and external-ODR linkage use the ordinary member machinery.
"""

from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.sema import Scope, Symbol
from pcc.frontends.cpp.templates import dependent
from pcc.frontends.cpp.types import ConstType, PointerType, ReferenceType, contains_array, contains_function, is_enum_type, unqualified


class MethodPattern:
    def __init__(self, owner, symbol, parameters, parameter_symbols, scope, tokens):
        self.owner, self.symbol = owner, symbol
        self.parameters, self.parameter_symbols = parameters, parameter_symbols
        self.parameter_scope, self.tokens = scope, tokens
        self.body = None
        self.initializers = None
        self.this = None


class PatternDefinition:
    """Declaration-only view for the shared member semantic owner, not a layout."""
    def __init__(self, owner):
        self.dtype, self.scope = owner.injected, owner.member_scope
        self.access = 'private' if owner.class_key == 'class' else 'public'
        self.destructor = None


def unsupported(coord, message):
    raise CppDiagnostic('cpp.unsupported.member-templates', message, coord)


def self_reference(dtype, owner):
    """A borrow of this declaration's injected/concrete class, not any T&."""
    return isinstance(dtype, ReferenceType) and unqualified(dtype.referent) is owner


def parse_destructor_signature(parser, owner, virtual=None):
    """Retain a primary D1 declaration and typed body, without a concrete name."""
    from pcc.frontends.cpp.destructors import declare_destructor, parse_parameters, parse_virt_specifiers
    from pcc.frontends.cpp.inline_constructors import collect_body
    parser._expect('~')
    name = parser._identifier()
    definition = owner.method_definition
    parameter_scope = parse_parameters(parser, definition, name, check_lowering=False)
    specifiers = parse_virt_specifiers(parser)
    if parser._peek().value not in (';', '{'):
        unsupported(name.coord, 'defaulted/deleted/ref-qualified template destructors require special-member rules')
    symbol = declare_destructor(definition, name, encode_name=False)
    symbol.class_template_pattern = owner
    symbol.definition_linkage = 'external_odr'
    symbol.template_virtual = (virtual, specifiers, False)
    check_pattern_destructor(owner, symbol, name.coord)
    if parser._accept(';'):
        tokens = None
    else:
        if owner.scope.internal_linkage:
            unsupported(name.coord, 'internal-linkage class template definitions require separate ownership')
        tokens = collect_body(parser)
    record = MethodPattern(owner, symbol, (), (), parameter_scope, tokens)
    symbol.member_pattern = record
    owner.methods.append(record)
    return record


def pattern_bases(owner):
    from pcc.frontends.cpp.inheritance import base_chain
    base = getattr(owner, 'base', None)
    return [] if base is None else [base.dtype, *base_chain(base.dtype)]


def check_pattern_virtual(owner, symbol, coord):
    """Diagnose a nondependent signature's override rules at the definition."""
    from pcc.frontends.cpp.virtual_functions import _check_final, _check_return, overridden_in, static_override
    virtual, specifiers, pure = symbol.template_virtual
    if virtual is None and not specifiers and not pure:
        overridden = overridden_in(pattern_bases(owner), symbol) if not dependent_signature(symbol) else None
        if overridden is not None:
            # [class.virtual]/2: an implicit overrider obeys the same rules.
            if getattr(symbol, 'method_static', False):
                static_override(coord)
            _check_final(overridden, coord)
            _check_return(owner.method_definition, symbol, overridden, coord)
        return
    if getattr(symbol, 'method_static', False):
        raise CppDiagnostic('cpp.invalid-virtual', 'a static member function cannot be virtual', coord)
    if virtual is None and pure:
        raise CppDiagnostic('cpp.invalid-pure-specifier', 'a pure-specifier requires a virtual function', coord)
    if symbol.variadic or symbol.name.startswith('operator'):
        from pcc.frontends.cpp.virtual_functions import _unsupported
        _unsupported(coord, 'variadic or operator virtual functions require further table entries')
    if dependent_signature(symbol):
        return  # Checked by each specialization's declaration.
    overridden = overridden_in(pattern_bases(owner), symbol)
    if 'override' in specifiers and overridden is None:
        raise CppDiagnostic('cpp.invalid-override',
                            "'" + symbol.name + "' is marked override but does not override a base member", coord)
    if virtual is None and overridden is None:
        raise CppDiagnostic('cpp.invalid-virt-specifier', 'final requires a virtual function', coord)
    if overridden is not None:
        _check_final(overridden, coord)
        _check_return(owner.method_definition, symbol, overridden, coord)


def check_pattern_destructor(owner, symbol, coord):
    from pcc.frontends.cpp.virtual_functions import _check_final
    virtual, specifiers, _ = symbol.template_virtual
    bases = pattern_bases(owner)
    overridden = next((base.definition.destructor for base in bases[:1]
                       if getattr(base.definition.destructor, 'method_virtual', False)), None)
    if 'override' in specifiers and overridden is None:
        raise CppDiagnostic('cpp.invalid-override',
                            "'" + symbol.name + "' is marked override but does not override a base member", coord)
    if virtual is None and overridden is None and specifiers:
        raise CppDiagnostic('cpp.invalid-virt-specifier', 'final requires a virtual function', coord)
    if overridden is not None:
        _check_final(overridden, coord)


def dependent_signature(symbol):
    return any(dependent(dtype) for dtype in (symbol.dtype, *symbol.parameters))


def parse_signature(parser, owner, name, result_type, static=False, constructor=False, virtual=None):
    """Register an ordinary member of a primary class template, not a member template."""
    from pcc.frontends.cpp.inline_constructors import collect_body, collect_definition
    from pcc.frontends.cpp.members import declare_member_function
    parent = parser.scope
    parameter_scope = Scope(owner.member_scope)
    parser.scope = parameter_scope
    params, parameter_symbols = [], []
    previous_signature = getattr(parser, 'class_template_signature', False)
    parser.class_template_signature = True
    try:
        parser._expect('(')
        parser._parameter_clause(params, parameter_symbols, check_lowering=False)
        parser._expect(')')
    finally:
        parser.class_template_signature = previous_signature
        parser.scope = parent
    # [class.copy.ctor]/5 forbids this declaration even without a body or
    # specialization. Defaults are not admitted here, so only one parameter
    # can meet the 'all other parameters have defaults' condition.
    if constructor and len(params) == 1 and unqualified(params[0][1]) is owner.injected:
        raise CppDiagnostic('cpp.invalid-constructor', 'a constructor cannot take its own class by value as its sole parameter', name.coord)
    copy_declaration = ((constructor or name.value == 'operator=') and len(params) == 1
                        and self_reference(params[0][1], owner.injected))
    if name.value == 'operator=' and not copy_declaration:
        from pcc.frontends.cpp.class_templates import unsupported as unsupported_class_template
        unsupported_class_template(name.coord, 'only self-reference copy-assignment declarations are implemented')
    const = parser._accept('const') is not None
    if constructor and const:
        raise CppDiagnostic('cpp.constructor-cv', 'constructors cannot have cv-qualifiers', name.coord)
    from pcc.frontends.cpp.virtual_functions import parse_member_specifiers
    specifiers, pure = parse_member_specifiers(parser)
    if constructor and (specifiers or pure):
        raise CppDiagnostic('cpp.invalid-virtual', 'a constructor cannot be virtual or overriding', name.coord)
    if parser._peek().value in ('volatile', '&', '&&', 'noexcept', 'throw', 'try', '=', '->'):
        unsupported(parser._peek().coord, 'member ref/exception/default/delete/trailing contracts are not implemented')
    from pcc.frontends.cpp.operators import validate_assignment, validate_subscript
    validate_subscript(name, params, member=True, static=static)
    validate_assignment(name, params, member=True, static=static)
    if copy_declaration and parser._peek().value != ';':
        unsupported(name.coord, 'copy-reference member definitions require explicit copy/lifetime semantics')
    from pcc.frontends.cpp.references import check_result
    check_result(result_type, name.coord)
    for index, dtype in enumerate((result_type,) + tuple(t for _, t in params)):
        plain = unqualified(dtype)
        if isinstance(plain, ReferenceType) and index and not copy_declaration:
            raise CppDiagnostic('cpp.unsupported.references', 'class template member reference signatures require referent Pattern semantics', name.coord)
        if contains_array(dtype):
            raise CppDiagnostic('cpp.unsupported.array-templates', 'array member signatures require template array semantics', name.coord)
        nominal = unqualified(plain.referent) if isinstance(plain, ReferenceType) else plain
        nominal = unqualified(nominal.pointee) if isinstance(nominal, PointerType) else nominal
        if is_enum_type(nominal):
            raise CppDiagnostic('cpp.unsupported.enum-templates', 'enum member signatures need typed template support', name.coord)
        if contains_function(dtype):
            unsupported(name.coord, 'class template callback signatures are not implemented')
    symbol = Symbol(name.value, 'function', name.coord, dtype=result_type,
                    parameters=tuple(unqualified(dtype) for _, dtype in params))
    symbol.class_template_pattern = owner
    symbol.template_copy_declaration = copy_declaration
    symbol.definition_linkage = 'external_odr'
    if constructor:
        if static:
            raise CppDiagnostic('cpp.invalid-member-storage', 'a constructor cannot be static', name.coord)
        if any(item.parameters == symbol.parameters for item in owner.constructors.overloads):
            raise CppDiagnostic('cpp.redeclaration', 'constructor redeclared in its class', name.coord)
        symbol.constructor = True
        symbol.class_owner, symbol.owner = owner.injected, owner.member_scope
        symbol.access = owner.method_definition.access
        symbol.method_static = symbol.method_const = False
        symbol.internal_linkage = owner.scope.internal_linkage
        owner.constructors.overloads.append(symbol)
    else:
        symbol = declare_member_function(owner.method_definition, symbol, const=const, static=static, encode_name=False)
        symbol.template_virtual = (virtual, specifiers, pure)
        check_pattern_virtual(owner, symbol, name.coord)
    for parameter in parameter_symbols:
        if parameter is not None:
            parameter.c_name = parser._local_projection_name(parameter.name)
    if parser._accept(';'):
        tokens = None
    else:
        if owner.scope.internal_linkage:
            unsupported(name.coord, 'internal-linkage class template definitions require separate ownership')
        tokens = collect_definition(parser, owner.injected) if constructor else collect_body(parser)
    record = MethodPattern(owner, symbol, tuple(params), tuple(parameter_symbols), parameter_scope, tokens)
    symbol.member_pattern = record
    owner.methods.append(record)
    return record


def bind_patterns(parser, owner):
    """Validate every definition at the primary definition point, even if unused."""
    from pcc.frontends.cpp.constructors import parse_member_initializers
    from pcc.frontends.cpp.arrays import const_array_elements
    from pcc.frontends.cpp.parser import CppToken, TokenCursor
    fields = ('cursor', 'scope', 'template_function', 'return_type', 'member_function',
              'this_symbol', 'current_function')
    saved = {field: getattr(parser, field) for field in fields}
    previous = getattr(parser, 'class_template_method', None)
    try:
        for record in owner.methods:
            if record.tokens is None:
                continue
            symbol = record.symbol
            parser.class_template_method = record
            parser.cursor = TokenCursor(record.tokens + (CppToken('EOF', '', symbol.coord),))
            parser.scope = record.parameter_scope
            parser.template_function = parser.member_function = parser.current_function = symbol
            parser.return_type = symbol.dtype
            if symbol.method_static:
                parser.this_symbol = None
            else:
                referent = ConstType(owner.injected) if symbol.method_const else owner.injected
                record.this = Symbol('this', 'value', symbol.coord,
                                     c_name=parser._local_projection_name('this'), dtype=PointerType(referent))
                parser.this_symbol = record.this
            if getattr(symbol, 'constructor', False):
                record.initializers = parse_member_initializers(parser, owner.method_definition)
                for field, expression in record.initializers.items():
                    if expression is not None:
                        parser._convert(expression, field.dtype, field.coord, contextual=True)
                for field in owner.member_symbols:
                    if (field not in record.initializers and const_array_elements(field.dtype)
                            and not dependent(field.dtype)):
                        raise CppDiagnostic('cpp.const-initializer', 'const data member requires initialization', field.coord)
            record.body = parser._block(new_scope=False)
            if parser._peek().kind != 'EOF':
                raise CppDiagnostic('cpp.unsupported.syntax', 'unexpected class template method body tail', parser._peek().coord)
            symbol.defined = True
    finally:
        parser.class_template_method = previous
        for field, value in saved.items():
            setattr(parser, field, value)


class MethodInstance:
    def __init__(self, record, symbol, declaration, this, parameter_symbols, arguments):
        self.record, self.symbol, self.declaration = record, symbol, declaration
        self.this, self.parameter_symbols, self.arguments = this, parameter_symbols, arguments


def declare_instances(parser, owner, definition, arguments):
    """Instantiate declarations only; no unused body is lowered or emitted."""
    from pcc.frontends.c.ast import c_ast
    from pcc.frontends.cpp.class_templates import substitute_member
    from pcc.frontends.cpp.classes import is_class_type
    from pcc.frontends.cpp.constructors import _complete_name, _declaration
    from pcc.frontends.cpp.destructors import (
        declare_destructor, declaration as destructor_declaration,
    )
    from pcc.frontends.cpp.members import declare_member_function, object_pointer_type
    for record in owner.methods:
        origin = record.symbol
        result = substitute_member(origin.dtype, arguments, definition.dtype, origin.coord, parser)
        params = tuple((token, substitute_member(dtype, arguments, definition.dtype, origin.coord, parser))
                       for token, dtype in record.parameters)
        from pcc.frontends.cpp.references import check_result
        check_result(result, origin.coord)
        for index, dtype in enumerate((result,) + tuple(dtype for _, dtype in params)):
            plain = unqualified(dtype)
            nominal = unqualified(plain.referent) if isinstance(plain, ReferenceType) else plain
            nominal = unqualified(nominal.pointee) if isinstance(nominal, PointerType) else nominal
            if is_enum_type(nominal):
                raise CppDiagnostic('cpp.unsupported.enum-templates', 'substituted enum member signatures need typed template support', origin.coord)
            copy_parameter = (index and getattr(origin, 'template_copy_declaration', False)
                              and self_reference(dtype, definition.dtype))
            if (is_class_type(dtype) or (isinstance(dtype, ReferenceType) and index and not copy_parameter)
                    or contains_array(dtype) or contains_function(dtype)):
                unsupported(origin.coord, 'substituted class-value/callback member signatures require further ABI/lifetime semantics')
            parser._check_extended_value_type(dtype, origin)
        if any(unqualified(dtype) == 'void' for _, dtype in params):
            raise CppDiagnostic('cpp.invalid-parameter', 'substitution formed a void parameter', origin.coord)
        symbol = Symbol(origin.name, 'function', origin.coord, dtype=result,
                        parameters=tuple(unqualified(dtype) for _, dtype in params))
        symbol.template_copy_declaration = getattr(origin, 'template_copy_declaration', False)
        definition.set_access(origin.access)
        if getattr(origin, 'destructor', False):
            # Destructors are not overload-set members and retain a separate
            # declaration identity, just as in the ordinary class owner.
            symbol = declare_destructor(definition, origin)
        elif getattr(origin, 'constructor', False):
            if any(item.parameters == symbol.parameters for item in definition.constructors.overloads):
                raise CppDiagnostic('cpp.redeclaration', 'constructor signatures coincide after substitution', origin.coord)
            symbol.constructor = True
            symbol.class_owner, symbol.owner = definition.dtype, definition.scope
            symbol.access = origin.access
            symbol.method_static = symbol.method_const = False
            symbol.internal_linkage = definition.dtype.internal_linkage
            symbol.c_name = _complete_name(definition.dtype, symbol.parameters)
            definition.constructors.overloads.append(symbol)
        else:
            symbol = declare_member_function(definition, symbol, const=origin.method_const,
                                             static=origin.method_static)
        symbols = tuple(None if original is None else Symbol(
            original.name, 'value', original.coord, c_name=original.c_name, dtype=dtype)
            for original, (_, dtype) in zip(record.parameter_symbols, params))
        if getattr(origin, 'destructor', False):
            declaration, this = destructor_declaration(parser, symbol)
        elif getattr(origin, 'constructor', False):
            declaration, this = _declaration(parser, symbol, params, symbols, True)
        else:
            this = (None if symbol.method_static else Symbol(
                'this', 'value', origin.coord, c_name=parser._local_projection_name('this'),
                dtype=object_pointer_type(symbol)))
            parameters = ([] if this is None else [parser._decl(this.c_name, this.dtype, origin.coord)])
            parameters += [parser._decl(item.c_name if item is not None else '__pcc_cpp_unnamed_' + str(index),
                                       dtype, token.coord if token is not None else origin.coord)
                           for index, (item, (token, dtype)) in enumerate(zip(symbols, params))]
            declaration = parser._decl(symbol.c_name, result, origin.coord)
            declaration.type = c_ast.FuncDecl(c_ast.ParamList(parameters, origin.coord), declaration.type, origin.coord)
            if symbol.internal_linkage:
                declaration.storage = ['static']
        virtual, specifiers, pure = getattr(origin, 'template_virtual', (None, (), False))
        if getattr(origin, 'destructor', False):
            # A virtual base destructor makes this specialization's virtual.
            from pcc.frontends.cpp.virtual_functions import declare_destructor as declare_virtual_destructor
            declare_virtual_destructor(definition, symbol, declaration, virtual, specifiers, False, origin.coord)
        elif not getattr(origin, 'constructor', False):
            from pcc.frontends.cpp.virtual_functions import declare_virtual
            declare_virtual(definition, symbol, virtual, specifiers, pure, origin.coord)
            if getattr(symbol, 'method_virtual', False):
                symbol.virtual_prototype = declaration
        symbol.class_template_instance = MethodInstance(record, symbol, declaration, this, symbols, arguments)
        parser.function_projection_names.add(symbol.c_name)
        parser.class_template_declarations.append(declaration)
    definition.dtype.template_member_groups = {
        group: definition.scope.names[name]
        for name, group in owner.member_scope.names.items() if group.kind == 'function'
    }



def has_tu_local_type(types):
    """Inspect type identity only, never instantiate a specialization's layout."""
    from pcc.frontends.cpp.templates import IntegralArgument
    from pcc.frontends.cpp.types import ArrayType, FunctionType
    pending, seen = list(types), set()
    while pending:
        dtype = unqualified(pending.pop())
        if isinstance(dtype, IntegralArgument):
            pending.append(dtype.dtype)
            continue
        if id(dtype) in seen:
            continue
        seen.add(id(dtype))
        if getattr(dtype, 'internal_linkage', False):
            return True
        scope = getattr(dtype, 'declaration_scope', None)
        if scope is not None and scope.kind not in ('namespace', 'class'):
            return True
        if isinstance(dtype, PointerType):
            pending.append(dtype.pointee)
        elif isinstance(dtype, ReferenceType):
            pending.append(dtype.referent)
        elif isinstance(dtype, ArrayType):
            pending.append(dtype.element)
        elif isinstance(dtype, FunctionType):
            pending.extend((dtype.result,) + dtype.parameters)
        pending.extend(getattr(dtype, 'template_arguments', ()))
    return False


def request_body(parser, symbol, coord):
    context = getattr(symbol, 'class_template_instance', None)
    if context is None or parser.unevaluated_depth:
        return
    if has_tu_local_type(context.arguments + (symbol.dtype,) + symbol.parameters):
        unsupported(coord, 'TU-local nominal arguments/signatures require local specialization identity and linkage')
    if context.record.body is None:
        raise CppDiagnostic('cpp.template-definition', 'selected class template member has no available definition', coord)
    try:
        from pcc.backend.definition_linkage import supports_external_odr_function_definitions
    except ModuleNotFoundError as error:
        if error.name != 'pcc.backend.definition_linkage':
            raise
        supported = False
    else:
        supported = supports_external_odr_function_definitions(parser.types.target_triple)
    if not supported or not hasattr(context.declaration, 'definition_linkage'):
        raise CppDiagnostic('cpp.unsupported.inline-member-functions',
                            'class template member emission requires external-ODR definition linkage', coord)
    parser.instance_registry.request((context.record.symbol, context.arguments), symbol, context)


def instantiate_body(parser, context):
    from copy import deepcopy
    from pcc.frontends.c.ast import c_ast
    from pcc.frontends.cpp.parser import Expression
    from pcc.frontends.cpp.constructors import lower_member_initializers, call_free_body
    from pcc.frontends.cpp.lifetimes import AutomaticLifetimes
    record, symbol = context.record, context.symbol
    fields = ('return_type', 'member_function', 'this_symbol', 'current_function', 'scope',
              'instantiating_template', 'lifetimes')
    saved = {field: getattr(parser, field) for field in fields}
    saved['template_specialization'] = getattr(parser, 'template_specialization', None)
    parser.template_specialization = context.symbol.class_owner
    previous = getattr(parser, 'class_template_instance', None)
    environment = {}
    for original, concrete in zip(record.parameter_symbols, context.parameter_symbols):
        if original is not None:
            environment[id(original)] = Expression(c_ast.ID(concrete.c_name, concrete.coord), concrete.dtype, True)
    if record.this is not None:
        environment[id(record.this)] = Expression(c_ast.ID(context.this.c_name, context.this.coord), context.this.dtype)
    parser.class_template_instance = context
    parser.return_type, parser.member_function, parser.this_symbol = symbol.dtype, symbol, context.this
    parser.current_function, parser.scope = symbol, symbol.class_owner.definition.scope
    parser.instantiating_template = True
    # Replay is a separate function, never part of a requesting function's
    # lifetime stack. Local class/reference/array Patterns remain rejected.
    parser.lifetimes = AutomaticLifetimes()
    construction_calls = selected_destructors = None
    try:
        stores = []
        if getattr(symbol, 'constructor', False):
            initializers = {
                symbol.class_owner.definition.scope.names[member.name]:
                None if expression is None else parser._instantiate_expression(expression, context.arguments, environment)
                for member, expression in record.initializers.items()
            }
            # The same closed-construction owner as bind_constructor_body:
            # subobjects needing destruction require a finite proven body.
            from pcc.frontends.cpp.destructors import member_destructors
            definition = symbol.class_owner.definition
            selected_destructors = member_destructors(definition, parser.scope)
            if selected_destructors and definition.destructor is None:
                raise CppDiagnostic('cpp.unsupported.destructor-subobjects',
                                    'member construction requires a declared enclosing destructor', symbol.coord)
            construction_calls = {}
            stores = lower_member_initializers(parser, definition, context.this, initializers,
                                               construction_calls=construction_calls)
        if getattr(symbol, 'destructor', False):
            from pcc.frontends.cpp.destructors import complete_body
            body = complete_body(
                parser, symbol.class_owner.definition, symbol, parser.scope,
                context.this, symbol.coord,
                lambda: parser._instantiate_statement(record.body, context.arguments, environment),
            )
        else:
            body = parser._instantiate_statement(record.body, context.arguments, environment)
        body.block_items = stores + body.block_items
    finally:
        parser.class_template_instance = previous
        for field, value in saved.items():
            setattr(parser, field, value)
    declaration = deepcopy(context.declaration)
    declaration.definition_linkage = 'external_odr'
    symbol.defined = True
    if getattr(symbol, 'constructor', False):
        from pcc.frontends.cpp.constructors import closed_construction_body
        closed = closed_construction_body(body, construction_calls)
        if selected_destructors and not closed:
            raise CppDiagnostic('cpp.unsupported.destructor-subobjects',
                                'unknown construction effects require partial member exception cleanup', symbol.coord)
        symbol.closed_construction_body = closed
        symbol.closed_normal_body = call_free_body(body)
    return c_ast.FuncDef(declaration, None, body, symbol.coord)


def pattern_member_access(parser, expression, operator, token):
    """Bind current-instantiation fields to their declaration identity."""
    from pcc.frontends.cpp.parser import Expression
    from pcc.frontends.cpp.templates import Pattern
    from pcc.frontends.cpp.types import qualify_const
    dtype = unqualified(expression.dtype)
    if operator == '->':
        if not isinstance(dtype, PointerType):
            raise CppDiagnostic('cpp.invalid-member-access', 'arrow requires a class pointer', token.coord)
        dtype = dtype.pointee
    owner = unqualified(dtype)
    current = parser.class_template_method.owner
    if getattr(owner, 'cpp_nested_class_pattern', False) and owner.owner is current:
        # [temp.dep.type]/6: a member class of the current instantiation is
        # looked up at the definition; each specialization's own member
        # class supplies the field when the body is instantiated.
        symbol = owner.member_scope.names.get(token.value)
        if symbol is None or symbol.kind != 'member':
            raise CppDiagnostic('cpp.no-member', "member class '" + owner.name + "' has no data member "
                                + token.value, token.coord)
        if symbol.access != 'public':
            unsupported(token.coord, 'non-public members of member classes are not implemented in member Patterns')
        if operator == '.' and not expression.lvalue:
            unsupported(token.coord, 'class template temporary member access requires materialization')
        result_type = symbol.dtype
        if isinstance(dtype, ConstType):
            result_type = qualify_const(result_type)
        return Expression(Pattern('member-field', token.coord, (symbol, operator, token), [expression]),
                          result_type, True, symbol, decltype_entity=symbol.dtype)
    if owner is not current.injected:
        unsupported(token.coord, 'dependent/foreign-class member lookup is not implemented in member Patterns')
    symbol = current.member_scope.names.get(token.value)
    if symbol is None and getattr(current, 'base', None) is not None:
        from pcc.frontends.cpp.inheritance import find_member
        if find_member(current.base.dtype, token.value)[0] is not None:
            raise CppDiagnostic('cpp.unsupported.class-templates',
                                'base members named in class template members are not implemented', token.coord)
    if symbol is None:
        raise CppDiagnostic('cpp.no-member', 'class template has no member ' + token.value, token.coord)
    parser.scope.check_member_access(symbol, token.coord)
    if symbol.kind == 'function':
        from pcc.frontends.cpp.members import is_static_group
        if not parser._accept('('):
            unsupported(token.coord, 'member function values require pointer-to-member semantics')
        return pattern_call(parser, symbol, expression, parser._call_arguments(), token.coord,
                            is_static_group(symbol), operator == '->')
    if symbol.kind != 'member':
        unsupported(token.coord, 'member Pattern does not designate a value')
    if operator == '.' and not expression.lvalue:
        unsupported(token.coord, 'class template temporary member access requires materialization')
    result_type = symbol.dtype
    if isinstance(dtype, ConstType) and not symbol.mutable:
        result_type = qualify_const(result_type)
    return Expression(Pattern('member-field', token.coord, (symbol, operator, token), [expression]),
                      result_type, True, symbol, decltype_entity=symbol.dtype)


def pattern_call(parser, group, receiver, arguments, coord, static, address_available=True):
    from pcc.frontends.cpp.parser import Expression
    from pcc.frontends.cpp.templates import Pattern
    from pcc.frontends.cpp.members import select_member_overload
    from pcc.frontends.cpp.sema import resolve_overload
    owner = parser.class_template_method.owner
    if receiver is not None and not address_available:
        receiver = parser._unary_expression('&', receiver, coord)
        address_available = True
    if getattr(group, 'class_owner', None) is not owner.injected:
        unsupported(coord, 'foreign-class member call binding is not implemented in class template Patterns')
    candidates = [method for method in group.overloads if len(method.parameters) == len(arguments)]
    if not candidates:
        raise CppDiagnostic('cpp.no-viable-overload', 'no member has the supplied argument count', coord)
    if not static and isinstance(unqualified(receiver.dtype).pointee, ConstType):
        candidates = [method for method in candidates if method.method_const]
        if not candidates:
            raise CppDiagnostic('cpp.no-viable-overload', 'const object has no viable member overload', coord)
    selected = None
    if (not any(dependent(argument.dtype) for argument in arguments)
            and not any(dependent(dtype) for method in candidates for dtype in method.parameters)):
        if static:
            selected = resolve_overload(group, [arg.dtype for arg in arguments], parser.types, coord,
                                        [arg.null_pointer for arg in arguments],
                                        [arg.lvalue or arg.assignment_result for arg in arguments])
        else:
            selected = select_member_overload(parser, group, receiver, arguments, coord)
        parser.scope.check_member_access(selected, coord)
    dtype = selected.dtype if selected is not None else candidates[0].dtype if len(candidates) == 1 else None
    children = ([] if receiver is None else [receiver]) + list(arguments)
    result = dtype.referent if isinstance(dtype, ReferenceType) else dtype
    return Expression(Pattern('member-call', coord,
                              (group, static, receiver is not None, address_available), children), result,
                      isinstance(dtype, ReferenceType))
