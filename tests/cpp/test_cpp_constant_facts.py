"""Original typed integral fact rules, not a complete constexpr/native gate.

Primary rules: WG21 N4659 [expr.const], [expr.mul], [expr.shift],
[conv.integral], [conv.prom], [expr.log.and], [expr.log.or].
https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2017/n4659.pdf
These tests use the real target ArithmeticTypes. No GCC/Clang test source is
copied; implementation-defined signed policies remain explicit capability
gaps instead of borrowing the host Python representation or rounding rules.
"""

import pytest

from pcc.frontends.c.parse.plyparser import Coord
from pcc.frontends.cpp.constants import (
    KNOWN,
    NOT_CONSTANT,
    UNMODELED,
    ConstantFact,
    binary_fact,
    converted_fact,
    known,
    not_constant,
    unary_fact,
    unmodeled,
)
from pcc.frontends.cpp.types import ArithmeticTypes, ConstType, PointerType


LINUX = ArithmeticTypes("x86_64-unknown-linux-gnu")
WINDOWS = ArithmeticTypes("x86_64-pc-windows-gnu")
ARM = ArithmeticTypes("aarch64-unknown-linux-gnu")
COORD = Coord("constant-facts.cpp", 7, 9)
OP_COORD = Coord("constant-facts.cpp", 8, 3)
INT_MIN = -(1 << 31)
INT_MAX = (1 << 31) - 1
UINT_MAX = (1 << 32) - 1


def assert_known(fact, dtype, value):
    assert fact.status == KNOWN
    assert fact.dtype == dtype
    assert fact.value == value
    assert fact.reason == ""


def binary(operator, left, right, dtype="int", rhs_type=None, result_type=None, types=LINUX):
    return binary_fact(
        operator, known(dtype, left), known(rhs_type or dtype, right),
        dtype, rhs_type or dtype, result_type or dtype, types, OP_COORD,
    )


def test_fact_states_are_distinct_and_have_typed_values_only_when_known():
    first = known(ConstType("int"), 42, COORD)
    assert_known(first, "int", 42)
    assert first.coord is COORD
    assert_known(known("bool", True), "bool", 1)
    for factory, status in ((not_constant, NOT_CONSTANT), (unmodeled, UNMODELED)):
        fact = factory("int", "reason", COORD)
        assert fact.status == status
        assert fact.value is None
        assert fact.reason == "reason"
        assert fact.coord is COORD
    assert len({KNOWN, NOT_CONSTANT, UNMODELED}) == 3


@pytest.mark.parametrize("value", (1.0, "42", None))
def test_known_never_coerces_host_float_or_source_spelling_to_an_integer(value):
    with pytest.raises(TypeError, match="integer value"):
        known("int", value)


@pytest.mark.parametrize("source,value,destination,expected", (
    ("int", 42, "unsigned char", 42),
    ("int", -1, "unsigned int", UINT_MAX),
    ("int", -1, "unsigned long", (1 << 64) - 1),
    ("int", -256, "unsigned char", 0),
    ("unsigned int", UINT_MAX, "long", UINT_MAX),
    ("int", -42, "long", -42),
    ("int", 0, "bool", 0),
    ("int", -42, "bool", 1),
    ("bool", 1, "int", 1),
    ("long", 255, "unsigned char", 255),
))
def test_selected_integral_conversions_use_target_values(source, value, destination, expected):
    fact = converted_fact(known(source, value), destination, LINUX, COORD)
    assert_known(fact, destination, expected)
    assert fact.coord is COORD


@pytest.mark.parametrize("source,value,destination", (
    ("int", 128, "signed char"),
    ("unsigned int", UINT_MAX, "int"),
    ("long", 1 << 31, "int"),
))
def test_signed_out_of_range_conversion_is_unknown_not_signed_arithmetic_ub(source, value, destination):
    fact = converted_fact(known(source, value), destination, LINUX)
    assert fact.status == UNMODELED
    assert "target policy" in fact.reason


