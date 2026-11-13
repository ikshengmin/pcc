"""Function types and function literals as syntax, with an explicit value gap.

https://go.dev/ref/spec#Function_types https://go.dev/ref/spec#Function_literals
`func(x, y, c uint) (z, cout uint)` is parsed wherever a type may appear, and
`func(params) results { body }` wherever an operand may appear, including a
called literal used as a statement. Function values need closures (captured
variables live beyond their frame) and an indirect-call ABI, so the checker
reports go.unsupported.function-values at the first type or literal it
checks; nothing is lowered. math/bits' bits_test.go uses both from line 716.
Original sources; acceptance of the valid ones was compared with a local
development go1.23.6 reference.
"""
import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.syntax import FunctionLiteral, FunctionTypeSyntax, GoParser, SliceSyntax
from tests.go.darwin_execution import REFERENCE_UNAVAILABLE, reference_rejection


def parse(source):
    return GoParser().parse(source, 'original.go', require_main=False)


def test_function_types_keep_parameters_and_named_results():
    unit = parse('package p\nfunc t(f func(x, y, c uint) (z, cout uint), g func(uint64, uint64) uint64) {}\n')
    (_, first), (_, second) = unit.functions[0].params
    assert isinstance(first, FunctionTypeSyntax) and first.parameters == ['uint', 'uint', 'uint']
    assert [name.text for name in first.names] == ['x', 'y', 'c']
    assert [name.text for name in first.result_names] == ['z', 'cout']
    assert isinstance(second, FunctionTypeSyntax) and second.names == [None, None] and second.result == 'uint64'


def test_function_literals_keep_signature_and_body():
    unit = parse('package p\nfunc t() {\n\ttest := func(msg string, x uint) uint { return x }\n'
                 '\t_ = test\n\tfunc() {}()\n}\n')
    assign, _, call = unit.functions[0].body
    literal = assign.exprs[-1]
    assert literal.kind == 'func_literal' and isinstance(literal.value, FunctionLiteral)
    assert [name.text for name in literal.value.signature.names] == ['msg', 'x']
    assert literal.value.signature.result == 'uint' and len(literal.value.body) == 1
    assert call.exprs[0].kind == 'call' and call.exprs[0].callee.kind == 'func_literal'


def test_slices_of_function_types_parse():
    unit = parse('package p\nvar tests = []func(uint64, uint64) uint64{func(a, b uint64) uint64 { return a }}\n')
    literal = unit.variables[0].exprs[0]
    assert isinstance(literal.value, SliceSyntax) and isinstance(literal.value.element, FunctionTypeSyntax)
    assert literal.args[0].kind == 'func_literal'


VALID = (
    'package main\nfunc main() { f := func(x int) int { return x }; _ = f }\n',
    'package main\nfunc main() { func() {}() }\n',
    'package main\nfunc main() { x := func() int { return 1 }(); _ = x }\n',
    'package main\nfunc g(f func(int) int) int { return f(1) }\nfunc main() {}\n',
    'package main\ntype F func() (int, int)\nfunc main() {}\n',
)


@pytest.mark.parametrize('source', VALID)
def test_function_values_are_an_explicit_checker_gap(source):
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(source, 'gap.go')
    assert caught.value.code == 'go.unsupported.function-values'


@pytest.mark.xdist_group(name='go-darwin-native')
@pytest.mark.pcc_gate(unavailable=REFERENCE_UNAVAILABLE)
def test_reference_accepts_the_same_sources(tmp_path):
    for index, source in enumerate(VALID):
        directory = tmp_path / str(index)
        directory.mkdir()
        returncode, stderr = reference_rejection((('valid.go', source),), directory)
        assert returncode == 0, stderr


def test_a_function_type_is_not_a_value():
    with pytest.raises(GoDiagnostic) as caught:
        compile_go('package main\nfunc main() { x := func(); _ = x }\n', 'invalid.go')
    assert not isinstance(caught.value, UnsupportedGo)


# The math/bits frontier (formerly bits_test.go:851:4, `defer`) moved to
# tests/go/test_defer_statements.py.
