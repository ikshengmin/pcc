"""Typed sizeof storage facts, before C AST projection.

N4659 [expr.sizeof]/1-4,6; [expr]/8; [basic.fundamental]/10.
Reuse target scalar facts and the shared natural-record query used by C IR
layout. No host sizeof, codegen invocation or alternate record calculation.
Dependent syntax is retained by the parser until concrete semantic replay.
"""
from pcc.frontends.c.c_abi_layout import (
    CAbiScalarLayout, builtin_scalar_layout, c_size_type_name,
    natural_record_layout, pointer_scalar_layout,
)
from pcc.frontends.cpp.classes import is_class_type, require_complete
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import (
    ArrayType, PointerType, ReferenceType, c_type_names, function_type, is_enum_type, unqualified,
)


def object_layout(dtype, types, coord):
    if isinstance(dtype, ReferenceType):
        return object_layout(dtype.referent, types, coord)
    plain = unqualified(dtype)
    if plain == "void" or function_type(plain):
        raise CppDiagnostic("cpp.invalid-sizeof", "sizeof requires a complete object type, not void or a function", coord)
    if is_class_type(plain):
        require_complete(plain, coord)
        # All admitted classes project to this natural record. Unsupported
        # bases, bit-fields and attributes never reach here. A polymorphic
        # class's leading table pointer is its first field, as in
        # ClassDefinition.c_declaration, which also adds the one-byte
        # empty-class field matched by the minimum size.
        fields = [object_layout(member.dtype, types, member.coord) for member in plain.definition.members]
        if plain.is_union:
            # [class.union]/2: every member starts at offset 0.
            alignment = max([1] + [field.alignment for field in fields])
            size = max([1] + [field.size for field in fields])
            return CAbiScalarLayout(-(-size // alignment) * alignment, alignment)
        from pcc.frontends.cpp.inheritance import base_specifier, is_empty_class
        specifier = base_specifier(plain.definition)
        if specifier is not None and not is_empty_class(specifier.dtype):
            # inheritance.py admits only bases whose leading C field is exact.
            fields.insert(0, object_layout(specifier.dtype, types, coord))
        from pcc.frontends.cpp.virtual_functions import owns_vptr
        if owns_vptr(plain.definition):
            fields.insert(0, pointer_scalar_layout())
        record = natural_record_layout(fields, minimum_size=1)
        return CAbiScalarLayout(record.size, record.alignment)
    if isinstance(plain, ArrayType):
        if plain.bound is None:
            raise CppDiagnostic("cpp.invalid-sizeof", "sizeof cannot apply to an array of unknown bound", coord)
        element = object_layout(plain.element, types, coord)
        return CAbiScalarLayout(plain.bound * element.size, element.alignment)
    elif isinstance(plain, PointerType) or plain == "nullptr":
        # A pointer is complete independently of its pointee. nullptr_t has
        # the size of void* in C++17 [basic.fundamental]/10.
        return pointer_scalar_layout()
    elif is_enum_type(plain):
        if not plain.complete:
            raise CppDiagnostic("cpp.incomplete-enum", "sizeof requires a complete enum type", coord)
        return builtin_scalar_layout(c_type_names(plain.underlying), types.target_triple)
    elif isinstance(plain, str):
        return builtin_scalar_layout(c_type_names(plain), types.target_triple)
    else:
        raise CppDiagnostic("cpp.unsupported.sizeof-types", "sizeof type semantics are not implemented", coord)


def sizeof_type(dtype, types, coord):
    size = object_layout(dtype, types, coord).size
    size_type = c_size_type_name(types.target_triple)
    if not 0 < size < (1 << types.width(size_type)):
        raise CppDiagnostic("cpp.sizeof-overflow", "object size is not representable in target size_t", coord)
    return size
