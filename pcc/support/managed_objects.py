"""Compiler-only storage and root obligations shared by source frontends.

These immutable plans are not registered runtime descriptors, an object ABI,
or an allocator. Language owners retain nominal identity, assignability,
dispatch, equality, exceptions and literal semantics. Unknown offsets stay
unknown. The existing GC slot roles remain distinct even in count-free modes.
No managed plan is admitted to native lowering by constructing these records.
"""
from dataclasses import dataclass, field


MAX_LAYOUT_BYTES = (1 << 63) - 1
REFERENCE_ROLES = frozenset({'owned', 'borrowed', 'update_only'})
MANAGED_REQUIREMENTS = (
    'runtime_descriptor', 'allocation_publication', 'rooted_lifetime',
    'field_barriers', 'safepoint_reload', 'runtime_registration',
)


class ManagedContractError(ValueError):
    """Malformed or unresolved compiler contract, never a runtime outcome."""


def _text(value, name):
    if not isinstance(value, str) or not value:
        raise ManagedContractError(name + ' requires a nonempty string')


def _path(value, name):
    if not isinstance(value, tuple) or not value:
        raise ManagedContractError(name + ' requires an immutable nonempty path')
    for part in value:
        _text(part, name)


def _quantity(value, name):
    if value is not None and (type(value) is not int or not 0 <= value <= MAX_LAYOUT_BYTES):
        raise ManagedContractError(name + ' requires a nonnegative bounded integer or None')


def _alignment(value):
    _quantity(value, 'alignment')
    if value is not None and (value == 0 or value & (value - 1)):
        raise ManagedContractError('alignment must be a positive power of two')


@dataclass(frozen=True)
class ManagedTypeIdentity:
    language: str
    origin: tuple[str, ...]
    key: str
    # Optional session-owned declaration token preserves nominal identity
    # where equal spellings do not denote the same language declaration.
    # It is never serialized as runtime type metadata or a process address.
    declaration: object = field(default=None, repr=False)

    def __post_init__(self):
        _text(self.language, 'language')
        if not isinstance(self.origin, tuple):
            raise ManagedContractError('origin requires an immutable tuple')
        for part in self.origin:
            _text(part, 'origin')
        _text(self.key, 'type key')
        if self.declaration is not None:
            try:
                hash(self.declaration)
            except TypeError as error:
                raise ManagedContractError('declaration token must have stable language identity') from error


@dataclass(frozen=True)
class ScalarFieldPlan:
    path: tuple[str, ...]
    kind: str
    offset: int | None = None
    size: int | None = None
    alignment: int | None = None

    def __post_init__(self):
        _path(self.path, 'scalar field')
        _text(self.kind, 'scalar kind')
        _quantity(self.offset, 'scalar offset')
        _quantity(self.size, 'scalar size')
        _alignment(self.alignment)
        if self.size == 0:
            raise ManagedContractError('a scalar field cannot have zero size')


@dataclass(frozen=True)
class ReferenceEdgePlan:
    path: tuple[str, ...]
    target: ManagedTypeIdentity | None = None
    role: str = 'owned'
    nullable: bool = True
    offset: int | None = None

    def __post_init__(self):
        _path(self.path, 'reference field')
        if self.target is not None and not isinstance(self.target, ManagedTypeIdentity):
            raise ManagedContractError('reference target requires a language type identity')
        _text(self.role, 'reference role')
        if self.role not in REFERENCE_ROLES:
            raise ManagedContractError('unknown reference role')
        if type(self.nullable) is not bool:
            raise ManagedContractError('nullable requires a boolean')
        _quantity(self.offset, 'reference offset')


@dataclass(frozen=True)
class ManagedLayoutPlan:
    identity: ManagedTypeIdentity
    fixed_size: int | None = None
    alignment: int | None = None
    scalars: tuple[ScalarFieldPlan, ...] = ()
    references: tuple[ReferenceEdgePlan, ...] = ()
    element: 'ManagedLayoutPlan | None' = None
    element_stride: int | None = None

    def __post_init__(self):
        if not isinstance(self.identity, ManagedTypeIdentity):
            raise ManagedContractError('layout requires a language type identity')
        _quantity(self.fixed_size, 'fixed extent')
        _alignment(self.alignment)
        _quantity(self.element_stride, 'element stride')
        for values, cls in ((self.scalars, ScalarFieldPlan), (self.references, ReferenceEdgePlan)):
            if not isinstance(values, tuple) or any(not isinstance(value, cls) for value in values):
                raise ManagedContractError('layout fields require immutable typed tuples')
        paths = [field.path for field in self.scalars + self.references]
        if len(paths) != len(set(paths)):
            raise ManagedContractError('duplicate scalar/reference field path')
        if self.element is not None and not isinstance(self.element, ManagedLayoutPlan):
            raise ManagedContractError('repeated element requires a layout plan')
        if self.element is None and self.element_stride is not None:
            raise ManagedContractError('element stride requires a repeated element')
        if self.element_stride == 0:
            raise ManagedContractError('element stride cannot be zero')


@dataclass(frozen=True)
class ManagedValuePlan:
    identity: ManagedTypeIdentity
    storage: str = 'unresolved'
    layout: ManagedLayoutPlan | None = None
    requirements: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.identity, ManagedTypeIdentity):
            raise ManagedContractError('value requires a language type identity')
        _text(self.storage, 'storage kind')
        if self.storage not in {'static', 'managed', 'unresolved'}:
            raise ManagedContractError('unknown storage kind')
        if self.layout is not None and (
            not isinstance(self.layout, ManagedLayoutPlan) or self.layout.identity != self.identity
        ):
            raise ManagedContractError('value and layout type identities differ')
        if not isinstance(self.requirements, tuple):
            raise ManagedContractError('requirements require an immutable tuple')
        for requirement in self.requirements:
            _text(requirement, 'requirement')
        if len(self.requirements) != len(set(self.requirements)):
            raise ManagedContractError('duplicate requirement')


