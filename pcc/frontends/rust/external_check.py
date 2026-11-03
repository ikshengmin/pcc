"""Explicit transitional rustc checker, separate from the owned Rust compiler.

This development/transition adapter executes a caller-audited, pinned direct
rustc installation. It never consumes MIR or substitutes external codegen for
PCC. Python callers and the local tool installation are trusted; the private
minting seal is an API boundary, not a defence against Python reflection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import subprocess

from .certification import ExternalRustcCheck
from .editions import require_edition
from .syntax import RustError
from .targets import DEFAULT_TARGET, resolve_target

_SEAL = object()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _file_sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class RustcToolchain:
    """Audited direct installation, not a PATH/rustup proxy or an unpinned tool."""
    root: Path
    compiler_sha256: str
    verbose_version: str


class RustExternalCheckError(RustError):
    """Actual external checking failed; no checked-source token was minted."""


@dataclass(frozen=True)
class ExternalRustcLints:
    """Result metadata only; never an admission capability or owned lint claim."""
    attributes: tuple
    stderr: bytes
    provenance: ExternalRustcCheck
    owner: str = field(default='external_rustc_lints', init=False)


@dataclass(frozen=True, init=False)
class CheckedRustSource:
    _source: str
    _record: ExternalRustcCheck
    _stderr: bytes
    _seal: object

    def __init__(self, source, record, *, stderr=b'', _seal=None):
        if _seal is not _SEAL:
            raise RustError('CheckedRustSource can only be created by check_rust_source after an actual successful check')
        object.__setattr__(self, '_source', source)
        object.__setattr__(self, '_record', record)
        object.__setattr__(self, '_stderr', stderr)
        object.__setattr__(self, '_seal', _seal)

    @property
    def provenance(self):
        return self._record

    @property
    def stderr(self):
        """Exact bytes from the actual successful metadata check, not a log path."""
        return self._stderr

    def configuration(self, source, *, edition):
        """Bind owned cfg evaluation to this actual unchanged-source command."""
        self.validate(source, target_triple=self._record.target_triple,
                      overflow_checks=self._record.overflow_checks, edition=edition)
        from .configuration import RustConfiguration
        return RustConfiguration(self._record.source_sha256, edition,
                                 self._record.target_triple, self._record.cfg)

    def validate(self, source, *, target_triple, overflow_checks, edition=2021):
        if self._seal is not _SEAL or source != self._source:
            raise RustError('checked Rust source token does not match the unchanged Rust source bytes')
        if type(self._stderr) is not bytes or _sha(self._stderr) != self._record.stderr_sha256:
            raise RustError('checked Rust source diagnostic bytes do not match external rustc provenance')
        self._record.validate(source, target_triple=target_triple,
                              overflow_checks=overflow_checks, edition=edition)

    def compile_owned(self, *, module_name='rust'):
        """Pure PCC phase: no external process, using the exact checked bytes."""
        from . import compile_rust
        return compile_rust(self._source, rustc_check=self, module_name=module_name,
                            target_triple=self._record.target_triple,
                            overflow_checks=self._record.overflow_checks,
                            edition=int(self._record.edition))


def check_rust_source(source: str, *, toolchain: RustcToolchain,
                      evidence_directory: Path, target_triple: str = DEFAULT_TARGET,
                      overflow_checks: bool = False,
                      edition: int = 2021) -> CheckedRustSource:
    """Actually check frozen bytes with rustc, or fail without an admission token.

    The bounded context is the selected edition, no cfg/extern dependencies and
    overflow-checks off. Evidence must use a fresh directory: an old metadata
    file can never satisfy this invocation. The caller supplies an audited
    installation pin, not merely a desired version label.
    """
    if type(source) is not str or type(toolchain) is not RustcToolchain:
        raise RustError('external checking requires source text and an audited RustcToolchain')
    edition = require_edition(edition)
    if overflow_checks is not False:
        raise RustError('external checked Rust overflow options are unsupported')
    target = resolve_target(target_triple)
    root = Path(toolchain.root).resolve()
    compiler = root / 'bin' / 'rustc'
    if compiler.is_symlink() or not compiler.is_file() or _file_sha(compiler) != toolchain.compiler_sha256:
        raise RustExternalCheckError('direct rustc executable does not match the audited installation pin')
    directory = Path(evidence_directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    original = source.encode('utf-8')
    path = directory / 'original.rs'
    path.write_bytes(original)
    metadata = directory / 'checked.rmeta'
    observations = []

    def run(label, command):
        try:
            result = subprocess.run(command, capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as error:
            stdout = getattr(error, 'stdout', None) or b''
            stderr = getattr(error, 'stderr', None) or b''
            (directory / (label + '.stdout')).write_bytes(stdout)
            (directory / (label + '.stderr')).write_bytes(stderr)
            observations.append({'command': command, 'status': type(error).__name__,
                                 'exit_code': None, 'stdout_sha256': _sha(stdout),
                                 'stderr_sha256': _sha(stderr)})
            (directory / 'processes.json').write_text(json.dumps(observations, indent=2) + '\n')
            raise RustExternalCheckError('external rustc process did not complete: ' + str(error)) from error
        (directory / (label + '.stdout')).write_bytes(result.stdout)
        (directory / (label + '.stderr')).write_bytes(result.stderr)
        observations.append({'command': command, 'status': 'COMPLETE', 'exit_code': result.returncode,
                             'stdout_sha256': _sha(result.stdout), 'stderr_sha256': _sha(result.stderr)})
        (directory / 'processes.json').write_text(json.dumps(observations, indent=2) + '\n')
        if result.returncode != 0:
            raise RustExternalCheckError('external rustc rejected input: ' + result.stderr.decode(errors='replace'))
        return result

    version = run('rustc-version', [str(compiler), '-vV'])
    if version.stdout.decode() != toolchain.verbose_version or not version.stdout.startswith(b'rustc '):
        raise RustExternalCheckError('rustc verbose version does not match the audited installation pin')
    sysroot = run('rustc-sysroot', [str(compiler), '--print=sysroot'])
    if Path(sysroot.stdout.decode().strip()).resolve() != root:
        raise RustExternalCheckError('rustc selected a different sysroot; PATH/rustup proxy is not a direct installation')
    command = [str(compiler), '--edition=' + str(edition), '--target=' + target.triple, '-Coverflow-checks=off',
               '--emit=metadata', '--crate-type=lib', '--crate-name=certified_input',
               str(path), '-o', str(metadata)]
    result = run('check', command)
    if (path.read_bytes() != original or not metadata.is_file() or metadata.stat().st_size == 0
            or _file_sha(compiler) != toolchain.compiler_sha256):
        raise RustExternalCheckError('external check source/tool/output identity changed or metadata is missing')
    record = ExternalRustcCheck(
        source_sha256=_sha(original), edition=str(edition), target_triple=target.triple,
        cfg=(), dependencies=(), overflow_checks=False,
        compiler_sha256=toolchain.compiler_sha256, compiler_version=version.stdout.decode(),
        command=tuple(command), exit_code=result.returncode,
        stderr_sha256=_sha(result.stderr), metadata_sha256=_file_sha(metadata))
    record.validate(source, target_triple=target.triple, overflow_checks=False, edition=edition)
    from dataclasses import asdict
    (directory / 'external-check.json').write_text(json.dumps(asdict(record), indent=2) + '\n')
    return CheckedRustSource(source, record, stderr=result.stderr, _seal=_SEAL)


def compile_rust_checked(source: str, *, toolchain: RustcToolchain,
                         evidence_directory: Path, target_triple: str = DEFAULT_TARGET,
                         overflow_checks: bool = False, module_name: str = 'rust',
                         edition: int = 2021):
    """Supported transitional entry: actual external check, then owned lowering."""
    checked = check_rust_source(source, toolchain=toolchain, evidence_directory=evidence_directory,
                                target_triple=target_triple, overflow_checks=overflow_checks,
                                edition=edition)
    return checked.compile_owned(module_name=module_name)
