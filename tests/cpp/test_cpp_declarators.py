"""Syntax-only declarator checks; these do not prove pointer/ref execution.

Only the separate grouped-name tests exercise the public frontend and emitted
programs. These tests deliberately keep unresolved types out of the C AST.
"""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.declarators import (
    DeclaratorParser,
    parse_cv_qualifiers,
    parse_plain_name,
    parse_qualified_name,
    require_plain_name,
)
from pcc.frontends.cpp.parser import TokenCursor
from pcc.frontends.cpp.sema import Scope, Symbol


def reader(source):
    parser = CppParser()
    parser.cursor = TokenCursor(parser._tokenize(source, "declarator.cpp"))
    return parser


def parse(source):
    parser = reader(source)
    result = DeclaratorParser(parser.cursor, parser._identifier).parse()
    assert parser.cursor.peek().kind == "EOF"
    return result


@pytest.mark.parametrize("source", ("value", "(value)", "(((value)))"))
def test_redundant_grouping_preserves_identifier_and_coord(source):
    declaration = parse(source)
    token = require_plain_name(declaration)
    assert declaration.chunks == ()
    assert token.value == "value"
    assert token.coord.file == "declarator.cpp"
    assert token.coord.line == 1
    assert token.coord.column == source.index("value") + 1


@pytest.mark.parametrize(
    "source,kinds",
    (
        ("*value", ("pointer",)),
        ("&value", ("lvalue-reference",)),
        ("&&value", ("rvalue-reference",)),
        ("*&value", ("lvalue-reference", "pointer")),
        ("*&&value", ("rvalue-reference", "pointer")),
        ("**value", ("pointer", "pointer")),
        ("*function()", ("function", "pointer")),
        ("(*function)()", ("pointer", "function")),
        ("*array[]", ("array", "pointer")),
        ("(*array)[]", ("pointer", "array")),
        ("(&function)()", ("lvalue-reference", "function")),
        ("(&&array)[]", ("rvalue-reference", "array")),
        ("array[][]", ("array", "array")),
    ),
)
def test_chunks_are_ordered_from_name_outwards(source, kinds):
    declaration = parse(source)
    assert tuple(chunk.kind for chunk in declaration.chunks) == kinds


def test_pointer_cv_is_per_level_not_pointee_cv():
    declaration = parse("* const * volatile value")
    assert declaration.chunks[0].qualifiers == ("volatile",)
    assert declaration.chunks[1].qualifiers == ("const",)
    assert [chunk.coord.column for chunk in declaration.chunks] == [9, 1]


def test_cv_order_is_canonical_but_stays_separate_from_the_base_type():
    parser = reader("volatile const int")
    assert parse_cv_qualifiers(parser.cursor) == ("const", "volatile")
    assert parser.cursor.peek().value == "int"
    assert parse("* volatile const value").chunks[0].qualifiers == ("const", "volatile")


@pytest.mark.parametrize("source", ("*const const value", "*volatile volatile value"))
def test_duplicate_cv_is_diagnosed_at_the_second_qualifier(source):
    with pytest.raises(CppDiagnostic) as error:
        parse(source)
    assert error.value.code == "cpp.duplicate-cv-qualifier"
    assert error.value.coord.column == source.rindex(source.split()[1]) + 1


@pytest.mark.parametrize("source", ("&const value", "&&volatile value"))
def test_direct_reference_cv_is_not_silently_ignored(source):
    with pytest.raises(CppDiagnostic) as error:
        parse(source)
    assert error.value.code == "cpp.invalid-declarator"
    assert error.value.coord.file == "declarator.cpp"


@pytest.mark.parametrize(
    "source,parts,global_scope",
    (
        ("value", ("value",), False),
        ("Outer::value", ("Outer", "value"), False),
        ("::Outer::Inner::value", ("Outer", "Inner", "value"), True),
        ("Tag::type", ("Tag", "type"), False),
    ),
)
def test_qualified_names_retain_components_without_performing_lookup(source, parts, global_scope):
    parser = reader(source)
    name = parse_qualified_name(parser.cursor, parser._identifier)
    assert name.parts == parts
    assert name.global_scope is global_scope
    assert name.identifier.value == parts[-1]
    assert name.coord.column == 1
    assert parser.cursor.peek().kind == "EOF"


