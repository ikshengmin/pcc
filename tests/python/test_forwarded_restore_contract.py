"""Directed cleanup witnesses for promoted ownership forwarded through PHIs.

These bounded IR fixtures exercise the checker, not a native GC execution.
The original mixed fixture and its eighteen checks remain unchanged.
"""
import pytest

from tests.python.promoted_restore_contract import PromotedRestoreOwners
from tests.python.root_slot_contract import RootSlotContract
from tests.python.test_generator_frame_take_group import assert_group_restore_contract
from tests.python.test_generator_operand_root_groups import _parsed_function
from tests.python.test_promoted_restore_contract import _MIXED, _unchecked


def _replace_once(text, before, after):
    assert text.count(before) == 1, "fixture change must target exactly one site"
    return text.replace(before, after)


# Like the handler continuation, the restored flag has no direct branch use.
# Its value reaches cleanup through descendant PHIs instead.
_FORWARDED = _replace_once(
    _MIXED,
    "normal:\n  br i1 %promoted, label %release, label %continue",
    "normal:\n  %handoff = phi i1 [ %promoted, %gen.select ]\n"
    "  br label %guard\n"
    "guard:\n  %forward.guard = phi i1 [ %handoff, %normal ]\n"
    "  br i1 %forward.guard, label %release, label %continue",
)


def test_forwarded_restore_reaches_its_own_guarded_cleanup():
    module, blocks = _parsed_function(_FORWARDED, "resume")
    batches = assert_group_restore_contract(blocks, module.globals_, promoted_owners=True)
    assert list(batches.values()) == [((0, ("outputs", 0)), (1, ("outputs", 8)))]
    owners = PromotedRestoreOwners(RootSlotContract(blocks, module.globals_))
    assert all(kind != "terminator" for kind, _, _, _ in owners.uses["promoted"])
    assert owners.owner("promoted") == ("outputs", 0)
    assert {"promoted", "handoff", "forward.guard"} <= owners.owners.keys()


# The ancestor has a real guarded cleanup. The restored source and ancestor
# meet only at an error-projection PHI, so source cannot reach that guard.
_ANCESTOR_ONLY = _replace_once(
    _MIXED, "gen.dispatch:\n", "gen.dispatch:\n  %ancestor = phi i1 [ 1, %entry ]\n",
)
_ANCESTOR_ONLY = _replace_once(
    _ANCESTOR_ONLY,
    "normal:\n  br i1 %promoted, label %release, label %continue",
    "normal:\n  br i1 %ancestor, label %release, label %continue",
)
_ANCESTOR_ONLY = _replace_once(
    _ANCESTOR_ONLY, "continue:\n  br label %finish", "continue:\n  br label %error",
)
_ANCESTOR_ONLY = _replace_once(
    _ANCESTOR_ONLY,
    "%forward = phi i1 [ %promoted, %gen.select ]",
    "%forward = phi i1 [ %promoted, %gen.select ], [ %ancestor, %continue ]",
)


@pytest.mark.parametrize("prime_cache", (False, True))
def test_ancestor_guard_cannot_validate_or_cache_an_unwitnessed_restore(prime_cache):
    module, blocks = _parsed_function(_ANCESTOR_ONLY, "resume")
    owners = PromotedRestoreOwners(RootSlotContract(blocks, module.globals_))
    owners.assert_phi_predecessors()
    if prime_cache:
        assert owners.owner("ancestor") == ("outputs", 0)
        assert "promoted" not in owners.owners, "cache needs a directed witness for each value"
    with pytest.raises(AssertionError, match="forward guarded cleanup witness"):
        owners.owner("promoted")
    with pytest.raises(AssertionError, match="forward guarded cleanup witness"):
        assert_group_restore_contract(blocks, module.globals_, promoted_owners=True)


@pytest.mark.parametrize("before, after, diagnostic", [
    ("release:\n  call void @pcc_gc_store_root(ptr %cell0, ptr null)",
     "release:\n  call void @pcc_gc_store_root(ptr %cell1, ptr null)",
     "exactly one physical output"),
    ("%forward.guard = phi i1 [ %handoff, %normal ]",
     "%forward.guard = phi i1 [ 1, %normal ]", "forward guarded cleanup witness"),
    ("%forward.guard = phi i1 [ %handoff, %normal ]",
     "%forward.guard = phi i1 [ %handoff, %entry ]", "forward guarded cleanup witness"),
])
def test_forwarded_restore_rejects_wrong_cell_or_disconnected_cleanup(before, after, diagnostic):
    module, blocks = _parsed_function(_FORWARDED, "resume")
    assert_group_restore_contract(blocks, module.globals_, promoted_owners=True)
    module, blocks = _unchecked(_replace_once(_FORWARDED, before, after))
    with pytest.raises(AssertionError, match=diagnostic):
        assert_group_restore_contract(blocks, module.globals_, promoted_owners=True)


@pytest.mark.parametrize("prime_cache", (False, True))
def test_true_phi_ancestor_guard_does_not_validate_its_projection_only_descendant(prime_cache):
    module, blocks = _parsed_function(_MIXED, "resume")
    owners = PromotedRestoreOwners(RootSlotContract(blocks, module.globals_))
    owners.assert_phi_predecessors()
    assert [item.value for item in owners.phis["forward"][1].incoming] == ["promoted"]
    if prime_cache:
        assert owners.owner("promoted") == ("outputs", 0)
        assert "forward" not in owners.owners, "an ancestor's guard cannot validate its descendant"
    with pytest.raises(AssertionError, match="forward guarded cleanup witness"):
        owners.owner("forward")
