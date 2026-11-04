"""Unresolved Go value plans consume shared c017 storage contracts.

https://go.dev/ref/spec#Interface_types
https://go.dev/ref/spec#Assignability
https://go.dev/ref/spec#Comparison_operators
No model result is a registered object ABI, allocation, dispatch or GC test.
"""
from dataclasses import replace

import pytest

from pcc.frontends.go.interface_values import (
    InterfaceEqualityPlan, boxed_interface_plan, interface_equality_plan,
    interface_identity, nil_interface_plan, transferred_interface_plan,
)
from pcc.frontends.go.records import DefinedType, InterfaceType
from pcc.frontends.go.types import PointerType
from pcc.support.managed_objects import MANAGED_REQUIREMENTS, ManagedContractError
from tests.go.test_interface_methods import TOKEN, interface, method


def test_nil_typed_nil_and_unknown_identity_are_three_different_states():
    target = interface()
    owner = DefinedType('T', 'p', 'int')
    method(owner, pointer=True)
    nil = nil_interface_plan(target)
    typed_nil = boxed_interface_plan(PointerType(owner), target, TOKEN)
    unknown = transferred_interface_plan(target, InterfaceType(()), TOKEN)
    assert nil.nil_state is True and typed_nil.nil_state is False and unknown.nil_state is None
    assert typed_nil.dynamic_type == PointerType(owner)
    assert typed_nil.payload.identity == interface_identity(PointerType(owner))
    assert typed_nil.itable.methods[0].indirect
    assert 'go-receiver-adjustment-and-nil-panic' in typed_nil.unmet
    assert 'go-interface-preserve-dynamic-identity' in unknown.unmet
    assert set(MANAGED_REQUIREMENTS) | {'object_abi'} <= set(typed_nil.unmet)


def test_nominal_identity_survives_boxing_instead_of_layout_equivalence():
    first = DefinedType('T', 'p', 'int')
    second = DefinedType('T', 'p', 'int')
    target = InterfaceType(())
    left = boxed_interface_plan(first, target, TOKEN)
    right = boxed_interface_plan(second, target, TOKEN)
    assert left.dynamic_identity != right.dynamic_identity
    assert left.payload.layout.fixed_size == right.payload.layout.fixed_size
    assert interface_equality_plan(left, right).known_result is False


def test_boxing_does_not_erase_copy_root_or_dynamic_type_requirements():
    owner = DefinedType('T', 'p', 'int')
    method(owner)
    value = boxed_interface_plan(owner, interface(), TOKEN)
    assert value.payload.storage == value.storage.storage == 'unresolved'
    assert {'go-value-copy-semantics', 'go-interface-value-snapshot', 'rooted_lifetime',
            'runtime_descriptor', 'runtime_registration', 'go-typed-owned-dispatch-thunks'} <= set(value.unmet)
    with pytest.raises(ManagedContractError, match='canonical'):
        replace(value, storage=replace(value.storage, requirements=()))
    with pytest.raises(ManagedContractError, match='canonical'):
        replace(value, payload=replace(value.payload, requirements=()))
    with pytest.raises(ManagedContractError, match='dynamic type'):
        replace(value, dynamic_identity=interface_identity('int'))


def test_itable_cannot_crosswire_implementation_or_receiver_projection():
    first = DefinedType('T', 'p', 'int')
    second = DefinedType('T', 'p', 'int')
    method(first)
    alien = method(second)
    value = boxed_interface_plan(first, interface(), TOKEN)
    selection = value.itable.methods[0]
    for bad in (replace(selection, implementation=alien), replace(selection, field_path=(0,)),
                replace(selection, indirect=True)):
        with pytest.raises(ManagedContractError, match='exact dynamic source'):
            replace(value, itable=replace(value.itable, methods=(bad,)))
    with pytest.raises(ManagedContractError, match='every exact'):
        replace(value, itable=replace(value.itable, methods=()))


def test_transfer_checks_static_inclusion_without_boxing_the_interface_type():
    source = interface()
    target = InterfaceType(())
    transferred = transferred_interface_plan(source, target, TOKEN)
    assert transferred.dynamic_state == 'preserve'
    assert transferred.dynamic_type is transferred.payload is transferred.itable is None
    with pytest.raises(ManagedContractError, match='preserve dynamic identity'):
        boxed_interface_plan(source, target, TOKEN)


def test_equality_known_facts_never_discard_evaluation_or_runtime_obligations():
    target = InterfaceType(())
    nil = nil_interface_plan(target)
    concrete = boxed_interface_plan('int', target, TOKEN)
    unknown = transferred_interface_plan(target, target, TOKEN)
    for left, right, expected in ((nil, nil, True), (nil, concrete, False),
                                  (concrete, concrete, None), (nil, unknown, None)):
        plan = interface_equality_plan(left, right)
        assert plan.known_result is expected and plan.evaluate_both
        assert {'go-interface-evaluate-both-operands', 'go-interface-dynamic-comparability-and-panic',
                'go-interface-dynamic-value-equality'} <= set(plan.requirements)
        with pytest.raises(ManagedContractError, match='evaluation'):
            replace(plan, evaluate_both=False)
        with pytest.raises(ManagedContractError, match='obligations'):
            replace(plan, requirements=())
    with pytest.raises(ManagedContractError, match='invent'):
        replace(interface_equality_plan(concrete, concrete), known_result=True)


def test_equality_rejects_incompatible_static_method_sets():
    left = nil_interface_plan(interface('M', result='int'))
    right = nil_interface_plan(interface('M', result='string'))
    with pytest.raises(ManagedContractError, match='compatible static'):
        interface_equality_plan(left, right)


def test_equality_rejects_mutable_or_fabricated_requirement_containers():
    class PretendEqual(list):
        def __eq__(self, other):
            return True

        def __ne__(self, other):
            return False

    nil = nil_interface_plan(InterfaceType(()))
    for invalid in (PretendEqual(), [], ('',), (1,), ('duplicate', 'duplicate')):
        with pytest.raises(ManagedContractError, match='immutable unique'):
            InterfaceEqualityPlan(nil, nil, True, invalid)
