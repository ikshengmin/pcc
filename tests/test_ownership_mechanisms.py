"""Shared place/may-flow mechanisms, not a second language ownership policy."""
from pcc.analysis.ownership import (
    Field,
    Index,
    LoopEdges,
    Place,
    clone,
    join,
    restrict,
    solve_loop,
)


def test_root_and_projection_identity_are_structural():
    root = Place("local")
    field = root.project(Field(0))
    element = root.project(Index(0))
    nested = field.project(Index(2)).project(Field("payload"))
    assert len({root, field, element, nested, Place("other")}) == 5
    assert field == Place("local", (Field(0),))
    assert nested.path == (Field(0), Index(2), Field("payload"))


def test_overlap_is_symmetric_ancestry_not_sibling_or_alias_proof():
    root = Place(1)
    field = root.project(Field(0))
    nested = field.project(Field(1))
    sibling = root.project(Field(1))
    for left, right in ((root, field), (field, nested), (root, nested), (field, field)):
        assert left.overlaps(right) and right.overlaps(left)
    assert not field.overlaps(sibling)
    assert not field.overlaps(Place(2, field.path))
    assert root.contains(nested) and not nested.contains(root)
    # This is only structural identity. No claim about two aliased pointer roots.


def test_index_identity_does_not_conflate_fields_or_distinct_elements():
    first = Place("array").project(Index(0))
    second = Place("array").project(Index(1))
    assert not first.overlaps(second)
    assert not first.overlaps(Place("array").project(Field(0)))
    assert first.overlaps(first.project(Field("member")))


def test_clone_does_not_share_mutable_fact_sets():
    original = {1: {(Field(0),)}, 2: set()}
    copied = clone(original)
    copied[1].add((Index(1),))
    copied[2].add(())
    copied[3] = set()
    assert original == {1: {(Field(0),)}, 2: set()}


def test_join_distinguishes_unreachable_and_reachable_empty_state():
    assert join([]) is None
    assert join([{}]) == {}
    assert join([{1: set()}, {}]) == {1: set()}


def test_join_unions_all_roots_without_aliasing_inputs():
    left = {1: {(Field(0),)}}
    right = {1: {(Field(1),)}, 2: {()}}
    merged = join([left, right])
    assert merged == {1: {(Field(0),), (Field(1),)}, 2: {()}}
    merged[1].clear()
    merged[2].clear()
    assert left == {1: {(Field(0),)}}
    assert right == {1: {(Field(1),)}, 2: {()}}


def test_union_is_idempotent_commutative_and_associative():
    a, b, c = {1: {()}}, {1: {(Field(0),)}}, {2: {(Index(1),)}}
    assert join([a, a]) == a
    assert join([a, b]) == join([b, a])
    assert join([join([a, b]), c]) == join([a, join([b, c])])


def test_scope_projection_drops_local_roots_but_copies_visible_facts():
    state = {"outer": {()}, "local": {(Field(0),)}}
    outer = restrict(state, {"outer", "not_declared"})
    assert outer == {"outer": {()}}
    outer["outer"].clear()
    assert state["outer"] == {()}


def test_loop_edge_buckets_are_independent_and_scope_projected():
    first, second = LoopEdges({"outer"}), LoopEdges({"outer"})
    source = {"outer": {()}, "local": {()}}
    first.breaks.append(first.outer(source))
    first.continues.append(first.outer(source))
    source["outer"].clear()
    assert first.breaks == first.continues == [{"outer": {()}}]
    assert second.breaks == second.continues == []


def test_loop_revisits_until_backedge_facts_stabilize():
    entry = {"item": set()}
    first, second = (Field("first"),), (Field("second"),)
    headers = []

    def transfer(header):
        headers.append(clone(header))
        back = clone(header)
        back["item"].add(first)
        if first in header["item"]:
            back["item"].add(second)
        return [back], [header]

    assert solve_loop(entry, transfer) == {"item": {first, second}}
    assert headers == [{"item": set()}, {"item": {first}}, {"item": {first, second}}]
    assert entry == {"item": set()}


def test_loop_joins_all_backedges_and_only_stable_iteration_exits():
    transient = (Field("transient"),)
    entry = {"root": set()}

    def transfer(header):
        exits = [{"root": {transient}}] if not header["root"] else [header]
        return [{"root": {(Index(0),)}}, {"root": {(Index(1),)}}], exits

    assert solve_loop(entry, transfer) == {"root": {(Index(0),), (Index(1),)}}


def test_loop_unreachable_exit_and_callback_mutation_do_not_change_entry():
    entry = {"root": set()}

    def transfer(header):
        header["root"].add(())
        return [], []

    assert solve_loop(entry, transfer) is None
    assert entry == {"root": set()}


def test_synthetic_escape_facts_use_same_solver_without_copy_or_borrow_rules():
    # Policy-separation example only: no Python IR integration or escape proof.
    escaped = Place("allocation").project(Field("child")).project(Index(3))
    entry = {escaped.root: set()}

    def mark_escape(header):
        header[escaped.root].add(escaped.path)
        return [header], [header]

    assert solve_loop(entry, mark_escape) == {escaped.root: {escaped.path}}
