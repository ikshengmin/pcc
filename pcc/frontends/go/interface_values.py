"""Language-owned interface plans over the shared compiler contract owner.

These are not values, an itable layout or native runtime availability. A nil
interface, a concrete dynamic type with a possibly null pointer payload, and
preserved runtime dynamic identity are three different states. Managed boxes,
dispatch, equality/panic and root/barrier operations remain required capabilities.
"""
from dataclasses import dataclass

from pcc.support.managed_objects import (
    ManagedContractError, ManagedLayoutPlan, ManagedTypeIdentity, ManagedValuePlan,
    ReferenceEdgePlan, ScalarFieldPlan, unmet_requirements,
)
from pcc.frontends.go.interface_methods import (
    MethodSelection, interface_type, member_key, method_set, require_implements,
)
from pcc.frontends.go.managed_objects import type_identity, value_plan
from pcc.frontends.go.records import DefinedType, InterfaceMethod, MethodSignature, StructType, underlying
from pcc.frontends.go.types import ArrayType, PointerType, SliceType, is_error_interface, result_types


def interface_identity(dtype):
    """Canonical semantic identity; no address or physical runtime type ID."""
    if isinstance(dtype, DefinedType):
        return type_identity(dtype)
    if is_error_interface(dtype):
        # Preserve the common adapter's existing structural error identity.
        return type_identity(dtype)
    owner = interface_type(dtype)
    if owner is not None:
        methods = tuple((member_key(method.name, method.package),
                         tuple(interface_identity(t) for t in method.signature.parameters),
                         tuple(interface_identity(t) for t in result_types(method.signature.result)),
                         method.signature.variadic) for method in owner.methods)
        return ManagedTypeIdentity('go', ('structural-interface',), 'basic-interface',
                                   declaration=methods)
    if isinstance(dtype, PointerType):
        return ManagedTypeIdentity('go', ('structural',), 'pointer',
                                   declaration=(interface_identity(dtype.element),))
    if isinstance(dtype, SliceType):
        return ManagedTypeIdentity('go', ('structural',), 'slice',
                                   declaration=(interface_identity(dtype.element),))
    if isinstance(dtype, ArrayType):
        return ManagedTypeIdentity('go', ('structural',), 'array',
                                   declaration=(dtype.count, interface_identity(dtype.element)))
    if isinstance(dtype, StructType):
        fields = tuple((f.name, f.package, f.embedded, interface_identity(f.type)) for f in dtype.fields)
        return ManagedTypeIdentity('go', ('structural',), 'struct', declaration=fields)
    return type_identity(dtype)


def interface_storage_plan(dtype):
    owner = interface_type(dtype)
    if owner is None:
        raise ManagedContractError('interface storage requires a complete static interface')
    if is_error_interface(dtype):
        return value_plan(dtype)
    identity = interface_identity(dtype)
    layout = ManagedLayoutPlan(identity,
        scalars=(ScalarFieldPlan(('dynamic-type-and-method-table',), 'immutable-metadata'),),
        references=(ReferenceEdgePlan(('dynamic-value',)),))
    return ManagedValuePlan(identity, 'unresolved', layout,
        ('go-interface-value-abi', 'go-interface-dynamic-type-and-method-table',
         'go-interface-typed-nil-and-equality', 'go-interface-box-copy'))


@dataclass(frozen=True)
class ITablePlan:
    dynamic_identity: ManagedTypeIdentity
    target_identity: ManagedTypeIdentity
    methods: tuple

    def __post_init__(self):
        if (not isinstance(self.dynamic_identity, ManagedTypeIdentity)
                or not isinstance(self.target_identity, ManagedTypeIdentity)
                or not isinstance(self.methods, tuple)
                or any(not isinstance(method, MethodSelection) for method in self.methods)):
            raise ManagedContractError('itable requires immutable typed method selections and identities')
        for selected in self.methods:
            method = selected.method
            if (not isinstance(method, InterfaceMethod) or not isinstance(method.name, str)
                    or not method.name or not isinstance(method.package, str)
                    or not isinstance(method.signature, MethodSignature)
                    or not isinstance(method.signature.parameters, tuple)
                    or type(method.signature.variadic) is not bool
                    or not isinstance(selected.field_path, tuple)
                    or any(type(index) is not int or index < 0 for index in selected.field_path)
                    or type(selected.indirect) is not bool):
                raise ManagedContractError('itable receiver paths and signatures must be immutable typed metadata')


