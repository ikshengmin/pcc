"""Original nominal-enum rules; these are not native or whole-project gates.

Primary C++17 rules: N4659 [dcl.enum], [conv.prom], [over.ics.rank].
https://timsong-cpp.github.io/cppwp/n4659/dcl.enum
https://timsong-cpp.github.io/cppwp/n4659/conv.prom
GNU no-short-enum storage and implicit-successor policy reference:
https://github.com/gcc-mirror/gcc/blob/releases/gcc-14.2.0/gcc/cp/decl.cc
No GCC/Clang source or tests are copied. GCC is a policy reference only, not
an implementation dependency. CWG2932 is not applied as an accepted C++17 DR:
https://cplusplus.github.io/CWG/issues/2932.html
"""

import pytest

from pcc.frontends.c.parse.plyparser import Coord
from pcc.frontends.cpp.constants import KNOWN, known, not_constant, unmodeled
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.enums import (
    EnumType, enum_promotion_type, enum_promotion_types, integer_range,
    parse_enum, select_unfixed_underlying, unfixed_value_range,
)
from pcc.frontends.cpp.parser import CppParser, TokenCursor
from pcc.frontends.cpp.sema import Scope, Symbol
from pcc.frontends.cpp.types import ArithmeticTypes, ConstType, PointerType


LINUX = ArithmeticTypes("x86_64-unknown-linux-gnu")
WINDOWS = ArithmeticTypes("x86_64-pc-windows-gnu")
ARM = ArithmeticTypes("aarch64-unknown-linux-gnu")
COORD = Coord("enum-model.cpp", 3, 5)
INT_MIN, INT_MAX = -(1 << 31), (1 << 31) - 1
UINT_MAX, ULL_MAX = (1 << 32) - 1, (1 << 64) - 1


def make_enum(name="E", scoped=False, underlying=None, types=LINUX, parent=None):
    parent = Scope() if parent is None else parent
    dtype = EnumType(name, parent.namespace_path() + (name,), COORD,
                     scoped=scoped, underlying=underlying,
                     declaration_scope=parent, types=types)
    symbol = parent.declare(Symbol(name, "type", COORD, dtype=dtype))
    symbol.enum_declaration = True
    return dtype


def define_enum(values=(), scoped=False, underlying=None, types=LINUX):
    dtype = make_enum(scoped=scoped, underlying=underlying, types=types)
    dtype.begin_definition()
    for index, value in enumerate(values):
        fact = None if value is None else known(value[0], value[1], COORD)
        dtype.add_enumerator("value" + str(index), COORD, fact, types)
    dtype.finish(types)
    return dtype


def parser_for(source, target=LINUX.target_triple, scope=None):
    """Use the real lexer, expression parser, constant facts and Scope owner."""
    parser = CppParser(target)
    parser.scope = Scope() if scope is None else scope
    parser.template_function = None
    parser.parsing_template = False
    parser.cursor = TokenCursor(parser._tokenize(source, "enum-model.cpp"))
    return parser


def parse_declarations(source, target=LINUX.target_triple):
    parser = parser_for(source, target)
    while parser._peek().kind != "EOF":
        assert parse_enum(parser) is None
    return parser.scope


def test_enum_identity_is_nominal_not_underlying_or_qualified_name_equality():
    left, right = make_enum(underlying="int"), make_enum(underlying="int")
    assert left is not right and left != right
    assert len({left, right, "int"}) == 3
    assert left.cpp_enum_type
    assert left.qualified_name == right.qualified_name == ("E",)
    assert ConstType(left) != ConstType(right)


def test_fixed_opaque_enum_is_complete_before_definition_without_enumerators():
    dtype = make_enum(underlying=ConstType("unsigned char"))
    assert dtype.complete and not dtype.defined and not dtype.defining
    assert dtype.fixed and dtype.underlying == "unsigned char"
    assert dtype.value_range == (0, 255)
    assert dtype.enumerators == []
    assert dtype.scope.kind == "enum"
    assert dtype.scope.parent is dtype.declaration_scope
    assert dtype.declaration_scope.names["E"].namespace_scope is None


def test_scoped_default_base_is_int_and_has_no_implicit_promotion():
    dtype = make_enum(scoped=True)
    assert dtype.complete and dtype.fixed and dtype.underlying == "int"
    assert dtype.value_range == (INT_MIN, INT_MAX)
    assert dtype.promotion_targets(LINUX) == ()
    assert enum_promotion_type(ConstType(dtype), LINUX) is None


