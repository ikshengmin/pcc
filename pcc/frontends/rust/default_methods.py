"""Declaration-checked default bodies for concrete local trait implementations.

https://doc.rust-lang.org/reference/items/traits.html#associated-item-declarations
Defaults are checked with abstract Self before any concrete body is checked.
Recorded trait calls are rebound by identity, not looked up as inherent methods
on the implementing type. Ordinary function scheduling and lowering own code.
This is not trait objects, specialization, blanket impls or borrow checking.
"""
from copy import deepcopy
from dataclasses import replace

from .generics import substitute


def reject_uncertified_defaults(traits):
    for trait in traits:
        for function in trait.methods:
            if function.body is not None:
                raise function.token.error(
                    'default trait methods require an actual source/options-bound '
                    'CheckedRustSource; caller attestation is not certification')


class DefaultMethods:
    def __init__(self, traits):
        self.traits = traits
        self.entries = {}
        self.inherited = []

    def remember(self, trait, function, scope, signature):
        if function.body is None:
            return
        self.entries[trait, function.token.text] = (
            dict(scope), signature, deepcopy(function.body))
        registry = self.traits.checker.function_generics
        registry.bound_calls[signature.declaration_name] = {}

    def inherit(self, implementation, members):
        for name, source in self.traits.traits[implementation.trait].items():
            if name in members or source.body is None:
                continue
            identity = implementation.trait, name
            _, _, body = self.entries[identity]
            function = replace(deepcopy(source), body=deepcopy(body),
                implementation=implementation, default_source=identity,
                inline_attributes=source.inline_attributes)
            members[name] = function
            self.traits.checker.functions.append(function)
            self.inherited.append(function)

    def check(self):
        checker = self.traits.checker
        previous_scope, previous_abstract = checker.type_scope, checker.abstract_function
        try:
            for (trait, _), (scope, signature, body) in self.entries.items():
                self_parameter = scope['Self']
                # Unlike a function type parameter, trait Self has no implicit
                # Sized bound. Lookup needs its trait identities, not storage.
                checker.standard_markers.constraints[self_parameter] = frozenset()
                self.traits.parameter_bounds[self_parameter] = frozenset(
                    {trait, *self.traits.hierarchy.ancestors[trait]})
                checker.type_scope, checker.abstract_function = scope, True
                for token, ty, _ in signature.params:
                    if ty == self_parameter:
                        raise token.error('default trait methods with by-value Self require Sized body obligations, which are not implemented')
                    checker.valid_type(ty, token)
                if signature.result == self_parameter:
                    raise signature.token.error('default trait methods returning Self require Sized body obligations, which are not implemented')
                checker.valid_type(signature.result, signature.token, allow_reference=False)
                checked_body = replace(signature, body=deepcopy(body))
                checker.check_body(checked_body, scope, abstract=True)
                if not checker.externally_checked:
                    from .moves import MoveChecker
                    MoveChecker(checker).check_function(checked_body)
        finally:
            checker.type_scope, checker.abstract_function = previous_scope, previous_abstract
        self.pin_calls()

    def pin_calls(self):
        """Remap declaration-bound calls before replaying any inherited body."""
        registry = self.traits.checker.function_generics
        for function in self.inherited:
            trait, name = function.default_source
            scope, signature, _ = self.entries[trait, name]
            bindings = dict(zip(self.traits.method_parameters[trait, name],
                                registry.parameters[function.declaration_name]))
            owner = function.implementation.owner
            bindings[scope['Self']] = owner
            bindings.update(self.traits.associated.bindings(trait, owner))
            registry.bound_calls[function.declaration_name] = {
                source_call: (selected_trait, substitute(selected_owner, bindings), method)
                for source_call, (selected_trait, selected_owner, method) in
                registry.bound_calls[signature.declaration_name].items()}