@dataclass(frozen=True)
class InterfaceValuePlan:
    static_type: object
    storage: ManagedValuePlan
    dynamic_state: str
    dynamic_type: object = None
    dynamic_identity: ManagedTypeIdentity | None = None
    payload: ManagedValuePlan | None = None
    itable: ITablePlan | None = None

    def __post_init__(self):
        if (interface_type(self.static_type) is None or not isinstance(self.storage, ManagedValuePlan)
                or self.storage.identity != interface_identity(self.static_type)):
            raise ManagedContractError('interface plan requires its exact static interface identity')
        if self.storage.storage != 'unresolved':
            raise ManagedContractError('interface value ABI must remain unresolved in this compiler owner')
        if self.storage != interface_storage_plan(self.static_type):
            raise ManagedContractError('interface storage must retain its canonical layout and language requirements')
        if self.dynamic_state not in {'nil', 'concrete', 'preserve'}:
            raise ManagedContractError('interface dynamic state must not conflate unknown and nil')
        fields = (self.dynamic_type, self.dynamic_identity, self.payload, self.itable)
        if self.dynamic_state != 'concrete':
            if any(field is not None for field in fields):
                raise ManagedContractError('nil/preserved interface cannot invent a concrete dynamic type')
        elif (any(field is None for field in fields)
              or not isinstance(self.dynamic_identity, ManagedTypeIdentity)
              or not isinstance(self.payload, ManagedValuePlan) or not isinstance(self.itable, ITablePlan)
              or interface_type(self.dynamic_type) is not None
              or self.dynamic_identity != interface_identity(self.dynamic_type)
              or self.payload.identity != self.dynamic_identity
              or self.itable.dynamic_identity != self.dynamic_identity
              or self.itable.target_identity != self.storage.identity):
            raise ManagedContractError('concrete interface plan loses dynamic type or payload identity')
        if self.payload is not None and self.payload != _payload_plan(self.dynamic_type):
            raise ManagedContractError('interface payload must retain its canonical layout and language requirements')
        if self.itable is not None:
            required_methods = interface_type(self.static_type).methods
            selected = tuple(item.method for item in self.itable.methods)
            if (len(required_methods) != len(selected)
                    or any(member_key(want.name, want.package) != member_key(got.name, got.package)
                           or want.signature != got.signature for want, got in zip(required_methods, selected))):
                raise ManagedContractError('itable must retain every exact target method signature')
            actual = method_set(self.dynamic_type)
            for required, supplied in zip(required_methods, self.itable.methods):
                canonical = actual.lookup(required.name, required.package)
                if (canonical is None or canonical.method.signature != supplied.method.signature
                        or canonical.implementation is not supplied.implementation
                        or canonical.field_path != supplied.field_path
                        or canonical.indirect is not supplied.indirect):
                    raise ManagedContractError('itable selection must belong to the exact dynamic source type')

    @property
    def nil_state(self):
        return True if self.dynamic_state == 'nil' else False if self.dynamic_state == 'concrete' else None

    @property
    def unmet(self):
        required = list(unmet_requirements(self.storage))
        if self.payload is not None:
            required.extend(unmet_requirements(self.payload))
        if self.dynamic_state == 'concrete':
            required.extend(('go-interface-value-snapshot', 'go-typed-owned-dispatch-thunks'))
            if any(method.implementation is None for method in self.itable.methods):
                required.append('go-promoted-interface-dispatch-thunks')
            if any(method.indirect or method.implementation is None for method in self.itable.methods):
                # Receiver projections or value-method wrappers can load through
                # a null pointer/interface; membership is not a nonnil proof.
                required.append('go-receiver-adjustment-and-nil-panic')
        elif self.dynamic_state == 'preserve':
            required.append('go-interface-preserve-dynamic-identity')
        return tuple(dict.fromkeys(required))


def nil_interface_plan(target):
    return InterfaceValuePlan(target, interface_storage_plan(target), 'nil')


def boxed_interface_plan(dtype, target, token):
    """Plan a copied concrete value, never a nested box of an interface value."""
    if interface_type(dtype) is not None:
        raise ManagedContractError('interface conversion must preserve dynamic identity, not box its static type')
    actual = require_implements(dtype, target, token)
    payload = _payload_plan(dtype)
    storage = interface_storage_plan(target)
    required = interface_type(target).methods
    selections = tuple(actual.lookup(method.name, method.package) for method in required)
    table = ITablePlan(payload.identity, storage.identity, selections)
    # Even a known-null pointer has its concrete dynamic pointer type. The
    # payload is copied as a pointer value, not a copied pointee or nil interface.
    return InterfaceValuePlan(target, storage, 'concrete', dtype,
                              payload.identity, payload, table)


