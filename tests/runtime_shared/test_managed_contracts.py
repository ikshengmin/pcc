"""Compiler plans, not managed-runtime or all-five-GC acceptance; all UNRUN.

The language meaning is retained by adapters: JLS 4.3/10/15.21.3 and Go
Types, String_types and Interface_types. This owner checks only explicit
storage, root-role and extent facts; it does not implement these languages.
https://docs.oracle.com/javase/specs/jls/se21/html/jls-4.html#jls-4.3
https://go.dev/ref/spec#Types
The existing runtime slot contract distinguishes roles 1/2/3; no new ABI.
"""
from dataclasses import FrozenInstanceError

import pytest

from pcc.support.managed_objects import (
    MAX_LAYOUT_BYTES,
    ManagedContractError,
    ManagedLayoutPlan,
    ManagedSpanPlan,
    ManagedTypeIdentity,
    ManagedValuePlan,
    ReferenceEdgePlan,
    ScalarFieldPlan,
    allocation_extent,
    pointer_map,
    unmet_requirements,
)


TYPE = ManagedTypeIdentity('test', ('original',), 'Record')


def test_nominal_declaration_identity_is_not_underlying_or_spelling_identity():
    first, second = object(), object()
    a = ManagedTypeIdentity('go', ('pkg',), 'T', first)
    b = ManagedTypeIdentity('go', ('pkg',), 'T', second)
    assert a != b and a == ManagedTypeIdentity('go', ('pkg',), 'T', first)
    assert a != ManagedTypeIdentity('java', ('pkg',), 'T', first)
    with pytest.raises(FrozenInstanceError):
        a.key = 'Other'


def test_precise_payload_maps_keep_scalar_and_all_reference_roles_distinct():
    layout = ManagedLayoutPlan(TYPE, 40, 8,
        scalars=(ScalarFieldPlan(('n',), 'i32', 0, 4, 4),
                 ScalarFieldPlan(('type',), 'immutable_metadata', 8, 8, 8)),
        references=(ReferenceEdgePlan(('child',), role='owned', offset=16),
                    ReferenceEdgePlan(('metadata',), role='borrowed', offset=24),
                    ReferenceEdgePlan(('weak',), role='update_only', offset=32)))
    resolved = pointer_map(layout, pointer_size=8)
    assert (resolved.owned, resolved.borrowed, resolved.update_only) == ((16,), (24,), (32,))
    assert resolved.element is None
    # Resolving a payload map is not descriptor registration or ABI admission.
    value = ManagedValuePlan(TYPE, 'managed', layout)
    assert {'runtime_registration', 'object_abi', 'rooted_lifetime'} <= set(unmet_requirements(value))


def test_primitive_and_reference_array_element_maps_are_not_interchangeable():
    primitive = ManagedLayoutPlan(TYPE, 4, 4, scalars=(ScalarFieldPlan(('value',), 'i32', 0, 4, 4),))
    reference = ManagedLayoutPlan(TYPE, 8, 8, references=(ReferenceEdgePlan(('value',), offset=0),))
    a = ManagedLayoutPlan(TYPE, 16, 8, element=primitive, element_stride=4)
    b = ManagedLayoutPlan(TYPE, 16, 8, element=reference, element_stride=8)
    assert pointer_map(a, pointer_size=8).element.owned == ()
    assert pointer_map(b, pointer_size=8).element.owned == (0,)
    assert allocation_extent(a, 3) == 28
    assert allocation_extent(b, 3) == 40
    assert allocation_extent(b, 0) == 16


@pytest.mark.parametrize('layout,width', [
    (ManagedLayoutPlan(TYPE), 8),
    (ManagedLayoutPlan(TYPE, 8, 8, references=(ReferenceEdgePlan(('p',)),)), 8),
    (ManagedLayoutPlan(TYPE, 8, 8, scalars=(ScalarFieldPlan(('n',), 'int'),)), 8),
    (ManagedLayoutPlan(TYPE, 8, 8, references=(ReferenceEdgePlan(('p',), offset=4),)), 8),
    (ManagedLayoutPlan(TYPE, 8, 8, references=(ReferenceEdgePlan(('p',), offset=8),)), 8),
    (ManagedLayoutPlan(TYPE, 8, 8, scalars=(ScalarFieldPlan(('n',), 'i64', 0, 8, 8),),
                       references=(ReferenceEdgePlan(('p',), offset=0),)), 8),
    (ManagedLayoutPlan(TYPE, 8, 8), 0),
    (ManagedLayoutPlan(TYPE, 8, 8), True),
    (ManagedLayoutPlan(TYPE, 8, 8, element=ManagedLayoutPlan(TYPE, 16, 8), element_stride=8), 8),
])
def test_unknown_overlapping_misaligned_and_out_of_extent_layouts_cannot_make_maps(layout, width):
    with pytest.raises(ManagedContractError):
        pointer_map(layout, pointer_size=width)


