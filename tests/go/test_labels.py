"""Go label/goto obligations. Authored, never executed in the CI-only batch.

The full licensed upstream test/ken/label.go is an unchanged input, not a
selected body or a replacement for the math/bits package and its own tests.
https://go.dev/ref/spec#Label_scopes
https://go.dev/ref/spec#Goto_statements
https://go.dev/ref/spec#Terminating_statements
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from pcc.frontends.go import GO_MAIN_SYMBOL, GoDiagnostic, UnsupportedGo, compile_go
from pcc.frontends.go.branches import walk
from pcc.frontends.go.packages import parse_package
from pcc.frontends.go.pointer_flow import Graph
from pcc.frontends.go.sema import Analyzer
from pcc.frontends.go.syntax import GoParser
from tests.go.owned_execution import (
    Execution, compile_owned, compile_reference, compile_reference_rejection, run, write_artifact,
)
from tests.go.test_execution import forbid_external_compiler


ROOT = Path(__file__).parent
FIXTURE = ROOT / 'fixtures/labels.go'
UPSTREAM_ROOT = ROOT / 'upstream/go1.27.3'
UPSTREAM = UPSTREAM_ROOT / 'test/ken/label.go'
UPSTREAM_SHA256 = '60642b4daaae82b23dc7c9b68fef1de0caa16801e1fcb7e1fc03716edc373228'
UPSTREAM_BLOB = 'fcb3e611d9d6a62ab3f9bb5fed3d7da035c4b3ca'
ENTRIES = (
    ('Forward', 7), ('Backward', 5), ('AfterReturn', (1 << 56) + 37),
    ('OuterBreak', 2), ('OuterContinue', 4), ('SwitchBreak', 17),
    ('ContinueRange', 10), ('ContinueString', 298), ('FallthroughLabel', 7),
    ('BlockExit', 4), ('Names', 4), ('GenericResult', (1 << 50) + 9),
    ('CaseLocalGoto', 3),
)


def source(body, declarations=''):
    prefix = 'package main;' + (declarations + ';' if declarations else '')
    return prefix + 'func main(){' + body + '}'


def units(path):
    return ((path.name, path.read_bytes().decode('utf-8')),)


def analyze(original):
    owner = Analyzer(parse_package((('original.go', original),), require_main=True))
    owner.analyze()
    return owner


def observed(binary, directory, *, owner, originals):
    """Retain both streams, status and binary/source identities before assert."""
    directory.mkdir(parents=True, exist_ok=True)
    identity = {
        'owner': owner, 'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'sources': {name: hashlib.sha256(text.encode('utf-8')).hexdigest() for name, text in originals},
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
    assert identity['binary_sha256'] == hashlib.sha256(binary.read_bytes()).hexdigest()
    return result


def test_complete_official_label_original_and_license_keep_pinned_bytes():
    original = UPSTREAM.read_bytes()
    provenance = json.loads((ROOT / 'upstream/provenance.json').read_text())
    assert provenance['commit'] == '2aae1000ea1d4b28923b96db28acf4e3b99d6aff'
    assert provenance['license'] == 'BSD-3-Clause'
    assert hashlib.sha256(original).hexdigest() == UPSTREAM_SHA256
    assert hashlib.sha1(b'blob ' + str(len(original)).encode() + b'\0' + original).hexdigest() == UPSTREAM_BLOB
    assert provenance['files']['test/ken/label.go'] == UPSTREAM_SHA256
    assert provenance['git_blobs']['test/ken/label.go'] == UPSTREAM_BLOB
    assert hashlib.sha256((UPSTREAM_ROOT / 'LICENSE').read_bytes()).hexdigest() == (
        '911f8f5782931320f5b8d1160a76365b83aea6447ee6c04fa6d5591467db9dad')
    assert units(UPSTREAM)[0][1].encode('utf-8') == original


def test_owned_parser_keeps_labels_after_return_and_explicit_branch_names():
    tree = GoParser().parse(units(UPSTREAM)[0][1], UPSTREAM.name)
    statements = list(walk(tree.functions[0].body))
    assert [stmt.label.text for stmt in statements if stmt.kind == 'label'] == ['loop', 'gogoloop']
    assert sum(stmt.kind == 'goto' for stmt in statements) == 6
    assert tree.functions[0].body[-1].kind == 'label'


@pytest.mark.parametrize('path', [UPSTREAM, FIXTURE])
def test_complete_originals_build_owned_ir_without_external_compiler(path, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(units(path)[0][1], path.name)


def test_goto_cfg_targets_are_real_edges_even_when_forward_and_after_return():
    owner = analyze(units(UPSTREAM)[0][1])
    function = owner.program.functions[0]
    graph = Graph(function)
    labels = [stmt for stmt in walk(function.body) if stmt.kind == 'label']
    assert set(graph.labels) == {id(stmt) for stmt in labels}
    for label in labels:
        anchor = graph.labels[id(label)]
        assert graph.nodes[anchor].kind == 'jump' and len(graph.nodes[anchor].edges) == 1
        assert any(anchor in node.edges for node in graph.nodes)


def test_labeled_branch_targets_preserve_enclosing_identity_not_innermost_loop():
    owner = analyze(units(FIXTURE)[0][1])
    for function in owner.program.functions:
        for statement in walk(function.body):
            if statement.kind in ('break', 'continue'):
                assert statement.control_target.kind in ('for', 'range', 'switch')
                if statement.label is not None:
                    label = next(item for item in walk(function.body)
                                 if item.kind == 'label' and item.label.text == statement.label.text)
                    assert statement.control_target is label.body[0]


VALID = (
    source('goto End; End:'),
    source('goto Start; Start: goto End; End:;'),
    source('x:=1;{x:=2;_=x;goto End};End:_=x'),
    source('{goto End; x:=1;_=x};End:'),
    source('goto End;const x=1;End:_=x'),
    source('goto L;var();L:'),  # Exact former INVALID input; an empty group declares no variable.
    source('goto V;V:var x=1;_=x'),
    source('x:=0;Next:x++;if x<2{goto Next}'),
    source('_:;_:;'),
    source('goto true;true:return'),
    source('f()', 'func f()int{goto End;End:return 1}'),
    source('Outer:for{for{break};break Outer}'),
    source('Switch:switch{default:for{break Switch}}'),
    source('goto 最後;最後:return'),
)

DEAD_INIT_ORIGINALS = (
    'package main;func main(){return;if panic("unreachable");true{L:goto L}}',
    'package main;func main(){return;for panic("unreachable");true;{L:goto L}}',
    'package main;func main(){return;switch panic("unreachable");{default:L:goto L}}',
)
VALID += DEAD_INIT_ORIGINALS


@pytest.mark.parametrize('original', DEAD_INIT_ORIGINALS)
def test_terminal_control_initializers_keep_dead_body_labels_and_all_terminators(original, monkeypatch):
    forbid_external_compiler(monkeypatch)
    module = compile_go(original, 'original.go')
    main = module.globals[GO_MAIN_SYMBOL]
    labels = [block for block in main.blocks if block.name.startswith('source_label')]
    assert len(labels) == 1
    assert all(block.is_terminated for block in main.blocks)


@pytest.mark.parametrize('original', VALID)
def test_valid_label_scope_and_termination_originals_compile(original, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(original, 'original.go')


INVALID = (
    (source('goto Missing'), 'undefined label', r'label Missing not defined|label Missing not declared'),
    (source('goto _'), 'undefined label', r'label _ not defined|label _ not declared'),
    (source('Unused:return'), 'not used', r'label Unused .*not used'),
    (source('goto L;L:;L:'), 'already defined', r'label L already defined|label L already declared'),
    (source('goto L;{L:return}'), 'jumps into a block', r'goto L jumps into block'),
    (source('{goto L};{L:return}'), 'jumps into a block', r'goto L jumps into block'),
    (source('goto L;x:=1;L:_=x'), 'jumps over declaration', r'goto L jumps over declaration of x|goto L jumps over variable declaration'),
    (source('goto L;var _ int;L:'), 'jumps over variable declaration', r'goto L jumps over declaration|goto L jumps over variable declaration'),
    (source('Outer:for{break Outer};continue Outer'), 'invalid continue label', r'invalid continue label Outer'),
    (source('Block:{break Block}'), 'invalid break label', r'invalid break label Block'),
    (source('Switch:switch{default:continue Switch}'), 'invalid continue label', r'invalid continue label Switch'),
    (source('A:B:for{break A;break B}'), 'invalid break label', r'invalid break label A'),
    (source('f()', 'func f()int{goto End;End:}'), 'missing return', r'missing return'),
    (source('f()', 'func f()int{Outer:for{for{break Outer}}}'), 'missing return', r'missing return'),
    (source('goto L', 'func f(){L:return}'), 'not used|undefined label', r'label L .*not used|label L not defined|label L not declared'),
)


def retained_switch_original():
    from tests.go.test_records import source as original_source
    from tests.go.test_switches import INVALID as switch_invalid
    body, declarations, historical_diagnostic = switch_invalid[25]
    assert historical_diagnostic == 'semicolon or newline'
    return original_source(declarations, body)


# Its source bytes and historical tuple stay intact. Parsing the label operand
# now exposes the real missing-label semantic error, still invalid official Go.
INVALID += ((retained_switch_original(), 'undefined label',
             r'break label not defined: Outer|invalid break label Outer|label Outer not defined'),)


@pytest.mark.parametrize('original,pcc_diagnostic,reference_diagnostic', INVALID)
def test_invalid_label_originals_fail_before_ir(original, pcc_diagnostic, reference_diagnostic, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid label reached IR'))
    with pytest.raises(GoDiagnostic, match=pcc_diagnostic) as caught:
        compile_go(original, 'original.go')
    assert not isinstance(caught.value, UnsupportedGo)


POINTER_FRONTIERS = (
    (source('var p *int;goto Use;Use:_=*p'), 'go.unsupported.pointer-nil'),
    (source('x:=1;var p *int=&x;Again:if p==nil{goto Use};p=nil;goto Again;Use:_=*p'),
     'go.unsupported.pointer-nil'),
    (source('n:=0;var p *int;Again:x:=n;p=&x;_=p;n++;if n<2{goto Again}'),
     'go.unsupported.pointer-iteration-identity'),
    (source('x:=1;var p *int=&x;Outer:for i:=0;i<1;i++{p=nil;for{continue Outer}};_=*p'),
     'go.unsupported.pointer-nil'),
    (source('x:=1;var p *int=&x;Outer:for{p=nil;for{break Outer}};_=*p'),
     'go.unsupported.pointer-nil'),
)


@pytest.mark.parametrize('original,code', POINTER_FRONTIERS)
def test_goto_never_invents_pointer_nonnil_or_reentered_storage_proofs(original, code, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('pointer frontier reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original, 'original.go')
    assert caught.value.code == code


POINTER_VALID = (
    source('x:=3;var p *int=&x;goto Read;Read:if p!=nil{_=*p}'),
    source('x:=3;var p *int=&x;n:=0;Again:if p!=nil{_=*p};n++;if n<2{goto Again}'),
)


def test_source_helper_and_authored_originals_keep_pinned_bytes():
    assert source('goto L;var();L:') == 'package main;func main(){goto L;var();L:}'
    assert source('f()', 'func f(){}') == 'package main;func f(){};func main(){f()}'
    data = (ROOT / 'fixtures/labels_originals.json').read_bytes()
    assert hashlib.sha256(data).hexdigest() == 'a5fcec63ec8a470df2abfed2701c763f36bdc5e1c285be06ba6839800d7cbfe7'
    pinned = json.loads(data.decode('utf-8'))
    originals = {
        'valid': VALID, 'invalid': tuple(row[0] for row in INVALID),
        'pointer_frontiers': tuple(row[0] for row in POINTER_FRONTIERS),
        'pointer_valid': POINTER_VALID,
    }
    assert set(pinned) == set(originals)
    for group, texts in originals.items():
        assert list(texts) == [row['source'] for row in pinned[group]]
        for text, row in zip(texts, pinned[group]):
            assert hashlib.sha256(text.encode('utf-8')).hexdigest() == row['sha256']


@pytest.mark.parametrize('original', POINTER_VALID)
def test_goto_preserves_a_real_dominating_nonnil_proof(original, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(original, 'original.go')


@pytest.mark.integration
@pytest.mark.parametrize('entry,expected', ENTRIES)
def test_complete_label_fixture_executes_full_width_owned_results(entry, expected, tmp_path, monkeypatch):
    original = units(FIXTURE)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(original, observe_entry=(entry, ()))
    binary = tmp_path / 'owned'
    write_artifact(artifact, binary)
    result = observed(binary, tmp_path / 'observation', owner=artifact.owner, originals=original)
    assert result == Execution(0, expected.to_bytes(8, 'little'), b'')


@pytest.mark.integration
@pytest.mark.parametrize('path', [UPSTREAM, FIXTURE])
def test_complete_originals_execute_owned_main(path, tmp_path, monkeypatch):
    original = units(path)
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
def test_identical_complete_and_scope_originals_match_official_raw_execution(tmp_path, monkeypatch):
    originals = (units(UPSTREAM), units(FIXTURE)) + tuple(
        (('original.go', item),) for item in (*VALID, *POINTER_VALID))
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
        owned_result = observed(binary, tmp_path / ('pcc-observation-' + str(index)),
                                owner=artifact.owner, originals=original)
        reference_result = observed(reference, tmp_path / ('go-observation-' + str(index)),
                                    owner='official-go', originals=original)
        assert owned_result == reference_result == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original,pcc_diagnostic,reference_diagnostic', INVALID)
def test_identical_invalid_label_originals_have_attributed_official_rejections(
        original, pcc_diagnostic, reference_diagnostic, tmp_path):
    with pytest.raises(GoDiagnostic, match=pcc_diagnostic) as caught:
        compile_go(original, 'original.go')
    assert not isinstance(caught.value, UnsupportedGo)
    compile_reference_rejection((('original.go', original),), tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'],
        diagnostics=reference_diagnostic)


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_reentered_address_original_is_reference_only_not_pcc_admission(tmp_path):
    original, code = next((original, code) for original, code in POINTER_FRONTIERS
                          if code == 'go.unsupported.pointer-iteration-identity')
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original, 'original.go')
    assert caught.value.code == code
    originals = (('original.go', original),)
    reference = compile_reference(originals, tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert (reference.parent / 'original.go').read_bytes() == original.encode('utf-8')
    assert observed(reference, tmp_path / 'go-observation', owner='official-go',
                    originals=originals) == Execution(0, b'', b'')