def test_single_colon_remains_owned_by_enclosing_declaration():
    parser = reader("value:3")
    declaration = DeclaratorParser(parser.cursor, parser._identifier).parse()
    assert declaration.name.parts == ("value",)
    assert parser.cursor.peek().value == ":"
    assert parser.cursor.peek().coord.column == 6


@pytest.mark.parametrize("source", ("Outer: :value", "Outer:\n:value"))
def test_separate_colon_tokens_do_not_become_scope_operator(source):
    with pytest.raises(CppDiagnostic) as error:
        parse(source)
    assert error.value.code == "cpp.invalid-qualified-name"
    assert error.value.coord.column == 6


def test_parameters_and_array_bounds_retain_callback_payloads_by_identity():
    parser = reader("(*(*dispatch[3])(int))[5]")
    parameters = object()
    bounds = []

    def parameter_clause():
        assert parser.cursor.take().value == "int"
        return parameters

    def array_bound():
        token = parser.cursor.take()
        result = c_ast.Constant("int", token.value, token.coord)
        bounds.append(result)
        return result

    declaration = DeclaratorParser(
        parser.cursor, parser._identifier, parameter_clause, array_bound
    ).parse()
    assert parser.cursor.peek().kind == "EOF"
    assert tuple(chunk.kind for chunk in declaration.chunks) == (
        "array", "pointer", "function", "pointer", "array"
    )
    assert declaration.chunks[0].bound is bounds[0]
    assert declaration.chunks[2].parameters is parameters
    assert declaration.chunks[4].bound is bounds[1]
    assert [bound.value for bound in bounds] == ["3", "5"]


def test_function_cv_and_ref_qualifiers_remain_distinct_syntax():
    declaration = parse("method() volatile const &&")
    function = declaration.chunks[0]
    assert function.qualifiers == ("const", "volatile")
    assert function.ref_qualifier == "&&"
    with pytest.raises(CppDiagnostic) as error:
        require_plain_name(declaration)
    assert error.value.code == "cpp.unsupported.function-declarators"


@pytest.mark.parametrize(
    "source,code",
    (
        ("array[3]", "cpp.unsupported.array-bounds"),
        ("function(int)", "cpp.unsupported.function-parameters"),
        ("function() noexcept", "cpp.unsupported.exceptions"),
        ("function() throw()", "cpp.unsupported.exceptions"),
        ("function() -> int", "cpp.unsupported.trailing-return-types"),
        ("value[[maybe_unused]]", "cpp.unsupported.attributes"),
        ("Owner::*value", "cpp.unsupported.member-pointers"),
        ("Owner<int>::value", "cpp.unsupported.template-id-declarators"),
        ("Owner::template value<int>", "cpp.unsupported.dependent-template-names"),
    ),
)
def test_missing_callback_or_unsupported_syntax_is_an_explicit_gap(source, code):
    with pytest.raises(CppDiagnostic) as error:
        parse(source)
    assert error.value.code == code
    assert error.value.coord.file == "declarator.cpp"


@pytest.mark.parametrize("source", ("(value", "((value)", "value[", "value(int"))
def test_incomplete_declarators_cannot_report_success(source):
    parser = reader(source)
    with pytest.raises(CppDiagnostic):
        DeclaratorParser(
            parser.cursor, parser._identifier,
            lambda: parser.cursor.take(), lambda: parser.cursor.take(),
        ).parse()


def test_callback_must_stop_before_its_closing_delimiter():
    parser = reader("value(int)")
    with pytest.raises(CppDiagnostic) as error:
        DeclaratorParser(parser.cursor, parser._identifier, lambda: ()).parse()
    assert error.value.code == "cpp.invalid-declarator"
    assert error.value.coord.column == 7


@pytest.mark.parametrize("source", ("value(int)", "value[3]"))
def test_nonempty_callback_must_not_discard_its_payload(source):
    parser = reader(source)

    def discarded():
        parser.cursor.take()

    with pytest.raises(CppDiagnostic) as error:
        DeclaratorParser(
            parser.cursor, parser._identifier, discarded, discarded
        ).parse()
    assert error.value.code == "cpp.invalid-declarator"
    assert error.value.coord.column == 6


@pytest.mark.parametrize("source", ("&*value", "value()[]", "value[][]"))
def test_semantically_invalid_compositions_do_not_pass_the_plain_gate(source):
    # Syntax chunks are not a semantic type: pointers to references, functions
    # returning arrays and arrays with inner unknown bounds need later Sema.
    declaration = parse(source)
    with pytest.raises(CppDiagnostic):
        require_plain_name(declaration)


