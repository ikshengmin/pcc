"""Abstractly checked Rust generic functions, with shared instance scheduling.

A body is checked once using declaration-bound, non-Copy type parameters before
any concrete body is materialized. Concrete substitution is not a substitute
for that obligation. Only explicit, borrow-free type arguments are admitted;
trait bounds, generic inference, lifetimes and foreign generic ABI remain gates.
"""
from copy import deepcopy
from dataclasses import replace

from pcc.support.instantiation import InstanceRegistry
from .generics import PRIMITIVES, bounded_type, children, substitute
from .moves import MoveChecker
from .types import SharedReference, TupleType, TypeApplication, TypeParameter

MAX_FUNCTION_INSTANCES = 128


def type_code(ty):
    """Private deterministic identity spelling, never a host object address."""
    if isinstance(ty, TupleType):
        return ['tuple', [type_code(item) for item in ty.elements]]
    if isinstance(ty, TypeApplication):
        return ['data', ty.name, [type_code(item) for item in ty.arguments]]
    if isinstance(ty, (TypeParameter, SharedReference)):
        raise AssertionError('non-concrete value type reached function symbol encoding')
    return ['name', ty]


class GenericFunctions:
    def __init__(self, checker):
        self.checker = checker
        self.parameters = {}
        self.raw_bodies = {}
        self.instances = InstanceRegistry()

    def prepare(self, function):
        scope = {}
        for index, parameter in enumerate(function.type_parameters):
            if parameter.text in scope:
                raise parameter.error('duplicate generic parameter')
            if parameter.text in PRIMITIVES:
                raise parameter.error('generic parameters shadowing primitive names are not implemented')
            scope[parameter.text] = TypeParameter('function ' + function.declaration_name,
                                                  index, parameter.text)
        self.parameters[function.declaration_name] = tuple(scope.values())
        if function.type_parameters:
            self.raw_bodies[function.declaration_name] = deepcopy(function.body)
        if function.implementation is not None:
            scope['Self'] = self.checker.generics.normalize(function.implementation.owner, function.token, scope)
        return scope

    def scope(self, function, arguments=None):
        parameters = self.parameters[function.declaration_name]
        scope = {parameter.name: value for parameter, value in
                 zip(parameters, parameters if arguments is None else arguments)}
        if function.implementation is not None:
            scope['Self'] = self.checker.generics.normalize(function.implementation.owner, function.token, scope)
        return scope

    def select(self, function, arguments, token):
        if arguments is None:
            if function.type_parameters:
                raise token.error('generic function requires explicit type arguments; argument inference is not implemented')
            return function
        if not function.type_parameters:
            raise token.error('type arguments supplied to a nongeneric function')
        if len(arguments) != len(function.type_parameters):
            raise token.error('generic function argument count mismatch')
        arguments = tuple(self.checker.generics.normalize(ty, token) for ty in arguments)
        bounded_type(TupleType(arguments), token)
        for argument in arguments:
            pending = [argument]
            while pending:
                ty = pending.pop()
                if isinstance(ty, SharedReference):
                    raise token.error('generic function reference arguments require lifetime/escape analysis, which is not implemented')
                pending.extend(children(ty))
            self.checker.valid_type(argument, token, allow_reference=False)
        bindings = dict(zip(self.parameters[function.declaration_name], arguments))
        params = [(name, substitute(ty, bindings), mutable) for name, ty, mutable in function.params]
        result = substitute(function.result, bindings)
        if self.checker.abstract_function:
            # Abstract calls check the substituted signature but do not enqueue
            # code, even when their arguments happen to be fully concrete.
            return replace(function, params=params, result=result, body=None,
                           type_parameters=(), instance_arguments=arguments)
        key = (id(function), arguments)
        entry = self.instances.get(key)
        if entry is not None:
            return entry.value
        if len(self.instances) >= MAX_FUNCTION_INSTANCES:
            raise token.error('generic function instances exceed implementation limit (128)')
        import json
        encoded = json.dumps([function.declaration_name, [type_code(ty) for ty in arguments]],
                             ensure_ascii=True, separators=(',', ':')).encode('ascii').hex()
        instance = replace(function, params=params, result=result,
                           body=deepcopy(self.raw_bodies[function.declaration_name]),
                           type_parameters=(), instance_arguments=arguments,
                           instance_name='__pcc_rust_instance_' + encoded)
        self.checker.valid_type(result, token, allow_reference=False)
        for name, ty, _ in params:
            self.checker.valid_type(ty, name)
        return self.instances.request(key, instance, token).value

    def materialize(self):
        def build(entry):
            function = entry.value
            self.checker.check_body(function, self.scope(function, function.instance_arguments))
            MoveChecker(self.checker).check_function(function)
            return function
        # A body can request itself or another specialization. The shared
        # scheduler returns IN_PROGRESS placeholders, then completes FIFO work.
        self.checker.functions.extend(self.instances.drain(build))
