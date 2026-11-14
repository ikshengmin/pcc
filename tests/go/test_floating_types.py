"""Floating-point types float32 and float64.

https://go.dev/ref/spec#Numeric_types, #Floating-point_literals, #Constants,
#Representability, #Arithmetic_operators, #Comparison_operators, #Conversions
Decimal and hexadecimal floating literals scan as one token and are exact
untyped float constants (rationals, within the 4096-bit constant budget);
untyped int and rune constants mix into the float kind. Typed float
constants round half-to-even to binary32/binary64, overflow is an error and
a constant conversion to an integer type never truncates. Values support
+ - * /, comparisons with IEEE NaN/signed-zero semantics, unary + and -,
integer/float and float/float conversions (truncating toward zero), switch,
arrays and struct fields (aggregate equality compares floats with IEEE ==).
A floating dividend divided by constant zero is IEEE division; % and bitwise
operators, shifts of floats and float-typed shifts are errors. Printing
floats, min/max of floats, floating type arguments and complex numbers stay
explicit. testing's AllocsPerRun returns float64 (go1.27.3
src/testing/allocs.go:20). The program below is original; owned native
arm64 results and every verdict were compared with a local development
go1.23.6.
"""
from fractions import Fraction

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.sema import float_literal
from pcc.frontends.go.syntax import Expr, Token, scan
from pcc.frontends.go.types import round_float
from tests.go.darwin_execution import (
    REFERENCE_UNAVAILABLE, UNAVAILABLE, reference_rejection, run_owned_go_function, run_reference_go_function,
)

PROGRAM = '''package main

type Point struct{ X, Y float64 }

func scale(p Point, k float64) Point { return Point{p.X * k, p.Y * k} }

func mean(values [4]float64) float64 {
	total := 0.0
	for _, v := range values {
		total += v
	}
	return total / float64(len(values))
}

func narrow(x float64) float32 { return float32(x) }

func classify(x float64) uint64 {
	switch x {
	case 0.5:
		return 1
	case 2:
		return 2
	}
	return 3
}

func truncate(x float64) int64 { return int64(x) }

func Answer() uint64 {
	p := scale(Point{1.5, -2.25}, 4)
	m := mean([4]float64{1, 2, 3.5, 5.5})
	r := uint64(p.X)*1000 + uint64(-p.Y)*100 + uint64(m)*10
	if float64(narrow(0.1)) != 0.1 {
		r += 1
	}
	var zero float64
	nan := zero / zero
	if nan != nan {
		r += 10000
	}
	if !(nan < 1) && !(nan >= 1) && !(nan == nan) {
		r += 20000
	}
	if -zero == zero {
		r += 40000
	}
	if 1/zero > 1e308 {
		r += 80000
	}
	r += classify(0.5)*1000000 + classify(m-1)*10000000 + classify(m)*100000000
	r += uint64(-truncate(-7.9)) * 1000000000
	var f float32 = 16777216
	f += 1
	if f == 16777216 {
		r += 10000000000
	}
	const third = 1.0 / 3
	if third*3 == 1 && float32(third) != third {
		r += 20000000000
	}
	if (Point{1, 2} == Point{1, 2}) && [2]float32{0, 1} != [2]float32{0, 2} {
		r += 40000000000
	}
	h := 0x1.8p1 + 1e2 + .5
	return r + uint64(h*2)*1000000000000
}
'''
EXPECTED = 207057321156931


@pytest.mark.parametrize('text,kind', [
    ('1.5', 'float'), ('.5e-3', 'float'), ('1.e+2', 'float'), ('1e9', 'float'), ('09.5', 'float'),
    ('0x1.8p1', 'float'), ('0xFFp-2', 'float'), ('1_000.0_1', 'float'), ('0x1e3', 'integer'),
    ('0b101', 'integer'), ('2i', 'imaginary'), ('1.5i', 'imaginary'),
])
def test_numeric_literals_scan_as_one_token(text, kind):
    token, *rest = scan(text)
    assert (token.kind, token.text) == (kind, text) and [item.kind for item in rest] == [';', 'eof']


