"""Local static trait identities and concrete implementation obligations.

No trait solver, blanket impls, dynamic dispatch, lang items or second body
checker. Selected implementation functions enter the ordinary method inference
and shared function-instantiation worklist.
"""
from dataclasses import replace

from .generics import PRIMITIVES, children
from .types import SharedReference, TypeApplication, TypeParameter


class StaticTraits:
    def __init__(self, checker, declarations, implementations):
        self.checker = checker
        self.declarations = declarations
        self.implementations = implementations
        self.traits = {}
        self.impls = {}
        self.method_parameters = {}

    def restricted_signature(self, function):
        if function.lifetime_parameters:
            raise function.token.error('trait method lifetime parameters are not implemented')
        pending = [function.result] + [ty for _, ty, _ in function.params]
        while pending:
            ty = pending.pop()
            if isinstance(ty, SharedReference) and ty.lifetime:
                raise function.token.error('explicit trait method reference lifetimes are not implemented')
            pending.extend(children(ty))

    def prepare(self):
        checker = self.checker
        declarations = {}
        for trait in self.declarations:
            if trait.token.text in declarations:
                raise trait.token.error('duplicate or reserved trait type name')
            declarations[trait.token.text] = trait
        from .trait_hierarchy import TraitHierarchy
        def parent(identity, path, token):
            if path not in declarations:
                raise token.error('supertraits require declared local traits; external/builtin trait loading is not implemented')
            return path
        self.hierarchy = TraitHierarchy(declarations, parent)
        impl_tokens = {}
        for trait in self.declarations:
            name = trait.token.text
            if name in self.traits or name in checker.generics.definitions or name in PRIMITIVES or name == 'Self':
                raise trait.token.error('duplicate or reserved trait type name')
            methods = {}
            for function in trait.methods:
                self.restricted_signature(function)
                if function.token.text in methods:
                    raise function.token.error('duplicate trait associated item')
                methods[function.token.text] = function
                scope = {'Self': TypeParameter('trait ' + name, 0, 'Self')}
                for index, parameter in enumerate(function.type_parameters, 1):
                    if parameter.text in scope or parameter.text in PRIMITIVES:
                        raise parameter.error('duplicate or reserved trait method type parameter')
                    scope[parameter.text] = TypeParameter('trait ' + name + '::' + function.token.text, index, parameter.text)
                # Method parameters share the ordinary marker model. Trait
                # Self is intentionally NOT a method type parameter and must
                # not acquire the implicit Sized default from prepare().
                method_scope = {p.text: scope[p.text] for p in function.type_parameters}
                checker.standard_markers.prepare(function, method_scope)
                self.method_parameters[name, function.token.text] = tuple(method_scope.values())
                checker.type_scope, checker.abstract_function = scope, True
                # Unlike ordinary type parameters, trait Self is not implicitly
                # Sized. Direct Self / &Self signatures are admitted, but a
                # nested aggregate/application needs bound obligations this
                # static slice deliberately does not solve.
                for raw in [function.result] + [ty for _, ty, _ in function.params]:
                    normalized = checker.generics.normalize(raw, function.token)
                    if normalized == scope['Self'] or (isinstance(normalized, SharedReference)
                                                        and normalized.referent == scope['Self']):
                        continue
                    pending = [normalized]
                    while pending:
                        item = pending.pop()
                        if item == scope['Self']:
                            raise function.token.error('nested trait Self types require Sized/bound checking, which is not implemented')
                        pending.extend(children(item))
                parameter_names = set()
                for token, ty, mutable in function.params:
                    if token.text in parameter_names:
                        raise token.error('duplicate trait method parameter')
                    parameter_names.add(token.text)
                    if mutable:
                        raise token.error('patterns in required trait method declarations are not supported')
                    checker.valid_type(checker.generics.normalize(ty, token), token)
                checker.valid_type(checker.generics.normalize(function.result, function.token), function.token, allow_reference=False)
            self.traits[name] = methods
        checker.type_scope, checker.abstract_function = {}, False
        for index, implementation in enumerate(self.implementations):
            if implementation.trait is None:
                continue
            methods = self.traits.get(implementation.trait)
            if methods is None:
                raise implementation.token.error('trait impls require a declared local trait; external traits/lang items are not implemented')
            owner = checker.generics.normalize(implementation.owner, implementation.token, {})
            base = owner.name if isinstance(owner, TypeApplication) else owner
            if base not in checker.generics.definitions:
                raise implementation.token.error('trait impl requires a concrete local nominal struct or enum')
            checker.valid_type(owner, implementation.token)
            # Canonical owner arguments (including evaluated array lengths)
            # belong to declaration identity, not source pretty-print spelling.
            original = implementation
            implementation = replace(original, owner=owner)
            self.implementations[index] = implementation
            for function in checker.functions:
                if function.implementation is original:
                    function.implementation = implementation
            key = implementation.trait, owner
            if key in self.impls:
                raise implementation.token.error('conflicting concrete trait implementations')
            members = {}
            for function in checker.functions:
                if function.implementation is not implementation:
                    continue
                self.restricted_signature(function)
                if any(p.text == 'Self' for p in function.type_parameters):
                    raise function.token.error('Self is reserved in trait method parameters')
                if function.token.text in members:
                    raise function.token.error('duplicate trait implementation method')
                if function.token.text not in methods:
                    raise function.token.error('method is not a member of the implemented trait')
                members[function.token.text] = function
            missing = methods.keys() - members.keys()
            if missing:
                raise implementation.token.error('missing required trait methods: ' + ', '.join(sorted(missing)))
            self.impls[key] = members
            impl_tokens[key] = implementation.token
        self.hierarchy.require_concrete_impls(self.impls, impl_tokens)

    def validate_signatures(self):
        checker = self.checker
        for (trait, owner), members in self.impls.items():
            for name, function in members.items():
                required = self.traits[trait][name]
                if function.receiver != required.receiver or len(function.type_parameters) != len(required.type_parameters):
                    raise function.token.error('trait method receiver or generic parameter count mismatch')
                parameters = checker.function_generics.parameters[function.declaration_name]
                required_parameters = self.method_parameters[trait, name]
                constraints = checker.standard_markers.constraints
                contracts = [(constraints[required_parameter], constraints[parameter])
                             for required_parameter, parameter in zip(required_parameters, parameters)]
                if any(not actual <= expected for expected, actual in contracts):
                    raise function.token.error('trait implementation method bounds are stricter than the trait contract')
                # Selection always produces the implementation function, but
                # a call through dot/type/trait-qualified syntax must retain
                # the trait declaration's obligations. Keep the impl's own
                # declared constraints for its unused abstract body; do not
                # inject stronger trait assumptions into that body.
                checker.standard_markers.call_constraints.update(
                    (parameter, constraints[required_parameter])
                    for required_parameter, parameter in zip(required_parameters, parameters))
                scope = {p.text: value for p, value in zip(required.type_parameters, parameters)}
                scope['Self'] = owner
                expected = tuple(checker.generics.normalize(ty, token, scope) for token, ty, _ in required.params)
                result = checker.generics.normalize(required.result, required.token, scope)
                if expected != tuple(ty for _, ty, _ in function.params) or result != function.result:
                    raise function.token.error('trait implementation method signature mismatch')

    def candidates(self, owner, name):
        return [members[name] for (_, target), members in self.impls.items()
                if target == owner and name in members]

    def qualified(self, owner, trait, name, token):
        if trait in self.checker.type_scope or trait not in self.traits:
            raise token.error('qualified path requires a declared local trait')
        owner = self.checker.generics.normalize(owner, token)
        self.checker.valid_type(owner, token)
        members = self.impls.get((trait, owner))
        if members is None:
            raise token.error('no concrete implementation for qualified trait path')
        function = members.get(name)
        if function is None:
            raise token.error('unknown trait associated method')
        return function, ()
