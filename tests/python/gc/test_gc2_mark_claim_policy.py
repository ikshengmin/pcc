"""Serialized specification of GC2 whole-owner work, not a runtime pool."""
from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class Claim:
    epoch: int
    worker: int
    serial: int
    owner: str


class MarkEpoch:
    def __init__(self, epoch, slots=2):
        self.epoch = epoch
        self.slots = slots
        self.serial = 0
        self.hidden = set()
        self.ready = set()
        self.inflight = {}
        self.scanned = set()

    def shade(self, owner):
        # Credit starts at reservation, not just when the producer's batch
        # becomes visible. Re-shading an existing owner adds no credit.
        if (owner not in self.hidden and owner not in self.ready
                and owner not in self.scanned
                and all(claim.owner != owner for claim in self.inflight.values())):
            self.hidden.add(owner)

    def publish(self, owner):
        self.hidden.remove(owner)
        self.ready.add(owner)

    def claim(self, worker):
        if not self.ready or worker in self.inflight or len(self.inflight) == self.slots:
            return None
        owner = min(self.ready)
        self.ready.remove(owner)
        self.serial += 1
        claim = Claim(self.epoch, worker, self.serial, owner)
        self.inflight[worker] = claim
        return claim

    def retire(self, claim, referents=(), *, retry=False):
        if claim.epoch != self.epoch or self.inflight.get(claim.worker) != claim:
            return False
        if retry:
            self.ready.add(claim.owner)
        else:
            # Retire the parent's credit only after its output is reserved.
            for referent in referents:
                self.shade(referent)
            self.scanned.add(claim.owner)
        del self.inflight[claim.worker]
        return True

    def can_request_final_remark(self):
        return not (self.hidden or self.ready or self.inflight)


@pytest.mark.parametrize("finish_order", [(0, 1), (1, 0)])
def test_two_workers_cannot_retire_the_same_owner_in_either_order(finish_order):
    epoch = MarkEpoch(1)
    for owner in ("left", "right", "overflow"):
        epoch.shade(owner)
        epoch.publish(owner)
    claims = [epoch.claim(0), epoch.claim(1)]
    assert claims[0].owner != claims[1].owner
    assert epoch.claim(0) is None
    assert epoch.claim(2) is None
    for i in finish_order:
        assert epoch.retire(claims[i], ("left", "right", "overflow"))
        assert not epoch.retire(claims[i])
    assert not epoch.can_request_final_remark()
    remaining = epoch.claim(2)
    assert epoch.retire(remaining)
    assert epoch.scanned == {"left", "right", "overflow"}
    assert epoch.can_request_final_remark()


def test_empty_visible_queue_with_private_tail_or_scan_cannot_request_finish():
    epoch = MarkEpoch(1)
    epoch.shade("root")
    assert epoch.claim(0) is None and not epoch.can_request_final_remark()
    epoch.publish("root")
    root = epoch.claim(0)
    assert epoch.claim(1) is None and not epoch.can_request_final_remark()
    assert epoch.retire(root, ("child", "child"))
    assert epoch.claim(1) is None and not epoch.can_request_final_remark()
    epoch.publish("child")
    assert epoch.retire(epoch.claim(1), ("root",))
    assert epoch.can_request_final_remark()
    # A concurrent empty check requests STW remark, not permission to sweep.
    epoch.shade("late-root")
    assert not epoch.can_request_final_remark()


def test_abandoned_owner_is_requeued_and_old_epoch_result_cannot_clear_new_work():
    old = MarkEpoch(1)
    old.shade("owner")
    old.publish("owner")
    stale = old.claim(0)
    assert old.retire(stale)
    assert old.can_request_final_remark()

    new = MarkEpoch(2)
    new.shade("owner")
    new.publish("owner")
    current = new.claim(0)
    assert not new.retire(stale, ("wrong-epoch-child",))
    assert new.retire(current, retry=True)
    assert not new.can_request_final_remark()
    retried = new.claim(0)
    assert retried.owner == current.owner
    assert not new.retire(current)
    assert new.retire(retried)
    assert new.scanned == {"owner"}
    assert new.can_request_final_remark()
