"""File-block dot imports: authored source/native/reference obligations, UNRUN.

https://go.dev/ref/spec#Import_declarations
https://go.dev/ref/spec#Declarations_and_scope
The unchanged upstream test import is a syntax frontier, not math/bits or
testing/runtime acceptance. No dependency or original source is substituted.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from pcc.frontends.go import (
    GoDiagnostic, UnsupportedGo, compile_go, compile_go_packages, function_symbol,
)
from pcc.frontends.go.syntax import GoParser
from tests.go.owned_execution import (
    Execution, compile_module, compile_reference_packages,
    compile_reference_packages_rejection, run, write_artifact,
)
from tests.go.test_execution import forbid_external_compiler


LIBRARY = ('package library;const Base int64=17;var Shared int64=3;'
           'type Number int64;func(n Number)Next()Number{return n+1};'
           'type Pair struct{X int64;hidden int64};'
           'var Record Pair=Pair{X:17,hidden:25};'
           'func(p Pair)Total()int64{return p.X+p.hidden};'
           'func Add(x int64)int64{return x+Base};'
           'func Identity[T any](x T)T{return x};'
           'func hidden()int64{return 1}')


def graph(body, *, library=LIBRARY, extra=()):
    return {
        'main': [('main.go', 'package main;import . "lib";'
                  'func Result()int64{' + body + '};'
                  'func main(){if Result()!=42{for{}}}'), *extra],
        'lib': [('library.go', library)],
    }


PROGRAMS = (
    ('canonical-global-storage', graph('Shared=0;Shared+=Base;return Add(Shared)+8')),
    ('nominal-type-and-methods', graph('var n Number=40;n=n.Next();return int64(Number.Next(n))')),
    ('imported-record-field-and-method', graph('Record.X=17;return Record.Total()')),
    ('local-shadow-with-type-use', graph('var n Number=40;{Base:=int64(1);n+=Number(Base)};return int64(n.Next())')),
    ('shared-generic-owner', graph('return Identity[int64](17)+other()', extra=(
        ('other.go', 'package main;import named "lib";func other()int64{return named.Identity[int64](25)}'),))),
    ('independent-file-bindings', graph('return Add(0)+other()', extra=(
        ('other.go', 'package main;import . "lib";func other()int64{return Identity[int64](25)}'),))),
    ('unicode-export', graph('return Éxport()', library='package library;func Éxport()int64{return 42}')),
    ('type-only-import-use', graph('var n Number;n=42;return int64(n)')),
    ('generic-definition-file-imports', {
        'main': [('main.go', 'package main;import . "lib";func Result()int64{return Identity[int64](42)};func main(){if Result()!=42{for{}}}')],
        'lib': [('library.go', 'package library;import . "base";func Identity[T any](x T)T{if Flag{return x};return x}')],
        'base': [('base.go', 'package base;const Flag=true')],
    }),
    ('disjoint-dot-imports', {
        'main': [('main.go', 'package main;import(. "left";. "right");func Result()int64{return Left+Right};func main(){if Result()!=42{for{}}}')],
        'left': [('left.go', 'package left;const Left int64=17')],
        'right': [('right.go', 'package right;const Right int64=25')],
    }),
)


@pytest.mark.parametrize('name,packages', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_dot_imports_resolve_existing_declarations_without_external_tools(name, packages, monkeypatch):
    forbid_external_compiler(monkeypatch)
    module = compile_go_packages(packages)
    assert function_symbol('Result') in str(module)
    if name == 'canonical-global-storage':
        assert function_symbol('Shared', 'lib') in module.globals
        assert function_symbol('Shared') not in module.globals
    if name == 'shared-generic-owner':
        prefix = function_symbol('Identity', 'lib') + '_instance_'
        instances = [symbol for symbol in module.globals if symbol.startswith(prefix)]
        assert len(instances) == 1


def test_original_bits_test_dot_import_is_retained_before_the_next_real_syntax_boundary():
    path = Path(__file__).parent / 'upstream/go1.27.3/src/math/bits/bits_test.go'
    original = path.read_bytes()

    class RecordingParser(GoParser):
        def import_declaration(self):
            items = super().import_declaration()
            self.recorded_imports.extend(items)
            return items

    parser = RecordingParser()
    parser.recorded_imports = []
    # Complete original input is supplied. Later syntax remains a real failure,
    # rather than a rewritten test file or a fabricated testing package.
    with pytest.raises(GoDiagnostic) as failure:
        parser.parse(original.decode(), 'bits_test.go', require_main=False)
    imported = parser.recorded_imports
    assert [(item.alias, item.path) for item in imported] == [
        ('.', 'math/bits'), (None, 'runtime'), (None, 'testing'), (None, 'unsafe'),
    ]
    assert imported[0].token.line == 8
    assert failure.value.filename == 'bits_test.go' and failure.value.line > 12
    assert path.read_bytes() == original


def test_exact_legacy_dot_original_remains_a_missing_source_or_unused_import_error():
    original = 'package main;import . "local";func main(){}'
    with pytest.raises(GoDiagnostic, match='import source unavailable') as missing:
        compile_go(original)
    assert not isinstance(missing.value, UnsupportedGo)
    with pytest.raises(GoDiagnostic, match='imported and not used'):
        compile_go_packages({'main': [('main.go', original)],
                             'local': [('local.go', 'package local;const X=3')]})


INVALID = (
    ('unused', graph('return 42'), 'imported and not used', 'not used'),
    ('unexported', graph('return hidden()'), 'undefined function', 'undefined: hidden'),
    ('private-field', graph('return Record.hidden'), 'unexported struct field', 'unexported field|cannot refer'),
    ('local-call-shadow', graph('Add:=1;_=Base;return Add(2)'), 'cannot call non-function', 'cannot call'),
    ('local-type-shadow', graph('Number:=1;_=Base;var x Number;_=Number;return int64(x)'), 'identifier is not a type', 'not a type'),
    ('sibling-isolation', graph('return Add(0)+other()', extra=(
        ('other.go', 'package main;func other()int64{return Base+8}'),)), 'undefined name Base', 'undefined: Base'),
    ('sibling-package-collision', graph('return Base+25', extra=(
        ('other.go', 'package main;const Base=17'),)), 'conflicts with package declaration', 'already declared|redeclar'),
    ('dot-export-collision', {
        'main': [('main.go', 'package main;import(. "a";. "b");func main(){_=X}')],
        'a': [('a.go', 'package a;const X=1')], 'b': [('b.go', 'package b;const X=2')],
    }, 'duplicate dot-imported name', 'redeclar|already declared'),
    ('named-alias-collision', {
        'main': [('main.go', 'package main;import(. "a";X "b");func main(){_=X.Y}')],
        'a': [('a.go', 'package a;const X=1')], 'b': [('b.go', 'package b;const Y=2')],
    }, 'conflicts with import name', 'redeclar|already declared'),
    ('independent-unused-dot-binding', {
        'main': [('main.go', 'package main;import(. "a";. "b");func main(){_=X}')],
        'a': [('a.go', 'package a;const X=1')], 'b': [('b.go', 'package b;const Y=2')],
    }, 'imported and not used', 'not used'),
    ('empty-export-import', graph('return 42', library='package library;const hidden=1'), 'imported and not used', 'not used'),
    ('local-shadow-is-not-import-use', graph('Base:=int64(42);return Base'), 'imported and not used', 'not used'),
    ('sibling-use-does-not-satisfy-unused-file', graph('return Base+25', extra=(
        ('unused.go', 'package main;import . "lib";func helper(){}'),)), 'imported and not used', 'not used'),
    ('type-parameter-shadow-is-not-import-use', {
        'main': [('main.go', 'package main;import . "lib";func F[Number any](x Number)Number{return x};func main(){_=F(42)}')],
        'lib': [('library.go', LIBRARY)],
    }, 'imported and not used', 'not used'),
)


@pytest.mark.parametrize('name,packages,message,reference_message', INVALID, ids=[row[0] for row in INVALID])
def test_dot_import_invalid_graphs_fail_before_lowering(name, packages, message, reference_message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('invalid dot import reached IR'))
    with pytest.raises(GoDiagnostic, match=message) as failure:
        compile_go_packages(packages)
    assert not isinstance(failure.value, UnsupportedGo)


def test_dot_syntax_cannot_overwrite_a_named_alias():
    with pytest.raises(GoDiagnostic, match='expected'):
        GoParser().parse('package main;import named . "lib";func main(){}')


@pytest.mark.integration
@pytest.mark.parametrize('name,packages', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_dot_import_graphs_execute_owned_with_full_width_results(name, packages, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_module(compile_go_packages(packages), observe_result=True)
        assert artifact == compile_module(compile_go_packages(packages), observe_result=True)
    binary = tmp_path / 'owned'
    write_artifact(artifact, binary)
    assert run(binary) == Execution(0, (42).to_bytes(8, 'little'), b'')


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('name,packages', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_same_dot_import_graphs_match_official_go(name, packages, tmp_path, monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_module(compile_go_packages(packages))
    binary = tmp_path / 'owned'
    write_artifact(artifact, binary)
    reference = compile_reference_packages(packages, tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'])
    assert run(binary) == run(reference) == Execution(0, b'', b'')
    identity = hashlib.sha256(json.dumps(packages, sort_keys=True).encode()).hexdigest()
    (tmp_path / 'source-identity.json').write_text(json.dumps({'sha256': identity, 'scope': 'same supplied source graph'}) + '\n')
    for package, units in packages.items():
        for filename, text in units:
            assert (reference.parent / 'gopath/src' / package / filename).read_bytes() == text.encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env='PCC_GO_REFERENCE')
@pytest.mark.xdist_group(name='go-reference')
@pytest.mark.parametrize('name,packages,message,reference_message', INVALID, ids=[row[0] for row in INVALID])
def test_official_go_rejects_identical_invalid_dot_import_graphs(name, packages, message, reference_message, tmp_path):
    receipt = compile_reference_packages_rejection(packages, tmp_path / 'reference',
        compiler=os.environ['PCC_GO_REFERENCE'], cache=os.environ['PCC_GO_REFERENCE_CACHE'],
        diagnostics=reference_message)
    assert receipt['classification'] == 'normal_source_rejection'
    for package, units in packages.items():
        for filename, text in units:
            assert (tmp_path / 'reference/gopath/src' / package / filename).read_bytes() == text.encode()
