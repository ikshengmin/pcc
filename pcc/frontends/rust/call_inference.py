"""Argument/result-constrained generic calls, with fallback deferred per body.

This is language checking, not another instantiation cache: completed argument
constraints are handed to GenericFunctions.select and its shared worklist.
Unbound parameters get declaration-keyed inference variables, not a guessed
concrete type. Unresolved variables fail before any instance is registered.
"""
from dataclasses import replace

from .generics import children, substitute
from .types import FixedArrayType, SharedReference, TupleType, TypeApplication, TypeParameter


def partial(ty):
    from .semantics import _root
    ty = _root(ty)
    if isinstance(ty, FixedArrayType):
        return FixedArrayType(partial(ty.element), ty.length)
    if isinstance(ty, TupleType):
        return TupleType(tuple(partial(t) for t in ty.elements))
    if isinstance(ty, TypeApplication):
        return TypeApplication(ty.name, tuple(partial(t) for t in ty.arguments))
    if isinstance(ty, SharedReference):
        return SharedReference(partial(ty.referent), ty.mutable, ty.lifetime)
    return ty


def pending_integer(ty):
    from .semantics import InferredInteger, InferredType
    todo = [ty]
    while todo:
        item = partial(todo.pop())
        if isinstance(item, (InferredInteger, InferredType)):
            return True
        todo.extend(children(item))
    return False


def infer(checker, function, arguments, token):
    from .semantics import InferredType, NEVER, unify
    parameters = checker.function_generics.parameters[function.declaration_name]
    # Keep each call's unknowns shared with its substituted result/parameters.
    # Later annotations, return types and argument contexts constrain these
    # same variables before finish selects the concrete shared-registry entry.
    bindings = {parameter: InferredType(
        token, description=f"generic parameter {parameter.name} (result-context inference)")
        for parameter in parameters}

    def constrain(pattern, actual):
        actual = partial(actual)
        if actual == NEVER:
            return  # A diverging operand does not invent a concrete type.
        if isinstance(pattern, TypeParameter) and pattern in parameters:
            bindings[pattern] = unify(bindings[pattern], actual, token)
        elif isinstance(pattern, FixedArrayType) and isinstance(actual, FixedArrayType) and pattern.length == actual.length:
            constrain(pattern.element, actual.element)
        elif isinstance(pattern, TupleType) and isinstance(actual, TupleType) and len(pattern.elements) == len(actual.elements):
            for p, a in zip(pattern.elements, actual.elements):
                constrain(p, a)
        elif isinstance(pattern, SharedReference) and isinstance(actual, SharedReference):
            constrain(pattern.referent, actual.referent)
        elif (isinstance(pattern, TypeApplication) and isinstance(actual, TypeApplication)
              and pattern.name == actual.name and len(pattern.arguments) == len(actual.arguments)):
            for p, a in zip(pattern.arguments, actual.arguments):
                constrain(p, a)
        else:
            # A wholly unknown actual may stand for a composite formal. Bind
            # it to this call's variables, never to the callee's declaration
            # parameters, so enclosing result constraints can still reach it.
            unify(substitute(pattern, bindings), actual, token)

    for (_, pattern, _), actual in zip(function.params, arguments):
        constrain(pattern, actual)
    types = tuple(bindings[p] for p in parameters)
    signature = replace(function,
                        params=[(name, substitute(ty, bindings), mutable) for name, ty, mutable in function.params],
                        result=substitute(function.result, bindings), body=None,
                        type_parameters=(), instance_arguments=types)
    return signature, types


def finish(checker, requests):
    from .semantics import resolve
    for expression, declaration, arguments, token in requests:
        concrete = tuple(resolve(argument) for argument in arguments)
        expression.binding = checker.function_generics.select(declaration, concrete, token)
