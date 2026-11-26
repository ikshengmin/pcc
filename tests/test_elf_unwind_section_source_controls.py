"""Independent exact-hash inverse controls; never execute restored owners."""
import hashlib
from pathlib import Path

import pytest

from tests.elf_unwind_section_preimage import (
    OWNER_SHA256, BASE_OWNER_SHA256, OWNER_REVERSE_EDITS, without_unwind_section,
)
from tests.elf_external_odr_preimage import without_external_odr, OWNER_SHA256 as ODR_OWNER
from tests.elf_host_exports_preimage import without_host_exports
from tests.elf_x86_packed_application_reference import without_x86_packed_application


def _source():
    return (Path(__file__).resolve().parents[1] / "pcc/backend/elf_x86_64.py").read_text()


def _sha(source):
    return hashlib.sha256(source.encode()).hexdigest()


def test_exact_unwind_inverse_keeps_old_reference_hashes():
    source = _source()
    assert _sha(source) == OWNER_SHA256
    assert BASE_OWNER_SHA256 == ODR_OWNER
    parent = without_unwind_section(source)
    assert _sha(parent) == BASE_OWNER_SHA256
    assert without_unwind_section(parent) == parent
    assert without_external_odr(source) == without_external_odr(parent)
    assert without_host_exports(source) == without_host_exports(parent)
    assert without_x86_packed_application(source) == without_x86_packed_application(parent)
    restored = parent
    for changed, original in OWNER_REVERSE_EDITS:
        assert restored.count(original) == 1
        restored = restored.replace(original, changed)
    assert restored == source


@pytest.mark.parametrize("hunk", range(3))
@pytest.mark.parametrize("fault", ("missing", "damaged", "duplicated"))
def test_unwind_inverse_requires_every_exact_hunk(hunk, fault):
    source = _source()
    changed, original = OWNER_REVERSE_EDITS[hunk]
    assert source.count(changed) == 1
    replacement = {"missing": original, "damaged": changed.replace("\n", " # drift\n", 1),
                   "duplicated": changed + changed}[fault]
    drift = source.replace(changed, replacement)
    assert drift != source
    for restore in (without_unwind_section, without_external_odr, without_host_exports,
                    without_x86_packed_application):
        with pytest.raises(AssertionError):
            restore(drift)


def test_unwind_inverse_rejects_unrelated_drift():
    source = _source() + "\n# unrelated owner drift\n"
    for restore in (without_unwind_section, without_external_odr, without_host_exports,
                    without_x86_packed_application):
        with pytest.raises(AssertionError):
            restore(source)
