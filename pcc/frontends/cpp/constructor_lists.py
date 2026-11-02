"""Local direct-list C1 arguments, N4659 [dcl.init.list]/3.6,4,7.

The candidate-set guard excludes initializer-list/class-parameter constructors;
class-value arguments and nested braced clauses remain explicit boundaries.
Existing overload selection owns candidate ranking. This owner applies list
constraints afterward and sequences the selected parameter initializations.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.classes import is_class_type
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.initializers import (
    ListInitializer,
    check_list_conversion,
    parse_initializer_list,
)
from pcc.frontends.cpp.types import ReferenceType, array_type, unqualified


def parse_arguments(parser):
    initializer = parse_initializer_list(parser)
    arguments = []
    for clause in initializer.clauses:
        if isinstance(clause, ListInitializer):
            raise CppDiagnostic(
                "cpp.unsupported.constructor-list-initialization",
                "braced constructor arguments require list-specific overload resolution", clause.coord,
            )
        if is_class_type(clause.dtype):
            raise CppDiagnostic(
                "cpp.unsupported.class-initialization",
                "class constructor arguments require copy/conversion lifetime semantics", clause.node.coord,
            )
        arguments.append(parser._decay_array(clause))
    return arguments


def require_simple_candidates(group, coord):
    # A declaration-only class-reference parameter is representable already.
    # Do not silently ignore such a candidate: it could be an initializer-list
    # constructor whose first-phase viability differs from scalar argument ranks.
    for candidate in group.overloads:
        for dtype in candidate.parameters:
            plain = unqualified(dtype.referent if isinstance(dtype, ReferenceType) else dtype)
            if is_class_type(plain) or array_type(plain):
                raise CppDiagnostic(
                    "cpp.unsupported.constructor-list-initialization",
                    "class/array constructor candidates require list-specific overload resolution", coord,
                )


def sequence_arguments(parser, arguments, parameters):
    """Evaluate each original clause/conversion once, in brace order.

    Scalar parameters are copy-initialized, so the existing list-conversion
    check applies (including no implicit nullptr-to-bool conversion). References
    retain the existing direct-binding/temporary-lifetime rules. Saving the
    bound address, not the referent value, preserves aliasing across clauses.
    """
    declarations, values = [], []
    for expression, dtype in zip(arguments, parameters):
        coord = expression.node.coord
        if not isinstance(dtype, ReferenceType):
            check_list_conversion(expression, dtype, parser.types, coord)
        converted = parser._convert(expression, dtype, coord)
        name = parser._fresh_projection_name("__pcc_cpp_constructor_arg_")
        declarations.append(parser._decl(name, dtype, coord, converted))
        values.append(c_ast.ID(name, coord))
    return declarations, values
