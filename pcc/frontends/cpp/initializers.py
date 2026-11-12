"""Checked C++17 explicit-brace aggregate initialization over the shared C AST.

This layer retains typed initializer clauses in source order and adds only
list-initialization constraints to the existing arithmetic/pointer conversion
owner. Constructor overloads, class copies, brace elision and floating/object
constant evaluation remain explicit capability boundaries. No host floating
point or C constant evaluator substitutes for missing C++ constexpr semantics.

Rules: WG21 N4659 [dcl.init.aggr], [dcl.init.list], [expr.const], with P1957R2's
pointer-to-bool narrowing defect correction against C++17. Rule tests are
original PCC inputs, not copied GCC or Clang test-suite sources.
"""

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.classes import (
    check_aggregate_destructor_access,
    empty_list_initialize,
    is_class_type,
    require_complete,
)
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.constants import KNOWN, NOT_CONSTANT
from pcc.frontends.cpp.types import PointerType, array_type, is_enum_type, unqualified


_FLOAT_RANKS = {"float": 1, "double": 2, "long double": 3}


class ListInitializer:
    """One braced list; children are lists or existing typed Expressions."""

    def __init__(self, clauses, coord):
        self.clauses = tuple(clauses)
        self.coord = coord


def parse_initializer_list(parser):
    """Consume exactly one braced list, without token replay or conversions."""
    if parser.template_function is not None:
        raise CppDiagnostic(
            "cpp.unsupported.class-templates",
            "class list-initializers in templates are not implemented",
            parser._peek().coord,
        )
    opening = parser._expect("{")
    clauses = []
    if parser._accept("}"):
        return ListInitializer(clauses, opening.coord)
    while True:
        if parser._peek().value == "{":
            clause = parse_initializer_list(parser)
        else:
            clause = parser._expression()
            # Retain the type of a class leaf so Sema can reject class-copy
            # paths rather than accidentally brace-eliding or casting them.
            parser._require_value(clause, allow_class=True)
        clauses.append(clause)
        if parser._accept("}"):
            return ListInitializer(clauses, opening.coord)
        parser._expect(",")
        if parser._accept("}"):
            return ListInitializer(clauses, opening.coord)


def _integral_range(dtype, types):
    if is_enum_type(dtype):
        return unqualified(dtype).value_range
    if dtype == "bool":
        return (0, 1)
    width = types.width(dtype)
    if types.unsigned(dtype):
        return (0, (1 << width) - 1)
    return (-(1 << (width - 1)), (1 << (width - 1)) - 1)


