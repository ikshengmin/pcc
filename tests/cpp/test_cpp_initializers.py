"""Original initializer-model rules; not native pcc1 or full-C++17 qualification.

These tests use the shared ArithmeticTypes, Expression, Scope and class model,
plus the real token cursor/parser for list grammar. They do not execute emitted
programs or certify backend evaluation order; public integration covers that.
N4659 [dcl.init.aggr], [dcl.init.list], [expr.const]:
https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2017/n4659.pdf
P1957R2 (pointer-to-bool narrowing, C++17 defect correction):
https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2020/p1957r2.html
No upstream compiler test or implementation is copied.
"""

import pytest

from pcc.frontends.cpp.initializers import (
    ListInitializer,
    check_list_conversion,
    lower_aggregate_initializer,
    parse_initializer_list,
)
from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.parse.plyparser import Coord
from pcc.frontends.cpp.classes import ClassDefinition, ClassType
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.parser import CppParser, Expression, TokenCursor
from pcc.frontends.cpp.sema import Scope, Symbol
from pcc.frontends.cpp.types import ArithmeticTypes, ConstType, PointerType


COORD = Coord("aggregate-candidate.cpp", 1, 1)
TYPES = ArithmeticTypes("x86_64-unknown-linux-gnu")


def define(name, members, key="struct"):
    dtype = ClassType(name, (name,), COORD)
    definition = ClassDefinition(dtype, Scope(), key)
    for member_name, member_type in members:
        definition.add_member(Symbol(member_name, "member", COORD, dtype=member_type))
    definition.finish()
    return dtype


def expression(dtype="int", value="42", null=False):
    kind = "float" if dtype in ("float", "double", "long double") else "int"
    return Expression(c_ast.Constant(kind, value, COORD), dtype, null_pointer=null)


def parser_for_list(source):
    parser = CppParser("x86_64-unknown-linux-gnu")
    parser.scope = Scope()
    parser.template_function = None
    parser.parsing_template = False
    parser.cursor = TokenCursor(parser._tokenize(source, "initializers.cpp"))
    return parser


def test_parser_retains_nested_lists_empty_lists_trailing_comma_and_leaf_types():
    parser = parser_for_list("{1,{2,},{},}")
    init = parse_initializer_list(parser)
    assert len(init.clauses) == 3
    assert init.clauses[0].dtype == "int"
    assert init.clauses[0].node.value == "1"
    assert init.clauses[1].clauses[0].node.value == "2"
    assert init.clauses[2].clauses == ()
    assert parser._peek().kind == "EOF"


@pytest.mark.parametrize("source", ("{1 2}", "{,}", "{1,", "{{1}"))
def test_malformed_lists_raise_diagnostics_instead_of_replaying_tokens(source):
    with pytest.raises(CppDiagnostic):
        parse_initializer_list(parser_for_list(source))


def test_list_parser_does_not_enter_unimplemented_template_lifetime_paths():
    parser = parser_for_list("{}")
    parser.template_function = object()
    with pytest.raises(CppDiagnostic, match="cpp.unsupported.class-templates"):
        parse_initializer_list(parser)
    assert parser._peek().value == "{"


def test_lowering_reuses_conversion_callback_in_source_field_order_once():
    dtype = define("S", (("a", "int"), ("b", "long"), ("pointer", PointerType("int"))))
    first = Expression(c_ast.FuncCall(c_ast.ID("first", COORD), None, COORD), "int")
    second = Expression(c_ast.FuncCall(c_ast.ID("second", COORD), None, COORD), "int")
    calls = []

    def convert(expr, destination, coord):
        calls.append((expr, destination))
        return expr.node

    lowered = lower_aggregate_initializer(dtype, ListInitializer([first, second], COORD), TYPES, convert)
    assert calls == [(first, "int"), (second, "long")]
    assert lowered.exprs[0] is first.node
    assert lowered.exprs[1] is second.node
    assert lowered.exprs[2].value == "0"


