"""Go adapters for shared compiler-only storage obligations.

Language identity survives boxing; an interface table is immutable metadata,
not a traced object edge. These plans do not select a box layout, admit concrete
interfaces, or turn the private nil-error word into an interface ABI. Current
string lowering consumes only the static span adapter; managed spans require
an owner and retain unresolved root, barrier and safepoint obligations.
"""
from pcc.support.managed_objects import (
    ManagedContractError, ManagedLayoutPlan, ManagedSpanPlan,
    ManagedTypeIdentity, ManagedValuePlan, ReferenceEdgePlan, ScalarFieldPlan,
)
from .records import DefinedType, StructType, underlying
from .types import ArrayType, ERROR_TYPE, INTEGER_TYPES, PointerType, is_error_interface, is_string


def type_identity(dtype):
    """Preserve declaration identity and structural component identities."""
    if isinstance(dtype, DefinedType):
        origin = ('universe',) if dtype is ERROR_TYPE else ('package', dtype.package or '<main>')
        return ManagedTypeIdentity('go', origin, dtype.name, declaration=dtype)
    if isinstance(dtype, str) and (dtype in INTEGER_TYPES or dtype in {'bool', 'string'}):
        return ManagedTypeIdentity('go', ('universe',), dtype)
    if isinstance(dtype, PointerType):
        return ManagedTypeIdentity('go', ('structural',), 'pointer',
                                   declaration=(type_identity(dtype.element),))
    if isinstance(dtype, ArrayType):
        return ManagedTypeIdentity('go', ('structural',), 'array',
                                   declaration=(dtype.count, type_identity(dtype.element)))
    if isinstance(dtype, StructType):
        fields = tuple((item.name, item.package, getattr(item, 'embedded', False),
                        type_identity(item.type)) for item in dtype.fields)
        return ManagedTypeIdentity('go', ('structural',), 'struct', declaration=fields)
    if is_error_interface(dtype):
        return ManagedTypeIdentity('go', ('structural-interface',), 'Error() string')
    raise ManagedContractError('Go managed identity requires a supported resolved value type')


def value_plan(dtype):
    """Describe a prospective boxed value without admitting native allocation.

    The semantic interface owner supplies conversion, copy, equality and itable
    obligations. Aggregate trace flattening is deliberately unresolved rather
    than returning an empty pointer map for a possibly reference-bearing value.
    """
    identity = type_identity(dtype)
    base = underlying(dtype)
    requirements = ('go-value-copy-semantics',)
    if isinstance(base, str) and (base in INTEGER_TYPES or base == 'bool'):
        size = 1 if base == 'bool' else INTEGER_TYPES[base][0] // 8
        layout = ManagedLayoutPlan(identity, size, size,
            (ScalarFieldPlan(('value',), base, 0, size, size),))
    elif isinstance(base, PointerType):
        layout = ManagedLayoutPlan(identity,
            references=(ReferenceEdgePlan(('value',), type_identity(base.element)),))
        requirements += ('go-pointer-provenance-and-escape',)
    elif is_string(dtype):
        # Logical fields only: no assertion that a Go string is physically
        # {owner, offset, length}, or that existing static strings have roots.
        layout = ManagedLayoutPlan(identity,
            scalars=(ScalarFieldPlan(('offset',), 'byte-offset'),
                     ScalarFieldPlan(('length',), 'byte-length')),
            references=(ReferenceEdgePlan(('backing-owner',)),))
        requirements += ('go-string-owner-and-span-lowering',)
    elif is_error_interface(dtype):
        layout = ManagedLayoutPlan(identity,
            scalars=(ScalarFieldPlan(('dynamic-type-and-method-table',), 'immutable-metadata'),),
            references=(ReferenceEdgePlan(('dynamic-value',)),))
        requirements += ('go-interface-dynamic-type-and-method-table',
                         'go-interface-typed-nil-and-equality', 'go-interface-box-copy')
    elif isinstance(base, (ArrayType, StructType)):
        layout = None
        requirements += ('go-aggregate-field-trace-and-copy-plan',)
    else:
        raise ManagedContractError('Go managed value plan is not available for this type')
    return ManagedValuePlan(identity, 'unresolved', layout, requirements)


def static_string_plan(data):
    """Known literal bytes live in immutable static storage, not the GC heap."""
    if not isinstance(data, bytes):
        raise ManagedContractError('Go static string backing requires immutable bytes')
    return ManagedSpanPlan('static', offset=0, length=len(data))


def managed_string_plan(owner, *, offset=None, length=None):
    """Retain a base owner; never assume derived pointers are rewritten by GC."""
    return ManagedSpanPlan('managed', owner=owner, offset=offset, length=length)
