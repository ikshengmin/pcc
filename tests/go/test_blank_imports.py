"""Static blank-import graphs and the //go:linkname ownership frontier.

All execution is UNRUN. Full normal math/bits files are retained, not replaced
with bootstrap errors or a fake unsafe/runtime package. Go import scope:
https://go.dev/ref/spec#Import_declarations
https://go.dev/ref/spec#Package_initialization
Linkname recognition follows the pinned Go 1.27.3 compiler, not a C ABI alias.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from pcc.frontends.go import GoDiagnostic, UnsupportedGo, compile_go, compile_go_packages, function_symbol
from tests.go.owned_execution import (
    Execution, compile_module, compile_owned, compile_reference, compile_reference_packages,
    compile_reference_packages_rejection, compile_reference_rejection, write_artifact,
)
from tests.go.test_diagnostics import GRADUATED_STATIC_BLANK_IMPORT as DIAGNOSTIC_ORIGINAL
from tests.go.test_execution import forbid_external_compiler
from tests.go.test_labels import observed
from tests.go.test_packages import GRADUATED_STATIC_BLANK_IMPORT as PACKAGE_ORIGINAL


ROOT = Path(__file__).parent
FIXTURE = ROOT / 'fixtures/blank_imports.json'
FIXTURE_SHA256 = '9b382ab6616faeb3487944b60772d05e17670fa5edd9ca356eb9de1f181f8481'
CORPUS = json.loads(FIXTURE.read_bytes().decode('utf-8'))
PLACEMENT_FIXTURE = ROOT / 'fixtures/directive_placement.json'
PLACEMENT_SHA256 = '315fbfcc2452c7b8d31abafbb4d2c0285559cf47eee9e8bc98de9f38e110c044'
PLACEMENT = json.loads(PLACEMENT_FIXTURE.read_bytes().decode('utf-8'))
UPSTREAM = ROOT / 'upstream/go1.27.3/src/math/bits'
NORMAL_SHA256 = {
    'bits.go': '694e0114bda396589e8c993a3d7880a0b461da23ee64c272929a3a5b69c2acf2',
    'bits_errors.go': '1341283973609b16798176456337e3a0cbff9bfb32f79673a45197223471d7c6',
    'bits_tables.go': 'c4cb4a05dab6d395d9d51720893a42f0a9d1cdf8a0c2383d87268e49acb73f7f',
}
LINKNAME = 'package main\nimport _ "unsafe"\n//go:linkname X\nvar X int\nfunc main(){}\n'
INERT = (
    'package main\n// go:linkname X\nfunc main(){}',
    'package main\n//go:linknamed X\nfunc main(){}',
    'package main\n//go:linkname\nfunc main(){}',
    'package main\n//go:linkname\tX\nfunc main(){}',
    'package main\n/* //go:linkname X */\nfunc main(){}',
    'package main\nconst X=`//go:linkname X`\nfunc main(){_=X}',
) + tuple(row['source'] for row in PLACEMENT['inert'])
BAD_ARITY = tuple('package main\nimport _ "unsafe"\n'+directive+'\nvar X int\nfunc main(){}\n'
                  for directive in ('//go:linkname ', '//go:linkname X Y Z'))


def graph(row):
    return {package: tuple(files.items()) for package, files in row['packages'].items()}


def flattened(packages):
    return tuple((package+'/'+name, text) for package, units in packages.items() for name, text in units)


def positive_graphs():
    return (DIAGNOSTIC_ORIGINAL, PACKAGE_ORIGINAL) + tuple(graph(row) for row in CORPUS['programs'])


def record_diagnostic(path, packages, error):
    path.write_text(json.dumps({
        'sources': {name: hashlib.sha256(text.encode('utf-8')).hexdigest() for name, text in flattened(packages)},
        'diagnostic': str(error), 'filename': error.filename,
        'line': error.line, 'column': error.column,
    }, indent=2)+'\n')


def test_complete_graph_corpus_and_normal_math_bits_inputs_keep_exact_identities():
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256
    assert hashlib.sha256(PLACEMENT_FIXTURE.read_bytes()).hexdigest() == PLACEMENT_SHA256
    assert PLACEMENT['invalid'][0]['source'] == (
        'package main\nimport _ "unsafe"\nvar X int //go:linkname X\nfunc main(){}\n')
    assert PLACEMENT['inert'][0]['source'] == '\ufeff//go:linkname\npackage main\nfunc main(){}\n'
    assert PLACEMENT['standalone'][3]['source'] == (
        '\ufeff//go:linkname X\npackage main\nimport _ "unsafe"\nvar X int\nfunc main(){}\n')
    for rows in PLACEMENT.values():
        for row in rows:
            assert hashlib.sha256(row['source'].encode('utf-8')).hexdigest() == row['sha256']
    provenance = json.loads((ROOT / 'upstream/provenance.json').read_text())
    assert provenance['commit'] == '2aae1000ea1d4b28923b96db28acf4e3b99d6aff'
    for name, expected in NORMAL_SHA256.items():
        data = (UPSTREAM / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected
        assert provenance['files']['src/math/bits/'+name] == expected
    assert DIAGNOSTIC_ORIGINAL == {
        'main': (('main.go', 'package main;import _ "local";func main(){}'),),
        'local': (('lib.go', 'package local;const X=3'),),
    }
    assert PACKAGE_ORIGINAL == {
        'main': [('main.go', 'package main; import _ "lib"; func main(){}')],
        'lib': [('lib.go', 'package arithmetic; const Base = 3; func Add(x int) int {return x+1}')],
    }


@pytest.mark.parametrize('packages', (DIAGNOSTIC_ORIGINAL, PACKAGE_ORIGINAL))
def test_exact_previous_blank_import_boundaries_build_owned_ir(packages, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go_packages(packages)


@pytest.mark.parametrize('row', CORPUS['programs'], ids=lambda row: row['name'])
def test_blank_imports_visit_dependencies_without_introducing_names_or_duplicates(row, monkeypatch):
    forbid_external_compiler(monkeypatch)
    module = compile_go_packages(graph(row))
    assert function_symbol('Result') in module.globals
    if row['name'] == 'static-only':
        assert function_symbol('Dormant', 'dep') in module.globals
        assert function_symbol('Value', 'dep') in module.globals
    if row['name'] == 'repeated-and-same-package-spelling':
        assert function_symbol('Shared', 'dep') in module.globals
        assert function_symbol('Shared', 'other') in module.globals
    if row['name'] == 'named-and-blank-single-owner':
        assert list(module.globals).count(function_symbol('Value', 'dep')) == 1


@pytest.mark.parametrize('row', CORPUS['invalid'], ids=lambda row: row['name'])
def test_blank_dependencies_keep_full_source_semantics_before_ir(row, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid dependency reached IR'))
    with pytest.raises(GoDiagnostic, match=row['pcc']) as caught:
        compile_go_packages(graph(row))
    assert type(caught.value) is GoDiagnostic


# unsafe is now a compiler-owned pseudo-package: its exact original builds.
GRADUATED_STRUCTURAL = ('unsafe-source-not-fabricated',)
STRUCTURAL = tuple(row for row in CORPUS['structural_failures'] if row['name'] not in GRADUATED_STRUCTURAL)


@pytest.mark.parametrize('row', STRUCTURAL, ids=lambda row: row['name'])
def test_blank_imports_do_not_ignore_missing_sources_cycles_or_main_packages(row, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('incomplete graph reached IR'))
    with pytest.raises(GoDiagnostic, match=row['pcc']) as caught:
        compile_go_packages(graph(row))
    assert type(caught.value) is GoDiagnostic


@pytest.mark.parametrize('row', [row for row in CORPUS['structural_failures'] if row['name'] in GRADUATED_STRUCTURAL],
                         ids=lambda row: row['name'])
def test_blank_unsafe_import_needs_no_supplied_or_fabricated_source(row, monkeypatch):
    forbid_external_compiler(monkeypatch)
    packages = graph(row)
    assert set(packages) == {'main'}
    compile_go_packages(packages)


@pytest.mark.parametrize('row', CORPUS['frontiers'], ids=lambda row: row['name'])
def test_dynamic_and_transitive_initialization_remain_explicit_frontiers(row, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('runtime initialization reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go_packages(graph(row))
    assert caught.value.code == 'go.unsupported.package-initialization'


@pytest.mark.parametrize('original', INERT)
def test_linkname_spelling_inside_inert_comments_or_literals_does_not_rewrite_source(original, monkeypatch):
    forbid_external_compiler(monkeypatch)
    compile_go(original, 'original.go')


@pytest.mark.parametrize('directive', (
    '//go:linkname X', '//go:linkname X dep.X', '//go:linknamestd X dep.X',
    '//go:linkname  X\u00a0dep.X',
))
def test_recognized_linker_aliases_cannot_manufacture_ordinary_scalar_globals(directive, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    original = 'package main\nimport _ "unsafe"\n'+directive+'\nvar X int\nfunc main(){}\n'
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unowned linker alias reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(original, 'original.go')
    assert caught.value.code == 'go.unsupported.linkname-directives'
    assert (caught.value.filename, caught.value.line, caught.value.column) == ('original.go', 3, 1)


@pytest.mark.parametrize('directive', (
    '//go:linkname X\x1cY',
    # Three Go Fields, but four Python split fields: the distinction must not
    # become a false usage error. Go White_Space excludes U+001C, so the local
    # name is X\x1cY, which this package does not declare.
    '//go:linkname X\x1cY Z',
))
def test_linkname_local_names_must_be_declared(directive):
    original = 'package main\nimport _ "unsafe"\n'+directive+'\nvar X int\nfunc main(){}\n'
    with pytest.raises(GoDiagnostic, match='must refer to declared function or variable') as caught:
        compile_go(original, 'original.go')
    assert type(caught.value) is GoDiagnostic
    assert (caught.value.filename, caught.value.line, caught.value.column) == ('original.go', 3, 1)


@pytest.mark.parametrize('original', BAD_ARITY)
def test_malformed_linkname_arity_is_invalid_not_a_capability_result(original):
    with pytest.raises(GoDiagnostic, match='usage: //go:linkname') as caught:
        compile_go(original, 'original.go')
    assert type(caught.value) is GoDiagnostic


@pytest.mark.parametrize('row', PLACEMENT['invalid'], ids=lambda row: row['name'])
def test_misplaced_directives_are_invalid_before_keyword_arity_or_capability(row, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('misplaced directive reached IR'))
    with pytest.raises(GoDiagnostic, match='misplaced compiler directive') as caught:
        compile_go(row['source'], 'original.go')
    assert type(caught.value) is GoDiagnostic
    assert caught.value.code is None
    assert (caught.value.filename, caught.value.line, caught.value.column) == (
        'original.go', row['line'], row['column'])


@pytest.mark.parametrize('row', PLACEMENT['standalone'], ids=lambda row: row['name'])
def test_standalone_directives_allow_only_source_whitespace_before_the_comment(row, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unowned linker alias reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go(row['source'], 'original.go')
    assert caught.value.code == 'go.unsupported.linkname-directives'
    assert (caught.value.filename, caught.value.line, caught.value.column) == (
        'original.go', row['line'], row['column'])


@pytest.mark.parametrize('row', PLACEMENT['invalid_bom'], ids=lambda row: row['name'])
def test_directive_placement_does_not_allow_a_noninitial_or_second_bom(row, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid BOM reached IR'))
    with pytest.raises(GoDiagnostic, match='unsupported character') as caught:
        compile_go(row['source'], 'original.go')
    assert type(caught.value) is GoDiagnostic
    assert caught.value.code is None


def test_complete_normal_math_bits_stays_at_real_symbol_ownership_boundary(monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    originals = tuple((name, (UPSTREAM / name).read_bytes().decode('utf-8')) for name in NORMAL_SHA256)
    inputs = {'main': (('admission-main.go', 'package main;import "math/bits";func main(){_=bits.LeadingZeros(1)}'),),
              'math/bits': originals}
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('runtime-owned math/bits reached IR'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go_packages(inputs)
    # Both directives bind owned runtime error values; the first variable
    # then needs package interface storage and runtime initialization.
    assert caught.value.code == 'go.unsupported.interface-types'
    assert 'runtime-owned runtime.overflowError' in caught.value.message
    assert (caught.value.filename, caught.value.line, caught.value.column) == ('bits_errors.go', 12, 5)
    assert set(inputs) == {'main', 'math/bits'}  # No unsafe/runtime replacement.
    for name, text in originals:
        assert (UPSTREAM / name).read_bytes() == text.encode('utf-8')


@pytest.mark.integration
@pytest.mark.parametrize('row', CORPUS['programs'], ids=lambda row: row['name'])
def test_static_blank_graphs_have_full_width_owned_native_results(row, tmp_path, monkeypatch):
    packages = graph(row)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_module(compile_go_packages(packages), observe_entry=('Result', ()))
    binary = tmp_path / 'owned'
    write_artifact(artifact, binary)
    assert observed(binary, tmp_path / 'observation', owner=artifact.owner,
                    originals=flattened(packages)) == Execution(0, row['expected'].to_bytes(8, 'little'), b'')


@pytest.mark.integration
def test_complete_graphs_and_exact_former_boundaries_execute_owned_main(tmp_path, monkeypatch):
    for index, packages in enumerate(positive_graphs()):
        with monkeypatch.context() as denied:
            forbid_external_compiler(denied)
            artifact = compile_module(compile_go_packages(packages))
        binary = tmp_path / ('owned-'+str(index))
        write_artifact(artifact, binary)
        assert observed(binary, tmp_path / ('observation-'+str(index)), owner=artifact.owner,
                        originals=flattened(packages)) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_identical_complete_blank_import_graphs_match_official_processes(tmp_path, monkeypatch):
    for index, packages in enumerate(positive_graphs()):
        original = flattened(packages)
        with monkeypatch.context() as denied:
            forbid_external_compiler(denied)
            artifact = compile_module(compile_go_packages(packages))
        binary = tmp_path / ('owned-'+str(index))
        write_artifact(artifact, binary)
        reference = compile_reference_packages(packages, tmp_path / ('reference-'+str(index)),
            compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
        for name, text in original:
            assert (reference.parent / 'gopath/src' / name).read_bytes() == text.encode('utf-8')
        actual = observed(binary, tmp_path / ('pcc-observation-'+str(index)), owner=artifact.owner, originals=original)
        expected = observed(reference, tmp_path / ('go-observation-'+str(index)), owner='official-go', originals=original)
        assert actual == expected == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('row', CORPUS['invalid'], ids=lambda row: row['name'])
def test_identical_invalid_blank_dependencies_have_attributed_official_rejections(row, tmp_path):
    packages = graph(row)
    with pytest.raises(GoDiagnostic, match=row['pcc']) as caught:
        compile_go_packages(packages)
    assert type(caught.value) is GoDiagnostic
    record_diagnostic(tmp_path / 'pcc-diagnostic.json', packages, caught.value)
    compile_reference_packages_rejection(packages, tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'], diagnostics=row['reference'])


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
def test_actual_initialization_and_linkname_frontiers_remain_reference_only(tmp_path):
    initialization = tuple(graph(row) for row in CORPUS['frontiers'])
    linknames = (LINKNAME,) + tuple(row['source'] for row in PLACEMENT['standalone'])
    cases = initialization + tuple({'main': (('main.go', original),)} for original in linknames)
    for index, packages in enumerate(cases):
        with pytest.raises(UnsupportedGo) as caught:
            compile_go_packages(packages)
        assert caught.value.code == ('go.unsupported.linkname-directives' if index >= len(initialization)
                                     else 'go.unsupported.package-initialization')
        record_diagnostic(tmp_path / ('pcc-diagnostic-'+str(index)+'.json'), packages, caught.value)
        reference = compile_reference_packages(packages, tmp_path / ('reference-'+str(index)),
            compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
        original = flattened(packages)
        for name, text in original:
            assert (reference.parent / 'gopath/src' / name).read_bytes() == text.encode('utf-8')
        assert observed(reference, tmp_path / ('go-observation-'+str(index)), owner='official-go',
                        originals=original) == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original', INERT)
def test_identical_inert_directive_spellings_match_official_processes(original, tmp_path, monkeypatch):
    originals = (('original.go', original),)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_owned(originals)
    binary = tmp_path / 'owned'
    write_artifact(artifact, binary)
    reference = compile_reference(originals, tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert (reference.parent / 'original.go').read_bytes() == original.encode('utf-8')
    actual = observed(binary, tmp_path / 'pcc-observation', owner=artifact.owner, originals=originals)
    expected = observed(reference, tmp_path / 'go-observation', owner='official-go', originals=originals)
    assert actual == expected == Execution(0, b'', b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('original', BAD_ARITY)
def test_identical_malformed_linkname_arity_has_official_source_rejection(original, tmp_path):
    with pytest.raises(GoDiagnostic, match='usage: //go:linkname') as caught:
        compile_go(original, 'original.go')
    assert type(caught.value) is GoDiagnostic
    record_diagnostic(tmp_path / 'pcc-diagnostic.json', {'main': (('original.go', original),)}, caught.value)
    compile_reference_rejection((('original.go', original),), tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'],
        diagnostics='usage: //go:linkname')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('row', PLACEMENT['invalid'], ids=lambda row: row['name'])
def test_identical_misplaced_directives_have_official_source_rejections(row, tmp_path):
    original = row['source']
    with pytest.raises(GoDiagnostic, match='misplaced compiler directive') as caught:
        compile_go(original, 'original.go')
    assert type(caught.value) is GoDiagnostic
    record_diagnostic(tmp_path / 'pcc-diagnostic.json', {'main': (('original.go', original),)}, caught.value)
    receipt = compile_reference_rejection((('original.go', original),), tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'],
        diagnostics='misplaced compiler directive')
    assert receipt['classification'] == 'normal_source_rejection'
    assert (tmp_path / 'reference/original.go').read_bytes() == original.encode('utf-8')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('row', PLACEMENT['invalid_bom'], ids=lambda row: row['name'])
def test_identical_noninitial_bom_originals_have_official_source_rejections(row, tmp_path):
    original = row['source']
    with pytest.raises(GoDiagnostic, match='unsupported character') as caught:
        compile_go(original, 'original.go')
    assert type(caught.value) is GoDiagnostic
    record_diagnostic(tmp_path / 'pcc-diagnostic.json', {'main': (('original.go', original),)}, caught.value)
    receipt = compile_reference_rejection((('original.go', original),), tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'],
        diagnostics='invalid BOM in the middle of the file')
    assert receipt['classification'] == 'normal_source_rejection'
    assert (tmp_path / 'reference/original.go').read_bytes() == original.encode('utf-8')
