"""Non-static member functions with nominal ownership and an implicit object.

N4659 [class.mfct], [over.match.funcs], [class.access], [expr.call]; Itanium
ABI 3.1.2.1 and 5.1.5. The implicit this argument is a real first parameter,
not part of the source parameter list or its mangled encoding. No object
header, second layout engine, virtual dispatch or private runtime is added.

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


def mangle_member(owner, name, parameters, const=False):
    namespaces = owner.qualified_name[:-1]
    substitutions = [("namespace-prefix", namespaces[:i + 1])
                     for i in range(len(namespaces))]
    parts = namespaces
    prefix = ""
    if parts and parts[0] == "std":
        prefix, parts = "St", parts[1:]
        substitutions = substitutions[1:]
    prefix += "".join(str(len(part)) + part for part in parts)
    prefix += str(len(owner.name)) + owner.name
    # The complete owner is a TYPE substitution, not a namespace spelling.
    substitutions.append(owner)
    encoded = "_ZN" + ("K" if const else "") + prefix + str(len(name)) + name + "E"
    return encoded + ("".join(_mangle_type(unqualified(t), substitutions)
                              for t in parameters) or "v")


def declare_member_function(definition, symbol, const=False, outside=False):
    scope = definition.scope
    group = scope.names.get(symbol.name)
    if group is not None and (group.kind != "function"
                              or getattr(group, "class_owner", None) is not definition.dtype):
        raise CppDiagnostic("cpp.redeclaration", "member function conflicts with a member", symbol.coord)
    if symbol.name == definition.dtype.name:
        raise CppDiagnostic("cpp.invalid-constructor", "a constructor cannot have a return type", symbol.coord)
    if group is not None:
        for previous in group.overloads:
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
    symbol.class_owner = definition.dtype
    symbol.method_const = const
    symbol.access = definition.access
    symbol.owner = scope
    symbol.language_linkage = "C++"
    symbol.internal_linkage = definition.dtype.internal_linkage
    symbol.c_name = mangle_member(definition.dtype, symbol.name, symbol.parameters, const)
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


def call_member(parser, group, object_expression, arguments, coord, address_available=True):
    if parser.template_function is not None or parser.instantiating_template:
        raise CppDiagnostic("cpp.unsupported.member-templates", "member calls in templates require dependent lookup", coord)
    arguments = [parser._decay_array(arg) for arg in arguments]
    from pcc.frontends.cpp.classes import is_class_type
    if any(is_class_type(arg.dtype) for arg in arguments):
        raise CppDiagnostic("cpp.unsupported.class-value-abi", "class by-value arguments need lifetime ABI", coord)
    # Use the same partial-order conversion owner as ordinary overloads. The
    # implicit-object candidate is local; canonical explicit signatures remain
    # unchanged for redeclaration identity and ABI encoding.
    candidates = Symbol(group.name, "function", coord)
    for method in group.overloads:
        candidate = Symbol(method.name, "function", method.coord, dtype=method.dtype,
                           parameters=(object_pointer_type(method),) + method.parameters)
        candidate.member_origin = method
        candidates.overloads.append(candidate)
    actuals = [object_expression] + arguments
    selected = resolve_overload(candidates, [arg.dtype for arg in actuals], parser.types,
                                coord, [arg.null_pointer for arg in actuals]).member_origin
    parser.scope.check_member_access(selected, coord)
    if not address_available:
        raise CppDiagnostic("cpp.unsupported.member-object-address",
                            "dot member calls require shared address-only aggregate lowering", coord)
    this_type = object_pointer_type(selected)
    # This is compiler-created storage, not a source local declaration.
    # Its initializer and explicit arguments may themselves name this_argument;
    # never let C point-of-declaration shadow the already-resolved C++ binding.
    name = parser._fresh_projection_name("__pcc_cpp_this_")
    temporary = parser._decl(name, this_type, coord,
                             parser._convert(object_expression, this_type, coord))
    converted = [c_ast.ID(name, coord)] + [parser._convert(arg, dtype, coord)
                                        for arg, dtype in zip(arguments, selected.parameters)]
    call = c_ast.FuncCall(c_ast.ID(selected.c_name, coord), c_ast.ExprList(converted, coord), coord)
    # [expr.call]: postfix-expression (including object) precedes every
    # argument. Capture once before evaluating arguments; do not rely on C's
    # argument evaluation order, or repeat an effectful object expression.
    node = c_ast.StmtExpr(c_ast.Compound([temporary, call], coord), coord)
    dtype = unqualified(selected.dtype)
    return type(object_expression)(node, dtype,
        constant_fact=not_constant(dtype, "call to a non-constexpr member function", coord))
