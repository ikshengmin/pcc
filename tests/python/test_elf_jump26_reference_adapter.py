"""Bounded source restoration only; no compiler or emitted executable runs."""
import hashlib
from pathlib import Path

import pytest

from pcc.backend import elf_x86_64 as elf
from tests.elf_host_exports_preimage import without_host_exports
from tests.elf_x86_packed_application_reference import (
    _AARCH64_JUMP26_REVERSE_EDITS,
    _without_aarch64_jump26_veneers,
    _without_native_scalar_writes,
    without_x86_packed_application,
)
from tests.elf_arm_branch_slot_reference import without_arm_branch_slot_defaults
from tests.elf_x86_packed_classification_reference import without_x86_packed_classification


def _source():
    # These controls isolate JUMP26 from the separately pinned later exports.
    return without_host_exports(Path(elf.__file__).read_text(encoding="utf-8"))


def _sha(source):
    return hashlib.sha256(source.encode()).hexdigest()


def test_exact_jump_inverse_restores_authenticated_immediate_owner():
    source = _source()
    restored = _without_aarch64_jump26_veneers(source)
    assert restored != source
    assert _sha(restored) == "ba10325e3646ed8b4f101ce8af15294f39fc7d8f5fdb00988808bf6c5ca81228"
    # Reapply all exact forward hunks: the entire current owner is recovered.
    for changed, original in _AARCH64_JUMP26_REVERSE_EDITS:
        assert restored.count(original) == 1
        restored = restored.replace(original, changed)
    assert restored == source


def test_historical_owner_passes_through_jump_adapter_unchanged():
    restored = _without_aarch64_jump26_veneers(_source())
    assert _without_aarch64_jump26_veneers(restored) == restored
    historical = without_x86_packed_application(restored)
    assert without_x86_packed_application(historical) == historical


@pytest.mark.parametrize("hunk", list(range(10)))
def test_each_declared_jump_hunk_is_exactly_required(hunk):
    source = _source()
    changed, _original = _AARCH64_JUMP26_REVERSE_EDITS[hunk]
    assert source.count(changed) == 1
    drift = source.replace(changed, changed.replace("\n", " # undeclared drift\n", 1))
    assert drift != source
    with pytest.raises(AssertionError):
        without_x86_packed_application(drift)


@pytest.mark.parametrize("fault", ["unrelated", "marker", "operation"])
def test_partial_or_unrelated_owner_drift_is_not_accepted(fault):
    source = _source()
    if fault == "unrelated":
        drift = source + "\n# unrelated owner drift\n"
    elif fault == "marker":
        drift = source.replace("def _aarch64_veneer_branch(kind, ordinary_record):", "def _changed_branch(kind, ordinary_record):")
    else:
        drift = source.replace("target + addend, place", "target - addend, place", 1)
    assert drift != source
    with pytest.raises(AssertionError):
        without_x86_packed_application(drift)


def test_all_historical_exact_hashes_remain_unchanged():
    source = _source()
    restored = _without_aarch64_jump26_veneers(source)
    assert _sha(_without_native_scalar_writes(restored)) == "f2476a2c75257d5a051b5d5af21b82a1d19be53096de28a3267f81cf4680d20f"
    assert _sha(without_x86_packed_application(source)) == "3b85ba9dde549bb6b2a1d272657a6efc02c58277d8059c7a77df120871c66929"
    assert _sha(without_arm_branch_slot_defaults(source)) == "411959c27c76d984d2ccff427fb821bfa7bbd003bcbbc56c1ecb6de2589eeeaf"
    # The older classification helper also pins its complete reconstructed owner.
    assert without_x86_packed_classification(source) != source