@pytest.mark.parametrize(
    "source,code",
    (
        ("*value", "cpp.unsupported.pointers"),
        ("(&value)", "cpp.unsupported.references"),
        ("(&&value)", "cpp.unsupported.references"),
        ("value[]", "cpp.unsupported.arrays-or-lambdas"),
        ("value()", "cpp.unsupported.function-declarators"),
        ("Outer::value", "cpp.unsupported.qualified-names"),
        ("::value", "cpp.unsupported.qualified-names"),
    ),
)
def test_syntax_acceptance_never_authorizes_rich_type_lowering(source, code):
    declaration = parse(source)
    with pytest.raises(CppDiagnostic) as error:
        require_plain_name(declaration)
    assert error.value.code == code


@pytest.mark.parametrize("source", ("value(3)", "(value)(3)", "((value))(int)"))
def test_plain_name_reader_leaves_the_owner_its_clause(source):
    parser = reader(source)
    token = parse_plain_name(parser.cursor, parser._identifier)
    assert token.value == "value"
    assert parser.cursor.peek().value == "("


@pytest.mark.parametrize(
    "source,code",
    (
        ("(*value[3])", "cpp.unsupported.pointers"),
        ("(&value)[3]", "cpp.unsupported.references"),
        ("((&&value))", "cpp.unsupported.references"),
        ("(Outer::value)", "cpp.unsupported.qualified-names"),
    ),
)
def test_plain_name_reader_rejects_unsupported_prefix_before_grouping(source, code):
    parser = reader(source)
    with pytest.raises(CppDiagnostic) as error:
        parse_plain_name(parser.cursor, parser._identifier)
    assert error.value.code == code


def test_tentative_declarator_parse_restores_cursor_and_never_installs_names():
    parser = reader("(*function)()")
    parser.scope = Scope()
    coord = parser.cursor.peek().coord
    symbol = Symbol("T", "type", coord, dtype="int")
    parser.scope.declare(symbol)
    names = dict(parser.scope.names)
    with parser.cursor.tentative():
        declaration = DeclaratorParser(parser.cursor, parser._identifier).parse()
        assert len(declaration.chunks) == 2
    assert parser.cursor.position == 0
    assert parser.scope.names == names
    with pytest.raises(CppDiagnostic):
        with parser.cursor.tentative():
            parse_plain_name(parser.cursor, parser._identifier)
    assert parser.cursor.position == 0
    assert parser.scope.names == names
    assert parser.scope.lookup("T") is symbol


@pytest.mark.integration
def test_declarator_binding_matches_explicit_cpp17_type_oracle():
    """External reference only: static type checks, not PCC execution evidence."""
    import os
    import shutil
    import subprocess

    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit development oracle unavailable; set PCC_CPP_ORACLE"
    source = r"""
        #include <type_traits>
        int *function();
        extern int (*function_pointer)();
        extern int *array[7];
        extern int (*array_pointer)[7];
        extern int (&function_reference)();
        extern int (&&array_reference)[7];
        extern int * const * volatile qualified_pointer;
        extern int (*(*dispatch[3])(int))[5];
        static_assert(std::is_same_v<decltype(function), int*()>);
        static_assert(std::is_same_v<decltype(function_pointer), int(*)()>);
        static_assert(std::is_same_v<decltype(array), int*[7]>);
        static_assert(std::is_same_v<decltype(array_pointer), int(*)[7]>);
        static_assert(std::is_same_v<decltype(function_reference), int(&)()>);
        static_assert(std::is_same_v<decltype(array_reference), int(&&)[7]>);
        static_assert(std::is_same_v<decltype(qualified_pointer), int*const*volatile>);
        using ArrayPointer = int(*)[5];
        using DispatchFunction = ArrayPointer(int);
        static_assert(std::is_same_v<decltype(dispatch), DispatchFunction*[3]>);
        struct Owner { int method() volatile const &&; };
        static_assert(std::is_same_v<decltype(&Owner::method), int(Owner::*)()const volatile&&>);
    """
    result = subprocess.run(
        [compiler, "-std=c++17", "-pedantic-errors", "-x", "c++", "-fsyntax-only", "-"],
        input=source.encode("utf-8"), capture_output=True, timeout=30,
    )
    assert result.returncode == 0, (
        "explicit declarator type oracle failed: " + repr(result.stderr)
    )