def test_pointer_width_is_explicit_not_the_host_word_size():
    layout = ManagedLayoutPlan(TYPE, 4, 4, references=(ReferenceEdgePlan(('p',), offset=0),))
    assert pointer_map(layout, pointer_size=4).owned == (0,)
    with pytest.raises(ManagedContractError):
        pointer_map(layout, pointer_size=8)


def test_repeated_extent_does_not_hide_unknown_or_nested_variable_size_elements():
    leaf = ManagedLayoutPlan(TYPE, 8, 8)
    repeated = ManagedLayoutPlan(TYPE, 8, 8, element=leaf, element_stride=8)
    nested = ManagedLayoutPlan(TYPE, 8, 8, element=repeated, element_stride=8)
    for layout in (ManagedLayoutPlan(TYPE, 8, 8, element=ManagedLayoutPlan(TYPE), element_stride=8),
                   ManagedLayoutPlan(TYPE, 8, 8, element=leaf, element_stride=4), nested):
        with pytest.raises(ManagedContractError):
            allocation_extent(layout, 2)
    with pytest.raises(ManagedContractError, match='nested'):
        pointer_map(nested, pointer_size=8)


@pytest.mark.parametrize('count', [-1, True, None, MAX_LAYOUT_BYTES])
def test_repeated_extent_rejects_unresolved_or_overflow_count(count):
    layout = ManagedLayoutPlan(TYPE, 16, 8, element=ManagedLayoutPlan(TYPE, 8, 8), element_stride=8)
    with pytest.raises(ManagedContractError):
        allocation_extent(layout, count)


@pytest.mark.parametrize('factory', [
    lambda: ScalarFieldPlan(('n',), 'int', size=0),
    lambda: ScalarFieldPlan(('n',), 'int', alignment=3),
    lambda: ReferenceEdgePlan(('p',), role='raw'),
    lambda: ReferenceEdgePlan(('p',), nullable=1),
    lambda: ManagedLayoutPlan(TYPE, scalars=[ScalarFieldPlan(('n',), 'int')]),
    lambda: ManagedLayoutPlan(TYPE, scalars=(ScalarFieldPlan(('same',), 'int'),),
                              references=(ReferenceEdgePlan(('same',)),)),
    lambda: ManagedLayoutPlan(TYPE, element_stride=8),
    lambda: ManagedLayoutPlan(TYPE, element=ManagedLayoutPlan(TYPE), element_stride=0),
    lambda: ManagedValuePlan(TYPE, requirements=('rooted_lifetime', 'rooted_lifetime')),
    lambda: ManagedValuePlan(TYPE, layout=ManagedLayoutPlan(ManagedTypeIdentity('test', (), 'Other'))),
])
def test_malformed_contracts_fail_before_any_consumer(factory):
    with pytest.raises(ManagedContractError):
        factory()


def test_static_bytes_and_dynamic_owner_relative_spans_remain_distinct():
    static = ManagedSpanPlan('static', offset=0, length=3)
    owner = ManagedValuePlan(TYPE, 'managed', requirements=('foreign_transition',))
    dynamic = ManagedSpanPlan('managed', owner, 1, 2)
    assert static.owner is None and dynamic.owner is owner
    assert 'foreign_transition' in unmet_requirements(owner)
    assert 'runtime_descriptor' in unmet_requirements(owner)
    assert unmet_requirements(ManagedValuePlan(TYPE, 'static')) == ()
    with pytest.raises(ManagedContractError):
        ManagedSpanPlan('managed', None, 0, 1)
    with pytest.raises(ManagedContractError):
        ManagedSpanPlan('managed', ManagedValuePlan(TYPE, 'static'), 0, 1)
    with pytest.raises(ManagedContractError):
        ManagedSpanPlan('static', offset=MAX_LAYOUT_BYTES, length=1)
