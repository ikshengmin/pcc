"""Receipt memory controls; no target execution or qualification is implied."""
from pathlib import Path
import hashlib
import struct
import subprocess

import pytest

from scripts import bootstrap_platform as bootstrap
from tests.cpp.project_cases import _native_magic, sha256
from tests.test_windows_system_dependency_receipts import _pe_import_table


def _elf():
    from pcc.backend.elf_x86_64 import link_static_executable
    from pcc.backend.x86_64_asm_driver import assemble_file
    return link_static_executable([assemble_file(
        '.intel_syntax noprefix\n.text\n.globl _start\n_start:\n ret\n')])


@pytest.mark.parametrize('payload', [b'', b'MZ', b'\x7fELF', _pe_import_table(('KERNEL32.dll',)),
                                      _pe_import_table(('libpython3.dll',))])
def test_mapped_receipt_preserves_validator_results_and_errors(tmp_path, payload):
    path = tmp_path / 'image'
    path.write_bytes(payload)
    try:
        expected = bootstrap._dependency_receipt_data(payload)
    except Exception as error:
        with pytest.raises(type(error)) as actual:
            bootstrap.dependency_receipt(path)
        assert str(actual.value) == str(error)
    else:
        assert bootstrap.dependency_receipt(path) == expected


def test_receipt_retains_private_snapshot_after_original_mutation(tmp_path, monkeypatch):
    path = tmp_path / 'image'
    original = _elf()
    path.write_bytes(original)
    validate = bootstrap._dependency_receipt_data
    expected = validate(original)
    def mutate_and_validate(snapshot):
        path.write_bytes(b'different and truncated')
        assert snapshot[:] == original
        return validate(snapshot)
    monkeypatch.setattr(bootstrap, '_dependency_receipt_data', mutate_and_validate)
    assert bootstrap.dependency_receipt(path) == expected


def test_large_image_receipt_and_hash_do_not_use_read_bytes(tmp_path, monkeypatch):
    path = tmp_path / 'large-image'
    image = _elf()
    with path.open('wb') as stream:
        stream.write(image)
        stream.truncate(32 * 1024 * 1024)
    original_read_bytes = Path.read_bytes
    def forbidden(target):
        if target == path:
            raise AssertionError('large image was copied into the Python heap')
        return original_read_bytes(target)
    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    assert _native_magic(path)
    assert bootstrap.dependency_receipt(path) == bootstrap._dependency_receipt_data(image)
    expected = hashlib.sha256(image)
    block = bytes(1024 * 1024)
    remaining = path.stat().st_size - len(image)
    while remaining:
        chunk = min(remaining, len(block))
        expected.update(block[:chunk])
        remaining -= chunk
    assert sha256(path) == expected.hexdigest()
    with path.open('r+b') as stream:
        stream.seek(-1, 2)
        stream.write(b'1')
    assert sha256(path) != expected.hexdigest()


def test_native_magic_reads_only_required_prefix(tmp_path):
    for payload, expected in [(b'',False), (b'M',False), (b'MZ',True), (b'\x7fELF',True), (b'#!',False)]:
        path=tmp_path/'prefix'
        path.write_bytes(payload)
        assert _native_magic(path) is expected


def test_snapshot_uses_workspace_and_closes_on_validator_failure(tmp_path, monkeypatch):
    path = tmp_path / 'bad-image'
    path.write_bytes(b'MZ')
    factory = bootstrap.tempfile.TemporaryFile
    observed = []
    def tracked(*args, **kwargs):
        assert kwargs['dir'] == bootstrap.ROOT / 'build' / 'native-image-snapshots'
        stream = factory(*args, **kwargs)
        observed.append(stream)
        return stream
    monkeypatch.setattr(bootstrap.tempfile, 'TemporaryFile', tracked)
    with pytest.raises(struct.error):
        bootstrap.dependency_receipt(path)
    assert len(observed) == 1 and observed[0].closed


def test_visible_windows_style_snapshot_does_not_dirty_source_identity(tmp_path, monkeypatch):
    repo = tmp_path / 'checkout'
    repo.mkdir()
    def git(*args):
        subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
    git('init')
    (repo / '.gitignore').write_text('build/\n')
    git('add', '.gitignore')
    git('-c', 'user.name=Receipt test', '-c', 'user.email=receipt@example.invalid',
        'commit', '-m', 'isolated test input')
    monkeypatch.setattr(bootstrap, 'ROOT', repo)
    path = tmp_path / 'image'
    path.write_bytes(_elf())
    before = bootstrap.source_identity()
    # Emulate Windows' visible NamedTemporaryFile behavior on every platform.
    factory = bootstrap.tempfile.NamedTemporaryFile
    names = []
    def named(*args, **kwargs):
        stream = factory(*args, **kwargs)
        names.append(Path(stream.name))
        return stream
    monkeypatch.setattr(bootstrap.tempfile, 'TemporaryFile', named)
    validate = bootstrap._dependency_receipt_data
    def check_identity(data):
        assert names[0].is_file()
        assert bootstrap.source_identity() == before
        return validate(data)
    monkeypatch.setattr(bootstrap, '_dependency_receipt_data', check_identity)
    assert bootstrap.dependency_receipt(path)['format'] == 'ELF'
    assert not names[0].exists()
    assert bootstrap.source_identity() == before


def test_snapshot_write_failure_closes_and_does_not_validate(tmp_path, monkeypatch):
    path = tmp_path / 'image'
    path.write_bytes(_elf())
    factory = bootstrap.tempfile.TemporaryFile
    streams = []
    class FailingSnapshot:
        def __init__(self, **kwargs):
            self.stream = factory(**kwargs)
            streams.append(self.stream)
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            self.stream.close()
        def write(self, chunk):
            raise OSError('simulated snapshot storage exhausted')
    def forbidden(data):
        raise AssertionError('partial snapshot reached dependency validation')
    monkeypatch.setattr(bootstrap.tempfile, 'TemporaryFile', FailingSnapshot)
    monkeypatch.setattr(bootstrap, '_dependency_receipt_data', forbidden)
    with pytest.raises(OSError, match='snapshot storage exhausted'):
        bootstrap.dependency_receipt(path)
    assert len(streams) == 1 and streams[0].closed