@pytest.mark.parametrize('text,value', [
    ('1.5', Fraction(3, 2)), ('.5e-3', Fraction(1, 2000)), ('1.e+2', Fraction(100)), ('0x1.8p1', Fraction(3)),
    ('0xFFp-2', Fraction(255, 4)), ('0x.8p0', Fraction(1, 2)), ('1_000.0_1', Fraction(100001, 100)),
    ('0.1', Fraction(1, 10)),
])
def test_floating_literals_are_exact(text, value):
    assert float_literal(Expr('float', Token('float', text, 'f.go', 1, 1), text)) == value


def test_typed_constants_round_half_to_even_with_subnormals_and_overflow():
    assert round_float(Fraction(1, 10), 'float64') == Fraction(0.1)
    assert round_float(Fraction(2 ** 1024 - 2 ** 970), 'float64') is None      # halfway: rounds to infinity
    assert round_float(Fraction(2 ** 1024 - 2 ** 970 - 1), 'float64') == Fraction(2 ** 1024 - 2 ** 971)
    assert round_float(Fraction(1, 2 ** 1075), 'float64') == 0                 # half the least subnormal: even
    assert round_float(Fraction(3, 2 ** 1076), 'float64') == Fraction(1, 2 ** 1074)
    assert round_float(Fraction(16777217), 'float32') == 16777216
    assert round_float(Fraction(2 ** 128), 'float32') is None


VALID = (
    'package main\nfunc f(x float64) float64 { return x*2.5 + 1 }\nfunc main() { _ = f(1) }\n',
    'package main\nconst c = 1.5\nfunc f() int { return int(c * 2) }\nfunc main() { _ = f() }\n',
    'package main\nvar g = 0.25\nfunc f() uint64 { return uint64(g * 8) }\nfunc main() { _ = f() }\n',
    'package main\nfunc f(x float64) float64 { return x / 0 }\nfunc main() { _ = f(1) }\n',
    'package main\nfunc f(a [4]int) int { return a[2.0] + 1<<2.0 }\nfunc main() { _ = f([4]int{}) }\n',
    'package main\nfunc f(x float32) bool { return x != x || x < 1e-3 }\nfunc main() { _ = f(1) }\n',
)


@pytest.mark.parametrize('source', VALID)
def test_valid_floating_sources_compile(source):
    compile_go(source, 'valid.go')


@pytest.mark.xdist_group(name='go-darwin-native')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
def test_program_runs_with_owned_compile_assemble_and_link(tmp_path, monkeypatch):
    assert run_owned_go_function((('floats.go', PROGRAM),), 'Answer', tmp_path, monkeypatch) == EXPECTED


@pytest.mark.xdist_group(name='go-darwin-native')
@pytest.mark.pcc_gate(unavailable=REFERENCE_UNAVAILABLE)
def test_reference_returns_the_same_value(tmp_path):
    assert run_reference_go_function((('floats.go', PROGRAM),), 'Answer', tmp_path) == EXPECTED


def test_linux_artifact_is_owned_and_stable():
    """Builds the x86-64 Linux executable; its execution is a Linux-lane node."""
    from tests.go.owned_execution import compile_owned
    units = (('floats.go', PROGRAM), ('observer_main.go', 'package main\n\nfunc main() {}\n'))
    first = compile_owned(units, observe_entry=('Answer', ()))
    assert first == compile_owned(units, observe_entry=('Answer', ()))
    assert 'double' in first.ir and 'fcmp' in first.ir


