"""Simulated reference-process protocol tests, NOT Go semantic qualification.

No compiler or generated program executes here. Real rejection acceptance is
separately gated on the exact original sources and the pinned official Go tool.
"""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from tests.go import owned_execution as execution


ORIGINAL = 'package main;func main(){var p *int=1;_=p}'
DIAGNOSTIC = b'./original.go:1:34: cannot use 1 as *int value in variable declaration\n'
EXPECTED = (b'cannot use 1', b'as *int value in variable declaration')


@pytest.fixture
def reference_protocol(tmp_path):
    compiler = tmp_path / 'simulated-go-not-executable'
    compiler.write_bytes(b'protocol test identity, not a Go compiler')
    return compiler, tmp_path / 'cache', tmp_path / 'reference'


def invoke(protocol, *, reject=True, diagnostics=EXPECTED):
    compiler, cache, directory = protocol
    options = dict(compiler=compiler, cache=cache)
    if reject:
        return execution.compile_reference_rejection(
            (('original.go', ORIGINAL),), directory, diagnostics=diagnostics, **options)
    return execution.compile_reference((('original.go', ORIGINAL),), directory, **options)


def observed(protocol, *, returncode, stdout, stderr, classification):
    compiler, cache, directory = protocol
    receipt = json.loads((directory / 'build.execution.json').read_text())
    assert (directory / 'original.go').read_bytes() == ORIGINAL.encode()
    assert (directory / 'build.stdout').read_bytes() == stdout
    assert (directory / 'build.stderr').read_bytes() == stderr
    assert receipt['returncode'] == returncode
    assert receipt['classification'] == classification
    assert receipt['stdout_sha256'] == hashlib.sha256(stdout).hexdigest()
    assert receipt['stderr_sha256'] == hashlib.sha256(stderr).hexdigest()
    assert receipt['sources'] == {str((directory / 'original.go').resolve()): hashlib.sha256(ORIGINAL.encode()).hexdigest()}
    assert receipt['command'] == [str(compiler), 'build', '-p=2', '-o', str(directory / 'oracle'), str(directory / 'original.go')]
    assert receipt['environment']['GOCACHE'] == str(cache)
    assert receipt['environment']['GOTOOLCHAIN'] == 'local'
    return receipt


def process(monkeypatch, *, returncode=1, stdout=b'', stderr=DIAGNOSTIC, output=False):
    def simulate(command, **options):
        assert options['capture_output'] is True and options['timeout'] == 60
        if output:
            Path(command[command.index('-o') + 1]).write_bytes(b'simulated binary')
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)
    monkeypatch.setattr(execution.subprocess, 'run', simulate)


def test_normal_rejection_requires_exact_exit_and_original_source_diagnostics(reference_protocol, monkeypatch):
    process(monkeypatch)
    receipt = invoke(reference_protocol)
    assert receipt == observed(reference_protocol, returncode=1, stdout=b'', stderr=DIAGNOSTIC,
                               classification='normal_source_rejection')
    assert receipt['source_diagnostics'] == ['cannot use 1 as *int value in variable declaration']
    assert not issubclass(execution.ReferenceBuildFailure, AssertionError)