def test_target_long_and_plain_char_ranges_are_not_host_guesses():
    value = known("unsigned int", UINT_MAX)
    assert_known(converted_fact(value, "long", LINUX), "long", UINT_MAX)
    assert converted_fact(value, "long", WINDOWS).status == UNMODELED
    character = known("unsigned char", 255)
    assert_known(converted_fact(character, "char", ARM), "char", 255)
    assert converted_fact(character, "char", LINUX).status == UNMODELED
    # A source fact valid under LP64 cannot be reused as if valid under LLP64.
    assert_known(converted_fact(known("long", 1 << 31), "long long", LINUX),
                 "long long", 1 << 31)
    assert converted_fact(known("long", 1 << 31), "long long", WINDOWS).status == UNMODELED


@pytest.mark.parametrize("fact", (
    known("bool", 2), known("unsigned int", -1), known("int", 1 << 31),
    ConstantFact(KNOWN, "int", value=1.0), ConstantFact("INVALID", "int"),
))
def test_invalid_input_facts_cannot_become_constant_proofs(fact):
    assert converted_fact(fact, "int", LINUX).status == UNMODELED


@pytest.mark.parametrize("factory,status", ((not_constant, NOT_CONSTANT), (unmodeled, UNMODELED)))
def test_conversion_preserves_failure_kind_and_original_reason_location(factory, status):
    fact = converted_fact(factory("int", "original reason", COORD), "long", LINUX, OP_COORD)
    assert fact.status == status
    assert fact.dtype == "long"
    assert fact.value is None
    assert fact.reason == "original reason"
    assert fact.coord is COORD


@pytest.mark.parametrize("source,destination", (
    ("double", "int"), ("int", "float"),
    (PointerType("int"), "bool"), ("nullptr", PointerType("int")),
))
def test_floating_and_pointer_facts_remain_unmodeled(source, destination):
    fact = converted_fact(unmodeled(source, "not an integral fact"), destination, LINUX)
    assert fact.status == UNMODELED
    assert fact.dtype == destination
    assert fact.value is None


@pytest.mark.parametrize("operator,source,value,promoted,result,expected", (
    ("+", "short", -7, "int", "int", -7),
    ("-", "int", 7, "int", "int", -7),
    ("-", "unsigned int", 1, "unsigned int", "unsigned int", UINT_MAX),
    ("~", "unsigned int", 0, "unsigned int", "unsigned int", UINT_MAX),
    ("~", "unsigned int", UINT_MAX, "unsigned int", "unsigned int", 0),
    ("!", "int", -7, "bool", "bool", 0),
    ("!", "int", 0, "bool", "bool", 1),
    ("+", "bool", 1, "int", "int", 1),
))
def test_unary_integral_facts_use_selected_promotion(operator, source, value, promoted, result, expected):
    fact = unary_fact(operator, known(source, value), promoted, result, LINUX, COORD)
    assert_known(fact, result, expected)


def test_unary_signed_min_overflow_is_an_inert_nonconstant_fact():
    fact = unary_fact("-", known("int", INT_MIN), "int", "int", LINUX)
    assert fact.status == NOT_CONSTANT
    assert "overflow" in fact.reason


@pytest.mark.parametrize("value", (0, 1, -1, INT_MIN))
def test_signed_complement_does_not_assume_twos_complement_host(value):
    assert unary_fact("~", known("int", value), "int", "int", LINUX).status == UNMODELED


@pytest.mark.parametrize("operator,left,right,expected", (
    ("+", 17, 25, 42), ("-", 17, 25, -8), ("*", -6, 7, -42),
    ("/", -7, 3, -2), ("%", -7, 3, -1),
    ("/", 7, -3, -2), ("%", 7, -3, 1),
    ("/", -7, -3, 2), ("%", -7, -3, -1),
    ("&", 6, 3, 2), ("|", 6, 3, 7), ("^", 6, 3, 5),
))
def test_pure_integral_operations_match_cpp_values(operator, left, right, expected):
    assert_known(binary(operator, left, right), "int", expected)


