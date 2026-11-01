"""External rustc provenance data; this record is NOT an admission capability.

This is provenance consistency checking, NOT proof a compiler ran. The supported
external_check adapter actually checks frozen source and privately mints a
CheckedRustSource; compile_rust rejects caller-constructed records of this type.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from .syntax import RustError


@dataclass(frozen=True)
class ExternalRustcCheck:
    source_sha256: str
    edition: str
    target_triple: str
    cfg: tuple[str, ...]
    dependencies: tuple[tuple[str, str], ...]
    overflow_checks: bool
    compiler_sha256: str
    compiler_version: str
    command: tuple[str, ...]
    exit_code: int
    stderr_sha256: str
    metadata_sha256: str

    def validate(self, source: str, *, target_triple: str, overflow_checks: bool) -> None:
        def digest(value):
            return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)

        if self.source_sha256 != hashlib.sha256(source.encode('utf-8')).hexdigest():
            raise RustError('external rustc check does not match the unchanged Rust source bytes')
        if (self.edition != '2021' or self.target_triple != target_triple
                or self.cfg != () or self.dependencies != ()
                or self.overflow_checks is not overflow_checks):
            raise RustError('external rustc check configuration does not match the supported edition/target/cfg/dependency/options context')
        if (type(self.exit_code) is not int or self.exit_code != 0
                or not all(digest(value) for value in (self.source_sha256, self.compiler_sha256,
                                                       self.stderr_sha256, self.metadata_sha256))
                or not isinstance(self.compiler_version, str) or not self.compiler_version.startswith('rustc ')
                or not isinstance(self.command, tuple) or not self.command
                or not all(isinstance(arg, str) for arg in self.command)):
            raise RustError('external rustc check requires an actual successful metadata run and complete tool/process provenance')
        # One deliberately finite adapter command, not a partial search of argv.
        # Extra/duplicate flags, response files, cfg and extern arguments could
        # otherwise contradict the normalized record despite required options
        # also being present. More build contexts need an explicit later adapter.
        options = ('--edition=2021', '--target=' + target_triple,
                   '-Coverflow-checks=off', '--emit=metadata', '--crate-type=lib',
                   '--crate-name=certified_input')
        if (len(self.command) != 10 or self.command[1:7] != options
                or self.command[8] != '-o'
                or any(not value or value.startswith(('-', '@')) or '\x00' in value
                       for value in (self.command[0], self.command[7], self.command[9]))):
            raise RustError('external rustc check command does not bind the declared compilation options')
