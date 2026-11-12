"""Exact rune values and default types; all new execution obligations UNRUN.

https://go.dev/ref/spec#Rune_literals
https://go.dev/ref/spec#Constant_expressions
https://go.dev/ref/spec#Type_inference
The complete upstream inputs retain their own dependencies/errors. They are
not rewritten into an admitted package or counted as project acceptance.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.packages import parse_package
from pcc.frontends.go.runes import literal_value
from pcc.frontends.go.sema import Analyzer
from pcc.frontends.go.syntax import GoParser, scan
from tests.go.owned_execution import (
    Execution, compile_owned, compile_reference, compile_reference_rejection, write_artifact,
)
from tests.go.test_execution import forbid_external_compiler
from tests.go.test_labels import observed


ROOT = Path(__file__).parent
FIXTURE = ROOT / 'fixtures/runes.go'
UPSTREAM = ROOT / 'upstream/go1.27.3'
ORIGINALS = {
    'test/char_lit.go': ('836c3c1a2d0b0de309abe4b8a6ffa0aabc85668a',
                       'a564a0a6031ae143fafa3b03b85214d69354cc5b7bb63baff20335a981dde053'),
    'test/char_lit1.go': ('8899aff83aba34a42e887f0faa48e2630fbfd686',
                        'da0e5854c860c2081cd0da3ddd3c2b59f9547daacd00a7bbd668a987d6246100'),
}
ENTRIES = (
    ('DefaultRune', 30028, 4), ('MixedKinds', 196, 4), ('ShiftContexts', 65 << 33, 8),
    ('LeftShiftKind', 16, 8), ('WideConstant', (65 << 40) + 3, 8), ('RuneRange', 6, 4),
    ('ExistingRange', 6, 1), ('GenericKinds', 132, 4), ('RuneString', 255, 8),
    ('CaseRune', 2, 8), ('ArrayRunes', 26732, 8),
)


def units(path):
    # Raw CR inside a rune is a value. Do not apply universal-newline folding.
    return ((path.name, path.read_bytes().decode('utf-8')),)


def source(body='', declarations=''):
    prefix = 'package main;' + (declarations + ';' if declarations else '')
    return prefix + 'func main(){' + body + '}'


SPELLINGS = (
    ("'a'", 97), ("'ä'", 228), ("'本'", 26412), ("'😀'", 128512),
    (r"'\a'", 7), (r"'\b'", 8), (r"'\f'", 12), (r"'\n'", 10),
    (r"'\r'", 13), (r"'\t'", 9), (r"'\v'", 11), (r"'\\'", 92),
    (r"'\''", 39), ("'\"'", 34), (r"'\000'", 0), (r"'\123'", 83),
    (r"'\377'", 255), (r"'\xff'", 255), (r"'\u00FF'", 255),
    (r"'\uD7FF'", 0xd7ff), (r"'\uE000'", 0xe000),
    (r"'\U0010FFFF'", 0x10ffff), ("'\r'", 13),
)
SPELLING_ORIGINALS = tuple(source('var x uint64=' + spelling + ';if x!=' + str(value) + '{for{}}')
                           for spelling, value in SPELLINGS)


@pytest.mark.parametrize('spelling,expected', SPELLINGS)
def test_rune_spelling_decodes_one_codepoint_not_string_bytes(spelling, expected):
    token = scan(spelling)[0]
    assert token.kind == 'rune'
    assert literal_value(token) == expected


def test_default_types_keep_rune_kind_and_shift_left_kind():
    original = source(declarations="var r='a';var mixed=1+'a';var reversed='a'+1;"
        "var left='a'<<1;var right=1<<'\\x01';const large='A'<<40")
    owner = Analyzer(parse_package((('original.go', original),), require_main=True))
    owner.analyze()
    assert {name: owner.globals[name].type for name in ('r', 'mixed', 'reversed', 'left', 'right')} == {
        'r': 'int32', 'mixed': 'int32', 'reversed': 'int32', 'left': 'int32', 'right': 'int'}
    assert owner.globals['large'].type == 'untyped rune'
    assert owner.globals['large'].constant == 65 << 40


@pytest.mark.parametrize('original', SPELLING_ORIGINALS)
def test_complete_spelling_originals_build_owned_ir(original, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(original, 'original.go')


def test_complete_rune_fixture_uses_owned_ir_and_one_inferred_rune_instance(monkeypatch):
    forbid_external_compiler(monkeypatch)
    module = compile_go(units(FIXTURE)[0][1], FIXTURE.name)
    assert len([name for name in module.globals if '_instance_' in name]) == 1


@pytest.mark.parametrize('path,identity', ORIGINALS.items())
def test_full_official_inputs_and_license_keep_original_identities(path, identity):
    data = (UPSTREAM / path).read_bytes()
    provenance = json.loads((ROOT / 'upstream/provenance.json').read_text())
    assert provenance['commit'] == '2aae1000ea1d4b28923b96db28acf4e3b99d6aff'
    assert provenance['license'] == 'BSD-3-Clause'
    assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == identity[0]
    assert hashlib.sha256(data).hexdigest() == identity[1] == provenance['files'][path]
    assert provenance['git_blobs'][path] == identity[0]
    assert hashlib.sha256((UPSTREAM / 'LICENSE').read_bytes()).hexdigest() == (
        '911f8f5782931320f5b8d1160a76365b83aea6447ee6c04fa6d5591467db9dad')


def test_complete_valid_official_input_parses_but_keeps_missing_os_dependency(monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    original = units(UPSTREAM / 'test/char_lit.go')[0][1]
    tree = GoParser().parse(original, 'char_lit.go')
    assert tree.imports[0].path == 'os'
    assert all(0 <= literal_value(token) <= 0x10ffff for token in scan(original) if token.kind == 'rune')
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('missing os source reached IR'))
    with pytest.raises(GoDiagnostic, match='import source unavailable: os'):
        compile_go(original, 'char_lit.go')


def test_complete_invalid_official_input_rejects_unicode_before_ir(monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid Unicode reached IR'))
    with pytest.raises(GoDiagnostic, match='Unicode') as caught:
        compile_go(units(UPSTREAM / 'test/char_lit1.go')[0][1], 'char_lit1.go')
    assert not isinstance(caught.value, UnsupportedGo)


INVALID_LITERALS = (
    ("''", 'empty rune', r'empty rune literal|invalid character literal'),
    ("'ab'", 'exactly one', r'more than one character|invalid character literal'),
    ("'é'", 'exactly one', r'more than one character|invalid character literal'),
    (r"'\q'", 'invalid rune escape', r'unknown escape'),
    (r"'\"'", 'invalid rune escape', r'unknown escape'),
    (r"'\0'", 'escape digits', r'invalid character.*escape|invalid.*escape'),
    (r"'\x0'", 'escape digits', r'invalid character.*escape|invalid.*escape'),
    (r"'\xgg'", 'escape digits', r'invalid character.*escape|invalid.*escape'),
    (r"'\400'", 'overflows byte', r'octal escape.*255|octal escape overflows'),
    (r"'\x001'", 'exactly one', r'more than one character|invalid character literal'),
    (r"'\uD800'", 'Unicode', r'Unicode|unicode'),
    (r"'\uDFFF'", 'Unicode', r'Unicode|unicode'),
    (r"'\U00110000'", 'Unicode', r'Unicode|unicode'),
    (r"'\UFFFFFFFF'", 'Unicode', r'Unicode|unicode'),
    ("'\n'", 'newline', r'newline in rune literal|newline in character literal'),
    ("'\0'", 'NUL', r'NUL'),
)
INVALID = tuple((source('_=' + literal), message, reference)
                for literal, message, reference in INVALID_LITERALS) + (
    # Go 1.27.3 names the rune type and reports "... as rune value in ... (overflows)".
    (source("r:='a';var i int=r;_=i"), 'cannot use int32 as int', r'cannot use.*r.*(?:int32|rune).*as int'),
    (source("r:='A'<<40;_=r"), 'overflows int32', r'overflows rune|overflows int32|as rune value in .*\(overflows\)'),
    (source("_=uint8('界')"), 'overflows uint8', r'overflows uint8|overflows byte'),
    (source("_='a'<<-'a'"), 'negative shift count', r'negative shift count'),
    (source("_=f('a',true)", 'func f[T any](a,b T)T{return a}'),
     'cannot infer type argument', r'mismatched types|cannot infer'),
    (source("_=f('a',1<<40)", 'func f[T any](a,b T)T{return a}'),
     'overflows int32', r'overflows rune|overflows int32|as rune value in .*\(overflows\)'),
)


def test_source_helper_and_authored_originals_keep_pinned_bytes():
    assert source("_='a'") == "package main;func main(){_='a'}"
    assert source('', "var r='a'") == "package main;var r='a';func main(){}"
    assert source("_='\r'").encode('utf-8') == b"package main;func main(){_='\r'}"
    data = (ROOT / 'fixtures/rune_originals.json').read_bytes()
    assert hashlib.sha256(data).hexdigest() == '683a4d2e1bbc79a5d483630b71e35393e88b246962945de1c6951f1a5941699e'
    pinned = json.loads(data.decode('utf-8'))
    originals = {'spelling': SPELLING_ORIGINALS, 'invalid': tuple(row[0] for row in INVALID)}
    assert set(pinned) == set(originals)
    for group, texts in originals.items():
        assert list(texts) == [row['source'] for row in pinned[group]]
        for text, row in zip(texts, pinned[group]):
            assert hashlib.sha256(text.encode('utf-8')).hexdigest() == row['sha256']


@pytest.mark.parametrize('original,message,reference', INVALID)
def test_invalid_rune_originals_fail_before_ir(original, message, reference, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid rune reached IR'))
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(original, 'original.go')
    assert not isinstance(caught.value, UnsupportedGo)


def test_dynamic_rune_string_conversion_does_not_open_allocation(monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('dynamic allocation reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(source("r:='a';_=string(r)"), 'original.go')
    assert caught.value.code == 'go.unsupported.dynamic-string-conversion'


@pytest.mark.integration
@pytest.mark.parametrize('entry,expected,byte_width', ENTRIES)
def test_complete_rune_fixture_executes_full_width_native_results(entry, expected, byte_width, tmp_path, monkeypatch):
    original = units(FIXTURE)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(original, observe_entry=(entry, ()))
    binary = tmp_path / 'owned'
    write_artifact(artifact, binary)
    (tmp_path / 'observer.json').write_text(json.dumps({
        'entry': entry, 'bytes': byte_width, 'expected_hex': expected.to_bytes(byte_width, 'little').hex(),
    }, indent=2) + '\n')
    assert observed(binary, tmp_path / 'observation', owner=artifact.owner,
                    originals=original) == Execution(0, expected.to_bytes(byte_width, 'little'), b'')


@pytest.mark.integration
def test_complete_rune_fixture_executes_owned_main(tmp_path, monkeypatch):
    original = units(FIXTURE)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(original)
    binary = tmp_path / 'owned'
    write_artifact(artifact, binary)
    assert observed(binary, tmp_path / 'observation', owner=artifact.owner,
                    originals=original) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_complete_rune_originals_match_official_raw_execution(tmp_path, monkeypatch):
    originals = (units(FIXTURE),) + tuple((('original.go', text),) for text in SPELLING_ORIGINALS)
    for index, original in enumerate(originals):
        with monkeypatch.context() as denied:
            forbid_external_compiler(denied)
            artifact = compile_owned(original)
        binary = tmp_path / ('owned-' + str(index))
        write_artifact(artifact, binary)
        reference = compile_reference(original, tmp_path / ('reference-' + str(index)),
            compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
        for name, text in original:
            assert (reference.parent / name).read_bytes() == text.encode('utf-8')
        actual = observed(binary, tmp_path / ('pcc-observation-' + str(index)),
                          owner=artifact.owner, originals=original)
        expected = observed(reference, tmp_path / ('go-observation-' + str(index)),
                            owner='official-go', originals=original)
        assert actual == expected == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original,message,reference', INVALID)
def test_identical_invalid_rune_originals_have_attributed_official_rejections(original, message, reference, tmp_path):
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go(original, 'original.go')
    assert not isinstance(caught.value, UnsupportedGo)
    compile_reference_rejection((('original.go', original),), tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'],
        diagnostics=reference)


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_complete_official_character_literals_remain_reference_only_without_os_sources(tmp_path):
    original = units(UPSTREAM / 'test/char_lit.go')
    with pytest.raises(GoDiagnostic, match='import source unavailable: os'):
        compile_go(original[0][1], original[0][0])
    reference = compile_reference(original, tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert (reference.parent / original[0][0]).read_bytes() == original[0][1].encode('utf-8')
    assert observed(reference, tmp_path / 'go-observation', owner='official-go',
                    originals=original) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_complete_official_invalid_unicode_has_same_input_rejection(tmp_path):
    original = units(UPSTREAM / 'test/char_lit1.go')
    with pytest.raises(GoDiagnostic, match='Unicode') as caught:
        compile_go(original[0][1], original[0][0])
    assert not isinstance(caught.value, UnsupportedGo)
    compile_reference_rejection(original, tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'],
        diagnostics=r'Unicode|unicode')
