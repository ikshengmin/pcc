"""Composable Itanium encoding for supported typed template signatures.

The caller owns the declaring function/member prefix and passes its shared
substitution list. This module does not turn concrete substituted signatures
into template declarations or erase their dependent parameter identity.
"""
from pcc.frontends.cpp.diagnostics import CppDiagnostic
from pcc.frontends.cpp.types import ConstType, PointerType, ReferenceType, _mangle_type, _substitution


def mangle_template_type(dtype, substitutions):
    from pcc.frontends.cpp.templates import TypeParameter, ValueParameter, IntegralArgument, mangle_argument, type_key
    if isinstance(dtype, IntegralArgument):
        return mangle_argument(dtype)
    if isinstance(dtype, ValueParameter):
        return 'X' + ('T_' if dtype.index == 0 else 'T' + str(dtype.index - 1) + '_') + 'E'
    from pcc.frontends.cpp.templates import dependent, Pattern
    if not dependent(dtype) and not isinstance(dtype, Pattern):
        return _mangle_type(dtype, substitutions)
    key = ('template-pattern', type_key(dtype))
    if key in substitutions:
        return _substitution(substitutions.index(key))
    if isinstance(dtype, TypeParameter):
        encoded = 'T_' if dtype.index == 0 else 'T' + str(dtype.index - 1) + '_'
    elif isinstance(dtype, ReferenceType):
        encoded = 'R' + mangle_template_type(dtype.referent, substitutions)
    elif isinstance(dtype, ConstType):
        encoded = 'K' + mangle_template_type(dtype.base, substitutions)
    elif isinstance(dtype, PointerType):
        encoded = 'P' + mangle_template_type(dtype.pointee, substitutions)
    elif getattr(dtype, 'cpp_template_id_pattern', False):
        parts = dtype.symbol.dtype.qualified_name
        namespaces = parts[:-1]
        prefix_key = ('class-template-prefix', dtype.symbol)
        if prefix_key in substitutions:
            prefix = _substitution(substitutions.index(prefix_key))
        else:
            prefix, start = '', 0
            for count in range(len(namespaces), 0, -1):
                ns_key = ('namespace-prefix', namespaces[:count])
                if ns_key in substitutions:
                    prefix, start = _substitution(substitutions.index(ns_key)), count
                    break
            if not prefix and namespaces and namespaces[0] == 'std':
                prefix, start = 'St', 1
            for i in range(start, len(namespaces)):
                prefix += str(len(namespaces[i])) + namespaces[i]
                substitutions.append(('namespace-prefix', namespaces[:i + 1]))
            prefix += str(len(parts[-1])) + parts[-1]
            substitutions.append(prefix_key)
        args = ''.join(mangle_template_type(a, substitutions) for a in dtype.arguments)
        encoded = prefix + 'I' + args + 'E'
        if namespaces and namespaces != ('std',):
            encoded = 'N' + encoded + 'E'
    else:
        from pcc.frontends.cpp.templates import dependent, Pattern
        if dependent(dtype) or isinstance(dtype, Pattern):
            raise CppDiagnostic('cpp.unsupported.template-abi',
                                'this dependent signature has no supported ABI encoding', getattr(dtype, 'coord', None))
        return _mangle_type(dtype, substitutions)
    substitutions.append(key)
    return encoded
