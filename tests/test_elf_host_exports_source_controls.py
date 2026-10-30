"""Authenticate the later export delta without repinning historical proofs.

Only source restoration runs: no PCC import, linker, compiler or executable.
"""

import hashlib
from pathlib import Path

import pytest

from tests.elf_host_exports_preimage import (
    BASE_OWNER_SHA256,
    OWNER_REVERSE_EDITS,
    OWNER_SHA256,
    without_host_exports,
)
from tests.elf_x86_packed_application_reference import (
    _without_aarch64_jump26_veneers,
    _without_native_scalar_writes,
    without_x86_packed_application,
)


def _source():
    owner = Path(__file__).resolve().parents[1] / "pcc/backend/elf_x86_64.py"
    source = owner.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert "\r" not in source
    return source


def _sha(source):
    return hashlib.sha256(source.encode()).hexdigest()


def test_export_inverse_reconstructs_exact_parent_and_round_trips():
    source = _source()
    assert _sha(source) == OWNER_SHA256
    restored = without_host_exports(source)
    assert restored != source
    assert _sha(restored) == BASE_OWNER_SHA256
    assert without_host_exports(restored) == restored
    # All four historical diff hunks, including both public/private entries,
    # reconstruct the complete current owner rather than only the loop AST.
    assert len(OWNER_REVERSE_EDITS) == 4
    assert sum(changed.count("\n") - original.count("\n")
               for changed, original in OWNER_REVERSE_EDITS) == 54
    for changed, original in OWNER_REVERSE_EDITS:
        assert restored.count(original) == 1
        restored = restored.replace(original, changed)
    assert restored == source


@pytest.mark.parametrize("hunk", range(4))
def test_every_export_hunk_is_exactly_required(hunk):
    source = _source()
    changed, original = OWNER_REVERSE_EDITS[hunk]
    assert source.count(changed) == 1
    for replacement in (original, changed + "# undeclared hunk drift\n"):
        drift = source.replace(changed, replacement)
        assert drift != source
        with pytest.raises(AssertionError):
            without_host_exports(drift)
        with pytest.raises(AssertionError):
            without_x86_packed_application(drift)


@pytest.mark.parametrize("fault", ("unrelated", "marker", "application"))
def test_export_adapter_does_not_admit_unreviewed_owner_drift(fault):
    source = _source()
    if fault == "unrelated":
        drift = source + "\n# unrelated owner drift\n"
    elif fault == "marker":
        # Removing the adapter's dispatch marker cannot bypass older hashes.
        drift = source.replace("export_symbols", "changed_export_names")
    else:
        drift = source.replace("target + addend, place", "target - addend, place", 1)
    assert drift != source
    with pytest.raises(AssertionError):
        without_host_exports(drift)
    with pytest.raises(AssertionError):
        without_x86_packed_application(drift)


def test_exports_extend_the_chain_without_changing_old_owner_hashes():
    source = _source()
    jump_preimage = _without_aarch64_jump26_veneers(source)
    assert _sha(jump_preimage) == "ba10325e3646ed8b4f101ce8af15294f39fc7d8f5fdb00988808bf6c5ca81228"
    scalar_preimage = _without_native_scalar_writes(jump_preimage)
    assert _sha(scalar_preimage) == "f2476a2c75257d5a051b5d5af21b82a1d19be53096de28a3267f81cf4680d20f"
    assert _sha(without_x86_packed_application(source)) == "3b85ba9dde549bb6b2a1d272657a6efc02c58277d8059c7a77df120871c66929"