@dataclass(frozen=True)
class ManagedSpanPlan:
    backing: str
    owner: ManagedValuePlan | None = None
    offset: int | None = None
    length: int | None = None

    def __post_init__(self):
        _text(self.backing, 'span backing')
        if self.backing not in {'static', 'managed'}:
            raise ManagedContractError('unknown span backing')
        if self.backing == 'managed':
            if not isinstance(self.owner, ManagedValuePlan) or self.owner.storage == 'static':
                raise ManagedContractError('managed span requires a rooted managed owner plan')
        elif self.owner is not None:
            raise ManagedContractError('static span cannot claim a managed owner')
        _quantity(self.offset, 'span offset')
        _quantity(self.length, 'span length')
        if self.offset is not None and self.length is not None:
            if self.offset > MAX_LAYOUT_BYTES - self.length:
                raise ManagedContractError('span extent overflows')


@dataclass(frozen=True)
class ReferenceMap:
    """Payload-relative compiler offsets, not an emitted GC descriptor."""
    pointer_size: int
    fixed_size: int
    alignment: int
    owned: tuple[int, ...]
    borrowed: tuple[int, ...]
    update_only: tuple[int, ...]
    element: 'ReferenceMap | None'
    element_stride: int | None


def pointer_map(layout, *, pointer_size):
    """Resolve exact role-separated offsets; never guess an unknown layout."""
    if not isinstance(layout, ManagedLayoutPlan):
        raise ManagedContractError('pointer map requires a layout plan')
    if type(pointer_size) is not int or pointer_size not in (4, 8):
        raise ManagedContractError('unsupported target pointer width')
    if layout.fixed_size is None or layout.alignment is None:
        raise ManagedContractError('physical layout is unresolved')
    if layout.fixed_size % layout.alignment:
        raise ManagedContractError('fixed extent is not aligned')
    intervals = []
    offsets = {role: [] for role in REFERENCE_ROLES}
    for field in layout.scalars:
        if field.offset is None or field.size is None or field.alignment is None:
            raise ManagedContractError('scalar layout is unresolved')
        if field.offset % field.alignment or layout.alignment < field.alignment:
            raise ManagedContractError('scalar field alignment is invalid')
        intervals.append((field.offset, field.size))
    for edge in layout.references:
        if edge.offset is None:
            raise ManagedContractError('reference layout is unresolved')
        if edge.offset % pointer_size or layout.alignment < pointer_size:
            raise ManagedContractError('reference field alignment is invalid')
        offsets[edge.role].append(edge.offset)
        intervals.append((edge.offset, pointer_size))
    end = 0
    for offset, size in sorted(intervals):
        if offset < end or size > layout.fixed_size or offset > layout.fixed_size - size:
            raise ManagedContractError('overlapping or out-of-extent field')
        end = offset + size
    element = None
    if layout.element is not None:
        if layout.element.element is not None:
            raise ManagedContractError('nested variable-size elements require a separate count and layout contract')
        if layout.element_stride is None:
            raise ManagedContractError('repeated element layout is unresolved')
        element = pointer_map(layout.element, pointer_size=pointer_size)
        if (layout.element_stride < element.fixed_size
                or layout.element_stride % element.alignment
                or layout.fixed_size % element.alignment
                or layout.alignment < element.alignment):
            raise ManagedContractError('repeated element alignment or extent is invalid')
    return ReferenceMap(pointer_size, layout.fixed_size, layout.alignment,
                        tuple(sorted(offsets['owned'])), tuple(sorted(offsets['borrowed'])),
                        tuple(sorted(offsets['update_only'])), element, layout.element_stride)


def allocation_extent(layout, count=0):
    """Checked arithmetic only; no allocation or header size is invented."""
    if not isinstance(layout, ManagedLayoutPlan) or layout.fixed_size is None:
        raise ManagedContractError('allocation extent is unresolved')
    _quantity(count, 'element count')
    if count is None:
        raise ManagedContractError('allocation count is unresolved')
    if layout.element is None:
        if count:
            raise ManagedContractError('fixed layout has no repeated elements')
        return layout.fixed_size
    if layout.element_stride is None:
        raise ManagedContractError('allocation element stride is unresolved')
    if layout.element.element is not None:
        raise ManagedContractError('nested variable-size elements require a separate count and layout contract')
    if layout.element.fixed_size is None:
        raise ManagedContractError('allocation element extent is unresolved')
    if layout.element_stride < layout.element.fixed_size:
        raise ManagedContractError('allocation element stride is smaller than its extent')
    if count > (MAX_LAYOUT_BYTES - layout.fixed_size) // layout.element_stride:
        raise ManagedContractError('allocation extent overflows')
    return layout.fixed_size + count * layout.element_stride


def unmet_requirements(value):
    """This compiler-only owner cannot certify a managed runtime lane."""
    if not isinstance(value, ManagedValuePlan):
        raise ManagedContractError('capability query requires a value plan')
    required = list(value.requirements)
    if value.storage != 'static':
        required.extend(MANAGED_REQUIREMENTS)
        # No target-specific descriptor/header binding is performed here,
        # even if a caller supplied a fully resolved payload reference map.
        required.append('object_abi')
    return tuple(dict.fromkeys(required))
