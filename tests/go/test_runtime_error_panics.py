"""Dynamic integer division and runtime error values as terminal panics.

https://go.dev/ref/spec#Integer_operators (a zero divisor causes a run-time
panic; for the most negative value x, x / -1 == x and x % -1 == 0),
https://go.dev/ref/spec#Run_time_panics and https://go.dev/ref/spec#Handling_panics.
Go's runtime/panic.go panics with divideError for a zero divisor; an uncaught
panic with a runtime error reports its Error() text. Both reuse the existing
terminal uncaught panic (no defer/recover/goroutine in the admitted domain).
A runtime-owned error bound by //go:linkname is admitted only as the whole
panic operand: no interface value is materialized. Other value uses and
non-Linux targets stay explicit gaps (the builtin-io target boundary).
"""
import hashlib
import os

import pytest

from pcc.frontends.go import UnsupportedGo, compile_go
from tests.go.owned_execution import Execution, compile_owned, compile_reference, write_artifact
from tests.go.test_builtin_io import capture
from tests.go.test_execution import forbid_external_compiler

LINKNAME = 'package main\nimport _ "unsafe"\n//go:linkname e runtime.{}\nvar e error\nfunc main(){{{}}}\n'

QUOTIENTS = (
    'package main\n'
    'func q64(a, b int64) int64 { return a / b }\n'
    'func r64(a, b int64) int64 { return a % b }\n'
    'func q8(a, b int8) int8 { return a / b }\n'
    'func r8(a, b int8) int8 { a %= b; return a }\n'
    'func qu(a, b uint64) uint64 { return a / b }\n'
    'func ru16(a, b uint16) uint16 { return a % b }\n'
    'func main() {\n'
    '\tm := int64(-9223372036854775808)\n'
    '\tprintln(q64(m, -1), r64(m, -1), q64(-7, 2), r64(-7, 2), q64(7, -2), r64(7, -2))\n'
    '\tprintln(q8(-128, -1), r8(-128, -1), q8(-7, 2), r8(-7, 2))\n'
    '\tprintln(qu(18446744073709551615, 10), ru16(65535, 256))\n'
    '}\n'
)
QUOTIENTS_STDERR = (b'-9223372036854775808 0 -3 -1 -3 1\n-128 0 -3 -1\n'
                    b'1844674407370955161 255\n')
DIVIDE_BY_ZERO = (
    'package main\n'
    'func q(a, b uint32) uint32 { return a / b }\n'
    'func main() { println("before"); println(q(1, 0)); println("after") }\n'
)
REMAINDER_BY_ZERO = 'package main\nfunc r(a, b int) int { a %= b; return a }\nfunc main() { _ = r(1, 0) }\n'
PANICS = (
    ('divide-by-zero', DIVIDE_BY_ZERO, b'before\npanic: runtime error: integer divide by zero\n'),
    ('remainder-by-zero', REMAINDER_BY_ZERO, b'panic: runtime error: integer divide by zero\n'),
    ('overflow-error-operand', LINKNAME.format('overflowError', 'panic(e)'),
     b'panic: runtime error: integer overflow\n'),
    ('parenthesized-divide-error', LINKNAME.format('divideError', 'println("x");panic((e))'),
     b'x\npanic: runtime error: integer divide by zero\n'),
)


def test_dynamic_division_checks_the_divisor_before_dividing():
    ir = str(compile_go(QUOTIENTS, 'quotients.go'))
    assert ir.count('division_by_zero:') == 6
    assert 'c"\\70\\61\\6E\\69\\63\\3A\\20\\72\\75\\6E\\74\\69\\6D\\65' in ir  # "panic: runtime"


@pytest.mark.parametrize('name,original,_', PANICS, ids=[row[0] for row in PANICS])
def test_runtime_error_panics_lower_without_an_interface_value(name, original, _):
    compile_go(original, 'panic.go')


@pytest.mark.parametrize('use', ['_=e', 'if e!=nil{}', 'var f error=e;_=f', 'panic(e!=nil)', 'println(e)'])
def test_other_runtime_owned_error_uses_stay_interface_gaps(use):
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(LINKNAME.format('divideError', use), 'original.go')
    assert caught.value.code == 'go.unsupported.interface-types'


def test_nonlinux_dynamic_division_is_an_explicit_target_boundary():
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(DIVIDE_BY_ZERO, 'panic.go', target_triple='arm64-apple-darwin')
    assert caught.value.code == 'go.unsupported.builtin-io'
    assert (caught.value.line, caught.value.column) == (2, 39)


@pytest.mark.integration
@pytest.mark.parametrize('name,original,expected', [('quotients', QUOTIENTS, QUOTIENTS_STDERR), *PANICS],
                         ids=['quotients', *[row[0] for row in PANICS]])
def test_runtime_error_panics_execute_natively(name, original, expected, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(((name + '.go', original),))
    binary = tmp_path / 'native'
    write_artifact(artifact, binary)
    actual = capture(tmp_path, 'pcc', binary, hashlib.sha256(original.encode()).hexdigest())
    assert actual == Execution(0 if name == 'quotients' else 2, b'', expected)


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('name,original,expected', [('quotients', QUOTIENTS, QUOTIENTS_STDERR), *PANICS],
                         ids=['quotients', *[row[0] for row in PANICS]])
def test_official_go_reports_the_same_status_and_panic_prefix(name, original, expected, tmp_path):
    reference = compile_reference(((name + '.go', original),), tmp_path / 'reference',
                                  compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    oracle = capture(tmp_path, 'reference', reference, hashlib.sha256(original.encode()).hexdigest())
    assert oracle.stdout == b''
    if name == 'quotients':
        assert oracle == Execution(0, b'', expected)
    else:
        # Official Go appends its goroutine traceback after the same report.
        assert oracle.returncode == 2 and oracle.stderr.startswith(expected)
