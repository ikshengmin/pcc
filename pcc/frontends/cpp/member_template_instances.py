"""ODR-use and ABI of function templates owned by a non-template class.

The existing shared registry owns specialization publication. Speculative overload
candidates never enter it. The source parameter list excludes the implicit object;
only the final C projection prepends the real this pointer.
"""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.sema import Symbol
from pcc.frontends.cpp.templates import IntegralArgument, mangle_argument
from pcc.frontends.cpp.types import ReferenceType, _mangle_type


def mangle_member_instance(origin, arguments):
    """Itanium nested-name: cv owner + template-id + original return/parameters."""
    from pcc.frontends.cpp.template_abi import mangle_template_type
    owner = origin.class_owner
    if not origin.name.isidentifier():
        raise CppDiagnostic('cpp.unsupported.member-templates',
                            'operator member templates require their own ABI names', origin.coord)
    if getattr(owner, 'template_origin', None) is not None:
        raise CppDiagnostic('cpp.unsupported.member-templates',
                            'member templates of class templates require two template levels', origin.coord)
    substitutions = []
    prefix = _mangle_type(owner, substitutions)
    if prefix.startswith('N') and prefix.endswith('E'):
        prefix = prefix[1:-1]
    name = str(len(origin.name)) + origin.name
    substitutions.append(('member-function-template-prefix', origin))
    args = ''.join(mangle_argument(a) if isinstance(a, IntegralArgument)
                   else _mangle_type(a, substitutions) for a in arguments)
    result = '_ZN' + ('K' if origin.method_const else '') + prefix + name + 'I' + args + 'EE'
    result += mangle_template_type(origin.dtype, substitutions)
    result += ''.join(mangle_template_type(t, substitutions) for t in origin.parameters) or 'v'
    if origin.variadic:
        result += 'z'
    return result


def concrete_type(parser, dtype, bindings):
    from pcc.frontends.cpp.class_templates import specialization
    from pcc.frontends.cpp.templates import substitute
    return substitute(dtype, bindings,
                      instantiate_class=lambda symbol, args, at: specialization(parser, symbol, args, at))


def make_candidate(parser, origin, bindings):
    """Materialize a checked signature without requesting an unselected body."""
    existing = origin.instances.get(bindings)
    if existing is not None:
        return existing
    candidate = Symbol(origin.name, 'function', origin.coord,
                       c_name=mangle_member_instance(origin, bindings),
                       dtype=concrete_type(parser, origin.dtype, bindings),
                       parameters=tuple(concrete_type(parser, dtype, bindings) for dtype in origin.parameters),
                       arity=len(origin.parameters))
    candidate.template_origin = origin
    candidate.template_arguments = bindings
    for key in ('class_owner', 'method_const', 'method_static', 'access', 'owner',
                'internal_linkage', 'variadic', 'nonthrowing', 'noreturn', 'deleted_template'):
        setattr(candidate, key, getattr(origin, key))
    candidate.definition_linkage = 'external_odr'
    return candidate


def request_selected(parser, candidate, coord):
    """Only winning, accessible, potentially evaluated calls become ODR uses."""
    from pcc.backend.definition_linkage import supports_external_odr_function_definitions
    parser.scope.check_member_access(candidate, coord)
    if candidate.deleted_template:
        raise CppDiagnostic('cpp.deleted-function', 'selected member template is deleted', coord)
    if parser.unevaluated_depth:
        return candidate
    if not supports_external_odr_function_definitions(parser.types.target_triple):
        raise CppDiagnostic('cpp.unsupported.external-odr',
                            'member template instantiations require owned external-ODR definitions', coord)
    instance = parser.instance_registry.request((candidate.template_origin, candidate.template_arguments),
                                                candidate, context=coord).value
    from pcc.frontends.cpp.member_placement_patterns import bind_selected_poi
    bind_selected_poi(parser, instance, coord)
    return instance


def instantiate_body(parser, instance, request_coord):
    from pcc.frontends.cpp.parser import Expression
    from pcc.frontends.cpp.lifetimes import AutomaticLifetimes
    from pcc.frontends.cpp.members import object_pointer_type
    origin = instance.template_origin
    if origin.pattern is None:
        raise CppDiagnostic('cpp.template-definition', 'selected member template has no available definition', request_coord)
    fields = ('return_type', 'instantiating_template', 'template_function', 'member_function',
              'this_symbol', 'current_function', 'scope', 'lifetimes')
    saved = {key: getattr(parser, key) for key in fields}
    old_context = getattr(parser, 'member_template_instance', None)
    environment, parameters = {}, []
    this = None
    if not instance.method_static:
        source_this = origin.this_symbol
        this = Symbol('this', 'value', origin.coord, c_name=source_this.c_name,
                      dtype=object_pointer_type(instance))
        parameters.append(parser._decl(this.c_name, this.dtype, this.coord))
        environment[id(source_this)] = Expression(c_ast.ID(this.c_name, this.coord), this.dtype)
    for source, abi_type in zip(origin.parameter_symbols, instance.parameters):
        # [dcl.fct]: top-level cv is absent from the function type (and ABI)
        # but belongs to the parameter object declared in this definition.
        # It must survive lookup, overload resolution, and assignment in the body.
        dtype = abi_type if source is None else concrete_type(parser, source.dtype,
                                                              instance.template_arguments)
        coord = origin.coord if source is None else source.coord
        if dtype == 'void':
            raise CppDiagnostic('cpp.invalid-parameter', 'substitution formed a void parameter', coord)
        if source is None:
            # An unnamed function parameter occupies a real argument position
            # but cannot be found by lookup in the template definition.
            name = parser._fresh_projection_name('__pcc_cpp_unnamed_')
            parameters.append(parser._decl(name, dtype, coord))
            continue
        parameters.append(parser._decl(source.c_name, dtype, coord))
        node = c_ast.ID(source.c_name, source.coord)
        if isinstance(dtype, ReferenceType):
            environment[id(source)] = Expression(c_ast.UnaryOp('*', node, source.coord),
                                                dtype.referent, True, discarded_known=True, discarded_node=node)
        else:
            environment[id(source)] = Expression(node, dtype, True)
    declaration = parser._decl(instance.c_name, instance.dtype, origin.coord)
    declaration.type = c_ast.FuncDecl(c_ast.ParamList(parameters, origin.coord), declaration.type, origin.coord)
    if instance.internal_linkage:
        declaration.storage = ['static']
    else:
        declaration.definition_linkage = 'external_odr'
    parser.return_type, parser.member_function = instance.dtype, instance
    parser.this_symbol, parser.current_function = this, instance
    parser.scope = instance.class_owner.definition.scope
    parser.instantiating_template, parser.template_function = True, None
    parser.lifetimes = AutomaticLifetimes()
    parser.member_template_instance = instance
    try:
        body = parser._instantiate_statement(origin.pattern, instance.template_arguments, environment)
    except CppDiagnostic as error:
        raise CppDiagnostic(error.code, error.message + ' (while instantiating ' + instance.c_name
                            + ' requested at ' + str(request_coord) + ')', error.coord)
    finally:
        for key, value in saved.items():
            setattr(parser, key, value)
        parser.member_template_instance = old_context
    return c_ast.FuncDef(declaration, None, body, origin.coord)
