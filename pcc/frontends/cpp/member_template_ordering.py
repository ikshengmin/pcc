"""Call-context partial ordering for bounded non-static member templates.

N4659 [temp.func.order]/3 and [temp.deduct.partial]/3,5-10,12:
https://timsong-cpp.github.io/cppwp/n4659/temp.func.order
https://timsong-cpp.github.io/cppwp/n4659/temp.deduct.partial

This is symbolic transformed-type deduction, not actual-argument conversion
ranking. It is called only after ordinary conversion sequences tie. Source
parameters omit `this`; argument_count counts explicitly supplied source call
arguments, not default arguments or the implicit object. Other shapes return
None, which must preserve the caller's explicit unsupported boundary.
"""
from pcc.frontends.cpp.templates import TypeParameter
from pcc.frontends.cpp.types import ConstType, PointerType, ReferenceType


def _shape(dtype, parameters, top=True):
    if type(dtype) is TypeParameter:
        for index, parameter in enumerate(parameters):
            if dtype is parameter:
                return ('parameter', index)
        return None
    if type(dtype) is ConstType:
        inner = _shape(dtype.base, parameters, False)
        return None if inner is None else ('const', inner)
    if type(dtype) is PointerType:
        inner = _shape(dtype.pointee, parameters, False)
        return None if inner is None else ('pointer', inner)
    if top and type(dtype) is ReferenceType:
        inner = _shape(dtype.referent, parameters, False)
        return None if inner is None else ('reference', inner)
    # No arrays, class template-ids, concrete-only function parameters,
    # derived-class deductions, function types, NTTPs, or reference collapsing.
    return None


def _unqualified(shape):
    return shape[1] if shape[0] == 'const' else shape


def _normalized(shape):
    reference = shape[0] == 'reference'
    referred = shape[1] if reference else shape
    return _unqualified(referred), reference, referred[0] == 'const'


def _transform(shape):
    if shape[0] == 'parameter':
        # Distinct declarations become distinct arbitrary types; repeated
        # occurrences retain identity. No size/name/depth estimate is used.
        return ('unique-type', shape[1])
    return (shape[0], _transform(shape[1]))


def _match(parameter, argument, bindings):
    if parameter[0] == 'parameter':
        index = parameter[1]
        if index in bindings:
            return bindings[index] == argument
        bindings[index] = argument
        return True
    if parameter[0] != argument[0]:
        return False
    return _match(parameter[1], argument[1], bindings)


def _at_least(argument_shapes, parameter_shapes):
    bindings = {}
    for argument, parameter in zip(argument_shapes, parameter_shapes):
        a, a_ref, a_const = _normalized(argument)
        p, p_ref, p_const = _normalized(parameter)
        if not _match(p, _transform(a), bindings):
            return False
        # [temp.deduct.partial]/9 applies only if this pair deduces both ways.
        # Both are lvalue references in the supported vocabulary, so only the
        # cv condition can remove an otherwise valid direction.
        if (a_ref and p_ref and p_const and not a_const
                and _match(a, _transform(p), {})):
            return False
    return True


def compare_member_template_order(first, second, argument_count):
    """-1: first more specialized; +1: second; 0: no winner; None: unsupported.

    Pass declaration origins (survey.template_origin), not instantiated method
    parameter lists. Both functions must have the same ordinary class owner and
    identical non-static object qualifiers. Return types do not affect ordering.
    A 0 result is proved equality/incomparability, never permission to pick one.
    """
    owner = getattr(first, 'class_owner', None)
    if (owner is None or owner is not getattr(second, 'class_owner', None)
            or getattr(owner, 'template_origin', None) is not None
            or type(argument_count) is not int or argument_count < 0):
        return None
    for item in (first, second):
        if (getattr(item, 'method_static', False) or getattr(item, 'variadic', False)
                or getattr(item, 'method_ref_qualifier', None)
                or getattr(item, 'method_volatile', False)
                or not item.template_parameters
                or not all(type(p) is TypeParameter for p in item.template_parameters)
                or argument_count > len(item.parameters)):
            return None
    if getattr(first, 'method_const', False) != getattr(second, 'method_const', False):
        return None
    shapes = []
    for item in (first, second):
        selected = tuple(_shape(t, item.template_parameters)
                         for t in item.parameters[:argument_count])
        if any(t is None for t in selected):
            return None
        shapes.append(selected)
    first_at_least = _at_least(shapes[0], shapes[1])
    second_at_least = _at_least(shapes[1], shapes[0])
    if first_at_least == second_at_least:
        return 0
    return -1 if first_at_least else 1
