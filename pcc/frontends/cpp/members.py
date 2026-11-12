"""Member functions with nominal ownership and explicit static/object rules.

N4659 [class.mfct], [over.match.funcs], [class.access], [expr.call]; Itanium
ABI 3.1.2.1 and 5.1.5. For non-static members, the implicit this argument is
a real first parameter, not part of the source parameter list or its mangled
encoding. Static members have no object parameter ([class.static.mfct]). No object
header, second layout engine or private runtime is added. An unqualified call
of a virtual member loads its callee through virtual_functions.py.

The first emission boundary is non-inline declarations and out-of-class
bodies. In-class bodies require C++ inline/ODR emission, not C's TU-local
inline behavior, and therefore remain an explicit capability boundary.
"""
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.constants import not_constant
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.sema import Symbol, resolve_overload
from pcc.frontends.cpp.templates import same_type
from pcc.frontends.cpp.types import ConstType, PointerType, _mangle_type, unqualified


def mangle_member(owner, name, parameters, const=False, variadic=False):
    substitutions = []
    prefix = _mangle_type(unqualified(owner), substitutions)
    # Use the type owner's real namespace/template-prefix substitution order.
    # Only nested-name framing is removed; template-argument E remains intact.
    if prefix.startswith("N") and prefix.endswith("E"):
        prefix = prefix[1:-1]
    encoded_name = {"operator=": "aS", "operator[]": "ix"}.get(name, str(len(name)) + name)
    encoded = "_ZN" + ("K" if const else "") + prefix + encoded_name + "E"
    return encoded + ("".join(_mangle_type(unqualified(t), substitutions)
                              for t in parameters) + ("z" if variadic else "") or "v")


def declare_member_function(definition, symbol, const=False, outside=False, static=False, encode_name=True):
    if static and const:
        raise CppDiagnostic("cpp.static-member-cv", "a static member function cannot have cv-qualifiers", symbol.coord)
    scope = definition.scope
    group = scope.names.get(symbol.name)
    if group is not None and (group.kind != "function"
                              or getattr(group, "class_owner", None) is not definition.dtype):
        raise CppDiagnostic("cpp.redeclaration", "member function conflicts with a member", symbol.coord)
    if symbol.name == definition.dtype.name:
        raise CppDiagnostic("cpp.invalid-constructor", "a constructor cannot have a return type", symbol.coord)
    if group is not None:
        for previous in group.overloads:
            if previous.variadic != symbol.variadic:
                continue
            previous_static = getattr(previous, "method_static", False)
            if previous.parameters == symbol.parameters and (static or previous_static):
                if not outside and static != previous_static:
                    raise CppDiagnostic("cpp.static-nonstatic-overload",
                                        "static and non-static members cannot share an explicit signature", symbol.coord)
                if const:
                    raise CppDiagnostic("cpp.static-member-cv", "a static member function cannot have cv-qualifiers", symbol.coord)
            if previous.parameters != symbol.parameters or previous.method_const != const:
                continue
            if not same_type(previous.dtype, symbol.dtype):
                raise CppDiagnostic("cpp.conflicting-return-type", "member return type differs", symbol.coord)
            if not outside:
                raise CppDiagnostic("cpp.redeclaration", "a member cannot be declared twice in its class", symbol.coord)
            if previous.defined:
                raise CppDiagnostic("cpp.redefinition", "member function already defined", symbol.coord)
            previous.defined = True
            return previous
    if outside:
        raise CppDiagnostic("cpp.no-matching-member", "definition does not match a declared member function", symbol.coord)
    if group is not None and any(getattr(previous, "method_static", False) != static
                                 for previous in group.overloads):
        raise CppDiagnostic("cpp.unsupported.mixed-static-overloads",
                            "mixed static/non-static overload sets require implicit-object ranking", symbol.coord)
    symbol.class_owner = definition.dtype
    symbol.method_const = const
    symbol.method_static = static
    symbol.access = definition.access
    symbol.owner = scope
    symbol.language_linkage = "C++"
    symbol.internal_linkage = definition.dtype.internal_linkage
    if encode_name:
        symbol.c_name = mangle_member(definition.dtype, symbol.name, symbol.parameters, const, symbol.variadic)
    if group is None:
        group = Symbol(symbol.name, "function", symbol.coord)
        group.class_owner = definition.dtype
        # Check access on the selected overload, not the overload-set name.
        group.access = "public"
        scope.declare(group)
    group.overloads.append(symbol)
    return symbol


def object_pointer_type(method):
    owner = ConstType(method.class_owner) if method.method_const else method.class_owner
    return PointerType(owner)


def select_member_overload(parser, group, object_expression, arguments, coord):
    """Shared implicit-object ranking, usable before body projection."""
    candidates = Symbol(group.name, "function", coord)
    for method in group.overloads:
        candidate = Symbol(method.name, "function", method.coord, dtype=method.dtype,
                           parameters=(object_pointer_type(method),) + method.parameters)
        candidate.variadic = method.variadic
        candidate.member_origin = method
        candidate.default_arguments = (None,) + tuple(getattr(method, "default_arguments", (None,) * len(method.parameters)))
        candidates.overloads.append(candidate)
    actuals = [object_expression] + arguments
    return resolve_overload(candidates, [arg.dtype for arg in actuals], parser.types,
                                coord, [arg.null_pointer for arg in actuals],
                                [arg.lvalue or arg.assignment_result for arg in actuals]).member_origin