def test_explicit_nested_list_preserves_const_aggregate_omissions():
    inner = define("I", (("value", ConstType("int")),))
    outer = define("O", (("inner", inner), ("tail", "int")))
    leaf = expression()
    calls = []

    def convert(expr, destination, coord):
        calls.append(expr)
        return expr.node

    lowered = lower_aggregate_initializer(
        outer, ListInitializer([ListInitializer([], COORD), leaf], COORD), TYPES, convert,
    )
    assert lowered.exprs[0].exprs[0].value == "0"
    assert lowered.exprs[1] is leaf.node
    assert calls == [leaf]


def test_real_parser_conversion_owner_receives_scalar_braces_and_const_destination():
    dtype = define("S", (("value", ConstType("long")), ("pointer", PointerType("int"))))
    parser = parser_for_list("{{42},{nullptr}}")
    initializer = parse_initializer_list(parser)
    lowered = lower_aggregate_initializer(dtype, initializer, parser.types, parser._convert)
    assert isinstance(lowered.exprs[0], c_ast.Cast)
    assert lowered.exprs[0].to_type.type.type.names == ["long"]
    assert lowered.exprs[0].expr is initializer.clauses[0].clauses[0].node
    assert isinstance(lowered.exprs[1], c_ast.Cast)
    assert isinstance(lowered.exprs[1].to_type.type, c_ast.PtrDecl)
    assert lowered.exprs[1].expr is initializer.clauses[1].clauses[0].node


def test_nested_conversion_callbacks_follow_leaf_order_without_duplication():
    inner = define("I", (("a", "int"), ("b", "int")))
    outer = define("O", (("inner", inner), ("last", "int")))
    leaves = [Expression(c_ast.FuncCall(c_ast.ID(name, COORD), None, COORD), "int")
              for name in ("first", "second", "third")]
    initializer = ListInitializer([
        ListInitializer(leaves[:2], COORD), leaves[2],
    ], COORD)
    calls = []

    def convert(expr, destination, coord):
        calls.append(expr)
        return expr.node

    lowered = lower_aggregate_initializer(outer, initializer, TYPES, convert)
    assert calls == leaves
    assert lowered.exprs[0].exprs == [leaf.node for leaf in leaves[:2]]
    assert lowered.exprs[1] is leaves[2].node


@pytest.mark.parametrize("source,destination", (
    ("bool", "int"), ("short", "int"), ("int", "long"),
    ("unsigned int", "long"), ("float", "double"),
    (PointerType("int"), PointerType(ConstType("int"))),
    (PointerType("int"), PointerType("void")),
))
def test_value_independent_non_narrowing_conversions_pass(source, destination):
    check_list_conversion(expression(source), destination, TYPES, COORD)


@pytest.mark.parametrize("source,destination", (
    ("int", "unsigned char"), ("long", "int"), ("unsigned int", "int"),
    ("int", "bool"), ("int", "float"), ("short", "double"), ("double", "float"),
))
def test_value_dependent_cases_are_capability_gaps_not_false_illformed_diagnostics(source, destination):
    with pytest.raises(CppDiagnostic) as error:
        check_list_conversion(expression(source), destination, TYPES, COORD)
    assert error.value.code == "cpp.unsupported.narrowing-analysis"


@pytest.mark.parametrize("source,destination", (
    ("double", "int"), ("float", "bool"), (PointerType("int"), "bool"),
))
def test_unconditional_narrowing_is_rejected(source, destination):
    with pytest.raises(CppDiagnostic) as error:
        check_list_conversion(expression(source), destination, TYPES, COORD)
    assert error.value.code == "cpp.narrowing-conversion"


def test_range_checks_follow_target_long_width_and_plain_char_signedness():
    windows = ArithmeticTypes("x86_64-pc-windows-gnu")
    arm = ArithmeticTypes("aarch64-unknown-linux-gnu")
    check_list_conversion(expression("unsigned int"), "long", TYPES, COORD)
    with pytest.raises(CppDiagnostic, match="cpp.unsupported.narrowing-analysis"):
        check_list_conversion(expression("unsigned int"), "long", windows, COORD)
    check_list_conversion(expression("unsigned char"), "char", arm, COORD)
    with pytest.raises(CppDiagnostic, match="cpp.unsupported.narrowing-analysis"):
        check_list_conversion(expression("unsigned char"), "char", TYPES, COORD)