INVALID = (
    ('package main\nfunc f() int { return int(1.5) }\nfunc main() { _ = f() }\n', '2:27', 'truncated'),
    ('package main\nfunc f() int { var x int = 2.5; return x }\nfunc main() { _ = f() }\n', '2:28', 'truncated'),
    ('package main\nfunc f(x float64) float64 { return x % 2 }\nfunc main() { _ = f(1) }\n', '2:36', 'operator %'),
    ('package main\nfunc f(x float64) float64 { return x & 1 }\nfunc main() { _ = f(1) }\n', '2:36', 'operator &'),
    ('package main\nfunc f() float32 { return 1e39 }\nfunc main() { _ = f() }\n', '2:27', 'overflows'),
    ('package main\nfunc f(s uint) float64 { var x float64 = 1 << s; return x }\nfunc main() { _ = f(1) }\n',
     '2:42', 'must be integer'),
    ('package main\nfunc f(x float64) float64 { return ^x }\nfunc main() { _ = f(1) }\n', '2:37', 'operator \\^'),
    ('package main\nfunc f(x float64) float64 { return x << 1 }\nfunc main() { _ = f(1) }\n', '2:36', 'must be integer'),
)
# Rejected by both, at a different position or with a lexical message.
INVALID_ELSEWHERE = (
    ('package main\nfunc f() float64 { return 1.0 / 0 }\nfunc main() { _ = f() }\n', 'division by zero'),
    ('package main\nfunc f(x float64) string { return string(x) }\nfunc main() { _ = f(1) }\n', 'invalid conversion'),
    ('package main\nfunc f(x float64, y float32) bool { return x == y }\nfunc main() { _ = f(1, 1) }\n',
     'mismatched operand types'),
    ('package main\nfunc f() float64 { return 1.e }\nfunc main() { _ = f() }\n', 'invalid floating-point literal'),
    ('package main\nfunc f() float64 { return 0x1.8 }\nfunc main() { _ = f() }\n', 'invalid floating-point literal'),
    ('package main\nfunc f() float64 { return 1_.5 }\nfunc main() { _ = f() }\n', 'invalid floating-point literal'),
)


@pytest.mark.parametrize('source,position,message', INVALID)
def test_invalid_floating_sources_are_errors_at_gc_positions(source, position, message):
    with pytest.raises(GoDiagnostic, match=f'invalid.go:{position}: Go: .*{message}') as caught:
        compile_go(source, 'invalid.go')
    assert not isinstance(caught.value, UnsupportedGo)


@pytest.mark.parametrize('source,message', INVALID_ELSEWHERE)
def test_other_invalid_floating_sources_are_errors(source, message):
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(source, 'invalid.go')
    assert not isinstance(caught.value, UnsupportedGo)


# Former rows, moved verbatim: test_diagnostics' floating-types capability
# case and test_frontend's unimplemented-literal cases. All are now valid.
MOVED_DECLARATION = 'package main;\n' + 'func f(x float64){}' + '\nfunc main(){' + '' + '}\n'
MOVED_LITERALS = ("1e4", "1.5")


def test_former_floating_rows_now_compile():
    compile_go(MOVED_DECLARATION, filename='original.go')
    for literal in MOVED_LITERALS:
        module = compile_go("package main\n" + "" + "\nfunc main() { " + "_ = " + literal + " }\n")
        assert 'define' in str(module)


GAPS = (
    ('package main\nfunc main() { println(1.5) }\n', 'floating-types', 'printing floating values'),
    ('package main\nfunc f(x float64) float64 { return min(x, 1) }\nfunc main() { _ = f(1) }\n',
     'floating-types', 'NaN and signed-zero ordering'),
    ('package main\nfunc f[T any](x T) T { return x }\nfunc main() { _ = f[float64](1) }\n',
     'floating-types', 'type argument'),
    ('package main\nfunc f() float64 { return real(2i) }\nfunc main() { _ = f() }\n', 'complex-types', 'imaginary literals'),
)


@pytest.mark.parametrize('source,suffix,message', GAPS)
def test_remaining_floating_domains_stay_explicit(source, suffix, message):
    with pytest.raises(UnsupportedGo, match=message) as caught:
        compile_go(source, 'gap.go')
    assert caught.value.code == 'go.unsupported.' + suffix


@pytest.mark.xdist_group(name='go-darwin-native')
@pytest.mark.pcc_gate(unavailable=REFERENCE_UNAVAILABLE)
def test_reference_accepts_and_rejects_the_same_sources(tmp_path):
    for index, source in enumerate(VALID + tuple(source for source, _, _ in GAPS)):
        directory = tmp_path / f'valid{index}'
        directory.mkdir()
        returncode, stderr = reference_rejection((('valid.go', source),), directory)
        assert returncode == 0, stderr
    for index, (source, position, _) in enumerate(INVALID):
        directory = tmp_path / f'invalid{index}'
        directory.mkdir()
        returncode, stderr = reference_rejection((('invalid.go', source),), directory)
        assert returncode != 0 and f'invalid.go:{position}: ' in stderr, stderr
    for index, (source, _) in enumerate(INVALID_ELSEWHERE):
        directory = tmp_path / f'elsewhere{index}'
        directory.mkdir()
        returncode, stderr = reference_rejection((('invalid.go', source),), directory)
        assert returncode != 0, stderr
