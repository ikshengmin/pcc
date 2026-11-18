"""Narrow immediate-context substitution failure, N4659 [temp.deduct]/8.

No exception-code filtering: a class specialization, body instantiation, or
unsupported semantic operation may raise the same diagnostic as a direct type
composition. Those failures must never be hidden by this check.
"""
from pcc.frontends.cpp.templates import TypeParameter
from pcc.frontends.cpp.types import ConstType, PointerType, ReferenceType, unqualified


def immediate_reference_to_void(function, bindings):
    """Recognize only an explicitly substituted direct T& / const T& -> void&.

    This is a signature-only syntactic check, with no lookup, specialization,
    expression evaluation, cache mutation, or diagnostic suppression. All other
    type shapes continue to the original substitution owner unchanged.
    """
    def invalid(dtype):
        if isinstance(dtype, ReferenceType):
            referent = unqualified(dtype.referent)
            if isinstance(referent, TypeParameter):
                return unqualified(bindings[referent.index]) == 'void'
            return invalid(dtype.referent)
        if isinstance(dtype, ConstType):
            return invalid(dtype.base)
        if isinstance(dtype, PointerType):
            return invalid(dtype.pointee)
        # In particular do not descend into template-id arguments: forming
        # those specializations is another semantic context with hard errors.
        return False
    return any(invalid(dtype) for dtype in (function.dtype,) + function.parameters)
