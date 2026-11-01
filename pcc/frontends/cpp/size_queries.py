"""Typed, nondependent sizeof facts, before C AST projection.

N4659 [expr.sizeof]/1-4,6; [expr]/8; [basic.fundamental]/10.
Reuse the actual target scalar ABI facts. Do not duplicate class layout or
invoke codegen to obtain a semantic constant. Unsupported record layout,
references and dependent types remain separate explicit capabilities.
"""
from pcc.frontends.c.c_abi_layout import (
    builtin_scalar_layout, c_size_type_name, pointer_scalar_layout,
)
from pcc.frontends.cpp.classes import is_class_type, require_complete
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import (
    ArrayType, PointerType, c_type_names, is_enum_type, unqualified,
)


def sizeof_type(dtype, types, coord):
    plain = unqualified(dtype)
    if plain == "void":
        raise CppDiagnostic("cpp.invalid-sizeof", "sizeof requires a complete object type, not void", coord)
    if is_class_type(plain):
        require_complete(plain, coord)
        raise CppDiagnostic("cpp.unsupported.sizeof-class-layout",
                            "class sizeof requires a shared semantic layout query", coord)
    if isinstance(plain, ArrayType):
        if plain.bound is None:
            raise CppDiagnostic("cpp.invalid-sizeof", "sizeof cannot apply to an array of unknown bound", coord)
        size = plain.bound * sizeof_type(plain.element, types, coord)
    elif isinstance(plain, PointerType) or plain == "nullptr":
        # A pointer is complete independently of its pointee. nullptr_t has
        # the size of void* in C++17 [basic.fundamental]/10.
        size = pointer_scalar_layout().size
    elif is_enum_type(plain):
        if not plain.complete:
            raise CppDiagnostic("cpp.incomplete-enum", "sizeof requires a complete enum type", coord)
        size = builtin_scalar_layout(c_type_names(plain.underlying), types.target_triple).size
    elif isinstance(plain, str):
        size = builtin_scalar_layout(c_type_names(plain), types.target_triple).size
    else:
        raise CppDiagnostic("cpp.unsupported.sizeof-types", "sizeof type semantics are not implemented", coord)
    size_type = c_size_type_name(types.target_triple)
    if not 0 < size < (1 << types.width(size_type)):
        raise CppDiagnostic("cpp.sizeof-overflow", "object size is not representable in target size_t", coord)
    return size