def check_list_conversion(expression, dtype, types, coord):
    """Check a copy-initialized aggregate element without evaluating it.

    Value-dependent integral conversions consume the shared typed constant
    proof. Unmodeled floating/object cases retain a capability diagnostic.
    A const-looking source spelling, host float conversion, or C constant
    evaluator is not a substitute.
    This is not general direct-list-initialization: e.g. nullptr-to-bool has a
    special direct-initialization rule, but is not an implicit conversion for
    an aggregate element. Reuse the shared implicit conversion owner here.
    """
    # List constraints see the standard array-to-pointer transformation too;
    # otherwise a bool element would bypass the pointer-narrowing rule below.
    # Preserve the original array's element qualifiers before stripping cv.
    source = (PointerType(expression.dtype.element) if array_type(expression.dtype)
              else unqualified(expression.dtype))
    destination = unqualified(dtype)
    if is_class_type(source) or is_class_type(destination):
        raise CppDiagnostic(
            "cpp.unsupported.class-initialization",
            "class-copy and user-defined list conversions are not implemented", coord,
        )
    if types.conversion_rank(source, destination, expression.null_pointer) is None:
        raise CppDiagnostic("cpp.invalid-conversion", "invalid list-initialization conversion", coord)
    if source == destination:
        return
    # P1957R2 is a defect correction against C++17. A pointer's bool conversion
    # exists, but does not make its use in a list non-narrowing.
    if isinstance(source, PointerType) and destination == "bool":
        raise CppDiagnostic("cpp.narrowing-conversion", "pointer-to-bool list conversion narrows", coord)
    source_integral = types.integral_operand(source)
    destination_integral = types.integral(destination)
    if source in _FLOAT_RANKS and destination_integral:
        raise CppDiagnostic("cpp.narrowing-conversion", "floating-to-integral list conversion narrows", coord)
    needs_constant = False
    if source_integral and destination_integral:
        source_min, source_max = _integral_range(source, types)
        destination_min, destination_max = _integral_range(destination, types)
        needs_constant = source_min < destination_min or source_max > destination_max
    elif source_integral and destination in _FLOAT_RANKS:
        # Even short -> double requires the constant-expression exception; the
        # rule is not relaxed merely because every short value is representable.
        needs_constant = True
    elif source in _FLOAT_RANKS and destination in _FLOAT_RANKS:
        needs_constant = _FLOAT_RANKS[source] > _FLOAT_RANKS[destination]
    if needs_constant:
        if source_integral and destination_integral:
            fact = expression.constant_fact
            # Only the typed C++ proof owner may discharge this exception.
            # An arbitrary C Constant node or a source spelling is not proof.
            if fact is not None and fact.dtype == source:
                if fact.status == KNOWN:
                    if destination_min <= fact.value <= destination_max:
                        return
                    raise CppDiagnostic(
                        "cpp.narrowing-conversion",
                        "constant value is not representable in the destination type", coord,
                    )
                if fact.status == NOT_CONSTANT:
                    raise CppDiagnostic(
                        "cpp.narrowing-conversion",
                        "integer list conversion requires a constant expression: " + fact.reason,
                        coord,
                    )
        raise CppDiagnostic(
            "cpp.unsupported.narrowing-analysis",
            "this list conversion requires typed constant-expression analysis", coord,
        )


def _zero_initializer(dtype, coord):
    if array_type(dtype):
        from pcc.frontends.cpp.arrays import zero_array_initializer
        return zero_array_initializer(dtype, coord)
    if is_class_type(dtype):
        raise CppDiagnostic(
            "cpp.unsupported.class-initialization",
            "class value-initialization requires the scoped aggregate owner", coord,
        )
    # This is value-initialization, not a narrowing conversion from a user
    # clause. The existing C initializer owner performs target scalar zeroing.
    return c_ast.Constant("int", "0", coord)


def _lower_scalar(dtype, initializer, types, convert, coord):
    if isinstance(initializer, ListInitializer):
        if not initializer.clauses:
            return _zero_initializer(dtype, initializer.coord)
        if len(initializer.clauses) != 1:
            raise CppDiagnostic(
                "cpp.too-many-initializers", "a scalar subobject takes at most one initializer",
                initializer.coord,
            )
        clause = initializer.clauses[0]
        # [dcl.init.list]/3.8 requires a single element of a type E. A nested
        # braced-init-list has no type; recursively peeling it would accept
        # invalid scalar forms such as int{{1}} or int{{}}.
        if isinstance(clause, ListInitializer):
            raise CppDiagnostic(
                "cpp.invalid-scalar-initializer",
                "a scalar initializer list cannot contain another braced list",
                clause.coord,
            )
        return _lower_scalar(dtype, clause, types, convert, initializer.coord)
    check_list_conversion(initializer, dtype, types, coord)
    return convert(initializer, dtype, coord)


def require_union_list_storage(dtype, types, coord):
    """A union list stores only its first member; the other bytes must be zero.

    Zero-initialization also zeroes a union's padding ([dcl.init]/6.3), but the
    C initializer stores only the first member. Admit unions whose first
    member covers the whole object.
    """
    from pcc.frontends.cpp.size_queries import object_layout
    definition = unqualified(dtype).definition
    if not definition.members:
        return
    first = object_layout(definition.members[0].dtype, types, coord).size
    if first != object_layout(dtype, types, coord).size:
        raise CppDiagnostic("cpp.unsupported.unions",
                            "list-initialization of a union whose first member is smaller than the union "
                            "is not implemented", coord)


