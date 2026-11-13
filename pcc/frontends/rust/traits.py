"""Local static trait identities and concrete implementation obligations.

No trait solver, blanket impls, dynamic dispatch, lang items or second body
checker. Selected implementation functions enter the ordinary method inference
and shared function-instantiation worklist.
"""
from dataclasses import replace

from .generics import PRIMITIVES, children, substitute
from .standard_markers import SIZED
from .types import SharedReference, TypeApplication, TypeParameter


class StaticTraits:
    def __init__(self, checker, declarations, implementations):
        self.checker = checker
        self.declarations = declarations
        self.implementations = implementations
        self.traits = {}
        self.impls = {}
        self.method_parameters = {}
        self.signatures = {}
        self.parameter_bounds = {}
        # An implementation may declare fewer obligations than its trait.
        # Calls must retain the required contract without granting those
        # extra assumptions to the implementation's abstract body.
        self.call_bounds = {}
        # Trait methods declared with `where Self: Sized`, by (trait, name).
        self.sized_self_methods = set()
        from .associated_types import AssociatedTypes
        self.associated = AssociatedTypes(self)
        from .default_methods import DefaultMethods
        self.defaults = DefaultMethods(self)

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
        for implementation in self.implementations:
            # The former parse-time rule, now applied to retained syntax:
            # impl type parameters, impl where predicates or a generic trait.
            if implementation.trait is not None and (
                    implementation.type_parameters or implementation.where_predicates
                    or not isinstance(implementation.trait, str)):
                raise implementation.token.error(
                    'generic traits and blanket/generic trait impls require impl instantiation and '
                    'coherence checking, which is not implemented')
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
        # All trait names/members are identities before any signature bounds
        # are resolved: a method may mention its own or a later local trait.
        for trait in self.declarations:
            name = trait.token.text
            if name in self.traits or name in checker.generics.definitions or name in PRIMITIVES or name == 'Self':
                raise trait.token.error('duplicate or reserved trait type name')
            methods = {}
            for function in trait.methods:
                if function.token.text in methods:
                    raise function.token.error('duplicate trait associated item')
                methods[function.token.text] = function
            self.traits[name] = methods
        self.associated.prepare()
        impl_tokens = {}
        for trait in self.declarations:
            name = trait.token.text
            for function in trait.methods:
                self.restricted_signature(function)
                scope = {'Self': TypeParameter('trait ' + name, 0, 'Self')}
                scope.update(self.associated.scope(name))
                for index, parameter in enumerate(function.type_parameters, 1):
                    if parameter.text in scope or parameter.text in PRIMITIVES:
                        raise parameter.error('duplicate or reserved trait method type parameter')
                    scope[parameter.text] = TypeParameter('trait ' + name + '::' + function.token.text, index, parameter.text)
                # Method parameters share the ordinary marker model. Trait
                # Self is intentionally NOT a method type parameter and must
                # not acquire the implicit Sized default from prepare().
                method_scope = {p.text: scope[p.text] for p in function.type_parameters}
                marker_bounds = self.prepare_function_bounds(function, method_scope)
                checker.standard_markers.prepare(replace(function, parameter_bounds=marker_bounds,
                                                          where_predicates=()), method_scope)
                sized_self = self.self_sized(function, method_scope)
                if sized_self:
                    self.sized_self_methods.add((name, function.token.text))
                self.method_parameters[name, function.token.text] = tuple(method_scope.values())
                checker.type_scope, checker.abstract_function = scope, True
                # Unlike ordinary type parameters, trait Self is not implicitly
                # Sized. Direct Self / &Self signatures are admitted, but a
                # nested aggregate/application needs the Sized obligation that
                # only a `where Self: Sized` method declares.
                for raw in [function.result] + [ty for _, ty, _ in function.params]:
                    normalized = checker.generics.normalize(raw, function.token)
                    if normalized == scope['Self'] or (isinstance(normalized, SharedReference)
                                                        and normalized.referent == scope['Self']):
                        continue
                    pending = [normalized]
                    while pending:
                        item = pending.pop()
                        if item == scope['Self'] and not sized_self:
                            raise function.token.error('nested trait Self types require Sized/bound checking, which is not implemented')
                        pending.extend(children(item))
                parameter_names = set()
                for token, ty, mutable in function.params:
                    if token.text in parameter_names:
                        raise token.error('duplicate trait method parameter')
                    parameter_names.add(token.text)
                    if mutable and function.body is None:
                        raise token.error('patterns in required trait method declarations are not supported')
                    # Required methods have no body or by-value storage yet.
                    # A direct ?Sized projection/parameter is legal here; its
                    # concrete impl still passes ordinary Sized validation.
                    # Nested aggregate/application members keep their own
                    # Sized well-formedness checks in valid_type/normalize.
                    checker.valid_type(checker.generics.normalize(ty, token), token, require_sized=False)
                checker.valid_type(checker.generics.normalize(function.result, function.token),
                                   function.token, allow_reference=False, require_sized=False)
                signature = replace(function,
                    params=[(t, checker.generics.normalize(ty, t), mutable)
                            for t, ty, mutable in function.params],
                    result=checker.generics.normalize(function.result, function.token))
                self.signatures[name, function.token.text] = scope['Self'], signature
                checker.function_generics.parameters[signature.declaration_name] = tuple(method_scope.values())
                self.defaults.remember(name, function, scope, signature)
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
            self.associated.register(implementation)
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
            missing = {name for name in methods.keys() - members.keys()
                       if methods[name].body is None}
            if missing:
                raise implementation.token.error('missing required trait methods: ' + ', '.join(sorted(missing)))
            self.defaults.inherit(implementation, members)
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
                local_contracts = [(self.parameter_bounds[required_parameter], self.parameter_bounds[parameter])
                                   for required_parameter, parameter in zip(required_parameters, parameters)]
                if any(not actual <= expected for expected, actual in contracts + local_contracts):
                    raise function.token.error('trait implementation method bounds are stricter than the trait contract')
                # Selection always produces the implementation function, but
                # a call through dot/type/trait-qualified syntax must retain
                # the trait declaration's obligations. Keep the impl's own
                # declared constraints for its unused abstract body; do not
                # inject stronger trait assumptions into that body.
                checker.standard_markers.call_constraints.update(
                    (parameter, constraints[required_parameter])
                    for required_parameter, parameter in zip(required_parameters, parameters))
                self.call_bounds.update(
                    (parameter, self.parameter_bounds[required_parameter])
                    for required_parameter, parameter in zip(required_parameters, parameters))
                # Compare declaration-resolved types by alpha substitution.
                # Never resolve raw required paths in the impl environment:
                # its parameter names may shadow nominal/trait declarations.
                self_parameter, signature = self.signatures[trait, name]
                bindings = dict(zip(required_parameters, parameters))
                bindings[self_parameter] = owner
                bindings.update(self.associated.bindings(trait, owner))
                expected = tuple(substitute(ty, bindings) for _, ty, _ in signature.params)
                result = substitute(signature.result, bindings)
                if expected != tuple(ty for _, ty, _ in function.params) or result != function.result:
                    raise function.token.error('trait implementation method signature mismatch')

    def self_sized(self, function, scope):
        """A trait method's `where Self: Sized` predicate.

        https://doc.rust-lang.org/reference/items/traits.html#dyn-compatibility
        It holds for every implementing type here: implementations are
        concrete local nominal types. Abstract owners are checked at calls.
        """
        markers = self.checker.standard_markers
        sized = False
        for bound in function.self_bounds:
            if bound.relaxed:
                raise bound.token.error('relaxed where Self predicates are not implemented')
            if bound.path in self.traits:
                raise bound.token.error('where Self predicates naming local traits require supertrait obligations, which are not implemented')
            if markers.resolve(bound, scope) != SIZED:
                raise bound.token.error('where Self predicates other than canonical Sized are not implemented')
            if markers.first_explicit_bound is None:
                markers.first_explicit_bound = bound.token
            sized = True
        return sized

    def prepare_function_bounds(self, function, scope):
        marker_bounds = []
        for parameter in scope.values():
            self.parameter_bounds[parameter] = frozenset()
        for name, bounds in function.effective_parameter_bounds:
            local, markers = set(), []
            for bound in bounds:
                if bound.path not in self.traits or bound.path in scope:
                    markers.append(bound)
                    continue
                if bound.callable is not None:
                    # rustc E0658: parenthesized arguments are Fn-family sugar
                    # only; a local trait named Fn is not a closure trait.
                    raise bound.token.error('parenthesized arguments are only Fn, FnMut and FnOnce trait sugar; a local trait does not accept them')
                if bound.relaxed:
                    raise bound.token.error('local/user trait bounds cannot be relaxed; only canonical ?Sized is supported')
                if self.associated.has_types(bound.path):
                    raise bound.token.error('associated types through generic trait bounds require projection normalization, which is not implemented')
                if self.checker.module_resolution is not None:
                    raise bound.token.error('module-scoped local trait bounds are not implemented')
                local.add(bound.path)
                local.update(self.hierarchy.ancestors[bound.path])
            if local and any(bound.relaxed for bound in markers):
                raise name.error('relaxed local-trait parameter layouts are not implemented')
            self.parameter_bounds[scope[name.text]] = frozenset(local)
            marker_bounds.append((name, tuple(markers)))
        return tuple(marker_bounds)

    def require_checked_context(self, checked, source, target, overflow_checks):
        # Presence admission, not trait legality: run before any abstract body
        # check or concrete scheduling. The semantic pass below resolves the
        # bound and checks all obligations, including unused declarations.
        names = {trait.token.text for trait in self.declarations}
        functions = list(self.checker.functions)
        functions.extend(function for trait in self.declarations for function in trait.methods)
        functions.extend(self.implementations)  # Empty impl headers are still declarations.
        first = next((item.token for declaration in self.declarations
                      for item in declaration.associated_types), None)
        if first is None:
            first = next((item.token for implementation in self.implementations
                          for item in implementation.associated_types), None)
        associated = first is not None
        if first is None:
            first = next((bound.token for function in functions
                      for _, bounds in function.effective_parameter_bounds for bound in bounds
                      if bound.path in names and not bound.relaxed
                      and bound.path not in {p.text for p in function.type_parameters}), None)
        if first is None:
            return
        from .external_check import CheckedRustSource
        if type(checked) is not CheckedRustSource:
            subject = 'associated types' if associated else 'local trait bounds'
            raise first.error(subject + ' require an actual source/options-bound CheckedRustSource during this transition')
        checked.validate(source, target_triple=target, overflow_checks=overflow_checks,
                         edition=self.checker.standard_markers.policy.edition)
        if checked.provenance.edition != str(self.checker.standard_markers.policy.edition):
            raise first.error('local trait context and checked edition differ')

    def proves(self, owner, trait):
        return (trait in self.parameter_bounds.get(owner, ())
                if isinstance(owner, TypeParameter) else (trait, owner) in self.impls)

    def require(self, owner, trait, token):
        if not self.proves(owner, trait):
            raise token.error(f'unsatisfied local trait bound {owner}: {trait}')

    def check_function_call(self, parameters, arguments, token):
        for parameter, argument in zip(parameters, arguments):
            for trait in sorted(self.call_bounds.get(parameter, self.parameter_bounds.get(parameter, ()))):
                self.require(argument, trait, token)

    def abstract_method(self, owner, trait, name, token):
        self.require(owner, trait, token)
        entry = self.signatures.get((trait, name))
        if entry is None:
            raise token.error('unknown trait associated method')
        self_parameter, signature = entry
        if (trait, name) in self.sized_self_methods and not self.checker.standard_markers.proves_sized(owner):
            raise token.error(f'trait method {name} requires Self: Sized, which {owner} does not satisfy')
        bindings = {self_parameter: owner}
        return replace(signature,
            params=[(t, substitute(ty, bindings), mutable) for t, ty, mutable in signature.params],
            result=substitute(signature.result, bindings), bound_trait=(trait, owner))

    def candidates(self, owner, name):
        if isinstance(owner, TypeParameter):
            return [self.abstract_method(owner, trait, name, self.traits[trait][name].token)
                    for trait in sorted(self.parameter_bounds.get(owner, ()))
                    if name in self.traits[trait]]
        return [members[name] for (_, target), members in self.impls.items()
                if target == owner and name in members]

    def qualified(self, owner, trait, name, token):
        if trait in self.checker.type_scope or trait not in self.traits:
            raise token.error('qualified path requires a declared local trait')
        owner = self.checker.generics.normalize(owner, token)
        return self.qualified_canonical(owner, trait, name, token)

    def qualified_canonical(self, owner, trait, name, token):
        self.checker.valid_type(owner, token, require_sized=False)
        if isinstance(owner, TypeParameter):
            return self.abstract_method(owner, trait, name, token), ()
        members = self.impls.get((trait, owner))
        if members is None:
            raise token.error('no concrete implementation for qualified trait path')
        function = members.get(name)
        if function is None:
            raise token.error('unknown trait associated method')
        return function, ()
