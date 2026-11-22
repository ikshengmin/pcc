"""Design-review fixtures only: no nursery or runtime backend is enabled."""

import pytest

from tests.python.gc.gc3_sparse_remembered_model import Model


def test_unremembered_writer_is_caught_by_an_independent_reachability_oracle():
    model = Model()
    model.add_old("frame", "frame-card")
    model.add_young("stored", ("child",))
    model.add_young("child")
    # Simulate the raw generator-frame writer that bypasses the owner barrier.
    model.cards["frame-card"]["frame"] = ("stored",)
    assert model.full_reachable_young(("frame",)) == {"stored", "child"}
    assert model.minor(("frame",)).live_young == set()


@pytest.mark.parametrize("clean_count", [32, 128, 512])
def test_minor_does_not_visit_clean_old_heap(clean_count):
    model = Model()
    for i in range(clean_count):
        model.add_old(i, i)
    model.add_old("owner", "dirty-card")
    model.add_young("direct", (0, "nested"))
    model.add_young("nested")
    model.add_young("unreachable")
    model.store_old("dirty-card", "owner", ("direct",))
    expected = model.full_reachable_young(("owner", 0))

    class SparseOnlyCards(dict):
        def no_enumeration(self):
            pytest.fail("minor enumerated the old card table")

        __iter__ = keys = items = values = no_enumeration

    model.cards = SparseOnlyCards(model.cards)
    work = model.minor(("owner", 0))
    assert work.live_young == expected == {"direct", "nested"}
    assert work.cards == ("dirty-card",)
    assert work.old_visits == 1
    assert work.young_visits == 2
    assert work.root_visits == 2


def test_persistent_old_edge_survives_second_minor_without_new_store():
    model = Model()
    model.add_old("frame", "frame-card")
    model.add_young("value")
    model.store_old("frame-card", "frame", ("value",))
    assert model.minor(()).live_young == {"value"}
    assert model.minor(()).live_young == {"value"}
    model.store_old("frame-card", "frame", ())
    cleared = model.minor(())
    assert cleared.live_young == set() and cleared.old_visits == 1
    assert model.minor(()).cards == ()


def test_promotion_seeds_existing_edges_and_preserves_the_old_owner():
    model = Model()
    model.add_young("parent", ("child",))
    model.add_young("child")
    model.promote("parent", "promoted-card")
    assert model.minor(()).live_young == {"child"}
    assert model.minor(()).cards == ("promoted-card",)
    assert model.cards["promoted-card"]["parent"] == ("child",)


def test_queue_failure_forces_major_without_sweeping_young():
    model = Model(queue_capacity=1)
    model.add_old("first", "card1")
    model.add_old("second", "card2")
    model.add_young("a")
    model.add_young("b")
    model.store_old("card1", "first", ("a",))
    model.store_old("card2", "second", ("b",))
    assert model.minor(()).major_required
    assert set(model.young) == {"a", "b"}
