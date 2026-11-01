"""Nominal method lookup with ordered concrete static trait candidates.

Impl parameters are inferred only by matching an already-known nominal owner.
Bodies use the same abstract checker and shared function instance worklist as
free functions. Method parameters are inferred later by the ordinary call
checker; lookup must not prematurely materialize an owner-only instance.
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
            if implementation.trait is not None:
                continue
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
            if function.implementation is None or function.implementation.trait is not None:
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

    def lookup(self, owner, name, token, *, receiver=False):
        through_reference = isinstance(owner, SharedReference)
        reference_mutable = owner.mutable if through_reference else None
        if through_reference:
            owner = owner.referent
        # Typed receivers are canonical, source-qualified owners are lexical.
        if not receiver:
            owner = self.checker.generics.normalize(owner, token)
        self.checker.valid_type(owner, token)
        base = owner.name if isinstance(owner, TypeApplication) else owner
        if not receiver:
            enum = self.checker.enums.get(owner)
            if enum is not None and name in enum.discriminants:
                # A type-qualified enum constructor is in the enum's value
                # namespace. A same-named trait associated function remains
                # legal but requires its trait-qualified path here.
                return None
        inherent = self.declarations.get((base, name))
        traits = self.checker.traits.candidates(owner, name)
        function = None
        if receiver:
            # The admitted receiver graph has one nominal value and at most
            # one reference layer. Rust inserts &T/&mut T at EACH deref step;
            # unsupported nested-reference candidates cannot match a signature.
            # Search inherent then visible traits at each candidate, not all
            # inherent receiver kinds before all trait receiver kinds.
            order = ([None, False, True] if not through_reference else
                     [reference_mutable, None, not reference_mutable])
            def receiver_kind(candidate):
                ty = candidate.params[0][1]
                return ty.mutable if isinstance(ty, SharedReference) else None
            for kind in order:
                if inherent is not None and inherent.receiver and receiver_kind(inherent) is kind:
                    function = inherent
                    break
                matches = [f for f in traits if f.receiver and receiver_kind(f) is kind]
                if len(matches) > 1:
                    raise token.error('ambiguous trait method; use an explicit qualified trait path')
                if matches:
                    function = matches[0]
                    break
            if function is None:
                if inherent is not None or traits:
                    raise token.error('associated function has no self receiver; use its qualified type path')
                raise token.error('unknown inherent or concrete trait method')
        elif inherent is not None:
            function = inherent
        elif len(traits) > 1:
            raise token.error('ambiguous trait associated method; use an explicit qualified trait path')
        elif traits:
            function = traits[0]
        else:
            return None
        # Resolution is complete. Do not retry another candidate because this
        # selected receiver fails mutability, move or lifetime requirements.
        if receiver and through_reference and not isinstance(function.params[0][1], SharedReference):
            raise token.error('cannot move a value receiver out of a shared reference; general autoderef is not implemented')
        arguments = ()
        if function.implementation.type_parameters:
            pattern = function.implementation.owner
            bindings = dict(zip(pattern.arguments, owner.arguments))
            arguments = tuple(bindings[p.text] for p in function.implementation.type_parameters)
        return function, arguments
