"""Type assertions as syntax, inside the interface-values boundary.

https://go.dev/ref/spec#Type_assertions
`x.(T)` parses wherever a primary expression continues (the comma-ok form
too). The checker requires x to be of interface type: a non-interface
operand and an undefined asserted type are ordinary errors at the positions
gc reports. A dynamic type check needs interface values, which remain the
explicit go.unsupported.interface-types boundary; `x.(type)` (type
switches) stays inside the same boundary at parse time. math/bits'
bits_test.go asserts `err.(runtime.Error)` at 1038. Original sources; each
verdict and position was compared with a local development go1.23.6.
"""
import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.syntax import GoParser
from tests.go.darwin_execution import REFERENCE_UNAVAILABLE, reference_rejection


def test_assertion_keeps_operand_and_type():
    unit = GoParser().parse('package p\nfunc f(e error) {\n\t_, _ = e.(interface{ Error() string })\n}\n',
                            'original.go', require_main=False)
    assertion = unit.functions[0].body[0].exprs[0]
    assert assertion.kind == 'type_assert' and assertion.args[0].value == 'e'
    assert (assertion.token.line, assertion.token.column) == (3, 11)  # the `(`


VALID = (
    'package main\nfunc main() { var e error; _, _ = e.(interface{ Error() string }) }\n',
    'package main\nfunc main() { var e error; _ = e.(error) }\n',
)


@pytest.mark.parametrize('source', VALID)
def test_assertions_on_interface_values_stay_explicit(source):
    with pytest.raises(UnsupportedGo, match='type assertions require interface dynamic types') as caught:
        compile_go(source, 'gap.go')
    assert caught.value.code == 'go.unsupported.interface-types'


def test_type_switches_stay_inside_the_interface_boundary():
    with pytest.raises(UnsupportedGo, match='type switches') as caught:
        compile_go('package main\nfunc f(x any) { switch x.(type) {} }\nfunc main() {}\n', 'gap.go')
    assert caught.value.code == 'go.unsupported.interface-types'


INVALID = (
    ('package main\nfunc main() { x := 1; _ = x.(int) }\n', '2:27', 'is not an interface'),
    ('package main\nfunc main() { var e error; _ = e.(Missing) }\n', '2:35', 'undefined'),
)


@pytest.mark.parametrize('source,position,message', INVALID)
def test_invalid_assertions_are_source_errors(source, position, message):
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
        assert returncode != 0 and f'invalid.go:{position}: ' in stderr and message in stderr, stderr


# The math/bits frontier (formerly bits_test.go:9:2, the runtime import) moved
# to tests/go/test_owned_runtime_package.py.
