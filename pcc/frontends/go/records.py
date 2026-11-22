"""Go nominal identity and value-record types, independent of IR layout.

A defined type gets declaration identity, not identity from its representation.
Aliases reuse the resolved type object. No runtime class/GC layout is introduced.
"""
from dataclasses import dataclass, field


@dataclass(eq=False)
class DefinedType:
    name: str
    package: str
    underlying: object = None
    methods: dict = field(default_factory=dict)
    # Empty for package definitions. Lexically distinct local declarations
    # may share their source name, representation and package.
    local_site: tuple = ()
    # A named instantiation is not its underlying signature or an anonymous
    # spelling. The template interns (declaration, exact type arguments).
    template: object = None
    type_arguments: tuple = ()

    def __deepcopy__(self, memo):
        # Declarations are canonical owners, not mutable syntax snapshots.
        return self

    def __str__(self):
        name = self.package + "." + self.name if self.package else self.name
        return name + ('[' + ', '.join(map(str, self.type_arguments)) + ']' if self.type_arguments else '')


@dataclass(frozen=True)
class Field:
    name: str
    type: object
    package: str = ""
    embedded: bool = False
    # Decoded Go string bytes. An absent tag and an empty string are identical.
    tag: bytes = b""


@dataclass(frozen=True)
class MethodSignature:
    parameters: tuple
    result: object
    variadic: bool = False


@dataclass(frozen=True)
class NativeFunctionBinding:
    """Source-probe ABI facts for a separately produced owned object."""
    symbol: str
    target: str
    parameters: tuple
    results: tuple
    object_sha256: str


@dataclass(frozen=True)
class InterfaceMethod:
    name: str
    package: str
    signature: MethodSignature


@dataclass(frozen=True)
class InterfaceType:
    """A complete basic-interface method set, without a value representation."""
    methods: tuple

    def __str__(self):
        return "interface{" + "; ".join(method.name + "(" + ", ".join(
            map(str, method.signature.parameters)) + ") " + str(method.signature.result)
            for method in self.methods) + "}"


@dataclass(frozen=True)
class StructType:
    fields: tuple

    def __str__(self):
        return "struct{" + "; ".join(
            (field.name if field.embedded else field.name + " " + str(field.type)) +
            (" " + repr(field.tag) if field.tag else "")
            for field in self.fields) + "}"


def underlying(dtype):
    return dtype.underlying if isinstance(dtype, DefinedType) else dtype


def is_named(dtype):
    return isinstance(dtype, DefinedType) or isinstance(dtype, str) and not dtype.startswith("untyped ")


def same_underlying(left, right):
    return underlying(left) == underlying(right)
