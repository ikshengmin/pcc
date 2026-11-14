"""Defer statements as syntax, with an explicit deferred-call gap.

https://go.dev/ref/spec#Defer_statements
`defer Call` parses: the operand must be a function or method call and
cannot be parenthesized, both syntax errors at the position gc reports. The
checker checks the call as an expression statement would (conversions and
undefined functions are ordinary errors) and then reports
go.unsupported.defer: running deferred calls at function exit, also while
panicking, and recover() need the shared unwinding design, and today's
terminal panics assume no deferred call exists. math/bits' bits_test.go
uses `defer func() { ... recover() ... }()` from line 851. Original sources;
each verdict and position was compared with a local development go1.23.6.
"""
import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.syntax import GoParser
from tests.go.darwin_execution import REFERENCE_UNAVAILABLE, reference_rejection


def test_defer_keeps_its_call():
    unit = GoParser().parse('package p\ntype T int\nfunc (T) m() {}\nfunc f(x int) {}\n'
                            'func g(t T) {\n\tdefer f(1)\n\tdefer t.m()\n}\n', 'original.go', require_main=False)
    first, second = unit.functions[-1].body
    assert (first.kind, first.exprs[0].kind, first.token.line) == ('defer', 'call', 6)
    assert (second.kind, second.exprs[0].kind) == ('defer', 'call')


VALID = (
    'package main\nfunc f() {}\nfunc main() { defer f() }\n',
    'package main\nfunc f(x int) int { return x }\nfunc main() { defer f(1) }\n',
    'package main\ntype T int\nfunc (T) m() {}\nfunc main() { var t T; defer t.m() }\n',
    'package main\nfunc main() { defer println("x") }\n',
)


@pytest.mark.parametrize('source', VALID)
def test_deferred_calls_are_an_explicit_checker_gap(source):
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(source, 'gap.go')
    assert caught.value.code == 'go.unsupported.defer'
    assert ': Go: deferred calls require function-exit sequencing' in str(caught.value)


def test_a_deferred_function_literal_reports_the_function_value_gap_first():
    with pytest.raises(UnsupportedGo) as caught:
        compile_go('package main\nfunc main() { defer func() {}() }\n', 'gap.go')
    assert caught.value.code == 'go.unsupported.function-values'


INVALID = (
    ('package main\nfunc f() {}\nfunc main() { defer (f()) }\n', '3:21', 'expression in defer must not be parenthesized'),
    ('package main\nfunc f() {}\nfunc main() { defer f }\n', '3:21', 'expression in defer must be function call'),
    ('package main\nfunc main() { defer g() }\n', '2:21', 'undefined'),
    ('package main\nfunc main() { defer int(1) }\n', '2:21', 'defer requires'),
)


@pytest.mark.parametrize('source,position,message', INVALID)
def test_invalid_defer_statements_are_source_errors(source, position, message):
    with pytest.raises(GoDiagnostic, match=f'invalid.go:{position}: Go: .*{message}') as caught:
        compile_go(source, 'invalid.go')
    assert not isinstance(caught.value, UnsupportedGo)


@pytest.mark.xdist_group(name='go-darwin-native')
@pytest.mark.pcc_gate(unavailable=REFERENCE_UNAVAILABLE)
def test_reference_accepts_and_rejects_the_same_sources(tmp_path):
    for index, source in enumerate(VALID):
        directory = tmp_path / f'valid{index}'
        directory.mkdir()
        returncode, stderr = reference_rejection((('valid.go', source),), directory)
        assert returncode == 0, stderr
    for index, (source, position, message) in enumerate(INVALID):
        directory = tmp_path / f'invalid{index}'
        directory.mkdir()
        returncode, stderr = reference_rejection((('invalid.go', source),), directory)
        assert returncode != 0 and f'invalid.go:{position}: ' in stderr, stderr
        assert message.split()[0] in stderr, stderr


# The math/bits frontier (formerly bits_test.go:1038:26, the type assertion)
# moved to tests/go/test_type_assertions.py.
