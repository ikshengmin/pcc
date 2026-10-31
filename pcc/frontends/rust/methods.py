"""Inherent nominal methods with value receivers; no trait/borrow dispatch.

Impl parameters are inferred only by matching an already-known nominal owner.
Bodies use the same abstract checker and shared function instance worklist as
free functions. This is not general argument inference or a second mono cache.
"""
from .generics import PRIMITIVES
from .types import SharedReference, TypeApplication


class InherentMethods:
    def __init__(self, checker, implementations):
        self.checker = checker
        self.implementations = implementations
        self.declarations = {}

    def prepare(self):
        for implementation in self.implementations:
            owner = implementation.owner
            name = owner.name if isinstance(owner, TypeApplication) else owner
            definition = self.checker.generics.definitions.get(name)
            if definition is None:
                raise implementation.token.error('inherent impl requires a local nominal struct or enum')
            names = [p.text for p in implementation.type_parameters]
            if len(names) != len(set(names)):
                raise implementation.token.error('duplicate impl generic parameter')
            if any(name in PRIMITIVES for name in names):
                raise implementation.token.error('impl parameters shadowing primitive names are not implemented')
            if definition.type_parameters:
                if not isinstance(owner, TypeApplication) or len(owner.arguments) != len(definition.type_parameters):
                    raise implementation.token.error('generic impl owner requires complete type arguments')
                # Each parameter is constrained exactly once by this nominal
                # owner. Permutations are legal; specializations/overlap are a
                # separate coherence obligation, not guessed from call sites.
                if (any(not isinstance(arg, str) for arg in owner.arguments)
                        or len(owner.arguments) != len(names)
                        or set(owner.arguments) != set(names)):
                    raise implementation.token.error('specialized, repeated or unconstrained impl parameters require coherence checking, which is not implemented')
            elif isinstance(owner, TypeApplication) or names:
                raise implementation.token.error('nongeneric impl owner cannot have generic arguments or unconstrained parameters')
        for function in self.checker.functions:
            if function.implementation is None:
                continue
            owner = function.implementation.owner
            name = owner.name if isinstance(owner, TypeApplication) else owner
            key = name, function.token.text
            if key in self.declarations:
                raise function.token.error('duplicate inherent associated function or method')
            definition = self.checker.generics.definitions[name]
            if hasattr(definition, 'variants') and any(t.text == function.token.text for t, _ in definition.variants):
                raise function.token.error('associated names colliding with enum variants are not implemented')
            self.declarations[key] = function

    def select(self, owner, name, token, *, receiver=False):
        if isinstance(owner, SharedReference):
            raise token.error('reference receiver autoderef/autoborrow requires ownership and lifetime analysis, which is not implemented')
        owner = self.checker.generics.normalize(owner, token)
        self.checker.valid_type(owner, token)
        base = owner.name if isinstance(owner, TypeApplication) else owner
        function = self.declarations.get((base, name))
        if function is None:
            if receiver:
                raise token.error('unknown inherent method')
            return None
        if receiver and not function.receiver:
            raise token.error('associated function has no self receiver; use its qualified type path')
        arguments = None
        if function.type_parameters:
            pattern = function.implementation.owner
            bindings = dict(zip(pattern.arguments, owner.arguments))
            arguments = tuple(bindings[p.text] for p in function.type_parameters)
        return self.checker.function_generics.select(function, arguments, token)