def call_member(parser, group, object_expression, arguments, coord, address_available=True,
                virtual_dispatch=True):
    if getattr(parser, 'class_template_method', None) is not None:
        from pcc.frontends.cpp.class_template_members import pattern_call
        return pattern_call(parser, group, object_expression, arguments, coord, False, address_available)
    if parser.template_function is not None or (parser.instantiating_template and getattr(parser, 'class_template_instance', None) is None):
        raise CppDiagnostic("cpp.unsupported.member-templates", "member calls in templates require dependent lookup", coord)
    arguments = [parser._decay_array(arg) for arg in arguments]
    # Use the same partial-order conversion owner as ordinary overloads. The
    # implicit-object candidate is local; canonical explicit signatures remain
    # unchanged for redeclaration identity and ABI encoding.
    selected = select_member_overload(parser, group, object_expression, arguments, coord)
    parser._check_call_value_abi(selected, coord)
    parser.scope.check_member_access(selected, coord)
    from pcc.frontends.cpp.class_template_members import request_body
    request_body(parser, selected, coord)
    if not address_available:
        raise CppDiagnostic("cpp.unsupported.member-object-address",
                            "dot member calls require shared address-only aggregate lowering", coord)
    from pcc.frontends.cpp.default_arguments import expand
    arguments = expand(parser, selected, arguments, coord)
    this_type = object_pointer_type(selected)
    # This is compiler-created storage, not a source local declaration.
    # Its initializer and explicit arguments may themselves name this_argument;
    # never let C point-of-declaration shadow the already-resolved C++ binding.
    name = parser._fresh_projection_name("__pcc_cpp_this_")
    temporary = parser._decl(name, this_type, coord,
                             parser._convert(object_expression, this_type, coord))
    from pcc.frontends.cpp.references import convert_call_arguments
    converted = [c_ast.ID(name, coord)] + convert_call_arguments(parser, arguments, selected.parameters, coord)
    if selected.variadic:
        from pcc.frontends.cpp.varargs import convert_ellipsis_arguments
        converted += convert_ellipsis_arguments(parser, arguments[len(selected.parameters):], coord)
    function = c_ast.ID(selected.c_name, coord)
    if virtual_dispatch and getattr(selected, "method_virtual", False):
        # [class.virtual]/16: a qualified name suppresses the virtual call.
        from pcc.frontends.cpp.virtual_functions import callee
        function = callee(selected, c_ast.ID(name, coord), coord)
    call = c_ast.FuncCall(function, c_ast.ExprList(converted, coord), coord)
    # [expr.call]: postfix-expression (including object) precedes every
    # argument. Capture once before evaluating arguments; do not rely on C's
    # argument evaluation order, or repeat an effectful object expression.
    node = c_ast.StmtExpr(c_ast.Compound([temporary, call], coord), coord)
    from pcc.frontends.cpp.references import call_result, ordinary_class_result_context
    return call_result(node, selected.dtype, coord,
                       allow_class=ordinary_class_result_context(parser))


def is_static_group(group):
    """Every admitted overload set is homogeneous until object-rank support."""
    return bool(group.overloads) and all(getattr(method, "method_static", False)
                                         for method in group.overloads)


def call_static_member(parser, group, arguments, coord, discarded_base=None):
    # [class.static.mfct]: no this parameter. [expr.ref]/[expr.call]: an
    # object-qualified postfix expression is still evaluated before arguments.
    from pcc.frontends.cpp.parser import Expression

    if getattr(parser, 'class_template_method', None) is not None:
        from pcc.frontends.cpp.class_template_members import pattern_call
        if discarded_base is not None:
            raise CppDiagnostic('cpp.unsupported.member-templates', 'untyped discarded receiver cannot enter a method Pattern', coord)
        return pattern_call(parser, group, None, arguments, coord, True)
    if parser.template_function is not None or (parser.instantiating_template and getattr(parser, 'class_template_instance', None) is None):
        raise CppDiagnostic("cpp.unsupported.member-templates",
                            "member calls in templates require dependent lookup", coord)
    arguments = [parser._decay_array(arg) for arg in arguments]
    selected = resolve_overload(group, [arg.dtype for arg in arguments], parser.types,
                                coord, [arg.null_pointer for arg in arguments],
                                [arg.lvalue or arg.assignment_result for arg in arguments])
    parser._check_call_value_abi(selected, coord)
    parser.scope.check_member_access(selected, coord)
    from pcc.frontends.cpp.class_template_members import request_body
    request_body(parser, selected, coord)
    from pcc.frontends.cpp.default_arguments import expand
    arguments = expand(parser, selected, arguments, coord)
    from pcc.frontends.cpp.references import convert_call_arguments
    converted = convert_call_arguments(parser, arguments, selected.parameters, coord)
    if selected.variadic:
        from pcc.frontends.cpp.varargs import convert_ellipsis_arguments
        converted += convert_ellipsis_arguments(parser, arguments[len(selected.parameters):], coord)
    node = c_ast.FuncCall(c_ast.ID(selected.c_name, coord), c_ast.ExprList(converted, coord), coord)
    if discarded_base is not None:
        node = c_ast.ExprList([discarded_base, node], coord)
    from pcc.frontends.cpp.references import call_result, ordinary_class_result_context
    return call_result(node, selected.dtype, coord,
                       allow_class=ordinary_class_result_context(parser))
