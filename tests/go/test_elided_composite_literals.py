"""Elided element types in composite literals, and slice types as syntax.

https://go.dev/ref/spec#Composite_literals https://go.dev/ref/spec#Slice_types
Within an array (slice, map) composite literal, an element or key that is
itself a composite literal may elide its type, which is then the element
(key) type; anywhere else a type-less literal is a "missing type" error.
math/bits' bits_test.go builds its tables this way. A slice type is ordinary
syntax everywhere; outside signatures the checker keeps the explicit
slice-storage gap. Original sources; values and rejections were compared with
a local development go1.23.6 reference.
"""
import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go, compile_go_packages
from pcc.frontends.go.syntax import GoParser, SliceSyntax
from scripts import ci_go_math_bits_project as project
from tests.go.darwin_execution import (
    REFERENCE_UNAVAILABLE, UNAVAILABLE, reference_rejection, run_owned_go_function, run_reference_go_function,
)
from tests.go.owned_execution import Execution, compile_owned, run, write_artifact
from tests.go.test_execution import forbid_external_compiler, units

FIXTURE = units(('elided_composite_literals.go',))
EXPECTED = (('Rows', 254), ('Anonymous', 270480), ('Indexed', 9478), ('Nested', 123546))


def parse(source):
    return GoParser().parse(source, 'original.go', require_main=False)


def test_elided_elements_are_type_less_composite_nodes():
    unit = parse('package p\ntype P struct{x, y int}\nvar a = [2]P{{1, 2}, 1: {y: 3}}\n')
    literal, = unit.variables[0].exprs
    first, second = literal.args
    assert [(item.kind, item.value, item.token.column) for item in literal.args] == [
        ('composite', None, 14), ('composite', None, 25)]
    assert [key.value for key in second.keys] == ['y'] and literal.keys[1].value == '1'
    assert [item.value for item in first.args] == ['1', '2']


def test_slice_types_parse_outside_signatures():
    unit = parse('package p\nvar s []int\nvar t = []struct{ x int }{{1}}\n')
    declared, literal = unit.variables[0].type, unit.variables[1].exprs[0]
    assert isinstance(declared, SliceSyntax) and declared.element == 'int'
    assert isinstance(literal.value, SliceSyntax) and literal.args[0].value is None


@pytest.mark.parametrize('target', ['x86_64-unknown-linux-gnu', 'arm64-apple-darwin'])
def test_fixture_compiles_without_an_external_owner(target, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        module = compile_go(FIXTURE[0][1] + '\nfunc main() {}\n', 'elided.go', target_triple=target)
    assert 'define' in str(module)


@pytest.mark.parametrize('function,expected', EXPECTED)
@pytest.mark.xdist_group(name='go-darwin-native')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
def test_fixture_runs_natively_with_owned_compile_assemble_and_link(function, expected, tmp_path, monkeypatch):
    assert run_owned_go_function(FIXTURE, function, tmp_path, monkeypatch) == expected


@pytest.mark.parametrize('function,expected', EXPECTED)
@pytest.mark.xdist_group(name='go-darwin-native')
@pytest.mark.pcc_gate(unavailable=REFERENCE_UNAVAILABLE)
def test_reference_toolchain_computes_the_same_values(function, expected, tmp_path):
    assert run_reference_go_function(FIXTURE, function, tmp_path) == expected


@pytest.mark.integration
@pytest.mark.parametrize('function,expected', EXPECTED)
def test_fixture_executes_linux_owned_observers(function, expected, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(FIXTURE + (('main.go', 'package main\nfunc main() {}\n'),),
                                 observe_entry=(function, ()))
    binary = tmp_path / 'elided-observer'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, expected.to_bytes(8, 'little'), b'')


INVALID = (
    # (source, message, reference message), positions agree with go1.23.6.
    ('package main\ntype pair struct{ x, r uint64 }\ntype Q struct{ p pair }\nfunc main() { q := Q{{1, 2}}; _ = q }\n',
     '4:22: Go: missing type in composite literal', '4:22: missing type in composite literal'),
    ('package main\ntype pair struct{ x, r uint64 }\ntype Q struct{ p pair }\nfunc main() { q := Q{p: {1, 2}}; _ = q }\n',
     '4:25: Go: missing type in composite literal', '4:25: missing type in composite literal'),
    ('package main\ntype pair struct{ x, r uint64 }\nfunc main() { a := [2]pair{{1, 2}: {3, 4}}; _ = a }\n',
     '3:28: Go: missing type in composite literal', '3:28: missing type in composite literal'),
    ('package main\nfunc main() { a := [2]int{{1}, {2}}; _ = a }\n',
     '2:27: Go: composite literal requires an array or struct type', '2:27: invalid composite literal type int'),
    ('package main\nfunc main() { x := {1}; _ = x }\n',
     "2:20: Go: unsupported expression token '{'", '2:20: syntax error: unexpected {'),
)


@pytest.mark.parametrize('source,message,reference', INVALID, ids=[row[1] for row in INVALID])
def test_invalid_elisions_are_source_errors(source, message, reference):
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(source, 'invalid.go')
    assert not isinstance(caught.value, UnsupportedGo)


@pytest.mark.xdist_group(name='go-darwin-native')
@pytest.mark.pcc_gate(unavailable=REFERENCE_UNAVAILABLE)
def test_reference_rejects_the_same_sources_at_the_same_positions(tmp_path):
    for index, (source, _, reference) in enumerate(INVALID):
        directory = tmp_path / str(index)
        directory.mkdir()
        returncode, stderr = reference_rejection((('invalid.go', source),), directory)
        assert returncode == 1 and reference in stderr, stderr


@pytest.mark.parametrize('source,code', (
    # Slice values still need backing storage; the gap moved from parsing to
    # the checker at the same type.
    ('package main\nfunc main() { var s []int; _ = s }\n', 'go.unsupported.slice-storage'),
    ('package main\nfunc main() { for range []struct{ x int }{{1}} {} }\n', 'go.unsupported.slice-storage'),
    ('package main\nfunc main() { a := [2][3]uint64{{1, 2, 3}, {4, 5, 6}}; _ = a }\n', 'go.unsupported.array-element-types'),
))
def test_remaining_literal_shapes_stay_explicit(source, code):
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(source, 'gap.go')
    assert caught.value.code == code


def test_math_bits_external_test_package_now_stops_at_a_function_literal():
    _, files = project.load_inputs()
    source = lambda names: [(name, files[project.PACKAGE_DIR + '/' + name].decode('utf-8')) for name in names]
    packages = {
        'main': [('m.go', 'package main;import _ "math/bits_test";func main(){}')],
        project.PACKAGE: source(project.NORMAL + project.INTERNAL_TEST),
        project.PACKAGE + '_test': source(project.EXTERNAL_TEST),
    }
    # Previously bits_test.go:458:23 (the slice type of the TestReverse
    # table), then :461:3 (its elided {x, r} rows).
    with pytest.raises(GoDiagnostic, match=r"bits_test.go:716:10: Go: unsupported expression token 'func'"):
        compile_go_packages(packages, target_triple='x86_64-unknown-linux-gnu')
