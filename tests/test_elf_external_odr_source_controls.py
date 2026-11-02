"""Whole-owner ODR inverse proof; no compiler/linker/executable is run here."""

import hashlib
from pathlib import Path

import pytest

from tests.elf_external_odr_preimage import (
    BASE_OWNER_SHA256,
    OWNER_REVERSE_EDITS,
    OWNER_SHA256,
    without_external_odr,
)
from tests.elf_host_exports_preimage import (
    BASE_OWNER_SHA256 as PRE_EXPORT_OWNER_SHA256,
    OWNER_SHA256 as EXPORT_OWNER_SHA256,
    without_host_exports,
)
from tests.elf_arm_branch_slot_reference import without_arm_branch_slot_defaults
from tests.elf_x86_packed_application_reference import (
    _without_aarch64_jump26_veneers,
    _without_native_scalar_writes,
    without_x86_packed_application,
)
from tests.elf_x86_packed_classification_reference import without_x86_packed_classification


def _source():
    owner = Path(__file__).resolve().parents[1] / "pcc/backend/elf_x86_64.py"
    source = owner.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert "\r" not in source
    return source


def _sha(source):
    return hashlib.sha256(source.encode()).hexdigest()


def test_odr_inverse_reconstructs_exact_parent_and_round_trips():
    source = _source()
    assert _sha(source) == OWNER_SHA256
    assert BASE_OWNER_SHA256 == EXPORT_OWNER_SHA256
    restored = without_external_odr(source)
    assert restored != source
    assert _sha(restored) == BASE_OWNER_SHA256
    assert without_external_odr(restored) == restored
    assert len(OWNER_REVERSE_EDITS) == 2
    assert sum(changed.count("\n") - original.count("\n")
               for changed, original in OWNER_REVERSE_EDITS) == 6
    for changed, original in OWNER_REVERSE_EDITS:
        assert restored.count(original) == 1
        restored = restored.replace(original, changed)
    assert restored == source


@pytest.mark.parametrize("hunk", range(2))
@pytest.mark.parametrize("fault", ("missing", "damaged", "duplicated"))
def test_each_odr_hunk_is_exactly_required(hunk, fault):
    source = _source()
    changed, original = OWNER_REVERSE_EDITS[hunk]
    assert source.count(changed) == 1
    replacement = {
        "missing": original,
        "damaged": changed.replace("STB_WEAK", "STB_GLOBAL"),
        "duplicated": changed + changed,
    }[fault]
    drift = source.replace(changed, replacement)
    assert drift != source
    for restore in (without_external_odr, without_host_exports,
                    without_x86_packed_application):
        with pytest.raises(AssertionError):
            restore(drift)


@pytest.mark.parametrize("fault", ("unrelated", "marker", "symbol_identity", "metadata"))
def test_odr_inverse_rejects_unreviewed_owner_drift(fault):
    source = _source()
    if fault == "unrelated":
        drift = source + "\n# unrelated owner drift\n"
    elif fault == "marker":
        drift = source.replace("def _symbol_identity(", "def _changed_symbol_identity(")
    elif fault == "symbol_identity":
        drift = source.replace("symbol.binding != STB_WEAK", "symbol.binding == STB_WEAK", 1)
    else:
        drift = source.replace("physical local anchor", "prevailing global anchor", 1)
    assert drift != source
    for restore in (without_external_odr, without_host_exports,
                    without_x86_packed_application):
        with pytest.raises(AssertionError):
            restore(drift)


def test_odr_layer_preserves_both_previous_host_export_admissions():
    parent = without_external_odr(_source())
    assert _sha(parent) == EXPORT_OWNER_SHA256
    before_exports = without_host_exports(parent)
    assert _sha(before_exports) == PRE_EXPORT_OWNER_SHA256
    assert without_host_exports(before_exports) == before_exports
    # The narrow ODR adapter does not acquire arbitrary old-owner admission.
    with pytest.raises(AssertionError):
        without_external_odr(before_exports)
    assert without_host_exports(_source()) == before_exports


def test_odr_layer_preserves_every_historical_owner_hash():
    source = _source()
    jump = _without_aarch64_jump26_veneers(source)
    assert _sha(jump) == "ba10325e3646ed8b4f101ce8af15294f39fc7d8f5fdb00988808bf6c5ca81228"
    assert _sha(_without_native_scalar_writes(jump)) == "f2476a2c75257d5a051b5d5af21b82a1d19be53096de28a3267f81cf4680d20f"
    assert _sha(without_x86_packed_application(source)) == "3b85ba9dde549bb6b2a1d272657a6efc02c58277d8059c7a77df120871c66929"
    assert _sha(without_arm_branch_slot_defaults(source)) == "411959c27c76d984d2ccff427fb821bfa7bbd003bcbbc56c1ecb6de2589eeeaf"
    assert _sha(without_x86_packed_classification(source)) == "7b204f9eb3af55fdb3e1f2ab8f72e8a5cf965ba9b9ea0c845b541a85f09d212c"
