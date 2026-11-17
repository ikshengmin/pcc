"""The selected reference cache changes reuse, never the verdict or observer."""
import os
import subprocess
from pathlib import Path

import pytest

from tests.go import darwin_execution as helper


@pytest.mark.parametrize('cache', [None, '', 'selected', 'relative/cache', 'off', 'file'])
@pytest.mark.parametrize('kind', ['reject', 'run'])
def test_cache_selection_preserves_build_and_environment(tmp_path, monkeypatch, cache, kind):
    selected = str(tmp_path / 'selected-cache') if cache == 'selected' else cache
    if cache == 'file':
        cache_file = tmp_path / 'not-a-directory'
        cache_file.write_text('existing file')
        selected = str(cache_file)
    if selected is None:
        monkeypatch.delenv('PCC_GO_REFERENCE_CACHE', raising=False)
    else:
        monkeypatch.setenv('PCC_GO_REFERENCE_CACHE', selected)
    monkeypatch.setattr(helper, 'REFERENCE', '/selected/go')
    before = dict(os.environ)
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, (17).to_bytes(8, 'little'), b'diagnostic')
    monkeypatch.setattr(helper.subprocess, 'run', run)
    units = (('a.go', 'package main\nfunc main() {}\n'),)
    if kind == 'reject':
        assert helper.reference_rejection(units, tmp_path) == (0, 'diagnostic')
    else:
        assert helper.run_reference_go_function(units, 'value', tmp_path) == 17
    command, options = calls[0]
    directory = tmp_path / 'reference'
    assert command == ['/selected/go', 'build', '-o', str(directory / 'reference'), 'a.go'] + (
        ['observer_main.go'] if kind == 'run' else [])
    assert options['cwd'] == directory
    assert options['timeout'] == 120 and options['capture_output'] is True
    assert options.get('check', False) is (kind == 'run')
    expected = dict(before, GOTOOLCHAIN='local', GOPROXY='off', GOSUMDB='off', GOWORK='off',
                    GO111MODULE='off', CGO_ENABLED='0', GOCACHE=selected or str(tmp_path / 'cache'))
    assert options['env'] == expected
    assert dict(os.environ) == before
    assert not (tmp_path / 'cache').exists()
    assert (directory / 'a.go').read_text() == units[0][1]
    if kind == 'run':
        assert calls[1] == ([str(directory / 'reference')], dict(capture_output=True, timeout=10, check=True))


@pytest.mark.parametrize('kind', ['reject', 'run'])
def test_reference_timeout_propagates(tmp_path, monkeypatch, kind):
    error = subprocess.TimeoutExpired('go', 120)
    def run(*args, **kwargs):
        raise error
    monkeypatch.setattr(helper.subprocess, 'run', run)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        if kind == 'reject':
            helper.reference_rejection((('a.go', 'package main'),), tmp_path)
        else:
            helper.run_reference_go_function((('a.go', 'package main'),), 'value', tmp_path)
    assert caught.value is error


def test_rejection_keeps_failure_code_and_decoding(tmp_path, monkeypatch):
    monkeypatch.setattr(helper.subprocess, 'run', lambda command, **kw:
                        subprocess.CompletedProcess(command, 2, b'', b'a.go:2:3: invalid\xff'))
    assert helper.reference_rejection((('a.go', 'package main'),), tmp_path) == (2, 'a.go:2:3: invalid\ufffd')


def test_observer_still_requires_eight_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(helper.subprocess, 'run', lambda command, **kw:
                        subprocess.CompletedProcess(command, 0, b'wrong', b''))
    with pytest.raises(AssertionError):
        helper.run_reference_go_function((('a.go', 'package main'),), 'value', tmp_path)