@pytest.mark.parametrize('returncode,stdout,stderr,output,kind', [
    (2, b'', DIAGNOSTIC, False, 'not_normal_rejection_exit'),
    (-9, b'', DIAGNOSTIC, False, 'not_normal_rejection_exit'),
    (0, b'', DIAGNOSTIC, False, 'not_normal_rejection_exit'),
    (1, b'unexpected output', DIAGNOSTIC, False, 'unexpected_rejection_output'),
    (1, b'', DIAGNOSTIC, True, 'unexpected_rejection_output'),
    (1, b'', b'cannot use 1 as *int value in variable declaration\n', False, 'unattributed_rejection'),
    (1, b'', DIAGNOSTIC.replace(b'original.go', b'dependency.go'), False, 'unattributed_rejection'),
    (1, b'', b'original.go:1:1: unrelated parse error\n', False, 'unexpected_source_diagnostics'),
    (1, b'', DIAGNOSTIC + b'panic: compiler crash\n', False, 'abnormal_reference_failure'),
    (1, b'', DIAGNOSTIC + b'fatal error: out of memory\n', False, 'abnormal_reference_failure'),
    (1, b'', DIAGNOSTIC + b'compile: signal: killed\n', False, 'abnormal_reference_failure'),
    (1, b'', DIAGNOSTIC + b'cannot find package "missing"\n', False, 'abnormal_reference_failure'),
    (1, b'', DIAGNOSTIC + b'exec format error\n', False, 'abnormal_reference_failure'),
    (1, b'', DIAGNOSTIC + b'go: failed to trim cache: write /cache/trim.txt: no space left on device\n', False, 'unclassified_reference_stderr'),
    (1, b'', DIAGNOSTIC + b'unrecognized tool failure\n', False, 'unclassified_reference_stderr'),
    (1, b'', DIAGNOSTIC + b'dependency.go:1:1: unexpected dependent error\n', False, 'unclassified_reference_stderr'),
    (1, b'', DIAGNOSTIC + b'\tgo: failed to trim cache\n', False, 'unclassified_reference_stderr'),
])
def test_abnormal_build_never_counts_as_semantic_rejection(reference_protocol, monkeypatch,
                                                          returncode, stdout, stderr, output, kind):
    process(monkeypatch, returncode=returncode, stdout=stdout, stderr=stderr, output=output)
    with pytest.raises(execution.ReferenceBuildFailure) as caught:
        invoke(reference_protocol)
    assert caught.value.kind == kind
    observed(reference_protocol, returncode=returncode, stdout=stdout, stderr=stderr, classification=kind)


def test_missing_expected_diagnostics_is_not_an_unconstrained_rejection(reference_protocol, monkeypatch):
    process(monkeypatch)
    with pytest.raises(execution.ReferenceBuildFailure) as caught:
        invoke(reference_protocol, diagnostics=())
    assert caught.value.kind == 'missing_expected_diagnostics'
    observed(reference_protocol, returncode=1, stdout=b'', stderr=DIAGNOSTIC, classification=caught.value.kind)


def test_timeout_retains_partial_streams_but_is_never_a_rejection(reference_protocol, monkeypatch):
    def timeout(command, **options):
        raise subprocess.TimeoutExpired(command, 60, output=b'partial stdout', stderr=DIAGNOSTIC)
    monkeypatch.setattr(execution.subprocess, 'run', timeout)
    with pytest.raises(execution.ReferenceBuildFailure) as caught:
        invoke(reference_protocol)
    assert caught.value.kind == 'timeout'
    observed(reference_protocol, returncode=None, stdout=b'partial stdout', stderr=DIAGNOSTIC,
             classification='timeout')


def test_spawn_failure_retains_receipt_without_rejection(reference_protocol, monkeypatch):
    def missing(command, **options):
        raise FileNotFoundError('simulated unavailable executable')
    monkeypatch.setattr(execution.subprocess, 'run', missing)
    with pytest.raises(execution.ReferenceBuildFailure) as caught:
        invoke(reference_protocol)
    assert caught.value.kind == 'tool_failure'
    observed(reference_protocol, returncode=None, stdout=b'', stderr=b'', classification='tool_failure')


@pytest.mark.parametrize('returncode,output,kind', [(1, False, 'build_failed'), (0, False, 'missing_output')])
def test_positive_api_failure_is_distinct_from_semantic_rejection(reference_protocol, monkeypatch, returncode, output, kind):
    process(monkeypatch, returncode=returncode, output=output)
    with pytest.raises(execution.ReferenceBuildFailure) as caught:
        invoke(reference_protocol, reject=False)
    assert caught.value.kind == kind
    observed(reference_protocol, returncode=returncode, stdout=b'', stderr=DIAGNOSTIC, classification=kind)


def test_positive_api_still_returns_exact_output_path(reference_protocol, monkeypatch):
    process(monkeypatch, returncode=0, stdout=b'', stderr=b'', output=True)
    binary = invoke(reference_protocol, reject=False)
    assert binary == reference_protocol[2] / 'oracle'
    assert binary.read_bytes() == b'simulated binary'
    observed(reference_protocol, returncode=0, stdout=b'', stderr=b'', classification='native_build_succeeded')