def lower_aggregate_initializer(dtype, initializer, types, convert, *, scope):
    """Return ordered C InitList storage only after C++ list Sema succeeds.

    ``convert`` is the existing bound parser._convert callback. No other
    implicit conversion policy is introduced by this module. Both direct-list
    and copy-list initialization of an aggregate copy-initialize its elements;
    no direct-init-only nullptr-to-bool exception is introduced for a field.
    """
    if not is_class_type(dtype):
        raise TypeError("aggregate initializer lowering requires a class type")
    if not isinstance(initializer, ListInitializer):
        raise TypeError("aggregate initializer lowering requires a braced initializer tree")
    require_complete(dtype, initializer.coord)
    from pcc.frontends.cpp.default_member_initializers import has_initializers, unsupported
    if has_initializers(unqualified(dtype).definition):
        unsupported(initializer.coord)
    if not initializer.clauses:
        return empty_list_initialize(dtype, initializer.coord, scope=scope)
    definition = unqualified(dtype).definition
    if not definition.aggregate:
        raise CppDiagnostic(
            "cpp.unsupported.class-constructors",
            "nonempty initialization of a nonaggregate requires constructor semantics",
            initializer.coord,
        )
    # An aggregate initialized from a single same-type class expression uses
    # copy semantics rather than an element list. Class-expression leaves also
    # require copy/conversion semantics when they initialize a nested member.
    if (len(initializer.clauses) == 1
            and not isinstance(initializer.clauses[0], ListInitializer)
            and is_class_type(initializer.clauses[0].dtype)):
        raise CppDiagnostic(
            "cpp.unsupported.class-initialization",
            "class-expression initializer clauses require copy/conversion semantics",
            initializer.coord,
        )
    check_aggregate_destructor_access(definition, scope)
    members = definition.members
    if unqualified(dtype).is_union:
        # [dcl.init.aggr]/16: a union's list initializes its first member.
        require_union_list_storage(dtype, types, initializer.coord)
        members = members[:1]
    values = []
    for index, member in enumerate(members):
        if index >= len(initializer.clauses):
            values.append(empty_list_initialize(member.dtype, initializer.coord, scope=scope)
                          if is_class_type(member.dtype)
                          else _zero_initializer(member.dtype, initializer.coord))
            continue
        clause = initializer.clauses[index]
        if array_type(member.dtype):
            if not isinstance(clause, ListInitializer):
                raise CppDiagnostic('cpp.unsupported.aggregate-brace-elision',
                                    'an array subobject requires an explicit braced initializer at this boundary',
                                    clause.node.coord)
            from pcc.frontends.cpp.arrays import lower_array_initializer
            values.append(lower_array_initializer(member.dtype, clause, types, convert))
        elif is_class_type(member.dtype):
            if not isinstance(clause, ListInitializer):
                code = ("class-initialization" if is_class_type(clause.dtype)
                        else "aggregate-brace-elision")
                raise CppDiagnostic(
                    "cpp.unsupported." + code,
                    "a class subobject requires an explicit braced initializer at this boundary",
                    clause.node.coord,
                )
            values.append(lower_aggregate_initializer(member.dtype, clause, types, convert, scope=scope))
        else:
            values.append(_lower_scalar(member.dtype, clause, types, convert, initializer.coord))
    # Do not check this before examining class members: a valid brace-elided
    # list may have more clauses than direct members, and must remain a missing
    # capability diagnostic rather than an incorrect ill-formed diagnosis.
    if len(initializer.clauses) > len(members):
        raise CppDiagnostic(
            "cpp.too-many-initializers", "too many aggregate initializer clauses", initializer.coord
        )
    if not values:
        values.append(c_ast.Constant("int", "0", initializer.coord))
    return c_ast.InitList(values, initializer.coord)
