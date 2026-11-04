"""Struct tags are semantic identity, not storage or reflection support.

https://go.dev/ref/spec#Struct_types
https://go.dev/ref/spec#Type_identity
https://go.dev/ref/spec#Conversions
https://go.dev/ref/spec#String_literals
https://go.dev/ref/spec#Method_sets
Authored source, native and same-input reference obligations; CI-only UNRUN.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go, compile_go_packages
from pcc.frontends.go.generics import type_code
from pcc.frontends.go.interface_methods import method_set, require_implements
from pcc.frontends.go.managed_objects import type_identity
from pcc.frontends.go.packages import parse_package
from pcc.frontends.go.records import DefinedType, Field, StructType, underlying
from pcc.frontends.go.sema import Analyzer
from pcc.frontends.go.syntax import GoParser
from pcc.frontends.go.types import identical_ignoring_tags, same_underlying_ignoring_tags
from tests.go.owned_execution import (
    Execution, compile_module, compile_owned, compile_reference, compile_reference_packages,
    compile_reference_rejection, run, write_artifact,
)
from tests.go.test_execution import forbid_external_compiler


FIXTURE = Path(__file__).parent / 'fixtures/struct_tags.go'
SPELLINGS_FIXTURE = FIXTURE.parent / 'struct_tag_spellings.go'
INVALID_SPELLINGS = (
    'raw_cr_vs_interpreted.go', 'unicode_not_normalized.go',
    'non_utf8_vs_unicode.go', 'nul_vs_empty.go',
)
SPELLING_IDENTITIES = (
    ('AbsentA', 'EmptyA', b''), ('EmptyB', 'RawEmpty', b''),
    ('RawJSON', 'QuotedJSON', b'json:"x"'), ('RawCR', 'ElidedCR', b'ab'),
    ('HexByte', 'OctalByte', b'\xff'),
    ('UnicodeEscape', 'UTF8Bytes', b'\xc3\xa9'),
    ('UnicodeLiteral', 'LongUnicodeEscape', b'\xc3\xa9'),
    ('HexNUL', 'OctalNUL', b'\0'),
)


def source(declarations='', body=''):
    return 'package main;' + (declarations + ';' if declarations else '') + 'func main(){' + body + '}'


def analyze(original):
    owner = Analyzer(parse_package((('original.go', original),), require_main=True))
    owner.analyze()
    return owner


def units(fixture=FIXTURE):
    # Text-mode universal newline conversion would silently erase raw CRs
    # inside backquoted tags. Both compilers must receive these exact bytes.
    return ((fixture.name, fixture.read_bytes().decode('utf-8')),)


def observed(binary, directory, *, owner, originals):
    """Retain raw output and identity before any result-equality assertion."""
    directory.mkdir(parents=True, exist_ok=True)
    binary_sha256 = hashlib.sha256(binary.read_bytes()).hexdigest()
    identity = {
        'owner': owner, 'binary_sha256': binary_sha256,
        'sources': [{'path': name, 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}
                    for name, text in originals],
    }

    def retain(status, returncode, stdout, stderr, error=None):
        (directory / 'stdout').write_bytes(stdout)
        (directory / 'stderr').write_bytes(stderr)
        (directory / 'execution.json').write_text(json.dumps({
            **identity, 'status': status, 'returncode': returncode, 'error': error,
            'stdout_hex': stdout.hex(), 'stderr_hex': stderr.hex(),
            'stdout_sha256': hashlib.sha256(stdout).hexdigest(),
            'stderr_sha256': hashlib.sha256(stderr).hexdigest(),
        }, indent=2) + '\n')

    try:
        result = run(binary)
    except subprocess.TimeoutExpired as error:
        retain('timeout', None, error.stdout or b'', error.stderr or b'', str(error))
        raise
    except OSError as error:
        retain('spawn-failed', None, b'', b'', str(error))
        raise
    retain('completed', result.returncode, result.stdout, result.stderr)
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == binary_sha256
    return result


def package_originals(packages):
    return tuple((package + '/' + name, text) for package, items in packages.items()
                 for name, text in items)


def test_parser_keeps_one_tag_token_for_every_field_in_a_group():
    parsed = GoParser().parse(source('type T struct{A,B uint64 `pair`; C string "name"; D bool}'))
    record = parsed.types[0].type
    assert [token.text for token, _ in record.fields] == ['A', 'B', 'C', 'D']
    assert [token.text if token else None for token in record.tags] == ['`pair`', '`pair`', '"name"', None]
    assert record.tags[0] is record.tags[1]


@pytest.mark.parametrize('left,right,expected', [
    ('', '""', b''),
    ('""', '``', b''),
    ('`json:"x"`', '"json:\\"x\\""', b'json:"x"'),
    ('`a\rb`', '"ab"', b'ab'),
    ('"\\xff"', '"\\377"', b'\xff'),
    ('"\\u00e9"', '"\\xc3\\xa9"', b'\xc3\xa9'),
    ('`é`', '"\\U000000e9"', b'\xc3\xa9'),
    ('"\\x00"', '"\\000"', b'\0'),
])
def test_decoded_tag_bytes_define_identity_not_source_spelling(left, right, expected):
    owner = analyze(source('type A=struct{X uint64 ' + left + '};type B=struct{X uint64 ' + right + '}'))
    a, b = (owner.types[name].resolved for name in ('A', 'B'))
    assert a == b and a.fields[0].tag == b.fields[0].tag == expected
    assert type_code(a) == type_code(b)
    assert type_identity(a) == type_identity(b)


def test_raw_cr_removal_is_not_interpreted_cr_or_unicode_normalization():
    owner = analyze(source('type A=struct{X uint64 `a\rb`};type B=struct{X uint64 "a\\rb"};'
                           'type C=struct{X uint64 `é`};type D=struct{X uint64 `é`}'))
    a, b, c, d = (owner.types[name].resolved for name in ('A', 'B', 'C', 'D'))
    assert a.fields[0].tag == b'ab' and b.fields[0].tag == b'a\rb'
    assert a != b and c != d


def test_complete_spelling_original_preserves_literal_bytes_and_type_identity(monkeypatch):
    forbid_external_compiler(monkeypatch)
    original = units(SPELLINGS_FIXTURE)[0][1]
    assert original.encode('utf-8') == SPELLINGS_FIXTURE.read_bytes()
    assert b'`a\rb`' in SPELLINGS_FIXTURE.read_bytes()
    owner = analyze(original)
    for left, right, expected in SPELLING_IDENTITIES:
        a, b = (owner.types[name].resolved for name in (left, right))
        assert a == b and a.fields[0].tag == b.fields[0].tag == expected
    compile_go(original, SPELLINGS_FIXTURE.name)


@pytest.mark.parametrize('filename', INVALID_SPELLINGS)
def test_complete_distinct_spelling_originals_reject_assignment_before_ir(filename, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('different tag identity reached IR'))
    fixture = FIXTURE.parent / 'struct_tag_invalid' / filename
    original = units(fixture)[0][1]
    assert original.encode('utf-8') == fixture.read_bytes()
    if filename == 'raw_cr_vs_interpreted.go':
        assert b'`a\rb`' in fixture.read_bytes() and b'"a\\rb"' in fixture.read_bytes()
    with pytest.raises(GoDiagnostic, match='cannot use') as caught:
        compile_go(original, filename)
    assert not isinstance(caught.value, UnsupportedGo)


def test_tag_identity_reaches_generic_symbols_and_managed_compiler_plans():
    plain = StructType((Field('X', 'uint64'),))
    a = StructType((Field('X', 'uint64', tag=b'a'),))
    b = StructType((Field('X', 'uint64', tag=b'b'),))
    assert a != b and a != plain
    assert type_code(plain) == ['struct', [['X', '', ['scalar', 'uint64']]]]
    assert type_code(a) != type_code(b) != type_code(plain)
    assert type_identity(a) != type_identity(b) != type_identity(plain)
    assert identical_ignoring_tags(a, b)


def test_conversion_relation_preserves_nested_nominal_and_private_field_identity():
    left = DefinedType('N', 'left', 'uint64')
    right = DefinedType('N', 'right', 'uint64')
    a = StructType((Field('Value', left, tag=b'a'),))
    b = StructType((Field('Value', right, tag=b'b'),))
    assert not identical_ignoring_tags(a, b)
    assert not same_underlying_ignoring_tags(a, b)
    assert same_underlying_ignoring_tags(left, right)
    assert not identical_ignoring_tags(
        StructType((Field('hidden', 'uint64', 'left', tag=b'a'),)),
        StructType((Field('hidden', 'uint64', 'right', tag=b'b'),)))
    assert not identical_ignoring_tags(
        StructType((Field('N', left, embedded=False),)),
        StructType((Field('N', left, embedded=True),)))


@pytest.mark.parametrize('position', ['parameter', 'result'])
def test_method_signatures_keep_tag_identity_in_parameters_and_results(position):
    a, b = 'struct{X uint64 `a`}', 'struct{X uint64 `b`}'
    signature_a = 'M(' + a + ')' if position == 'parameter' else 'M()' + a
    signature_b = 'M(' + b + ')' if position == 'parameter' else 'M()' + b
    owner = analyze(source('type A interface{' + signature_a + '};type B interface{' + signature_b + '}'))
    one, two = (owner.types[name].resolved for name in ('A', 'B'))
    assert method_set(one).lookup('M').method.signature != method_set(two).lookup('M').method.signature
    with pytest.raises(GoDiagnostic, match='wrong type for method M'):
        require_implements(one, two, owner.program.token)


def test_complete_tagged_program_uses_owned_lowering_and_three_generic_identities(monkeypatch):
    forbid_external_compiler(monkeypatch)
    module = compile_go(units()[0][1], FIXTURE.name)
    instances = [name for name in module.globals if '_instance_' in name]
    assert len(instances) == 3 and len(set(instances)) == 3


def historical_originals():
    from tests.go.test_diagnostics import GRADUATED_STRUCT_TAG_CASE
    from tests.go.test_records import GRADUATED_STRUCT_TAG, source as record_source
    declarations, body, _ = GRADUATED_STRUCT_TAG
    return (GRADUATED_STRUCT_TAG_CASE[1], record_source(declarations, body))


def test_both_exact_former_diagnostic_originals_are_positive_obligations(monkeypatch):
    forbid_external_compiler(monkeypatch)
    originals = historical_originals()
    assert originals == (
        'package main;\ntype T struct{x int `tag`}\nfunc main(){}\n',
        'package main;type A struct{X int `json:"x"`};func main(){}',
    )
    for original in originals:
        compile_go(original, 'original.go')


INVALID = (
    (source(body='var a struct{X int `a`};var b struct{X int `b`};a=b;_=a'), 'cannot use', 'cannot use'),
    (source(body='var a struct{X int};var b struct{X int `a`};a=b;_=a'), 'cannot use', 'cannot use'),
    (source(body='var a struct{X int `a`};var b struct{X int `b`};_=a==b'), 'mismatched', 'mismatched types'),
    (source('type A int;type B int;type S struct{X A `a`};type T struct{X B `b`}', '_=T(S{})'),
     'invalid conversion', 'cannot convert'),
    (source('type S struct{X uint64 `a`};type T struct{X int64 `b`}', '_=T(S{})'),
     'invalid conversion', 'cannot convert'),
    (source('type S struct{X uint64 `a`};type T struct{Y uint64 `b`}', '_=T(S{})'),
     'invalid conversion', 'cannot convert'),
    (source('type S struct{X [2]int `a`};type T struct{X [3]int `b`}', '_=T(S{})'),
     'invalid conversion', 'cannot convert'),
    (source('type A struct{X int `a`};type B struct{X int `b`};type P *A;type Q *B',
            'var p P;_=Q(p)'), 'invalid conversion', 'cannot convert'),
    (source('func pair[T any](a,b T)T{return a}',
            '_=pair(struct{X int `a`}{},struct{X int `b`}{})'),
     'conflicting inferred', 'inferred type|cannot use'),
    (source('type A interface{M(struct{X int `a`})};type B interface{M(struct{X int `b`})};type C interface{A;B}'),
     'conflicting embedded interface method', 'duplicate method M'),
    (source('type A interface{M()struct{X int `a`}};type B interface{M()struct{X int `b`}};type C interface{A;B}'),
     'conflicting embedded interface method', 'duplicate method M'),
    (source('type T struct{X int "\\q"}'), 'invalid string escape', 'unknown escape'),
    (source('type T struct{X int "\\400"}'), 'overflows byte', 'escape'),
    (source('type T struct{X int "\\uD800"}'), 'invalid Unicode scalar', 'Unicode|unicode'),
)


@pytest.mark.parametrize('original,pcc_message,reference_message', INVALID)
def test_invalid_tag_identity_or_literal_is_rejected_before_lowering(original, pcc_message, reference_message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid source reached IR'))
    with pytest.raises(GoDiagnostic, match=pcc_message) as caught:
        compile_go(original, 'original.go')
    assert not isinstance(caught.value, UnsupportedGo)


FRONTIERS = (
    (source('type Base int;type T struct{Base `tag`}'), 'go.unsupported.embedded-fields'),
    (source('type T struct{_ int `tag`}'), 'go.unsupported.blank-struct-fields'),
    (source('type T struct{X *int `tag`}'), 'go.unsupported.pointer-storage'),
    (source('type T struct{X any `tag`}'), 'go.unsupported.interface-types'),
    (source('type A struct{X int `a`};type B struct{X int `b`}',
            'a:=A{};p:=&a;q:=(*B)(p);_=q'), 'go.unsupported.pointer-conversions'),
    (source('type P *struct{X int `a`};type Q *struct{X int `b`}',
            'var p P;_=Q(p)'), 'go.unsupported.pointer-conversions'),
)


@pytest.mark.parametrize('original,code', FRONTIERS)
def test_tags_do_not_open_unowned_storage_or_pointer_conversions(original, code, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported source reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original, 'original.go')
    assert caught.value.code == code


PACKAGES = {
    'left': (('types.go', 'package values;type T struct{X uint64 `left`};func Read(v T)uint64{return v.X}'),),
    'right': (('types.go', 'package values;type T struct{X uint64 `right`};func Read(v T)uint64{return v.X}'),),
    'main': (('main.go', 'package main;import a "left";import b "right";'
              'func main(){x:=a.T{X:0xfedcba9876543210};y:=b.T(x);'
              'if a.Read(x)!=b.Read(y){panic("package identity")}}'),),
}


def test_cross_package_tag_conversion_keeps_exported_and_private_field_identity(monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go_packages(PACKAGES)
    private = dict(PACKAGES)
    for path in ('left', 'right'):
        private[path] = (('types.go', 'package same;type T struct{hidden uint64 `' + path + '`}'),)
    private['main'] = (('main.go', 'package main;import a "left";import b "right";func main(){_=b.T(a.T{})}'),)
    with pytest.raises(GoDiagnostic, match='invalid conversion') as caught:
        compile_go_packages(private)
    assert not isinstance(caught.value, UnsupportedGo)


OBSERVERS = (
    ('AliasIdentity', 49), ('CopiesAndMethods', 0xfedcba9876543210),
    ('NestedConversion', 0x123456789abcdef0), ('PackageConversion', 0xfedcba9876543210),
    ('GenericInstances', 1234), ('ConversionEffects', 38), ('CompareAfterConversion', 73),
)


@pytest.mark.integration
@pytest.mark.parametrize('entry,expected', OBSERVERS)
def test_tagged_record_helpers_execute_full_width_native_values(entry, expected, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(units(), observe_entry=(entry, ()))
    binary = tmp_path / 'tagged-owned'
    write_artifact(artifact, binary)
    result = observed(binary, tmp_path / 'native-observation', owner=artifact.owner, originals=units())
    raw = expected.to_bytes(8, 'little')
    (tmp_path / 'observation.json').write_text(json.dumps({
        'owner': artifact.owner, 'function': entry, 'expected_hex': raw.hex(),
        'returncode': result.returncode, 'stdout_hex': result.stdout.hex(), 'stderr_hex': result.stderr.hex(),
        'source_sha256': hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        'executable_sha256': hashlib.sha256(artifact.executable).hexdigest(),
    }, indent=2) + '\n')
    assert result == Execution(0, raw, b'')


@pytest.mark.integration
def test_complete_and_exact_former_originals_execute_owned_native(tmp_path, monkeypatch):
    originals = (units(), units(SPELLINGS_FIXTURE)) + tuple((('original.go', item),) for item in historical_originals())
    for index, original in enumerate(originals):
        with monkeypatch.context() as denied:
            forbid_external_compiler(denied)
            artifact = compile_owned(original)
        binary = tmp_path / ('original-' + str(index))
        write_artifact(artifact, binary)
        assert observed(binary, tmp_path / ('observation-' + str(index)),
                        owner=artifact.owner, originals=original) == Execution(0, b'', b'')


@pytest.mark.integration
def test_complete_literal_spelling_original_executes_full_width_native_value(tmp_path, monkeypatch):
    original = units(SPELLINGS_FIXTURE)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(original, observe_entry=('Result', ()))
    binary = tmp_path / 'spellings-owned'
    write_artifact(artifact, binary)
    assert observed(binary, tmp_path / 'native-observation', owner=artifact.owner,
                    originals=original) == Execution(0, (255).to_bytes(8, 'little'), b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_complete_and_former_originals_match_official_raw_execution(tmp_path, monkeypatch):
    originals = (units(), units(SPELLINGS_FIXTURE)) + tuple((('original.go', item),) for item in historical_originals())
    for index, original in enumerate(originals):
        with monkeypatch.context() as denied:
            forbid_external_compiler(denied)
            artifact = compile_owned(original)
        binary = tmp_path / ('original-' + str(index))
        write_artifact(artifact, binary)
        reference = compile_reference(original, tmp_path / ('reference-' + str(index)),
            compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
        for name, text in original:
            assert (reference.parent / name).read_bytes() == text.encode()
        owned_result = observed(binary, tmp_path / ('pcc-observation-' + str(index)),
                                owner=artifact.owner, originals=original)
        reference_result = observed(reference, tmp_path / ('go-observation-' + str(index)),
                                    owner='official-go', originals=original)
        assert owned_result == reference_result == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_package_graph_matches_official_raw_execution(tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_module(compile_go_packages(PACKAGES))
    binary = tmp_path / 'packages-owned'
    write_artifact(artifact, binary)
    reference = compile_reference_packages(PACKAGES, tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    for package, originals in PACKAGES.items():
        for name, text in originals:
            assert (reference.parent / 'gopath/src' / package / name).read_bytes() == text.encode()
    originals = package_originals(PACKAGES)
    owned_result = observed(binary, tmp_path / 'pcc-observation', owner=artifact.owner, originals=originals)
    reference_result = observed(reference, tmp_path / 'go-observation', owner='official-go', originals=originals)
    assert owned_result == reference_result == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('filename', INVALID_SPELLINGS)
def test_identical_distinct_tag_spellings_have_attributed_official_rejections(filename, tmp_path):
    fixture = FIXTURE.parent / 'struct_tag_invalid' / filename
    originals = units(fixture)
    with pytest.raises(GoDiagnostic, match='cannot use') as caught:
        compile_go(originals[0][1], filename)
    assert not isinstance(caught.value, UnsupportedGo)
    compile_reference_rejection(originals, tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'],
        diagnostics='cannot use')
    assert (tmp_path / 'reference' / filename).read_bytes() == fixture.read_bytes()
    assert not (tmp_path / 'reference/oracle').exists()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original,pcc_message,reference_message', INVALID)
def test_identical_invalid_originals_have_attributed_official_rejections(original, pcc_message, reference_message, tmp_path):
    with pytest.raises(GoDiagnostic, match=pcc_message) as caught:
        compile_go(original, 'original.go')
    assert not isinstance(caught.value, UnsupportedGo)
    compile_reference_rejection((('original.go', original),), tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'],
        diagnostics=reference_message)
    assert (tmp_path / 'reference/original.go').read_bytes() == original.encode()
    assert not (tmp_path / 'reference/oracle').exists()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original,code', FRONTIERS)
def test_valid_storage_frontiers_have_official_reference_only_execution(original, code, tmp_path):
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original, 'original.go')
    assert caught.value.code == code
    reference = compile_reference((('original.go', original),), tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert (reference.parent / 'original.go').read_bytes() == original.encode()
    assert observed(reference, tmp_path / 'go-observation', owner='official-go-reference-only',
                    originals=(('original.go', original),)) == Execution(0, b'', b'')