def test_unfixed_incomplete_type_is_not_promotable():
    dtype = make_enum()
    assert not dtype.complete and not dtype.fixed
    assert dtype.underlying is None and dtype.value_range is None
    with pytest.raises(CppDiagnostic, match="cpp.incomplete-enum"):
        dtype.promotion_type(LINUX)


@pytest.mark.parametrize("dtype,types,expected", (
    ("bool", LINUX, (0, 1)),
    ("char", LINUX, (-128, 127)),
    ("char", ARM, (0, 255)),
    ("unsigned char", LINUX, (0, 255)),
    ("short", LINUX, (-(1 << 15), (1 << 15) - 1)),
    ("long", LINUX, (-(1 << 63), (1 << 63) - 1)),
    ("long", WINDOWS, (INT_MIN, INT_MAX)),
))
def test_integral_ranges_are_owned_target_layout_not_host_width(dtype, types, expected):
    assert integer_range(ConstType(dtype), types) == expected


@pytest.mark.parametrize("minimum,maximum,types,expected", (
    (0, 0, LINUX, "unsigned int"),
    (0, 255, LINUX, "unsigned int"),
    (-1, 255, LINUX, "int"),
    (0, UINT_MAX, LINUX, "unsigned int"),
    (0, UINT_MAX + 1, LINUX, "unsigned long"),
    (0, UINT_MAX + 1, WINDOWS, "unsigned long long"),
    (-1, UINT_MAX, LINUX, "long"),
    (-1, UINT_MAX, WINDOWS, "long long"),
    (0, ULL_MAX, LINUX, "unsigned long"),
    (0, ULL_MAX, WINDOWS, "unsigned long long"),
))
def test_unfixed_storage_policy_is_not_promotion_or_short_enum_policy(minimum, maximum, types, expected):
    assert select_unfixed_underlying(minimum, maximum, types) == expected


def test_no_supported_underlying_type_can_hold_negative_and_ull_max_together():
    with pytest.raises(CppDiagnostic, match="cpp.enum-value-range"):
        select_unfixed_underlying(-1, ULL_MAX, LINUX, COORD)


def test_non_gnu_windows_unfixed_abi_policy_is_not_inferred():
    types = ArithmeticTypes("x86_64-pc-windows-msvc")
    with pytest.raises(CppDiagnostic, match="cpp.unsupported.enum-abi-policy"):
        select_unfixed_underlying(0, 1, types, COORD)
    assert make_enum(underlying="long", types=types).value_range == (INT_MIN, INT_MAX)


@pytest.mark.parametrize("minimum,maximum,expected", (
    (0, 0, (0, 0)), (0, 1, (0, 1)), (0, 2, (0, 3)),
    (2, 7, (0, 7)), (2, 8, (0, 15)),
    (-1, 0, (-1, 0)), (-1, 1, (-2, 1)),
    (-4, 3, (-4, 3)), (-4, 4, (-8, 7)),
    (-3, -2, (-4, 3)),
    # The C++17 wording is deliberately not replaced by later hypothetical-
    # integer wording or the implementation practice discussed by CWG2932.
    (INT_MIN, INT_MIN, (-(1 << 32), UINT_MAX)),
))
def test_cxx17_value_range_is_separate_from_enumerator_extrema(minimum, maximum, expected):
    assert unfixed_value_range(minimum, maximum, LINUX) == expected


def test_unresolved_extreme_range_storage_difference_is_a_capability_gate():
    dtype = make_enum()
    dtype.begin_definition()
    dtype.add_enumerator("only", COORD, known("int", INT_MIN), LINUX)
    with pytest.raises(CppDiagnostic, match="cpp.unsupported.enum-range-policy"):
        dtype.finish(LINUX)
    assert not dtype.defined