@pytest.mark.parametrize("operator,left,right,expected", (
    ("+", UINT_MAX, 1, 0), ("-", 0, 1, UINT_MAX),
    ("*", UINT_MAX, 2, UINT_MAX - 1),
))
def test_unsigned_arithmetic_wrap_is_still_a_constant_expression(operator, left, right, expected):
    assert_known(binary(operator, left, right, "unsigned int"), "unsigned int", expected)


@pytest.mark.parametrize("operator,left,right", (
    ("+", INT_MAX, 1), ("-", INT_MIN, 1), ("*", INT_MAX, 2),
    ("/", INT_MIN, -1), ("%", INT_MIN, -1), ("/", 1, 0), ("%", 1, 0),
))
def test_evaluated_undefined_arithmetic_returns_a_fact_not_a_source_exception(operator, left, right):
    fact = binary(operator, left, right)
    assert fact.status == NOT_CONSTANT
    assert fact.value is None
    assert fact.coord is OP_COORD


@pytest.mark.parametrize("operator,left,right,expected", (
    ("==", -1, -1, 1), ("!=", -1, -1, 0),
    ("<", -1, 0, 1), ("<=", 1, 1, 1), (">", 1, 2, 0), (">=", 2, 1, 1),
))
def test_comparison_fact_has_cpp_bool_identity(operator, left, right, expected):
    assert_known(binary(operator, left, right, result_type="bool"), "bool", expected)


def test_selected_promotions_and_usual_conversions_are_not_recomputed():
    promoted = binary_fact("+", known("short", 32767), known("short", 1),
                           "int", "int", "int", LINUX)
    assert_known(promoted, "int", 32768)
    wrapped = binary_fact("+", known("int", -1), known("unsigned int", 1),
                          "unsigned int", "unsigned int", "unsigned int", LINUX)
    assert_known(wrapped, "unsigned int", 0)
    compared = binary_fact("<", known("int", -1), known("unsigned int", 1),
                           "unsigned int", "unsigned int", "bool", LINUX)
    assert_known(compared, "bool", 0)


def test_long_arithmetic_distinguishes_lp64_from_llp64():
    assert_known(binary("+", INT_MAX, 1, "long", types=LINUX), "long", 1 << 31)
    assert binary("+", INT_MAX, 1, "long", types=WINDOWS).status == NOT_CONSTANT


@pytest.mark.parametrize("operator,left,count,dtype,expected", (
    ("<<", 1, 30, "int", 1 << 30),
    ("<<", 1, 31, "unsigned int", 1 << 31),
    ("<<", UINT_MAX, 1, "unsigned int", UINT_MAX - 1),
    (">>", 17, 2, "int", 4), (">>", UINT_MAX, 31, "unsigned int", 1),
))
def test_defined_shift_values(operator, left, count, dtype, expected):
    assert_known(binary(operator, left, count, dtype, rhs_type="int"), dtype, expected)


@pytest.mark.parametrize("operator,left,count", (
    ("<<", 1, -1), ("<<", 1, 32), (">>", 1, -1), (">>", 1, 32),
    ("<<", -1, 0), ("<<", 2, 31),
))
def test_undefined_shifts_are_nonconstant_without_performing_unbounded_host_shift(operator, left, count):
    assert binary(operator, left, count).status == NOT_CONSTANT
    huge = binary("<<", 1, 1 << 60, rhs_type="long long")
    assert huge.status == NOT_CONSTANT


def test_signed_shift_to_sign_bit_is_not_misclassified_as_overflow():
    fact = binary("<<", 1, 31)
    assert fact.status == UNMODELED
    assert "conversion" in fact.reason
    promoted = binary_fact("<<", known("short", 1), known("int", 15),
                           "int", "int", "int", LINUX)
    assert_known(promoted, "int", 32768)


