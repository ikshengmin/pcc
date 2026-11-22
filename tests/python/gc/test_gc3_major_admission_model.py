"""Ordering controls for nursery handoff; no runtime logic is under test."""

from tests.python.gc.gc3_sparse_remembered_model import MajorAdmissionModel


def test_major_cannot_seed_between_nursery_reservation_and_publication():
    gc = MajorAdmissionModel()
    gc.reserve("child")
    gc.publish()
    gc.reserve("root", ("child",))
    assert not gc.close_nursery()
    gc.publish()
    assert gc.close_nursery()
    assert not gc.young
    gc.seed(("root",))
    assert gc.finish_mark() == frozenset()
    assert gc.marked == {"root", "child"}


def test_old_publication_during_mark_protects_new_object_and_its_edge():
    gc = MajorAdmissionModel()
    for name in ("late-child", "unreachable"):
        gc.reserve(name)
        gc.publish()
    assert gc.close_nursery()
    gc.seed(())
    gc.reserve("born-black", ("late-child",))
    gc.publish()
    assert not gc.young
    assert gc.marked == {"born-black", "late-child"}
    assert gc.finish_mark() == {"unreachable"}


def test_nursery_stays_closed_through_handoff_and_sweep():
    gc = MajorAdmissionModel()
    gc.reserve("unreachable")
    gc.publish()
    assert gc.close_nursery()
    gc.reserve("handoff-old")
    gc.publish()
    gc.seed(("handoff-old",))
    candidates = gc.finish_mark()
    gc.reserve("sweep-old")
    gc.publish()
    assert not gc.young
    assert candidates == {"unreachable"}
    gc.finish_sweep(candidates)
    assert set(gc.old) == {"handoff-old", "sweep-old"}
    gc.reserve("next-young")
    gc.publish()
    assert set(gc.young) == {"next-young"}
