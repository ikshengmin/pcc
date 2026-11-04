"""Arrays of admitted value records, driven by the original math/bits table.

https://go.dev/ref/spec#Array_types https://go.dev/ref/spec#Struct_types
https://go.dev/ref/spec#Composite_literals https://go.dev/ref/spec#Assignments
https://go.dev/ref/spec#Comparison_operators https://go.dev/ref/spec#For_range
Source, owned-native execution and unchanged official-reference inputs are
separate obligations. These components do not qualify the whole math/bits
package, its init function, panic/recovery, testing dependency or native pcc1.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from tests.go.owned_execution import (
    Execution, compile_owned, compile_reference, compile_reference_rejection,
    run, write_artifact,
)
from tests.go.test_diagnostics import original as diagnostic_original
from tests.go.test_execution import forbid_external_compiler, units
from tests.go.test_nullable_pointers import record_pair
from tests.go.test_records import source


# Every exact former diagnostic original remains a complete positive program.
# They are not replacements for the full-width observers in the new fixture.
GRADUATED = (
    diagnostic_original('type T struct{x int};var x [1]T'),
    source('type A struct{X int}', 'var a [2]A;_=a'),
    source('type R struct{X int}', 'var a [1]R;_=a==a'),
    source('func Zero[T any]() [0]T{var x [0]T;return x}', '_=Zero[struct{X int}]()'),
)
PROGRAMS = (
    *[('former-' + str(i), (('original.go', text),)) for i, text in enumerate(GRADUATED)],
    ('record-arrays', tuple(units(('record_arrays.go',)))),
    ('math-bits-record-table', tuple(units(('math_bits_record_table.go',)))),
)
RAW = (
    ('record_arrays.go', 'GlobalRecord', 0x8877665544332211),
    ('record_arrays.go', 'Copies', 0x8877665544332211),
    ('record_arrays.go', 'NestedRecord', 513),
    ('record_arrays.go', 'ParallelFields', 44030),
    ('record_arrays.go', 'ArrayRangeCopy', 10),
    ('record_arrays.go', 'ArrayRangePointer', 102),
    ('record_arrays.go', 'CompareAndSwitch', 42),
    ('record_arrays.go', 'GenericAndEmpty', 0xfedcba9876543210),
    ('record_arrays.go', 'EmptyRecords', 332),
    ('math_bits_record_table.go', 'RecordTable', 888),
)


def test_project_table_declarations_remain_exact_upstream_bytes():
    root = Path(__file__).resolve().parent
    original = (root / 'upstream/go1.27.3/src/math/bits/bits_test.go').read_bytes()
    declarations = b''.join(original.splitlines(keepends=True)[1309:1315])
    assert declarations == (
        b'type entry = struct {\n\tnlz, ntz, pop int\n}\n\n'
        b'// tab contains results for all uint8 values\nvar tab [256]entry\n'
    )
    assert declarations in (root / 'fixtures/math_bits_record_table.go').read_bytes()


@pytest.mark.parametrize('name,originals', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_complete_record_array_originals_lower_without_external_owner(name, originals, monkeypatch):
    forbid_external_compiler(monkeypatch)
    assert str(compile_go(originals[0][1])) == str(compile_go(originals[0][1]))


@pytest.mark.parametrize('declarations', (
    'type R struct{X int};var a [2048]R',
    'type R struct{X [2047]int};var a [2]R',
    'type R struct{};var a [4096]R',
    'type R struct{X [2]int;S string};var a [0]R',
))
def test_record_array_layout_keeps_original_bounded_budget(declarations):
    compile_go(source(declarations))


@pytest.mark.parametrize('alias', ('', '='))
@pytest.mark.parametrize('count', (0, 1))
def test_empty_record_array_type_expansion_is_bounded_before_ir(alias, count, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unbounded type reached IR'))
    declarations = ['type R0 ' + alias + ' struct{}']
    for level in range(1, 15):
        declarations.append(f'type R{level} {alias} struct{{A [{count}]R{level-1};B [{count}]R{level-1}}}')
    original = source(';'.join(declarations), 'var x R14;_=x')
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original)
    assert caught.value.code == 'go.unsupported.limit-record-layout'


@pytest.mark.parametrize('check', ('require_bounded_value_layout', 'require_value_type'))
def test_shared_empty_record_type_graph_rejected_without_expansion(check, monkeypatch):
    from pcc.frontends.go import sema
    from pcc.frontends.go.records import Field, StructType
    from pcc.frontends.go.syntax import Token
    from pcc.frontends.go.types import ArrayType

    record = StructType(())
    for _ in range(30):
        array = ArrayType(0, record)
        record = StructType((Field('A', array), Field('B', array)))
    original_underlying = sema.underlying
    visits = 0

    def counted(value):
        nonlocal visits
        visits += 1
        assert visits < 200, 'shared type graph expanded before its bound was checked'
        return original_underlying(value)

    monkeypatch.setattr(sema, 'underlying', counted)
    token = Token('ident', 'R30', 'resource.go', 1, 1)
    with pytest.raises(UnsupportedGo) as caught:
        getattr(sema, check)(record, token)
    assert caught.value.code == 'go.unsupported.limit-record-layout'


FRONTIERS = (
    ('limit-record-layout', 'type R struct{X int};var a [2049]R'),
    ('limit-record-layout', 'type R struct{X [2048]int};var a [2]R'),
    ('limit-record-layout', 'type R struct{X [100]int};type S struct{A [30]R};var a [2]S'),
    ('pointer-storage', 'func f(a [0]struct{P *int}){}'),
    ('pointer-storage', 'func f() [0]struct{R struct{P *int}}{for{}}'),
    ('interface-types', 'func f(a [0]struct{E error}){}'),
    ('interface-types', 'func f(a [0]struct{E any}){}'),
    ('slice-storage', 'func f(a [0]struct{S []int}){}'),
    ('array-element-types', 'var a [1][1]int'),
    ('array-element-types', 'var a [1]string'),
    ('array-bounds-panic', 'type R struct{X int};func f(a [2]R,i int)int{return a[i].X}'),
    ('array-bounds-panic', 'type R struct{X int};func next()uint64{return 1};func f(a [2]R)int{return a[next()].X}'),
    ('pointer-nil', 'type R struct{X int};func f(p *[1]R)int{return p[0].X}'),
    ('package-initialization', 'type R struct{X int};func f()R{return R{3}};var a=[1]R{f()}'),
)


@pytest.mark.parametrize('code,declarations', FRONTIERS)
def test_record_arrays_preserve_storage_runtime_and_resource_frontiers(code, declarations, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported record array reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(source(declarations))
    assert caught.value.code == 'go.unsupported.' + code


INVALID = (
    ('type R struct{X int};type S struct{X int}',
     'var a [1]R;var b [1]S;a=b;_=a', 'cannot use', 'cannot use'),
    ('type R struct{X int}', '_=[1]R{R{1},R{2}}', 'out of bounds', 'out of bounds'),
    ('type R struct{X int}', '_=[2]R{1:R{1},1:R{2}}', 'duplicate', 'duplicate index'),
    ('type R struct{X int}', '_=[1]R{7}', 'cannot use', 'cannot use'),
    ('type R struct{X int};func f()[1]R{return [1]R{R{1}}}',
     'f()[0].X=3', 'non-addressable', 'cannot assign'),
    ('type R struct{X int}', '[1]R{R{1}}[0].X=3', 'non-addressable', 'cannot assign'),
    ('type R struct{X int}', 'var a [1]R;_=a[1].X', 'out of bounds', 'out of bounds'),
    ('type R struct{X int}', 'var a [1]R;_=a<a', 'only equality', 'not defined|cannot'),
    ('type R struct{X int}', 'const a=[1]R{R{1}};_=a', 'not a constant', 'not constant'),
)


@pytest.mark.parametrize('declarations,body,message,reference_message', INVALID)
def test_invalid_record_arrays_fail_as_source_errors(declarations, body, message, reference_message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid record array reached IR'))
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(source(declarations, body))
    assert not isinstance(caught.value, UnsupportedGo)


@pytest.mark.integration
@pytest.mark.parametrize('filename,name,expected', RAW)
def test_record_arrays_execute_independent_full_width_observers(filename, name, expected, tmp_path, monkeypatch):
    originals = units((filename,))
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals, observe_entry=(name, ()))
    binary = tmp_path / 'record-array-observer'
    write_artifact(artifact, binary)
    actual = run(binary)
    (tmp_path / 'observation.json').write_text(json.dumps({
        'owner': artifact.owner, 'function': name,
        'source_sha256': hashlib.sha256(originals[0][1].encode()).hexdigest(),
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'returncode': actual.returncode, 'stdout_hex': actual.stdout.hex(),
        'stderr_hex': actual.stderr.hex(), 'expected_hex': expected.to_bytes(8, 'little').hex(),
    }, indent=2) + '\n')
    assert actual == Execution(0, expected.to_bytes(8, 'little'), b'')


@pytest.mark.integration
@pytest.mark.parametrize('name,originals', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_complete_record_array_programs_execute_owned(name, originals, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    binary = tmp_path / 'record-array-main'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('name,originals', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_identical_record_array_programs_match_official(name, originals, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    binary = tmp_path / 'record-array-main'
    write_artifact(artifact, binary)
    reference = compile_reference(originals, tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    record_pair(tmp_path, hashlib.sha256(originals[0][1].encode()).hexdigest(), binary, reference)
    for filename, text in originals:
        assert (reference.parent / filename).read_bytes() == text.encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('declarations,body,message,reference_message', INVALID)
def test_identical_invalid_record_arrays_have_official_rejections(declarations, body, message, reference_message, tmp_path):
    original = source(declarations, body)
    with pytest.raises(GoDiagnostic, match=message):
        compile_go(original)
    receipt = compile_reference_rejection((('invalid.go', original),), tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'],
        diagnostics=reference_message)
    assert receipt['classification'] == 'normal_source_rejection'
    assert (tmp_path / 'reference/invalid.go').read_bytes() == original.encode()
