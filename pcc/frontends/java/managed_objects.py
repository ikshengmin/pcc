"""Java source adapters for the shared compiler-only managed contracts.

Nominal source identities, unresolved bootstrap String and primitive arrays
remain distinct. These plans neither create references nor register runtime
descriptors. Class offsets describe only the existing primitive payload model;
the object header, roots, barriers and native representation remain unresolved.
"""
from pcc.support.managed_objects import (
    ManagedContractError,
    ManagedLayoutPlan,
    ManagedTypeIdentity,
    ManagedValuePlan,
    ReferenceEdgePlan,
    ScalarFieldPlan,
)

from .source_types import ArrayType, BootstrapStringType, ClassType, descriptor


PRIMITIVE_SIZES = {
    "boolean": 1, "byte": 1, "short": 2, "char": 2,
    "int": 4, "long": 8, "float": 4, "double": 8,
}
CLASS_REQUIREMENTS = (
    "owned-bootstrap-Object-definition-and-super-construction",
    "class-initialization-and-construction-publication",
)
ARRAY_REQUIREMENTS = (
    "java-array-header-and-length-layout",
    "java-array-allocation-and-zero-initialization",
    "java-array-null-bounds-and-negative-size-exceptions",
)
STRING_REQUIREMENTS = (
    "owned-bootstrap-type-definition",
    "owned-String-layout-and-storage",
    "shared-owning-root-and-safepoint-ABI",
)


def type_identity(dtype):
    """Preserve Java type identity, never a diagnostic filename or content key."""
    if isinstance(dtype, ClassType):
        return ManagedTypeIdentity("java", ("source-class",), dtype.class_name)
    if isinstance(dtype, BootstrapStringType):
        return ManagedTypeIdentity("java", ("unresolved-bootstrap",), dtype.class_name)
    if isinstance(dtype, ArrayType):
        return ManagedTypeIdentity("java", ("primitive-array",), descriptor(dtype))
    if isinstance(dtype, str) and dtype in PRIMITIVE_SIZES:
        return ManagedTypeIdentity("java", ("primitive",), dtype)
    raise ManagedContractError("Java managed identity requires a resolved source type")


def array_value_plan(dtype):
    """Known primitive element widths do not resolve an array object header."""
    if not isinstance(dtype, ArrayType):
        raise ManagedContractError("Java array plan requires a primitive array type")
    size = PRIMITIVE_SIZES[dtype.component]
    element = ManagedLayoutPlan(
        type_identity(dtype.component), fixed_size=size, alignment=size,
        scalars=(ScalarFieldPlan(("value",), dtype.component, 0, size, size),),
    )
    identity = type_identity(dtype)
    layout = ManagedLayoutPlan(identity, element=element, element_stride=size)
    return ManagedValuePlan(identity, "managed", layout, ARRAY_REQUIREMENTS)


def string_value_plan(*, materialization=False):
    """A constant's UTF-16 content is not a static or interned String object."""
    requirements = STRING_REQUIREMENTS
    if materialization:
        requirements += ("managed-literal-materialization", "interned-reference-identity")
    return ManagedValuePlan(type_identity(BootstrapStringType()), "unresolved",
                            requirements=requirements)


def reference_value_plan(dtype, *, materialization=False):
    """Attach unresolved storage contracts to checked reference expressions."""
    if isinstance(dtype, ArrayType):
        return array_value_plan(dtype)
    if isinstance(dtype, BootstrapStringType):
        return string_value_plan(materialization=materialization)
    if isinstance(dtype, ClassType):
        return ManagedValuePlan(type_identity(dtype), "managed", requirements=CLASS_REQUIREMENTS)
    # In particular, a null literal has no object identity or allocation plan.
    return None


def class_value_plan(program, slots, payload_size, payload_alignment, *, requirements=()):
    """Consume the checked instance slots without guessing reference offsets."""
    identity = type_identity(ClassType(program.name))
    scalars, references = [], []
    for slot in slots:
        field = slot.field
        path = (field.token.text,)
        if isinstance(field.dtype, (ArrayType, ClassType, BootstrapStringType)):
            references.append(ReferenceEdgePlan(path, type_identity(field.dtype)))
        else:
            scalars.append(ScalarFieldPlan(path, field.dtype, slot.offset, slot.size, slot.alignment))
    layout = ManagedLayoutPlan(identity, payload_size, payload_alignment,
                               tuple(scalars), tuple(references))
    return ManagedValuePlan(identity, "managed", layout,
                            tuple(dict.fromkeys((*CLASS_REQUIREMENTS, *requirements))))