def test_package_rejection_is_attributed_to_exact_supplied_source_graph(reference_protocol, monkeypatch):
    compiler, cache, directory = reference_protocol
    packages = {'main': [('main.go', 'package main;import "lib";func main(){lib.F()}')],
                'lib': [('lib.go', 'package lib;func F(){var p *int=1;_=p}')]}
    diagnostic = b'gopath/src/lib/lib.go:1:31: cannot use 1 as *int value in variable declaration\n'
    process(monkeypatch, stderr=diagnostic)
    receipt = execution.compile_reference_packages_rejection(
        packages, directory, compiler=compiler, cache=cache, diagnostics=EXPECTED)
    assert receipt['classification'] == 'normal_source_rejection'
    assert receipt['returncode'] == 1
    assert len(receipt['sources']) == 2
    assert (directory / 'build.stderr').read_bytes() == diagnostic
    for package, units in packages.items():
        for name, source in units:
            assert (directory / 'gopath/src' / package / name).read_bytes() == source.encode()


def test_source_attribution_includes_positioned_recursive_type_subdiagnostics(reference_protocol, monkeypatch):
    stderr = (b'original.go:1:18: invalid recursive type A\n'
              b'\toriginal.go:1:18: A refers to B\n'
              b'\toriginal.go:1:29: B refers to A\n')
    process(monkeypatch, stderr=stderr)
    receipt = invoke(reference_protocol, diagnostics=(b'invalid recursive type A', b'A refers to B', b'B refers to A'))
    assert receipt['classification'] == 'normal_source_rejection'
    observed(reference_protocol, returncode=1, stdout=b'', stderr=stderr, classification='normal_source_rejection')


def test_positive_package_api_preserves_exact_sources_and_returns_binary(reference_protocol, monkeypatch):
    compiler, cache, directory = reference_protocol
    packages = {'main': [('main.go', 'package main;func main(){}')]}
    process(monkeypatch, returncode=0, stderr=b'', output=True)
    binary = execution.compile_reference_packages(packages, directory, compiler=compiler, cache=cache)
    assert binary == directory / 'oracle'
    assert binary.read_bytes() == b'simulated binary'
    assert (directory / 'gopath/src/main/main.go').read_text() == packages['main'][0][1]
    receipt = json.loads((directory / 'build.execution.json').read_text())
    assert receipt['classification'] == 'native_build_succeeded' and receipt['returncode'] == 0



def test_source_attached_have_want_lines_are_normal_argument_diagnostics(reference_protocol, monkeypatch):
    stderr = (b'# command-line-arguments\noriginal.go:1:30: not enough arguments in call to f\n'
              b'\thave ()\n\twant (*int)\n')
    process(monkeypatch, stderr=stderr)
    receipt = invoke(reference_protocol, diagnostics=b'not enough arguments in call to f')
    assert receipt['classification'] == 'normal_source_rejection'
    assert receipt['unexpected_stderr_lines'] == []
    observed(reference_protocol, returncode=1, stdout=b'', stderr=stderr, classification='normal_source_rejection')



@pytest.mark.parametrize('selection', ['absolute-path-search', 'relative-path-search', 'relative-executable'])
def test_positive_api_hashes_and_executes_same_cwd_path_resolved_tool(tmp_path, monkeypatch, selection):
    # Both possible locations contain a file: hashing the Python cwd candidate
    # while running the child-cwd/PATH candidate must not accidentally pass.
    caller = tmp_path / 'caller/deep'
    caller.mkdir(parents=True)
    (caller / 'go').write_bytes(b'wrong caller cwd go')
    folder = tmp_path / 'bin'
    folder.mkdir()
    chosen = folder / 'go'
    chosen.write_bytes(b'chosen child cwd PATH compiler identity')
    chosen.chmod(0o700)
    monkeypatch.chdir(caller)
    if selection == 'absolute-path-search':
        monkeypatch.setenv('PATH', str(folder))
        requested = 'go'
    elif selection == 'relative-path-search':
        monkeypatch.setenv('PATH', '../bin')
        requested = 'go'
    else:
        requested = '../bin/go'
    process(monkeypatch, returncode=0, stderr=b'', output=True)
    directory = tmp_path / 'reference'
    binary = execution.compile_reference((('original.go', ORIGINAL),), directory,
                                         compiler=requested, cache=tmp_path / 'cache')
    assert binary == directory / 'oracle'
    receipt = json.loads((directory / 'build.execution.json').read_text())
    assert receipt['requested_compiler'] == requested
    assert receipt['command'][0] == str(chosen.resolve())
    assert receipt['compiler_sha256'] == hashlib.sha256(chosen.read_bytes()).hexdigest()
    assert receipt['classification'] == 'native_build_succeeded'
