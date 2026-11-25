"""Reference-qualification protocol controls, not simulated compiler evidence."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import ci_validation_cpp_reference as reference


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/pcc1-package-parity.yml'


def test_cpp17_platform_uses_one_qualified_reference_for_the_unmodified_suite():
    job = WORKFLOW.read_text().split('\n  cpp17:\n', 1)[1].split('\n  pcc-ab-qualification:', 1)[0]
    linux = job.split('- name: Linux x86_64', 1)[1].split('- name: macOS arm64', 1)[0]
    assert 'owned ELF and Clang++ reference' in linux
    assert 'oracle: clang++-18' in linux and 'diagnostic_oracle: g++' in linux
    assert 'scripts/ci_validation_cpp_reference.py' in job
    assert '--compiler "$PCC_CPP_ORACLE"' in job
    assert '--identity "$CPP17_OUT/identity.json"' in job
    assert '--diagnostic-driver "$REFERENCE_DIAGNOSTIC_DRIVER"' in job
    assert job.count('tests/cpp/test_cpp_platform_execution.py::test_cpp_platform_explicit_cpp17_reference') == 1
    assert 'continue-on-error' not in job and '|| true' not in job
    assert job.count('--timeout 90 --auto-tree-rss-ceiling-bytes 1073741824 --min-tree-rss-bytes 1073741824') == 4
    assert job.count('--performance-lock --process-rss-history') == 4
    # This independent source-lane tool binding is not silently replaced.
    owner = (ROOT / 'scripts/ci_validation_tools.py').read_text()
    assert 'environment["PCC_CPP_ORACLE"] = receipt["g++"]["invocation"]' in owner


def modeled_compiler(tmp_path, monkeypatch, *, positive_accepts, negative_rejects,
                     native_exit=42, version='Ubuntu clang version 18.1.3'):
    """Only exercise the receipt/routing owner; hosted runs supply real evidence."""
    real = tmp_path / 'driver'
    real.write_bytes(b'driver identity model')
    compiler = tmp_path / 'clang++-18'
    compiler.symlink_to(real)
    calls = []

    def observe(command, **options):
        calls.append(tuple(command))
        assert options == dict(capture_output=True, timeout=15, check=False)
        if command[0] != str(compiler):
            assert command == [str(tmp_path / 'probe' / 'const-member' / 'program')]
            return subprocess.CompletedProcess(command, native_exit, b'', b'')
        if command[1] == '--version':
            return subprocess.CompletedProcess(command, 0, version.encode() + b'\n', b'')
        if command[1] == '-dumpmachine':
            return subprocess.CompletedProcess(command, 0, b'x86_64-pc-linux-gnu\n', b'')
        assert command[1:4] == ['-std=c++17', '-pedantic-errors', '-O0']
        if '-fsyntax-only' in command:
            assert Path(command[-1]).read_text() == reference.PROBES[1][1]
            status = 1 if negative_rejects else 0
            return subprocess.CompletedProcess(command, status, b'', b'error: uninitialized const member' if status else b'')
        assert Path(command[4]).read_text() == reference.PROBES[0][1]
        status = 0 if positive_accepts else 1
        if status == 0:
            Path(command[-1]).write_bytes(b'program model')
        return subprocess.CompletedProcess(command, status, b'', b'error: rejected const member' if status else b'')

    monkeypatch.setattr(reference.subprocess, 'run', observe)
    return compiler, real, calls


def test_capability_observation_uses_driver_spelling_and_retains_real_contracts(tmp_path, monkeypatch):
    compiler, real, calls = modeled_compiler(tmp_path, monkeypatch,
                                            positive_accepts=True, negative_rejects=True)
    row = reference.observe_compiler(str(compiler), tmp_path / 'probe', role='normative')
    assert row['status'] == 'CAPABILITY_CONFIRMED'
    assert row['compiler_invocation'] == str(compiler)
    assert row['compiler_resolved'] == str(real)
    assert len(calls) == 5
    assert row['probes'][0]['compile']['returncode'] == 0
    assert row['probes'][0]['execution']['returncode'] == 42
    assert row['probes'][1]['compile']['returncode'] == 1
    assert (tmp_path / 'probe' / 'uninitialized-scalar' / 'compile.stderr.bin').read_bytes() == b'error: uninitialized const member'
    assert json.loads((tmp_path / 'probe' / 'receipt.json').read_text()) == row
    reference.require_normative(row, {key: row[key] for key in reference.IDENTITY_FIELDS})


@pytest.mark.parametrize('positive_accepts,negative_rejects,native_exit', (
    (False, True, 42), (True, False, 42), (True, True, 1),
))
def test_capability_mismatch_cannot_qualify_any_mandatory_reference(tmp_path, monkeypatch,
                                                                  positive_accepts, negative_rejects, native_exit):
    compiler, _, _ = modeled_compiler(tmp_path, monkeypatch,
                                        positive_accepts=positive_accepts,
                                        negative_rejects=negative_rejects,
                                        native_exit=native_exit)
    row = reference.observe_compiler(str(compiler), tmp_path / 'probe', role='diagnostic')
    assert row['status'] == 'CAPABILITY_MISMATCH'
    assert row['role'] == 'diagnostic'
    assert json.loads((tmp_path / 'probe' / 'receipt.json').read_text()) == row
    # Even if the diagnostic outcome were presented as normative, it cannot
    # qualify the comparator or stand in for a successful execution.
    with pytest.raises(ValueError):
        reference.require_normative(dict(row, role='normative'), {})


def test_contract_requires_bound_identity_and_supported_clang(tmp_path, monkeypatch):
    compiler, _, _ = modeled_compiler(tmp_path, monkeypatch,
                                        positive_accepts=True, negative_rejects=True,
                                        version='clang version 14.0.0')
    row = reference.observe_compiler(str(compiler), tmp_path / 'probe', role='normative')
    assert row['status'] == 'CAPABILITY_CONFIRMED'
    identity = {key: row[key] for key in reference.IDENTITY_FIELDS}
    with pytest.raises(ValueError, match='Clang 15'):
        reference.require_normative(row, identity)
    row['version'] = 'clang version 18.1.3\n'
    with pytest.raises(ValueError, match='identity'):
        reference.require_normative(row, identity)


def test_wrapper_does_not_replace_failed_normative_compiler_with_diagnostic(tmp_path, monkeypatch):
    compiler = 'fixed-clang++'
    diagnostic = 'observed-g++'
    calls = []
    rows = [dict(status='CAPABILITY_MISMATCH', role='normative'),
            dict(status='CAPABILITY_CONFIRMED', role='diagnostic')]

    def observe(invocation, target, *, role):
        calls.append((invocation, role))
        return rows[len(calls)-1]

    monkeypatch.setattr(reference, 'observe_compiler', observe)
    monkeypatch.setattr(reference.shutil, 'which', lambda name: '/usr/bin/' + name)
    with pytest.raises(ValueError):
        reference.qualify(compiler, diagnostic, tmp_path / 'qualification', {})
    assert calls == [(compiler, 'normative'), ('/usr/bin/'+diagnostic, 'diagnostic')]
    receipt = json.loads((tmp_path / 'qualification' / 'receipt.json').read_text())
    assert receipt == {'normative': rows[0], 'diagnostic': rows[1]}


def test_whole_reference_command_inherits_exact_qualified_driver(tmp_path, monkeypatch):
    identity = tmp_path / 'identity.json'
    identity.write_text('{}')
    out = tmp_path / 'qualification'
    expected = ['uv', 'run', 'python', '-m', 'pytest', 'original-suite']
    monkeypatch.setattr(sys, 'argv', ['qualifier', '--compiler', '/qualified/clang++-18',
                                   '--identity', str(identity), '--out', str(out),
                                   '--diagnostic-driver', 'g++', '--', *expected])
    monkeypatch.setenv('PCC_CPP_ORACLE', '/unqualified/g++')
    calls = []

    def qualify(compiler, diagnostic, output, bound):
        calls.append(('preflight', compiler, diagnostic, output, bound))

    def execvpe(program, arguments, environment):
        assert environment['PCC_CPP_ORACLE'] == '/qualified/clang++-18'
        calls.append(('suite', program, arguments))

    monkeypatch.setattr(reference, 'qualify', qualify)
    monkeypatch.setattr(reference.os, 'execvpe', execvpe)
    reference.main()
    assert calls == [('preflight', '/qualified/clang++-18', 'g++', out, {}),
                     ('suite', 'uv', expected)]