def test_invalid_pointer_conversion_is_not_reclassified_as_narrowing():
    with pytest.raises(CppDiagnostic) as error:
        check_list_conversion(expression(PointerType("long")), PointerType("int"), TYPES, COORD)
    assert error.value.code == "cpp.invalid-conversion"


def test_null_pointer_clause_keeps_shared_conversion_rules():
    destination = PointerType("int")
    check_list_conversion(expression("int", "0", null=True), destination, TYPES, COORD)
    check_list_conversion(expression("nullptr", "0", null=True), destination, TYPES, COORD)
    with pytest.raises(CppDiagnostic, match="cpp.invalid-conversion"):
        check_list_conversion(expression("nullptr", "0", null=True), "bool", TYPES, COORD)


def test_forward_class_pointer_needs_no_pointee_completion_but_keeps_nominal_identity():
    first = ClassType("A", ("A",), COORD)
    other = ClassType("A", ("other", "A"), COORD)
    check_list_conversion(expression(PointerType(first)), PointerType(ConstType(first)), TYPES, COORD)
    with pytest.raises(CppDiagnostic, match="cpp.invalid-conversion"):
        check_list_conversion(expression(PointerType(first)), PointerType(other), TYPES, COORD)


def test_valid_brace_elision_does_not_receive_a_false_excess_elements_diagnostic():
    inner = define("I", (("a", "int"), ("b", "int")))
    outer = define("O", (("i", inner), ("c", "int")))
    with pytest.raises(CppDiagnostic) as error:
        lower_aggregate_initializer(
            outer, ListInitializer([expression(), expression(), expression()], COORD), TYPES,
            lambda expr, destination, coord: expr.node,
        )
    assert error.value.code == "cpp.unsupported.aggregate-brace-elision"


def test_empty_aggregate_single_class_leaf_never_bypasses_copy_semantics():
    empty = define("E", ())
    leaf = Expression(c_ast.ID("other", COORD), empty, lvalue=True)
    with pytest.raises(CppDiagnostic) as error:
        lower_aggregate_initializer(empty, ListInitializer([leaf], COORD), TYPES,
                                    lambda expr, destination, coord: expr.node)
    assert error.value.code == "cpp.unsupported.class-initialization"


def test_nonaggregate_nonempty_list_does_not_initialize_private_storage_directly():
    private = define("P", (("value", "int"),), key="class")
    with pytest.raises(CppDiagnostic) as error:
        lower_aggregate_initializer(private, ListInitializer([expression()], COORD), TYPES,
                                    lambda expr, destination, coord: expr.node)
    assert error.value.code == "cpp.unsupported.class-constructors"


def test_omitted_nonaggregate_member_keeps_deleted_default_constructor_constraint():
    private = define("P", (("value", ConstType("int")),), key="class")
    outer = define("O", (("first", "int"), ("private", private)))
    with pytest.raises(CppDiagnostic, match="cpp.deleted-default-constructor"):
        lower_aggregate_initializer(outer, ListInitializer([expression()], COORD), TYPES,
                                    lambda expr, destination, coord: expr.node)


def test_true_scalar_excess_elements_is_diagnosed():
    dtype = define("S", (("value", "int"),))
    initializer = ListInitializer([ListInitializer([expression(), expression()], COORD)], COORD)
    with pytest.raises(CppDiagnostic) as error:
        lower_aggregate_initializer(dtype, initializer, TYPES, lambda expr, destination, coord: expr.node)
    assert error.value.code == "cpp.too-many-initializers"


@pytest.mark.parametrize("member_type,innermost", (
    ("int", [expression()]), ("int", []), (PointerType("int"), []),
))
def test_scalar_nested_list_is_not_recursively_unwrapped(member_type, innermost):
    dtype = define("S", (("value", member_type),))
    scalar_list = ListInitializer([ListInitializer(innermost, COORD)], COORD)
    initializer = ListInitializer([scalar_list], COORD)
    with pytest.raises(CppDiagnostic) as error:
        lower_aggregate_initializer(dtype, initializer, TYPES, lambda expr, destination, coord: expr.node)
    assert error.value.code == "cpp.invalid-scalar-initializer"
