"""Integral constant proofs for C++17 narrowing, not full constexpr execution.

Original PCC rules use N4659 [expr.const], [expr.mul], [expr.shift] and
[dcl.init.list]. No upstream compiler tests or implementation are copied.
Existing owned compilation/execution and explicitly labeled C++17 reference
adapters remain the only execution paths; facts never replace the runtime AST.
"""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.constants import KNOWN
from pcc.frontends.cpp.parser import TokenCursor
from pcc.frontends.cpp.sema import Scope
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record


def byte_case(expression, prefix="", statements=""):
    return (prefix + "struct S{unsigned char value;};int cpp_test_entry(){"
            + statements + "S s{" + expression + "};return s.value;}")


CONSTANT_CASES = (
    ("integral-precedence", byte_case("6*7"), 42),
    ("division-truncates-toward-zero", byte_case("(-85/2)+84"), 42),
    ("remainder-follows-truncating-quotient", byte_case("(-85%2)+43"), 42),
    ("unsigned-arithmetic-wrap", byte_case("4294967295u+43u"), 42),
    ("typed-nondecimal-literals", byte_case("0x20+010+0b10"), 42),
    ("const-initializer-stores-converted-value",
     "struct S{signed char value;};int cpp_test_entry(){const unsigned char k=298;"
     "S s{k};return s.value;}", 42),
    ("const-expression-name", byte_case("k+2", statements="const int k=6*7-2;"), 42),
    ("inner-scope-does-not-change-outer-binding",
     byte_case("k", statements="const int k=42;{int k=7;k=k+1;}"), 42),
    ("short-circuit-excludes-runtime-read",
     byte_case("42+(false&&n)", statements="int n=3;"), 42),
    ("short-circuit-excludes-runtime-call",
     "int bump(int*p){*p=*p+1;return 9;}struct S{unsigned char value;};"
     "int cpp_test_entry(){int n=0;S s{41+(true||bump(&n))};return s.value+n;}", 42),
    ("short-circuit-excludes-division-by-zero", byte_case("42+(false&&(1/0))"), 42),
    ("short-circuit-excludes-signed-overflow", byte_case("42+(false&&(2147483647+1))"), 42),
    ("self-name-is-not-retroactively-initialized",
     byte_case("k", statements="const int k=42+(false&&k);"), 42),
    ("bool-conversion-fact", byte_case("41+bool(2)"), 42),
    ("signed-left-shift-representable", byte_case("21<<1"), 42),
    ("unsigned-shift-high-bit", byte_case("((1u<<31)>>26)+10u"), 42),
    ("nonnegative-right-shift", byte_case("84>>1"), 42),
    ("comparison-retains-cpp-bool-proof", byte_case("41+(3<4)"), 42),
    ("nonnegative-bitwise-expression", byte_case("(63&42)|0"), 42),
    ("unsigned-complement-uses-target-width", byte_case("~4294967253u"), 42),
    ("explicit-integral-conversion-proof",
     "using U=unsigned char;struct S{signed char value;};"
     "int cpp_test_entry(){S s{U(298)};return s.value;}", 42),
    ("short-circuit-excludes-instantiated-nonconstexpr-call",
     byte_case("42+(false&&id(9))", "template<class T>T id(T n){return n;}"), 42),
    ("value-initialized-integral-cast", byte_case("42+int()"), 42),
    ("no-constant-requirement-for-range-contained-conversion",
     "struct S{long value;};int cpp_test_entry(){int n=42;S s{n};return s.value;}", 42),
    ("literal-zero-pointer-semantics-unchanged",
     "struct S{int*p;unsigned char value;};int cpp_test_entry(){S s{0x0,42};"
     "return s.value+int(s.p!=nullptr);}", 42),
)


NARROWING_CASES = (
    ("evaluated-mutable-read", byte_case("n", statements="int n=42;")),
    ("inner-mutable-name-hides-const",
     "struct S{unsigned char value;};int f(){const int n=42;{int n=42;S s{n};}return 0;}"),
    ("parameter-is-not-a-constant",
     "struct S{unsigned char value;};int f(const int n){S s{n};return s.value;}"),
    ("evaluated-nonconstexpr-call", byte_case("value()", "int value(){return 42;}")),
    ("const-name-with-runtime-initializer",
     byte_case("n", "int value(){return 42;}", "const int n=value();")),
    ("self-initialization-has-no-preceding-proof",
     byte_case("n", statements="const int n=n;")),
    ("evaluated-signed-add-overflow", byte_case("2147483647+1")),
    ("evaluated-signed-negation-overflow", byte_case("-(-2147483647-1)")),
    ("evaluated-signed-multiply-overflow", byte_case("2147483647*2")),
    ("evaluated-division-by-zero", byte_case("1/0")),
    ("evaluated-remainder-by-zero", byte_case("1%0")),
    ("signed-division-overflow", byte_case("(-2147483647-1)/-1")),
    ("signed-remainder-overflow", byte_case("(-2147483647-1)%-1")),
    ("negative-shift-count", byte_case("1<<-1")),
    ("too-large-shift-count", byte_case("1<<32")),
    ("negative-signed-left-shift", byte_case("-1<<1")),
    ("left-shift-outside-corresponding-unsigned", byte_case("2147483647<<2")),
    ("assignment-cannot-prove-a-runtime-object-constant",
     byte_case("n=42", statements="int n=0;")),
    ("nonconstant-left-logical-operand-is-not-folded",
     byte_case("42+(n&&false)", statements="int n=0;")),
)