@pytest.mark.parametrize("values,underlying,promotion,value_range", (
    ((), "unsigned int", "int", (0, 0)),
    ((("int", 0),), "unsigned int", "int", (0, 0)),
    ((("int", 2),), "unsigned int", "int", (0, 3)),
    ((("int", -1), ("int", 1)), "int", "int", (-2, 1)),
    ((("unsigned int", UINT_MAX),), "unsigned int", "unsigned int", (0, UINT_MAX)),
    ((("long", UINT_MAX + 1),), "unsigned long", "long", (0, (1 << 33) - 1)),
))
def test_final_unfixed_promotion_uses_whole_enum_range(values, underlying, promotion, value_range):
    dtype = define_enum(values)
    assert dtype.complete and dtype.defined and not dtype.fixed
    assert dtype.underlying == underlying
    assert dtype.value_range == value_range
    assert dtype.promotion_targets(LINUX) == (promotion,)
    assert dtype.promotion_type(LINUX) == promotion


@pytest.mark.parametrize("underlying,expected", (
    ("bool", ("bool", "int")),
    ("char", ("char", "int")),
    ("unsigned char", ("unsigned char", "int")),
    ("unsigned short", ("unsigned short", "int")),
    ("int", ("int",)),
    ("unsigned int", ("unsigned int",)),
    ("long", ("long",)),
))
def test_fixed_promotions_preserve_underlying_target_for_overload_ranking(underlying, expected):
    dtype = make_enum(underlying=underlying)
    assert dtype.promotion_targets(LINUX) == expected
    assert dtype.promotion_types(LINUX) == expected
    assert enum_promotion_types(ConstType(dtype), LINUX) == expected
    assert dtype.promotion_type(LINUX) == expected[-1]


def test_unscoped_enumerator_has_namespace_owner_and_same_symbol_in_enum_scope():
    dtype = make_enum()
    dtype.begin_definition()
    first = dtype.add_enumerator("first", COORD, known("short", 7), LINUX)
    body_fact = first.constant_fact
    assert first.dtype == "short" and first.enum_type is dtype
    assert first.owner is dtype.declaration_scope
    assert dtype.scope.names["first"] is dtype.declaration_scope.names["first"] is first
    assert first.initialization_complete
    dtype.finish(LINUX)
    assert first.dtype is dtype and first.constant_fact.dtype is dtype
    assert first.constant_fact.status == KNOWN and first.constant_fact.value == 7
    assert body_fact.dtype == "short" and body_fact.value == 7
    assert first.constant_fact is not body_fact


def test_scoped_enumerator_has_enum_owner_and_does_not_pollute_namespace():
    dtype = make_enum(scoped=True, underlying="unsigned short")
    dtype.begin_definition()
    value = dtype.add_enumerator("E", COORD, known("int", 7), LINUX)
    assert value.owner is dtype.scope and value.dtype == "unsigned short"
    assert dtype.declaration_scope.names["E"].dtype is dtype
    assert dtype.scope.names["E"] is value
    dtype.finish(LINUX)
    assert value.dtype is dtype


def test_implicit_successor_widens_provisional_type_without_unsigned_wrap():
    dtype = make_enum()
    dtype.begin_definition()
    first = dtype.add_enumerator("first", COORD, known("unsigned int", UINT_MAX), LINUX)
    second = dtype.add_enumerator("second", COORD, None, LINUX)
    assert first.dtype == "unsigned int"
    assert second.dtype == "long" and second.enum_value == UINT_MAX + 1
    dtype.finish(LINUX)
    assert dtype.underlying == "unsigned long"


def test_implicit_successor_uses_llp64_rank_and_first_implicit_is_int_zero():
    dtype = make_enum(types=WINDOWS)
    dtype.begin_definition()
    first = dtype.add_enumerator("zero", COORD, None, WINDOWS)
    assert first.dtype == "int" and first.enum_value == 0
    dtype.add_enumerator("large", COORD, known("unsigned long", UINT_MAX), WINDOWS)
    next_value = dtype.add_enumerator("next", COORD, None, WINDOWS)
    assert next_value.dtype == "long long" and next_value.enum_value == UINT_MAX + 1


def test_implicit_successor_beyond_ull_max_does_not_wrap_to_zero():
    dtype = make_enum()
    dtype.begin_definition()
    dtype.add_enumerator("maximum", COORD, known("unsigned long long", ULL_MAX), LINUX)
    with pytest.raises(CppDiagnostic, match="cpp.enum-value-range"):
        dtype.add_enumerator("overflow", COORD, None, LINUX)
    assert len(dtype.enumerators) == 1