def _payload_plan(dtype):
    """Keep new structural interface components unresolved, never untraced."""
    seen = set()

    def has_general_interface(item):
        if isinstance(item, DefinedType):
            # Nominal identity can be known while its underlying payload shape
            # is still outside the bounded common layout adapter.
            if item in seen:
                return False
            seen.add(item)
            return has_general_interface(underlying(item))
        if interface_type(item) is not None:
            return not is_error_interface(item)
        if isinstance(item, (PointerType, ArrayType)):
            return has_general_interface(item.element)
        if isinstance(item, StructType):
            return any(has_general_interface(field.type) for field in item.fields)
        return False

    if not has_general_interface(dtype):
        return value_plan(dtype)
    requirements = ('go-polymorphic-payload-layout-trace-and-copy',)
    base = underlying(dtype)
    if isinstance(base, PointerType):
        requirements += ('go-pointer-provenance-and-escape',)
    elif isinstance(base, (ArrayType, StructType)):
        requirements += ('go-aggregate-field-trace-and-copy-plan',)
    return ManagedValuePlan(interface_identity(dtype), 'unresolved', requirements=requirements)


def transferred_interface_plan(source, target, token):
    """Static inclusion preserves the runtime pair, never the static source type.

    No concrete value is supplied here. A zero value cannot be inferred merely
    because a static source type happens to use the private nil-error carrier.
    The eventual lowering must copy/root the actual dynamic value and choose
    the target table without nesting one interface box inside another.
    """
    if interface_type(source) is None:
        raise ManagedContractError('interface transfer requires an interface source')
    require_implements(source, target, token)
    return InterfaceValuePlan(target, interface_storage_plan(target), 'preserve')


def _equality_facts(left, right):
    if left.dynamic_state == right.dynamic_state == 'nil':
        return True
    if {left.dynamic_state, right.dynamic_state} == {'nil', 'concrete'}:
        return False
    if (left.dynamic_state == right.dynamic_state == 'concrete'
            and left.dynamic_identity != right.dynamic_identity):
        return False
    return None


def _equality_requirements(left, right):
    # Even an unequal dynamic type or known nil pair cannot discard operand
    # evaluation. Equal dynamic types still need the language comparison and
    # possible non-comparable-value panic, never raw payload or pointer equality.
    required = list(left.unmet) + list(right.unmet)
    required.extend(('go-interface-evaluate-both-operands',
                     'go-interface-dynamic-comparability-and-panic',
                     'go-interface-dynamic-value-equality'))
    return tuple(dict.fromkeys(required))


def _equality_types(left, right):
    """Interfaces may be compared if either static type is assignable to the other."""
    for source, target in ((left, right), (right, left)):
        methods = method_set(source)
        if all((selected := methods.lookup(required.name, required.package)) is not None
               and selected.method.signature == required.signature
               for required in interface_type(target).methods):
            return True
    return False


@dataclass(frozen=True)
class InterfaceEqualityPlan:
    left: InterfaceValuePlan
    right: InterfaceValuePlan
    known_result: bool | None
    requirements: tuple
    evaluate_both: bool = True

    def __post_init__(self):
        if not isinstance(self.left, InterfaceValuePlan) or not isinstance(self.right, InterfaceValuePlan):
            raise ManagedContractError('interface equality requires typed value plans')
        if not _equality_types(self.left.static_type, self.right.static_type):
            raise ManagedContractError('interface equality requires compatible static method sets')
        if self.evaluate_both is not True:
            raise ManagedContractError('interface equality must retain evaluation of both operands')
        if (self.known_result is not None and type(self.known_result) is not bool
                or self.known_result is not _equality_facts(self.left, self.right)):
            raise ManagedContractError('interface equality cannot invent a dynamic-value result')
        if (type(self.requirements) is not tuple
                or any(type(item) is not str or not item for item in self.requirements)
                or len(self.requirements) != len(set(self.requirements))):
            raise ManagedContractError('interface equality requires immutable unique requirement strings')
        if self.requirements != _equality_requirements(self.left, self.right):
            raise ManagedContractError('interface equality must retain its runtime and effect obligations')


def interface_equality_plan(left, right):
    """Record semantic facts only; never authorize a runtime comparison/branch."""
    if not isinstance(left, InterfaceValuePlan) or not isinstance(right, InterfaceValuePlan):
        raise ManagedContractError('interface equality requires typed value plans')
    return InterfaceEqualityPlan(left, right, _equality_facts(left, right),
                                 _equality_requirements(left, right))