UNMODELED_CASES = (
    ("signed-right-shift-negative", byte_case("(-84>>1)+84")),
    ("signed-left-shift-conversion-policy", byte_case("(1<<31)+2147483647")),
    ("signed-complement-representation", byte_case("~0+43")),
    ("negative-signed-bitwise-representation", byte_case("(-1&42)")),
    ("out-of-range-signed-explicit-conversion", byte_case("int(4294967295u)+43")),
    ("floating-to-integral-constant-proof", byte_case("int(42.0)")),
    ("pointer-derived-read-proof", byte_case("*p", statements="const int n=42;const int*p=&n;")),
    ("const-class-member-not-a-complete-integral-object",
     "struct I{const int x;};struct S{unsigned char value;};"
     "int cpp_test_entry(){const I i{42};S s{i.x};return s.value;}"),
)


@pytest.mark.parametrize("name,source,value", CONSTANT_CASES,
                         ids=[case[0] for case in CONSTANT_CASES])
def test_integral_constant_admission_preserves_deterministic_owned_artifacts(name, source, value):
    units = ((name + ".cpp", source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize("name,source", NARROWING_CASES,
                         ids=[case[0] for case in NARROWING_CASES])
def test_proven_nonconstant_or_unrepresentable_integer_narrowing_is_rejected(name, source, monkeypatch):
    _reject_before_emission(name, source, "cpp.narrowing-conversion", monkeypatch)


@pytest.mark.parametrize("name,source", UNMODELED_CASES,
                         ids=[case[0] for case in UNMODELED_CASES])
def test_unmodeled_constant_semantics_remain_capability_gates(name, source, monkeypatch):
    _reject_before_emission(name, source, "cpp.unsupported.narrowing-analysis", monkeypatch)


def _reject_before_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid/unmodeled constant initializer reached emission")

    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name + ".cpp", source),))
    assert error.value.code == code


@pytest.mark.parametrize("initializer", ("0+0", "zero"))
def test_integral_constant_zero_does_not_become_a_null_pointer_literal(initializer):
    source = ("int f(){const int zero=0;int*p=" + initializer + ";return 0;}")
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == "cpp.invalid-conversion"


@pytest.mark.parametrize("source,kind", (
    (byte_case("6*7"), c_ast.BinaryOp),
    (byte_case("k", statements="const int k=42;"), c_ast.ID),
))
def test_constant_proof_does_not_replace_original_expression_ast(source, kind):
    tree = CppParser().parse(source)
    function = next(item for item in tree.ext if isinstance(item, c_ast.FuncDef))
    declaration = next(item for item in function.body.block_items
                       if isinstance(item, c_ast.Decl) and item.name == "s")
    clause = declaration.init.exprs[0]
    assert isinstance(clause, c_ast.Cast)
    assert isinstance(clause.expr, kind)


def test_public_narrowing_uses_target_long_width_not_host_integer_width():
    source = "struct S{long x;};int f(){S s{2147483648u};return 0;}"
    CppParser("x86_64-unknown-linux-gnu").parse(source)
    with pytest.raises(CppDiagnostic) as error:
        CppParser("x86_64-pc-windows-gnu").parse(source)
    assert error.value.code == "cpp.narrowing-conversion"
    CppParser("x86_64-pc-windows-gnu").parse(source.replace("2147483648", "2147483647"))


@pytest.mark.parametrize("source,value", (("true", 1), ("false", 0)))
def test_bool_literal_pattern_retains_proof_without_becoming_a_null_literal(source, value):
    parser = CppParser()
    parser.scope = Scope()
    parser.parsing_template = True
    parser.template_function = object()
    parser.cursor = TokenCursor(parser._tokenize(source, "bool-pattern.cpp"))
    pattern = parser._primary()
    parser.template_function = None
    result = parser._instantiate_expression(pattern, {}, {})
    assert result.constant_fact.status == KNOWN
    assert result.constant_fact.dtype == "bool"
    assert result.constant_fact.value == value
    assert not result.null_pointer


@pytest.mark.integration
@pytest.mark.parametrize("name,source,value", CONSTANT_CASES,
                         ids=[case[0] for case in CONSTANT_CASES])
def test_integral_constant_admission_executes_original_ast_against_cpp17_reference(
    name, source, value, tmp_path, monkeypatch,
):
    execute_and_record(tmp_path, monkeypatch, ((name + ".cpp", source),), value)