@pytest.mark.parametrize("underlying,source,value", (
    ("bool", "int", 2), ("unsigned char", "int", -1),
    ("unsigned char", "int", 256), ("signed char", "int", 128),
    ("int", "unsigned int", UINT_MAX),
))
def test_fixed_enumerator_requires_representability_not_modular_conversion(underlying, source, value):
    dtype = make_enum(underlying=underlying)
    dtype.begin_definition()
    with pytest.raises(CppDiagnostic, match="cpp.enum-value-range"):
        dtype.add_enumerator("bad", COORD, known(source, value), LINUX)


def test_fixed_successor_must_fit_even_if_a_larger_type_exists():
    dtype = make_enum(underlying="unsigned char")
    dtype.begin_definition()
    dtype.add_enumerator("last", COORD, known("int", 255), LINUX)
    with pytest.raises(CppDiagnostic, match="cpp.enum-value-range"):
        dtype.add_enumerator("next", COORD, None, LINUX)


def test_unscoped_enum_initializer_uses_underlying_not_promotion_type():
    source = define_enum((("int", 7),), underlying="unsigned char")
    destination = make_enum()
    destination.begin_definition()
    value = destination.add_enumerator("value", COORD, source.enumerators[0].constant_fact, LINUX)
    assert source.promotion_type(LINUX) == "int"
    assert value.dtype == "unsigned char" and value.enum_value == 7


@pytest.mark.parametrize("fact,code", (
    (not_constant("int", "division by zero"), "cpp.enum-constant-expression"),
    (unmodeled("int", "unsupported constant operator"), "cpp.unsupported.enum-constant-expression"),
    (unmodeled("double", "floating facts unimplemented"), "cpp.enum-initializer-type"),
    (unmodeled(PointerType("int"), "pointer facts unimplemented"), "cpp.enum-initializer-type"),
    (known("unsigned int", -1), "cpp.unsupported.enum-constant-expression"),
))
def test_constant_proof_failure_is_not_confused_with_invalid_language_type(fact, code):
    dtype = make_enum()
    dtype.begin_definition()
    with pytest.raises(CppDiagnostic) as error:
        dtype.add_enumerator("value", COORD, fact, LINUX)
    assert error.value.code == code and error.value.coord is COORD


def test_scoped_enum_cannot_implicitly_initialize_an_enumerator():
    source = define_enum((("int", 7),), scoped=True)
    destination = make_enum()
    destination.begin_definition()
    with pytest.raises(CppDiagnostic, match="cpp.enum-initializer-type"):
        destination.add_enumerator("value", COORD, source.enumerators[0].constant_fact, LINUX)


@pytest.mark.parametrize("value", (0, 1))
def test_fixed_bool_converted_constant_requires_allowed_conversion_not_just_range(value):
    # CWG1407 is NAD: an integer-to-bool conversion is not permitted in a
    # converted constant expression, even if common implementations accept it.
    # https://open-std.org/JTC1/SC22/WG21/docs/cwg_closed.html#1407
    dtype = make_enum(underlying="bool")
    dtype.begin_definition()
    with pytest.raises(CppDiagnostic, match="cpp.enum-converted-constant"):
        dtype.add_enumerator("bad", COORD, known("int", value), LINUX)
    good = dtype.add_enumerator("good", COORD, known("bool", value), LINUX)
    assert good.dtype == "bool" and good.enum_value == value


def test_duplicate_enumerators_and_redefinitions_are_not_alias_redeclarations():
    dtype = make_enum()
    dtype.begin_definition()
    dtype.add_enumerator("value", COORD, None, LINUX)
    with pytest.raises(CppDiagnostic, match="cpp.redeclaration"):
        dtype.add_enumerator("value", COORD, None, LINUX)
    dtype.finish(LINUX)
    with pytest.raises(CppDiagnostic, match="cpp.enum-redefinition"):
        dtype.begin_definition()


def test_unscoped_enumerator_tag_hiding_is_an_explicit_lookup_boundary():
    dtype = make_enum()
    dtype.begin_definition()
    with pytest.raises(CppDiagnostic, match="cpp.unsupported.enum-tag-hiding"):
        dtype.add_enumerator("E", COORD, None, LINUX)
    assert dtype.declaration_scope.names["E"].dtype is dtype


