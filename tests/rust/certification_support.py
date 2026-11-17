"""Development-only actual-check support; never mints tokens from declarations."""
import json
import os
from pathlib import Path

from pcc.frontends.rust.reference_admission import contains_references
from pcc.frontends.rust.syntax import Parser


def source_has_references(source):
    parser = Parser(source)
    functions = parser.parse()
    return contains_references(functions, parser.records, parser.enums, parser.implementations, parser.traits, parser.modules)


def certify_source(source, directory, *, target_triple='x86_64-unknown-linux-gnu'):
    from pcc.frontends.rust import check_rust_source
    from tests.rust.test_rust_certified_references import toolchain

    return check_rust_source(source, toolchain=toolchain(), evidence_directory=directory,
                             target_triple=target_triple)


def actual_check_prerequisite():
    receipt = os.environ.get('PCC_RUSTC_INSTALL_RECEIPT')
    if not receipt or not Path(receipt).is_file():
        return 'actual audited rustc check required; caller flag cannot authorize reference lowering'
    root = Path(json.loads(Path(receipt).read_text())['new_prefix'])
    if not (root / 'bin/rustc').is_file():
        return 'audited direct rustc executable unavailable; reference lowering cannot proceed'
    return None


def darwin_check_prerequisite():
    missing = actual_check_prerequisite()
    if missing:
        return missing
    receipt = os.environ['PCC_RUSTC_INSTALL_RECEIPT']
    root = Path(json.loads(Path(receipt).read_text())['new_prefix'])
    libraries = root / 'lib/rustlib/aarch64-apple-darwin/lib'
    if not all(any(libraries.glob(pattern)) for pattern in ('libcore-*.rlib', 'libstd-*.rlib')):
        return ('aarch64-apple-darwin rustc core/std prerequisite unavailable; '
                'Linux check cannot authorize Darwin reference lowering')
    return None


def forbid_moves_for_references(patch, source, forbidden):
    """Forbid the owned move checker only where the actual check still owns moves.

    Owned moves decide reference-free input in every admission mode
    (pcc/frontends/rust/README.md, "Replacing the rustc admission gate", stage
    2). Whole-program checks of reference-bearing certified input still use
    the actual check. Generic declarations are checked separately: a
    reference-free declaration must retain owned move checking even when
    another declaration in the same source contains references.
    """
    from pcc.frontends.rust.moves import MoveChecker
    if source_has_references(source):
        patch.setattr(MoveChecker, 'check', forbidden)
        check_function = MoveChecker.check_function

        def check_reference_free_function(self, function):
            if contains_references(function):
                return forbidden(self, function)
            return check_function(self, function)

        patch.setattr(MoveChecker, 'check_function', check_reference_free_function)