@pytest.mark.parametrize("operator", (">>", "&", "|", "^"))
def test_negative_signed_representation_dependent_operations_stay_unmodeled(operator):
    assert binary(operator, -1, 1).status == UNMODELED


def test_unsigned_bitwise_after_selected_conversion_does_not_inherit_signed_unknown():
    fact = binary_fact("&", known("int", -1), known("unsigned int", 1),
                       "unsigned int", "unsigned int", "unsigned int", LINUX)
    assert_known(fact, "unsigned int", 1)


@pytest.mark.parametrize("operator,left,result", (("&&", 0, 0), ("||", 1, 1)))
@pytest.mark.parametrize("rhs", (
    not_constant("int", "runtime read"), unmodeled("int", "missing proof"),
    binary("/", 1, 0), unary_fact("-", known("int", INT_MIN), "int", "int", LINUX),
    unmodeled("double", "floating arithmetic"), None,
))
def test_short_circuit_masks_unexecuted_nonconstant_unknown_and_ub_facts(operator, left, result, rhs):
    fact = binary_fact(operator, known("bool", left), rhs, "bool", "bool", "bool", LINUX)
    assert_known(fact, "bool", result)


@pytest.mark.parametrize("operator,left", (("&&", 1), ("||", 0)))
@pytest.mark.parametrize("factory,status", ((not_constant, NOT_CONSTANT), (unmodeled, UNMODELED)))
def test_logical_evaluated_rhs_preserves_its_failure_state(operator, left, factory, status):
    rhs = factory("int", "rhs cause", COORD)
    fact = binary_fact(operator, known("bool", left), rhs, "bool", "bool", "bool", LINUX, OP_COORD)
    assert fact.status == status
    assert fact.reason == "rhs cause"
    assert fact.coord is COORD


@pytest.mark.parametrize("operator", ("&&", "||"))
@pytest.mark.parametrize("rhs", (known("bool", 0), known("bool", 1),
                                not_constant("bool", "rhs runtime"),
                                unmodeled("bool", "rhs unknown")))
def test_unknown_logical_lhs_cannot_be_downgraded_by_possibly_unevaluated_rhs(operator, rhs):
    fact = binary_fact(operator, unmodeled("bool", "lhs unknown"), rhs,
                       "bool", "bool", "bool", LINUX)
    assert fact.status == UNMODELED
    assert fact.reason == "lhs unknown"


@pytest.mark.parametrize("operator", ("&&", "||"))
def test_nonconstant_logical_lhs_is_always_evaluated_even_if_result_can_be_folded(operator):
    fact = binary_fact(operator, not_constant("bool", "lhs runtime"), known("bool", operator == "||"),
                       "bool", "bool", "bool", LINUX)
    assert fact.status == NOT_CONSTANT
    assert fact.reason == "lhs runtime"


@pytest.mark.parametrize("left,right", (
    (not_constant("int", "runtime"), unmodeled("int", "unknown")),
    (unmodeled("int", "unknown"), not_constant("int", "runtime")),
))
def test_both_evaluated_operands_allow_proven_nonconstant_to_dominate_unknown(left, right):
    fact = binary_fact("+", left, right, "int", "int", "int", LINUX)
    assert fact.status == NOT_CONSTANT
    assert fact.reason == "runtime"


def test_missing_and_unsupported_operations_never_create_a_constant_proof():
    assert converted_fact(None, "int", LINUX).status == UNMODELED
    assert unary_fact("&", known("int", 42), "int", PointerType("int"), LINUX).status == UNMODELED
    assert binary_fact(",", known("int", 1), known("int", 2), "int", "int", "int", LINUX).status == UNMODELED


def test_optional_operation_coordinate_retains_known_operand_origin():
    fact = known("int", 42, COORD)
    assert converted_fact(fact, "long", LINUX).coord is COORD
    assert unary_fact("+", fact, "int", "int", LINUX).coord is COORD
    assert binary_fact("+", fact, known("int", 0), "int", "int", "int", LINUX).coord is COORD