def test_parser_consumes_real_grammar_and_body_expression_facts():
    scope = parse_declarations("enum Flags { A = 1, B = 2, C = A | B, D, }; enum class Mask : unsigned char { Zero, All = C }; ")
    flags, mask = scope.names["Flags"].dtype, scope.names["Mask"].dtype
    assert [(item.name, item.enum_value) for item in flags.enumerators] == [("A", 1), ("B", 2), ("C", 3), ("D", 4)]
    assert [(item.name, item.enum_value) for item in mask.enumerators] == [("Zero", 0), ("All", 3)]
    assert scope.lookup("C") is flags.scope.names["C"]
    assert "Zero" not in scope.names and "All" not in scope.names


def test_parser_keeps_fixed_forward_identity_and_allows_opaque_redeclarations():
    parser = parser_for("enum E : unsigned short; enum E : unsigned short { A = 7 }; enum E : unsigned short;")
    parse_enum(parser)
    dtype = parser.scope.names["E"].dtype
    assert dtype.complete and not dtype.defined
    parse_enum(parser)
    assert parser.scope.names["E"].dtype is dtype and dtype.defined
    parse_enum(parser)
    assert parser.scope.names["E"].dtype is dtype


def test_parser_explicit_unsigned_expression_wrap_is_not_implicit_successor_wrap():
    scope = parse_declarations("enum E { Wrapped = 4294967295u + 1u, Next };")
    dtype = scope.names["E"].dtype
    assert [item.enum_value for item in dtype.enumerators] == [0, 1]


@pytest.mark.parametrize("source,code", (
    ("enum E : float {};", "cpp.enum-underlying-type"),
    ("enum E : long double {};", "cpp.enum-underlying-type"),
    ("enum E : void {};", "cpp.enum-underlying-type"),
    ("enum E { A = 1.5 };", "cpp.enum-initializer-type"),
    ("enum E { A = 1 / 0 };", "cpp.enum-constant-expression"),
    ("enum E { A = -1 >> 1 };", "cpp.unsupported.enum-constant-expression"),
    ("enum E { A, A };", "cpp.redeclaration"),
    ("enum E {}; enum E {};", "cpp.enum-redefinition"),
    ("enum E : int; enum class E {};", "cpp.enum-redeclaration"),
    ("enum E : int; enum E : unsigned int {};", "cpp.enum-underlying-mismatch"),
    ("enum E : int; enum E {};", "cpp.enum-redeclaration"),
    ("enum E;", "cpp.unsupported.enum-elaborated-declaration"),
    ("enum { A };", "cpp.unsupported.anonymous-enums"),
    ("enum E { A } object;", "cpp.unsupported.enum-declarators"),
    ("enum n::E {};", "cpp.unsupported.enum-qualified-definition"),
    ("enum E { E };", "cpp.unsupported.enum-tag-hiding"),
))
def test_parser_diagnostics_do_not_silently_flatten_unsupported_shapes(source, code):
    parser = parser_for(source)
    parent = parser.scope
    with pytest.raises(CppDiagnostic) as error:
        while parser._peek().kind != "EOF":
            parse_enum(parser)
    assert error.value.code == code
    assert parser.scope is parent


@pytest.mark.parametrize("kind", ("block", "class"))
def test_local_and_unbound_class_scopes_do_not_invent_enum_identity(kind):
    # A bare class-kind Scope has no ClassDefinition/nominal owner. It must not
    # project the enum into its parent namespace just because the kind matches.
    # Actual bound class scopes are exercised by test_cpp_class_enums.
    scope = Scope(Scope(), kind=kind, name="Owner")
    parser = parser_for("enum E { A };", scope=scope)
    with pytest.raises(CppDiagnostic, match="cpp.unsupported.enum-scope"):
        parse_enum(parser)


def test_dependent_enum_declarations_are_not_instantiated_as_namespace_types():
    parser = parser_for("enum E { A };")
    parser.parsing_template = True
    with pytest.raises(CppDiagnostic, match="cpp.unsupported.enum-templates"):
        parse_enum(parser)


def test_imported_fixed_enum_cannot_be_defined_in_importing_namespace():
    original = make_enum(underlying="int")
    imported = Scope()
    imported.names["E"] = original.declaration_scope.names["E"]
    parser = parser_for("enum E : int { A };", scope=imported)
    with pytest.raises(CppDiagnostic, match="cpp.unsupported.enum-redeclaration"):
        parse_enum(parser)
    assert not original.defined
